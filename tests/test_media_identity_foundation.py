from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity import (
    AnalyzerContext,
    AnalyzerResult,
    EvidenceCategory,
    EvidenceRelation,
    ExternalAnalysisSource,
    IdentityAnalyzer,
    IdentityCandidate,
    IdentityEvidence,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)
from app.media_identity.external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalSourceStatus,
)


class _Analyzer:
    key = "fixture"
    version = "1"
    minimum_profile = IdentityProfile.FAST
    evidence_categories = frozenset({EvidenceCategory.SUBTITLE_TEXT})

    def available(self, context: AnalyzerContext) -> bool:
        return bool(context.media.path)

    def cache_key(self, context: AnalyzerContext) -> str:
        return f"fixture:{context.media.file_id}:{context.media.size_bytes}"

    def analyze(self, context, candidates):
        return AnalyzerResult()


class _ExternalSource:
    source_key = "fixture"
    version = "1"

    def status(self):
        return ExternalSourceStatus(
            self.source_key, True, frozenset({ExternalCapability.MEDIA_METADATA})
        )

    def resolve_media(self, context):
        return ExternalMediaRef(self.source_key, str(context.media.file_id), context.media.path)

    def preview_frames(self, media):
        return ()

    def read_preview(self, frame):
        raise LookupError("fixture has no preview frames")

    def subtitles(self, media):
        return ()

    def media_metadata(self, media):
        return {"path": media.path}

    def fingerprints(self, media):
        return ()

    def known_identity(self, media):
        return None


class MediaIdentityDomainTests(unittest.TestCase):
    def test_profiles_are_progressive_and_heavy_is_only_an_alias_for_deep(self):
        self.assertEqual(IdentityProfile.parse("FAST"), IdentityProfile.FAST)
        self.assertEqual(IdentityProfile.parse("heavy"), IdentityProfile.DEEP)
        self.assertTrue(IdentityProfile.NORMAL.permits(IdentityProfile.FAST))
        self.assertTrue(IdentityProfile.DEEP.permits(IdentityProfile.NORMAL))
        self.assertFalse(IdentityProfile.FAST.permits(IdentityProfile.NORMAL))
        with self.assertRaises(ValueError):
            IdentityProfile.parse("maximum")

    def test_content_identity_is_stable_across_alternate_order_mappings(self):
        official = IdentityReference(
            "episode", "tvdb", "123", order_namespace="official",
            season=4, episode=31,
        )
        production = IdentityReference(
            "episode", "tvdb", "123", order_namespace="production",
            season=4, episode=34,
        )
        self.assertEqual(official.content_key, production.content_key)
        self.assertEqual(official.stable_key, production.stable_key)
        self.assertNotEqual(official.mapping_key, production.mapping_key)
        self.assertEqual(IdentityCandidate(official).key, IdentityCandidate(production).key)

    def test_evidence_requires_explainable_provenance_fields(self):
        evidence = IdentityEvidence(
            analyzer_key="subtitle-text",
            analyzer_version="1",
            category=EvidenceCategory.SUBTITLE_TEXT,
            relation=EvidenceRelation.SUPPORTS,
            strength=0.8,
            correlation_group="dialogue:0-120000",
            source_kind="embedded_subtitle",
            value="distinctive phrase",
        )
        self.assertEqual(evidence.profile, IdentityProfile.FAST)
        with self.assertRaises(ValueError):
            IdentityEvidence(
                analyzer_key="subtitle-text", analyzer_version="1",
                category=EvidenceCategory.SUBTITLE_TEXT,
                relation=EvidenceRelation.SUPPORTS, strength=1.1,
                correlation_group="dialogue",
            )

    def test_analyzer_and_external_source_are_structural_protocols(self):
        self.assertIsInstance(_Analyzer(), IdentityAnalyzer)
        self.assertIsInstance(_ExternalSource(), ExternalAnalysisSource)

        media = MediaIdentityFile(1, 2, "/media/show/episode.mkv", 100, 12.0)
        claimed = IdentityReference("episode", "tvdb", "123", season=1, episode=2)
        context = AnalyzerContext(media, claimed, IdentityProfile.NORMAL)
        candidate = IdentityCandidate(claimed)
        self.assertTrue(_Analyzer().available(context))
        self.assertEqual(_Analyzer().analyze(context, [candidate]), AnalyzerResult())
        resolved = _ExternalSource().resolve_media(context)
        self.assertIsNotNone(resolved)
        self.assertEqual(_ExternalSource().media_metadata(resolved)["path"], media.path)


class MediaIdentityPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,'/media','tv','TV')"
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path)
                   VALUES (1,1,'tv','Example','/media/Example')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,seen_scan
                   ) VALUES (1,1,'/media/Example/S01E01.mkv','S01E01.mkv','mkv',
                             100,10.0,1,1,1,'Example','scan')"""
            )
            conn.execute(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (1,1,1001,1,1,'Pilot')"""
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_migration_19_creates_generic_identity_storage(self) -> None:
        with self.database.connect() as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "media_identity_scans",
                "media_identity_candidates",
                "media_identity_evidence",
                "media_identity_artifacts",
                "media_identity_confirmations",
            }.issubset(tables))

            cursor = conn.execute(
                """INSERT INTO media_identity_scans(
                     file_id,profile,claimed_identity_json,file_size_bytes,
                     file_modified_at,status,stage
                   ) VALUES (1,'fast','{}',100,10.0,'running','subtitle_text')"""
            )
            scan_id = int(cursor.lastrowid)
            candidate_key = "episode|tvdb|1001"
            conn.execute(
                """INSERT INTO media_identity_candidates(
                     scan_id,candidate_key,identity_kind,provider,provider_item_id,
                     expected_episode_id,order_namespace,season,episode,display_name,rank
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (scan_id, candidate_key, "episode", "tvdb", "1001", 1,
                 "official", 1, 1, "Pilot", 1),
            )
            conn.execute(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,value_text,profile
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (scan_id, candidate_key, "subtitle-text", "1", "subtitle_text",
                 "dialogue:0-120000", "supports", 0.8, "embedded_subtitle",
                 "distinctive phrase", "fast"),
            )
            conn.execute(
                """INSERT INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,cache_key,
                     source_kind,source_ref,source_signature,file_size_bytes,
                     file_modified_at,text_value
                   ) VALUES (1,'subtitle_text','subtitle-text','1','artifact:1',
                             'embedded_subtitle','stream:2','streamsig',100,10.0,
                             'cached subtitle text')"""
            )
            conn.execute(
                """INSERT INTO media_identity_confirmations(
                     file_id,identity_kind,provider,provider_item_id,
                     expected_episode_id,order_namespace,season,episode,display_name,
                     source_scan_id,confirmed_size_bytes,confirmed_modified_at
                   ) VALUES (1,'episode','tvdb','1001',1,'official',1,1,'Pilot',
                             ?,100,10.0)""",
                (scan_id,),
            )

            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM media_identity_evidence WHERE scan_id=?",
                    (scan_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT profile,stage FROM media_identity_scans WHERE id=?",
                    (scan_id,),
                ).fetchone()["stage"],
                "subtitle_text",
            )

    def test_identity_storage_rejects_invalid_profile_relation_and_strength(self) -> None:
        with self.database.connect() as conn:
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO media_identity_scans(file_id,profile) VALUES (1,'heavy')"
                )
            cursor = conn.execute(
                "INSERT INTO media_identity_scans(file_id,profile) VALUES (1,'deep')"
            )
            scan_id = int(cursor.lastrowid)
            with self.assertRaises(Exception):
                conn.execute(
                    """INSERT INTO media_identity_evidence(
                         scan_id,analyzer_key,analyzer_version,evidence_category,
                         correlation_group,relation,strength,profile
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (scan_id, "x", "1", "subtitle_text", "g", "supports", 1.5, "deep"),
                )
            with self.assertRaises(Exception):
                conn.execute(
                    """INSERT INTO media_identity_evidence(
                         scan_id,analyzer_key,analyzer_version,evidence_category,
                         correlation_group,relation,strength,profile
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (scan_id, "x", "1", "subtitle_text", "g", "agrees", 0.5, "deep"),
                )


if __name__ == "__main__":
    unittest.main()
