"""Connexion SQLite + runner de migrations (spec data-model §3/§4/§7).

Chaque connexion est ouverte en autocommit RÉEL (``autocommit=True``, Python ≥ 3.12) :
les transactions sont EXPLICITES (``BEGIN``/``COMMIT``/``ROLLBACK`` écrits par les
repositories), aucune isolation implicite. PRAGMA d'ouverture (spec §3) :
``journal_mode=WAL`` — EXIGÉ : ``:memory:`` ne le porte pas (il répond ``memory``)
et est donc refusé net ; les tests utilisent des fichiers réels (spec §8) —
``foreign_keys=ON``, et ``recursive_triggers=ON`` (sans quoi ``INSERT OR REPLACE``
traverse les triggers append-only, spec §3 amendement post-review).

Le runner lit les scripts ``NNNN_*.sql`` embarqués dans le paquet (``importlib.
resources``), les applique en ordre croissant CHACUN dans SA transaction (échec →
ROLLBACK best-effort, version inchangée — même esprit que le ``close()`` best-effort
du transport EC), et trace l'état dans ``PRAGMA user_version``. Une base PLUS RÉCENTE
que le code → refus net (``MigrationError``, fail-fast spec MVP §14). Les scripts ne
contiennent AUCUN ``BEGIN``/``COMMIT`` : c'est le runner qui enveloppe.

Ce module porte aussi l'horloge partagée des repositories (``Clock``/``utc_now``/
``utc_iso``) : ISO-8601 UTC en TEXT (spec §3), microsecondes FIXES pour que l'ordre
lexicographique SOIT l'ordre chronologique (le claim FIFO trie sur ``enqueued_at``).
"""

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from itertools import pairwise
from pathlib import Path

from emule_indexer.adapters.persistence_sqlite.errors import (
    MigrationError,
    PersistenceError,
    wrap_sqlite_errors,
)

type Clock = Callable[[], datetime]

_MIGRATIONS = resources.files("emule_indexer.adapters.persistence_sqlite") / "migrations"


def utc_now() -> datetime:
    """Horloge par défaut des repositories (spec §3 : injectable, ``datetime.now(UTC)``)."""
    return datetime.now(UTC)


def utc_iso(moment: datetime) -> str:
    """ISO-8601 UTC à largeur fixe (microsecondes TOUJOURS écrites), p.ex.
    ``2026-06-11T12:00:00.000000+00:00``. ``moment`` doit être AWARE (contrat de
    ``Clock``, IMPOSÉ : naïf → ``ValueError``) ; un fuseau non-UTC est normalisé,
    jamais stocké tel quel."""
    if moment.tzinfo is None:
        raise ValueError("utc_iso exige un datetime aware (contrat de Clock)")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def open_catalog(path: Path | str) -> sqlite3.Connection:
    """Ouvre/migre ``catalog.db`` (les triggers append-only font partie du schéma)."""
    return _open(path, _MIGRATIONS / "catalog")


def open_local(path: Path | str) -> sqlite3.Connection:
    """Ouvre/migre ``local.db``."""
    return _open(path, _MIGRATIONS / "local")


def _open(path: Path | str, scripts_dir: Traversable) -> sqlite3.Connection:
    with wrap_sqlite_errors():
        connection = sqlite3.connect(path, autocommit=True)
    try:
        with wrap_sqlite_errors():
            _configure(connection)
            _apply_migrations(connection, _load_scripts(scripts_dir))
    except BaseException:
        # Close inconditionnel : une erreur NON-sqlite (p.ex. OSError d'iterdir) ne doit
        # pas faire fuir la connexion ; elle se propage ensuite telle quelle.
        connection.close()
        raise
    return connection


def _configure(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if journal_mode != "wal":
        raise PersistenceError(
            f"journal_mode={journal_mode!r} : WAL exigé (spec §3) — base fichier uniquement"
        )
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=ON")


def _load_scripts(directory: Traversable) -> tuple[tuple[int, str], ...]:
    """Découverte des migrations : ``NNNN_*.sql`` triés par nom (ordre lexicographique).

    Un fichier non-``.sql`` est ignoré ; un ``.sql`` sans préfixe numérique est un BUG
    d'empaquetage → ``MigrationError`` (fail-fast, pas de migration silencieusement sautée).
    Les versions doivent être STRICTEMENT croissantes dans l'ordre lexicographique des
    noms : un doublon (``0001_a`` + ``0001_b``) ou un préfixe non zéro-paddé qui inverse
    l'ordre (``10_b`` trié avant ``2_a``) → ``MigrationError`` (sinon migration sautée
    ou rejouée en silence). Les trous (0001 puis 0003) restent permis.
    """
    scripts: list[tuple[int, str]] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        prefix = entry.name.partition("_")[0]
        if not prefix.isdigit():
            raise MigrationError(f"nom de script invalide (attendu NNNN_*.sql) : {entry.name}")
        scripts.append((int(prefix), entry.read_text(encoding="utf-8")))
    for (left, _), (right, _) in pairwise(scripts):
        if right <= left:
            raise MigrationError(
                f"versions de migration non strictement croissantes : {left} puis {right} "
                "(préfixes NNNN uniques et zéro-paddés exigés)"
            )
    return tuple(scripts)


def _apply_migrations(connection: sqlite3.Connection, scripts: tuple[tuple[int, str], ...]) -> None:
    """Applique les scripts de version > ``user_version``, chacun dans SA transaction.

    Enveloppe POSÉE PAR LE RUNNER, pièce par pièce : ``BEGIN`` explicite, puis
    ``executescript(script)`` (vérifié empiriquement sous ``autocommit=True``, SQLite
    3.47.1 : il ne commet PAS la transaction en cours), puis GARDE ``in_transaction``
    — un script qui contient un ``COMMIT`` parasite clot l'enveloppe et serait sinon
    stampé/commité partiellement → ``MigrationError`` AVANT le stamp — puis ``PRAGMA
    user_version = N`` DANS la transaction (le pragma est transactionnel : un ROLLBACK
    le rend), puis ``COMMIT``. PRAGMA n'accepte pas de paramètre lié : ``version``
    vient d'``int()``, l'interpolation est sûre.
    """
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    latest = scripts[-1][0] if scripts else 0
    if current > latest:
        raise MigrationError(
            f"base en version {current}, code en version {latest} : "
            "base plus récente que le code, refus de démarrer (spec §3)"
        )
    # Course entre deux runners concurrents : le perdant échoue proprement (sqlite3.Error
    # → MigrationError), jamais de corruption — writer unique par doctrine (spec §3).
    for version, script in scripts:
        if version <= current:
            continue
        try:
            connection.execute("BEGIN")
            connection.executescript(script)
            if not connection.in_transaction:
                raise MigrationError(
                    f"migration {version} : le script a clos la transaction du runner "
                    "(COMMIT/ROLLBACK interdits dans un script de migration)"
                )
            connection.execute(f"PRAGMA user_version = {version}")
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise MigrationError(f"migration {version} échouée : {error}") from error
