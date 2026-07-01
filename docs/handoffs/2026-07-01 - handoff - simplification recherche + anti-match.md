# Handoff — Simplification recherche (keroro+titar) + refonte policy matching

> Branche `feat/search-simplification-antimatch` (worktree). Spec
> `docs/specs/2026-07-01-search-simplification-antimatch.md`, plan
> `docs/plans/2026-07-01-search-simplification-antimatch.md`. Exécution subagent-driven
> (5 tâches + revue par tâche + revue finale whole-branch **opus : « Ready to merge: Yes »**).
> **Intégration : PR ouverte** (pas de merge local). **Tag `v0.22.0-search-antimatch` à poser
> APRÈS merge de la PR** (les tags projet sont locaux, non poussés, un par subsystème).

## État courant

Deux changements, gate complet vert (matching 203 / crawler 730 / verifier 176 / webui 97,
tous 100 % branche ; ruff, ruff format, mypy --strict, sqlfluff, check_templates clean) :

1. **Recherche simplifiée** (`packages/crawler/`) : la recherche eD2k/Kad n'émet plus que
   **deux sentinelles issues de config** — `keroro` (filet large) + `titar` (spécifique FR,
   jackpot-proof). Nouveau champ `crawler.yml` `search.keywords: [keroro, titar]`
   (`CrawlerConfig.search_keywords`, défaut `("keroro", "titar")`). `generate_keywords(targets)`
   → `generate_keywords(keywords: Sequence[str])` ; `run_search_cycle` perd le paramètre
   `targets`. **Toute la génération de mots-clés par cible (segment_id `062a` + tokens de titre)
   est retirée.** `self._targets` reste utilisé pour le matching + le download.

2. **Refonte de la policy de matching** (`packages/matching/`, **config uniquement — moteur
   intouché**, vérifié `git diff -- packages/matching/src/` vide) :
   - **Garde franchise universelle** `is_keroro = french_safe ∧ keroro_titar` + **préambule
     actionnable** `is_episode = is_keroro ∧ ¬not_episode`, en tête de **chaque** règle
     actionnable ; le tier `catalog` (`keroro_large`) reste sur `is_keroro` (permissif).
   - **Anti-match `foreign_lang` enrichi** du filtre aMule éprouvé de Geoffrey (`dino-riders`,
     `guerriero`, `signor`, codes pays `\(ita|j|jp|k|kr|ks\)`, …). Comme `foreign_lang` est
     **dans** `is_keroro`, un fichier veto-é est **écarté** (pas juste catalogué).
   - **Format à trois voies** : `is_video` (élargi `mpeg|divx|m4v`) → tiers actionnables ;
     nouveau `is_archive` → `archive_candidate` (**notify**, revue humaine) ; reste (mp3, pdf…)
     → seulement `keroro_large` (catalogue permissif, invariant « cataloguer toute la métadonnée »).
   - **Marqueurs de source** `teletoon`/`idf1`/`vf` (= `vf|vff|vfb`) factorisés en `source_marker`
     — **upgradeurs** de tier (identifiant faible → download), jamais requis, jamais identifiants
     d'épisode seuls.
   - **Règles découplées** : un identifiant = une règle (numéro exact / titre / numéro nu),
     avec le motif régulier *faible → notify ; faible + source_marker → download*.
     `not_episode` (opening/ending/sample…) neutralise les clips **par nom** sur les tiers
     actionnables (jamais le catalogue).
   - Policy synchronisée dans **4 copies** : `canonical_config.yaml` (source), `deploy/config/
     crawler/matcher.yml`, `tests/smoke/matcher.yml`, et le dict inline `_CANONICAL_RAW` de
     `test_engine.py` (revue finale : les 4 confirmées cohérentes).

3. **Lot C — régression `found` se re-télécharge** : aucun changement de code. `download_policy`
   ne skippe que `target_status == "complete"`, jamais posé en PROD (`found`/`lost`) → un épisode
   déjà trouvé se re-télécharge quand un **nouveau hash** le matche (seul le hash identique est
   dédupliqué). Verrouillé par un test.

## Ce qui a été construit (5 tâches, subagent-driven + revue par tâche + revue finale)

1. `feat(config)` — champ `search.keywords` (additif). 2. `refactor(search)` — recherche →
config, retrait du per-target (16 sites de test balayés). 3. `feat(matching)` — policy refondue
+ golden (2 cas changés, 10 nouveaux). 4. `test(matching)` — tests inline engine alignés +
routage mono découplé. 5. `test(download)` — régression `found`. + un commit de nettoyage
post-revue (noms de test à jour, `assert target_id`, id golden). Revue finale **opus** :
tous les cas golden/inline/mono tracés à la main, cohérence recherche↔matching et sync 4-copies
vérifiées.

## Pièges appris (le raisonnement qui a mené là — creusé en phase Discuss avec la donnée réelle)

- **Matcher ≠ recherche.** Le matcher RE2 reconnaît une forme en **sous-chaîne** ; la recherche
  eD2k/Kad matche des **tokens entiers**. Transposer les formes du matcher (`s2e11a`…) en
  mots-clés était donc sans fondement — piste du handoff précédent **invalidée par la donnée réelle**.
- **Le fichier lost est normalement ABSENT du réseau** (mesures terrain : `keroro` = 96 eD2k /
  223 Kad, que du bruit ; la cible n'y est pas, elle « clignote » quand un détenteur rare partage).
  La valeur du crawler est la **surveillance temporelle 24/7**, pas la largeur de recherche.
  Les recherches ciblées par cible étaient donc du **poids mort**.
- **`titar` est une sentinelle FR jackpot-proof** : validé en réel, 1 seul résultat réseau (un
  Dino-Riders ES, écarté par le matcher). Token rare → jamais saturable → remontera la collection
  VF au « jackpot » (une source avec toute la collection, seul cas où le filet large tronquerait).
- **La taille ne discrimine PAS les openings** : l'opening réel pèse **51,96 Mo**, chevauchant un
  segment 12 min compressé. Donc **pas de plancher de taille** (`attr_between` reste latent) ;
  clips neutralisés **par nom** (`not_episode`), durée robuste au **verifier** post-download.
- **Un identifiant d'épisode est spécifique à la cible** (`segment_id`, `title_hit`), un
  **marqueur de source** ne l'est pas (`teletoon`/`idf1`/`vf`) — un marqueur seul ne peut porter
  aucune décision par-cible (le moteur attribuerait le plus petit `target_id` au hasard) → il ne
  peut qu'**upgrader**.
- **Bug de rappel corrigé** : l'ancien `id_segment_exact` exigeait `keroro` *spécifiquement* ; un
  fichier trouvé via `titar` (sans le mot « keroro ») n'était que catalogué. La garde
  `keroro_titar` (via `is_keroro`) le fait maintenant partir en download.
- **Le matcher est un DSL qui gagne son pain** : tout l'anti-match, les marqueurs, le format et la
  neutralisation des clips sont de la **config**, moteur intouché. La question « le DSL est-il
  overkill ? » a été tranchée par l'audit : non — chaque évolution est une édition de données
  validée fail-fast, pas du code.
- **RegexMatcher folde le filename** (diacritiques retirés, casefold) et est insensible à la casse
  → tokens en minuscules sans accent ; les alternations existantes en MAJ sont indifférentes.
- **Sync 4 copies obligatoire** pour toute modif de policy (sinon PROD tourne une policy non testée).

## NON validé contre vrai matériel / vraie donnée

- **Recall du couple `keroro`+`titar`** sur une vraie apparition VF : non observé (fichier absent).
  Hypothèse « tout fichier VF porte `keroro` ou `titar` » sur **n=1**.
- **Immunité de troncature de `titar` au jackpot** : structurellement solide (1 résultat réseau
  mesuré), jamais éprouvée sur une collection complète réelle.
- **⚠️ Direction PERTE DE DONNÉES (T3, revue finale)** : les tokens `foreign_lang` non ancrés
  (`catala`/`signor`/`benjo`/`guerriero`…) matchent en **sous-chaîne** → un faux positif **écarte**
  un fichier. Portés verbatim du filtre aMule éprouvé de Geoffrey (voulu), mais c'est le **premier
  cran à desserrer** si un vrai fichier VF est un jour écarté à tort. À surveiller.
- **Marqueurs `idf1`/`vff`/`vfb`** : présumés présents dans certains noms réels, non confirmés.
- **Précision des règles découplées** (title/numéro nu ± `source_marker`, archives, `not_episode`) :
  exercée par le golden/inline, pas sur de vrais flux eD2k. Seuils à réviser à la 1re vraie apparition.

## Prochaine étape suggérée

Le système est maintenant **aligné sur le réel** (recherche minimale + précision dans le matcher).
La prochaine valeur est **opérationnelle, pas de code** : **valider le recall sur une vraie
apparition VF** (surveiller que `keroro`/`titar` la remontent et que la décision est correcte),
et **tuner `foreign_lang`/`not_episode`/seuils de titre en config** au fil des observations (c'est
tout l'intérêt du DSL). Levier futur documenté, non construit (YAGNI) : « notify franchise
non-identifiée » (source FR sans numéro ni titre) si ce cas se présente ; soupape anti-troncature
auto-escaladée si un large sature un jour.
