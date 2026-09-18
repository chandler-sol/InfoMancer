from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import os
from pathlib import Path
import re
import stat
from typing import Iterable, Mapping


SIDECAR_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}
MAX_SIDECAR_BYTES = 10 * 1024 * 1024
MAX_SIDECAR_COUNT = 16
MAX_TOTAL_SIDECAR_BYTES = 32 * 1024 * 1024
MAX_SIDECAR_SELECTION_POOL = 64
NORMALIZER_VERSION = "subtitle-normalizer-v1"

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
class SidecarIdentity:
    path: Path
    source_signature: str
    cache_key: str
    size_bytes: int
    modified_ns: int
    device_id: int | None
    inode_id: int | None


@dataclass(frozen=True)
class SidecarText:
    path: Path
    source_signature: str
    cache_key: str
    normalized_text: str


@dataclass(frozen=True)
class TextCorpus:
    tokens: frozenset[str]
    bigrams: frozenset[tuple[str, str]]


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
    prefix = media.stem.casefold()
    pool: list[tuple[tuple[str, str], Path]] = []
    try:
        for candidate in parent.iterdir():
            suffix = candidate.suffix.casefold()
            if suffix not in SIDECAR_EXTENSIONS:
                continue
            folded_name = candidate.name.casefold()
            if not (
                folded_name == f"{prefix}{suffix}"
                or folded_name.startswith(prefix + ".")
            ):
                continue
            sort_key = (folded_name, candidate.name)
            if len(pool) < MAX_SIDECAR_SELECTION_POOL:
                pool.append((sort_key, candidate))
                pool.sort(key=lambda item: item[0])
                continue
            if sort_key < pool[-1][0]:
                pool[-1] = (sort_key, candidate)
                pool.sort(key=lambda item: item[0])
    except OSError:
        return []

    found: list[Path] = []
    total_bytes = 0
    for _, candidate in pool:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            size_bytes = int(candidate.stat().st_size)
        except OSError:
            continue
        if size_bytes > MAX_SIDECAR_BYTES:
            continue
        if len(found) >= MAX_SIDECAR_COUNT:
            break
        if total_bytes + size_bytes > MAX_TOTAL_SIDECAR_BYTES:
            continue
        found.append(candidate)
        total_bytes += size_bytes
    return found


def sidecar_identity(path: Path) -> SidecarIdentity | None:
    """Fingerprint a sidecar from path/size/mtime without reading its contents."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        stat = path.stat()
        if stat.st_size > MAX_SIDECAR_BYTES:
            return None
        resolved = path.resolve()
    except OSError:
        return None
    device_id = int(getattr(stat, "st_dev", 0) or 0) or None
    inode_id = int(getattr(stat, "st_ino", 0) or 0) or None
    signature_payload = "\0".join((
        str(resolved),
        str(stat.st_size),
        str(stat.st_mtime_ns),
        "" if device_id is None else str(device_id),
        "" if inode_id is None else str(inode_id),
    ))
    source_signature = hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256(
        (source_signature + "\0" + NORMALIZER_VERSION).encode("utf-8")
    ).hexdigest()
    return SidecarIdentity(
        path=path,
        source_signature=source_signature,
        cache_key=cache_key,
        size_bytes=int(stat.st_size),
        modified_ns=int(stat.st_mtime_ns),
        device_id=device_id,
        inode_id=inode_id,
    )


def _same_descriptor_identity(
    descriptor_stat: os.stat_result,
    identity: SidecarIdentity,
) -> bool:
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or int(descriptor_stat.st_size) != identity.size_bytes
        or int(descriptor_stat.st_mtime_ns) != identity.modified_ns
    ):
        return False

    descriptor_device = int(getattr(descriptor_stat, "st_dev", 0) or 0) or None
    descriptor_inode = int(getattr(descriptor_stat, "st_ino", 0) or 0) or None
    if identity.device_id is not None and descriptor_device != identity.device_id:
        return False
    if identity.inode_id is not None and descriptor_inode != identity.inode_id:
        return False
    return True


def _open_sidecar_descriptor(path: Path) -> int | None:
    """Open a sidecar for reading without following symlinks where supported."""
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)

    try:
        path_stat = os.lstat(path)
        if stat.S_ISLNK(path_stat.st_mode):
            return None
        descriptor = os.open(path, flags | nofollow)
    except OSError:
        return None

    if nofollow:
        return descriptor

    # Windows does not expose O_NOFOLLOW through Python. Verify that the opened
    # object is the same non-symlink directory entry observed immediately before
    # os.open(), using device/inode when the platform supplies meaningful values.
    try:
        descriptor_stat = os.fstat(descriptor)
        current_path_stat = os.lstat(path)
        if stat.S_ISLNK(current_path_stat.st_mode):
            os.close(descriptor)
            return None
        for field in ("st_dev", "st_ino"):
            expected = getattr(current_path_stat, field, None)
            actual = getattr(descriptor_stat, field, None)
            if expected not in (None, 0) and actual not in (None, 0) and expected != actual:
                os.close(descriptor)
                return None
    except OSError:
        os.close(descriptor)
        return None

    return descriptor


def _read_sidecar_bounded(
    path: Path,
    identity: SidecarIdentity,
) -> bytes | None:
    descriptor = _open_sidecar_descriptor(path)
    if descriptor is None:
        return None

    try:
        before = os.fstat(descriptor)
        if (
            not _same_descriptor_identity(before, identity)
            or int(before.st_size) > MAX_SIDECAR_BYTES
        ):
            return None

        chunks: list[bytes] = []
        remaining = MAX_SIDECAR_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)

        data = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError:
        return None
    finally:
        os.close(descriptor)

    if (
        len(data) > MAX_SIDECAR_BYTES
        or len(data) != identity.size_bytes
        or not _same_descriptor_identity(after, identity)
        or int(after.st_size) != int(before.st_size)
        or int(after.st_mtime_ns) != int(before.st_mtime_ns)
    ):
        return None
    return data


def read_sidecar_text(
    path: Path,
    identity: SidecarIdentity | None = None,
) -> SidecarText | None:
    """Read one sidecar only if its bounded descriptor identity stays stable."""
    identity = identity or sidecar_identity(path)
    if identity is None:
        return None

    data = _read_sidecar_bounded(path, identity)
    if data is None:
        return None

    normalized = normalize_subtitle_text(_decode_subtitle(data), path.suffix)
    return SidecarText(
        path=path,
        source_signature=identity.source_signature,
        cache_key=identity.cache_key,
        normalized_text=normalized,
    )


def _tokens(text: str) -> list[str]:
    normalized = _NON_WORD.sub(" ", text.casefold())
    return [
        token for token in normalized.split()
        if len(token) >= 4 and token not in _STOPWORDS and not token.isdigit()
    ]


def text_corpus(text: str) -> TextCorpus:
    tokens = _tokens(text)
    return TextCorpus(
        tokens=frozenset(tokens),
        bigrams=frozenset(zip(tokens, tokens[1:])),
    )


def synopsis_similarity_from_corpus(corpus: TextCorpus, synopsis: str) -> float:
    """Score synopsis concepts against a pre-tokenized subtitle corpus."""
    synopsis_tokens = _tokens(synopsis)
    if len(synopsis_tokens) < 4 or not corpus.tokens:
        return 0.0

    synopsis_unique = list(dict.fromkeys(synopsis_tokens))
    token_coverage = sum(token in corpus.tokens for token in synopsis_unique) / len(synopsis_unique)

    synopsis_bigrams = set(zip(synopsis_tokens, synopsis_tokens[1:]))
    bigram_coverage = (
        len(synopsis_bigrams & corpus.bigrams) / len(synopsis_bigrams)
        if synopsis_bigrams else 0.0
    )

    score = (0.7 * token_coverage) + (0.3 * bigram_coverage)
    return round(max(0.0, min(1.0, score)), 6)


def synopsis_similarity(subtitle_text: str, synopsis: str) -> float:
    """Conservative lexical score for synopsis concepts found in subtitle dialogue."""
    return synopsis_similarity_from_corpus(text_corpus(subtitle_text), synopsis)


def combined_synopsis_similarity(
    sidecars: Iterable[SidecarText],
    synopsis: str,
    corpora: Mapping[str, TextCorpus] | None = None,
) -> tuple[float, str]:
    """Return the strongest sidecar score and its source reference."""
    best_score = 0.0
    best_source = ""
    for sidecar in sidecars:
        corpus = (
            corpora.get(sidecar.cache_key)
            if corpora is not None
            else None
        ) or text_corpus(sidecar.normalized_text)
        score = synopsis_similarity_from_corpus(corpus, synopsis)
        if score > best_score:
            best_score = score
            best_source = str(sidecar.path)
    return best_score, best_source
