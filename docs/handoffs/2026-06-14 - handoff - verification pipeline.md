# Handoff — emule-indexer (pipeline de vérification, D-verify — clôt le jalon « Plan D »)

> Continuation guide. Le plus récent des handoffs = point d'entrée. Lis aussi le handoff
> précédent (`2026-06-13 - handoff - download capability.md`) pour le contexte D-download
> dont D-verify dépend, et la spec `docs/superpowers/specs/2026-06-13-verification-pipeline-design.md`.

## 1. TL;DR

La **boucle full-mode est FERMÉE de bout en bout**. Le dépôt est désormais un **workspace uv
VIRTUEL** : `packages/crawler/` (paquet `emule_indexer`, dist `emule-indexer`) + `packages/verifier/`
(paquet `download_verifier`, dist `download-verifier`), séparés, ne partageant que **le contrat de
fil JSON**.

Chaîne : le download enfile (`enqueue_verification`) → la **boucle de vérification**
(`application/run_verification_cycle.py` : `run_verification_cycle` + `verification_loop`) consomme
la file durable `verification_tasks` (`reclaim`→`claim`→`verify`→`record`→`complete`) → appelle le
**verifier** par RPC (`HttpContentVerifier`, httpx) → écrit `file_verifications` (append-only). Le
**verifier** (`download_verifier`) est un service **Starlette** (`POST /verify` + `GET /health`) dont
la logique `check.verify_file` est **NO-OP** (un `stat` RO → `unverified` ; il ne lit JAMAIS les
octets, ignore `expected`). `CrawlerApp` câble **LIVE les DEUX boucles** (download + vérification) en
**mode full** (`verifier_url` présent → `health()` fail-fast au démarrage) ; **absent → mode
observateur** (les deux boucles OFF, comportement Plan C strictement inchangé). E2e
`verify_integration` (RPC réel in-process via httpx `ASGITransport`, **sans Docker**) VERT.

**Jalon « Plan D » (auto-download + vérification) COMPLET, taggé `v0.8.0-auto-download`** (annoté,
non poussé). Le verifier reste **NO-OP** : le vrai travail d'analyse est **D-analysis** (§3).

## 2. État vérifiable

Gate **PAR PAQUET** (le `uv run pytest` nu depuis la racine est neutralisé par un `conftest.py`
racine — voir CLAUDE.md « Commands ») :

```bash
( cd packages/crawler  && uv run pytest -q )   # 680 passed, 7 deselected — 100.00% branch
( cd packages/verifier && uv run pytest -q )   # 18 passed — 100.00% branch
uv run ruff check . && uv run ruff format --check . && uv run mypy   # racine, spannent les 2 paquets
uv run sqlfluff lint packages/crawler/src                            # crawler seul (verifier sans SQL)
( cd packages/crawler && uv run pytest -m verify_integration --no-cov -q )   # 1 passed (sans Docker)
```

Tag : `git tag --list | grep auto-download` → `v0.8.0-auto-download` (NON poussé).

## 3. Contrats que D-analysis doit respecter / brancher

1. **Remplacer le NO-OP** — `packages/verifier/src/download_verifier/check.py::verify_file` doit
   lancer le VRAI pipeline (type_sniff → ffprobe → clamav, agrégation worst-status) dans un **enfant
   jetable** (`net=none` / rlimits / timeout / RO / non-root), remplir `real_meta` (durée/bitrate/codec
   — le trou qu'EC ne comble pas, cf. `docs/reference/2026-06-11-ec-field-richness.md`) et rendre des
   verdicts réels (`clean`/`suspicious`/`malicious`). **Le contrat de fil ne change PAS** :
   `check.verify_file(quarantine_path, expected) -> (verdict, real_meta, checks)` ↔ `app.py` répond
   `{verdict, real_meta, checks}` ↔ DTO crawler `VerificationResult(verdict, real_meta, checks)`. La
   frontière de paquet est stable : `download_verifier` n'importe JAMAIS `emule_indexer` (et inversement).
2. **Exploiter `expected`** — la boucle bâtit déjà un `expected` MINIMAL (`{"target_id": …}` via
   `SqliteDownloadRepository.get_target_id`, ou `{}` si le hash n'a plus de ligne download). Le NO-OP
   l'ignore ; D-analysis l'enrichira (taille/durée/codec attendus de la cible) pour comparer.
3. **Le verifier ne lit que `quarantine/<hash>` en RO** ; il valide un **hash canonique** (32 hex)
   avant tout accès FS (anti-traversal) et **borne le corps** de la requête. Ces gardes restent valables
   pour D-analysis. Le confinement réel (enfant jetable) est l'ajout principal.
4. **Plan F (packaging, hors D-analysis)** — image Docker du verifier + réseau `internal: true` +
   compose + durcissement (gVisor/nsjail). Aujourd'hui le service tourne en dev (uvicorn,
   `python -m download_verifier`).

## 4. Pièges appris (CE jalon — la revue holistique a encore attrapé les pires, INVISIBLES au gate 100 %)

- **Busy-spin sur les chemins d'erreur re-bouclants de la boucle de vérif.** `fail_verification`
  remet la tâche en `pending` IMMÉDIATEMENT (pas de lease différé) et `attempts` est compté AU CLAIM.
  Tout chemin qui `fail`+`return` SANS dormir → la boucle re-claim aussitôt → rafale (RPC verify en
  boucle, lignes `file_verifications` dupliquées, logs en Go, dead-letter prématuré). Il a fallu un
  `await deps.clock.sleep(poll_interval)` sur **CHAQUE** chemin re-bouclant : file-vide,
  `VerifierUnavailableError`, **inner `RepositoryError` (record/complete)**, et le filet top-level.
  Les deux derniers ont été manqués au premier passage et rattrapés par la revue (le `record/complete`
  par la revue holistique finale). **Leçon : quand une boucle attrape une erreur et continue, vérifier
  que TOUS les chemins de continuation dorment, pas seulement le premier qu'on corrige.**
- **L'arrêt n'annule pas les tâches sœurs d'un `TaskGroup`.** `_supervise` n'annulait que le search
  `loop_task` ; annuler un enfant d'un `TaskGroup` n'annule PAS ses sœurs. Les boucles download/verify
  restaient bloquées dans leur sleep in-cycle (download jusqu'à 30 s ; `_sleep_or_nudge` ne surveille
  que poll/nudge, jamais `self._shutdown`) → le `shutdown_deadline` (10 s) tirait un `TimeoutError` →
  **force-exit au lieu d'un arrêt prompt**. Fix : collecter TOUS les handles de `group.create_task` et
  les `.cancel()` ensemble après `shutdown.wait()`. **Leçon : un `TaskGroup` n'annule pas en cascade ;
  chaque tâche longue doit être annulée explicitement OU surveiller l'event d'arrêt.**
- **`json.loads` lève `RecursionError` (un `RuntimeError`, pas `ValueError`) sur un JSON profondément
  imbriqué SOUS le cap d'octets** → 500 non géré côté verifier. Le cap d'octets borne la TAILLE, pas
  la PROFONDEUR. Fix : `except (json.JSONDecodeError, ValueError, RecursionError)` → 400 propre.
- **Path-traversal via le filename (hostile).** `resolve_staging_path` faisait `staging_base / filename`
  où `filename` vient de `last_observation().filename` (donnée réseau hostile, cf. CLAUDE.md). Un
  `/etc/passwd` ou `../../…` échappait `staging_base`, et ce chemin est la SOURCE d'un `os.replace`.
  Fix : confiner au **basename** (`Path(filename).name`) avec fallback hash sur le nom dégénéré.
  **Attention** : `Path("..").name == ".."` (PAS `""`) → le guard doit rejeter `{"", ".", ".."}`.
- **Satisfaction de Protocol** : l'adapter `HttpContentVerifier.verify` signe `expected: Mapping[str,
  object]` (PAS `dict`) pour satisfaire le port `ContentVerifier`. Le narrow `VerificationWriter`
  déclare `checks: tuple[object, ...]` et le repo réel accepte `Sequence[object]` (supertype) →
  satisfait par contravariance. Le `mypy_path` n'est PAS nécessaire (inerte) ; c'est l'absence de
  `packages/verifier/tests/__init__.py` qui évite la collision de module `tests` au mypy racine.
- **`uv run pytest` nu depuis la racine ne mesure AUCUNE coverage** (la racine n'a pas de
  `[tool.pytest.ini_options]`) et ne désélectionne pas les marqueurs d'intégration → faux « propre ».
  Neutralisé par un `conftest.py` racine (`collect_ignore_glob = ["packages/*"]`). **Toujours
  `cd packages/<pkg>` d'abord.**
- **`at-least-once` assumé** : `record_verification` (catalog.db) et `complete_verification` (local.db)
  ne peuvent PAS être atomiques (deux fichiers SQLite). Si `complete` échoue après un `record` réussi,
  le reclaim re-vérifie → ligne `file_verifications` DUPLIQUÉE possible. **D-analysis doit dédupliquer**
  (dernier verdict par hash). La boucle ne crash jamais et ne perd jamais une tâche.

## 5. Notes reportées (NON bloquantes — à trancher/faire en D-analysis ou plus tard)

- **Tension DV6/DV7 (sémantique de tolérance verifier).** Aujourd'hui un `VerifierUnavailableError`
  fait `fail_verification` (+ backoff) ; comme `attempts` est compté au claim et `fail` re-pend
  immédiatement, une panne verifier PROLONGÉE dead-letterera quand même les tâches en ~`max_attempts ×
  poll_interval`. DV7 voudrait une tolérance plus longue (« le verifier tombe → la boucle tolère,
  retry »). Acceptable pour le NO-OP (verifier de test up, download continue). À raffiner quand le
  verifier fait du vrai travail : un **health-gate dans la boucle** (ne pas claim si le verifier est
  down) ou un **retry-via-lease sans fail** distinguerait « service down » (transitoire) de « tâche
  poison » (déterministe → dead-letter).
- **`max_response_bytes` / cap du corps verifier** : les deux bornent une réponse/requête DÉJÀ
  bufferisée par httpx/Starlette — c'est un plafond de sanité/schéma, **PAS une défense mémoire/DoS**.
  Vrai bound en flux = durcissement **Plan F** (verifier sur `internal: true`).
- **Validation homelab manuelle** du download→verify COMPLET : le **vrai layout amuled** des fichiers
  complétés (`resolve_staging_path`, DÉCISION DV10) est **PENDING-homelab** — l'e2e pré-place le fichier
  en quarantaine, il n'exerce donc pas la chaîne `os.replace` depuis le staging amuled réel.
- **Reportées de D-download (toujours ouvertes)** : I1 (ré-émission `add_link` bénigne), I2 (granularité
  d'erreur par-étape dans `run_download_cycle`), T12 (couverture d'arrêt en intégration des boucles
  câblées — partiellement adressée ici par le test d'arrêt prompt). Voir le handoff D-download §5.

## 6. Méthode (bilan du jalon)

13 tâches exécutées **subagent-driven** (implémenteur frais → revue spec → revue qualité → revue
holistique finale avant tag). La **revue holistique a encore prouvé sa valeur** : elle a attrapé deux
bugs cross-cutting (le busy-spin `record/complete` et l'arrêt non-annulant) que le gate 100 % branch
ne voyait pas — comme à chaque jalon. Les revues qualité ont aussi attrapé le `RecursionError` (500) et
le path-traversal. Tests de régression écrits pour CHAQUE bug (et l'un d'eux vérifié comme attrapant
réellement le bug en restaurant la version buggée).

## 7. Prochaine étape

**D-analysis** (confinement réel + vrais checks remplissant `real_meta`, verdicts réels) — brainstormer
d'abord (la spec existe-t-elle ? sinon, design → plan → exécution). Puis **Plan E** (observabilité
Prometheus/apprise) et **Plan F** (packaging : 2 images Docker, compose, verifier `internal: true`,
glueforward pour aMule, quota disque infra).
