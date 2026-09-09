from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Beta2DesktopCleanupContracts(unittest.TestCase):
    def test_startup_shell_waits_for_launcher_ui_before_showing(self):
        source = (ROOT / "desktop/src-tauri/src/external_links.rs").read_text(encoding="utf-8")
        self.assertIn(".visible(false)", source)
        self.assertIn("PageLoadEvent::Finished", source)
        self.assertIn("window.show()", source)

        launcher = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        self.assertIn('id="startup-splash"', launcher)
        self.assertIn("0.8 BETA", launcher)
        self.assertNotIn("Windows Desktop", launcher)
        self.assertNotIn("Windows application data", launcher)

    def test_linux_package_metadata_is_platform_neutral_and_appstream_ready(self):
        cargo = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
        self.assertNotIn("Native Windows shell", cargo)
        self.assertIn("Native desktop client and local workspace for InfoMancer", cargo)

        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        bundle = config["bundle"]
        self.assertEqual(bundle["shortDescription"], "Self-hosted media catalog and library intelligence")
        self.assertEqual(bundle["homepage"], "https://infomancer.media/")
        self.assertEqual(bundle["linux"]["deb"]["desktopTemplate"], "linux/InfoMancer.desktop.hbs")
        self.assertIn(
            "/usr/share/metainfo/cloud.arsenik.infomancer.metainfo.xml",
            bundle["linux"]["deb"]["files"],
        )

        desktop = (ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs").read_text(encoding="utf-8")
        self.assertIn("StartupNotify=true", desktop)
        self.assertIn("Name={{name}}", desktop)

        metainfo = (ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml").read_text(
            encoding="utf-8"
        )
        self.assertIn("<name>InfoMancer</name>", metainfo)
        self.assertIn("<summary>Self-hosted media catalog and library intelligence</summary>", metainfo)
        self.assertIn("<screenshots>", metainfo)
        self.assertIn("https://infomancer.media/", metainfo)

    def test_release_matrix_keeps_linux_on_glibc_235_floor(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
        self.assertIn("- os: ubuntu-22.04", workflow)
        self.assertIn("compat_key: glibc235", workflow)
        self.assertIn("Confirm Linux compatibility baseline", workflow)
        self.assertIn("Verify Linux GLIBC compatibility floor", workflow)
        self.assertIn("binutils", workflow)
        self.assertIn("GLIBC_2.35", workflow)
        self.assertNotIn("- os: ubuntu-latest\n            label: Linux", workflow)

    def test_local_core_conflicts_fail_fast_instead_of_waiting_for_timeout(self):
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn('marker = data_dir / "desktop-core.pid"', sidecar)
        self.assertIn("INFOMANCER_STARTUP_CONFLICT:", sidecar)
        self.assertIn("_claim_desktop_instance", sidecar)
        self.assertIn("_release_desktop_instance", sidecar)

        launcher = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("struct CoreObservation", launcher)
        self.assertIn("CommandEvent::Terminated", launcher)
        self.assertIn("STARTUP_CONFLICT_PREFIX", launcher)
        self.assertIn("wait_for_local_core(port, &observation)", launcher)

    def test_desktop_instance_marker_can_recover_from_a_stale_pid(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("infomancer_desktop_sidecar", ROOT / "desktop/sidecar.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            marker = data_dir / "desktop-core.pid"
            marker.write_text("999999999", encoding="utf-8")
            claimed = module._claim_desktop_instance(data_dir)
            self.assertEqual(claimed, marker)
            self.assertEqual(marker.read_text(encoding="utf-8"), str(os.getpid()))
            module._release_desktop_instance(claimed)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
