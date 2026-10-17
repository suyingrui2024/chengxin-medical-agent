"""Actual bytes, transport permissions, stale images and bounded tabular projections."""
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import zlib

from medagent.execution import ExecutionManager, ExecutionError, safe_bytes
from medagent.execution_artifacts import projection, IMAGE_LIMIT
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer

CONFIG = {
    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
    "MEDAGENT_LLM_API_KEY": "local-artifact-fixture",
    "MEDAGENT_LLM_MODEL": "fixture",
}


def chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def png(width=2, height=1, pixel=b"\x2d\x64\x50"):
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + pixel * 2))
        + chunk(b"IEND", b"")
    )


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = ExecutionManager(root=Path(self.temporary.name) / "records")
        self.addCleanup(self.manager.close)
        self.session = self.manager.create(CONFIG)

    def test_static_image_metadata_and_hash_bound_bytes(self):
        data = png()
        (self.session.directory / "plot.png").write_bytes(data)
        preview = self.session.preview_file("plot.png")
        self.assertEqual(
            (preview["kind"], preview["mime"], preview["width"], preview["height"]),
            ("image", "image/png", 2, 1),
        )
        self.assertNotIn("text", preview)
        self.assertEqual(preview["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(
            self.session.image_file("plot.png", preview["sha256"]), (data, "image/png")
        )
        (self.session.directory / "plot.png").write_bytes(png(pixel=b"\x80\x30\x30"))
        with self.assertRaisesRegex(ExecutionError, "已变化"):
            self.session.image_file("plot.png", preview["sha256"])

    def test_valid_jpeg_uses_real_fixture_dimensions(self):
        data = (Path(__file__).parent / "fixtures/artifact-preview.jpg").read_bytes()
        preview = projection("photo.JPG", data, "fixture")
        self.assertEqual(
            (preview["mime"], preview["width"], preview["height"]), ("image/jpeg", 3, 2)
        )
        for invalid in [data[:-2], b"<svg></svg>", data + b"<script>ignored</script>"]:
            with self.assertRaises(ValueError):
                projection("photo.jpg", invalid, "fixture")

    def test_png_crc_dimensions_animation_and_payload_limits(self):
        corrupt = bytearray(png())
        corrupt[-5] ^= 1
        for data in [
            bytes(corrupt),
            png(8193, 1),
            png(8192, 8192),
            png() + b"trailing",
            png()[:33] + chunk(b"acTL", struct.pack(">II", 1000, 0)) + png()[33:],
            b"x" * (IMAGE_LIMIT + 1),
        ]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                projection("plot.png", data, "fixture")

    def test_tables_preserve_quotes_newlines_and_literal_formula_strings(self):
        text = '\ufeff指标,结果\n"换行\n名称",1\n"<img src=x onerror=alert(1)>","=HYPERLINK(""https://example.invalid"")"\n'
        preview = projection("result.csv", text.encode(), "fixture")
        self.assertEqual(preview["kind"], "table")
        self.assertEqual(preview["rows"][1], ["换行\n名称", "1"])
        self.assertEqual(preview["rows"][2][1], '=HYPERLINK("https://example.invalid")')
        self.assertEqual(preview["text"], text)
        self.assertEqual(
            projection("result.tsv", b"name\tvalue\na\t3\n", "fixture")["rows"][1],
            ["a", "3"],
        )

    def test_table_truncation_and_bad_csv_are_explicit(self):
        text = "\n".join(",".join(str(i) for i in range(30)) for _ in range(121))
        preview = projection("wide.csv", text.encode(), "fixture")
        self.assertEqual(
            (len(preview["rows"]), len(preview["rows"][0]), preview["total_rows"]),
            (100, 24, 121),
        )
        self.assertTrue(preview["rows_truncated"] and preview["columns_truncated"])
        bad = projection("bad.csv", b'a,b\n"unterminated', "fixture")
        self.assertEqual(bad["kind"], "text")
        self.assertIn("原文", bad["notice"])
        self.assertTrue(projection("ragged.csv", b"a,b\nx\n", "fixture")["ragged"])

    def test_svg_html_and_fake_image_extensions_never_become_image_payloads(self):
        for name in ["unsafe.svg", "unsafe.html"]:
            (self.session.directory / name).write_text('<svg onload="alert(1)"></svg>')
            preview = self.session.preview_file(name)
            self.assertEqual(preview["kind"], "text")
            with self.assertRaises(ExecutionError):
                self.session.image_file(name, preview["sha256"])
        (self.session.directory / "fake.png").write_text("<svg></svg>")
        with self.assertRaises(ExecutionError):
            self.session.preview_file("fake.png")

    def test_link_fifo_traversal_changed_reads_and_replaced_roots_are_rejected(self):
        root = self.session.directory
        (root / "linked.png").symlink_to(Path(self.temporary.name) / "outside.png")
        os.mkfifo(root / "pipe.png")
        for name in ["linked.png", "pipe.png", "../outside.png", "/etc/passwd"]:
            with self.subTest(name=name), self.assertRaises(ExecutionError):
                self.session.preview_file(name)
        path = root / "changing.txt"
        path.write_text("before")
        read = os.read

        def concurrent_change(*args):
            result = read(*args)
            path.write_text("after")
            return result

        with patch(
            "medagent.execution.os.read", side_effect=concurrent_change
        ), self.assertRaisesRegex(ExecutionError, "读取期间"):
            safe_bytes(root, "changing.txt")
        root.rename(root.with_name("moved"))
        root.mkdir()
        with self.assertRaises(ExecutionError):
            self.session.preview_file("missing.png")

    def test_preview_is_offline_and_revisions_detect_same_size_restored_timestamp(self):
        path = self.session.directory / "value.csv"
        path.write_text("n\n1\n")
        before = self.session.preview_file("value.csv")
        stamp = path.stat().st_mtime_ns
        path.write_text("n\n2\n")
        os.utime(path, ns=(stamp, stamp))
        self.assertFalse(
            self.session.artifact_status("value.csv", before["sha256"])["matches"]
        )
        self.assertIsNone(self.session.process)
        identity = self.session.id
        self.manager.close()
        restored = ExecutionManager(root=Path(self.temporary.name) / "records")
        try:
            session = restored.open(identity, {})
            self.assertEqual(session.preview_file("value.csv")["rows"], [["n"], ["2"]])
            self.assertIsNone(session.process)
        finally:
            restored.close()

    def test_http_preview_and_image_keep_desktop_auth_and_origin_checks(self):
        runtime = build_runtime(backend="demo")
        context = runtime.start()
        server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            context.services.require("events"),
            access_token="local-artifact-token",
        )
        server.execution.close()
        server.execution = self.manager
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        data = png()
        (self.session.directory / "plot.png").write_bytes(data)
        url = f"http://127.0.0.1:{server.server_port}/api/execution/{self.session.id}"
        headers = {"X-MedAgent-Token": "local-artifact-token"}
        with urlopen(
            Request(url + "/preview?path=plot.png", headers=headers)
        ) as response:
            preview = json.load(response)
        image = url + "/image?path=plot.png&sha256=" + preview["sha256"]
        with urlopen(Request(image, headers=headers)) as response:
            self.assertEqual(response.read(), data)
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        for request, status in [
            (Request(image), 401),
            (
                Request(
                    image, headers={**headers, "Origin": "https://foreign.invalid"}
                ),
                403,
            ),
            (Request(image.replace(preview["sha256"], "0" * 64), headers=headers), 400),
        ]:
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, status)
