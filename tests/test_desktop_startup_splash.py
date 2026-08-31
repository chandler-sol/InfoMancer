from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


class DesktopStartupSplashTests(unittest.TestCase):
    def setUp(self):
        self.launcher = (ROOT / "desktop" / "ui" / "index.html").read_text(encoding="utf-8")
        self.logo = (ROOT / "desktop" / "ui" / "infomancer-lockup.svg").read_text(encoding="utf-8")

    def test_launcher_has_branded_startup_splash(self):
        self.assertIn('id="startup-splash"', self.launcher)
        self.assertIn('src="infomancer-lockup.svg"', self.launcher)
        self.assertIn('id="startup-phase"', self.launcher)
        self.assertIn('Preparing Workspace', self.launcher)
        self.assertNotIn('% complete', self.launcher)
        self.assertIn('InfoMancer logo', self.logo)

    def test_launcher_remembers_local_or_remote_target(self):
        self.assertIn("infomancer.desktop.launch-target.v1", self.launcher)
        self.assertIn("rememberTarget({kind:'local'})", self.launcher)
        self.assertIn("rememberTarget({kind:'remote', url:normalized})", self.launcher)
        self.assertIn("target.kind === 'local'", self.launcher)
        self.assertIn("openLocal({remember:false})", self.launcher)
        self.assertIn("openRemote(target.url, {remember:false})", self.launcher)

    def test_first_run_and_manual_choice_remain_available(self):
        self.assertIn("if (startup.first_run)", self.launcher)
        self.assertIn('id="bootstrap-token"', self.launcher)
        self.assertIn('id="change-installation"', self.launcher)
        self.assertIn("get('choose') === '1'", self.launcher)
        self.assertIn("forgetTarget()", self.launcher)


if __name__ == "__main__":
    unittest.main()
