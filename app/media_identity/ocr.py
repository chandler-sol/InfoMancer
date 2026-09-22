from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
import tempfile
from typing import Any, Callable

from .normal import NormalIdentityError, OcrTextResult


RAPIDOCR_ADAPTER_VERSION = "1"


class RapidOcrUnavailableError(NormalIdentityError):
    """Raised when the optional RapidOCR CPU component is not installed."""


class RapidOcrExecutionError(NormalIdentityError):
    """Raised when the installed OCR component cannot process a bounded frame."""


class RapidOcrCpuEngine:
    """Optional RapidOCR adapter pinned to ONNX Runtime CPU inference.

    RapidOCR and onnxruntime intentionally remain optional dependencies. The
    adapter imports them lazily so InfoMancer's base install stays lightweight.
    """

    key = "rapidocr-onnx-cpu"

    def __init__(
        self,
        *,
        text_score: float = 0.5,
        runner_factory: Callable[..., Any] | None = None,
        package_version: str | None = None,
        onnxruntime_version: str | None = None,
    ) -> None:
        score = float(text_score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("RapidOCR text score must be between 0 and 1.")
        self.text_score = score
        self._runner_factory = runner_factory
        self._runner: Any | None = None
        self._package_version_override = package_version
        self._onnxruntime_version_override = onnxruntime_version

    @property
    def version(self) -> str:
        package_version = self._package_version_override
        if package_version is None:
            try:
                package_version = metadata.version("rapidocr")
            except metadata.PackageNotFoundError:
                package_version = "unavailable"
        return f"{RAPIDOCR_ADAPTER_VERSION}:{package_version}"

    def available(self) -> bool:
        if self._runner_factory is not None:
            return True
        return (
            util.find_spec("rapidocr") is not None
            and util.find_spec("onnxruntime") is not None
        )

    def cache_identity(self) -> dict[str, Any]:
        rapidocr_version = self._package_version_override
        if rapidocr_version is None:
            try:
                rapidocr_version = metadata.version("rapidocr")
            except metadata.PackageNotFoundError:
                rapidocr_version = "unavailable"
        onnxruntime_version = self._onnxruntime_version_override
        if onnxruntime_version is None:
            try:
                onnxruntime_version = metadata.version("onnxruntime")
            except metadata.PackageNotFoundError:
                onnxruntime_version = "unavailable"
        return {
            "backend": "onnxruntime-cpu",
            "rapidocr_version": str(rapidocr_version),
            "onnxruntime_version": str(onnxruntime_version),
            "text_score": round(float(self.text_score), 6),
            "model_bundle": "rapidocr-default",
        }

    @staticmethod
    def _cpu_params(engine_type: Any = "onnxruntime") -> dict[str, Any]:
        return {
            "Det.engine_type": engine_type,
            "Cls.engine_type": engine_type,
            "Rec.engine_type": engine_type,
            "EngineConfig.onnxruntime.use_cuda": False,
            "EngineConfig.onnxruntime.use_dml": False,
            "EngineConfig.onnxruntime.use_cann": False,
            "EngineConfig.onnxruntime.use_coreml": False,
        }

    def _build_runner(self) -> Any:
        if self._runner_factory is not None:
            return self._runner_factory(params=self._cpu_params())
        if not self.available():
            raise RapidOcrUnavailableError(
                "RapidOCR CPU analysis is not installed. "
                "Install the optional RapidOCR and ONNX Runtime component first."
            )
        try:
            from rapidocr import EngineType, RapidOCR
        except Exception as exc:
            raise RapidOcrUnavailableError(
                "RapidOCR CPU analysis could not be imported."
            ) from exc
        try:
            return RapidOCR(
                params=self._cpu_params(EngineType.ONNXRUNTIME)
            )
        except Exception as exc:
            raise RapidOcrUnavailableError(
                "RapidOCR CPU analysis could not initialize its ONNX models."
            ) from exc

    def _engine(self) -> Any:
        if self._runner is None:
            self._runner = self._build_runner()
        return self._runner

    def recognize(self, image: bytes) -> OcrTextResult:
        payload = bytes(image)
        if not payload:
            raise RapidOcrExecutionError("RapidOCR cannot process an empty image.")

        try:
            runner = self._engine()
            with tempfile.TemporaryDirectory(prefix="infomancer-ocr-") as temporary:
                image_path = Path(temporary) / "frame.jpg"
                image_path.write_bytes(payload)
                result = runner(str(image_path), text_score=self.text_score)
        except (RapidOcrUnavailableError, RapidOcrExecutionError):
            raise
        except Exception as exc:
            raise RapidOcrExecutionError(
                "RapidOCR could not process the preview frame."
            ) from exc

        raw_texts = getattr(result, "txts", None)
        raw_scores = getattr(result, "scores", None)
        if not raw_texts:
            return OcrTextResult(
                text="",
                confidence=None,
                details={"line_count": 0, "engine": self.key},
            )

        texts = [
            " ".join(str(value or "").split())
            for value in raw_texts
            if str(value or "").strip()
        ]
        if not texts:
            return OcrTextResult(
                text="",
                confidence=None,
                details={"line_count": 0, "engine": self.key},
            )

        scores: list[float] = []
        if raw_scores is not None:
            for value in raw_scores:
                try:
                    score = float(value)
                except (TypeError, ValueError):
                    continue
                if 0.0 <= score <= 1.0:
                    scores.append(score)

        confidence = sum(scores) / len(scores) if scores else None
        return OcrTextResult(
            text="\n".join(texts),
            confidence=confidence,
            details={
                "line_count": len(texts),
                "line_scores": scores,
                "engine": self.key,
                "engine_version": self.version,
                "backend": "onnxruntime-cpu",
            },
        )
