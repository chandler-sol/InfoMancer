from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
from pathlib import Path
import re
from typing import Iterable


SIDECAR_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}
MAX_SIDECAR_BYTES = 10 * 1024 * 1024

_TIMESTAMP = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{1,3}\s*-->\s*"
    r"(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{1,3}.*$"
)
_TAG = re.compile(r"<[^>]+>")
_ASS_TAG = re.compile(r"\{\\[^}]*\}")
_NON_WORD = re.compile(r"[^\w']+", flags=re.UNICODE)
_SPACE = re.compile(r"\s+")

_STOPWORDS = {
    "about", "after", "again", "also", "been", "before", "being", "between",
    "could", "does", "doing", "during", "each", "from", "have", "having",
    "into", "just", "more", "most", "other", "over", "same", "some", "such",
    "than", "that", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "very", "what", "when", "where", "which", "while", "with",
    "would", "your", "youre", "were", "will", "shall", "should", "because", "until",
}


@dataclass(frozen=True)
class SidecarText:
    path: Path
    source_signature: str
    cache_key: str
    normalized_text: str


def _decode_subtitle(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def normalize_subtitle_text(text: str, extension: str = "") -> str:
    """Remove subtitle transport markup while preserving dialogue deterministically."""
    extension = extension.casefold()
    dialogue: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.casefold() == "webvtt" or line.isdigit() or _TIMESTAMP.match(line):
            continue
        if line.startswith("[") and line.endswith("]") and extension in {".ass", ".ssa"}:
            continue
        if extension in {".ass", ".ssa"}:
            if not line.casefold().startswith("dialogue:"):
                continue
            payload = line.split(":", 1)[1].lstrip()
            fields = payload.split(",", 9)
            line = fields[-1] if fields else payload
            line = line.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
            line = _ASS_TAG.sub(" ", line)
        line = html.unescape(_TAG.sub(" ", line))
        line = _SPACE.sub(" ", line).strip()
        if line:
            dialogue.append(line)
    return _SPACE.sub(" ", " ".join(dialogue)).strip().casefold()


def discover_sidecar_subtitles(media_path: str | Path) -> list[Path]:
    """Return bounded, same-basename subtitle sidecars without following symlinks."""
    media = Path(media_path)
    parent = media.parent
    try:
        entries = list(parent.iterdir())
    except OSError:
        return []
    prefix = media.stem.casefold()
    found: list[Path] = []
    for candidate in entries:
        if candidate.suffix.casefold() not in SIDECAR_EXTENSIONS:
            continue
        name = candidate.name.casefold()
        if not (name == f"{prefix}{candidate.suffix.casefold()}" or name.startswith(prefix + ".")):
            continue
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.stat().st_size > MAX_SIDECAR_BYTES:
                continue
        except OSError:
            continue
        found.append(candidate)
    return sorted(found, key=lambda value: value.name.casefold())


def read_sidecar_text(path: Path) -> SidecarText | None:
    try:
        stat = path.stat()
        if stat.st_size > MAX_SIDECAR_BYTES or path.is_symlink():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    normalized = normalize_subtitle_text(_decode_subtitle(data), path.suffix)
    signature_payload = f"{path.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}"
    source_signature = hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256((source_signature + "\0subtitle-normalizer-v1").encode("utf-8")).hexdigest()
    return SidecarText(
        path=path,
        source_signature=source_signature,
        cache_key=cache_key,
        normalized_text=normalized,
    )


def _tokens(text: str) -> list[str]:
    normalized = _NON_WORD.sub(" ", text.casefold())
    return [
        token for token in normalized.split()
        if len(token) >= 4 and token not in _STOPWORDS and not token.isdigit()
    ]


def synopsis_similarity(subtitle_text: str, synopsis: str) -> float:
    """Conservative lexical score for synopsis concepts found in subtitle dialogue."""
    synopsis_tokens = _tokens(synopsis)
    if len(synopsis_tokens) < 4:
        return 0.0
    subtitle_tokens = set(_tokens(subtitle_text))
    if not subtitle_tokens:
        return 0.0

    synopsis_unique = list(dict.fromkeys(synopsis_tokens))
    token_coverage = sum(token in subtitle_tokens for token in synopsis_unique) / len(synopsis_unique)

    synopsis_bigrams = set(zip(synopsis_tokens, synopsis_tokens[1:]))
    subtitle_list = _tokens(subtitle_text)
    subtitle_bigrams = set(zip(subtitle_list, subtitle_list[1:]))
    bigram_coverage = (
        len(synopsis_bigrams & subtitle_bigrams) / len(synopsis_bigrams)
        if synopsis_bigrams else 0.0
    )

    score = (0.7 * token_coverage) + (0.3 * bigram_coverage)
    return round(max(0.0, min(1.0, score)), 6)


def combined_synopsis_similarity(sidecars: Iterable[SidecarText], synopsis: str) -> tuple[float, str]:
    """Return the strongest sidecar score and its source reference."""
    best_score = 0.0
    best_source = ""
    for sidecar in sidecars:
        score = synopsis_similarity(sidecar.normalized_text, synopsis)
        if score > best_score:
            best_score = score
            best_source = str(sidecar.path)
    return best_score, best_source
