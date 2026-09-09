from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinuxDesktopOrphanCleanupContracts(unittest.TestCase):
    def test_frozen_parent_watchdog_is_cross_platform(self):
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn("_start_onefile_parent_watchdog()", sidecar)
        self.assertIn("parent_pid = os.getppid()", sidecar)
        self.assertIn("if not getattr(sys, \"frozen\", False):", sidecar)
        self.assertNotIn('if os.name != "nt" or not getattr(sys, "frozen", False):', sidecar)
        self.assertIn("if not _process_is_alive(parent_pid):", sidecar)
        self.assertIn("os._exit(0)", sidecar)


if __name__ == "__main__":
    unittest.main()
