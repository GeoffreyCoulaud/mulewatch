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
    best_tier_display: str  # best_tier or "·"
    file_count: int


# ---------------------------------------------------------------------------
# File explorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileDecision:
    """One current decision on a file: the latest match decision for a given
    ``(ed2k_hash, target_id)``, already filtered to exclude retractions and the legacy
    ``target_id == ""`` sentinel (webui spec §9). A whole-episode file carries two (``072A``
    and ``072B``); an unidentified file carries one (``tier == "catalog"``); a file with no
    current match carries none."""

    target_id: str
    tier: str


@dataclass(frozen=True)
class FileRow:
    """Summary view of a file for the explorer (paginated list)."""

    ed2k_hash: str
    size_bytes: int
    filename: str  # latest observed name
    source_count: int  # source count (latest observation)
    last_seen: str  # observed_at of the latest observation (ISO-8601 UTC)
    decisions: tuple[FileDecision, ...]  # current decisions, latest per target, 0..N
    last_verdict: str | None  # latest verification verdict (per file, not per target)


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
    """Row of the paginated file list: all fields precomputed (webui spec W-D8, Task 3/9).

    A file usually carries 0, 1, or 2 current decisions (``FileRow.decisions``, already
    excluding retractions and the legacy ``target_id == ""`` sentinel — those never reach
    this layer). Each cell aggregates the per-decision resolution, joined with ``" · "``:

    - no decisions at all → every field is the literal ``"·"``.
    - per decision, ``target_display``/``title_display`` resolve via
      ``composition.app._resolve_target_display``: ``tier == "catalog"`` → ``"unidentified"``
      / ``"·"`` (the ``keroro_large`` catch-all, the only catalog-tier rule); otherwise the
      target is looked up in the current catalogue: found → the canonical id joined with its
      seasonal locator (e.g. ``"062A / S02E11A"``) + the episode title; not found (a
      target_id no longer in the current targets.yaml) → the raw id + ``"·"``.
    - ``tier_display`` is the shared tier when all decisions agree, else each decision listed
      as ``"{target_id}: {tier}"`` joined with ``" · "``.
    - ``verdict_display`` is a single per-file value (verification is per file, not per
      target): the latest verdict, or ``"pending"`` when at least one decision exists but no
      verdict has been recorded yet.
    """

    ed2k_hash: str
    short_hash: str
    filename: str
    source_count: int
    target_display: str
    title_display: str
    size_display: str  # human_size(size_bytes)
    last_seen_display: str  # short_timestamp(last_seen)
    tier_display: str  # shared tier, or "target_id: tier" per decision joined with " · "
    verdict_display: str  # last_verdict; "pending" if decisions exist but no verdict yet;
    # "·" if there are no current decisions at all
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


@dataclass(frozen=True)
class FilesSummary:
    """Precomputed summary line for the /files explorer (spec W-D8: no template logic).

    ``summary_text`` states how many files are shown vs. catalogued; ``toggle_label`` +
    ``toggle_url`` flip between matched-only (default) and the whole catalogue.
    """

    summary_text: str
    toggle_label: str
    toggle_url: str


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
    aich_hash_display: str  # aich_hash or "·"
    observations: tuple[ObservationRow, ...]
    decisions: tuple[DecisionView, ...]  # 0 or 1 element — for template iteration
    verifications: tuple[VerificationRow, ...]
    ed2k_link: str  # precomputed from the latest observation
    # Explanation fields (None if no explanation available)
    explanation_target_id: str | None
    explanation_rules_fired: tuple[str, ...]
    explanation_tokens_matched: tuple[str, ...]
    explanation_notes: tuple[str, ...]  # 0 or 1 element — the text note itself
