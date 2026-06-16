"""Tests TDD du cœur ``merge_catalogs`` (ATTACH + INSERT…SELECT idempotent) — §7 du design.

Tout est testé sans Docker : on fabrique N ``catalog.db`` réels (helpers), on merge, on
asserte contenu + cardinalité + ``id`` réassignés + idempotence.
"""

import sqlite3
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.merge.errors import MergeError
from emule_indexer.merge.merger import merge_catalogs

from .helpers import (
    FILE_OBSERVATION_COLUMNS,
    HASH_A,
    HASH_B,
    count,
    hash_for,
    ids,
    make_catalog,
    rows_without_id,
)


def _file_observation(ed2k_hash: str, *, node_id: str, observed_at: str) -> dict[str, object]:
    """Une observation complète (colonnes nullable laissées à None volontairement)."""
    return {
        "ed2k_hash": ed2k_hash,
        "filename": "keroro.avi",
        "size_bytes": 100,
        "source_count": 3,
        "complete_source_count": 1,
        "media_length_sec": None,
        "bitrate_kbps": None,
        "codec": None,
        "file_type": None,
        "raw_meta": "[]",
        "keyword": "keroro",
        "observed_at": observed_at,
        "node_id": node_id,
    }


def _full_catalog(letter: str, *, node_id: str) -> dict[str, list[dict[str, object]]]:
    """Un catalogue cohérent : 1 fichier, 1 source, et 1 ligne dans chacun des 4 journaux."""
    ed2k = hash_for(letter)
    user = f"user-{letter}"
    return {
        "files": [{"ed2k_hash": ed2k, "size_bytes": 100}],
        "sources": [{"user_hash": user, "client_name": "aMule"}],
        "file_observations": [_file_observation(ed2k, node_id=node_id, observed_at="t1")],
        "source_observations": [
            {
                "user_hash": user,
                "ed2k_hash": ed2k,
                "raw_meta": "[]",
                "observed_at": "t1",
                "node_id": node_id,
            }
        ],
        "match_decisions": [
            {
                "ed2k_hash": ed2k,
                "target_id": "S2E062A",
                "rule_name": "r",
                "tier": "download",
                "decided_at": "t1",
                "node_id": node_id,
            }
        ],
        "file_verifications": [
            {
                "ed2k_hash": ed2k,
                "verdict": "clean",
                "verified_at": "t1",
                "node_id": node_id,
            }
        ],
    }


_ALL_TABLES = (
    "files",
    "sources",
    "file_observations",
    "source_observations",
    "match_decisions",
    "file_verifications",
)


def test_t1_merge_two_distinct_catalogs(tmp_path: Path) -> None:
    src_a = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    src_b = make_catalog(tmp_path / "b.db", _full_catalog("b", node_id="node-b"))
    src_c = make_catalog(tmp_path / "c.db", _full_catalog("c", node_id="node-c"))
    out = tmp_path / "out.db"

    # N=3 prouve aussi qu'on attache une source à la fois (jamais > 1 base attachée).
    merge_catalogs(out, [src_a, src_b, src_c])

    # Cardinalité de chaque table = somme (contenus disjoints) ; FK satisfaites (sinon le
    # COMMIT aurait levé). Toutes les lignes des 3 sources sont présentes en sortie.
    for table in _ALL_TABLES:
        assert count(out, table) == 3
        expected = sorted(
            rows_without_id(src_a, table)
            + rows_without_id(src_b, table)
            + rows_without_id(src_c, table)
        )
        assert rows_without_id(out, table) == expected


def test_t2_merge_overlapping_identity_files_or_ignore(tmp_path: Path) -> None:
    # Les deux sources partagent le MÊME ed2k_hash et le MÊME user_hash (identité-contenu).
    src_a = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "sources": [{"user_hash": "shared", "client_name": "aMule"}],
        },
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "sources": [{"user_hash": "shared", "client_name": "aMule"}],
        },
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    # INSERT OR IGNORE : la PK déjà présente est ignorée → une seule ligne chacune.
    assert count(out, "files") == 1
    assert count(out, "sources") == 1


def test_t3_re_merge_is_idempotent(tmp_path: Path) -> None:
    src_a = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    src_b = make_catalog(tmp_path / "b.db", _full_catalog("b", node_id="node-b"))
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])
    first_pass = {table: rows_without_id(out, table) for table in _ALL_TABLES}

    # Re-merge des mêmes sources dans la même sortie → WHERE NOT EXISTS faux partout → no-op.
    merge_catalogs(out, [src_a, src_b])

    for table in _ALL_TABLES:
        assert rows_without_id(out, table) == first_pass[table]


def test_t4_journal_dedup_identical_rows_including_nulls(tmp_path: Path) -> None:
    # Observation BIT-POUR-BIT identique (mêmes NULL sur media_length_sec/bitrate/codec/file_type).
    obs = _file_observation(HASH_A, node_id="node", observed_at="t1")
    src_a = make_catalog(
        tmp_path / "a.db",
        {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}], "file_observations": [obs]},
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}], "file_observations": [obs]},
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    # Sans l'opérateur IS, NULL=NULL serait faux → 2 lignes. Avec IS → une seule.
    assert count(out, "file_observations") == 1


def test_t4_journal_distinct_observed_at_keeps_both(tmp_path: Path) -> None:
    # Même fichier, même nœud, deux INSTANTS → deux observations RÉELLES, clés naturelles
    # distinctes → les deux conservées (non-destructeur).
    src_a = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [_file_observation(HASH_A, node_id="node", observed_at="t1")],
        },
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [_file_observation(HASH_A, node_id="node", observed_at="t2")],
        },
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    assert count(out, "file_observations") == 2


def test_t5_journal_drops_local_id(tmp_path: Path) -> None:
    # Chaque source a une observation DISTINCTE qui (par autoincrément) porte id=1.
    src_a = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [_file_observation(HASH_A, node_id="node-a", observed_at="t1")],
        },
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {
            "files": [{"ed2k_hash": HASH_B, "size_bytes": 200}],
            "file_observations": [_file_observation(HASH_B, node_id="node-b", observed_at="t2")],
        },
    )
    assert ids(src_a, "file_observations") == [1]
    assert ids(src_b, "file_observations") == [1]
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    # On ne copie PAS id : la base réassigne 1 et 2, pas une collision.
    assert ids(out, "file_observations") == [1, 2]


def test_t6_fk_order_inserts_identity_first(tmp_path: Path) -> None:
    # Une source dont des journaux référencent files/sources. Si l'ordre d'insertion était
    # inversé (journaux avant identités), la FK lèverait — le merge réussit donc l'ordre tient.
    src = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    assert count(out, "file_observations") == 1
    assert count(out, "source_observations") == 1
    assert count(out, "match_decisions") == 1
    assert count(out, "file_verifications") == 1


def test_t14_aich_first_wins_a_then_b(tmp_path: Path) -> None:
    # srcA : aich=NULL ; srcB : aich renseigné ; merge A→B → la ligne garde aich=NULL.
    src_a = make_catalog(
        tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": None}]}
    )
    src_b = make_catalog(
        tmp_path / "b.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": "X"}]}
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    assert rows_without_id(out, "files") == [(HASH_A, 100, None)]


def test_t14_aich_first_wins_b_then_a(tmp_path: Path) -> None:
    # Ordre inverse B→A → la ligne garde aich='X' (premier arrivé gagne, ride §6 figée).
    src_a = make_catalog(
        tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": None}]}
    )
    src_b = make_catalog(
        tmp_path / "b.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": "X"}]}
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_b, src_a])

    assert rows_without_id(out, "files") == [(HASH_A, 100, "X")]


def test_t15_append_only_triggers_present_on_output(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}]})
    out = tmp_path / "out.db"
    merge_catalogs(out, [src])

    connection = open_catalog(out)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="files est append-only"):
            connection.execute("UPDATE files SET size_bytes = 2")
        with pytest.raises(sqlite3.IntegrityError, match="files est append-only"):
            connection.execute("DELETE FROM files")
    finally:
        connection.close()


def test_t16_merger_wraps_source_copy_in_a_transaction(tmp_path: Path) -> None:
    # Une source au schéma cassé (DB SQLite vide, sans les tables) → la copie échoue à
    # mi-parcours → ROLLBACK : la sortie ne garde PAS de copie partielle de cette source.
    good = make_catalog(tmp_path / "good.db", _full_catalog("a", node_id="node-a"))

    broken = tmp_path / "broken.db"
    raw = sqlite3.connect(broken)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("CREATE TABLE files (ed2k_hash TEXT PRIMARY KEY, size_bytes INTEGER)")
    raw.execute(f"INSERT INTO files VALUES ('{HASH_B}', 200)")
    raw.commit()
    raw.close()

    out = tmp_path / "out.db"
    merge_catalogs(out, [good])  # la bonne source d'abord, dans son propre merge réussi.

    with pytest.raises(MergeError, match="échec de la copie"):
        merge_catalogs(out, [broken], dest_is_source=False)

    # files (1ʳᵉ table) a pu être copié AVANT l'échec sur file_observations (table absente) ;
    # le ROLLBACK doit l'avoir annulé → la sortie n'a toujours QUE le contenu de `good`.
    assert rows_without_id(out, "files") == [(HASH_A, 100, None)]


def test_t16_unattachable_source_errors(tmp_path: Path) -> None:
    # Une source qui n'est PAS une base SQLite (fichier exists mais en-tête invalide) →
    # l'ATTACH lui-même lève → MergeError clair (branche d'attache, distincte de la copie).
    not_a_db = tmp_path / "garbage.db"
    not_a_db.write_bytes(b"not a sqlite database header" * 8)
    out = tmp_path / "out.db"

    with pytest.raises(MergeError, match="impossible d'attacher"):
        merge_catalogs(out, [not_a_db])


def test_t17_single_source_merge(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    for table in _ALL_TABLES:
        assert rows_without_id(out, table) == rows_without_id(src, table)


def test_t18_dedups_identical_rows_internal_to_one_source(tmp_path: Path) -> None:
    # UNE seule source contenant DEUX lignes de journal bit-pour-bit identiques (clé
    # naturelle identique, id différent par autoincrément, COLONNES NULL incluses) PLUS une
    # ligne légitimement distincte (un seul champ diffère). La fusion N=1 doit NORMALISER :
    # collapser les doublons internes (promesse §1/§8 — dédup at-least-once d'un seul
    # catalogue) sans jamais perdre la ligne distincte.
    identical = _file_observation(HASH_A, node_id="node", observed_at="t1")
    distinct = _file_observation(HASH_A, node_id="node", observed_at="t2")  # observed_at diffère
    src = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [identical, dict(identical), distinct],
        },
    )
    # La source contient bien 3 lignes (dont 2 jumelles) AVANT merge.
    assert count(src, "file_observations") == 3
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    # Doublon interne collapsé (2 jumelles → 1) ; ligne distincte préservée → 2 lignes.
    assert count(out, "file_observations") == 2
    assert rows_without_id(out, "file_observations") == sorted(
        [
            tuple(identical[column] for column in FILE_OBSERVATION_COLUMNS),
            tuple(distinct[column] for column in FILE_OBSERVATION_COLUMNS),
        ],
        key=lambda row: tuple(str(value) for value in row),
    )

    # Re-merge = no-op (idempotent même après normalisation).
    merge_catalogs(out, [src])
    assert count(out, "file_observations") == 2
