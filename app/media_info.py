from __future__ import annotations

import json
import subprocess
from pathlib import Path


class MediaInspectionError(RuntimeError):
    """A media-inspection failure with separate human and technical context."""

    def __init__(
        self, message: str, *, headline: str = "Media inspection could not finish",
        technical_detail: str = "",
    ) -> None:
        self.headline = headline
        self.user_message = message
        self.technical_detail = technical_detail.strip()
        combined = message
        if self.technical_detail:
            combined += f" Technical detail: {self.technical_detail}"
        super().__init__(combined)

    @property
    def log_detail(self) -> str:
        if not self.technical_detail:
            return self.user_message
        return f"{self.user_message}\n\nFFprobe output:\n{self.technical_detail}"


def _ffprobe_error(path: Path, technical: str) -> MediaInspectionError:
    """Translate common FFprobe output into an actionable explanation."""
    normalized = technical.casefold()
    if "permission denied" in normalized or "operation not permitted" in normalized:
        return MediaInspectionError(
            "InfoMancer does not have permission to read this file. Check the "
            "file and folder permissions for the account or container running "
            "InfoMancer, then inspect it again.",
            headline="InfoMancer cannot read this file",
            technical_detail=technical,
        )
    if "no such file or directory" in normalized:
        return MediaInspectionError(
            "The file moved, was renamed, or its storage disconnected after it "
            "was cataloged. Reconnect the storage and rescan this source.",
            headline="The cataloged file is no longer available",
            technical_detail=technical,
        )
    if "input/output error" in normalized or "i/o error" in normalized:
        return MediaInspectionError(
            "The storage device returned a read error. Confirm the disk or "
            "network mount is healthy and that the file plays normally. If "
            "other files fail too, check the drive, connection, and system logs "
            "before scanning again.",
            headline="The storage device could not read this file",
            technical_detail=technical,
        )
    if "ebml header parsing failed" in normalized:
        return MediaInspectionError(
            "This MKV does not contain a readable Matroska header. It is usually "
            "an incomplete or damaged copy, a download that is still in "
            "progress, or a different file type with an .mkv extension. Try "
            "playing the file. If playback also fails, replace or recopy it; if "
            "playback works, remux it into a new MKV and inspect the new file.",
            headline="This MKV appears incomplete or damaged",
            technical_detail=technical,
        )
    if "moov atom not found" in normalized:
        return MediaInspectionError(
            "This MP4 is missing the index information needed to read it. It is "
            "usually incomplete or damaged. Try playing the file; if it fails, "
            "replace or recopy it. If it plays, remux it into a new MP4.",
            headline="This MP4 appears incomplete or damaged",
            technical_detail=technical,
        )
    if "invalid data found when processing input" in normalized:
        return MediaInspectionError(
            f"{path.name} does not appear to contain readable media data. Check "
            "whether the copy is complete and whether its extension matches the "
            "real file type. Replace, recopy, or remux the file before inspecting "
            "it again.",
            headline="This file is not readable as media",
            technical_detail=technical,
        )
    return MediaInspectionError(
        "FFprobe could not identify this file's media information. Try playing "
        "the file and confirm its storage is connected. If it plays normally, "
        "remuxing it may repair unusual container metadata; otherwise replace "
        "or recopy the file.",
        headline="FFprobe could not understand this media file",
        technical_detail=technical,
    )


def _number(value, cast, default=None):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _dynamic_range(video: dict) -> str:
    transfer = str(video.get("color_transfer") or "").lower()
    primaries = str(video.get("color_primaries") or "").lower()
    side_data = " ".join(
        str(item.get("side_data_type") or "") for item in video.get("side_data_list", [])
    ).lower()
    if "smpte2084" in transfer or "arib-std-b67" in transfer:
        if "dovi" in side_data or "dolby vision" in side_data:
            return "Dolby Vision"
        return "HDR10" if "smpte2084" in transfer else "HLG"
    if "bt2020" in primaries:
        return "HDR"
    return "SDR"


def inspect_media(path: Path, timeout: int = 90) -> dict:
    if not path.exists() or not path.is_file():
        raise MediaInspectionError(
            "The media file is no longer available at its cataloged path. "
            "Reconnect its storage or rescan the source if the file moved.",
            headline="The cataloged file is no longer available",
        )
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,bit_rate,format_name:stream=index,codec_type,codec_name,width,height,channels,color_transfer,color_primaries:stream_side_data",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise MediaInspectionError(
            "FFprobe is not installed in the InfoMancer environment. Rebuild "
            "the application image or install FFmpeg, then try again.",
            headline="FFprobe is not installed",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaInspectionError(
            "FFprobe did not finish within the inspection time limit. Confirm "
            "the storage is responsive and the file plays normally, then try "
            "again. A consistently slow or failing file may need to be recopied.",
            headline="Media inspection timed out",
        ) from exc
    if result.returncode:
        technical = (result.stderr or "").strip()
        raise _ffprobe_error(path, technical[:1000])
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaInspectionError(
            "FFprobe returned information InfoMancer could not interpret. The "
            "file was not changed. Try inspecting it again after updating "
            "InfoMancer; if it continues, include this event in a diagnostic export.",
            headline="InfoMancer could not interpret FFprobe's response",
        ) from exc
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    media_format = payload.get("format") or {}
    return {
        "runtime_seconds": _number(media_format.get("duration"), float),
        "width": _number(video.get("width"), int),
        "height": _number(video.get("height"), int),
        "video_codec": str(video.get("codec_name") or "").upper(),
        "audio_codec": str(audio.get("codec_name") or "").upper(),
        "audio_channels": _number(audio.get("channels"), int),
        "bitrate": _number(media_format.get("bit_rate"), int),
        "container": str(media_format.get("format_name") or "").split(",", 1)[0].upper(),
        "dynamic_range": _dynamic_range(video) if video else "",
    }
