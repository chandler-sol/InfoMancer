import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stamp_build_version.py"


class BuildVersionStamp09Tests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("stamp_build_version_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_stamp_updates_runtime_and_packaging_versions_together(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            for relative in (
                "app/main.py",
                "desktop/sidecar.py",
                "desktop/src-tauri/Cargo.toml",
                "desktop/src-tauri/tauri.conf.json",
                "desktop/package.json",
                "desktop/package-lock.json",
            ):
                source = ROOT / relative
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            module.ROOT = temporary
            module.stamp("0.9.0-dev.2375")

            main = (temporary / "app/main.py").read_text(encoding="utf-8")
            sidecar = (temporary / "desktop/sidecar.py").read_text(encoding="utf-8")
            cargo = (temporary / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
            tauri = json.loads((temporary / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
            package = json.loads((temporary / "desktop/package.json").read_text(encoding="utf-8"))
            lock = json.loads((temporary / "desktop/package-lock.json").read_text(encoding="utf-8"))

            self.assertIn('APP_VERSION = "0.9.0-dev.2375"', main)
            self.assertIn('DESKTOP_VERSION = "0.9.0-dev.2375"', sidecar)
            self.assertIn('version = "0.9.0-dev.2375"', cargo)
            self.assertEqual(tauri["version"], "0.9.0-dev.2375")
            self.assertEqual(package["version"], "0.9.0-dev.2375")
            self.assertEqual(lock["version"], "0.9.0-dev.2375")
            self.assertEqual(lock["packages"][""]["version"], "0.9.0-dev.2375")

    def test_stamp_rejects_invalid_version(self):
        module = self.load_module()
        with self.assertRaises(ValueError):
            module.stamp("dev/latest")


if __name__ == "__main__":
    unittest.main()
