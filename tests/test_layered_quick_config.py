from importlib.util import module_from_spec, spec_from_file_location
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "layered_quick_config", ROOT / "scripts/config/configure.py"
)
CONFIG = module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class LayeredQuickConfigTests(unittest.TestCase):
    def make_root(self, directory):
        root = Path(directory)
        (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
        return root

    def test_full_configuration_creates_two_layers_and_merged_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            old = dict(os.environ)
            try:
                os.environ.update(
                    {
                        "MEDAGENT_LLM_API_KEY": "$(do-not-run)'local-secret",
                        "MEDAGENT_LLM_MODEL": "deepseek-flash",
                        "MEDAGENT_VISION_MODEL": "deepseek-flash",
                    }
                )
                output = io.StringIO()
                with redirect_stdout(output):
                    CONFIG.configure_full(root, interactive=False)
            finally:
                os.environ.clear()
                os.environ.update(old)

            common = CONFIG.read_partial(root / ".env.common")
            dsh = CONFIG.read_partial(root / ".env.dsh")
            merged = CONFIG.read_partial(root / ".env")
            self.assertEqual(common["MEDAGENT_BACKEND"], "compatible")
            self.assertEqual(common["MEDAGENT_LLM_BASE_URL"], "https://api.deepseek.com")
            self.assertNotIn("MEDAGENT_LLM_API_KEY", dsh)
            self.assertEqual(dsh["MEDAGENT_BACKEND"], "dsh")
            self.assertEqual(
                dsh["MEDAGENT_LLM_BASE_URL"],
                "https://api.deepseek.com/anthropic",
            )
            self.assertEqual(
                merged["MEDAGENT_LLM_API_KEY"], "$(do-not-run)'local-secret"
            )
            self.assertNotIn("local-secret", output.getvalue())
            self.assertEqual(merged["MEDAGENT_DSH_REVIEW_MODE"], "shadow")
            for name in (".env", ".env.common", ".env.dsh"):
                self.assertEqual(
                    stat.S_IMODE((root / name).stat().st_mode),
                    stat.S_IRUSR | stat.S_IWUSR,
                )

    def test_dsh_layer_requires_common_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            with self.assertRaisesRegex(ValueError, "01-configure-common"):
                CONFIG.configure_dsh(root, interactive=False)

    def test_invalid_review_mode_is_rejected_without_rewriting_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            (root / ".env.common").write_text(
                "MEDAGENT_BACKEND=compatible\n"
                "MEDAGENT_LLM_BASE_URL=https://api.deepseek.com\n"
                "MEDAGENT_LLM_API_KEY=secret\n"
                "MEDAGENT_LLM_MODEL=deepseek-flash\n"
            )
            (root / ".env.dsh").write_text(
                "MEDAGENT_BACKEND=dsh\n"
                "MEDAGENT_LLM_BASE_URL=https://api.deepseek.com/anthropic\n"
                "MEDAGENT_DSH_REVIEW_MODE=unsafe\n"
            )
            with self.assertRaisesRegex(ValueError, "shadow 或 advisory"):
                CONFIG.configure_dsh(root, interactive=False)
            self.assertFalse((root / ".env").exists())

    def test_ubuntu_full_entrypoint_uses_the_shared_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            environment = {
                **os.environ,
                "MEDAGENT_LLM_API_KEY": "ubuntu-fixture-secret",
                "MEDAGENT_LLM_MODEL": "deepseek-flash",
                "MEDAGENT_VISION_MODEL": "deepseek-flash",
            }
            result = subprocess.run(
                [
                    ROOT / "scripts/config/ubuntu/03-configure-full.sh",
                    "--project-root",
                    root,
                    "--non-interactive",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.assertNotIn("ubuntu-fixture-secret", result.stdout + result.stderr)
            merged = CONFIG.read_partial(root / ".env")
            self.assertEqual(merged["MEDAGENT_BACKEND"], "dsh")
            self.assertEqual(
                merged["MEDAGENT_LLM_BASE_URL"],
                "https://api.deepseek.com/anthropic",
            )

    def test_each_platform_exposes_three_configuration_entries(self):
        suffixes = {"macos": ".sh", "ubuntu": ".sh", "windows": ".ps1"}
        for platform, suffix in suffixes.items():
            directory = ROOT / "scripts/config" / platform
            entries = sorted(directory.glob(f"0*-configure-*{suffix}"))
            self.assertEqual(len(entries), 3, platform)


if __name__ == "__main__":
    unittest.main()
