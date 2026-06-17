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
