from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Beta2SecondPassContracts(unittest.TestCase):
    def test_desktop_update_check_is_bounded_and_offline_safe(self):
        ui = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("UPDATE_AUTO_CHECK_BUDGET_MS = 500", ui)
        self.assertIn("Promise.race", ui)
        self.assertIn("update-notes", ui)
        self.assertIn("InfoMancer can still run normally", ui)
        self.assertIn("Install it now, or start InfoMancer normally and update later.", ui)

    def test_linux_launcher_log_is_persistent(self):
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("XDG_DATA_HOME", rust)
        self.assertIn('data.push(".local")', rust)
        self.assertIn('data.push("share")', rust)
        self.assertIn('data.push("cloud.arsenik.infomancer")', rust)

    def test_tvdb_credentials_are_page_specific_not_late_workspace_polish(self):
        template = (ROOT / "app/templates/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "app/static/settings-tvdb-credentials.js").read_text(encoding="utf-8")
        workspace = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/settings_quick_actions.py").read_text(encoding="utf-8")
        self.assertIn("settings-tvdb-credentials.js", template)
        self.assertIn("tvdb-credential-dialog", template)
        self.assertIn("/settings/metadata/tvdb-credentials", template)
        self.assertIn("X-InfoMancer-Async", script)
        self.assertIn("X-CSRF-Token", script)
        self.assertNotIn("settings-polish.js", workspace)
        self.assertIn("candidate.test_connection()", route)
        self.assertIn("provider_secrets.update", route)

    def test_navigation_keeps_outgoing_page_and_subtle_root_handoff(self):
        script = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
        styles = (ROOT / "app/static/app-navigation.css").read_text(encoding="utf-8")
        stable = (ROOT / "app/static/navigation-paint-stability.css").read_text(encoding="utf-8")
        self.assertIn("coverOutgoingPage", script)
        self.assertIn("app-navigation-leaving", script)
        self.assertIn("Keep outgoing workspace painted directly", styles)
        self.assertIn("visibility:visible !important", styles)
        self.assertIn("infomancer-root-reveal", stable)
        self.assertIn("from { opacity: .985; }", stable)

    def test_linux_desktop_identity_is_retained_but_native_release_is_gated(self):
        desktop = (ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs").read_text(encoding="utf-8")
        metainfo = (ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml").read_text(encoding="utf-8")
        distribution = (ROOT / "docs/FFPROBE_DISTRIBUTION.md").read_text(encoding="utf-8")
        self.assertIn("StartupWMClass=infomancer-desktop", desktop)
        self.assertIn("<id>InfoMancer.desktop</id>", metainfo)
        self.assertIn("Linux and macOS native FFprobe bundling are not approved", distribution)
        self.assertFalse((ROOT / ".github/workflows/draft-08-release.yml").exists())

    def test_macos_auditor_is_retained_while_native_ffprobe_release_is_gated(self):
        auditor = (ROOT / "scripts/verify_macos_minos.py").read_text(encoding="utf-8")
        distribution = (ROOT / "docs/FFPROBE_DISTRIBUTION.md").read_text(encoding="utf-8")
        self.assertIn('"xcrun", "vtool", "-show-build"', auditor)
        self.assertIn("newer than supported", auditor)
        self.assertIn("Linux and macOS native FFprobe bundling are not approved", distribution)
        self.assertFalse((ROOT / ".github/workflows/draft-08-release.yml").exists())

    def test_beta2_firefighting_workflows_are_removed(self):
        obsolete = (
            "beta2-first-pass-finalize.yml", "beta2-platform-repair.yml",
            "linux-beta2-compat.yml", "macos-intel-priority.yml",
            "publish-beta2-complete-prerelease.yml", "publish-intel-release.yml",
            "validate-beta2-full-tests.yml", "apply-beta2-second-pass.yml",
            "apply-beta2-second-pass-v2.yml", "apply-beta2-second-pass-v3.yml",
            "draft-08-release.yml", "windows-preview.yml",
        )
        for name in obsolete:
            self.assertFalse((ROOT / ".github/workflows" / name).exists(), name)
        self.assertFalse((ROOT / "scripts/beta2_second_pass_apply.py").exists())
        self.assertFalse((ROOT / "scripts/beta2_second_pass_apply_v3.py").exists())


if __name__ == "__main__":
    unittest.main()
