"""PRECOMPUTED view-models (webui spec W-D8): the templates only iterate and interpolate
these fields — no template-side logic."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageStatus:
    status: str  # "found" | "partial" | "none"
    best_tier: str | None  # "download" | "notify" | "catalog" | None
    file_count: int


# ---------------------------------------------------------------------------
# Dashboard — coverage per target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetCoverageRow:
    """Dashboard row: a target's coverage."""

    target_id: str
    title: str
    status: str  # "found" | "partial" | "none"
    best_tier_display: str  # best_tier or "—"
    file_count: int


# ---------------------------------------------------------------------------
# File explorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileRow:
    """Summary view of a file for the explorer (paginated list)."""

    ed2k_hash: str
    size_bytes: int
    filename: str  # latest observed name
    source_count: int  # source count (latest observation)
    last_seen: str  # observed_at of the latest observation (ISO-8601 UTC)
    target_id: str | None  # latest decision
    tier: str | None  # tier of the latest decision
    last_verdict: str | None  # latest verification verdict


# ---------------------------------------------------------------------------
# File detail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationRow:
    """One entry in the observations timeline."""

    id: int
    filename: str
    size_bytes: int
    source_count: int
    complete_source_count: int
    media_length_sec: int | None
    bitrate_kbps: int | None
    keyword: str
    observed_at: str
    node_id: str


@dataclass(frozen=True)
class DecisionView:
    """Latest match decision for a file."""

    target_id: str
    rule_name: str
    tier: str
    decided_at: str
    node_id: str


@dataclass(frozen=True)
class VerificationRow:
    """A verification result."""

    id: int
    verdict: str
    verified_at: str
    node_id: str


@dataclass(frozen=True)
class FileDetail:
    """Full view of a file: timeline + decision + verdicts."""

    ed2k_hash: str
    size_bytes: int
    aich_hash: str | None
    observations: tuple[ObservationRow, ...]
    decision: DecisionView | None  # None if no decision
    verifications: tuple[VerificationRow, ...]


# ---------------------------------------------------------------------------
# Node state (local.db)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadRow:
    """An active or completed download (downloads table)."""

    ed2k_hash: str
    target_id: str
    state: str
    queued_at: str
    completed_at: str | None
    size_bytes: int


@dataclass(frozen=True)
class VerifTaskRow:
    """A verification task (verification_tasks table)."""

    ed2k_hash: str
    status: str
    attempts: int
    enqueued_at: str
    lease_until: str | None


@dataclass(frozen=True)
class SchedulerEntry:
    """A scheduler key/value pair (precomputed for the template)."""

    key: str
    value: str


@dataclass(frozen=True)
class NodeState:
    """Full node state: downloads, verifications, scheduler, identity."""

    downloads: tuple[DownloadRow, ...]
    verification_tasks: tuple[VerifTaskRow, ...]
    scheduler: Mapping[str, str]  # all scheduler_state pairs
    node_id: str | None  # None if absent from node_runtime
    created_at: str | None  # None if absent from node_runtime


# ---------------------------------------------------------------------------
# File explorer — display row (precomputed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileRowDisplay:
    """Row of the paginated file list: all fields precomputed."""

    ed2k_hash: str
    short_hash: str
    filename: str
    size_bytes: int
    source_count: int
    last_seen: str
    target_id_display: str  # target_id or "—"
    tier_display: str  # tier or "—"
    verdict_display: str  # last_verdict or "—"
    ed2k_link: str


@dataclass(frozen=True)
class PageNav:
    """Paginated navigation of a list — precomputed handler-side (spec W-D8: no logic in the
    template). ``prev_url``/``next_url`` are ``None`` when the end is reached; the template
    iterates ``(url,) if url`` to render the link when it exists.
    """

    page: int
    prev_url: str | None
    next_url: str | None


# ---------------------------------------------------------------------------
# File detail — display view (precomputed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileDetailDisplay:
    """Full view of a file: all fields precomputed for the template.

    ``decisions`` is a tuple of 0 or 1 element to allow the iteration
    ``{% for d in file.decisions %}`` in the template (the guard forbids {% if %}).
    ``explanation_notes`` is empty if there is no explanation, containing a single element
    (the text note) otherwise — allows conditional iteration without {% if %}.
    """

    ed2k_hash: str
    size_bytes: int
    aich_hash_display: str  # aich_hash or "—"
    observations: tuple[ObservationRow, ...]
    decisions: tuple[DecisionView, ...]  # 0 or 1 element — for template iteration
    verifications: tuple[VerificationRow, ...]
    ed2k_link: str  # precomputed from the latest observation
    # Explanation fields (None if no explanation available)
    explanation_target_id: str | None
    explanation_rules_fired: tuple[str, ...]
    explanation_tokens_matched: tuple[str, ...]
    explanation_notes: tuple[str, ...]  # 0 or 1 element — the text note itself
