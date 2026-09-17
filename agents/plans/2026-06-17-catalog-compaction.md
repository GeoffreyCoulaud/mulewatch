# Compaction du catalogue par rollup journalier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un outil opérateur standalone `python -m emule_indexer.compact` qui réduit la seule table
non bornée (`file_observations`) en un rollup journalier node-agnostique (`file_observation_ranges`),
sans toucher au code prod et en préservant l'append-only et l'idempotence du merge.

**Architecture:** Spec `docs/superpowers/specs/2026-06-17-catalog-compaction-design.md`. Migration
additive `catalog/0002` (table append-only). Cœur PUR `domain/retention/buckets.py` (bucketing +
agrégats). Orchestration SQLite `compact/compactor.py` (reconstruction vers une sortie NEUVE, calquée
sur `merge/merger.py`). CLI `compact/__main__.py` (safe-by-default, calquée sur `merge/__main__.py`).
Extension du merge (7ᵉ journal). Le crawler n'importe rien de `compact/` et ne lit/écrit jamais la
nouvelle table → gate 100 % branch préservé.

**Tech Stack:** Python ≥3.12, sqlite3 (autocommit + transactions explicites), pytest (100 % branch,
fichiers réels jamais `:memory:`), mypy --strict, ruff, sqlfluff (migrations).

**Gate (vert obligatoire avant chaque commit) :**
```bash
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
```
Pour un test ciblé : ajouter `--no-cov` (le seuil 100 % est global au paquet).

---

### Task 1 : Migration `catalog/0002` — table `file_observation_ranges`

**Files:**
- Create: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/migrations/catalog/0002_observation_ranges.sql`
- Test: `packages/crawler/tests/adapters/persistence_sqlite/test_catalog_observation_ranges.py`

- [ ] **Step 1 : Test d'abord** (le schéma comme spec : table présente, append-only, CHECK)

```python
"""catalog/0002 : table de rollup file_observation_ranges (append-only + CHECK).

Append-only IMPOSÉ PAR LA BASE (comme les tables de 0001) : UPDATE/DELETE → RAISE(ABORT) →
sqlite3.IntegrityError. Les CHECK (observation_count > 0, first <= last, LENGTH(bucket) = 10)
remontent aussi en sqlite3.IntegrityError. Fichier réel exigé (WAL ; open_catalog refuse :memory:).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog

_HASH = "a" * 32
_RANGE = (
    "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
    " observation_count, first_observed_at, last_observed_at, source_count_min,"
    " source_count_max, source_count_sum, complete_source_count_min,"
    " complete_source_count_max, complete_source_count_sum) VALUES"
    f" ('{_HASH}', '2026-03-01', '[\"f.avi\"]', '[\"n\"]', 3,"
    " '2026-03-01T00:00:00.000000+00:00', '2026-03-01T23:00:00.000000+00:00',"
    " 1, 9, 15, 0, 2, 3)"
)


@pytest.fixture
def seeded(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    connection.execute(f"INSERT INTO files (ed2k_hash, size_bytes) VALUES ('{_HASH}', 1)")
    connection.execute(_RANGE)
    yield connection
    connection.close()


def test_insert_is_allowed(seeded: sqlite3.Connection) -> None:
    assert seeded.execute("SELECT count(*) FROM file_observation_ranges").fetchone()[0] == 1


def test_update_is_rejected(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="file_observation_ranges est append-only"):
        seeded.execute("UPDATE file_observation_ranges SET observation_count = 4")


def test_delete_is_rejected(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="file_observation_ranges est append-only"):
        seeded.execute("DELETE FROM file_observation_ranges")


def test_check_observation_count_positive(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
            " observation_count, first_observed_at, last_observed_at, source_count_min,"
            " source_count_max, source_count_sum, complete_source_count_min,"
            " complete_source_count_max, complete_source_count_sum) VALUES"
            f" ('{_HASH}', '2026-03-02', '[]', '[]', 0, '2026-03-02', '2026-03-02', 0, 0, 0, 0, 0, 0)"
        )


def test_check_bucket_length(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
            " observation_count, first_observed_at, last_observed_at, source_count_min,"
            " source_count_max, source_count_sum, complete_source_count_min,"
            " complete_source_count_max, complete_source_count_sum) VALUES"
            f" ('{_HASH}', '2026-03', '[]', '[]', 1, '2026-03', '2026-03', 0, 0, 0, 0, 0, 0)"
        )


def test_check_first_before_last(seeded: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
            " observation_count, first_observed_at, last_observed_at, source_count_min,"
            " source_count_max, source_count_sum, complete_source_count_min,"
            " complete_source_count_max, complete_source_count_sum) VALUES"
            f" ('{_HASH}', '2026-03-03', '[]', '[]', 1, '2026-03-03T05:00', '2026-03-03T01:00',"
            " 0, 0, 0, 0, 0, 0)"
        )
```

- [ ] **Step 2 : Voir échouer** — `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_observation_ranges.py --no-cov -q )` → erreurs « no such table ».

- [ ] **Step 3 : La migration** (mirror exact du style de `0001_initial.sql` ; sqlfluff doit passer)

```sql
-- catalog.db — migration 0002 : rollup journalier des observations (compaction).
-- Écrite/lue UNIQUEMENT par l'outil de compaction + le merge ; le crawler l'ignore.
-- Une ligne = UN bucket (ed2k_hash, jour UTC), node-agnostique : agrégat de TOUTES les
-- observations de ce fichier ce jour-là, tous nœuds confondus. source_count et
-- complete_source_count sont NOT NULL dans file_observations → agrégats toujours définis.
-- filenames / node_ids : tableaux JSON CANONIQUES (distincts, triés). moyenne = sum / count
-- (non stockée — exacte, associativement combinable). Migration ADDITIVE : ne reconstruit
-- aucune table de 0001, ne touche donc pas à ses triggers.

CREATE TABLE file_observation_ranges (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    bucket TEXT NOT NULL,
    filenames TEXT NOT NULL,
    node_ids TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    source_count_min INTEGER NOT NULL,
    source_count_max INTEGER NOT NULL,
    source_count_sum INTEGER NOT NULL,
    complete_source_count_min INTEGER NOT NULL,
    complete_source_count_max INTEGER NOT NULL,
    complete_source_count_sum INTEGER NOT NULL,
    CHECK (observation_count > 0),
    CHECK (first_observed_at <= last_observed_at),
    CHECK (LENGTH(bucket) = 10)
);

CREATE INDEX idx_file_observation_ranges_ed2k_hash
ON file_observation_ranges (ed2k_hash);

CREATE TRIGGER file_observation_ranges_no_update
BEFORE UPDATE ON file_observation_ranges
BEGIN
    SELECT RAISE(ABORT, 'file_observation_ranges est append-only');
END;

CREATE TRIGGER file_observation_ranges_no_delete
BEFORE DELETE ON file_observation_ranges
BEGIN
    SELECT RAISE(ABORT, 'file_observation_ranges est append-only');
END;
```

- [ ] **Step 4 : Voir passer** — la même commande qu'au Step 2 → tous verts. Vérifier
  `uv run sqlfluff lint packages/crawler/src` vert. (NB : `test_connection.py` peut vérifier le
  nombre de migrations / `user_version` — si un test compte les scripts catalog, l'ajuster à 2.)

- [ ] **Step 5 : Commit** — `feat(persistence): migration catalog/0002 — table file_observation_ranges (append-only)`

---

### Task 2 : Cœur pur `domain/retention/buckets.py`

**Files:**
- Create: `packages/crawler/src/emule_indexer/domain/retention/__init__.py` (vide)
- Create: `packages/crawler/src/emule_indexer/domain/retention/buckets.py`
- Test: `packages/crawler/tests/domain/retention/__init__.py` (vide) + `.../test_buckets.py`

- [ ] **Step 1 : Tests d'abord**

```python
"""bucketize (pur) : groupe des observations par (ed2k_hash, jour UTC) en agrégats node-agnostiques."""

from emule_indexer.domain.retention.buckets import ObservationBucket, ObservationRow, bucketize


def _row(
    *, h: str = "a" * 32, node: str = "n1", name: str = "f.avi", sc: int = 5, csc: int = 1, at: str
) -> ObservationRow:
    return ObservationRow(
        ed2k_hash=h, node_id=node, filename=name, source_count=sc, complete_source_count=csc,
        observed_at=at,
    )


def test_empty_input_gives_no_bucket() -> None:
    assert bucketize([]) == []


def test_one_day_one_hash_aggregates() -> None:
    rows = [
        _row(sc=1, csc=0, at="2026-03-01T01:00:00.000000+00:00"),
        _row(sc=9, csc=2, at="2026-03-01T20:00:00.000000+00:00"),
    ]
    (bucket,) = bucketize(rows)
    assert bucket == ObservationBucket(
        ed2k_hash="a" * 32,
        bucket="2026-03-01",
        filenames='["f.avi"]',
        node_ids='["n1"]',
        observation_count=2,
        first_observed_at="2026-03-01T01:00:00.000000+00:00",
        last_observed_at="2026-03-01T20:00:00.000000+00:00",
        source_count_min=1,
        source_count_max=9,
        source_count_sum=10,
        complete_source_count_min=0,
        complete_source_count_max=2,
        complete_source_count_sum=2,
    )


def test_two_days_give_two_buckets() -> None:
    rows = [
        _row(at="2026-03-01T01:00:00.000000+00:00"),
        _row(at="2026-03-02T01:00:00.000000+00:00"),
    ]
    buckets = bucketize(rows)
    assert [b.bucket for b in buckets] == ["2026-03-01", "2026-03-02"]


def test_filenames_are_sorted_distinct_json() -> None:
    rows = [
        _row(name="b.avi", at="2026-03-01T01:00:00.000000+00:00"),
        _row(name="a.avi", at="2026-03-01T02:00:00.000000+00:00"),
        _row(name="b.avi", at="2026-03-01T03:00:00.000000+00:00"),
    ]
    (bucket,) = bucketize(rows)
    assert bucket.filenames == '["a.avi", "b.avi"]'


def test_node_agnostic_two_nodes_one_bucket() -> None:
    rows = [
        _row(node="n2", at="2026-03-01T01:00:00.000000+00:00"),
        _row(node="n1", at="2026-03-01T02:00:00.000000+00:00"),
    ]
    (bucket,) = bucketize(rows)
    assert bucket.node_ids == '["n1", "n2"]'
    assert bucket.observation_count == 2


def test_single_observation_min_eq_max_eq_sum() -> None:
    (bucket,) = bucketize([_row(sc=7, csc=3, at="2026-03-01T01:00:00.000000+00:00")])
    assert (bucket.source_count_min, bucket.source_count_max, bucket.source_count_sum) == (7, 7, 7)
    assert bucket.first_observed_at == bucket.last_observed_at
```

- [ ] **Step 2 : Voir échouer** — `( cd packages/crawler && uv run pytest tests/domain/retention/test_buckets.py --no-cov -q )` → import error.

- [ ] **Step 3 : Implémentation**

```python
"""Bucketing journalier des observations pour la compaction (spec compaction §4).

Domaine PUR (aucune I/O). Groupe des observations par (ed2k_hash, jour UTC) et calcule les
agrégats node-agnostiques d'un bucket. Le jour UTC est la TRANCHE observed_at[:10]
("YYYY-MM-DD") — observed_at est ISO-8601 UTC à largeur fixe (connection.utc_iso), donc aucun
calcul de fuseau. filenames/node_ids sont des tableaux JSON CANONIQUES (distincts, triés) :
deux ensembles égaux ont le même texte → la dédup append-only/merge par égalité de ligne marche.
La moyenne se dérive de *_sum / observation_count (non stockée).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True)
class ObservationRow:
    """Une observation brute lue pour la compaction (champs nécessaires au bucketing)."""

    ed2k_hash: str
    node_id: str
    filename: str
    source_count: int
    complete_source_count: int
    observed_at: str


@dataclass(frozen=True)
class ObservationBucket:
    """Un bucket (ed2k_hash, jour) : les 13 colonnes de file_observation_ranges hormis id."""

    ed2k_hash: str
    bucket: str
    filenames: str
    node_ids: str
    observation_count: int
    first_observed_at: str
    last_observed_at: str
    source_count_min: int
    source_count_max: int
    source_count_sum: int
    complete_source_count_min: int
    complete_source_count_max: int
    complete_source_count_sum: int


def _json_sorted_set(values: Sequence[str]) -> str:
    """Tableau JSON canonique des valeurs distinctes triées (texte stable pour la dédup)."""
    return json.dumps(sorted(set(values)))


def bucketize(observations: Sequence[ObservationRow]) -> list[ObservationBucket]:
    """Groupe `observations` par (ed2k_hash, jour UTC) → un ObservationBucket par groupe.

    L'entrée DOIT être triée par (ed2k_hash, observed_at, id) — l'orchestration (compactor) le
    garantit par SQL. Chaque groupe (même hash, même jour) est alors contigu (jours croissants).
    Node-agnostique : toutes les observations d'un (hash, jour), tous nœuds confondus, → UN bucket.
    """
    buckets: list[ObservationBucket] = []
    for (ed2k_hash, day), group_iter in groupby(
        observations, key=lambda row: (row.ed2k_hash, row.observed_at[:10])
    ):
        group = list(group_iter)
        source_counts = [row.source_count for row in group]
        complete_counts = [row.complete_source_count for row in group]
        observed_ats = [row.observed_at for row in group]
        buckets.append(
            ObservationBucket(
                ed2k_hash=ed2k_hash,
                bucket=day,
                filenames=_json_sorted_set([row.filename for row in group]),
                node_ids=_json_sorted_set([row.node_id for row in group]),
                observation_count=len(group),
                first_observed_at=min(observed_ats),
                last_observed_at=max(observed_ats),
                source_count_min=min(source_counts),
                source_count_max=max(source_counts),
                source_count_sum=sum(source_counts),
                complete_source_count_min=min(complete_counts),
                complete_source_count_max=max(complete_counts),
                complete_source_count_sum=sum(complete_counts),
            )
        )
    return buckets
```

- [ ] **Step 4 : Voir passer** — la commande du Step 2 → vert. `mypy`/`ruff` verts (chaque test
  annoté `-> None`, params typés).

- [ ] **Step 5 : Commit** — `feat(domain): bucketize — rollup journalier node-agnostique des observations`

---

### Task 3 : Orchestration `compact/compactor.py` (+ `errors.py`)

**Files:**
- Create: `packages/crawler/src/emule_indexer/compact/__init__.py` (vide)
- Create: `packages/crawler/src/emule_indexer/compact/errors.py`
- Create: `packages/crawler/src/emule_indexer/compact/compactor.py`
- Test: `packages/crawler/tests/compact/__init__.py` (vide) + `.../helpers.py` + `.../test_compactor.py`

- [ ] **Step 1 : `errors.py`** (pas de TDD dédié — couvert par les tests du compactor/CLI)

```python
"""`CompactError` : erreur d'usage ou de compaction (message clair pour le CLI, jamais nu).

Outil opérateur standalone (spec compaction §6), indépendant du contrat d'erreur des
repositories — comme MergeError. `__main__` la rend sur stderr avec un code de sortie non nul.
"""


class CompactError(Exception):
    """Usage invalide ou compaction qui échoue (fail-fast, message clair pour l'opérateur)."""
```

- [ ] **Step 2 : Helpers de test** (`tests/compact/helpers.py`)

```python
"""Helpers de test pour la compaction : seed/lecture de file_observation_ranges.

Les autres tables (files, file_observations) passent par tests.merge.helpers.make_catalog
(catalogues fichiers réels, jamais :memory:). Ici, ce qui est spécifique au rollup.
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog

RANGE_COLUMNS = (
    "ed2k_hash", "bucket", "filenames", "node_ids", "observation_count",
    "first_observed_at", "last_observed_at",
    "source_count_min", "source_count_max", "source_count_sum",
    "complete_source_count_min", "complete_source_count_max", "complete_source_count_sum",
)


def insert_ranges(path: Path, rows: Sequence[tuple[object, ...]]) -> None:
    placeholders = ", ".join("?" for _ in RANGE_COLUMNS)
    statement = (
        f"INSERT INTO file_observation_ranges ({', '.join(RANGE_COLUMNS)}) VALUES ({placeholders})"
    )
    connection = open_catalog(path)
    try:
        for row in rows:
            connection.execute(statement, row)
    finally:
        connection.close()


def read_ranges(path: Path) -> list[tuple[object, ...]]:
    connection = open_catalog(path)
    try:
        cursor = connection.execute(f"SELECT {', '.join(RANGE_COLUMNS)} FROM file_observation_ranges")
        return sorted(cursor.fetchall())
    finally:
        connection.close()


def read_observation_days(path: Path) -> list[str]:
    """Les `observed_at` du brut RÉCENT conservé (pour vérifier la fenêtre)."""
    connection = open_catalog(path)
    try:
        cursor = connection.execute("SELECT observed_at FROM file_observations ORDER BY observed_at")
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
```

- [ ] **Step 3 : Tests du compactor** (`tests/compact/test_compactor.py`) — fichiers réels, clock injecté

```python
"""compact_catalog : reconstruction vers une sortie neuve, fenêtre alignée jour, idempotence."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emule_indexer.compact.compactor import compact_catalog
from emule_indexer.compact.errors import CompactError

from ..merge.helpers import HASH_A, count, make_catalog
from .helpers import insert_ranges, read_observation_days, read_ranges

_H = HASH_A


def _obs(name: str, sc: int, csc: int, at: str, node: str = "n1") -> dict[str, object]:
    return {
        "ed2k_hash": _H, "filename": name, "size_bytes": 1, "source_count": sc,
        "complete_source_count": csc, "raw_meta": "[]", "keyword": "k", "observed_at": at,
        "node_id": node,
    }


def _clock(moment: str):
    return lambda: datetime.fromisoformat(moment)


def _source(path: Path, observations: list[dict[str, object]]) -> Path:
    return make_catalog(path, {"files": [{"ed2k_hash": _H, "size_bytes": 1}],
                               "file_observations": observations})


def test_old_observations_become_one_bucket_per_day(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [
        _obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00"),
        _obs("f.avi", 9, 2, "2026-01-10T20:00:00.000000+00:00"),
        _obs("f.avi", 4, 1, "2026-01-11T05:00:00.000000+00:00"),
    ])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    ranges = read_ranges(out)
    assert [r[1] for r in ranges] == ["2026-01-10", "2026-01-11"]  # bucket par jour
    assert count(out, "file_observations") == 0  # tout l'ancien est bucketisé


def test_recent_window_is_kept_raw(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [
        _obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00"),  # vieux
        _obs("f.avi", 2, 0, "2026-05-30T01:00:00.000000+00:00"),  # récent
    ])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert read_observation_days(out) == ["2026-05-30T01:00:00.000000+00:00"]
    assert [r[1] for r in read_ranges(out)] == ["2026-01-10"]


def test_cutoff_day_is_whole_day_aligned(tmp_path: Path) -> None:
    # cutoff_date = 2026-06-01 ; une obs DU 2026-06-01 (même tôt) reste RÉCENTE (>= "2026-06-01").
    src = _source(tmp_path / "src.db", [
        _obs("f.avi", 1, 0, "2026-06-01T00:00:00.000000+00:00"),
    ])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-08-30T12:00:00+00:00"))
    assert read_observation_days(out) == ["2026-06-01T00:00:00.000000+00:00"]
    assert read_ranges(out) == []


def test_verbatim_tables_copied(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "src.db", {
        "files": [{"ed2k_hash": _H, "size_bytes": 7}],
        "match_decisions": [{"ed2k_hash": _H, "target_id": "S2E062A", "rule_name": "r",
                             "tier": "download", "decided_at": "t", "node_id": "n"}],
    })
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert count(out, "files") == 1
    assert count(out, "match_decisions") == 1


def test_idempotent_on_already_compacted_source(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00")])
    out1 = tmp_path / "out1.db"
    compact_catalog(src, out1, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    out2 = tmp_path / "out2.db"  # recompacter la SORTIE : ses ranges sont recopiées verbatim
    compact_catalog(out1, out2, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert read_ranges(out2) == read_ranges(out1)


def test_preexisting_ranges_copied_verbatim(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "src.db", {"files": [{"ed2k_hash": _H, "size_bytes": 1}]})
    insert_ranges(src, [(_H, "2025-12-01", '["x"]', '["n"]', 2, "2025-12-01", "2025-12-01",
                         1, 3, 4, 0, 1, 1)])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert [r[1] for r in read_ranges(out)] == ["2025-12-01"]


def test_no_old_observations_is_a_clean_noop(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-05-31T01:00:00.000000+00:00")])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert read_ranges(out) == []
    assert count(out, "file_observations") == 1


def test_output_has_append_only_ranges(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00")])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
    connection = open_catalog(out)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM file_observation_ranges")
    finally:
        connection.close()


def test_unattachable_source_errors(tmp_path: Path) -> None:
    # Header non-SQLite → l'ATTACH lève → CompactError (branche d'attache, distincte de la copie).
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"not a sqlite database header" * 8)
    with pytest.raises(CompactError, match="impossible d'attacher"):
        compact_catalog(bad, tmp_path / "out.db", keep_recent_days=90,
                        clock=_clock("2026-06-01T00:00:00+00:00"))


def test_broken_schema_source_rolls_back(tmp_path: Path) -> None:
    # Source SQLite VALIDE mais sans les tables attendues → la copie échoue (sources manque
    # après files) → ROLLBACK best-effort → CompactError (branche de copie).
    broken = tmp_path / "broken.db"
    raw = sqlite3.connect(broken)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("CREATE TABLE files (ed2k_hash TEXT PRIMARY KEY, size_bytes INTEGER)")
    raw.commit()
    raw.close()
    with pytest.raises(CompactError, match="échec de la compaction"):
        compact_catalog(broken, tmp_path / "out.db", keep_recent_days=90,
                        clock=_clock("2026-06-01T00:00:00+00:00"))
```

- [ ] **Step 4 : Voir échouer** — `( cd packages/crawler && uv run pytest tests/compact/test_compactor.py --no-cov -q )`.

- [ ] **Step 5 : Implémentation** (`compact/compactor.py`, calquée sur `merge/merger.py`)

```python
"""`compact_catalog` : compaction d'un catalog.db en une sortie NEUVE (spec compaction §5).

Reconstruction via open_catalog (schéma + triggers, migrations 0001+0002). ATTACH de la source
(hors transaction — SQLite refuse d'attacher dans une transaction), puis DANS une transaction
explicite (BEGIN…COMMIT, ROLLBACK best-effort) : copie verbatim des 5 tables intactes (ordre FK),
copie verbatim du brut RÉCENT (observed_at >= cutoff_date), bucketize du brut ANCIEN
(observed_at < cutoff_date). COMMIT puis DETACH (hors transaction). On n'écrit JAMAIS dans la
source (que des SELECT). La sortie est supposée NEUVE (le CLI le garantit) → aucune dédup.

Coupure alignée JOUR UTC (spec §5bis) : cutoff_date est une DATE "YYYY-MM-DD" ; « ancien » ⟺
observed_at < cutoff_date — la comparaison lexicographique met tout horodatage du jour de coupure
côté récent ("2026-06-01" < "2026-06-01T.."). Un jour n'est compacté qu'entièrement.
"""

import sqlite3
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from emule_indexer.adapters.persistence_sqlite.connection import Clock, open_catalog, utc_now
from emule_indexer.compact.errors import CompactError
from emule_indexer.domain.retention.buckets import ObservationRow, bucketize

_SRC = "src"

# Tables recopiées VERBATIM (ordre FK : identités d'abord). file_observation_ranges existantes
# sont recopiées telles quelles (pas de combine — spec §7).
_COPY_VERBATIM: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("files", ("ed2k_hash", "size_bytes", "aich_hash")),
    ("sources", ("user_hash", "client_name", "client_version")),
    ("match_decisions", ("ed2k_hash", "target_id", "rule_name", "tier", "decided_at", "node_id")),
    (
        "file_verifications",
        ("ed2k_hash", "verdict", "real_meta", "checks", "verified_at", "node_id"),
    ),
    (
        "file_observation_ranges",
        (
            "ed2k_hash", "bucket", "filenames", "node_ids", "observation_count",
            "first_observed_at", "last_observed_at",
            "source_count_min", "source_count_max", "source_count_sum",
            "complete_source_count_min", "complete_source_count_max", "complete_source_count_sum",
        ),
    ),
)

_OBSERVATION_COLUMNS = (
    "ed2k_hash", "filename", "size_bytes", "source_count", "complete_source_count",
    "media_length_sec", "bitrate_kbps", "codec", "file_type", "raw_meta",
    "keyword", "observed_at", "node_id",
)

_SELECT_OLD = (
    "SELECT ed2k_hash, node_id, filename, source_count, complete_source_count, observed_at "
    f"FROM {_SRC}.file_observations WHERE observed_at < ? ORDER BY ed2k_hash, observed_at, id"
)

_INSERT_RANGE = (
    "INSERT INTO main.file_observation_ranges (ed2k_hash, bucket, filenames, node_ids, "
    "observation_count, first_observed_at, last_observed_at, source_count_min, source_count_max, "
    "source_count_sum, complete_source_count_min, complete_source_count_max, "
    "complete_source_count_sum) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def compact_catalog(
    source: Path, output: Path, *, keep_recent_days: int, clock: Clock = utc_now
) -> None:
    """Compacte `source` vers `output` (NEUF), fenêtre `keep_recent_days` alignée jour UTC."""
    cutoff_date = (clock() - timedelta(days=keep_recent_days)).date().isoformat()
    connection = open_catalog(output)
    try:
        _compact_one(connection, source, cutoff_date)
    finally:
        connection.close()


def _compact_one(connection: sqlite3.Connection, source: Path, cutoff_date: str) -> None:
    try:
        connection.execute(f"ATTACH DATABASE ? AS {_SRC}", (str(Path(source).resolve()),))
    except sqlite3.Error as error:
        raise CompactError(f"impossible d'attacher la source {source} : {error}") from error
    try:
        connection.execute("BEGIN")
        try:
            for table, columns in _COPY_VERBATIM:
                projection = ", ".join(columns)
                connection.execute(
                    f"INSERT INTO main.{table} ({projection}) "
                    f"SELECT {projection} FROM {_SRC}.{table}"
                )
            recent = ", ".join(_OBSERVATION_COLUMNS)
            connection.execute(
                f"INSERT INTO main.file_observations ({recent}) "
                f"SELECT {recent} FROM {_SRC}.file_observations WHERE observed_at >= ?",
                (cutoff_date,),
            )
            _bucketize_old(connection, cutoff_date)
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise CompactError(f"échec de la compaction de {source} : {error}") from error
    finally:
        with suppress(sqlite3.Error):
            connection.execute(f"DETACH DATABASE {_SRC}")


def _bucketize_old(connection: sqlite3.Connection, cutoff_date: str) -> None:
    cursor = connection.execute(_SELECT_OLD, (cutoff_date,))
    rows = [
        ObservationRow(
            ed2k_hash=row[0], node_id=row[1], filename=row[2],
            source_count=row[3], complete_source_count=row[4], observed_at=row[5],
        )
        for row in cursor.fetchall()
    ]
    for bucket in bucketize(rows):
        connection.execute(
            _INSERT_RANGE,
            (
                bucket.ed2k_hash, bucket.bucket, bucket.filenames, bucket.node_ids,
                bucket.observation_count, bucket.first_observed_at, bucket.last_observed_at,
                bucket.source_count_min, bucket.source_count_max, bucket.source_count_sum,
                bucket.complete_source_count_min, bucket.complete_source_count_max,
                bucket.complete_source_count_sum,
            ),
        )
```

- [ ] **Step 6 : Voir passer** — la commande du Step 4 → vert. (Couverture : `test_no_old_observations_is_a_clean_noop` exerce la boucle `for bucket in ...` à vide ; les autres la remplissent.)

- [ ] **Step 7 : Commit** — `feat(compact): compact_catalog — reconstruction vers une sortie neuve (fenêtre jour, bucketize)`

---

### Task 4 : CLI `compact/__main__.py`

**Files:**
- Create: `packages/crawler/src/emule_indexer/compact/__main__.py`
- Test: `packages/crawler/tests/compact/test_cli.py`

- [ ] **Step 1 : Tests d'abord**

```python
"""CLI compact : safe-by-default (output neuf, source présente, keep-recent-days >= 0)."""

from pathlib import Path

from emule_indexer.compact.__main__ import main

from ..merge.helpers import HASH_A, make_catalog
from .helpers import read_ranges


def _src(tmp_path: Path) -> Path:
    return make_catalog(tmp_path / "src.db", {
        "files": [{"ed2k_hash": HASH_A, "size_bytes": 1}],
        "file_observations": [{
            "ed2k_hash": HASH_A, "filename": "f.avi", "size_bytes": 1, "source_count": 1,
            "complete_source_count": 0, "raw_meta": "[]", "keyword": "k",
            "observed_at": "2020-01-01T00:00:00.000000+00:00", "node_id": "n",
        }],
    })


def test_new_output_succeeds(tmp_path: Path) -> None:
    out = tmp_path / "out.db"
    assert main([str(_src(tmp_path)), "-o", str(out)]) == 0
    assert len(read_ranges(out)) == 1  # keep-recent-days par défaut = 90 ; l'obs de 2020 est vieille


def test_existing_output_refused(tmp_path: Path) -> None:
    out = tmp_path / "out.db"
    out.write_bytes(b"")
    assert main([str(_src(tmp_path)), "-o", str(out)]) == 2


def test_missing_source_refused(tmp_path: Path) -> None:
    assert main([str(tmp_path / "absent.db"), "-o", str(tmp_path / "out.db")]) == 2


def test_negative_keep_recent_days_refused(tmp_path: Path) -> None:
    assert main([str(_src(tmp_path)), "-o", str(tmp_path / "out.db"), "--keep-recent-days", "-1"]) == 2
```

- [ ] **Step 2 : Voir échouer** — `( cd packages/crawler && uv run pytest tests/compact/test_cli.py --no-cov -q )`.

- [ ] **Step 3 : Implémentation** (calquée sur `merge/__main__.py`)

```python
"""Point d'entrée `python -m emule_indexer.compact` : CLI safe-by-default de la compaction.

`main(argv) -> int` : 0 = OK ; 2 = erreur d'usage/compaction (message clair sur stderr, jamais
de traceback nu) ; argparse rend lui-même 2 pour une erreur de parsing. Aucune variable
d'environnement (doctrine du repo). Safe-by-default (spec §6) : la sortie ne doit PAS exister
(pas de --force, pas d'append) ; source absente → erreur ; keep-recent-days >= 0.
"""

import argparse
import logging
import sys
from pathlib import Path

from emule_indexer.compact.compactor import compact_catalog
from emule_indexer.compact.errors import CompactError

_LOGGER = logging.getLogger("emule_indexer.compact")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer.compact",
        description="Compacte un catalog.db (rollup journalier des observations) vers une sortie neuve.",
    )
    parser.add_argument("source", type=Path, help="catalog.db à compacter.")
    parser.add_argument(
        "--output", "-o", required=True, type=Path,
        help="Fichier de sortie NEUF (refus s'il existe ; supprimez-le pour refaire).",
    )
    parser.add_argument(
        "--keep-recent-days", type=int, default=90,
        help="Jours récents gardés bruts (défaut 90 ; 0 = compacter tout l'historique).",
    )
    return parser.parse_args(argv)


def _validate(args: argparse.Namespace) -> None:
    """Règles safe-by-default, AVANT toute ouverture/création (CompactError, message clair)."""
    if not args.source.exists():
        raise CompactError(f"source introuvable : {args.source}")
    if args.output.exists():
        raise CompactError(f"la sortie existe déjà : {args.output} (supprimez-la pour refaire)")
    if args.keep_recent_days < 0:
        raise CompactError("--keep-recent-days doit être >= 0")


def main(argv: list[str] | None = None) -> int:
    """Entrée CLI. 0 = OK, 2 = erreur d'usage/compaction (message clair sur stderr)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _validate(args)
        _LOGGER.info("compact %s → %s (keep_recent_days=%d)", args.source, args.output, args.keep_recent_days)
        compact_catalog(args.source, args.output, keep_recent_days=args.keep_recent_days)
    except CompactError as error:
        print(f"Compaction impossible : {error}", file=sys.stderr, flush=True)
        return 2
    _LOGGER.info("compaction terminée : %s", args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4 : Voir passer** — la commande du Step 2 → vert.

- [ ] **Step 5 : Commit** — `feat(compact): CLI python -m emule_indexer.compact (safe-by-default)`

---

### Task 5 : Extension du merge — 7ᵉ journal `file_observation_ranges`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/merge/merger.py`
- Modify: `packages/crawler/tests/merge/helpers.py`
- Test: `packages/crawler/tests/merge/test_merger.py`

- [ ] **Step 1 : Étendre les helpers** (`tests/merge/helpers.py`) — ajouter le set de colonnes et l'enregistrer

```python
# Après FILE_VERIFICATION_COLUMNS :
FILE_OBSERVATION_RANGE_COLUMNS = (
    "ed2k_hash", "bucket", "filenames", "node_ids", "observation_count",
    "first_observed_at", "last_observed_at",
    "source_count_min", "source_count_max", "source_count_sum",
    "complete_source_count_min", "complete_source_count_max", "complete_source_count_sum",
)
# Ajouter au mapping _COLUMNS_BY_TABLE : "file_observation_ranges": FILE_OBSERVATION_RANGE_COLUMNS
# Ajouter "file_observation_ranges" à la liste des tables itérées dans make_catalog (après
# "file_verifications" ; FK files déjà insérées en tête).
```

- [ ] **Step 2 : Test d'abord** (`tests/merge/test_merger.py`) — union + dédup `IS` + re-merge no-op

```python
def test_merge_unions_observation_ranges_and_is_idempotent(tmp_path: Path) -> None:
    row_a = {
        "ed2k_hash": HASH_A, "bucket": "2026-01-01", "filenames": '["x"]', "node_ids": '["n1"]',
        "observation_count": 2, "first_observed_at": "2026-01-01", "last_observed_at": "2026-01-01",
        "source_count_min": 1, "source_count_max": 3, "source_count_sum": 4,
        "complete_source_count_min": 0, "complete_source_count_max": 1, "complete_source_count_sum": 1,
    }
    row_b = {**row_a, "node_ids": '["n2"]', "source_count_sum": 6}  # autre nœud → ligne distincte
    src1 = make_catalog(tmp_path / "s1.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 1}],
                                             "file_observation_ranges": [row_a]})
    src2 = make_catalog(tmp_path / "s2.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 1}],
                                             "file_observation_ranges": [row_b]})
    out = tmp_path / "out.db"
    merge_catalogs(out, [src1, src2])
    assert count(out, "file_observation_ranges") == 2  # union (deux node_ids distincts)
    merge_catalogs(out, [src1, src2], dest_is_source=False)  # re-merge → no-op
    assert count(out, "file_observation_ranges") == 2
```
(Imports à compléter au besoin : `make_catalog`, `count`, `HASH_A`, `merge_catalogs` déjà importés
dans le fichier.)

- [ ] **Step 3 : Voir échouer** — `( cd packages/crawler && uv run pytest tests/merge/test_merger.py -k observation_ranges --no-cov -q )` (la copie manque → 0 ligne ou erreur).

- [ ] **Step 4 : Implémentation** (`merge/merger.py`) — ajouter la copie de journal

```python
# Après _COPY_FILE_VERIFICATIONS :
_COPY_FILE_OBSERVATION_RANGES = _copy_journal(
    "file_observation_ranges",
    (
        "ed2k_hash", "bucket", "filenames", "node_ids", "observation_count",
        "first_observed_at", "last_observed_at",
        "source_count_min", "source_count_max", "source_count_sum",
        "complete_source_count_min", "complete_source_count_max", "complete_source_count_sum",
    ),
)

# Ajouter _COPY_FILE_OBSERVATION_RANGES à _COPY_STATEMENTS (après _COPY_FILE_VERIFICATIONS ;
# FK files déjà copiées en tête). Mettre à jour le docstring du module (« 6 tables » → « 7 »).
```

- [ ] **Step 5 : Voir passer** — la commande du Step 3 + la suite merge complète vertes.

- [ ] **Step 6 : Commit** — `feat(merge): unit file_observation_ranges (7ᵉ journal, union-dedup)`

---

### Task 6 : Documentation (runbook + CLAUDE.md)

**Files:**
- Modify: `docs/runbook-deployment.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1 : Runbook** — ajouter une section « Compaction du catalogue » : pourquoi (seule
  `file_observations` croît) ; quand (crawler arrêté, occasionnel/cron opérateur) ; commande
  `python -m emule_indexer.compact catalog.db -o catalog-compact.db [--keep-recent-days 90]` ;
  modèle (rollup journalier node-agnostique, `file_observation_ranges`) ; ordre recommandé
  **merge-puis-compact** ; conséquence §8 (`last_observation` rend `None` pour un fichier non vu
  depuis > `keep_recent_days`).

- [ ] **Step 2 : CLAUDE.md** — une phrase dans l'inventaire « Built so far » : l'outil de compaction
  (`python -m emule_indexer.compact`, migration `catalog/0002` append-only, cœur pur
  `domain/retention/buckets.py`, rollup journalier node-agnostique, merge étendu), et retirer
  « retention-compaction » de la liste « Not built yet ».

- [ ] **Step 3 : Commit** — `docs: runbook + CLAUDE.md pour la compaction du catalogue`

---

## Notes d'exécution (subagent-driven)

- **Ordre** : 1 → 2 → 3 → 4 → 5 → 6. Tâche 3 dépend de 1+2 ; 4 de 3 ; 5 de 1.
- Chaque tâche : implémenteur frais → revue spec → revue qualité → tâche suivante. Revue holistique
  finale avant tag (elle attrape les bugs transverses — la garder).
- **Gate par paquet** vert avant chaque commit. Tests d'intégration : aucun ici (tout est I/O SQLite
  fichier local, comme le merge).
- **Pièges connus** : `groupby` exige une entrée triée sur sa clé (le SQL `ORDER BY ed2k_hash,
  observed_at, id` la garantit) ; couvrir la boucle `for bucket` à vide ET non vide (100 % branch) ;
  `RAISE(ABORT)` et les `CHECK` remontent en `sqlite3.IntegrityError` ; sqlfluff doit passer sur la
  nouvelle migration (mirror du style de `0001`).
