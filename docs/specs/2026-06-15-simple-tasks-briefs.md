# Briefs — tâches simple/additif (Vague 1)

> Briefs **courts** (pas des design docs) pour les worktrees `WT-docs`, `WT-crawler-cli`,
> `WT-crawler-app`. Chaque brief = critères d'acceptation + pointeurs, pas de design. Règles dures du
> repo (TDD, 100 % branch, mypy strict, ruff, sqlfluff) et protocole de dispatch : voir
> `2026-06-15-backlog-parallelization-design.md` §6. Fichiers intégration-owned (compose, lock,
> CLAUDE.md…) : §4 — **delta proposé, pas édité**.

---

## WT-docs (1 agent — autonome, texte ; ne touche AUCUN code ni la structure compose)

Trois sous-tâches séquentielles dans le même worktree (elles se recoupent sur runbook/.env.example).

### D1. Déspécifier ProtonVPN
- **Contexte** : le projet n'exige PAS ProtonVPN ; le besoin = un provider qui accepte la redirection
  de port. **Nuance exacte à respecter** (acquis du spike gluetun) : le PF gluetun n'existe QUE pour
  **ProtonVPN, PIA, PrivateVPN, PerfectPrivacy** (`vendor/gluetun/internal/configuration/settings/portforward.go:68-76`).
  Pour tout autre provider → **Low-ID** (ou ouvrir un port chez soi). Ne PAS écrire « n'importe quel
  provider » : écrire « un des 4 providers PF supportés par gluetun, sinon Low-ID ».
- **Acceptation** : `grep -rni protonvpn` sur `docs/`, `runbook*`, `.env.example`, commentaires compose
  ne laisse plus aucune mention qui présente ProtonVPN comme *requis* ; chaque occurrence reformulée en
  « provider avec port forwarding (Proton/PIA/PrivateVPN/PerfectPrivacy) ou Low-ID/port ouvert ». La
  mémoire projet est déjà corrigée (ne pas y toucher).
- **Pointeurs** : grep d'abord pour lister les sites. `.env.example` est un fichier doc (éditable) ; la
  *structure* compose (services/env) est intégration-owned (proposer un delta de commentaire si besoin).

### D2. Réécrire le runbook pour un public moyennement technique
- **Contexte** : le runbook actuel (`docs/runbook-deployment.md`) a trop de jargon de second plan.
- **Acceptation** : prérequis simples listés, **étapes numérotées**, **glossaire minimal**, section « ce
  qu'on peut ignorer ». Ajouter explicitement : (a) la **dépendance au pin `ngosang/amule:3.0.0-1`**
  (ne jamais dériver vers `latest`/`2.3.3-*` — seul ≥3.0.0 auto-amorce `server.met`/`nodes.dat`) ; (b)
  le fait que l'**amorçage est automatique au 1er run** mais **dépend de l'egress-au-boot** (DNS+443
  via le VPN) ; (c) le mode **Low-ID** comme état normal tant que le port-sync n'est pas en place.
- **Pointeurs** : `docs/runbook-deployment.md`, `.env.example`, acquis §3 du plan maître.

### D3. Enrichir le doc richesse EC
- **Acceptation** (suggestions du spike, à appliquer) : ajouter `EC_TAG_SEARCH_PARENT` et
  `EC_TAG_KNOWNFILE_RATING` au tableau principal ; noter le comportement `EC_DETAIL_UPDATE` (seuls
  source_count/source_count_xfer/status émis) ; ajouter une ligne « container/media_type = — » distincte
  de `file_type` ; ajouter une phrase « tag de résultat identique eD2k/Kad, seule divergence EC =
  sémantique de `EC_TAG_SEARCH_STATUS` » ; épingler le commit/tag amont relu.
- **Pointeurs** : `docs/reference/2026-06-11-ec-field-richness.md`, `docs/reference/ec-protocol.md`,
  `packages/crawler/src/emule_indexer/adapters/mule_ec/codes.py`.

---

## WT-crawler-cli (1 agent — autonome ; run probe = Geoffrey)

### C1. Sous-commande `validate-config`
- **Acceptation** : une commande CLI qui charge la config YAML (matcher + targets) via le loader et la
  validation **existants** et rapporte les erreurs proprement (message clair + **code de sortie ≠ 0** si
  invalide, 0 si OK). N'ajoute PAS de logique de validation nouvelle — réutilise
  `adapters/config/yaml_loader.py` + `domain/matching/validation.py`. TDD : config valide → exit 0 ;
  config cassée (chaque type d'erreur testable) → exit ≠ 0 + message. 100 % branch.
- **Pointeurs** : le module CLI existant (`python -m emule_indexer` / `composition/`), `adapters/config/`,
  `domain/matching/validation.py` (`ConfigError` & co).

### C2. Prép. probe richesse EC (le run réel est à Geoffrey)
- **Acceptation** : étendre `packages/crawler/src/emule_indexer/adapters/mule_ec/tools/ec_probe.py` (ou
  équivalent) d'une option qui **dump TOUS les tags `raw`** des résultats d'une recherche réelle (pour
  mesurer le taux de remplissage empirique). Le CODE est testable (parsing/format) ; le **run** contre un
  vrai amuled est à Geoffrey. Sortie lisible (un tag par ligne avec id/type/valeur).
- **Pointeurs** : `adapters/mule_ec/tools/ec_probe.py`, le mapper `mapping.py` (capture-all `raw_meta`).

---

## WT-crawler-app (1 agent — autonome unit/mutation ; `orchestration_integration` = Geoffrey)

### A1. I2 — granularité d'erreur par-étape dans `run_download_cycle`
- **Contexte** : famine théorique — une erreur dans une étape ne doit pas affamer l'autre.
- **Acceptation** : séparer les try/except de `_handle_completions` et `_queue_new_candidates` (chacune
  isolée), de sorte qu'un échec d'une étape n'empêche pas l'autre de tourner ; backoff/tolérance
  préservés. TDD : test où l'étape A lève → l'étape B s'exécute quand même (et inversement). 100 % branch.
- **Pointeurs** : `packages/crawler/src/emule_indexer/application/run_download_cycle.py` (chercher
  `_handle_completions` / `_queue_new_candidates`).

### A2. T12 — couverture d'arrêt en intégration
- **Contexte** : promis en D-verify, jamais ajouté. Pas de tâche en fuite après shutdown.
- **Acceptation** : ajouter le guard `if not task.done()` au point d'annulation des tâches du `TaskGroup`,
  + un **test de mutation** qui prouve qu'aucune tâche ne survit après shutdown (`asyncio.all_tasks`).
  La part « vrai amuled » est `orchestration_integration` (Geoffrey) ; le test unitaire/mutation du guard
  est autonome. 100 % branch.
- **Pointeurs** : `composition/` (le `TaskGroup` de `CrawlerApp`, la séquence d'arrêt), les tests
  d'arrêt existants.
