# Spec — Simplification de la recherche + refonte de la policy de matching

> Date : 2026-07-01. Branche : `feat/search-simplification-antimatch`.
> Statut : **approuvée** (phase Spec du workflow projet). Plan à suivre dans `docs/plans/`.

## 1. Contexte & motivation (le « pourquoi », qui rotterait dans le code)

La « prochaine étape » du handoff précédent (étendre `keywords.py` aux formes saisonnales
`S2E11A`/`2x11A`) a été **invalidée par la donnée réelle** en phase Discuss. Le fil du raisonnement,
à conserver :

1. **Matcher ≠ recherche.** Le matcher (RE2) reconnaît une forme en **sous-chaîne** d'un nom de
   fichier. La recherche eD2k/Kad matche des **tokens entiers** (découpe sur les non-alphanumériques,
   ET booléen). Un mot-clé `s2e11a` ne trouverait que les fichiers portant littéralement ce token —
   inexistant en pratique. Transposer les formes du matcher en mots-clés de recherche n'a aucun sens.

2. **La convention de nommage réelle est la capture TV**, pas le fansub. Échantillon réel unique :
   `[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi`.
   Tokens : `tv keroro mission titar n 062a les demoiselles cambrioleuses … teletoon avi`.

3. **Le fichier est normalement ABSENT du réseau.** Mesures terrain (aMule 3.0.0, Low-ID) : `keroro`
   eD2k global = **96** (tous bruit étranger) ; `keroro` Kad = **223**, dont **1 gardé** — et ce 1
   est un **faux positif** (`Srg Keroro - Opening 1.avi`). La cible « d'habitude ne remonte pas » ;
   elle n'a été attrapée que parce qu'un utilisateur l'a partagée par hasard. **La valeur du crawler
   est la surveillance temporelle 24/7, pas la largeur de recherche.**

4. **Corollaire : les recherches ciblées par cible sont du poids mort.** 96/223 ne sont pas des
   plafonds → pas de troncature à la population courante → rien à déterrer. Et quand le fichier est
   là, il porte `keroro`.

5. **La seule troncature qui menace est le « jackpot »** (une source se connecte avec toute la
   collection VF → `keroro` sature, et les fichiers VF à 1 source sont triés en bas → tronqués en
   premier). Remède : un **mot-clé sentinelle spécifique FR**, **`titar`** (propre au doublage
   français). Validé en réel : `titar` renvoie **1 seul fichier** sur tout le réseau (un Dino-Riders
   ES sans rapport) → token rare, **jamais saturable** → il remontera la collection VF au jackpot.

6. **La recherche devenant « bête », le matcher porte toute la précision.** Audit de la policy réelle :
   le moteur déclaratif **n'est pas overkill** — l'anti-match existait déjà (`foreign_lang`/
   `french_safe`), le filtre format aussi (`is_video`), l'interpolation fait porter 180 cibles par
   quelques règles. **Tout ce qu'on ajoute est de la config, pas du code moteur** — ce qui valide le
   DSL en temps réel.

7. **Découvertes de la phase Spec qui ont fait évoluer la policy :**
   - L'opening réel pèse **51,96 Mo** → un plancher de **taille** ne le sépare PAS proprement d'un
     segment 12 min compressé (~50-80 Mo) sans risquer d'exclure un vrai épisode. **La taille est
     abandonnée comme discriminateur.** On neutralise les clips par **nom** (`not_episode`) sur les
     tiers actionnables ; la **durée au verifier** (post-download) reste le discriminateur robuste.
     `attr_between` **reste donc latent** (contrairement à une hypothèse antérieure de cette spec).
   - **Archives** (`.zip/.rar/.7z`) : un uploader 2008 a pu zipper ses épisodes. On ne doit pas les
     rater, mais PROD ne lit pas les octets et le verifier ne valide pas une archive → **archive →
     notify** (revue humaine), jamais auto-download.
   - **Cohérence des règles** : plusieurs règles matchaient sur le titre/segment **sans exiger la
     franchise**, correct seulement grâce à la garantie *implicite* du searcher (il ne ramène que
     `keroro`/`titar`). Fragile et pas future-proof (le **webui** partage ce moteur et pourra
     rejouer le matcher sur des observations historiques non filtrées par la recherche). Corrigé par
     une **garde franchise universelle** (`is_keroro`) et un **préambule actionnable** (`is_episode`).
     Bonus : `id_segment_exact` exigeait `keroro` *spécifiquement* → un fichier trouvé via `titar`
     (sans le mot « keroro ») n'était que catalogué → **bug de rappel** corrigé par `keroro_titar`.
   - **Marqueurs de source FR** : `teletoon`, `idf1` (autre chaîne de diffusion), `vf` (`vf|vff|vfb`).
     Ce sont des **confirmeurs agnostiques à l'épisode** : ils **upgradent** une identification faible
     (notify → download), ils n'identifient jamais l'épisode seuls.

**Garde-fou invariant** : le sujet du catalogue est le **fichier, jamais la personne**. Aucune
surveillance d'utilisateur.

## 2. Objectifs / non-objectifs

**Objectifs**
- Réduire la recherche à **deux sentinelles issues de config** : `keroro` (large) + `titar` (FR,
  jackpot-proof).
- **Refondre la policy de matching** (config uniquement) : garde franchise universelle, anti-match
  enrichi, format à trois voies (vidéo/archive/reste), marqueurs de source comme upgradeurs,
  neutralisation des clips par nom, règles **découplées** (un identifiant = une règle).
- **Vérifier** que le pipeline de download ne skippe pas les épisodes déjà `found` (redondance
  d'archivage voulue).

**Non-objectifs (YAGNI)**
- Pas de score continu : le tier discret `download > notify > catalog` **est** le score.
- Pas de recherche ciblée par cible.
- **Pas de discriminateur par taille** (`attr_between` reste latent — l'opening 52 Mo l'a tué).
- Pas de soupape anti-troncature auto-escaladée (documentée comme levier futur, non construite).
- Pas de « notify franchise non-identifiée » (source mais zéro numéro/titre) — rare en pratique
  (les captures réelles portent le numéro), reporté si observé.
- **Aucune modification du moteur** (`engine.py`/`validation.py`/`resolver.py`/matchers). Tout est
  config + un seul vrai changement de code côté recherche (Lot A).
- Aucun tracking de personne.

## 3. Périmètre technique

### Lot A — Recherche : `keywords.py` → config, 2 sentinelles

`generate_keywords(targets)` (émettant `keroro` + per-target `062a` + tokens de titre) est consommé
une seule fois, `application/run_search_cycle.py:169`. Le reste de la boucle (`shuffle_for_cycle`,
LIFO queue, workers, backoff, coverage) est **générique sur le nombre de mots-clés**.

- Nouveau champ `crawler.yml` : `search.keywords: [keroro, titar]` (résolu par l'adapter config ;
  `${NAME}` interpolé avant le domaine). **Défaut = `[keroro, titar]`**, extensible sans code.
- `generate_keywords` : perd `targets` → `generate_keywords(keywords: Sequence[str]) ->
  tuple[SearchKeyword, ...]`, conserve la **dédup ordonnée** + le tag d'origine.
- `run_search_cycle` : reçoit les mots-clés de config (via l'app/composition). Si `targets` n'est
  plus utilisé ailleurs (a priori uniquement `generate_keywords`), **retirer le paramètre** et faire
  remonter jusqu'à la composition. Corriger la docstring `run_search_cycle.py:6` (« larges + ciblés »).
- Retirer `_segment_id_keyword`, `_MIN_TARGETED_TOKEN_LENGTH`, et les imports morts
  (`tokenize`, `TargetSegment`) de `keywords.py`.

### Lot B — Policy de matching (config uniquement)

`RegexMatcher` matche sur `fold(filename)` (diacritiques retirés, casefold), **insensible à la
casse** → tokens en **minuscules, sans accent**. Policy cible complète :

```yaml
tokens:
  keroro:        { keyword: keroro }
  titar:         { keyword: titar }
  keroro_titar:  { any: [keroro, titar] }
  foreign_lang:  { regex: "\b(ita|kor|korean|italiano|coreano|vostfr|vosta|subs?fr|espanol|english\s?dub|eng)\b|dino-riders|guerriero|risveglio|sarxento|sargento|benjo|fatacolorata|catala|signor|\((?:ita|j|jp|k|kr|ks)\)" }
  french_safe:   { not: foreign_lang }
  is_keroro:     { all: [french_safe, keroro_titar] }                 # garde franchise universelle : franchise ET non-étranger
  not_episode:   { regex: "opening|ending|g[eé]n[eé]rique|\bsample\b|preview|trailer|bande.?annonce" }
  is_episode:    { all: [is_keroro, { not: not_episode }] }           # préambule ACTIONNABLE : is_keroro ET pas un clip
  teletoon:      { regex: "t[eé]l[eé]toon" }
  idf1:          { regex: "\bidf\s?1\b" }
  vf:            { regex: "\b(?:vf|vff|vfb)\b|version\s?francaise" }   # VFF (France) / VFB (Belgique)
  source_marker: { any: [teletoon, idf1, vf] }                        # confirmeurs de source FR : upgradeurs, jamais requis
  segment_id:    { regex: "(?:n[°o]?\s*0*{absolute_number}|s0*{season}\s*e0*{seasonal_number}|0*{season}\s*x\s*0*{seasonal_number})\s*{segment}" }
  segment_id_loose: { regex: "{mono_gate}(?:^|[^0-9])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9]|$)" }
  title_hit:     { coverage: title, min: 0.6 }
  is_video:      { regex: "\.(avi|mkv|mp4|mpg|mpeg|divx|m4v|ogm)$" }
  is_archive:    { regex: "\.(zip|7z|rar|r\d\d|z\d\d|part\d+\.rar)$" }
rules:
  # DOWNLOAD — identifiant fort seul, ou identifiant faible confirmé par une source
  - { name: id_segment_exact,    tier: download, all: [is_episode, is_video, segment_id] }
  - { name: title_confirmed,     tier: download, all: [is_episode, is_video, title_hit, source_marker] }
  - { name: numero_nu_confirmed, tier: download, all: [is_episode, is_video, segment_id_loose, source_marker] }
  # NOTIFY — identifiant faible seul (revue humaine)
  - { name: title_review,        tier: notify,   all: [is_episode, is_video, title_hit] }
  - { name: numero_nu,           tier: notify,   all: [is_episode, is_video, segment_id_loose] }
  - { name: archive_candidate,   tier: notify,   all: [is_episode, is_archive, { any: [segment_id, title_hit, source_marker] }] }
  # CATALOG — tout le reste Keroro-ish (permissif : archives, mp3, openings… catalogués comme métadonnée)
  - { name: keroro_large,        tier: catalog,  all: [is_keroro] }
```

Principes structurants :
- **Deux natures de tokens** — *identifiants d'épisode* (spécifiques à la cible : `segment_id`,
  `segment_id_loose`, `title_hit`) vs *marqueurs de source* (agnostiques : `teletoon`, `idf1`, `vf`).
  Un marqueur seul ne peut porter aucune décision par-cible (il n'identifie pas l'épisode → le moteur
  attribuerait le plus petit `target_id` au hasard) → il ne fait qu'**upgrader**.
- **Motif régulier** : *identifiant faible → notify* ; *identifiant faible + `source_marker` →
  download*, décliné pour le titre et le numéro nu. Toute règle actionnable = `[is_episode, <format>,
  <preuve>]`.
- **Format à trois voies** : `is_video` → tiers actionnables ; `is_archive` → notify (revue) ; le
  reste (mp3, pdf, nds…) → seulement `keroro_large` (catalogue permissif, invariant « cataloguer
  toute la métadonnée »).
- **`title_hit` référencé nu** (défaut 0.6) partout — pas de surcharge `{ token: …, min: … }`
  redondante. Si un jour l'archive doit être plus permissive → **second token nommé**
  `title_hit_loose: { coverage: title, min: 0.5 }`, pas de surcharge inline.
- `not_episode` **généralisé** via `is_episode` (toutes les règles actionnables, jamais le catalogue).

**Sync config obligatoire** : `matcher.yml` est « copie synchronisée de `canonical_config.yaml` ».
Toute modif va dans **les trois** : `packages/matching/tests/fixtures/canonical_config.yaml` (source
de vérité des tests), `deploy/config/crawler/matcher.yml`, `tests/smoke/matcher.yml`. Idem
`crawler.yml` (`search.keywords`) sous `deploy/config/crawler/` et `tests/smoke/`.

### Lot C — Pipeline download : ne pas skipper les `found`

Le **matcher est agnostique au statut** (il ne lit jamais `found`/`lost`). Un épisode identifiable
part en download quel que soit son statut. Vérifier que `application/run_download_cycle.py` (ou la
policy download) **ne filtre pas** les cibles déjà `found`/déjà téléchargées ; si un tel skip existe,
le **relâcher** (redondance d'archivage voulue — « au pire on re-télécharge », de toute façon absent
du réseau en temps normal). Item de plan (lecture + décision).

## 4. Points tranchés (décisions actées)

- **Recherche** = `keroro` + `titar` (config, défaut). Ciblés par cible retirés.
- **Archive → notify** (pas download : le verifier ne valide pas une archive).
- **`vf`/`idf1`/`teletoon`** = `source_marker`, **upgradeurs** (notify → download), jamais requis.
  `vf` = `vf|vff|vfb` (pas de vfq — inexistant pour Keroro).
- **Clips** neutralisés par **nom** (`not_episode`), pas par taille. Liste :
  `opening|ending|générique|sample|preview|trailer|bande annonce`.
- **`is_video`** += `mpeg|divx|m4v`.
- **`title_hit`** : seuil unique 0.6, référencé nu.
- **Garde franchise universelle** `is_keroro` + **préambule actionnable** `is_episode`.

**Résiduels tunables (pas bloquants)** : seuils de titre (0.6), contenu de `not_episode` et
`foreign_lang`, allowlist `is_video`/`is_archive` — ajustables en config à mesure des observations
réelles (c'est tout l'intérêt du DSL).

## 5. Tests (TDD strict, 100 % branche par package)

- **Lot A** (`packages/crawler`) : réécrire `tests/domain/search/test_keywords.py` (contrat
  config-sourced, dédup ordonnée, `keroro`+`titar`, plus de per-target). Adapter les tests de
  `run_search_cycle` si la signature perd `targets`. Vérifier `cycle`/`coverage`/`backoff` verts.
- **Lot B** (`packages/matching`) : golden corpus (`tests/fixtures/golden_corpus.yaml`) —
  - décoys **discard** : `dino-riders … titar`, `guerriero`, `\(jp\)`, `signor`, `… .mp3`, `… .pdf` ;
  - **opening** (`… opening ….avi`) → **catalog** (pas notify, via `not_episode`) ;
  - **archive** (`Keroro 062A.rar`) → **notify** (`archive_candidate`) ;
  - **upgrade** : `Keroro … 11 … teletoon.avi` (numéro nu + source) → **download** ; le même sans
    source → **notify** ; idem titre proche ± `source_marker` ;
  - **bug de rappel** : `Mission Titar N°062A.avi` (titar sans « keroro ») → **download** ;
  - exercer **les deux côtés** de chaque token/branche ajouté (couverture branche).
- **Sync** : répercuter la policy dans les trois fichiers (cf. §3) ; le golden existant
  `test_evaluate_real_62a_is_download…` doit rester **download** via `id_segment_exact`.

## 6. Invariants touchés / préservés

- **Clean/Hexagonal** : `keywords.py` reste PUR ; mots-clés via l'adapter config (le domaine ne lit
  pas l'env). Aucun I/O ajouté.
- **Policy 100 % en YAML** : tout l'anti-match, les marqueurs, le format et la neutralisation des
  clips sont de la **config**. Le moteur reste fixe et minimal.
- **Cataloguer toute la métadonnée** : le tier `catalog` reste permissif (format, openings) ; seul
  le veto **langue/contenu** (`foreign_lang`, non-Keroro réel) écarte du catalogue.
- **PROD ne lit jamais les octets** : inchangé (aucune lecture d'octets ajoutée ; la discrimination
  fine de durée reste au verifier post-download).

## 7. NON validé contre vrai matériel

- **Recall du couple `keroro`+`titar`** sur une vraie apparition VF : non observé (fichier absent).
  Hypothèse « tout fichier VF porte `keroro` ou `titar` » sur n=1.
- **Immunité de troncature de `titar` au jackpot** : structurellement solide (1 résultat réseau
  mesuré), jamais éprouvée sur une collection complète réelle.
- **Précision des règles découplées** (title/numéro nu ± `source_marker`, archives, `not_episode`) :
  exercée par le golden, pas sur de vrais flux eD2k. Seuils à réviser à la première vraie apparition.
- **Marqueurs `idf1`/`vff`/`vfb`** : présumés présents dans certains noms réels, non confirmés.
