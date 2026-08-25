from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import source_browser
from app.source_browser import SourceBrowserError, list_folders, preview_folder


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class SourceBrowserTests(unittest.TestCase):
    def setUp(self):
        source_browser._clear_allowed_roots_cache()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.allowed = (self.root,)

    def tearDown(self):
        source_browser._clear_allowed_roots_cache()
        self.temp.cleanup()

    def test_lists_only_visible_child_folders(self):
        (self.root / "Movies").mkdir()
        (self.root / "TV").mkdir()
        (self.root / ".Trash-1000").mkdir()
        result = list_folders(str(self.root), self.allowed)
        self.assertEqual([row["name"] for row in result["folders"]], ["Movies", "TV"])

    def test_rejects_paths_outside_configured_locations(self):
        with self.assertRaises(SourceBrowserError):
            list_folders(str(self.root.parent), self.allowed)

    def test_windows_guest_access_block_gets_actionable_hint(self):
        blocked = OSError(
            1272,
            "You can't access this shared folder because your organization's "
            "security policies block unauthenticated guest access",
        )
        blocked.winerror = 1272
        with mock.patch.object(Path, "resolve", side_effect=blocked), \
             mock.patch.object(source_browser, "_root_is_accessible", return_value=False):
            with self.assertRaises(SourceBrowserError) as raised:
                source_browser._resolved(str(self.root / "Movies"))
        message = str(raised.exception)
        self.assertIn("unauthenticated guest", message)
        self.assertIn("NFS drive", message)
        self.assertIn("same Windows user session", message)
        self.assertIn("SMB", message)
        self.assertNotIn("SMB fallback", message)

    def test_windows_1272_resolver_failure_falls_back_when_direct_open_works(self):
        (self.root / "Movies").mkdir()
        blocked = OSError()
        blocked.winerror = 1272
        blocked.strerror = "Guest access is blocked during final-path resolution"
        with mock.patch.object(Path, "resolve", side_effect=blocked):
            result = list_folders(str(self.root), self.allowed)
        self.assertEqual([row["name"] for row in result["folders"]], ["Movies"])
        self.assertEqual(result["current"], str(self.root))

    def test_other_access_errors_keep_their_original_message(self):
        blocked = OSError(13, "Permission denied")
        with mock.patch.object(Path, "resolve", side_effect=blocked):
            with self.assertRaises(SourceBrowserError) as raised:
                source_browser._resolved(str(self.root / "Movies"))
        self.assertIn("Permission denied", str(raised.exception))
        self.assertNotIn("net use", str(raised.exception))

    def test_rejects_symlink_that_escapes_configured_location(self):
        outside = Path(self.temp.name).parent
        link = self.root / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable on this platform")
        with self.assertRaises(SourceBrowserError):
            list_folders(str(link), self.allowed)

    def test_inaccessible_configured_root_remains_visible_but_unavailable(self):
        blocked = self.root.parent / "Blocked network drive"
        real_accessible = source_browser._root_is_accessible

        def accessible(path: Path) -> bool:
            if path == Path(os.path.abspath(os.fspath(blocked))):
                return False
            return real_accessible(path)

        with mock.patch.object(source_browser, "_root_is_accessible", side_effect=accessible):
            result = list_folders("", (self.root, blocked))

        self.assertEqual(len(result["locations"]), 2)
        locations = {row["path"]: row for row in result["locations"]}
        self.assertTrue(locations[str(self.root)]["accessible"])
        blocked_path = str(Path(os.path.abspath(os.fspath(blocked))))
        self.assertIn(blocked_path, locations)
        self.assertFalse(locations[blocked_path]["accessible"])

    def test_inaccessible_configured_root_cannot_be_browsed(self):
        blocked = self.root.parent / "Blocked network drive"
        with mock.patch.object(source_browser, "_root_is_accessible", return_value=False):
            with self.assertRaises(SourceBrowserError):
                list_folders(str(blocked), (blocked,))

    def test_configured_root_accessibility_probe_is_short_lived_cached(self):
        with mock.patch.object(source_browser, "_root_is_accessible", return_value=True) as probe:
            first = source_browser.allowed_roots(self.allowed)
            second = source_browser.allowed_roots(self.allowed)
        self.assertEqual(first, second)
        self.assertEqual(probe.call_count, 1)

    @unittest.skipIf(os.name == "nt", "POSIX roots are intentionally case-sensitive")
    def test_posix_roots_that_differ_only_by_case_remain_distinct(self):
        roots = (Path("/media/Movies"), Path("/media/movies"))
        with mock.patch.object(source_browser, "_resolved", side_effect=lambda value: Path(value)), \
             mock.patch.object(source_browser, "_root_is_accessible", return_value=True):
            result = source_browser.allowed_roots(roots)
        self.assertEqual(result, roots)

    def test_windows_style_resolution_error_becomes_source_browser_error(self):
        with mock.patch.object(
            Path,
            "resolve",
            side_effect=OSError(1272, "Guest access is blocked"),
        ):
            with self.assertRaises(SourceBrowserError) as caught:
                source_browser._resolved(Path("B:/"))
        self.assertIn("cannot access", str(caught.exception))

    def test_unresolvable_child_folder_is_skipped(self):
        (self.root / "Movies").mkdir()
        (self.root / "Blocked").mkdir()
        real_resolved = source_browser._resolved

        def resolve_path(value):
            path = Path(value)
            if path.name == "Blocked":
                raise SourceBrowserError("blocked")
            return real_resolved(value)

        with mock.patch.object(source_browser, "_resolved", side_effect=resolve_path):
            result = list_folders(str(self.root), self.allowed)

        self.assertEqual([row["name"] for row in result["folders"]], ["Movies"])

    def test_movie_preview_understands_alphabet_and_number_buckets(self):
        for bucket in ("A", "# 0-9"):
            (self.root / bucket).mkdir()
        (self.root / "A" / "Alien (1979).mkv").write_bytes(b"a")
        (self.root / "A" / "Arrival (2016).mp4").write_bytes(b"a")
        (self.root / "# 0-9" / "1917 (2019).mkv").write_bytes(b"a")
        result = preview_folder(str(self.root), self.allowed)
        self.assertEqual(result["recommended_kind"], "movie")
        self.assertEqual(result["movie_count"], 3)
        self.assertEqual(result["bucket_count"], 2)

    def test_tv_preview_counts_series_and_recognized_episodes(self):
        for show in ("1883 (2021)", "1923 (2022)"):
            season = self.root / show / "Season 01"
            season.mkdir(parents=True)
            (season / f"{show} - S01E01.mkv").write_bytes(b"a")
            (season / f"{show} - S01E02.mkv").write_bytes(b"a")
        result = preview_folder(str(self.root), self.allowed)
        self.assertEqual(result["recommended_kind"], "tv")
        self.assertEqual(result["show_count"], 2)
        self.assertEqual(result["episode_count"], 4)

    def test_mixed_movie_buckets_and_tv_episodes_require_override(self):
        (self.root / "A").mkdir()
        (self.root / "A" / "Alien (1979).mkv").write_bytes(b"a")
        season = self.root / "Example Show" / "Season 01"
        season.mkdir(parents=True)
        (season / "Example Show - S01E01.mkv").write_bytes(b"a")
        result = preview_folder(str(self.root), self.allowed)
        self.assertEqual(result["recommended_kind"], "mixed")
        self.assertTrue(result["warning"])


class SourceBrowserUiContracts(unittest.TestCase):
    def test_sources_page_uses_the_shared_browser_owner(self):
        sources = (TEMPLATES / "sources.html").read_text(encoding="utf-8")
        self.assertIn("{% include '_source_browser.html' %}", sources)
        self.assertIn("path='source-browser.js'", sources)
        self.assertNotIn('const fetchJson = async (url) => {', sources)
        self.assertNotIn('<dialog class="source-browser"', sources)

    def test_sources_page_has_no_inline_or_dom_injected_control_workarounds(self):
        sources = (TEMPLATES / "sources.html").read_text(encoding="utf-8")
        bootstrap = (STATIC / "app-shell-bootstrap.js").read_text(encoding="utf-8")
        controller = (STATIC / "source-actions.js").read_text(encoding="utf-8")
        self.assertIn('action="/roots/check-all"', sources)
        self.assertNotIn("<script>\n", sources)
        self.assertFalse((STATIC / "source-bulk-actions.js").exists())
        self.assertNotIn("source-bulk-actions.js", bootstrap)
        self.assertIn(".root-name-editor", controller)
        self.assertIn("infomancer-source-opened", controller)

    def test_browser_client_never_assumes_error_responses_are_json(self):
        script = (STATIC / "source-browser.js").read_text(encoding="utf-8")
        self.assertIn("const text = await response.text()", script)
        self.assertIn("JSON.parse(text)", script)
        self.assertNotIn("await response.json()", script)

    def test_close_button_has_one_explicit_svg_renderer(self):
        partial = (TEMPLATES / "_source_browser.html").read_text(encoding="utf-8")
        shared = (STATIC / "dialog-controls.css").read_text(encoding="utf-8")
        local = (STATIC / "sources.css").read_text(encoding="utf-8")
        self.assertIn('class="source-browser-close"', partial)
        self.assertIn('class="source-browser-close-icon"', partial)
        self.assertIn('d="M6 6L18 18M18 6L6 18"', partial)
        self.assertNotIn(">×</button>", partial)
        self.assertNotIn(".source-browser-close", shared)
        self.assertIn(".source-browser-close::before,.source-browser-close::after", local)
        self.assertIn("content:none!important", local)
        self.assertIn(".source-browser-close-icon path", local)
        self.assertIn("stroke:currentColor", local)
        self.assertIn("stroke-linecap:round", local)


if __name__ == "__main__":
    unittest.main()
