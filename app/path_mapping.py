from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|//)")


class PathMappingError(ValueError):
    """Raised when a path mapping cannot be proven safe and unambiguous."""


@dataclass(frozen=True)
class ParsedPath:
    anchor: str
    parts: tuple[str, ...]
    windows: bool


@dataclass(frozen=True)
class PathMapping:
    source_key: str
    external_root: str
    local_root: str
    priority: int = 100
    enabled: bool = True

    def __post_init__(self) -> None:
        source_key = str(self.source_key or "").strip().casefold()
        external_root = str(self.external_root or "").strip()
        local_root = str(self.local_root or "").strip()
        if not source_key:
            raise PathMappingError("A path mapping requires a source key.")
        if not external_root:
            raise PathMappingError("A path mapping requires an external root.")
        if not local_root:
            raise PathMappingError("A path mapping requires a local root.")
        parse_absolute_path(external_root)
        local = Path(local_root).expanduser()
        if not local.is_absolute():
            raise PathMappingError("A path mapping local root must be absolute.")
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "external_root", external_root)
        object.__setattr__(self, "local_root", str(local))
        object.__setattr__(self, "priority", int(self.priority))

    @property
    def external_depth(self) -> int:
        return len(parse_absolute_path(self.external_root).parts)


@dataclass(frozen=True)
class PathTranslation:
    source_key: str
    external_path: str
    local_path: str
    mapping: PathMapping


def parse_absolute_path(value: str) -> ParsedPath:
    """Parse a Windows or POSIX absolute path without using host OS semantics."""
    raw = str(value or "").strip()
    if "\x00" in raw:
        raise PathMappingError("Media paths cannot contain a null byte.")
    if _WINDOWS_ABSOLUTE.match(raw):
        path = PureWindowsPath(raw)
        anchor = path.anchor
        if not anchor:
            raise PathMappingError("An external media path must be absolute.")
        parts = tuple(path.parts[1:])
        if ".." in parts:
            raise PathMappingError("Media paths cannot contain parent traversal.")
        return ParsedPath(anchor=anchor, parts=parts, windows=True)

    path = PurePosixPath(raw)
    if not path.is_absolute():
        raise PathMappingError("An external media path must be absolute.")
    parts = tuple(path.parts[1:])
    if ".." in parts:
        raise PathMappingError("Media paths cannot contain parent traversal.")
    return ParsedPath(anchor=path.anchor, parts=parts, windows=False)


def relative_parts(value: str, root: str) -> tuple[str, ...]:
    """Return component-safe relative parts when value belongs to root."""
    parsed_value = parse_absolute_path(value)
    parsed_root = parse_absolute_path(root)
    if parsed_value.windows != parsed_root.windows:
        raise PathMappingError("The path uses a different style than the mapping root.")

    def comparable(part: str) -> str:
        return part.casefold() if parsed_value.windows else part

    if comparable(parsed_value.anchor) != comparable(parsed_root.anchor):
        raise PathMappingError("The path is outside the mapping root.")
    if len(parsed_value.parts) < len(parsed_root.parts):
        raise PathMappingError("The path is outside the mapping root.")
    for actual, expected in zip(parsed_value.parts, parsed_root.parts):
        if comparable(actual) != comparable(expected):
            raise PathMappingError("The path is outside the mapping root.")
    return parsed_value.parts[len(parsed_root.parts):]


def validate_local_relative_components(
    parts: tuple[str, ...], *, windows_host: bool | None = None
) -> None:
    """Reject external components that the local host could reinterpret as rooted paths."""
    use_windows = os.name == "nt" if windows_host is None else bool(windows_host)
    path_type = PureWindowsPath if use_windows else PurePosixPath
    for part in parts:
        parsed = path_type(part)
        if (
            not part
            or parsed.anchor
            or (use_windows and (parsed.drive or parsed.root))
            or len(parsed.parts) != 1
            or parsed.parts[0] != part
        ):
            raise PathMappingError(
                "An external media path contains a component that is unsafe on this host."
            )


def translate_path(value: str, external_root: str, local_root: str | Path) -> str:
    """Translate one external absolute path into a local absolute path."""
    relative = relative_parts(value, external_root)
    validate_local_relative_components(relative)
    destination = Path(local_root).expanduser()
    if not destination.is_absolute():
        raise PathMappingError("A path mapping local root must be absolute.")
    return str(destination.joinpath(*relative))


def validate_local_absolute_path(value: str | Path) -> None:
    """Reject malformed host-local paths before mapping selection."""
    raw = str(value or "").strip()
    if "\x00" in raw:
        raise PathMappingError("Local media paths cannot contain a null byte.")
    path = PureWindowsPath(raw) if os.name == "nt" else PurePosixPath(raw)
    if not path.is_absolute():
        raise PathMappingError("Local media paths must be absolute.")
    if ".." in path.parts:
        raise PathMappingError("Local media paths cannot contain parent traversal.")


def local_relative_parts(value: str | Path, root: str | Path) -> tuple[str, ...]:
    """Return lexical host-local relative parts using current-OS case semantics."""
    raw_value = str(value or "").strip()
    raw_root = str(root or "").strip()
    if "\x00" in raw_value or "\x00" in raw_root:
        raise PathMappingError("Local media paths cannot contain a null byte.")
    if os.name == "nt":
        value_path = PureWindowsPath(raw_value)
        root_path = PureWindowsPath(raw_root)
        if not value_path.is_absolute() or not root_path.is_absolute():
            raise PathMappingError("Local media paths and mapping roots must be absolute.")
        if ".." in value_path.parts or ".." in root_path.parts:
            raise PathMappingError("Local media paths cannot contain parent traversal.")

        def comparable(part: str) -> str:
            return part.casefold()
    else:
        value_path = PurePosixPath(raw_value)
        root_path = PurePosixPath(raw_root)
        if not value_path.is_absolute() or not root_path.is_absolute():
            raise PathMappingError("Local media paths and mapping roots must be absolute.")
        if ".." in value_path.parts or ".." in root_path.parts:
            raise PathMappingError("Local media paths cannot contain parent traversal.")

        def comparable(part: str) -> str:
            return part

    value_parts = value_path.parts
    root_parts = root_path.parts
    if len(value_parts) < len(root_parts):
        raise PathMappingError("The local path is outside the mapping root.")
    for actual, expected in zip(value_parts, root_parts):
        if comparable(actual) != comparable(expected):
            raise PathMappingError("The local path is outside the mapping root.")
    return tuple(value_parts[len(root_parts):])


def external_join(root: str, relative: tuple[str, ...]) -> str:
    parsed = parse_absolute_path(root)
    if parsed.windows:
        return str(PureWindowsPath(root).joinpath(*relative))
    return str(PurePosixPath(root).joinpath(*relative))


class ExternalPathMapper:
    """Component-aware source path translation with deterministic precedence."""

    def __init__(self, mappings: Iterable[PathMapping] = ()) -> None:
        self._mappings = tuple(mapping for mapping in mappings if mapping.enabled)

    def mappings_for(self, source_key: str) -> tuple[PathMapping, ...]:
        key = str(source_key or "").strip().casefold()
        return tuple(mapping for mapping in self._mappings if mapping.source_key == key)

    def reverse_translate(
        self, source_key: str, local_path: str | Path
    ) -> PathTranslation | None:
        key = str(source_key or "").strip().casefold()
        validate_local_absolute_path(local_path)
        matches: list[tuple[PathMapping, str]] = []
        for mapping in self.mappings_for(key):
            try:
                relative = local_relative_parts(local_path, mapping.local_root)
                external_path = external_join(mapping.external_root, relative)
            except PathMappingError:
                continue
            matches.append((mapping, external_path))

        if not matches:
            return None

        best_priority = min(mapping.priority for mapping, _ in matches)
        priority_matches = [
            (mapping, external_path)
            for mapping, external_path in matches
            if mapping.priority == best_priority
        ]
        best_depth = max(
            len(Path(mapping.local_root).parts)
            for mapping, _ in priority_matches
        )
        finalists = [
            (mapping, external_path)
            for mapping, external_path in priority_matches
            if len(Path(mapping.local_root).parts) == best_depth
        ]
        destinations = {external_path for _, external_path in finalists}
        if len(destinations) != 1:
            raise PathMappingError(
                "More than one equally preferred path mapping matches this local path."
            )

        mapping, external_path = finalists[0]
        return PathTranslation(
            source_key=key,
            external_path=external_path,
            local_path=str(local_path),
            mapping=mapping,
        )

    def translate(self, source_key: str, external_path: str) -> PathTranslation | None:
        key = str(source_key or "").strip().casefold()
        parse_absolute_path(external_path)
        matches: list[tuple[PathMapping, str]] = []
        for mapping in self.mappings_for(key):
            try:
                local_path = translate_path(
                    external_path,
                    mapping.external_root,
                    mapping.local_root,
                )
            except PathMappingError:
                continue
            matches.append((mapping, local_path))

        if not matches:
            return None

        best_priority = min(mapping.priority for mapping, _ in matches)
        priority_matches = [
            (mapping, local_path)
            for mapping, local_path in matches
            if mapping.priority == best_priority
        ]
        best_depth = max(mapping.external_depth for mapping, _ in priority_matches)
        finalists = [
            (mapping, local_path)
            for mapping, local_path in priority_matches
            if mapping.external_depth == best_depth
        ]

        destinations = {local_path for _, local_path in finalists}
        if len(destinations) != 1:
            raise PathMappingError(
                "More than one equally preferred path mapping matches this external path."
            )

        mapping, local_path = finalists[0]
        return PathTranslation(
            source_key=key,
            external_path=str(external_path),
            local_path=local_path,
            mapping=mapping,
        )
