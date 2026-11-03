"""The web package is served, not compiled — nothing fails until the browser does.

Three kinds of dangling reference were invisible to every check in this
repository: a ``<link href>`` to a renamed file, an ``@import`` naming a
stylesheet that no longer exists, and an ES-module ``import`` of a script that
moved. The server answers 404, the browser logs a warning, and the page still
renders — mostly. The first half of this file exercises the static handler
directly; the second half walks the reference graph from ``index.html`` and
requires it to close.
"""
from __future__ import annotations

import fnmatch
import io
import pathlib
import posixpath
import re
import unittest
from importlib import resources
from urllib.parse import urlparse

from medagent.server.core import HandlerCore

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "src" / "medagent" / "web"
PACKS = ROOT / "src" / "medagent" / "extensions" / "packs"

#: ``src="x"`` / ``href="x"`` in the page.
HTML_REFERENCE = re.compile(r'(?:src|href)="([^"]+)"')
CSS_IMPORT = re.compile(r'@import\s+url\(\s*["\']?([^"\')]+)["\']?\s*\)')
CSS_URL = re.compile(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)')
JS_IMPORT = re.compile(
    r'(?:\bfrom\s*|^\s*import\s*|\bimport\(\s*)["\']([^"\']+)["\']', re.M
)
#: A shipped asset named as a string literal, with an optional ``#fragment``.
ASSET_LITERAL = re.compile(
    r'["\'`]([\w./-]+\.(?:svg|png|otf|json|woff2))(?:#[^"\'`]*)?["\'`]'
)
#: A URL literal in a browser spec, naming a file the server would serve.
#: The fragment is part of the literal -- "/x.svg#step-2" is one URL -- so it
#: is matched here and stripped before the path is resolved.
SPEC_URL = re.compile(
    r'"(/[^"\s]+\.(?:js|css|svg|png|json|woff2?)(?:#[^"\s]*)?)"'
)
#: ``page.route("**/name", ...)`` -- the spec answers this request itself, so
#: the URL needs no file behind it.
ROUTE_GLOB = re.compile(r'page\.route\(\s*["\']([^"\']+)["\']')
#: In a manifest only these keys name a served file; ``upstream_path`` and
#: ``editable_source`` describe somewhere else entirely.
MANIFEST_ENTRY = re.compile(r'"(?:file|license_file)"\s*:\s*"([^"]+)"')
#: Manifests describe files without being reached from the page. They are
#: references in their own right: removing a name from one of these is how an
#: asset stops shipping, and a name with no file behind it is a broken promise.
MANIFESTS = (
    "assets/fonts/font-sources.json",
    "assets/illustrations/illustration-labels.json",
)


def external(reference: str) -> bool:
    return reference.startswith(("http:", "https:", "data:", "#", "mailto:"))


def resolve(base: str, reference: str) -> str:
    """Map a URL reference to a path relative to the served root.

    The URL space and the disk layout are the same shape on purpose: what the
    handler serves at ``/css/x.css`` is ``web/css/x.css``. A test that resolved
    differently would prove nothing about the running server.
    """
    target = urlparse(reference).path
    joined = target.lstrip("/") if target.startswith("/") else posixpath.join(base, target)
    return posixpath.normpath(joined)


def references(key: str) -> set[str]:
    """Everything one shipped file points at, as root-relative keys.

    Every path in this tree is written relative to the file that declares it —
    ``href`` in the page, ``url()`` in a stylesheet, a specifier in a module, a
    name in a manifest. One rule, four syntaxes.
    """
    path = WEB_ROOT / key
    text = path.read_text(encoding="utf-8")
    base = posixpath.dirname(key)
    found: set[str] = set()
    if path.suffix == ".html":
        found = {resolve(base, ref) for ref in HTML_REFERENCE.findall(text) if not external(ref)}
    elif path.suffix == ".css":
        for ref in CSS_IMPORT.findall(text) + CSS_URL.findall(text):
            if not external(ref):
                found.add(resolve(base, ref))
    elif path.suffix == ".js":
        for specifier in JS_IMPORT.findall(text):
            if specifier.startswith("."):
                found.add(resolve(base, specifier))
        found |= {resolve(base, ref) for ref in ASSET_LITERAL.findall(text)}
    elif path.suffix == ".json":
        found = {resolve(base, ref) for ref in MANIFEST_ENTRY.findall(text)}
    return found


def reachable_from_page() -> set[str]:
    """The closure of references starting at the page and its manifests."""
    seen = {"index.html", *MANIFESTS}
    queue = list(seen)
    while queue:
        key = queue.pop()
        if not (WEB_ROOT / key).is_file() or (WEB_ROOT / key).suffix not in {
            ".html",
            ".css",
            ".js",
            ".json",
        }:
            continue
        for target in references(key) - seen:
            seen.add(target)
            queue.append(target)
    return seen


class _Recorder(HandlerCore):
    """A handler that answers into memory instead of onto a socket."""

    def __init__(self) -> None:
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, status) -> None:
        self.status = int(status)

    def send_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def end_headers(self) -> None:
        pass

    def get(self, path: str):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}
        self._static(path)
        return self.status, self.headers, self.wfile.getvalue()


class StaticHandlerTests(unittest.TestCase):
    def serve(self, path: str):
        return _Recorder().get(path)

    def test_root_serves_the_page(self):
        status, headers, body = self.serve("/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"<!doctype html>", body)

    def test_nested_paths_are_served(self):
        """Code, styles and media live in separate directories."""
        for path, mime in (
            ("/css/styles.css", "text/css; charset=utf-8"),
            ("/js/app.js", "text/javascript; charset=utf-8"),
            ("/assets/fonts/NotoSansCJKsc-Regular.otf", "font/otf"),
            ("/assets/brand.png", "image/png"),
        ):
            with self.subTest(path=path):
                status, headers, body = self.serve(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], mime)
                self.assertGreater(len(body), 0)

    def test_hardening_headers_travel_with_the_body(self):
        _, headers, _ = self.serve("/css/styles.css")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_missing_file_and_directory_both_404(self):
        self.assertEqual(self.serve("/css/not-a-file.css")[0], 404)
        self.assertEqual(self.serve("/css")[0], 404)
        self.assertEqual(self.serve("/assets")[0], 404)

    def test_traversal_is_refused(self):
        for path in (
            "/../server/core.py",
            "/css/../../server/core.py",
            "/css/../core.py",
            "/./index.html",
            "/css//styles.css",
            "/%2e%2e/%2e%2e/server/core.py",
            "/..%2fserver%2fcore.py",
            "/css/..\\server\\core.py",
            "/index.html%00.png",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.serve(path)[0], 404)

    def test_dot_entries_and_sources_are_refused(self):
        """``__pycache__``, dotfiles and the package's own source are not assets."""
        for path in ("/.hidden", "/css/.hidden.css", "/__init__.py", "/core.py"):
            with self.subTest(path=path):
                self.assertEqual(self.serve(path)[0], 404)


class ReferenceGraphTests(unittest.TestCase):
    def test_page_references_resolve(self):
        missing = sorted(
            key for key in reachable_from_page() if not (WEB_ROOT / key).is_file()
        )
        self.assertEqual(missing, [], f"引用了不存在的文件：{missing}")

    def test_imports_are_relative(self):
        """A bare specifier would be resolved by the browser against nothing."""
        for path in sorted(WEB_ROOT.rglob("*.js")):
            for specifier in JS_IMPORT.findall(path.read_text(encoding="utf-8")):
                with self.subTest(module=path.name, specifier=specifier):
                    self.assertTrue(specifier.startswith("."))

    def test_cascade_slices_are_all_imported_in_order(self):
        """A slice nothing imports is dead CSS; a slice imported twice is a bug
        that only shows up as a doubled rule nobody can find."""
        imported = CSS_IMPORT.findall((WEB_ROOT / "css" / "styles.css").read_text(encoding="utf-8"))
        numbered = sorted(
            path.name for path in (WEB_ROOT / "css").glob("[0-9][0-9]-*.css")
        )
        self.assertTrue(numbered, "没有找到编号分片")
        self.assertEqual(
            [name for name in imported if name[0].isdigit()],
            numbered,
            "styles.css 的导入顺序与编号分片不一致（有序或缺失）",
        )
        self.assertEqual(len(imported), len(set(imported)), "styles.css 重复导入了同一个文件")

    def test_nothing_is_unreachable(self):
        """A file no reference points at is a file nobody maintains.

        Pack content names its illustrations by served URL, so those count as
        references too — they are the only reason ``body-location.svg`` ships.
        """
        reachable = reachable_from_page()
        for pack in PACKS.glob("*.json"):
            for asset in re.findall(r'"asset":\s*"([^"]+)"', pack.read_text(encoding="utf-8")):
                reachable.add(resolve("", asset))
        on_disk = {
            path.relative_to(WEB_ROOT).as_posix()
            for path in WEB_ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".py", ".pyc"}
        }
        self.assertEqual(sorted(on_disk - reachable), [], "以上文件没有任何引用")

    def test_package_data_matches_disk(self):
        """``resources.files("medagent.web")`` must see what the tests walk."""
        node = resources.files("medagent.web")
        self.assertTrue(node.joinpath("index.html").is_file())
        self.assertTrue(node.joinpath("css").joinpath("styles.css").is_file())
        self.assertTrue(node.joinpath("js").joinpath("app.js").is_file())


class DesktopShellTests(unittest.TestCase):
    """The Electron splash page loads over ``file://``.

    A wrong relative path there is a blank window during startup with no console
    anyone reads, and it is the one reference the served-page test cannot see.
    """

    def test_splash_page_references_resolve(self):
        page = (ROOT / "desktop" / "starting.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "desktop" / "starting.css").read_text(encoding="utf-8")
        references = [ref for ref in HTML_REFERENCE.findall(page) if not external(ref)]
        references += [ref for ref in CSS_IMPORT.findall(stylesheet) if not external(ref)]
        references += [ref for ref in CSS_URL.findall(stylesheet) if not external(ref)]
        self.assertTrue(references, "启动页没有引用任何资源")
        missing = [
            reference
            for reference in references
            if not (ROOT / "desktop" / reference).is_file()
        ]
        self.assertEqual(missing, [], f"启动页引用了不存在的文件：{missing}")


class BrowserSuiteReferenceTests(unittest.TestCase):
    """A browser spec that asks for a file the server does not serve fails at
    the moment it runs, not when it is written.

    The page's own reference graph is checked above.  The specs' is a second
    graph: they import modules by URL (``await import("/structured-ui.js")``)
    and the browser resolves that against the server.  When the web tree moved
    into ``css/``, ``js/`` and ``assets/``, the page was updated and the specs
    were not, so twenty-nine tests failed on a module that was simply no
    longer at the old address.
    """

    def test_spec_urls_resolve_to_served_files(self):
        served = {
            path.relative_to(WEB_ROOT).as_posix()
            for path in WEB_ROOT.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        missing = []
        for spec in sorted((ROOT / "tests" / "ui").glob("*.mjs")):
            text = spec.read_text(encoding="utf-8")
            mocked = ROUTE_GLOB.findall(text)
            for url in SPEC_URL.findall(text):
                if any(fnmatch.fnmatch(url, glob) for glob in mocked):
                    continue  # the spec answers this one itself
                key = resolve("", url.split("#")[0])
                if key not in served:
                    missing.append(f"{spec.name}: {url}")
        self.assertEqual(missing, [], f"浏览器测试引用了不存在的文件：{missing}")


if __name__ == "__main__":
    unittest.main()
