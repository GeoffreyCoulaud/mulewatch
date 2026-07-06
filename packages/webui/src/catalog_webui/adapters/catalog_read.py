"""Read-only reads of the catalog (webui spec W-D6 / §6).

``CatalogReader`` exposes four reads:

- ``target_coverage()`` — per ``target_id``, the list of ``(ed2k_hash, tier)`` from each
  file's LATEST match decision **per target** (ROW_NUMBER window PARTITION BY
  ``(ed2k_hash, target_id)``), so a whole-episode file contributes to every target it
  matches. The legacy ``target_id=''`` sentinel and per-target ``retracted`` rows are
  excluded.
- ``list_files()`` — filtered paginated explorer (files ⨝ latest observation ⨝
  latest decision ⨝ latest verdict, optional filters + LIMIT/OFFSET).
- ``count_files()`` — ``(matched, total)`` counts over the same filtered source, for the
  /files summary line.
- ``file_detail()`` — all observations + latest decision + all verdicts for a given
  hash; ``None`` if the hash is unknown.

All SQL lives in module constants, parameterized (no value interpolation).
"""

import sqlite3

from catalog_webui.domain.views import (
    DecisionView,
    FileDecision,
    FileDetail,
    FileRow,
    ObservationRow,
    VerificationRow,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_SIZE = 50
_PAGE_SIZE = PAGE_SIZE  # historical alias (internal) — the public value is used by the handler

# Latest decision per (hash, target_id) via ROW_NUMBER window: a whole-episode file holds
# one CURRENT decision per target it satisfies, so it must contribute to each of them, not
# just the single most-recent row across all its targets. The legacy target_id='' sentinel
# (pre-per-target retraction model) is excluded, and a per-target "retracted" latest decision
# (retracted == unmatched for that target) is dropped too.
_SQL_COVERAGE = """\
SELECT
    md.ed2k_hash,
    md.target_id,
    md.tier
FROM match_decisions AS md
WHERE (
    SELECT COUNT(*)
    FROM match_decisions AS md2
    WHERE
        md2.ed2k_hash = md.ed2k_hash
        AND md2.target_id = md.target_id
        AND (
            md2.decided_at > md.decided_at
            OR (md2.decided_at = md.decided_at AND md2.id > md.id)
        )
) = 0
AND md.target_id != ''
AND md.tier != 'retracted'
ORDER BY md.target_id, md.ed2k_hash
"""

# Current decisions per file: latest per (ed2k_hash, target_id), excluding the legacy
# ``target_id == ''`` sentinel and any target whose latest row is a ``retracted`` marker.
# ``dec_agg`` folds them to ONE row per hash — target_ids/tiers are ``char(31)``-joined,
# both ordered by target_id so the two lists stay index-aligned (spec §9, rendering A).
_SQL_LATEST_DEC_AGG = """\
WITH latest_dec AS (
    SELECT
        md.ed2k_hash,
        md.target_id,
        md.tier
    FROM match_decisions AS md
    WHERE (
        SELECT COUNT(*)
        FROM match_decisions AS md2
        WHERE
            md2.ed2k_hash = md.ed2k_hash
            AND md2.target_id = md.target_id
            AND (
                md2.decided_at > md.decided_at
                OR (md2.decided_at = md.decided_at AND md2.id > md.id)
            )
    ) = 0
    AND md.target_id != ''
    AND md.tier != 'retracted'
),
dec_agg AS (
    SELECT
        ld.ed2k_hash,
        group_concat(ld.target_id, char(31) ORDER BY ld.target_id) AS target_ids,
        group_concat(ld.tier, char(31) ORDER BY ld.target_id) AS tiers
    FROM latest_dec AS ld
    GROUP BY ld.ed2k_hash
)
"""

# Shared source: files ⨝ latest observation ⨝ current decisions (aggregated) ⨝ latest verdict.
_SQL_FILES_SOURCE = """\
FROM files AS f
LEFT JOIN file_observations AS obs
    ON obs.ed2k_hash = f.ed2k_hash
    AND (
        SELECT COUNT(*)
        FROM file_observations AS obs2
        WHERE
            obs2.ed2k_hash = obs.ed2k_hash
            AND (
                obs2.observed_at > obs.observed_at
                OR (obs2.observed_at = obs.observed_at AND obs2.id > obs.id)
            )
    ) = 0
LEFT JOIN dec_agg AS dec
    ON dec.ed2k_hash = f.ed2k_hash
LEFT JOIN file_verifications AS ver
    ON ver.ed2k_hash = f.ed2k_hash
    AND (
        SELECT COUNT(*)
        FROM file_verifications AS ver2
        WHERE
            ver2.ed2k_hash = ver.ed2k_hash
            AND (
                ver2.verified_at > ver.verified_at
                OR (ver2.verified_at = ver.verified_at AND ver2.id > ver.id)
            )
    ) = 0
"""

# Explorer: files + latest joins, driven by files. Optional filters added in list_files().
_SQL_LIST_FILES_BASE = (
    _SQL_LATEST_DEC_AGG
    + """\
SELECT
    f.ed2k_hash,
    f.size_bytes,
    obs.filename,
    obs.source_count,
    obs.observed_at AS last_seen,
    dec.target_ids,
    dec.tiers,
    ver.verdict AS last_verdict
"""
    + _SQL_FILES_SOURCE
)

# Counter for the /files summary: file-based totals over the same source + filters (the
# matched-only clause is deliberately absent). ``matched`` = files with at least one current
# decision (``dec.target_ids`` is non-NULL). COUNT(DISTINCT …) keeps both counts file-based
# and yields 0 (not NULL) on an empty catalogue.
_SQL_COUNT_FILES_BASE = (
    _SQL_LATEST_DEC_AGG
    + """\
SELECT
    COUNT(DISTINCT f.ed2k_hash) AS total,
    COUNT(DISTINCT CASE WHEN dec.target_ids IS NOT NULL THEN f.ed2k_hash END) AS matched
"""
    + _SQL_FILES_SOURCE
)

# All observations of a file (timeline), chronological order.
_SQL_OBSERVATIONS = """\
SELECT
    id,
    filename,
    size_bytes,
    source_count,
    complete_source_count,
    media_length_sec,
    bitrate_kbps,
    keyword,
    observed_at,
    node_id
FROM file_observations
WHERE ed2k_hash = ?
ORDER BY observed_at ASC, id ASC
"""

# Latest decision of a file.
_SQL_LAST_DECISION = """\
SELECT
    target_id,
    rule_name,
    tier,
    decided_at,
    node_id
FROM match_decisions
WHERE ed2k_hash = ?
ORDER BY decided_at DESC, id DESC
LIMIT 1
"""

# All verdicts of a file, chronological order.
_SQL_VERIFICATIONS = """\
SELECT
    id,
    verdict,
    verified_at,
    node_id
FROM file_verifications
WHERE ed2k_hash = ?
ORDER BY verified_at ASC, id ASC
"""

# Basic lookup on files (for file_detail).
_SQL_FILE = """\
SELECT ed2k_hash, size_bytes, aich_hash
FROM files
WHERE ed2k_hash = ?
"""


def _filter_clauses(
    target: str | None,
    tier: str | None,
    verdict: str | None,
    query: str | None,
) -> tuple[list[str], list[str]]:
    """Shared WHERE clauses + params for the explorer list and its counter.

    ``target``/``tier`` match a file if ANY of its current decisions matches (EXISTS over the
    ``latest_dec`` CTE), so a whole-episode file appears under each of its targets. The
    matched-only clause and LIMIT/OFFSET are list-specific and are NOT built here.
    """
    clauses: list[str] = []
    params: list[str] = []
    if target is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.target_id = ?)"
        )
        params.append(target)
    if tier is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.tier = ?)"
        )
        params.append(tier)
    if verdict is not None:
        clauses.append("ver.verdict = ?")
        params.append(verdict)
    if query is not None:
        clauses.append("obs.filename LIKE ?")
        params.append(f"%{query}%")
    return clauses, params


def _split_concat(concat: str | None) -> list[str]:
    """Split a ``char(31)``-joined aggregate (``group_concat``) into parts. ``None`` (a file
    with no current decision → the LEFT JOIN yields NULL) → an empty list."""
    return concat.split("\x1f") if concat is not None else []


# ---------------------------------------------------------------------------
# CatalogReader
# ---------------------------------------------------------------------------


class CatalogReader:
    """Read-only access to the catalog via a SQLite connection (open_ro)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------

    def target_coverage(self) -> dict[str, list[tuple[str, str]]]:
        """Return, for each ``target_id``, the list of ``(ed2k_hash, tier)``
        from each file's LATEST match decision **per target** (a whole-episode
        file appears under every target it currently matches).
        """
        rows = self._conn.execute(_SQL_COVERAGE).fetchall()
        result: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            target_id: str = row["target_id"]
            entry = (row["ed2k_hash"], row["tier"])
            if target_id not in result:
                result[target_id] = []
            result[target_id].append(entry)
        return result

    # ------------------------------------------------------------------
    # Filtered paginated explorer
    # ------------------------------------------------------------------

    def list_files(
        self,
        *,
        target: str | None,
        tier: str | None,
        verdict: str | None,
        query: str | None,
        page: int,
        matched_only: bool = False,
    ) -> list[FileRow]:
        """Return a page of ``FileRow`` (size ``_PAGE_SIZE``) with optional filters.

        Filters:
        - ``target`` : keep a file if ANY of its current decisions matches this target_id.
        - ``tier``   : keep a file if ANY of its current decisions has this tier.
        - ``verdict``: filter on ``ver.verdict`` (latest verdict).
        - ``query``  : substring of ``obs.filename`` (LIKE ``%query%``).
        - ``matched_only``: when true, keep only files with at least one current decision
          (retractions and the legacy ``target_id == ''`` sentinel never produce one).
          Default false = whole catalogue.
        - ``page``   : page number (1-based).
        """
        clauses, str_params = _filter_clauses(target, tier, verdict, query)
        if matched_only:
            # A file is matched iff it has at least one current (non-retracted) decision;
            # ``dec.target_ids`` is NULL for a file with none.
            clauses.append("dec.target_ids IS NOT NULL")
        params: list[str | int] = [*str_params]

        sql = _SQL_LIST_FILES_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        sql += "ORDER BY obs.observed_at DESC, f.ed2k_hash\n"
        sql += "LIMIT ? OFFSET ?\n"
        params.append(_PAGE_SIZE)
        params.append((page - 1) * _PAGE_SIZE)

        rows = self._conn.execute(sql, params).fetchall()
        result: list[FileRow] = []
        for row in rows:
            target_ids = _split_concat(row["target_ids"])
            tiers = _split_concat(row["tiers"])
            decisions = tuple(
                FileDecision(target_id=t, tier=ti) for t, ti in zip(target_ids, tiers, strict=True)
            )
            result.append(
                FileRow(
                    ed2k_hash=row["ed2k_hash"],
                    size_bytes=row["size_bytes"],
                    filename=row["filename"] or "",
                    source_count=row["source_count"],
                    last_seen=row["last_seen"] or "",
                    decisions=decisions,
                    last_verdict=row["last_verdict"],
                )
            )
        return result

    def count_files(
        self,
        *,
        target: str | None,
        tier: str | None,
        verdict: str | None,
        query: str | None,
    ) -> tuple[int, int]:
        """Return ``(matched, total)`` file counts in the current filter scope.

        ``total`` = files matching the ``target/tier/verdict/query`` filters (the
        matched-only clause is deliberately NOT applied); ``matched`` = of those, how many
        have a match decision. Feeds the /files summary line.
        """
        clauses, params = _filter_clauses(target, tier, verdict, query)
        sql = _SQL_COUNT_FILES_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        row = self._conn.execute(sql, params).fetchone()
        matched: int = row["matched"]
        total: int = row["total"]
        return (matched, total)

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    def file_detail(self, ed2k_hash: str) -> FileDetail | None:
        """Return the full detail of a file, or ``None`` if unknown."""
        file_row = self._conn.execute(_SQL_FILE, (ed2k_hash,)).fetchone()
        if file_row is None:
            return None

        obs_rows = self._conn.execute(_SQL_OBSERVATIONS, (ed2k_hash,)).fetchall()
        dec_row = self._conn.execute(_SQL_LAST_DECISION, (ed2k_hash,)).fetchone()
        ver_rows = self._conn.execute(_SQL_VERIFICATIONS, (ed2k_hash,)).fetchall()

        # A retracted latest decision is treated as no decision at all (retracted == unmatched,
        # spec §9): the earlier, pre-retraction real decision must never leak through here.
        decision: DecisionView | None = None
        if dec_row is not None and dec_row["tier"] != "retracted":
            decision = DecisionView(
                target_id=dec_row["target_id"],
                rule_name=dec_row["rule_name"],
                tier=dec_row["tier"],
                decided_at=dec_row["decided_at"],
                node_id=dec_row["node_id"],
            )

        return FileDetail(
            ed2k_hash=file_row["ed2k_hash"],
            size_bytes=file_row["size_bytes"],
            aich_hash=file_row["aich_hash"],
            observations=tuple(
                ObservationRow(
                    id=row["id"],
                    filename=row["filename"],
                    size_bytes=row["size_bytes"],
                    source_count=row["source_count"],
                    complete_source_count=row["complete_source_count"],
                    media_length_sec=row["media_length_sec"],
                    bitrate_kbps=row["bitrate_kbps"],
                    keyword=row["keyword"],
                    observed_at=row["observed_at"],
                    node_id=row["node_id"],
                )
                for row in obs_rows
            ),
            decision=decision,
            verifications=tuple(
                VerificationRow(
                    id=row["id"],
                    verdict=row["verdict"],
                    verified_at=row["verified_at"],
                    node_id=row["node_id"],
                )
                for row in ver_rows
            ),
        )
