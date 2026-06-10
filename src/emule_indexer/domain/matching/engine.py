"""Moteur d'ÉVALUATION du matching (cf. spec §8.5, partie évaluation).

Domaine PUR. Prend une :class:`MatcherConfig` déjà validée (Plan 2b) et des
:class:`TargetSegment`, pré-résout les arbres de matchers par cible une fois à la
construction (via :class:`MatcherResolver`), puis rend une décision EN MÉMOIRE pour un
:class:`FileCandidate`. AUCUNE I/O, AUCUN logging, AUCUNE DB : l'« explicabilité loggée
en DEBUG » de §8.5 = le moteur RETOURNE un résultat explicable ; le logging est l'affaire
d'un adapter d'un plan ultérieur.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.matchers import CoverageMatcher
from emule_indexer.domain.matching.models import FileCandidate, TargetSegment
from emule_indexer.domain.matching.resolver import MatcherResolver, ResolvedTarget


@dataclass(frozen=True)
class Explanation:
    """Pourquoi cette décision (cf. spec §8.5 : tokens/règles déclenchés + value coverage).

    Concerne la SEULE cible gagnante. ``rules_fired`` : noms des règles vraies pour cette
    cible, dans l'ordre de la config (la 1re est la gagnante). ``tokens_matched`` : noms
    des tokens nommés de la config qui matchent (triés). ``coverage_values`` : pour CHAQUE
    token coverage de la config (qu'il ait matché ou non), ``(nom, value(candidate))``
    triés — le score aide à déboguer un seuil même sous la barre. Tuples (et non dicts)
    pour rester GELÉ/hashable et déterministe.
    """

    target_id: str
    rules_fired: tuple[str, ...]
    tokens_matched: tuple[str, ...]
    coverage_values: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class MatchDecision:
    """Décision fichier (cf. spec §8.5). Porte les 3 colonnes de match_decisions (§11).

    ``target_id``/``rule_name``/``tier`` = exactement les colonnes que ``match_decisions``
    persistera (§11). ``decided_at``/``node_id``/``ed2k_hash`` ne sont PAS ici : ce sont
    des colonnes de persistance (horloge + identité + clé contenu) injectées par l'adapter
    DB d'un plan ultérieur. ``explanation`` embarque l'explicabilité (§8.5).
    """

    target_id: str
    rule_name: str
    tier: str
    explanation: Explanation


# Rang des paliers (cf. spec §8.5 : « palier le plus haut, download>notify>catalog »).
# Entier croissant = palier plus haut. `TIERS` (config) donne l'ensemble LICITE ; ce
# rang donne l'ORDRE de décision. Un test vérifie set(_TIER_RANK) == TIERS.
_TIER_RANK: dict[str, int] = {"catalog": 0, "notify": 1, "download": 2}


def _first_matching_rule(
    config: MatcherConfig,
    resolved: ResolvedTarget,
    candidate: FileCandidate,
) -> tuple[int, str, str] | None:
    """1re règle vraie pour (candidate, cible résolue) → ``(index, nom, tier)`` (§8.5).

    Parcourt ``config.rules`` DANS L'ORDRE (l'index = la position = la priorité) ; pour
    chaque règle, évalue l'arbre déjà construit ``resolved.rules[rule.name]``. Renvoie le
    1er match ; ``None`` si aucune règle ne matche (la cible ne contribue rien).
    """
    for index, rule in enumerate(config.rules):
        if resolved.rules[rule.name].matches(candidate):
            return (index, rule.name, rule.tier)
    return None


def _explain(
    config: MatcherConfig,
    resolved: ResolvedTarget,
    candidate: FileCandidate,
) -> Explanation:
    """Construit l'explication de la cible GAGNANTE (cf. spec §8.5).

    ``rules_fired`` : règles vraies dans l'ordre de la config. ``tokens_matched`` : tokens
    nommés qui matchent (triés). ``coverage_values`` : ``(nom, value)`` des tokens coverage
    (triés). Lit ``CoverageMatcher.value()`` (hors Protocol) via ``isinstance``.
    """
    rules_fired = tuple(
        rule.name for rule in config.rules if resolved.rules[rule.name].matches(candidate)
    )
    tokens_matched = tuple(
        sorted(name for name, matcher in resolved.tokens.items() if matcher.matches(candidate))
    )
    coverage_values = tuple(
        (name, matcher.value(candidate))
        for name, matcher in sorted(resolved.tokens.items())
        if isinstance(matcher, CoverageMatcher)
    )
    return Explanation(
        target_id=resolved.target.target_id,
        rules_fired=rules_fired,
        tokens_matched=tokens_matched,
        coverage_values=coverage_values,
    )


class MatchingEngine:
    """Façade pure du moteur d'évaluation (cf. spec §8.5). Pré-résout les cibles une fois.

    Brute-force §8.5 : chaque fichier est évalué contre TOUTES les cibles (aucune
    heuristique d'entonnoir). Les arbres de matchers (regex interpolées+compilées par
    cible) sont construits UNE FOIS à la construction. ``max_filename_length`` borne la
    longueur du nom avant matching (§8.5/§14) : un nom plus long est écarté (``None``).
    """

    def __init__(
        self,
        config: MatcherConfig,
        targets: Sequence[TargetSegment],
        *,
        max_filename_length: int = 4096,
    ) -> None:
        self._config = config
        self._max_filename_length = max_filename_length
        resolver = MatcherResolver(config)
        self._resolved: tuple[ResolvedTarget, ...] = tuple(
            resolver.resolve_all(target) for target in targets
        )

    def evaluate(self, candidate: FileCandidate) -> MatchDecision | None:
        """Décision fichier déterministe (cf. spec §8.5) ou ``None`` (fichier écarté).

        Bornage de longueur d'abord. Puis, par cible, 1re règle vraie ; décision = palier
        le plus haut, départage déterministe par index de règle puis ``target_id``. Aucune
        règle vraie nulle part → ``None``.
        """
        if len(candidate.filename) > self._max_filename_length:
            return None
        best: tuple[int, int, str] | None = None  # (-rang_palier, index_règle, target_id)
        best_resolved: ResolvedTarget | None = None
        best_rule_name = ""
        best_tier = ""
        for resolved in self._resolved:
            outcome = _first_matching_rule(self._config, resolved, candidate)
            if outcome is None:
                continue
            index, rule_name, tier = outcome
            # Clé de tri : palier le plus HAUT d'abord (-rang), puis index le plus PETIT,
            # puis target_id le plus PETIT. min() sur cette clé donne le gagnant.
            key = (-_TIER_RANK[tier], index, resolved.target.target_id)
            if best is None or key < best:
                best = key
                best_resolved = resolved
                best_rule_name = rule_name
                best_tier = tier
        if best_resolved is None:
            return None
        return MatchDecision(
            target_id=best_resolved.target.target_id,
            rule_name=best_rule_name,
            tier=best_tier,
            explanation=_explain(self._config, best_resolved, candidate),
        )
