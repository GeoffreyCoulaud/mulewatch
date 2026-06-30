# Handoff — Targets Keroro, double numérotation & nettoyage (v0.21.0-targets-keroro)

> Branche `feat/targets-keroro-dual-numbering`. Spec `docs/specs/2026-06-30-targets-keroro-dual-numbering.md`,
> plan `docs/plans/2026-06-30-targets-keroro-dual-numbering.md`. Revue finale de branche : **APPROVE / merge-ready**.

## État courant

Le moteur de matching (`packages/matching/`) et le catalogue de cibles ont été refondus :

- **`TargetSegment`** = `season`, `seasonal_number`, `absolute_number`, `segment`, `title`,
  `status` (par segment, défaut `lost`), `sole_segment` (dérivé). `target_id` =
  `S{season}E{absolute_number:03d}{LETTER}` (ex. `S2E062A`). **Double numérotation** :
  absolu (continu 1–103) **et** saisonnal (par saison).
- **Supprimé** : `broadcast_date` + placeholder `{date_alt}` + toute la machinerie (`FRENCH_MONTHS`,
  `date_alternation_pattern`, `MissingDateError`, le `try/except` du resolver) ; et le champ
  mort `aliases`. Whitelist d'interpolation = `{season} {seasonal_number} {absolute_number}
  {segment} {title}` (+ `{mono_gate}`).
- **`segment_id` (strict)** matche l'absolu (`N°062A`) **et** le saisonnal (`S2E11A`, `2x11A`),
  lettre A/B **obligatoire** → alimente `id_segment_exact` (download) et `numero_titre` (notify).
- **`segment_id_loose` (B-safe)** : numéro **nu** (`Keroro 11`), **cibles mono uniquement**
  (neutralisé sur les bi-segment par `{mono_gate}` → `[^\s\S]` never-match), gardes de bord
  chiffre. Consommé par la **nouvelle règle `numero_nu_mono` au tier `notify`** : un fichier
  mono à numéro nu **remonte pour revue, jamais en download**.
- **Catalogue PROD** `deploy/config/crawler/targets.yml` : **180 segments** réels S1+S2
  (S1=91, S2=89), titres FR verbatim de Wikipédia. **26 épisodes mono-segment**. **17 `found`**.
- **Gate complet vert** : matching 191 / crawler 726 / verifier 176 / webui 97 (tous 100 %
  branche) ; ruff, ruff format, mypy --strict, sqlfluff, check_templates clean.

## Ce qui a été construit (4 tâches, subagent-driven + revue par tâche + revue finale)

1. `refactor(matching)` — renommage `number`→`absolute_number`, ajout `seasonal_number`,
   suppression dates/aliases (16 fichiers, atomique). Le brief avait raté un caller PROD
   (`keywords.py` lisait `target.number`) — corrigé dans le même commit.
2. `feat(matching)` — `segment_id` dual-forme + golden corpus (formes saisonnales + decoys).
3. `feat(data)` — catalogue 180 segments. **Extraction par double passe sonnet+opus du
   wikitext brut, résultats identiques**, puis génération déterministe (script jetable).
4. `feat(matching)` — `segment_id_loose` mono notify-only (B-safe).

## Pièges appris

- **La liste FR Wikipédia n'est PAS un A/B uniforme** : 26 épisodes mono-segment (un seul
  titre). La spec supposait « 206 / 2 segments par épisode » ; le réel est **180**. Toujours
  vérifier la structure réelle de la donnée avant de figer un compte.
- **WebFetch (petit modèle) tronque/réordonne les gros tableaux** (100+ items, lignes sautées,
  décalage de numérotation). Pour une donnée fiable : récupérer le **wikitext brut**
  (`?action=raw`) + parsing déterministe, et **recouper avec un second modèle**.
- **Renommer un champ de modèle** (`number`→`absolute_number`) : `grep` les usages
  (`.number`/`number=`) dans **tous** les paquets — un caller PROD (`keywords.py`) avait été
  oublié de la cartographie initiale.
- **`status` est par segment** (pas par épisode) — seule façon d'exprimer « A retrouvé, B perdu ».
- **L'ordre A/B de Wikipédia peut diverger d'autres sources.** Les `found` ont été **validés
  contre la playlist YouTube « keroro vf archive »**
  (`PLz_-a6hzupnXpM0vcCVeIvI-N9x0uAmi8`, listée via `uvx yt-dlp --flat-playlist`). Le `found`
  est keyé par **titre** (non ambigu), pas par lettre : pour S01E27 et S01E36 le demi récupéré
  est le segment **B** dans notre ordre Wikipédia (« Station thermale à gogo ! », « La station
  de ski privée des Bellair »), alors que la chaîne les numérote « A ». On garde l'ordre
  Wikipédia ; seul le titre récupéré détermine quel segment est `found`.
- **Idiome never-match RE2 `[^\s\S]`** : `{mono_gate}` neutralise un token par cible (rejoue le
  motif de l'ancienne neutralisation `MissingDateError`, désormais retirée avec sa cause).
- **Ne pas ajouter de cible mono à `canonical_targets.yaml`** : ça déplace le départage catalog
  et casse le golden `keroro_only_catalog_tiebreak`. Tester le mono via configs inline dédiées.

## NON validé contre vrai matériel / vraie donnée

- **Recall/precision réels** des formes **saisonnales** (`S2E11A`/`2x11A`) et du **numéro-nu mono**
  sur de vrais noms de fichiers eD2k — non vérifié en prod. Le golden corpus n'exerce que la
  saison 2 / l'épisode canonique.
- **Précision de la branche NxM pour les autres saisons** non exercée par le golden corpus
  (atténuée par `{segment}` obligatoire + garde de bord).
- **Ambiguïté cross-saison du numéro nu** : un `22` nu matche `S1E022A` (absolu) ET `S2E073A`
  (saisonnal) → départage déterministe au plus petit `target_id`. **Par design routé `notify`**.
- **« Inédit » (S1#11)** conservé comme titre placeholder — potentiellement bruyant (« inédit »
  est un mot P2P courant) ; à durcir si observé.

## Prochaine étape suggérée

**Étendre la génération de mots-clés de recherche** (`packages/crawler/src/emule_indexer/domain/
search/keywords.py`) : aujourd'hui elle n'émet que la forme **absolue** `062a`. Le matcher
reconnaît désormais aussi le saisonnal (`S2E11A`/`2x11A`) et le numéro-nu mono, mais le crawler
ne **cherche** pas activement ces formes (elles ne remontent que via les mots-clés larges/titre).
Combler ce **gap de cohérence recherche↔matching** est le candidat naturel pour le prochain
jalon. En parallèle : valider le recall sur un échantillon réel eD2k.

## Reliquats Minor (revue finale, acceptés)

- `test_engine.py::_TARGET_MONO` a `absolute==seasonal` (10/10) → les tests de routing
  n'exercent pas spécifiquement l'alternance saisonnale du loose (couverte ailleurs). Un test
  avec une vraie cible mono S2 (ex. absolu 71 / saisonnal 20) la fermerait à peu de frais.
- Lignes `segment_id`/`segment_id_loose` ~150 char en YAML (non lintées) — cosmétique.
