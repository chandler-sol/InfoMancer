import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsDesktopContractTests(unittest.TestCase):
    def test_desktop_version_matches_application_alpha(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        match = re.search(r'APP_VERSION = "([^"]+)"', main)
        self.assertIsNotNone(match)
        expected_version = match.group(1)
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        sidecar_match = re.search(r'DESKTOP_VERSION = "([^"]+)"', sidecar)
        self.assertIsNotNone(sidecar_match)
        self.assertEqual(config["version"], expected_version)
        self.assertEqual(sidecar_match.group(1), expected_version)
        self.assertEqual(config["productName"], "InfoMancer")
        self.assertEqual(config["identifier"], "cloud.arsenik.infomancer")

    def test_nsis_uninstall_is_zero_residue_but_update_safe(self):
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        nsis = config["bundle"]["windows"]["nsis"]
        self.assertEqual(nsis["installerHooks"], "./windows/hooks.nsh")
        self.assertEqual(nsis["customLanguageFiles"]["English"], "./windows/English.nsh")
        hooks = (ROOT / "desktop/src-tauri/windows/hooks.nsh").read_text(encoding="utf-8")
        for path in (
            r'$APPDATA\cloud.arsenik.infomancer',
            r'$LOCALAPPDATA\cloud.arsenik.infomancer',
            r'$TEMP\InfoMancer',
        ):
            self.assertIn(path, hooks)
        self.assertIn("$DeleteAppDataCheckboxState != 1", hooks)
        self.assertIn("$UpdateMode == 1", hooks)
        self.assertIn("$UpdateMode != 1", hooks)
        self.assertIn("--recovery-output", hooks)
        language = (ROOT / "desktop/src-tauri/windows/English.nsh").read_text(encoding="utf-8")
        self.assertIn("all InfoMancer application data will be permanently removed", language)

    def test_uninstaller_recovery_uses_verified_portable_package(self):
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn("RecoveryPackageService", sidecar)
        self.assertIn("service.verify(output)", sidecar)
        self.assertIn("Choose a recovery destination outside", sidecar)

    def test_desktop_bootstrap_token_is_not_exposed_in_child_command_line(self):
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn('.env("INFOMANCER_BOOTSTRAP_TOKEN", &bootstrap_token)', rust)
        self.assertNotIn('"--bootstrap-token"', rust)
        self.assertIn('os.getenv("INFOMANCER_BOOTSTRAP_TOKEN", "")', sidecar)
        self.assertIn('os.environ["INFOMANCER_BOOTSTRAP_TOKEN"] = bootstrap_token', sidecar)

    def test_local_core_startup_allows_windows_cold_boot(self):
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("LOCAL_CORE_STARTUP_TIMEOUT: Duration = Duration::from_secs(60)", rust)
        self.assertIn("while started.elapsed() < LOCAL_CORE_STARTUP_TIMEOUT", rust)
        self.assertIn("Local core became HTTP-ready after", rust)
        self.assertIn("Check {} for startup details", rust)
        self.assertIn("launcher_log_path().display()", rust)
        self.assertNotIn("for _ in 0..120", rust)

    def test_windows_onefile_core_does_not_outlive_desktop_parent(self):
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        runtime = (ROOT / "app/runtime.py").read_text(encoding="utf-8")
        self.assertIn("_start_onefile_parent_watchdog()", sidecar)
        self.assertIn("os.getppid()", sidecar)
        self.assertIn('os.environ["INFOMANCER_RUNTIME_CONTEXT"] = "desktop"', sidecar)
        self.assertIn('f"desktop:{host}:{os.getpid()}', runtime)
        self.assertIn("_desktop_owner_pid", runtime)
        self.assertIn("_process_is_alive", runtime)

    def test_windows_core_log_survives_tauri_stream_capture(self):
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn('log_dir / "desktop-core.log"', sidecar)
        self.assertIn("class _TeeStream", sidecar)
        self.assertIn("_TeeStream(original_stdout, stream)", sidecar)
        self.assertIn("_TeeStream(original_stderr, stream)", sidecar)
        self.assertNotIn("sys.stdout is not None and sys.stderr is not None", sidecar)

    def test_large_desktop_launcher_scales_up_without_global_zoom(self):
        launcher = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("@media(min-width:1600px) and (min-height:900px)", launcher)
        self.assertIn("width:min(1180px,calc(100vw - 72px))", launcher)
        self.assertIn(".mark { width:52px; height:52px", launcher)
        self.assertIn("button { padding:12px 15px", launcher)
        self.assertIn("input { padding:11px 12px", launcher)
        self.assertNotIn("zoom:", launcher)

    def test_remote_http_pages_are_not_granted_tauri_ipc(self):
        capability = json.loads((ROOT / "desktop/src-tauri/capabilities/launcher.json").read_text(encoding="utf-8"))
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(capability["windows"], ["main"])
        self.assertNotIn("remote", capability)
        self.assertNotIn("dangerousRemoteDomainIpcAccess", config["app"]["security"])
        self.assertNotIn("dangerousRemoteUrlIpcAccess", config["app"]["security"])

    def test_updater_uses_signed_github_release_channel(self):
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn("tauri_plugin_updater", rust)
        self.assertIn("desktop-alpha/latest.json", rust)
        self.assertIn("INFOMANCER_UPDATER_PUBLIC_KEY", rust)
        self.assertIn("download_and_install", rust)
        capability = json.loads((ROOT / "desktop/src-tauri/capabilities/launcher.json").read_text(encoding="utf-8"))
        self.assertEqual(capability["permissions"], ["core:default"])
        release_config = json.loads((ROOT / "desktop/src-tauri/tauri.release.conf.json").read_text(encoding="utf-8"))
        self.assertTrue(release_config["bundle"]["createUpdaterArtifacts"])

    def test_release_workflow_keeps_private_key_out_of_source(self):
        workflow = (ROOT / ".github/workflows/windows-desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("secrets.TAURI_SIGNING_PRIVATE_KEY", workflow)
        self.assertIn("vars.TAURI_UPDATER_PUBLIC_KEY", workflow)
        self.assertIn("desktop-alpha", workflow)
        self.assertNotIn("BEGIN PRIVATE KEY", workflow)

    def test_release_workflow_is_owner_gated_and_version_bound(self):
        workflow = (ROOT / ".github/workflows/windows-desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("if: github.actor == github.repository_owner", workflow)
        self.assertIn("timeout-minutes: 60", workflow)
        self.assertIn("Verify release tag matches application version", workflow)
        self.assertIn("desktop/src-tauri/tauri.conf.json", workflow)
        self.assertIn('$env:GITHUB_REF_NAME -ne $expectedTag', workflow)


if __name__ == "__main__":
    unittest.main()
