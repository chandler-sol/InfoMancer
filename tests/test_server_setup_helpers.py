from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ServerSetupHelperContracts(unittest.TestCase):
    def test_release_builder_includes_guided_setup_helpers(self):
        builder = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")
        for filename in (
            "START-HERE.txt",
            "Setup-InfoMancer.cmd",
            "Setup-InfoMancer.command",
            "Setup-InfoMancer.ps1",
            "setup-infomancer.sh",
        ):
            with self.subTest(filename=filename):
                self.assertIn(f'"{filename}"', builder)
                self.assertTrue((ROOT / filename).is_file(), filename)

    def test_start_here_is_short_and_platform_specific(self):
        text = (ROOT / "START-HERE.txt").read_text(encoding="utf-8")
        self.assertIn("Docker Engine 24.0 or newer", text)
        self.assertIn("Docker Compose 2.20 or newer", text)
        self.assertIn("checks Docker for you", text)
        self.assertIn("Double-click", text)
        self.assertIn("Setup-InfoMancer.cmd", text)
        self.assertIn("Setup-InfoMancer.command", text)
        self.assertIn("./setup-infomancer.sh", text)
        self.assertIn("one-time setup code", text)
        self.assertIn("Do not port-forward port 8787", text)

    def test_unix_helper_automates_first_run_and_checks_docker(self):
        script = (ROOT / "setup-infomancer.sh").read_text(encoding="utf-8")
        for expected in (
            'MIN_DOCKER_ENGINE="24.0.0"',
            'MIN_DOCKER_COMPOSE="2.20.0"',
            "version_at_least",
            "docker compose version --short",
            "docker version --format '{{.Server.Version}}'",
            "docker info",
            "https://docs.docker.com/desktop/setup/install/mac-install/",
            "https://docs.docker.com/engine/install/",
            "INFOMANCER_UID",
            "INFOMANCER_GID",
            "compose.media.yaml",
            "dc up -d --build",
            "data/bootstrap-token",
            'http://127.0.0.1:8787/setup',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)

    def test_unix_helper_has_valid_shell_syntax_when_shell_is_available(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("sh is not available on this runner")
        result = subprocess.run(
            [shell, "-n", str(ROOT / "setup-infomancer.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_helper_automates_first_run_and_checks_docker(self):
        script = (ROOT / "Setup-InfoMancer.ps1").read_text(encoding="utf-8")
        wrapper = (ROOT / "Setup-InfoMancer.cmd").read_text(encoding="utf-8")
        for expected in (
            "$MinimumDockerEngine = [version]'24.0.0'",
            "$MinimumDockerCompose = [version]'2.20.0'",
            "Test-MinimumVersion",
            "docker compose version --short",
            "docker version --format '{{.Server.Version}}'",
            "Docker.DockerDesktop",
            "winget",
            "https://docs.docker.com/desktop/setup/install/windows-install/",
            "docker info",
            "compose.media.yaml",
            "up -d --build",
            "data\\bootstrap-token",
            'http://127.0.0.1:8787/setup',
            "Get-NetIPAddress",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)
        self.assertIn("-ExecutionPolicy Bypass", wrapper)
        self.assertIn("Setup-InfoMancer.ps1", wrapper)

    def test_install_guide_makes_helper_the_default_path(self):
        guide = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
        self.assertIn("## Quick install", guide)
        self.assertIn("Docker Engine **24.0 or newer**", guide)
        self.assertIn("Docker Compose **2.20 or newer**", guide)
        self.assertIn("https://docs.docker.com/desktop/setup/install/windows-install/", guide)
        self.assertIn("https://docs.docker.com/desktop/setup/install/mac-install/", guide)
        self.assertIn("https://docs.docker.com/engine/install/", guide)
        self.assertIn("Double-click:\n\n`Setup-InfoMancer.cmd`", guide)
        self.assertIn("`Setup-InfoMancer.command`", guide)
        self.assertIn("./setup-infomancer.sh", guide)
        self.assertIn("[Manual Server Setup](SERVER_MANUAL.md)", guide)
        self.assertIn("Only change `source:`", guide)
        self.assertIn("InfoMancer first-run bootstrap token:", guide)
        self.assertIn("http://SERVER-IP:8787", guide)
        self.assertNotIn("## Manual / advanced Server setup", guide)


if __name__ == "__main__":
    unittest.main()
