# Spec — Targets Keroro, double numérotation & nettoyage (dates/aliases)

> Sous-projet : moteur de matching (`v0.4.0-engine`) + données de catalogue.
> Conçu avec Geoffrey le 2026-06-30. Jalon visé : `v0.21.0-targets-keroro`.
> Source des titres : liste FR des épisodes de *Keroro, mission Titar*
> (`fr.wikipedia.org/wiki/Liste_des_épisodes_de_Keroro,_mission_Titar`).

## 1. Contexte & objectif

Aujourd'hui le catalogue de cibles (`deploy/config/crawler/targets.yml`) ne contient
qu'**une cible canonique de démonstration** (`S2E062A/B`). Le but réel du projet —
retrouver les épisodes/demi-épisodes perdus de la VF de Keroro — exige le **catalogue
complet** des segments réellement diffusés, avec leurs **titres français exacts**
(le titre est le principal moteur de recall : deux des quatre règles reposent sur
`coverage: title`).

En parallèle, les noms de fichiers eD2k rencontrés dans la nature désignent un épisode
soit par son **numéro absolu** (continu sur toute la série, ex. `62`), soit par son
**numéro saisonnal** explicite (`S2E11`, `2x11`). Le token numérique actuel ne connaît
qu'**un seul** nombre (`{number}`) et **aucun** `{season}`. Pour matcher les deux familles
de désignation, le moteur doit exposer les deux numéros + la saison à l'interpolation.

Enfin, deux notions du modèle sont des **reliques** sans valeur pour un outil de
recherche et issues d'une mauvaise communication initiale : la **date de diffusion**
(`broadcast_date` + placeholder `{date_alt}` + sa machinerie) et le champ **`aliases`**
(chargé mais consommé **nulle part** dans le matching). On les **supprime entièrement**.

Ce sous-projet livre donc : (a) l'**extension** du moteur pour la double numérotation,
(b) la **suppression** des dates et de `aliases`, (c) la **donnée** complète des saisons
1 et 2.

## 2. Périmètre

**Dans le scope :**
- **Extension** du modèle `TargetSegment` : `number` → `absolute_number`, ajout de
  `seasonal_number`. `target_id` reste bâti sur l'absolu (`S2E062A`, inchangé).
- **Extension** de l'interpolation : placeholders `{season}` et `{seasonal_number}`
  ajoutés, `{number}` renommé en `{absolute_number}`.
- **Suppression des dates** : champ `broadcast_date`, placeholder `{date_alt}`, table
  `FRENCH_MONTHS`, fonction `date_alternation_pattern`, exception `MissingDateError`,
  le `try/except MissingDateError` du resolver, la sonde datée, et les imports `datetime`
  devenus inutiles (`models.py`, `interpolation.py`, `validation.py`).
- **Suppression de `aliases`** : champ du modèle, parsing, l'import `field` devenu inutile.
- Réécriture du token `segment_id` (`matcher.yml` + fixture `canonical_config.yaml`) pour
  alterner sur les deux familles de formes ; golden corpus enrichi.
- **Données** : `deploy/config/crawler/targets.yml` rempli avec les **180 segments**
  (S1 = 91, S2 = 89 ; 26 épisodes mono, cf. §6), titres FR, `status` (`found`/`lost`).
- Fixtures de test (`canonical_targets.yaml`, `tests/smoke/targets.yml`) migrées au schéma,
  gardées **minimales** ; tous les tests référençant dates/aliases nettoyés.
- `CLAUDE.md` rafraîchi (placeholders d'interpolation, mention « folded French months »).
- Gate complet vert (4 paquets + ruff + format + mypy + sqlfluff + check_templates).

**Hors scope (différé) :**
- **Saison 3** : section Wikipedia vide (aucun titre) → impossible à spécifier depuis
  cette source. Pas de cibles S3.
- Toute modification des schémas SQLite : `target_id` est une simple colonne TEXT, aucune
  colonne `broadcast_date`/`aliases` n'existe en base (vérifié) → **aucune migration**.

## 3. Décisions verrouillées

- **Nommage explicite des trois champs** : `season`, `seasonal_number`, `absolute_number`
  (et non `number`/`season_number`, jugés confusants). Le placeholder devient
  `{absolute_number}` ; **`{number}` disparaît** de la whitelist (placeholder inconnu →
  `InterpolationError`, fail-fast au chargement via la sonde).
- **`target_id` reste l'absolu** : `S{season}E{absolute_number:03d}{LETTER}` → `S2E062A`.
  Zéro churn sur les `target_id` attendus du golden corpus.
- **`seasonal_number` ET `absolute_number` tous deux obligatoires** (`ConfigError` si
  absent), pour que `{season}`/`{seasonal_number}` soient **toujours** interpolables sans
  brancher sur `None`.
- **`status` est PAR SEGMENT** (et non par épisode) : c'est la granularité du modèle
  (`TargetSegment.status`) et la seule façon d'exprimer « A retrouvé, B perdu » pour un
  demi-épisode. `parse_targets` lit `status` dans chaque `segment:` (défaut `lost`) ;
  l'ancienne lecture au niveau épisode disparaît.
- **Pas de contrôle de cohérence `absolute = offset + seasonal` dans le moteur** : donnée
  Keroro-spécifique, le moteur reste générique. Cohérence garantie à l'**extraction**
  (S1 : `absolute = seasonal` ; S2 : `absolute = 51 + seasonal`).
- **`segment_id` matche les deux familles, jamais le nombre nu** : l'absolu garde un
  garde-fou de préfixe (`n°…`) ; le saisonnal s'appuie sur ses marqueurs structurels
  (`S…E…`, `…x…`). Le « 11 » nu n'est **pas** matché.
- **Dates et `aliases` supprimés, pas neutralisés** : on retire la machinerie, on ne la
  laisse pas dormante. La « décision design test-gaps#2 » (neutralisation d'une règle
  datée pour une cible sans date, `resolver.py`) **disparaît avec sa cause**. Après coup,
  un `{date_alt}` dans un matcher est une **erreur de config** (rejetée au chargement),
  pas un cas géré.
- **Seul le fichier PROD (`deploy/config/crawler/targets.yml`) porte les 180 segments.**
  Les fixtures de test restent minimales.

## 4. Modèle de données

```python
@dataclass(frozen=True)
class TargetSegment:
    season: int
    seasonal_number: int          # numéro dans la saison (1..51 / 1..52)
    absolute_number: int          # numéro continu sur la série (1..103)
    segment: str
    title: str
    status: str = "lost"

    @property
    def target_id(self) -> str:
        return f"S{self.season}E{self.absolute_number:03d}{self.segment.upper()}"
```

Plus de `broadcast_date`, plus de `aliases` ; imports `datetime` et `field` retirés.

**Schéma YAML d'un épisode :**
```yaml
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - { letter: A, title: "Les demoiselles cambrioleuses", status: found }
      - { letter: B, title: "Le grand combat sous-marin" }   # status défaut "lost"
```

`parse_targets` (validation.py) : `season`, `seasonal_number`, `absolute_number`
obligatoires au niveau épisode (`ConfigError` si absent ou non-`int`) ; `status` optionnel
**par segment** (défaut `lost`) ; `segments` optionnel (défaut `[]`) ; unicité des
`target_id` conservée. Le parsing de `broadcast_date`/`aliases` est **retiré**. La **sonde
de validation** (`_PROBE_TARGET`) perd son `broadcast_date` et gagne
`seasonal_number`/`absolute_number`.

## 5. Extension & nettoyage du moteur d'interpolation

Whitelist : `{number} {segment} {title} {date_alt}` → **`{season} {seasonal_number}
{absolute_number} {segment} {title}`**. Chaque numéro est rendu via
`str(re2.escape(str(...)))`.

**Supprimé de `interpolation.py`** : la table `FRENCH_MONTHS`, la fonction
`date_alternation_pattern`, la classe `MissingDateError`, et la branche `{date_alt}` du
callback. **`resolver.py`** : l'import `MissingDateError` et le `try/except` disparaissent —
`interpolate(pattern, target)` est appelé directement (les autres erreurs, dont
`InterpolationError` pour placeholder inconnu, restent attrapées au **chargement** par la
sonde, pas par cible).

**Token `segment_id` réécrit** (forme cible, regex exacte arrêtée en TDD) :
```yaml
# absolu (gardé par n°…)        |  saisonnal SxxExx          |  saisonnal NxM
segment_id:
  regex: "(?:n[°o]?\\s*0*{absolute_number}|s0*{season}\\s*e0*{seasonal_number}|0*{season}\\s*x\\s*0*{seasonal_number})\\s*{segment}"
```
Les autres tokens/règles (`keroro`, `titar`, `teletoon`, `foreign_lang`, `title_hit`,
`is_video`, et les 4 règles `download/notify/catalog`) sont **inchangés**.

## 6. Données — les 180 segments (S1 + S2)

**Structure réelle (vérifiée par double extraction sonnet+opus du wikitext brut, identiques).**
La liste FR n'est **pas** un A/B uniforme : c'est une liste numérotée et **26 épisodes sont
mono-segment** (un seul titre listé) — S1 : 10, 11, 22, 23, 25, 26, 31, 33, 35, 46, 51
(11 ép.) ; S2 : 20, 22, 23, 24, 25, 27, 29, 37, 39, 43, 44, 49, 50, 51, 52 (15 ép.).

- **Saison 1** : 51 épisodes → `seasonal 1..51`, `absolute = seasonal` (1..51). 40 bi-segment
  + 11 mono → **91 segments**.
- **Saison 2** : 52 épisodes → `seasonal 1..52`, `absolute = 51 + seasonal` (52..103).
  37 bi-segment + 15 mono → **89 segments**.
- Total **180 segments**. Un épisode mono → **une** cible (segment `A`). On ne fabrique pas
  de cible B sans titre. Titres FR recopiés **verbatim** de la source (graphies non
  normalisées conservées : « Economie », « Arthus », « plait » — le `fold()` + le fuzzy
  rapidfuzz absorbent ces variantes).
- **S1#11 = « Inédit »** (placeholder de la source) : **conservé** (décision Geoffrey) ; le
  matcher sera durci si ce titre s'avère bruyant.

**Statuts des retrouvés — 17 segments `found`** (le reste = `lost`) :

| Retrouvé (notation user) | season / seasonal / absolute | Statut |
|---|---|---|
| S01E01 → S01E06 (bi-segment) | 1 / 1..6 / 1..6 | **A + B = `found`** (12) |
| S01E10A (**mono**) | 1 / 10 / 10 | A = `found` (1) |
| S01E27A | 1 / 27 / 27 | A = `found`, B = `lost` (1) |
| S01E36A | 1 / 36 / 36 | A = `found`, B = `lost` (1) |
| S02E11A | 2 / 11 / 62 | A = `found`, B = `lost` (1) |
| S02E52A (**mono**) | 2 / 52 / 103 | A = `found` (1) |

> Tes demi-épisodes perdus (S01E27B, S01E36B, S02E11B) ont tous leur **B titré** dans la
> source → la lacune mono ne touche aucune cible prioritaire.

## 6 bis. Extension matcher — épisodes mono sans lettre (tâche dédiée)

Un fichier d'épisode mono est souvent nommé sans la lettre A/B (et souvent sans la structure
`N°`). `segment_id` exigeant la lettre, ces fichiers n'ont aujourd'hui que le **matching par
titre** (qui fonctionne). Évolution prévue, en **tâche séparée** (niveau A ou B à trancher
au moment de la tâche) : `parse_targets` pose un champ dérivé `sole_segment` (épisode à 1
segment) ; pour ces cibles, la lettre devient optionnelle (niveau A : structure conservée)
voire le numéro nu est accepté (niveau B : plus permissif, plus bruyant), avec garde de bord
chiffre RE2 `(?:[^0-9]|$)`. Les cibles bi-segment gardent la lettre **obligatoire**
(précision A/B intacte).

## 7. Stratégie de tests (TDD, 100 % branche, par paquet)

- **`models`** : `target_id` bâti sur `absolute_number` (`S2E062A`) ; les trois numéros
  portés ; défaut `status="lost"`. Assertions `broadcast_date`/`aliases` **retirées**.
- **`interpolation`** : `{season}`/`{seasonal_number}`/`{absolute_number}` substitués et
  échappés ; **`{number}` → `InterpolationError`** ; tests `{date_alt}` /
  `date_alternation_pattern` / `FRENCH_MONTHS` / `MissingDateError` **supprimés**.
- **`validation`** : `parse_targets` exige les trois numéros (`ConfigError` sur chaque
  absence) ; unicité des `target_id` ; sonde à jour ; un matcher utilisant `{date_alt}`
  est désormais **rejeté au chargement** (remplace `test_regex_date_alt_..._via_probe`).
- **`resolver`/golden corpus** : noms de fichiers dans **les deux formes** atteignent le
  bon tier — absolu (`...n°62A...`) et saisonnal (`...S2E11...`, `...2x11...`) ; un nom
  bruité (`...11...` nu, langue étrangère) ne matche pas ; A et B distincts. Tests de
  neutralisation « règle datée sur cible sans date » **supprimés**.
- **Chargement de la config PROD réelle** : un test charge `deploy/config/crawler/targets.yml`
  et vérifie **180 cibles, target_id tous uniques** (91 S1 + 89 S2, 17 `found`), sans
  `ConfigError` (garde-fou data).
- **Tests crawler** : les constructions `TargetSegment(..., broadcast_date=…)` nettoyées
  (4 fichiers) ; `test_yaml_loader` ajusté pour ne plus dépendre d'une date (sans perdre
  la branche du loader qu'il couvre).
- **Gate** : forme inchangée — ruff `E,F,I,UP,B,SIM` LL100, `mypy --strict` sur
  `src`+`tests`, sqlfluff (pas de SQL touché), check_templates (pas de template touché).

## 8. Livrables & definition of done

1. `TargetSegment` étendu **et nettoyé** (3 numéros, plus de date/alias) ; `interpolation`
   (placeholders ajoutés, machinerie de dates supprimée) ; `validation` (`parse_targets`
   + sonde) ; `resolver` simplifié — paquet `matching` 100 % branche vert.
2. `segment_id` réécrit dans `deploy/config/crawler/matcher.yml` **et**
   `packages/matching/tests/fixtures/canonical_config.yaml` ; golden corpus enrichi des
   deux formes.
3. `deploy/config/crawler/targets.yml` peuplé des 180 segments (titres FR, statuts), se
   charge proprement (test data-guard).
4. Fixtures `canonical_targets.yaml`, `tests/smoke/targets.yml` migrées (sans date/alias).
5. Tous les tests dates/aliases supprimés ou nettoyés (matching + crawler) ; gate vert.
6. `CLAUDE.md` mis à jour : liste des placeholders d'interpolation et suppression de la
   mention « folded French months ».
7. Tag annoté `v0.21.0-targets-keroro` (non poussé) ; handoff rédigé.

## 9. Questions laissées au plan d'implémentation

- **Regex `segment_id` exacte** : casse (matche-t-on contre le nom brut sensible à la
  casse ? prévoir `S`/`s`, `x`/`X`), zéro-padding toléré, garde-fous de bord de chiffre
  côté RE2 (pas de lookaround → guards consommants `(?:^|[^0-9])` si besoin), et si le
  suffixe `{segment}` doit rester **obligatoire** (précision A/B) ou optionnel (recall).
- **Méthodo d'extraction des titres** (FAITE) : double extraction sonnet+opus du wikitext
  brut, résultats identiques. Reliquat historique : dispatch sous-agents par lots de saison +
  passe de **vérification croisée** contre Wikipedia (donnée hostile : accents, titres
  doubles, segments A/B à ne pas intervertir). Calcul programmatique de `absolute_number`.
- **Traitement des quirks data** (FAIT) : 26 épisodes mono-segment identifiés (S2E52 est
  mono) ; « Inédit » S1#11 et « Keroro special » S2#23 conservés ; cf. §6.
- **Ripple du renommage** `number` → `absolute_number` : `grep` des usages de
  `.number`/`number=` hors paquet `matching` (a priori aucun, à confirmer au plan).
- **`test_yaml_loader`** (crawler) : choisir un champ neutre pour conserver la couverture
  de la branche de parsing du loader sans réintroduire de date.
