from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class IdentityProfile(str, Enum):
    """Progressive processing permission for one identity scan."""

    FAST = "fast"
    NORMAL = "normal"
    DEEP = "deep"

    @classmethod
    def parse(cls, value: str | "IdentityProfile") -> "IdentityProfile":
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().casefold()
        # The original design brief called the deepest tier Heavy. Keep that as an
        # input alias while using Deep consistently in persisted/product state.
        if normalized == "heavy":
            normalized = cls.DEEP.value
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError("Identity profile must be Fast, Normal, or Deep.") from exc

    @property
    def cost_rank(self) -> int:
        return {
            IdentityProfile.FAST: 0,
            IdentityProfile.NORMAL: 1,
            IdentityProfile.DEEP: 2,
        }[self]

    def permits(self, minimum: str | "IdentityProfile") -> bool:
        return self.cost_rank >= IdentityProfile.parse(minimum).cost_rank


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    CONFLICTS = "conflicts"
    NEUTRAL = "neutral"


class EvidenceCategory(str, Enum):
    """Independent evidence families used to avoid confidence double-counting."""

    CLAIMED_IDENTITY = "claimed_identity"
    CONTAINER_METADATA = "container_metadata"
    SUBTITLE_TEXT = "subtitle_text"
    PROVIDER_METADATA = "provider_metadata"
    ORDERING = "ordering"
    VISUAL_TEXT = "visual_text"
    SPEECH = "speech"
    FINGERPRINT = "fingerprint"
    EXTERNAL_IDENTITY = "external_identity"


class IdentityResultState(str, Enum):
    VERIFIED = "verified"
    PROBABLY_CORRECT = "probably_correct"
    INCONCLUSIVE = "inconclusive"
    POSSIBLE_MISMATCH = "possible_mismatch"
    LIKELY_MISMATCH = "likely_mismatch"
    STRONG_MATCH_OTHER = "strong_match_other"
    EPISODE_ORDER_CONFLICT = "episode_order_conflict"
    DUPLICATE_CONTENT_IDENTITY = "duplicate_content_identity"
    POSSIBLE_SWAPPED_EPISODES = "possible_swapped_episodes"


@dataclass(frozen=True)
class MediaIdentityFile:
    file_id: int
    title_id: int
    path: str
    size_bytes: int
    modified_at: float | None
    sha256: str | None = None


@dataclass(frozen=True)
class IdentityReference:
    """Provider-independent reference to one content identity and one optional mapping."""

    identity_kind: str
    provider: str = ""
    provider_item_id: str = ""
    expected_episode_id: int | None = None
    order_namespace: str = ""
    season: int | None = None
    episode: int | None = None
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.identity_kind.strip():
            raise ValueError("Identity reference must identify its media kind.")
        if self.provider_item_id.strip() and not self.provider.strip():
            raise ValueError("Provider item identities must identify their provider.")
        has_coordinate = self.season is not None and self.episode is not None
        if not (
            self.provider_item_id.strip()
            or self.expected_episode_id is not None
            or has_coordinate
            or self.display_name.strip()
        ):
            raise ValueError("Identity reference must include an id, coordinate, or display name.")

    @property
    def content_key(self) -> str:
        """Identify underlying content, ignoring numbering when a stable id exists."""
        kind = self.identity_kind.strip().casefold()
        provider = self.provider.strip().casefold()
        provider_item_id = self.provider_item_id.strip()
        if provider_item_id:
            return "|".join((kind, provider, "id", provider_item_id))
        if self.expected_episode_id is not None:
            return "|".join((kind, "infomancer", "expected", str(self.expected_episode_id)))

        coordinate = (
            f"{self.season}:{self.episode}"
            if self.season is not None and self.episode is not None
            else ""
        )
        if coordinate:
            return "|".join((
                kind,
                provider,
                "mapping",
                self.order_namespace.strip().casefold(),
                coordinate,
            ))

        label = " ".join(self.display_name.strip().casefold().split())
        return "|".join((kind, provider, "label", label))

    @property
    def mapping_key(self) -> str:
        """Identify one numbering/order mapping for the same underlying content."""
        coordinate = (
            f"{self.season}:{self.episode}"
            if self.season is not None and self.episode is not None
            else ""
        )
        return "|".join((
            self.content_key,
            self.order_namespace.strip().casefold(),
            coordinate,
        ))

    @property
    def stable_key(self) -> str:
        """Compatibility alias for the content identity key used by candidates."""
        return self.content_key


@dataclass(frozen=True)
class IdentityCandidate:
    identity: IdentityReference
    score: float = 0.0
    support_strength: float = 0.0
    conflict_strength: float = 0.0
    independent_categories: int = 0
    rank: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.identity.content_key


@dataclass(frozen=True)
class IdentityEvidence:
    analyzer_key: str
    analyzer_version: str
    category: EvidenceCategory
    relation: EvidenceRelation
    strength: float
    correlation_group: str
    candidate_key: str = ""
    source_kind: str = ""
    source_ref: str = ""
    timestamp_ms: int | None = None
    value: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    cache_key: str = ""
    profile: IdentityProfile = IdentityProfile.FAST

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.strength) <= 1.0:
            raise ValueError("Evidence strength must be between 0 and 1.")
        if not self.analyzer_key.strip():
            raise ValueError("Evidence must identify its analyzer.")
        if not self.analyzer_version.strip():
            raise ValueError("Evidence must identify its analyzer version.")
        if not self.correlation_group.strip():
            raise ValueError("Evidence must identify a correlation group.")


@dataclass(frozen=True)
class AnalyzerContext:
    media: MediaIdentityFile
    claimed_identity: IdentityReference
    profile: IdentityProfile
    metadata_signature: str = ""
    reusable_artifacts: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class AnalyzerResult:
    evidence: Sequence[IdentityEvidence] = ()
    artifacts: Sequence[Mapping[str, Any]] = ()
