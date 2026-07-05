"""Filesystem ``Quarantine`` adapter (download spec §8 — DECISION D10).

``promote`` does an ``os.replace`` (ATOMIC same-FS rename) of the staging file into
``quarantine_dir / <hash>``: metadata-only operation, the content is NEVER opened,
read, nor made executable (the rename does not touch permissions). A failure (FS full,
cross-device → ``OSError``; source AND target missing → ``FileNotFoundError``) PROPAGATES: the
download loop then leaves the download in ``completed`` and will retry (spec §9). But
re-promoting an ALREADY-promoted hash (source consumed by ``os.replace``, target in place) is an
idempotent no-op — so the post-promote sequence (enqueue/set_state) can be replayed without
risk after a transient failure (cf. logic-download#0). Staging and quarantine MUST
be on the same filesystem (otherwise ``os.replace`` raises — deployment constraint,
verified when wiring D-verify).
"""

import os
from pathlib import Path


class FilesystemQuarantine:
    """Quarantining via atomic rename (STRUCTURAL port satisfaction)."""

    def __init__(self, quarantine_dir: Path) -> None:
        self._quarantine_dir = quarantine_dir

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        """Atomic rename ``staging_path`` → ``quarantine_dir/<hash>`` (spec §8).

        ``os.replace`` is atomic on the same FS; it overwrites an existing target (an
        idempotent re-promote of the same hash is safe) and does not change permissions (never
        +x).

        IDEMPOTENT against an ALREADY-CONSUMED source: ``os.replace`` destroys the source, so if
        a post-promote step (enqueue/set_state) fails and the loop retries, ``promote``
        is called again with the source missing while ``quarantine/<hash>`` is already in place.
        This case is a SUCCESS (the file IS promoted), not a failure → no-op (otherwise the file
        stays stuck in ``completed`` forever, never verified — cf. logic-download#0). A source
        missing WITHOUT a target (never promoted) does raise ``FileNotFoundError``; the loop
        will retry.
        """
        # ed2k_hash: always 32 [0-9a-f] characters (guaranteed upstream — _map_partfile .hex(),
        # _CANONICAL_HASH_RE, and the SQLite CHECK constraint) → no '/' or '..' possible, no path
        # traversal outside quarantine_dir.
        target = self._quarantine_dir / ed2k_hash
        try:
            os.replace(staging_path, target)
        except FileNotFoundError:
            if target.exists():
                return  # already promoted (source consumed by an earlier promotion): idempotent
            raise  # never promoted (source AND target missing): real failure, the loop retries
