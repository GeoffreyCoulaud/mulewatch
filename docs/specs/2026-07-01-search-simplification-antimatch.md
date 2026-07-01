# Spec — Simplification de la recherche + anti-match (veto) & garde media-shape

> Date : 2026-07-01. Branche : `feat/search-simplification-antimatch`.
> Statut : **à approuver** (phase Spec du workflow projet). Plan à suivre dans `docs/plans/`.

## 1. Contexte & motivation (le « pourquoi », qui rotterait dans le code)

La « prochaine étape » du handoff précédent (étendre `keywords.py` aux formes saisonnales
`S2E11A`/`2x11A`) a été **invalidée par la donnée réelle** au cours de la phase Discuss. Le fil
du raisonnement, à conserver :

1. **Matcher ≠ recherche.** Notre matcher (RE2) reconnaît une forme en **sous-chaîne** d'un
   nom de fichier. La recherche eD2k/Kad, elle, matche des **tokens entiers** (découpe sur les
   non-alphanumériques, ET booléen). Un mot-clé `s2e11a` ne trouverait que les fichiers portant
   littéralement ce token — inexistant en pratique. Transposer les formes du matcher en mots-clés
   de recherche n'a donc **aucun fondement**.

2. **La convention de nommage réelle est la capture TV TELETOON**, pas le fansub anime. Échantillon
   réel (unique, un épisode fraîchement retrouvé) :
   `[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi`
   Tokens eD2k : `tv keroro mission titar n 062a les demoiselles cambrioleuses dimanche 21 septembre 2008 16h50 sur teletoon avi`.
   La génération **actuelle** (`keroro` + `062a` + tokens de titre) couvre déjà ce fichier par
   trois angles. Les formes saisonnales n'y apparaissent pas.

3. **Le fichier est normalement ABSENT du réseau.** Mesures terrain de Geoffrey (aMule 3.0.0,
   Low-ID) : `keroro` en eD2k global = **96 résultats** (tous du bruit étranger) ; `keroro` en Kad
   = **223 résultats**, dont **1 gardé** après son anti-match — et ce 1 est un **faux positif**
   (`Srg Keroro - Opening 1.avi`). La cible réelle « d'habitude ne remonte pas » ; elle n'a été
   attrapée que parce qu'un utilisateur l'a partagée par hasard. **La recherche n'est donc pas un
   problème de rappel sous troncature — le fichier n'est pas là la plupart du temps.** La valeur du
   crawler est la **surveillance temporelle 24/7**, pas la largeur de recherche.

4. **Corollaire : les recherches ciblées par cible sont du poids mort.** 96 et 223 ne sont pas des
   plafonds → pas de troncature à la population courante → rien à « déterrer » avec des tokens
   rares. Et quand le fichier est là, il porte `keroro`, donc le filet large l'attrape.

5. **La seule troncature qui menace est le « jackpot »** : une source se connecte avec **toute la
   collection VF**. Là, `keroro` peut saturer, et pire, les fichiers VF (1 source) sont triés en
   bas → **tronqués en premier**. Le remède n'est **pas** l'explosion de 180 mots-clés, mais **un
   mot-clé sentinelle spécifique FR** : **`titar`** (propre au doublage français ; absent des
   versions ITA/ES/JP). Validé en réel : `titar` renvoie **1 seul fichier** sur tout le réseau
   (un Dino-Riders ES sans rapport) → token rare, **jamais saturable** → il remontera la collection
   VF intégralement au moment du jackpot.

6. **Puisque la recherche devient « bête », c'est le matcher qui porte toute la précision.** Audit
   de la policy réelle (`deploy/config/crawler/matcher.yml`) : le moteur déclaratif **n'est pas
   overkill**, il gagne son pain. L'anti-match qu'on voulait « construire » **existe déjà**
   (`foreign_lang` + `french_safe`, présent dans les 5 règles) ; le filtre format existe déjà
   (`is_video`) ; l'interpolation fait porter 180 cibles par 5 règles. Le seul matcher **non
   utilisé** est `attr_between` — précisément l'outil qu'il faut pour discriminer les **openings**
   (trop courts) par la **taille/durée**. Ce travail convertit donc du latent en utilisé.

**Garde-fou invariant réaffirmé** : le sujet du catalogue est le **fichier, jamais la personne**.
Aucune surveillance d'utilisateur (pas de browse des partages d'une source, pas de guet de
reconnexion), même si la piste « MiaoussMalicieux » le frôle.

## 2. Objectifs / non-objectifs

**Objectifs**
- Simplifier la recherche à **deux mots-clés sentinelles** issus de config : `keroro` (filet large,
  recall max, accepte le bruit) + `titar` (spécifique FR, immunité à la troncature du jackpot).
- **Enrichir l'anti-match** existant (`foreign_lang`) avec les tokens éprouvés du filtre aMule de
  Geoffrey, et garantir que le filtre **format** (`is_video`) garde bien les tiers **actionnables**.
- **Câbler `attr_between`** (taille) pour neutraliser les openings sur le vecteur de faux positif
  identifié (`numero_nu_mono`).

**Non-objectifs (YAGNI, décidés en Discuss)**
- Pas de score continu (0.0–1.0). Le tier discret `download > notify > catalog` **est** déjà un
  score ; ce qui manquait, c'était le veto — qui existe. Couple **veto + tiers** retenu.
- Pas de recherche ciblée par cible (segment_id/tokens de titre) : retirée.
- Pas de soupape anti-troncature auto-escaladée : documentée comme levier futur, **non construite**
  (la donnée dit qu'on n'en a pas besoin ; `titar` couvre le jackpot).
- Pas de refonte du moteur de matching : on ne touche ni à `engine.py`, ni à `validation.py`, ni au
  resolver. Uniquement de la **config** (policy YAML) + le câblage d'un matcher déjà écrit et testé.
- Aucun tracking de personne.

## 3. Périmètre technique

Trois lots. **A** = seul vrai changement de code (côté crawler). **B**/**C** = essentiellement de
la config policy + tests.

### Lot A — Recherche : `keywords.py` → config, 2 sentinelles

État actuel : `generate_keywords(targets)` émet `keroro` + par cible `062a` + tokens de titre.
Consommé une seule fois, en `application/run_search_cycle.py:169`. Le reste de la boucle
(`shuffle_for_cycle`, LIFO queue, workers, backoff, coverage) est **générique sur le nombre de
mots-clés** — 2 ou 200, indifférent. Aucune de ces mécaniques ne suppose « beaucoup » de mots-clés.

Changements :
- Nouveau champ de config `crawler.yml` : `search.keywords: [keroro, titar]` (résolu par l'adapter
  config, `${NAME}` interpolé avant le domaine — le domaine ne touche jamais l'env). **Défaut =
  `[keroro, titar]`**, extensible sans toucher au code (philosophie « policy en données »).
- `generate_keywords` : ne prend plus `targets`. Signature cible `generate_keywords(keywords:
  Sequence[str]) -> tuple[SearchKeyword, ...]` — conserve la **déduplication ordonnée** (premier vu
  gagne) et le tag d'origine (`origin="config"`, ou le texte lui-même). On garde une fonction fine
  (plutôt que de l'inliner) pour préserver le contrat testable dédup + `SearchKeyword`.
- `run_search_cycle` : reçoit les mots-clés de config (via l'app/composition) au lieu de dériver de
  `targets`. Si `targets` n'est plus utilisé ailleurs dans la fonction (à vérifier — a priori
  uniquement `generate_keywords`), on **retire le paramètre `targets`** de `run_search_cycle` et on
  fait remonter le changement jusqu'à la composition. Docstring `run_search_cycle.py:6`
  (« larges + ciblés ») à corriger.
- Retrait de `_segment_id_keyword`, `_MIN_TARGETED_TOKEN_LENGTH`, et de l'import `tokenize` /
  `TargetSegment` devenus morts dans `keywords.py`.

### Lot B — Anti-match : enrichir `foreign_lang`, garantir `is_video` sur les tiers actionnables

`RegexMatcher` matche sur `fold(filename)` (diacritiques retirés, casefold) et est **insensible à
la casse** par défaut → les tokens s'écrivent **en minuscules, sans accent**.

Filtre aMule de Geoffrey (le plus récent) à porter :
```
dino-riders|guerriero|risveglio|sarxento|sargento|benjo|FataColorata|català|\(ITA\)|\(J\)|\(JP\)|\(K\)|\(KR\)|\(KS\)|.nds|.rmvb|.cbz|.cbr|.pdf|signor|.torrent|.mp3
```

Répartition dans les **deux mécanismes existants** :

- **Langue / contenu → `foreign_lang`** (veto global, appliqué à tous les tiers via `french_safe`) :
  `dino-riders`, `guerriero`, `risveglio`, `sarxento`, `sargento`, `benjo`, `fatacolorata`,
  `catala`, `signor`, et les codes pays parenthésés `\((?:ita|j|jp|k|kr|ks)\)`. À fusionner avec
  l'alternation existante. Proposition (revue au §4) :
  ```yaml
  foreign_lang: { regex: "\b(ita|kor|korean|italiano|coreano|vostfr|vosta|subs?fr|espanol|english\s?dub|eng)\b|dino-riders|guerriero|risveglio|sarxento|sargento|benjo|fatacolorata|catala|signor|\((?:ita|j|jp|k|kr|ks)\)" }
  ```
- **Format → `is_video`** (déjà présent, allowlist `avi|mkv|mp4|mpg|ogm`). Les extensions
  `.nds/.rmvb/.cbz/.cbr/.pdf/.mp3/.torrent` sont **déjà exclues** (non-vidéo ; `.rmvb` par omission
  de l'allowlist). Le trou : `is_video` **n'est pas dans toutes les règles actionnables** —
  `teletoon_titre` (download) et `numero_titre` (notify) en manquent. **Correctif : ajouter
  `is_video` à ces deux règles.** Le tier `catalog` (`keroro_large`) reste **permissif sur le
  format** (invariant « cataloguer toute la métadonnée » : un `.mp3` Keroro reste un fichier observé
  digne du catalogue) mais garde le veto langue (via `french_safe` inchangé).

Effet net : la cible `dino-riders`+`titar` est **écartée de tous les tiers** (n'est même plus
cataloguée comme hit Keroro-large — correct, ce n'est pas du Keroro) ; les formats non-vidéo ne
peuvent plus déclencher download/notify.

### Lot C — Media-shape : neutraliser les openings via `attr_between` (taille)

Vecteur de faux positif identifié : `Srg Keroro - Opening 1.avi` est une **vidéo** (`is_video` vrai),
`french_safe` vrai, contient `keroro`, et « Opening **1** » fait matcher le **numéro nu** →
`segment_id_loose` → règle `numero_nu_mono` (tier **notify**). Discriminateur **robuste** d'un
opening : sa **taille** (un opening ~90 s pèse très peu vs ~12 min pour un segment). `size_mb` est
**disponible au candidat** (les résultats de recherche EC portent la taille — cf. CLAUDE.md /
`2026-06-11-ec-field-richness.md`).

Changements :
- Nouveau token `plausible_size: { attr_between: size_mb, min: <T> }` (borne haute laissée ouverte).
- **L'ajouter à la règle `numero_nu_mono`** (le vecteur réel). Optionnellement à `numero_titre`
  (§4). **Ne pas** l'ajouter aux règles download fortes (`id_segment_exact`, `teletoon_titre`) : un
  opening ne peut pas y matcher (il n'a ni `segment_id` exact, ni `teletoon`+titre), donc pas de
  vecteur — et les gater casserait le golden `test_evaluate_real_62a_is_download...` (dont le
  candidat n'a pas forcément de `size_mb`). Ciblage minimal = surface de régression minimale.
- **Seuil `<T>` À CALIBRER** sur la taille réelle du fichier retrouvé (input à demander à Geoffrey).
  Ordre de grandeur : un `.avi` SD 12 min (2008, DivX/XviD ~1100–1500 kb/s) pèse ~100–175 Mo ; un
  opening ~1,5 min ~10–20 Mo. Départ conservateur proposé : **`min: 25`** (Mo), à ajuster.

**Faisabilité confirmée** : `NetworkObservation.size_bytes` est un champ **obligatoire** (pas
`None`), et `to_candidate()` (`domain/observation.py:42`) peuple **toujours** `size_mb`. Donc dans
le vrai flux, la garde `attr_between(size_mb)` a toujours un attribut à évaluer — pas de blocage du
notify. Le cas « attribut absent → faux » ne survient qu'avec des candidats **construits à la main
en test** (qu'on maîtrise). Par ailleurs `duration_sec` est bien `None` au candidat (EC ne fournit
pas la durée sur les résultats de recherche — `2026-06-11-ec-field-richness.md`), ce qui **valide**
le choix « **taille** au candidat (toujours là), **durée** au verifier (post-download) ».

## 4. Points à trancher (revue avec Geoffrey)

1. **Liste exacte des tokens `foreign_lang`** : le §3 propose la fusion. Valider chaque token
   (notamment `signor` — sous-chaîne, matcherait « signore/signora » ITA, voulu ; `benjo` /
   `fatacolorata` — confirmer l'orthographe attendue, espace éventuel dans « fata colorata »).
2. **`is_video` sur `catalog`** : recommandation = **non** (rester permissif au catalogue). Confirmer.
3. **`plausible_size` sur `numero_titre`** en plus de `numero_nu_mono` : recommandation = **oui**
   (un « 062 + titre » sur un clip court est aussi douteux), sauf si on veut minimiser la surface.
4. **Seuil de taille `<T>`** : fournir la taille du fichier `N°062A` retrouvé pour calibrer ;
   défaut `25 Mo` sinon.
5. **Allowlist `is_video`** : ajouter `mpeg`/`divx`/`m4v` aux captures 2008 plausibles ? Défaut =
   inchangé (`avi|mkv|mp4|mpg|ogm`), conservateur.

## 5. Tests (TDD strict, 100 % branche par package)

- **Lot A** (`packages/crawler`) : réécrire `tests/domain/search/test_keywords.py` pour le nouveau
  contrat (config-sourced, dédup ordonnée, `keroro`+`titar`, plus de per-target). Adapter les tests
  de `run_search_cycle` si la signature perd `targets`. Vérifier que `cycle`/`coverage`/`backoff`
  restent verts (aucune hypothèse sur le nombre de mots-clés).
- **Lot B** (`packages/matching`) : ajouts au **golden corpus** (`tests/fixtures/golden_corpus.yaml`)
  — décoys `dino-riders`+`titar`, `guerriero`, `\(jp\)`, `signor` → **discard** (aucune règle) ; un
  vrai nom FR (l'échantillon réel) reste `download`. Tester `foreign_lang` sur les deux côtés de
  chaque token ajouté (branche coverage).
- **Lot C** (`packages/matching`) : golden avec `FileCandidate` portant `size_mb` — un « opening »
  sous le seuil **ne notifie pas**, un segment plausible au-dessus **notifie** ; les deux bornes de
  `attr_between` exercées. Vérifier que le harnais de golden construit bien `size_mb` (sinon
  l'étendre — item de plan).
- **Sync config** : `matcher.yml` est « copie synchronisée de `canonical_config.yaml` ». Toute
  modif de policy va dans **les trois** : `packages/matching/tests/fixtures/canonical_config.yaml`
  (source de vérité des tests), `deploy/config/crawler/matcher.yml`, `tests/smoke/matcher.yml`.
  De même `crawler.yml` (search.keywords) dans `deploy/config/crawler/` et `tests/smoke/`.

## 6. Invariants touchés / préservés

- **Clean/Hexagonal** : `keywords.py` reste PUR ; les mots-clés viennent de la config via l'adapter
  (le domaine ne lit pas l'env). Aucun I/O ajouté au domaine.
- **Policy 100 % en YAML** : tout l'anti-match et le media-shape sont de la **config**, pas du code.
  Le moteur reste un moteur fixe minimal.
- **Cataloguer toute la métadonnée** : le tier `catalog` reste permissif sur le format ; seul le
  veto **langue/contenu** (non-Keroro réel) écarte du catalogue.
- **PROD ne lit jamais les octets** : inchangé (le media-shape n'utilise que `size_mb` déclaré dans
  les résultats de recherche, pas une lecture d'octets).

## 7. NON validé contre vrai matériel (à porter au handoff final)

- **Recall du couple `keroro`+`titar`** sur une vraie apparition VF : non observé (le fichier est
  normalement absent). L'hypothèse « tout fichier VF porte `keroro` ou `titar` » repose sur n=1.
- **Immunité de troncature de `titar` au jackpot** : structurellement solide (token rare, 1 résultat
  réseau mesuré) mais jamais éprouvée sur une collection complète réelle.
- **Seuil de taille `<T>`** : calibré sur au mieux un échantillon ; à réviser à la première vraie
  apparition (openings vs segments réels).
