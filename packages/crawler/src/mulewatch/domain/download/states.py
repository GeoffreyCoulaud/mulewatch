"""Download states (PURE, spec download §7 — DECISION D7).

PURE domain: no I/O. ``DownloadState`` is the CLOSED enum of a download's lifecycle on the
crawler side: ``queued`` (link added to amuled) → ``downloading`` (amuled is pulling it) →
``completed`` (amuled finished it and shares it from its IncomingDir); ``failed`` if amuled
reports an error. Nothing reads or moves the file afterwards.

The APPLICATION disk cap (spec §7) only counts ACTIVE downloads: a terminal state
(``completed``/``failed``) no longer consumes in-flight download quota. This is the only
business judgment made here; computing the sum lives in the repo adapter.
"""

from enum import StrEnum

# DECISION D7: terminal for the cap (no longer consume active quota).
_TERMINAL_STATES = frozenset({"completed", "failed"})


class DownloadState(StrEnum):
    """A download's lifecycle on the crawler side (closed enum, spec §7)."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


def is_terminal(state: DownloadState) -> bool:
    """``True`` if the state no longer consumes active download quota (spec §7)."""
    return state.value in _TERMINAL_STATES
