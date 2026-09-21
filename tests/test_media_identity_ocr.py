from __future__ import annotations

from pathlib import Path
import unittest

from app.media_identity.ocr import (
    RapidOcrCpuEngine,
    RapidOcrExecutionError,
)


class FakeRapidOutput:
    def __init__(self, txts=None, scores=None):
        self.txts = txts
        self.scores = scores


class RapidOcrCpuEngineTests(unittest.TestCase):
    def test_factory_is_forced_to_onnx_cpu_backend(self) -> None:
        captured = {}

        class Runner:
            def __call__(self, image_path, *, text_score):
                captured["path_exists"] = Path(image_path).is_file()
                captured["text_score"] = text_score
                return FakeRapidOutput(
                    txts=("Restaurant Rescue", "Charlotte"),
                    scores=(0.9, 0.7),
                )

        def factory(*, params):
            captured["params"] = dict(params)
            return Runner()

        engine = RapidOcrCpuEngine(
            text_score=0.55,
            runner_factory=factory,
            package_version="3.9.2",
        )
        self.assertTrue(engine.available())
        self.assertEqual(engine.version, "1:3.9.2")

        result = engine.recognize(b"fake-jpeg-bytes")

        self.assertTrue(captured["path_exists"])
        self.assertEqual(captured["text_score"], 0.55)
        self.assertEqual(
            captured["params"]["Det.engine_type"],
            "onnxruntime",
        )
        self.assertEqual(
            captured["params"]["Cls.engine_type"],
            "onnxruntime",
        )
        self.assertEqual(
            captured["params"]["Rec.engine_type"],
            "onnxruntime",
        )
        self.assertFalse(
            captured["params"]["EngineConfig.onnxruntime.use_cuda"]
        )
        self.assertFalse(
            captured["params"]["EngineConfig.onnxruntime.use_dml"]
        )
        self.assertFalse(
            captured["params"]["EngineConfig.onnxruntime.use_cann"]
        )
        self.assertFalse(
            captured["params"]["EngineConfig.onnxruntime.use_coreml"]
        )
        self.assertEqual(
            result.text,
            "Restaurant Rescue\nCharlotte",
        )
        self.assertAlmostEqual(result.confidence, 0.8)
        self.assertEqual(result.details["backend"], "onnxruntime-cpu")
        self.assertEqual(result.details["line_count"], 2)

    def test_empty_ocr_output_is_valid_neutral_observation(self) -> None:
        class Runner:
            def __call__(self, _image_path, *, text_score):
                return FakeRapidOutput(txts=None, scores=None)

        engine = RapidOcrCpuEngine(
            runner_factory=lambda **_kwargs: Runner(),
            package_version="3.9.2",
        )
        result = engine.recognize(b"frame")
        self.assertEqual(result.text, "")
        self.assertIsNone(result.confidence)
        self.assertEqual(result.details["line_count"], 0)

    def test_empty_input_fails_before_engine_initialization(self) -> None:
        calls = []

        def factory(**_kwargs):
            calls.append(True)
            raise AssertionError("factory should not be called")

        engine = RapidOcrCpuEngine(
            runner_factory=factory,
            package_version="3.9.2",
        )
        with self.assertRaisesRegex(RapidOcrExecutionError, "empty image"):
            engine.recognize(b"")
        self.assertEqual(calls, [])

    def test_runner_failure_is_translated(self) -> None:
        class Runner:
            def __call__(self, _image_path, *, text_score):
                raise RuntimeError("fixture inference failure")

        engine = RapidOcrCpuEngine(
            runner_factory=lambda **_kwargs: Runner(),
            package_version="3.9.2",
        )
        with self.assertRaisesRegex(
            RapidOcrExecutionError,
            "could not process",
        ):
            engine.recognize(b"frame")


if __name__ == "__main__":
    unittest.main()
