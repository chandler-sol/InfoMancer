from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class SupplyChainTests(unittest.TestCase):
    def test_container_images_are_versioned_and_digest_pinned(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cloudflare = (ROOT / "compose.cloudflare.yaml").read_text(encoding="utf-8")
        self.assertRegex(
            dockerfile.splitlines()[0],
            r"^FROM python:\d+\.\d+\.\d+-slim-[a-z]+@sha256:[0-9a-f]{64}$",
        )
        self.assertRegex(
            cloudflare,
            r"image: cloudflare/cloudflared:\d{4}\.\d+\.\d+@sha256:[0-9a-f]{64}",
        )
        self.assertNotIn(":latest", cloudflare)

    def test_checkout_does_not_persist_ci_credentials(self):
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
        for workflow in workflows:
            if workflow.name.startswith("_agent_"):
                continue
            text = workflow.read_text(encoding="utf-8")
            checkout_count = len(re.findall(r"^\s*uses:\s*actions/checkout@", text, re.MULTILINE))
            if checkout_count:
                self.assertGreaterEqual(
                    text.count("persist-credentials: false"),
                    checkout_count,
                    f"{workflow.name} must disable checkout credential persistence",
                )

    def test_github_actions_use_full_commit_shas(self):
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
        for workflow in workflows:
            if workflow.name.startswith("_agent_"):
                continue
            text = workflow.read_text(encoding="utf-8")
            for reference in re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.MULTILINE):
                self.assertRegex(
                    reference,
                    r"^[^@\s]+@[0-9a-f]{40}$",
                    f"{workflow.name} contains mutable action reference {reference}",
                )

    def test_dependency_audits_cover_python_rust_and_desktop_js(self):
        tests = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        windows = (ROOT / ".github/workflows/windows-desktop.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github/workflows/windows-desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("python -m pip_audit -r requirements.txt", tests)
        self.assertIn("cargo-audit --version 0.22.2", tests)
        self.assertIn("cargo audit --file desktop/src-tauri/Cargo.lock", tests)
        self.assertIn("npm audit --audit-level=high", windows)
        self.assertIn("npm audit --audit-level=high", release)


if __name__ == "__main__":
    unittest.main()
