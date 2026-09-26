from __future__ import annotations

from typing import Any, Mapping

from .versions import DEEP_EVIDENCE_PROMOTION_VERSION


DEEP_VISUAL_EVIDENCE_KEY = "deep-preview-ocr-synopsis"
DEEP_SPEECH_EVIDENCE_KEY = "deep-speech-synopsis"
DEEP_EVIDENCE_VERSION = str(DEEP_EVIDENCE_PROMOTION_VERSION)


def deep_evidence_metadata_is_current(
    metadata: object,
    evidence: list[Mapping[str, Any]],
    *,
    file_id: int,
) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    try:
        version = int(metadata.get("version") or 0)
        baseline_revision = int(
            metadata.get("baseline_revision") or 0
        )
        visual_count = int(
            metadata.get("visual_evidence_count") or 0
        )
        speech_count = int(
            metadata.get("speech_evidence_count") or 0
        )
    except (TypeError, ValueError):
        return False
    if (
        version != DEEP_EVIDENCE_PROMOTION_VERSION
        or baseline_revision < 1
        or visual_count < 0
        or speech_count < 0
    ):
        return False

    raw_added = metadata.get("added_candidate_keys")
    if not isinstance(raw_added, list):
        return False
    added_keys: list[str] = []
    for value in raw_added:
        if not isinstance(value, str) or not value:
            return False
        added_keys.append(value)
    if len(set(added_keys)) != len(added_keys):
        return False

    def _manifest_id(key: str) -> int | None | bool:
        value = metadata.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return False
        return value

    visual_manifest = _manifest_id(
        "visual_manifest_artifact_id"
    )
    speech_manifest = _manifest_id(
        "speech_manifest_artifact_id"
    )
    if visual_manifest is False or speech_manifest is False:
        return False

    visual_rows = [
        item for item in evidence
        if str(item.get("analyzer_key") or "")
        == DEEP_VISUAL_EVIDENCE_KEY
    ]
    speech_rows = [
        item for item in evidence
        if str(item.get("analyzer_key") or "")
        == DEEP_SPEECH_EVIDENCE_KEY
    ]
    if len(visual_rows) != visual_count:
        return False
    if len(speech_rows) != speech_count:
        return False
    if (visual_manifest is None) != (visual_count == 0):
        return False
    if (speech_manifest is None) != (speech_count == 0):
        return False

    for item in visual_rows:
        if (
            str(item.get("analyzer_version") or "")
            != DEEP_EVIDENCE_VERSION
            or str(item.get("evidence_category") or "")
            != "visual_text"
            or str(item.get("profile") or "") != "deep"
            or not str(item.get("correlation_group") or "")
        ):
            return False
        details = item.get("details")
        if not isinstance(details, Mapping):
            return False
        if details.get("manifest_artifact_id") != visual_manifest:
            return False

    expected_dialogue_group = f"subtitle-dialogue:{int(file_id)}"
    for item in speech_rows:
        if (
            str(item.get("analyzer_version") or "")
            != DEEP_EVIDENCE_VERSION
            or str(item.get("evidence_category") or "")
            != "speech"
            or str(item.get("profile") or "") != "deep"
            or str(item.get("correlation_group") or "")
            != expected_dialogue_group
        ):
            return False
        details = item.get("details")
        if not isinstance(details, Mapping):
            return False
        if details.get("manifest_artifact_id") != speech_manifest:
            return False

    return True
