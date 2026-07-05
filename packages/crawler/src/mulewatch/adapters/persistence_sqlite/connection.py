"""SQLite connection + migration runner (data-model spec §3/§4/§7).

Each connection is opened in REAL autocommit (``autocommit=True``, Python ≥ 3.12):
transactions are EXPLICIT (``BEGIN``/``COMMIT``/``ROLLBACK`` written by the
repositories), no implicit isolation. Opening PRAGMAs (spec §3):
``journal_mode=WAL`` — REQUIRED: ``:memory:`` does not carry it (it answers ``memory``)
and is therefore refused outright; the tests use real files (spec §8) —
``foreign_keys=ON``, and ``recursive_triggers=ON`` (without which ``INSERT OR REPLACE``
crosses the append-only triggers, spec §3 post-review amendment).

The runner reads the ``NNNN_*.sql`` scripts embedded in the package (``importlib.
resources``), applies them in ascending order EACH in ITS OWN transaction (failure →
best-effort ROLLBACK, version unchanged — same spirit as the EC transport's best-effort
``close()``), and tracks state in ``PRAGMA user_version``. A database NEWER than the
code → outright refusal (``MigrationError``, fail-fast MVP spec §14). The scripts
contain NO ``BEGIN``/``COMMIT``: it is the runner that wraps.

This module also carries the repositories' shared clock (``Clock``/``utc_now``/
``utc_iso``): ISO-8601 UTC as TEXT (spec §3), FIXED microseconds so that lexicographic
order IS chronological order (the FIFO claim sorts on ``enqueued_at``).
"""

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from itertools import pairwise
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.errors import (
    MigrationError,
    PersistenceError,
    wrap_sqlite_errors,
)

type Clock = Callable[[], datetime]

_MIGRATIONS = resources.files("mulewatch.adapters.persistence_sqlite") / "migrations"


def utc_now() -> datetime:
    """Default clock for the repositories (spec §3: injectable, ``datetime.now(UTC)``)."""
    return datetime.now(UTC)


def utc_iso(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC (microseconds ALWAYS written), e.g.
    ``2026-06-11T12:00:00.000000+00:00``. ``moment`` must be AWARE (``Clock``
    contract, ENFORCED: naive → ``ValueError``); a non-UTC zone is normalized,
    never stored as-is."""
    if moment.tzinfo is None:
        raise ValueError("utc_iso exige un datetime aware (contrat de Clock)")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def open_catalog(path: Path | str) -> sqlite3.Connection:
    """Opens/migrates ``catalog.db`` (the append-only triggers are part of the schema)."""
    return _open(path, _MIGRATIONS / "catalog")


def open_local(path: Path | str) -> sqlite3.Connection:
    """Opens/migrates ``local.db``."""
    return _open(path, _MIGRATIONS / "local")


def _open(path: Path | str, scripts_dir: Traversable) -> sqlite3.Connection:
    with wrap_sqlite_errors():
        connection = sqlite3.connect(path, autocommit=True)
    try:
        with wrap_sqlite_errors():
            _configure(connection)
            _apply_migrations(connection, _load_scripts(scripts_dir))
    except BaseException:
        # Unconditional close: a NON-sqlite error (e.g. OSError from iterdir) must not
        # leak the connection; it then propagates as-is.
        connection.close()
        raise
    return connection


def _configure(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if journal_mode != "wal":
        raise PersistenceError(
            f"journal_mode={journal_mode!r}: WAL required (spec §3) — file-backed db only"
        )
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=ON")


def _load_scripts(directory: Traversable) -> tuple[tuple[int, str], ...]:
    """Migration discovery: ``NNNN_*.sql`` sorted by name (lexicographic order).

    A non-``.sql`` file is ignored; a ``.sql`` without a numeric prefix is a packaging
    BUG → ``MigrationError`` (fail-fast, no migration silently skipped). The versions must
    be STRICTLY increasing in the lexicographic order of the names: a duplicate
    (``0001_a`` + ``0001_b``) or a non-zero-padded prefix that inverts the order
    (``10_b`` sorted before ``2_a``) → ``MigrationError`` (otherwise a migration is skipped
    or replayed silently). Gaps (0001 then 0003) stay allowed.
    """
    scripts: list[tuple[int, str]] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        prefix = entry.name.partition("_")[0]
        if not prefix.isdigit():
            raise MigrationError(f"invalid script name (NNNN_*.sql expected): {entry.name}")
        scripts.append((int(prefix), entry.read_text(encoding="utf-8")))
    for (left, _), (right, _) in pairwise(scripts):
        if right <= left:
            raise MigrationError(
                f"migration versions not strictly increasing: {left} then {right} "
                "(unique zero-padded NNNN prefixes required)"
            )
    return tuple(scripts)


def _apply_migrations(connection: sqlite3.Connection, scripts: tuple[tuple[int, str], ...]) -> None:
    """Applies the scripts with version > ``user_version``, each in ITS OWN transaction.

    Wrapper LAID BY THE RUNNER, piece by piece: explicit ``BEGIN``, then
    ``executescript(script)`` (verified empirically under ``autocommit=True``, SQLite
    3.47.1: it does NOT commit the current transaction), then GUARDS ``in_transaction``
    — a script that contains a stray ``COMMIT`` closes the wrapper and would otherwise be
    stamped/committed partially → ``MigrationError`` BEFORE the stamp — then ``PRAGMA
    user_version = N`` INSIDE the transaction (the pragma is transactional: a ROLLBACK
    undoes it), then ``COMMIT``. PRAGMA accepts no bound parameter: ``version``
    comes from ``int()``, the interpolation is safe.
    """
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    latest = scripts[-1][0] if scripts else 0
    if current > latest:
        raise MigrationError(
            f"db at version {current}, code at version {latest}: "
            "db newer than the code, refusing to start (spec §3)"
        )
    # Race between two concurrent runners: the loser fails cleanly (sqlite3.Error
    # → MigrationError), never corruption — single writer by doctrine (spec §3).
    for version, script in scripts:
        if version <= current:
            continue
        try:
            connection.execute("BEGIN")
            connection.executescript(script)
            if not connection.in_transaction:
                raise MigrationError(
                    f"migration {version}: the script closed the runner's transaction "
                    "(COMMIT/ROLLBACK forbidden inside a migration script)"
                )
            connection.execute(f"PRAGMA user_version = {version}")
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise MigrationError(f"migration {version} failed: {error}") from error
