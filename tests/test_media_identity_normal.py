from __future__ import annotations

from dataclasses import replace
import unittest

from app.media_identity.external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalSourceRegistry,
    ExternalSourceStatus,
    PreviewFrameRef,
)
from app.media_identity.models import (
    AnalyzerContext,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)
from app.media_identity.sources.jellyfin import JellyfinAdapterError
from app.media_identity.sources.plex import PlexBifError
from app.media_identity.normal import (
    NormalIdentityError,
    NormalPreviewOcrExecutor,
    NormalResourceLimits,
    NormalSamplingStage,
    OcrEngine,
    OcrTextResult,
    ocr_preview_cache_key,
    select_staged_preview_frames,
)


class DummyOcr:
    key = "dummy-ocr"
    version = "1"

    def available(self) -> bool:
        return True

    def cache_identity(self):
        return {"fixture": "dummy-v1"}

    def recognize(self, image: bytes) -> OcrTextResult:
        return OcrTextResult(text=image.decode("utf-8"), confidence=0.75)


def frame(index: int, *, signature: str = "preview-v1") -> PreviewFrameRef:
    return PreviewFrameRef(
        source_key="jellyfin",
        item_id="episode-1",
        timestamp_ms=index * 1_000,
        asset_ref=f"tile:{index}",
        source_signature=signature,
        width=320,
        height=180,
    )


class NormalOcrFoundationTests(unittest.TestCase):
    def test_ocr_protocol_is_runtime_checkable_and_result_is_evidence_only(self) -> None:
        engine = DummyOcr()
        self.assertIsInstance(engine, OcrEngine)
        self.assertTrue(engine.available())
        result = engine.recognize(b"location card")
        self.assertEqual(result.text, "location card")
        self.assertEqual(result.confidence, 0.75)

    def test_ocr_confidence_must_be_normalized(self) -> None:
        with self.assertRaisesRegex(NormalIdentityError, "between 0 and 1"):
            OcrTextResult(text="bad", confidence=1.1)

    def test_resource_limits_are_positive_and_stage_ordered(self) -> None:
        with self.assertRaisesRegex(NormalIdentityError, "positive"):
            NormalResourceLimits(max_preview_frames=0)
        with self.assertRaisesRegex(NormalIdentityError, "initial frame"):
            NormalResourceLimits(
                initial_preview_frames=6,
                expanded_preview_frames=5,
                max_preview_frames=12,
            )
        with self.assertRaisesRegex(NormalIdentityError, "expanded frame"):
            NormalResourceLimits(
                initial_preview_frames=5,
                expanded_preview_frames=13,
                max_preview_frames=12,
            )
        with self.assertRaisesRegex(NormalIdentityError, "byte limit"):
            NormalResourceLimits(
                max_preview_bytes_per_frame=64,
                max_preview_bytes_total=32,
            )

    def test_staged_sampling_is_bounded_deterministic_and_progressive(self) -> None:
        frames = [frame(index) for index in range(40)]
        first = select_staged_preview_frames(frames)
        second = select_staged_preview_frames(list(reversed(frames)))

        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        self.assertEqual(
            [sample.stage for sample in first].count(NormalSamplingStage.INITIAL),
            5,
        )
        self.assertEqual(
            [sample.stage for sample in first].count(NormalSamplingStage.EXPANDED),
            4,
        )
        self.assertEqual(
            [sample.stage for sample in first].count(NormalSamplingStage.FINAL),
            3,
        )
        self.assertEqual(
            [sample.ordinal for sample in first],
            list(range(1, 13)),
        )
        timestamps = {sample.frame.timestamp_ms for sample in first}
        self.assertEqual(len(timestamps), 12)
        self.assertTrue(any(value <= 3_000 for value in timestamps))
        self.assertTrue(any(17_000 <= value <= 22_000 for value in timestamps))
        self.assertTrue(any(value >= 36_000 for value in timestamps))

    def test_custom_stage_limits_are_honored_exactly(self) -> None:
        limits = NormalResourceLimits(
            initial_preview_frames=2,
            expanded_preview_frames=3,
            max_preview_frames=4,
        )
        samples = select_staged_preview_frames(
            [frame(index) for index in range(20)],
            limits=limits,
        )
        self.assertEqual(len(samples), 4)
        self.assertEqual(
            [sample.stage for sample in samples].count(NormalSamplingStage.INITIAL),
            2,
        )
        self.assertEqual(
            [sample.stage for sample in samples].count(NormalSamplingStage.EXPANDED),
            1,
        )
        self.assertEqual(
            [sample.stage for sample in samples].count(NormalSamplingStage.FINAL),
            1,
        )

    def test_duplicate_frame_references_are_collapsed_before_sampling(self) -> None:
        duplicate = frame(4)
        samples = select_staged_preview_frames(
            [frame(1), duplicate, duplicate, frame(9)]
        )
        identities = [
            (
                sample.frame.timestamp_ms,
                sample.frame.asset_ref,
                sample.frame.source_signature,
            )
            for sample in samples
        ]
        self.assertEqual(len(identities), 3)
        self.assertEqual(len(set(identities)), 3)

    def test_invalid_frame_provenance_fails_closed(self) -> None:
        with self.assertRaisesRegex(NormalIdentityError, "provenance"):
            select_staged_preview_frames(
                [replace(frame(1), source_signature="")]
            )
        with self.assertRaisesRegex(NormalIdentityError, "negative"):
            select_staged_preview_frames(
                [replace(frame(1), timestamp_ms=-1)]
            )

    def test_ocr_cache_key_binds_external_asset_engine_and_parameters(self) -> None:
        engine = DummyOcr()
        original = frame(3)
        baseline = ocr_preview_cache_key(
            original,
            engine,
            parameters={"language": "eng", "rotate": False},
        )
        self.assertEqual(
            baseline,
            ocr_preview_cache_key(
                original,
                engine,
                parameters={"rotate": False, "language": "eng"},
            ),
        )

        changed_signature = ocr_preview_cache_key(
            replace(original, source_signature="preview-v2"),
            engine,
            parameters={"language": "eng", "rotate": False},
        )
        self.assertNotEqual(changed_signature, baseline)

        class NewEngine(DummyOcr):
            version = "2"

        changed_engine = ocr_preview_cache_key(
            original,
            NewEngine(),
            parameters={"language": "eng", "rotate": False},
        )
        self.assertNotEqual(changed_engine, baseline)

        changed_parameters = ocr_preview_cache_key(
            original,
            engine,
            parameters={"language": "spa", "rotate": False},
        )
        self.assertNotEqual(changed_parameters, baseline)

        class ChangedConfig(DummyOcr):
            def cache_identity(self):
                return {"fixture": "dummy-v2"}

        changed_engine_config = ocr_preview_cache_key(
            original,
            ChangedConfig(),
            parameters={"language": "eng", "rotate": False},
        )
        self.assertNotEqual(changed_engine_config, baseline)

    def test_adapter_specific_resolution_errors_fall_through_to_next_source(self) -> None:
        class BrokenSource:
            version = "1"

            def __init__(self, source_key, error):
                self.source_key = source_key
                self.error = error

            def status(self):
                return ExternalSourceStatus(
                    source_key=self.source_key,
                    available=True,
                    capabilities=frozenset({ExternalCapability.PREVIEW_FRAMES}),
                )

            def resolve_media(self, _context):
                raise self.error

            def preview_frames(self, _media):
                return ()

            def read_preview(self, _frame):
                return b""

            def subtitles(self, _media):
                return ()

            def read_subtitle(self, _subtitle):
                return b""

            def media_metadata(self, _media):
                return {}

            def fingerprints(self, _media):
                return ()

            def known_identity(self, _media):
                return None

        class GoodSource(BrokenSource):
            def __init__(self):
                super().__init__("zz-fallback", RuntimeError("unused"))

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id="episode-1",
                    source_signature="fallback-media-v1",
                )

            def preview_frames(self, _media):
                return (PreviewFrameRef(
                    source_key=self.source_key,
                    item_id="episode-1",
                    timestamp_ms=1000,
                    asset_ref="frame:1",
                    source_signature="fallback-preview-v1",
                    width=320,
                    height=180,
                ),)

            def read_preview(self, _frame):
                return b"usable fallback text"

        context = AnalyzerContext(
            media=MediaIdentityFile(
                file_id=1,
                title_id=1,
                path="/media/episode.mkv",
                size_bytes=100,
                modified_at=1.0,
            ),
            claimed_identity=IdentityReference(
                identity_kind="episode",
                season=1,
                episode=1,
                display_name="Episode",
            ),
            profile=IdentityProfile.NORMAL,
        )

        for source_key, error in (
            ("plex", PlexBifError("ambiguous Plex media parts")),
            ("jellyfin", JellyfinAdapterError("ambiguous Jellyfin media source")),
        ):
            with self.subTest(source_key=source_key):
                run = NormalPreviewOcrExecutor(
                    ExternalSourceRegistry([
                        BrokenSource(source_key, error),
                        GoodSource(),
                    ]),
                    DummyOcr(),
                    limits=NormalResourceLimits(
                        initial_preview_frames=1,
                        expanded_preview_frames=1,
                        max_preview_frames=1,
                    ),
                ).run(context)
                self.assertEqual(run.source_key, "zz-fallback")
                self.assertTrue(run.has_text)
                self.assertTrue(
                    any(
                        item.startswith(f"{source_key}:resolve:")
                        for item in run.failures
                    )
                )

    def test_frame_attempt_budget_is_global_across_preview_sources(self) -> None:
        class CountingOcr(DummyOcr):
            def __init__(self):
                self.calls = 0

            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                return OcrTextResult(text="", confidence=0.75)

        class ManyFramesSource:
            version = "1"

            def __init__(self, source_key):
                self.source_key = source_key
                self.read_calls = 0

            def status(self):
                return ExternalSourceStatus(
                    source_key=self.source_key,
                    available=True,
                    capabilities=frozenset({ExternalCapability.PREVIEW_FRAMES}),
                )

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id=f"{self.source_key}-episode",
                    source_signature=f"{self.source_key}-media-v1",
                )

            def preview_frames(self, _media):
                return tuple(
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id=f"{self.source_key}-episode",
                        timestamp_ms=index * 1000,
                        asset_ref=f"{self.source_key}:frame:{index}",
                        source_signature=f"{self.source_key}-preview-v1",
                        width=320,
                        height=180,
                    )
                    for index in range(20)
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"frame"

            def subtitles(self, _media):
                return ()

            def read_subtitle(self, _subtitle):
                return b""

            def media_metadata(self, _media):
                return {}

            def fingerprints(self, _media):
                return ()

            def known_identity(self, _media):
                return None

        first = ManyFramesSource("aa-first")
        second = ManyFramesSource("bb-second")
        engine = CountingOcr()
        context = AnalyzerContext(
            media=MediaIdentityFile(
                file_id=1,
                title_id=1,
                path="/media/episode.mkv",
                size_bytes=100,
                modified_at=1.0,
            ),
            claimed_identity=IdentityReference(
                identity_kind="episode",
                season=1,
                episode=1,
                display_name="Episode",
            ),
            profile=IdentityProfile.NORMAL,
        )
        run = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([first, second]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=5,
                expanded_preview_frames=9,
                max_preview_frames=12,
            ),
        ).run(context)

        self.assertEqual(first.read_calls, 12)
        self.assertEqual(second.read_calls, 0)
        self.assertEqual(engine.calls, 12)
        self.assertEqual(run.total_frame_attempts, 12)
        self.assertTrue(run.budget_exhausted)

    def test_ocr_cache_key_requires_stable_engine_identity(self) -> None:
        class MissingVersion(DummyOcr):
            version = ""

        with self.assertRaisesRegex(NormalIdentityError, "stable key and version"):
            ocr_preview_cache_key(frame(1), MissingVersion())


if __name__ == "__main__":
    unittest.main()
