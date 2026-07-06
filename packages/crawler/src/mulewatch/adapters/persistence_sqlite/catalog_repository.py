"""``SqliteCatalogRepository``: ``FileObservation``/``MatchDecision`` → durable rows.

The adapter stamps what the domain ignores (data-model spec §3): ``observed_at``/
``decided_at`` (injectable clock, ``utc_now`` by default) and ``node_id`` (given at the
constructor — plan C will read it from ``LocalStateRepository``). ``raw_meta`` is serialized
as a JSON LIST of pairs (``[["0x0308", "0"], …]``), wire order and duplicates preserved,
``ensure_ascii=False``, no sorting (spec §3). ``record_observation`` makes ONE transaction
(spec §4): ``INSERT OR IGNORE`` into ``files`` (first sight wins) then ``INSERT`` into
``file_observations`` — the OBSERVED size is ALWAYS written into the observation
(deviation 1, spec §5: a size anomaly must not become invisible).

The hash canon (32 lowercase hex, v0.5.0 canon) is validated IN PYTHON before the
transaction: ``INSERT OR IGNORE`` silently swallows a CHECK violation (documented
SQLite behavior) — without this guard, a non-canonical hash would only be stopped
by the ``foreign_keys`` pragma (opaque diagnostic), and a connection without that
pragma would commit an ORPHAN observation. The rollback catches ``BaseException``
(same discipline as ``connection._open``): a NON-sqlite failure at binding (e.g.
``UnicodeEncodeError`` on a lone surrogate) must not leave the connection
``in_transaction`` — otherwise the repository would be permanently broken.
"""

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress

from catalog_matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    MatchDecision,
)
from mulewatch.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from mulewatch.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER
from mulewatch.ports.catalog_repository import ObservedFile, ReevalRow

_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")

_INSERT_FILE = "INSERT OR IGNORE INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, NULL)"

_INSERT_OBSERVATION = """
INSERT INTO file_observations (
    ed2k_hash, filename, size_bytes, source_count, complete_source_count,
    media_length_sec, bitrate_kbps, codec, file_type, raw_meta,
    keyword, observed_at, node_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_DECISION = """
INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_VERIFICATION = """
INSERT INTO file_verifications (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)
VALUES (?, ?, ?, ?, ?, ?)
"""

# Latest verdict per (ed2k_hash, target_id) for one hash (set-diff anti-redundancy, spec §7).
# ROW_NUMBER per target, order (decided_at, id) DESCENDING (most recent = rank 1); keep rank 1.
# INCLUDES a target whose latest tier is 'retracted' (no tier filter); EXCLUDES the legacy
# target_id='' sentinel (not a real target). The idx_match_decisions_ed2k_hash index serves
# the filter.
_SELECT_LAST_DECISIONS = """
SELECT target_id, rule_name, tier FROM (
    SELECT
        target_id, rule_name, tier,
        ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY decided_at DESC, id DESC) AS rn
    FROM match_decisions
    WHERE ed2k_hash = ? AND target_id <> ''
) WHERE rn = 1
"""

# Latest verdict per (ed2k_hash, target_id), kept when tier=download (download spec §5,
# multi-target §6). Window: ROW_NUMBER per (hash, target_id), order (decided_at, id)
# DESCENDING (most recent = rank 1); keep rank 1 AND tier='download'. PARTITION BY the FULL
# key so a whole-episode file with BOTH segments in download yields BOTH candidates. Stable
# sort by (hash, target_id) for a deterministic result.
_SELECT_DOWNLOAD_DECISIONS = """
SELECT ed2k_hash, target_id FROM (
    SELECT
        ed2k_hash, target_id, tier,
        ROW_NUMBER() OVER (
            PARTITION BY ed2k_hash, target_id ORDER BY decided_at DESC, id DESC
        ) AS rn
    FROM match_decisions
) WHERE rn = 1 AND tier = 'download'
ORDER BY ed2k_hash, target_id
"""

# Last observation of a hash (name + size for the ed2k link, download spec §5).
_SELECT_LAST_OBSERVATION = """
SELECT filename, size_bytes FROM file_observations
WHERE ed2k_hash = ?
ORDER BY observed_at DESC, id DESC
LIMIT 1
"""

# Every hash's LATEST observation (re-evaluation backfill spec §6), one row per hash:
# a correlated anti-join keeps only the observation with no strictly-later observation for
# the same hash (ties broken by id, the most recent INSERT). Stable sort by hash for a
# deterministic, streamable result (no window function needed, unlike download_decisions).
_SELECT_REEVALUATION_ROWS = """
SELECT o.ed2k_hash, o.filename, o.size_bytes, o.media_length_sec, o.bitrate_kbps
FROM file_observations AS o
WHERE (
    SELECT COUNT(*) FROM file_observations AS o2
    WHERE o2.ed2k_hash = o.ed2k_hash
      AND (o2.observed_at > o.observed_at
           OR (o2.observed_at = o.observed_at AND o2.id > o.id))
) = 0
ORDER BY o.ed2k_hash
"""


class SqliteCatalogRepository:
    """SQLite implementation of the ``CatalogRepository`` port (STRUCTURAL satisfaction)."""

    def __init__(
        self, connection: sqlite3.Connection, node_id: str, *, clock: Clock = utc_now
    ) -> None:
        self._connection = connection
        self._node_id = node_id
        self._clock = clock

    def record_observation(self, observation: FileObservation) -> None:
        """ONE transaction: file (first sight wins) + stamped observation."""
        if not _CANONICAL_HASH_RE.fullmatch(observation.ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {observation.ed2k_hash!r}")
        raw_meta = json.dumps(observation.raw_meta, ensure_ascii=False)
        observed_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN")
            try:
                self._connection.execute(
                    _INSERT_FILE, (observation.ed2k_hash, observation.size_bytes)
                )
                self._connection.execute(
                    _INSERT_OBSERVATION,
                    (
                        observation.ed2k_hash,
                        observation.filename,
                        observation.size_bytes,
                        observation.source_count,
                        observation.complete_source_count,
                        observation.media_length_sec,
                        observation.bitrate_kbps,
                        observation.codec,
                        observation.file_type,
                        raw_meta,
                        observation.keyword,
                        observed_at,
                        self._node_id,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        """INSERT alone (autocommit); unknown file → FK violated → ``PersistenceError``.

        Only the 3 columns of ``MatchDecision`` are persisted (engine spec);
        ``explanation`` is runtime explainability, NEVER a column.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (
                    ed2k_hash,
                    decision.target_id,
                    decision.rule_name,
                    decision.tier,
                    utc_iso(self._clock()),
                    self._node_id,
                ),
            )

    def record_retraction(self, ed2k_hash: str, target_id: str) -> None:
        """Appends a per-target ``retracted`` decision (spec §7).

        Mirrors ``record_decision`` (same canonical-hash guard, same autocommit ``INSERT``): a
        file that no longer matches ``target_id`` gets an appended
        ``(target_id, rule_name="", tier=RETRACTED_TIER)`` row instead of a mutation, per the
        append-only invariant. Retracting one target leaves the file's other targets intact.
        Unknown file → FK violated → ``PersistenceError``.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (ed2k_hash, target_id, "", RETRACTED_TIER, utc_iso(self._clock()), self._node_id),
            )

    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]:
        """Latest verdict per target for this hash (set-diff anti-redundancy, spec §7) — READ.

        Maps ``target_id`` → its latest :class:`DecisionRecord`. INCLUDES a target whose latest
        tier is ``retracted`` (the application's set-diff skips re-retracting it); EXCLUDES the
        legacy ``target_id=""`` sentinel. The hash is NOT validated canonical (harmless read: a
        non-canonical hash matches nothing → ``{}``).
        """
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_LAST_DECISIONS, (ed2k_hash,)).fetchall()
        return {
            row[0]: DecisionRecord(target_id=row[0], rule_name=row[1], tier=row[2]) for row in rows
        }

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        """``(hash, target_id)`` whose LATEST verdict is tier=download, to replay (download §5).

        Keyed per ``(hash, target_id)``: a whole-episode file matching both segments now yields
        MULTIPLE :class:`DownloadCandidate` for the SAME hash (one per target). READ.
        """
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_DOWNLOAD_DECISIONS).fetchall()
        return tuple(DownloadCandidate(ed2k_hash=row[0], target_id=row[1]) for row in rows)

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        """Last observation of a hash (name+size for the ed2k link), or ``None`` — READ."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_LAST_OBSERVATION, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return ObservedFile(filename=row[0], size_bytes=row[1])

    def iter_reevaluation_rows(self) -> Iterator[ReevalRow]:
        """Every hash's latest observation, streamed via the cursor (backfill spec §6) — READ."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_SELECT_REEVALUATION_ROWS)
            for row in cursor:
                yield ReevalRow(
                    ed2k_hash=row[0],
                    filename=row[1],
                    size_bytes=row[2],
                    media_length_sec=row[3],
                    bitrate_kbps=row[4],
                )

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: Sequence[object],
    ) -> None:
        """INSERT alone (autocommit) of a verdict (verify spec §5). Append-only (trigger).

        Templated on ``record_decision``: canonical hash guard BEFORE the INSERT (a
        non-canonical hash is a caller bug → clear ``PersistenceError``, not an opaque FK
        diagnostic); ``real_meta``/``checks`` serialized as JSON (``ensure_ascii=False``, the
        NO-OP verdict leaves them empty but D-analysis will fill them); ``verified_at``/``node_id``
        stamped by the adapter (the domain ignores persistence columns). Unknown file → FK
        violated → ``PersistenceError`` via ``wrap_sqlite_errors``.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_VERIFICATION,
                (
                    ed2k_hash,
                    verdict,
                    json.dumps(real_meta, ensure_ascii=False),
                    json.dumps(list(checks), ensure_ascii=False),
                    utc_iso(self._clock()),
                    self._node_id,
                ),
            )
