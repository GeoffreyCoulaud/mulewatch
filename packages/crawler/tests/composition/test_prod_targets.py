"""Garde-fou data : le catalogue PROD (deploy/config/crawler/targets.yml) se charge et
respecte les invariants attendus (180 cibles S1+S2, numérotation contiguë, 17 retrouvés).

Cf. spec 2026-06-30-targets-keroro-dual-numbering §6. Les 26 épisodes mono n'ont qu'une
cible (segment A) ; les épisodes bi-segment en ont deux.
"""

from pathlib import Path

from catalog_matching.validation import parse_targets
from emule_indexer.adapters.config.yaml_loader import load_yaml

_TARGETS = Path(__file__).resolve().parents[4] / "deploy" / "config" / "crawler" / "targets.yml"

# Segments retrouvés (cf. spec §6) : 17 au total.
_FOUND = {
    "S1E001A",
    "S1E001B",
    "S1E002A",
    "S1E002B",
    "S1E003A",
    "S1E003B",
    "S1E004A",
    "S1E004B",
    "S1E005A",
    "S1E005B",
    "S1E006A",
    "S1E006B",
    "S1E010A",  # mono retrouvé
    "S1E027B",  # "Station thermale à gogo !" (segment B), A perdu
    "S1E036B",  # "La station de ski privée des Bellair" (segment B), A perdu
    "S2E062A",  # S02E11A (absolu 62), B perdu
    "S2E103A",  # S02E52 (absolu 103), mono retrouvé
}


def test_prod_targets_load_180_unique_segments() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    assert len(targets) == 180
    assert len({t.target_id for t in targets}) == 180


def test_prod_targets_segment_counts_per_season() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    assert sum(t.season == 1 for t in targets) == 91
    assert sum(t.season == 2 for t in targets) == 89


def test_prod_targets_absolute_numbering_is_contiguous_1_to_103() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    assert {t.absolute_number for t in targets} == set(range(1, 104))


def test_prod_targets_seasonal_relationship_holds() -> None:
    # seasonal_number est load-bearing pour les formes saisonnales (S2E11A / 2x11A) et le
    # token mono numéro-nu : on verrouille la relation absolute = offset + seasonal et la
    # contiguïté saisonnale, pour qu'une transcription erronée d'un seasonal_number (avec un
    # absolute valide) ne route pas silencieusement la forme saisonnale vers le mauvais épisode.
    targets = parse_targets(load_yaml(_TARGETS))
    for t in targets:
        offset = 0 if t.season == 1 else 51
        assert t.absolute_number == offset + t.seasonal_number, t.target_id
    assert sorted({t.seasonal_number for t in targets if t.season == 1}) == list(range(1, 52))
    assert sorted({t.seasonal_number for t in targets if t.season == 2}) == list(range(1, 53))


def test_prod_targets_found_segments_match_recovered_list() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    found = {t.target_id for t in targets if t.status == "found"}
    assert found == _FOUND
