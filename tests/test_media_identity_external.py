from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.media_identity.external import (
    ExternalAnalysisSource,
    ExternalCapability,
    ExternalMediaRef,
    ExternalSourceRegistry,
    ExternalSourceRegistryError,
    ExternalSourceStatus,
)
from app.path_mapping import ExternalPathMapper, PathMapping, PathMappingError


class DummySource:
    source_key = "dummy"
    version = "1"

    def __init__(self, *, available: bool = True, fail_status: bool = False) -> None:
        self.available = available
        self.fail_status = fail_status

    def status(self):
        if self.fail_status:
            raise RuntimeError("offline")
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=self.available,
            capabilities=frozenset(
                {ExternalCapability.MEDIA_METADATA, ExternalCapability.PREVIEW_FRAMES}
            ),
        )

    def resolve_media(self, context):
        return ExternalMediaRef(source_key=self.source_key, item_id="item-1")

    def preview_frames(self, media):
        return ()

    def read_preview(self, frame):
        return b""

    def subtitles(self, media):
        return ()

    def read_subtitle(self, subtitle):
        return b""

    def media_metadata(self, media):
        return {}

    def fingerprints(self, media):
        return ()

    def known_identity(self, media):
        return None


class AlternateDummySource(DummySource):
    source_key = "other"


class ExternalPathMapperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.local = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_windows_source_paths_are_case_insensitive_on_any_host(self):
        mapper = ExternalPathMapper(
            [PathMapping("plex", r"D:\TV", str(self.local / "tv"))]
        )
        translated = mapper.translate(
            "PLEX",
            r"d:\tv\The Show\Season 01\Episode.mkv",
        )
        self.assertIsNotNone(translated)
        self.assertEqual(
            translated.local_path,
            str(self.local / "tv" / "The Show" / "Season 01" / "Episode.mkv"),
        )

    def test_posix_source_paths_remain_case_sensitive(self):
        mapper = ExternalPathMapper(
            [PathMapping("jellyfin", "/srv/TV", str(self.local / "tv"))]
        )
        self.assertIsNone(
            mapper.translate("jellyfin", "/srv/tv/Show/Episode.mkv")
        )

    def test_component_boundary_prevents_raw_prefix_matches(self):
        mapper = ExternalPathMapper(
            [PathMapping("plex", "/srv/tv", str(self.local / "tv"))]
        )
        self.assertIsNone(
            mapper.translate("plex", "/srv/tv-archive/Show/Episode.mkv")
        )

    def test_explicit_priority_beats_more_specific_lower_preference_mapping(self):
        mapper = ExternalPathMapper(
            [
                PathMapping("plex", "/srv", str(self.local / "general"), priority=10),
                PathMapping("plex", "/srv/tv", str(self.local / "tv"), priority=20),
            ]
        )
        translated = mapper.translate("plex", "/srv/tv/Show/Episode.mkv")
        self.assertEqual(
            translated.local_path,
            str(self.local / "general" / "tv" / "Show" / "Episode.mkv"),
        )

    def test_specific_root_wins_when_priority_is_equal(self):
        mapper = ExternalPathMapper(
            [
                PathMapping("plex", "/srv", str(self.local / "general"), priority=10),
                PathMapping("plex", "/srv/tv", str(self.local / "tv"), priority=10),
            ]
        )
        translated = mapper.translate("plex", "/srv/tv/Show/Episode.mkv")
        self.assertEqual(
            translated.local_path,
            str(self.local / "tv" / "Show" / "Episode.mkv"),
        )

    def test_equal_preference_conflict_fails_closed(self):
        mapper = ExternalPathMapper(
            [
                PathMapping("plex", "/srv/tv", str(self.local / "tv-a"), priority=10),
                PathMapping("plex", "/srv/tv", str(self.local / "tv-b"), priority=10),
            ]
        )
        with self.assertRaisesRegex(PathMappingError, "equally preferred"):
            mapper.translate("plex", "/srv/tv/Show/Episode.mkv")

    def test_disabled_mapping_is_ignored(self):
        mapper = ExternalPathMapper(
            [PathMapping("plex", "/srv/tv", str(self.local / "tv"), enabled=False)]
        )
        self.assertIsNone(
            mapper.translate("plex", "/srv/tv/Show/Episode.mkv")
        )

    def test_reverse_translation_maps_local_file_back_to_windows_source_path(self):
        local_root = self.local / "tv"
        mapper = ExternalPathMapper(
            [PathMapping("plex", r"D:\TV", str(local_root))]
        )
        translated = mapper.reverse_translate(
            "plex", local_root / "Show" / "Season 01" / "Episode.mkv"
        )
        self.assertIsNotNone(translated)
        self.assertEqual(
            translated.external_path,
            r"D:\TV\Show\Season 01\Episode.mkv",
        )

    def test_reverse_translation_keeps_component_boundaries(self):
        mapper = ExternalPathMapper(
            [PathMapping("jellyfin", "/srv/tv", str(self.local / "tv"))]
        )
        self.assertIsNone(
            mapper.reverse_translate(
                "jellyfin", self.local / "tv-archive" / "Show" / "Episode.mkv"
            )
        )

    def test_reverse_translation_uses_specific_local_root_when_priority_ties(self):
        mapper = ExternalPathMapper(
            [
                PathMapping("jellyfin", "/srv", str(self.local), priority=10),
                PathMapping("jellyfin", "/srv/tv", str(self.local / "tv"), priority=10),
            ]
        )
        translated = mapper.reverse_translate(
            "jellyfin", self.local / "tv" / "Show" / "Episode.mkv"
        )
        self.assertEqual(
            translated.external_path,
            "/srv/tv/Show/Episode.mkv",
        )

    def test_mapping_requires_absolute_roots(self):
        with self.assertRaisesRegex(PathMappingError, "absolute"):
            PathMapping("plex", "relative/tv", str(self.local / "tv"))
        with self.assertRaisesRegex(PathMappingError, "absolute"):
            PathMapping("plex", "/srv/tv", "relative/tv")


class ExternalSourceRegistryTests(unittest.TestCase):
    def test_protocol_and_registry_accept_read_only_source(self):
        source = DummySource()
        self.assertIsInstance(source, ExternalAnalysisSource)
        registry = ExternalSourceRegistry([source])
        self.assertEqual(registry.keys(), ("dummy",))
        self.assertIs(registry.get("DUMMY"), source)

    def test_duplicate_source_keys_are_rejected(self):
        with self.assertRaisesRegex(ExternalSourceRegistryError, "already registered"):
            ExternalSourceRegistry([DummySource(), DummySource()])

    def test_available_for_filters_by_status_and_capability(self):
        available = DummySource()
        unavailable = AlternateDummySource(available=False)
        registry = ExternalSourceRegistry([available, unavailable])
        self.assertEqual(
            registry.available_for(ExternalCapability.PREVIEW_FRAMES),
            (available,),
        )

    def test_status_failure_degrades_source_instead_of_raising(self):
        source = DummySource(fail_status=True)
        registry = ExternalSourceRegistry([source])
        statuses = registry.statuses()
        self.assertEqual(len(statuses), 1)
        self.assertFalse(statuses[0].available)
        self.assertIn("offline", statuses[0].detail)
        self.assertEqual(
            registry.available_for(ExternalCapability.MEDIA_METADATA),
            (),
        )


if __name__ == "__main__":
    unittest.main()
