import io
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from desktop import sidecar


class DesktopSidecarTests(unittest.TestCase):
    def test_inaccessible_media_root_is_skipped(self):
        blocked = OSError(1272, "Guest access is blocked")
        with mock.patch.object(sidecar.os, "scandir", side_effect=blocked):
            self.assertFalse(sidecar._root_is_accessible(Path("B:/")))

    def test_windows_drive_mask_includes_mapped_drive_letters(self):
        mask = (1 << 2) | (1 << 13) | (1 << 25)  # C, N, Z
        self.assertEqual(
            sidecar._windows_drive_strings_from_mask(mask),
            ["C:\\", "N:\\", "Z:\\"],
        )

    def test_windows_logical_drives_use_win32_drive_table(self):
        mask = (1 << 2) | (1 << 13)  # C, N
        fake_ctypes = SimpleNamespace(
            windll=SimpleNamespace(
                kernel32=SimpleNamespace(GetLogicalDrives=lambda: mask)
            )
        )
        with mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            drives = sidecar._windows_logical_drives()
        self.assertEqual([str(path) for path in drives], ["C:\\", "N:\\"])

    def test_media_root_deduplication_does_not_resolve_filesystem_paths(self):
        root = Path("B:/")
        with mock.patch.object(Path, "resolve", side_effect=OSError(1272, "blocked")):
            self.assertEqual(sidecar._dedupe_media_roots([root]), [root])

    def test_tee_stream_mirrors_output(self):
        primary = io.StringIO()
        log_stream = io.StringIO()
        tee = sidecar._TeeStream(primary, log_stream)
        tee.write("diagnostic line\n")
        tee.flush()
        self.assertEqual(primary.getvalue(), "diagnostic line\n")
        self.assertEqual(log_stream.getvalue(), "diagnostic line\n")

    def test_windows_runtime_streams_persist_even_when_launcher_captures_them(self):
        primary_out = io.StringIO()
        primary_err = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            with (
                mock.patch.object(sidecar.os, "name", "nt"),
                mock.patch.object(sidecar.sys, "stdout", primary_out),
                mock.patch.object(sidecar.sys, "stderr", primary_err),
            ):
                sidecar._ensure_runtime_streams(data_dir)
                print("stdout marker", file=sidecar.sys.stdout, flush=True)
                print("stderr marker", file=sidecar.sys.stderr, flush=True)

            log_text = (data_dir / "logs" / "desktop-core.log").read_text(encoding="utf-8")

        self.assertIn("InfoMancer desktop core diagnostics started", log_text)
        self.assertIn("stdout marker", log_text)
        self.assertIn("stderr marker", log_text)
        self.assertIn("stdout marker", primary_out.getvalue())
        self.assertIn("stderr marker", primary_err.getvalue())


if __name__ == "__main__":
    unittest.main()
