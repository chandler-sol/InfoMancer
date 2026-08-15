from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.host_updater import UpdateError, verify_release_tag


class HostUpdaterTests(unittest.TestCase):
    def test_unsigned_tag_is_rejected(self):
        completed = subprocess.CompletedProcess(["git"], 1, "", "bad signature")
        with tempfile.TemporaryDirectory() as temporary, patch("scripts.host_updater.subprocess.run", return_value=completed):
            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))

    def test_trusted_signature_fingerprint_is_enforced(self):
        fingerprint = "A" * 40
        completed = subprocess.CompletedProcess(
            ["git"], 0, "", f"[GNUPG:] VALIDSIG {fingerprint} 2026-01-01 0 4 0 1 10 00 {fingerprint}"
        )
        with tempfile.TemporaryDirectory() as temporary, patch("scripts.host_updater.subprocess.run", return_value=completed):
            verify_release_tag("v1.2.3", Path(temporary), {fingerprint})
            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary), {"B" * 40})


if __name__ == "__main__":
    unittest.main()
