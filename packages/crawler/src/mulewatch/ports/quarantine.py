"""``Quarantine`` port: move a completed file into quarantine (spec download §8/§10).

The crawler NEVER READS the content of a downloaded file (§10.3 MVP: the catalog's subject
is the file, never the person; we only verify/read after a safe quarantine). ``promote`` is
a METADATA-only operation: move (rename) the file from staging to ``quarantine/<hash>``,
without ever opening it or making it executable. The verifier (D-verify) will read the
quarantined file — not the crawler. The Protocol stub fits on ONE line (the ``def`` is
covered at class creation).
"""

from pathlib import Path
from typing import Protocol


class Quarantine(Protocol):
    """Quarantine contract (spec §8). ``promote`` only raises on an FS failure.

    IDEMPOTENT: re-promoting an ALREADY-promoted hash (source consumed, target in place) is a
    silent success — the download loop safely retries the post-promote sequence after a
    transient enqueue/set_state failure (cf. logic-download#0).
    """

    def promote(self, staging_path: Path, ed2k_hash: str) -> None: ...
