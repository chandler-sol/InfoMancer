import os
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from app import managed_tools


class ManagedTools09Tests(unittest.TestCase):
    def test_managed_path_lives_under_application_data(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"INFOMANCER_DATA_DIR": temp}, clear=False):
                expected = Path(temp).resolve() / "tools" / "ffprobe" / managed_tools.FFPROBE_MANAGED_VERSION
                self.assertEqual(managed_tools.managed_ffprobe_dir(), expected)
                self.assertEqual(managed_tools.managed_ffprobe_path().parent, expected)

    def test_resolution_prefers_override_then_managed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            managed = root / "tools" / "ffprobe" / managed_tools.FFPROBE_MANAGED_VERSION / (
                "ffprobe.exe" if os.name == "nt" else "ffprobe"
            )
            managed.parent.mkdir(parents=True)
            managed.write_bytes(b"managed")
            with mock.patch.dict(os.environ, {"INFOMANCER_DATA_DIR": str(root)}, clear=False):
                os.environ.pop("INFOMANCER_FFPROBE", None)
                self.assertEqual(
                    Path(managed_tools.resolve_ffprobe_executable()).resolve(),
                    managed.resolve(),
                )
                custom = root / "custom-ffprobe"
                os.environ["INFOMANCER_FFPROBE"] = str(custom)
                self.assertEqual(
                    Path(managed_tools.resolve_ffprobe_executable()).resolve(),
                    custom.resolve(),
                )

    def test_bootstrap_stages_and_atomically_installs_bundled_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            bundle = root / "bundle" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b"verified-bundled-ffprobe")
            (bundle.parent / "FFPROBE_LICENSE.txt").write_text("license", encoding="utf-8")
            healthy = managed_tools.ToolStatus("ffprobe", True, "managed", str(bundle), "6.1.1")
            with mock.patch.dict(os.environ, {"INFOMANCER_DATA_DIR": str(root / "data")}, clear=False), \
                 mock.patch.object(managed_tools, "bundled_ffprobe_path", return_value=bundle), \
                 mock.patch.object(managed_tools, "verify_ffprobe", return_value=healthy):
                result = managed_tools.bootstrap_managed_ffprobe_from_bundle()
                target = managed_tools.managed_ffprobe_path()

            self.assertTrue(result.healthy)
            self.assertEqual(target.read_bytes(), b"verified-bundled-ffprobe")
            self.assertEqual(
                (target.parent / "FFPROBE_LICENSE.txt").read_text(encoding="utf-8"),
                "license",
            )
            self.assertFalse(target.with_name(target.name + ".staging").exists())


if __name__ == "__main__":
    unittest.main()
