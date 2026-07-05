"""Download states (PURE, spec download §7 — DECISION D7).

PURE domain: no I/O. ``DownloadState`` is the CLOSED enum of a download's lifecycle on the
crawler side: ``queued`` (link added to amuled) → ``downloading`` (amuled is pulling it) →
``completed`` (bytes complete on amuled's side, still in staging) → ``quarantined`` (moved
out of staging by an atomic rename, verification queued); ``failed`` if amuled reports an
error.

The APPLICATION disk cap (spec §7) only counts ACTIVE downloads: a terminal state
(``completed``/``quarantined``/``failed``) no longer consumes in-flight download quota (a
``completed`` no longer grows and will be promoted on the next iteration). This is the only
business judgment made here; computing the sum lives in the repo adapter.
"""

from enum import StrEnum

# DECISION D7: terminal for the cap (no longer consume active quota).
_TERMINAL_STATES = frozenset({"completed", "quarantined", "failed"})


class DownloadState(StrEnum):
    """A download's lifecycle on the crawler side (closed enum, spec §7)."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


def is_terminal(state: DownloadState) -> bool:
    """``True`` if the state no longer consumes active download quota (spec §7)."""
    return state.value in _TERMINAL_STATES
