from __future__ import annotations

from dataclasses import replace
import unittest

from app.media_identity.external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalPreviewUnavailable,
    ExternalSourceFailure,
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
from app.media_identity.normal import (
    NormalPreviewOcrExecutor,
    NormalResourceLimits,
    NormalSamplingStage,
    OcrTextResult,
)


class FakeOcr:
    key = "fake-ocr"
    version = "1"

    def __init__(self, *, available=True, fail_on=None):
        self._available = available
        self.fail_on = fail_on
        self.calls: list[bytes] = []

    def available(self) -> bool:
        return self._available

    def cache_identity(self):
        return {"fixture": "fake-ocr-v1"}

    def recognize(self, image: bytes) -> OcrTextResult:
        self.calls.append(bytes(image))
        if self.fail_on is not None and image == self.fail_on:
            from app.media_identity.normal import NormalIdentityError
            raise NormalIdentityError("fixture OCR failure")
        return OcrTextResult(
            text=image.decode("utf-8"),
            confidence=0.8,
            details={"fixture": True},
        )


class PreviewSource:
    version = "1"

    def __init__(
        self,
        source_key: str,
        *,
        frames=(),
        payloads=None,
        available=True,
        resolve=True,
        list_error=None,
        read_error_at=None,
    ):
        self.source_key = source_key
        self._frames = tuple(frames)
        self.payloads = dict(payloads or {})
        self.available = available
        self.resolve = resolve
        self.list_error = list_error
        self.read_error_at = read_error_at
        self.read_calls: list[int] = []

    def status(self):
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=self.available,
            capabilities=frozenset({ExternalCapability.PREVIEW_FRAMES}),
        )

    def resolve_media(self, _context):
        if not self.resolve:
            return None
        return ExternalMediaRef(
            source_key=self.source_key,
            item_id=f"{self.source_key}-item",
            path="/srv/tv/show/episode.mkv",
            source_signature=f"{self.source_key}-media-v1",
        )

    def preview_frames(self, _media):
        if self.list_error is not None:
            raise self.list_error
        return self._frames

    def read_preview(self, frame):
        self.read_calls.append(int(frame.timestamp_ms))
        if self.read_error_at == int(frame.timestamp_ms):
            raise ExternalPreviewUnavailable("fixture preview missing")
        return self.payloads.get(int(frame.timestamp_ms), b"text")

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


def context(profile=IdentityProfile.NORMAL):
    return AnalyzerContext(
        media=MediaIdentityFile(
            file_id=1,
            title_id=1,
            path="/local/tv/show/episode.mkv",
            size_bytes=123,
            modified_at=1.0,
        ),
        claimed_identity=IdentityReference(
            identity_kind="episode",
            season=1,
            episode=1,
        ),
        profile=profile,
    )


def frames(source_key: str, count: int):
    return tuple(
        PreviewFrameRef(
            source_key=source_key,
            item_id=f"{source_key}-item",
            timestamp_ms=index * 1_000,
            asset_ref=f"{source_key}:{index}",
            source_signature=f"{source_key}-preview-v1",
            width=320,
            height=180,
        )
        for index in range(count)
    )


class NormalPreviewOcrExecutorTests(unittest.TestCase):
    def test_fast_profile_cannot_run_normal_ocr(self):
        source = PreviewSource("jellyfin", frames=frames("jellyfin", 3))
        executor = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            FakeOcr(),
        )
        with self.assertRaisesRegex(ValueError, "Normal or Deep"):
            executor.run(context(IdentityProfile.FAST))

    def test_unavailable_ocr_component_degrades_without_touching_sources(self):
        source = PreviewSource("jellyfin", frames=frames("jellyfin", 3))
        executor = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            FakeOcr(available=False),
        )
        result = executor.run(context())
        self.assertEqual(result.observations, ())
        self.assertEqual(result.failures, ("ocr-engine-unavailable",))
        self.assertEqual(source.read_calls, [])

    def test_first_usable_source_is_selected_without_double_counting_second_source(self):
        jellyfin_frames = frames("jellyfin", 8)
        plex_frames = frames("plex", 8)
        jellyfin = PreviewSource(
            "jellyfin",
            frames=jellyfin_frames,
            payloads={f.timestamp_ms: f"jf-{f.timestamp_ms}".encode() for f in jellyfin_frames},
        )
        plex = PreviewSource(
            "plex",
            frames=plex_frames,
            payloads={f.timestamp_ms: f"plex-{f.timestamp_ms}".encode() for f in plex_frames},
        )
        engine = FakeOcr()
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([plex, jellyfin]),
            engine,
        ).run(context(), max_stage=NormalSamplingStage.INITIAL)

        self.assertEqual(result.source_key, "jellyfin")
        self.assertEqual(len(result.observations), 5)
        self.assertEqual(len(engine.calls), 5)
        self.assertTrue(jellyfin.read_calls)
        self.assertEqual(plex.read_calls, [])
        self.assertTrue(
            all(item.source_key == "jellyfin" for item in result.observations)
        )

    def test_source_without_frames_falls_through_to_next_preview_source(self):
        jellyfin = PreviewSource("jellyfin", frames=())
        plex_frames = frames("plex", 4)
        plex = PreviewSource(
            "plex",
            frames=plex_frames,
            payloads={f.timestamp_ms: b"plex" for f in plex_frames},
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([plex, jellyfin]),
            FakeOcr(),
        ).run(context())

        self.assertEqual(result.source_key, "plex")
        self.assertEqual(len(result.observations), 4)

    def test_source_with_only_unreadable_frames_falls_through_to_next_source(self):
        class BrokenSource(PreviewSource):
            def read_preview(self, frame):
                self.read_calls.append(int(frame.timestamp_ms))
                raise ExternalPreviewUnavailable("all previews stale")

        jellyfin = BrokenSource(
            "jellyfin",
            frames=frames("jellyfin", 3),
        )
        plex_frames = frames("plex", 2)
        plex = PreviewSource(
            "plex",
            frames=plex_frames,
            payloads={f.timestamp_ms: b"plex" for f in plex_frames},
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([plex, jellyfin]),
            FakeOcr(),
        ).run(context())

        self.assertEqual(result.source_key, "plex")
        self.assertEqual(len(result.observations), 2)
        self.assertTrue(jellyfin.read_calls)
        self.assertTrue(
            any("all previews stale" in item for item in result.failures)
        )

    def test_malformed_preview_metadata_falls_through_to_next_source(self):
        broken_frame = replace(
            frames("aaa", 1)[0],
            source_signature="",
        )
        broken = PreviewSource(
            "aaa",
            frames=(broken_frame,),
            payloads={broken_frame.timestamp_ms: b"broken"},
        )
        useful_frames = frames("zzz", 1)
        useful = PreviewSource(
            "zzz",
            frames=useful_frames,
            payloads={useful_frames[0].timestamp_ms: b"useful"},
        )

        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([useful, broken]),
            FakeOcr(),
        ).run(context())

        self.assertEqual(result.source_key, "zzz")
        self.assertTrue(result.has_text)
        self.assertEqual(broken.read_calls, [])
        self.assertTrue(
            any("aaa:preview-normalize:" in item for item in result.failures)
        )

    def test_source_listing_failure_falls_through_and_is_recorded(self):
        jellyfin = PreviewSource(
            "jellyfin",
            list_error=ExternalSourceFailure("offline"),
        )
        plex_frames = frames("plex", 2)
        plex = PreviewSource(
            "plex",
            frames=plex_frames,
            payloads={f.timestamp_ms: b"ok" for f in plex_frames},
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([plex, jellyfin]),
            FakeOcr(),
        ).run(context())

        self.assertEqual(result.source_key, "plex")
        self.assertTrue(
            any("jellyfin:preview-list:offline" in item for item in result.failures)
        )

    def test_preview_failure_skips_one_frame_without_aborting_source(self):
        source_frames = frames("jellyfin", 4)
        failing_timestamp = source_frames[1].timestamp_ms
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={f.timestamp_ms: b"ok" for f in source_frames},
            read_error_at=failing_timestamp,
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            FakeOcr(),
        ).run(context())

        self.assertEqual(len(result.observations), 3)
        self.assertTrue(any(":preview:" in item for item in result.failures))

    def test_per_frame_byte_limit_skips_oversized_preview_before_ocr(self):
        source_frames = frames("jellyfin", 2)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={
                source_frames[0].timestamp_ms: b"x" * 11,
                source_frames[1].timestamp_ms: b"small",
            },
        )
        engine = FakeOcr()
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=2,
                expanded_preview_frames=2,
                max_preview_frames=2,
                max_preview_bytes_per_frame=10,
                max_preview_bytes_total=20,
            ),
        ).run(context())

        self.assertEqual(len(result.observations), 1)
        self.assertEqual(engine.calls, [b"small"])
        self.assertTrue(any("frame-byte-limit" in item for item in result.failures))

    def test_total_preview_byte_budget_stops_before_over_budget_ocr(self):
        source_frames = frames("jellyfin", 3)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={f.timestamp_ms: b"123456" for f in source_frames},
        )
        engine = FakeOcr()
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=3,
                expanded_preview_frames=3,
                max_preview_frames=3,
                max_preview_bytes_per_frame=8,
                max_preview_bytes_total=12,
            ),
        ).run(context())

        self.assertEqual(len(result.observations), 2)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(result.total_image_bytes, 12)
        self.assertTrue(result.budget_exhausted)

    def test_aggregate_byte_budget_applies_across_preview_sources(self):
        class ConditionalOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls.append(bytes(image))
                if image == b"1234567890":
                    return OcrTextResult(text="", confidence=0.9)
                return OcrTextResult(text="useful visual text", confidence=0.9)

        first_frames = frames("aaa", 1)
        second_frames = frames("zzz", 1)
        first = PreviewSource(
            "aaa",
            frames=first_frames,
            payloads={first_frames[0].timestamp_ms: b"1234567890"},
        )
        second = PreviewSource(
            "zzz",
            frames=second_frames,
            payloads={second_frames[0].timestamp_ms: b"useful"},
        )
        engine = ConditionalOcr()
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([second, first]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_preview_bytes_per_frame=10,
                max_preview_bytes_total=10,
            ),
        ).run(context())

        self.assertEqual(result.source_key, "aaa")
        self.assertEqual(result.total_image_bytes, 10)
        self.assertTrue(result.budget_exhausted)
        self.assertEqual(engine.calls, [b"1234567890"])
        self.assertEqual(second.read_calls, [])

    def test_initial_budget_at_ceiling_stops_before_touching_source(self):
        source_frames = frames("jellyfin", 1)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={source_frames[0].timestamp_ms: b"unused"},
        )
        engine = FakeOcr()
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_preview_bytes_per_frame=10,
                max_preview_bytes_total=10,
            ),
        ).run(
            context(),
            initial_image_bytes=10,
        )

        self.assertTrue(result.budget_exhausted)
        self.assertEqual(result.total_image_bytes, 10)
        self.assertEqual(source.read_calls, [])
        self.assertEqual(engine.calls, [])

    def test_ocr_failure_isolated_to_one_frame(self):
        source_frames = frames("jellyfin", 3)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={
                source_frames[0].timestamp_ms: b"one",
                source_frames[1].timestamp_ms: b"bad",
                source_frames[2].timestamp_ms: b"three",
            },
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            FakeOcr(fail_on=b"bad"),
        ).run(context())

        self.assertEqual(len(result.observations), 2)
        self.assertTrue(any(":ocr:" in item for item in result.failures))

    def test_text_budget_discards_incomplete_final_observation_and_stops(self):
        source_frames = frames("jellyfin", 3)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={
                source_frames[0].timestamp_ms: b"abcdef",
                source_frames[1].timestamp_ms: b"ghijkl",
                source_frames[2].timestamp_ms: b"mnopqr",
            },
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            FakeOcr(),
            limits=NormalResourceLimits(
                initial_preview_frames=3,
                expanded_preview_frames=3,
                max_preview_frames=3,
                max_ocr_text_chars=10,
            ),
        ).run(context())

        self.assertEqual(result.total_text_chars, 10)
        self.assertTrue(result.budget_exhausted)
        self.assertEqual(
            "".join(item.text for item in result.observations),
            "abcdef",
        )
        self.assertEqual(result.completed_frame_count, 1)

    def test_strong_initial_stage_can_stop_before_expansion(self):
        source_frames = frames("jellyfin", 40)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={frame.timestamp_ms: b"strong text" for frame in source_frames},
        )
        engine = FakeOcr()
        stages = []

        def sufficient(run, stage):
            stages.append((stage, len(run.observations)))
            return stage == NormalSamplingStage.INITIAL

        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            engine,
        ).run(
            context(),
            stage_sufficient=sufficient,
        )

        self.assertEqual(len(result.observations), 5)
        self.assertEqual(len(engine.calls), 5)
        self.assertEqual(
            stages,
            [(NormalSamplingStage.INITIAL, 5)],
        )
        self.assertTrue(
            all(
                item.stage == NormalSamplingStage.INITIAL
                for item in result.observations
            )
        )

    def test_textless_preview_source_falls_through_to_source_with_text(self):
        class ConditionalOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls.append(bytes(image))
                if image == b"blank":
                    return OcrTextResult(text="", confidence=0.9)
                return OcrTextResult(text="useful visual text", confidence=0.9)

        blank_frames = frames("aaa", 2)
        useful_frames = frames("zzz", 2)
        blank = PreviewSource(
            "aaa",
            frames=blank_frames,
            payloads={frame.timestamp_ms: b"blank" for frame in blank_frames},
        )
        useful = PreviewSource(
            "zzz",
            frames=useful_frames,
            payloads={frame.timestamp_ms: b"useful" for frame in useful_frames},
        )
        result = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([useful, blank]),
            ConditionalOcr(),
        ).run(context())

        self.assertEqual(result.source_key, "zzz")
        self.assertTrue(result.has_text)
        self.assertTrue(blank.read_calls)
        self.assertTrue(useful.read_calls)
        self.assertTrue(
            any("aaa:ocr:no-visual-text" in item for item in result.failures)
        )

    def test_cache_key_changes_when_preview_signature_changes(self):
        source_frames = frames("jellyfin", 1)
        source = PreviewSource(
            "jellyfin",
            frames=source_frames,
            payloads={0: b"text"},
        )
        executor = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([source]),
            FakeOcr(),
        )
        first = executor.run(context())

        changed_frame = replace(source_frames[0], source_signature="new-preview")
        changed_source = PreviewSource(
            "jellyfin",
            frames=(changed_frame,),
            payloads={0: b"text"},
        )
        second = NormalPreviewOcrExecutor(
            ExternalSourceRegistry([changed_source]),
            FakeOcr(),
        ).run(context())

        self.assertNotEqual(
            first.observations[0].cache_key,
            second.observations[0].cache_key,
        )


if __name__ == "__main__":
    unittest.main()
