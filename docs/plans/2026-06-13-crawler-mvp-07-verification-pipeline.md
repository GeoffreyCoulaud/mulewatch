# Crawler MVP — Plan 7 : Pipeline de vérification (D-verify, NO-OP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fermer la boucle full-mode de bout en bout. D-verify (a) restructure le dépôt en **workspace uv VIRTUEL** (racine sans `[project]` + `packages/crawler` (dist `emule-indexer` inchangé) + `packages/verifier`) ; (b) livre le **service verifier** (`download_verifier` : Starlette `POST /verify` + `GET /health`, logique NO-OP `check.py`, entrée uvicorn) qui ne lit que `quarantine/<hash>` en RO et rend `unverified` ; (c) ajoute côté crawler le **port `ContentVerifier`** + l'adapter **`HttpContentVerifier`** (httpx, parsing défensif), `CatalogRepository.record_verification`, `SqliteDownloadRepository.get_target_id`, la config `VerifyConfig`/`verifier_url`, et la **boucle `run_verification_cycle`/`verification_loop`** (consommatrice de la file `verification_tasks`) ; (d) **câble LIVE les DEUX boucles** (download de D-download + vérification) dans `CrawlerApp` avec le **gate mode full** (`verifier_url` présent → full + health-check fail-fast ; absent → observateur, boucles OFF). Le verifier est trivial : il prouve TOUTE la plomberie ; le **vrai** travail d'analyse (confinement + checks réels remplissant `real_meta`) est **D-analysis** (hors périmètre). D-verify **clôt le jalon « Plan D »** et pose donc, à la dernière tâche, le **tag annoté** (`v0.8.0-auto-download`). Spec : `docs/superpowers/specs/2026-06-13-verification-pipeline-design.md`.

**Architecture:** Clean/Hexagonal, inchangée. Règle de dépendance (spec MVP §4) : `domain` pur (aucune I/O) ; `ports` n'importe que le domaine ; **`application` dépend des ports/domaine, JAMAIS d'un adapter** ; `adapters`/`composition` implémentent et assemblent. **Nouvelle frontière de paquet** : le verifier (`download_verifier`) est un paquet SÉPARÉ qui ne partage RIEN avec le crawler **que le contrat de fil** (le JSON de `/verify`) — son DTO de résultat (`check.VerificationResult`) et le DTO crawler (`ports.content_verifier.VerificationResult`) sont **définis indépendamment**, gardés en phase par le test de contrat + l'e2e. La file `verification_tasks` (local.db) **EST le couplage** : D-download enfile (`enqueue_verification`), D-verify consomme (`claim`/`complete`/`fail`/`reclaim`) à sa cadence (`verify.poll_interval`). Les contrats d'erreur restent dans les PORTS : `MuleUnreachableError`/`RepositoryError` (Plan C) + un NOUVEAU `VerifierUnavailableError` (port) que la boucle attrape pour retry. Déterminisme : `Clock`/`sleep`/`signal` injectés.

**Tech Stack:** Python ≥ 3.12 (`asyncio`, `sqlite3` stdlib). **Workspace uv VIRTUEL** (racine sans `[project]` ; `[tool.uv.workspace]` + `[tool.uv.sources]` + `[tool.ruff]`/`[tool.mypy]` racine ; `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` par paquet ; un seul `uv.lock` ; gate `( cd packages/<pkg> && uv run pytest )` + `uv run ruff/mypy .` racine — VALIDÉ sur uv 0.8.11). **Nouvelles deps runtime** (résolution validée) : `httpx 0.28.x` (crawler), `starlette 1.3.x` + `uvicorn 0.49.x` (verifier). `ruff` (E/F/I/UP/B/SIM, line 100), `mypy --strict` (src + tests), `pytest` + `pytest-asyncio` (mode `strict`) + `pytest-cov` (gate **100 % branch par paquet**), `sqlfluff` (dialecte sqlite, **crawler seul**). Tests HTTP via `httpx.ASGITransport` (in-process, **sans socket**). Déterminisme TOTAL : `Clock`/`sleep` injectables ; faux `ContentVerifier` ; **vrais** repos SQLite sur `tmp_path` ; faux hub `DecisionSignal`.

> **Référence spec :** `docs/superpowers/specs/2026-06-13-verification-pipeline-design.md` — §1 (but/périmètre), §2 (décisions verrouillées), §3 (workspace), §4 (service verifier NO-OP), §5 (ports & adapter crawler), §6 (boucle `run_verification_cycle`), §7 (gate full-mode + câblage live), §8 (erreurs/résilience), §9 (tests), §10 (DoD), §11 (suite = D-analysis). Plan D-download de référence (style/densité) : `docs/superpowers/plans/2026-06-13-crawler-mvp-06-download-orchestration.md`. Handoff : `docs/handoffs/2026-06-13 - handoff - download capability.md` (§3 = contrats à brancher, §5 = notes reportées).

> **HORS PÉRIMÈTRE (spec §1/§11 — RIEN de tout ceci ici) :** **D-analysis** (le confinement réel — enfant jetable `net=none`/rlimits/timeout/RO/non-root — et les **vrais checks** type_sniff/ffprobe/clamav qui remplissent `real_meta` et donnent des verdicts réels `clean`/`suspicious`/`malicious` ; ici le verifier rend `unverified`, `real_meta={}`, `checks=[]`). **Plan F (packaging)** : image Docker du verifier, réseau `internal: true`, compose, gVisor/nsjail — ici le service tourne en local/dev (uvicorn) pour l'e2e. Les **upgrades** et le **quota disque infra** (déjà hors D-download).

---

## Décisions verrouillées (spec §2 + les 3 concrétisations du contrôleur — ne PAS relitiger)

> **DÉCISION DV1 — Verifier NO-OP trivial (spec §2.1).** Le verifier renvoie `unverified`, **aucune** machinerie d'enfant jetable (déviation assumée de « machinerie dormante » MVP §10.4) ; le confinement vient avec les checks en D-analysis. `check.verify_file` fait UNIQUEMENT un `stat` RO du fichier en quarantaine : présent → `("unverified", {}, [])` ; absent → `("error", {}, [])`. Il **ne lit JAMAIS les octets** et **ignore `expected`**.

> **DÉCISION DV2 — Stack HTTP figée via context7 (spec §2.2).** `httpx` (client RPC async, crawler), `starlette` + `uvicorn` (verifier). Tests via `httpx.ASGITransport` in-process (sans socket). Formes EXACTES (context7) :
> - **Starlette** : `app = Starlette(routes=[Route("/verify", verify_endpoint, methods=["POST"]), Route("/health", health_endpoint, methods=["GET"])])` ; endpoint `async def verify_endpoint(request: Request) -> JSONResponse: raw = await request.body(); …` ; corps **borné** (`len(raw)` AVANT parse) ; JSON invalide → `JSONResponse({...}, status_code=400)`.
> - **httpx** : `transport = httpx.ASGITransport(app=app)` ; `async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:` ; en prod `httpx.AsyncClient(base_url=verifier_url, timeout=httpx.Timeout(10.0))`. Exceptions : `httpx.HTTPError` (base de connect/timeout/network), `httpx.HTTPStatusError` (de `raise_for_status()`).
> - **uvicorn** : `__main__.py` → `uvicorn.run("download_verifier.app:app", host=..., port=...)` (chemin d'import string) ; `main()` testée via monkeypatch de `uvicorn.run` (100 % branch), `if __name__ == "__main__":` sous `# pragma: no cover`. L'image/compose = Plan F.
> - **Versions résolues (uv lock)** : httpx **0.28.1**, starlette **1.3.1**, uvicorn **0.49.0**. Tasks 3 & 5 RE-confirment l'API via context7 avant de coder (Step 0).

> **DÉCISION DV3 — Workspace uv VIRTUEL, racine pure, deux enfants (spec §2.3/§3 — layout VALIDÉ EMPIRIQUEMENT sur uv 0.8.11).** Racine = **workspace VIRTUEL** : `pyproject.toml` racine SANS `[project]` table — seulement `[tool.uv.workspace] members=["packages/*"]`, `[tool.uv.sources]` (`emule-indexer = {workspace=true}`, `download-verifier = {workspace=true}`), `[dependency-groups] dev` (les deux membres + l'outillage partagé), ET `[tool.ruff]`/`[tool.mypy]` **AU NIVEAU RACINE** (un seul `uv run ruff check .` / `uv run mypy` qui SPANNENT les deux paquets). `packages/crawler` (paquet `emule_indexer`, dist name **`emule-indexer` INCHANGÉ** — PAS de rename ; `src/` + `tests/` déplacés) + `packages/verifier` (paquet `download_verifier`, dist `download-verifier`, **neuf**). **Fait empirique CLÉ** : `[tool.pytest.ini_options]`/`[tool.coverage]`/`[tool.sqlfluff]` DOIVENT être **PAR PAQUET** (un pytest racine fusionnerait les deux arbres et défait le 100 %-branch par paquet) ; `[tool.ruff]`/`[tool.mypy]` DOIVENT être à la RACINE (sinon deux invocations). `sqlfluff` ne vise que le crawler. `config/`/`docs/`/`scripts/`/`.github/` runtime restent RACINE. Un seul `uv.lock`. **Aucune migration de comportement** : gate vert sur les deux paquets comme critère de fin du Task 1.

> **Le gate (5 checks, VALIDÉ) — utilisé VERBATIM dans CHAQUE step « Vérifier » + dans CLAUDE.md/CI/hook (Task 1) :**
> ```bash
> ( cd packages/crawler  && uv run pytest -q )          # crawler tests, 100 % branch
> ( cd packages/verifier && uv run pytest -q )          # verifier tests, 100 % branch
> uv run ruff check .
> uv run ruff format --check .
> uv run mypy
> uv run sqlfluff lint packages/crawler/src
> ```
> **Run focalisé d'un test** (coverage off) : `( cd packages/crawler && uv run pytest tests/<…>.py::<test> --no-cov -q )`. **Run dédié d'un e2e** : `( cd packages/crawler && uv run pytest -m verify_integration --no-cov -q )`.

> **DÉCISION DV4 — Le verifier ne partage RIEN que le contrat de fil (spec §2.4).** DTO `VerificationResult` (crawler) et réponse JSON (verifier) **définis indépendamment**, gardés en phase par le **test de contrat** (`HttpContentVerifier` ↔ vraie app Starlette via ASGITransport) + l'e2e. Frontière de paquet = isolation imposée : `packages/verifier` n'importe JAMAIS `emule_indexer`, et inversement.

> **DÉCISION DV5 — La file de vérification EST le couplage (spec §2.5).** Le download enfile (`enqueue_verification`, D-download), la vérif `claim` à sa cadence (`verify.poll_interval`). **Pas** de nouveau nudge pour la vérif (la file est durable, le poll est le filet). La boucle de vérif est un **consommateur** pur de `verification_tasks`.

> **DÉCISION DV6 — Séparation transitoire vs mauvaise réponse (spec §2.6/§8).** Service injoignable (connexion refusée / timeout / 5xx) → **transitoire** : `VerifierUnavailableError` levé par l'adapter → `fail_verification(task_id)` (lease → retry) ; on **n'invente pas** de verdict ; après `max_attempts` → **dead-letter** (poison). Réponse 200 **malformée / hors-schéma / trop grosse** → parsing défensif → `VerificationResult(verdict="error", …)` **enregistré** + `complete` (pas de boucle infinie sur une réponse déterministe mauvaise).

> **DÉCISION DV7 — Gate full-mode par `verifier_url` (spec §2.7/§7).** `verifier_url` **défini** → mode **full** : construit `HttpContentVerifier` → **`health()`** → **injoignable ⇒ fail-fast** (le crawler refuse de démarrer en full plutôt que télécharger sans vérif). `verifier_url` **absent** → **observateur** : les DEUX boucles (download + vérif) restent **OFF**. **Mi-parcours** : si le verifier tombe, la boucle de vérif **tolère** (RPC transitoire → retry via lease) et le download **continue** — **pas** de fail-fast à chaud.

> **DÉCISION DV8 — Câblage live des DEUX boucles (concrétisation contrôleur + spec §7).** En full, `CrawlerApp` valide **l'ensemble complet** de la config download (la lacune unidirectionnelle T10 du handoff : `download` + `download_endpoint` + `staging_dir` + `quarantine_dir`) AVANT d'activer ; construit le client EC download (connexion distincte, tolère `MuleUnreachableError`), `FilesystemQuarantine`, `SqliteDownloadRepository`, le `ContentVerifier`, et lance `download_loop` + `verification_loop` comme **tâches additionnelles du `TaskGroup`** de `_supervise`.

> **DÉCISION DV9 — Nudge download producteur (concrétisation 1 / handoff §3.2 / DÉCISION D13).** La boucle de download s'abonne au sujet FIXE `DOWNLOAD_NUDGE_SUBJECT = "download"`. D-verify ajoute un `signal("download")` dans `record_observations` **quand le verdict enregistré est de tier `"download"`**, pour que la boucle de download réagisse au nouveau verdict. Minimal : le poll de repli couvre déjà (un nudge perdu est inoffensif), mais le câblage est de la réactivité gratuite que le handoff a explicitement fléchée.

> **DÉCISION DV10 — `staging_path_for` resolver (concrétisation 2 / handoff §3.4 / DÉCISION D2).** `DownloadEntry` ne porte que le hash. La composition construit `staging_path_for = lambda entry: Path(staging_dir) / <filename>` où `<filename>` vient de `catalog.last_observation(entry.ed2k_hash).filename`. Si `last_observation` est `None`, le resolver produit un chemin (best-effort sous `staging_dir`) qui échouera simplement à `os.replace` → `_promote_completion`'s broad-except laisse `completed` (retry) — **JAMAIS de crash**. Le **vrai layout amuled** des fichiers complétés est **PENDING-homelab** (spec §9 accepte que la chaîne complète soit homelab-validée ; l'e2e pré-place le fichier en quarantaine, donc n'exerce pas ce chemin).

> **DÉCISION DV11 — `target_id` pour `expected` (concrétisation 3 / handoff §3).** `ClaimedTask` n'a PAS de `target_id` et le repo downloads n'avait pas de lookup. D-verify ADD `get_target_id(self, ed2k_hash) -> str | None` à `SqliteDownloadRepository` (`SELECT target_id FROM downloads WHERE ed2k_hash=?`) + le Protocol narrow que la boucle de vérif utilise. Le verifier NO-OP **ignore `expected`** → on bâtit un `expected` MINIMAL : `{"target_id": target_id}` si trouvé, `{}` sinon — documenté « minimal en NO-OP ; D-analysis enrichira ». La branche `None` est couverte.

> **DÉCISION DV12 — `VerifyConfig`/`verifier_url` OPTIONNELS (motif DÉCISION D11).** Comme `DownloadConfig`, la section `verify` de `crawler.yaml` (`poll_interval_seconds`) et `verifier_url` de `local.yaml` sont **optionnels** (`None` par défaut) pour ne pas casser le crawler observateur. Présents → validés fail-fast. Le gate full-mode (composition) exige `verifier_url` ET l'ensemble download avant d'activer.

> **Note couverture (gate 100 % branch par paquet — points chauds) :** stubs de Protocol **une ligne** (`def m(...) -> T: ...`, couverts par le `def`). Côtés exercés des DEUX côtés : `check.verify_file` (présent→unverified / absent→error) ; verifier `/verify` (valide / JSON invalide→400 / corps trop gros→400 / champ manquant→400) + `/health`→200 ; `HttpContentVerifier.verify` (200 bien formé→VerificationResult / 200 malformé→error / corps trop gros→error / connect→VerifierUnavailableError / timeout→VerifierUnavailableError / 5xx→VerifierUnavailableError) ; `HttpContentVerifier.health` (200→True / non-2xx→False / injoignable→False) ; `record_verification` (hash canonique / non canonique→PersistenceError ; real_meta/checks sérialisés) ; `get_target_id` (présent / absent→None) ; `VerifyConfig` parser (absent→None / présent / poll_interval ≤0→ConfigError / section non-mapping) ; `verifier_url` parser (absent→None / présent non-vide / vide→ConfigError) ; `run_verification_cycle` (reclaim ; claim None→sleep/return ; claim→target_id trouvé/None→expected ; verify→record→complete ; VerifierUnavailableError→fail ; RepositoryError(record)→fail ; malformé→verdict error enregistré+complete) ; `verification_loop` (shutdown avant start→0 cycle / cycle→sleep→stop / shutdown pendant cycle→break) ; composition (observateur : boucles OFF / full : health ok→ON / health injoignable→fail-fast / download incomplet en full→fail-fast) ; `record_observations` (tier download→signal("download") / tier autre→pas de signal("download")).

> **Note ordonnancement & convention de run :** chaque tâche = test(s) qui échoue(nt) → run/échec attendu → impl minimale → run/pass → **gate 5 checks (par paquet, voir Task 1)** → commit conventionnel. Runs focalisés en `--no-cov`. Laisser ruff trancher l'ordre des imports (`uv run ruff check . --fix && uv run ruff format .`) avant le gate. Chaque message de commit se termine par le trailer HEREDOC `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

```
emule-indexer/                                   # RACINE = workspace VIRTUEL (aucun [project], aucun code)
├── pyproject.toml                               # Modify : [tool.uv.workspace]+[tool.uv.sources]+dev + [tool.ruff]/[tool.mypy] RACINE
├── uv.lock                                      # un seul lockfile (régénéré par uv au Task 1)
├── packages/
│   ├── crawler/                                 # paquet emule_indexer (dist emule-indexer INCHANGÉ ; DÉPLACÉ par git mv)
│   │   ├── pyproject.toml                        # Create : [project] emule-indexer + httpx + hatchling + [tool.pytest]/[tool.coverage]/[tool.sqlfluff]
│   │   ├── src/emule_indexer/                    # MOVED depuis racine/src/
│   │   │   ├── ports/
│   │   │   │   ├── content_verifier.py           # Create : ContentVerifier (Protocol) + VerificationResult DTO
│   │   │   │   ├── verifier_errors.py            # Create : VerifierUnavailableError (port)
│   │   │   │   ├── catalog_repository.py         # Modify : + record_verification
│   │   │   │   └── …                             # (inchangés, déplacés)
│   │   │   ├── adapters/
│   │   │   │   ├── verifier_http.py              # Create : HttpContentVerifier (httpx)
│   │   │   │   ├── config/
│   │   │   │   │   ├── crawler_config.py          # Modify : + VerifyConfig (verify.poll_interval_seconds)
│   │   │   │   │   └── local_config.py            # Modify : + verifier_url
│   │   │   │   └── persistence_sqlite/
│   │   │   │       ├── catalog_repository.py      # Modify : + record_verification
│   │   │   │       └── download_repository.py     # Modify : + get_target_id
│   │   │   ├── application/
│   │   │   │   ├── run_verification_cycle.py      # Create : run_verification_cycle + verification_loop
│   │   │   │   └── record_observations.py         # Modify : + signal("download") sur tier=download
│   │   │   └── composition/
│   │   │       ├── app.py                         # Modify : gate full-mode + câblage des DEUX boucles
│   │   │       └── __main__.py                    # Modify : build_app passe verify/verifier_url (déjà via config)
│   │   └── tests/                                # MOVED depuis racine/tests/ (+ nouveaux fichiers de test)
│   │       ├── composition/test_main.py          # Modify : parents[2] -> parents[4] (config racine, Task 1 Step 6)
│   │       └── integration/test_verify_loop.py   # Create : e2e verify_integration
│   └── verifier/                                # paquet download_verifier (NEUF)
│       ├── pyproject.toml                        # Create : [project] download-verifier + starlette/uvicorn + hatchling + [tool.pytest]/[tool.coverage]
│       ├── src/download_verifier/
│       │   ├── __init__.py                        # Create (vide)
│       │   ├── check.py                           # Create : verify_file NO-OP (stat RO)
│       │   ├── app.py                             # Create : Starlette POST /verify + GET /health
│       │   └── __main__.py                        # Create : uvicorn entry (# pragma: no cover)
│       └── tests/
│           ├── __init__.py                        # Create (vide)
│           ├── test_package.py                    # Create (Task 1 : squelette gate-vert)
│           ├── test_check.py                      # Create (Task 2)
│           ├── test_app.py                        # Create (Task 3, httpx ASGITransport)
│           └── test_main.py                       # Create (Task 3, uvicorn.run monkeypatché)
├── config/   docs/   scripts/   .github/   .githooks/  README.md  CLAUDE.md   # RESTENT racine, adaptés au workspace
```

> **Carte de dépendance (cohérence des signatures, vérifiée à l'écriture) :**
> - **Verifier (paquet `download_verifier`, isolé)** :
>   - `check.verify_file(quarantine_path: Path, expected: Mapping[str, object]) -> tuple[str, dict[str, object], list[object]]` (verdict, real_meta, checks). `check.py`.
>   - `download_verifier.app.app: Starlette` (module-level, pour uvicorn par chemin d'import). `app.py`.
> - **Crawler (paquet `emule_indexer`)** :
>   - `VerificationResult(verdict: str, real_meta: Mapping[str, object], checks: tuple[object, ...])` (frozen). `ports/content_verifier.py`.
>   - `ContentVerifier(Protocol)` : `async verify(self, ed2k_hash: str, expected: Mapping[str, object]) -> VerificationResult` ; `async health(self) -> bool`. `ports/content_verifier.py`.
>   - `VerifierUnavailableError(RepositoryError)` ? **NON** — `VerifierUnavailableError(Exception)` indépendant (un port d'erreur dédié, motif `MuleUnreachableError`). `ports/verifier_errors.py`. La boucle l'attrape À CÔTÉ de `RepositoryError`.
>   - `HttpContentVerifier(client: httpx.AsyncClient, *, max_response_bytes: int = 65536)` : `async verify(...)`, `async health()`, `async aclose()`. `adapters/verifier_http.py`.
>   - `CatalogRepository.record_verification(self, ed2k_hash: str, verdict: str, real_meta: Mapping[str, object], checks: Sequence[object]) -> None`. Port + impl Sqlite.
>   - `SqliteDownloadRepository.get_target_id(self, ed2k_hash: str) -> str | None`.
>   - `VerifyConfig(poll_interval_seconds: float)` (frozen) ; `CrawlerConfig.verify: VerifyConfig | None = None`. `adapters/config/crawler_config.py`.
>   - `LocalConfig.verifier_url: str | None = None`. `adapters/config/local_config.py`.
>   - `VerifyDeps` (dataclass) + `VerifyLoopDeps(VerifyDeps)` + `run_verification_cycle(deps) -> None` + `verification_loop(deps) -> None`. `application/run_verification_cycle.py`. Protocols narrow : `VerificationTaskQueue` (reclaim_expired/claim_verification/complete_verification/fail_verification), `TargetIdLookup` (get_target_id), `VerificationWriter` (record_verification).

---

(Les tâches numérotées suivent. Chaque tâche est autonome : write failing test → run fail → impl complète → run pass → gate → commit. Le gate complet est défini au Task 1 — il CHANGE avec le workspace.)

---

## Task 1 : Migration en workspace uv VIRTUEL (racine pure + `packages/crawler` + `packages/verifier`)

**Files :** `git mv src/tests` → `packages/crawler/` ; Create `packages/crawler/pyproject.toml` + `packages/verifier/{pyproject.toml,src/download_verifier/__init__.py,tests/{__init__.py,test_package.py}}` ; Modify racine `pyproject.toml` (→ workspace virtuel) + `.github/workflows/ci.yml` + `.githooks/pre-push` + `CLAUDE.md` + `README.md` ; Modify `packages/crawler/tests/composition/test_main.py` (`parents[2]`→`parents[4]`). **AUCUN changement de comportement.** Critère de fin : **gate vert sur les DEUX paquets.**

> **Layout VALIDÉ EMPIRIQUEMENT** (revue structurelle en worktree jetable, uv 0.8.11 ; `uv lock` a résolu **httpx 0.28.1, starlette 1.3.1, uvicorn 0.49.0**). C'est la tâche la plus risquée ; sa preuve EST le gate vert : les **614 tests crawler existants** passent inchangés sous la nouvelle disposition (UN seul fix de chemin nécessaire, Step 6), et le squelette verifier atteint 100 % branch dès le Task 1. **Faits vérifiés** : (1) `tests/` est un paquet (`tests/__init__.py` présent) + 8 fichiers font `from tests.…` → la rootdir pytest reste celle qui contient `tests/` → on lance pytest **DEPUIS** `packages/crawler` (`cd packages/crawler && uv run pytest`), pas par chemin global ; (2) le runner de migrations charge les `.sql` par `importlib.resources.files("emule_indexer.adapters.persistence_sqlite") / "migrations"` → le `git mv` ne casse rien (paquet `emule_indexer` stable, `.sql` = données du paquet) ; (3) `config/` runtime reste RACINE → les défauts `config/*.yaml` de `__main__` (relatifs au CWD) inchangés, MAIS `tests/composition/test_main.py` lit le `config/` racine par chemin absolu (`parents[2]` → DOIT devenir `parents[4]`, Step 6) ; (4) `tests/fixtures/` se déplace AVEC les tests → les readers de `fixtures` (`parents[1]`/`parents[2]` relatifs à `tests/`) restent corrects, AUCUN changement ; (5) `[tool.ruff]`/`[tool.mypy]` à la RACINE (un `ruff check .` / `mypy` span les deux paquets), `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` PAR PAQUET (sinon le 100 %-branch par paquet est défait).

- [ ] **Step 1 : `git mv` du crawler sous `packages/crawler/`**

```bash
mkdir -p packages/crawler packages/verifier
git mv src packages/crawler/src
git mv tests packages/crawler/tests
```

(`config/`, `docs/`, `scripts/`, `.github/`, `.githooks/`, `README.md`, `CLAUDE.md` RESTENT à la racine. NE PAS déplacer `uv.lock` — la racine devient le workspace. `tests/fixtures/` part avec `tests/`.)

- [ ] **Step 2 : `packages/crawler/pyproject.toml` (membre crawler — dist `emule-indexer` INCHANGÉ)**

Créer `packages/crawler/pyproject.toml`. Le dist name reste **`emule-indexer`** (PAS de rename). Il porte les `[tool.*]` PAR PAQUET (`pytest`/`coverage`/`sqlfluff`) — **PAS** de `[tool.ruff]`/`[tool.mypy]` (ils vivent à la RACINE). Ajout de `httpx` :
```toml
[project]
name = "emule-indexer"
version = "0.0.0"
description = "Crawler eMule — surveillance, catalogue, auto-download (Keroro VF lost media)"
requires-python = ">=3.12"
dependencies = [
    "google-re2>=1.1.20251105",
    "pyyaml>=6.0.3",
    "rapidfuzz>=3.14.5",
    "httpx>=0.28",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/emule_indexer"]

[tool.pytest.ini_options]
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration and not orchestration_integration and not download_integration and not verify_integration"'
testpaths = ["tests"]
markers = [
    "ec_integration: tests d'intégration contre un amuled réel (Docker requis) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m ec_integration --no-cov",
    "orchestration_integration: boucle de crawl réelle contre un amuled testcontainers (Docker requis) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m orchestration_integration --no-cov",
    "download_integration: add_link + lecture de la file de download contre un amuled réel (Docker requis) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m download_integration --no-cov",
    "verify_integration: boucle de vérification contre le vrai service verifier (ASGITransport, sans Docker) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m verify_integration --no-cov",
]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"

[tool.coverage.run]
branch = true
source = ["emule_indexer"]

[tool.coverage.report]
show_missing = true
fail_under = 100
exclude_also = ["if TYPE_CHECKING:"]

[tool.sqlfluff.core]
dialect = "sqlite"
max_line_length = 100

[tool.sqlfluff.rules.references.keywords]
ignore_words = "key,value"
```

> **Note marqueur `verify_integration` :** ajouté ICI (Task 1) car la `addopts` du crawler doit le déselectionner par défaut ; l'e2e qui l'utilise arrive au Task 12 (un marqueur déclaré sans test est inoffensif avec `--strict-markers`).

- [ ] **Step 3 : `packages/verifier/` — squelette du paquet `download_verifier` (gate-vert dès le Task 1)**

```bash
mkdir -p packages/verifier/src/download_verifier packages/verifier/tests
```
Créer `packages/verifier/src/download_verifier/__init__.py` (VIDE) et `packages/verifier/tests/__init__.py` (VIDE).

Créer `packages/verifier/pyproject.toml` — dist `download-verifier` (import `download_verifier`), deps figées par la résolution (`starlette 1.3.1`/`uvicorn 0.49.0`) ; `[tool.*]` PAR PAQUET (`pytest`/`coverage`), **PAS** de `[tool.ruff]`/`[tool.mypy]` (RACINE) :
```toml
[project]
name = "download-verifier"
version = "0.0.0"
description = "Service de vérification (NO-OP) des fichiers en quarantaine — déployable séparé"
requires-python = ">=3.12"
dependencies = [
    "starlette>=1.3",
    "uvicorn>=0.30",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/download_verifier"]

[tool.pytest.ini_options]
addopts = '--cov=download_verifier --cov-report=term-missing --cov-fail-under=100 --strict-markers'
testpaths = ["tests"]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"

[tool.coverage.run]
branch = true
source = ["download_verifier"]

[tool.coverage.report]
show_missing = true
fail_under = 100
exclude_also = ["if TYPE_CHECKING:"]
```
(L'outillage dev — `pytest`/`pytest-asyncio`/`pytest-cov`/`mypy`/`ruff`/`sqlfluff`/`testcontainers`/`types-pyyaml`/`httpx` — vient du `[dependency-groups] dev` RACINE, partagé par tout le workspace.)

Pour que `download-verifier` atteigne 100 % branch dès le Task 1 (sinon `pytest` rapporte « no tests ran » → exit 5), créer un test trivial `packages/verifier/tests/test_package.py` :
```python
import download_verifier


def test_package_is_importable() -> None:
    assert download_verifier.__name__ == "download_verifier"
```
> La revue a confirmé qu'un paquet quasi-vide atteint « 1 passed, 100 % ». Task 2 remplit `check.py` ensuite.

- [ ] **Step 4 : `pyproject.toml` RACINE devient le workspace VIRTUEL (SANS `[project]`)**

Remplacer INTÉGRALEMENT le contenu de `pyproject.toml` (racine) par (workspace VIRTUEL — **aucune** `[project]` table ; `[tool.ruff]`/`[tool.mypy]` ICI, spannant les deux paquets) :
```toml
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
emule-indexer = { workspace = true }
download-verifier = { workspace = true }

[dependency-groups]
dev = [
  "emule-indexer",
  "download-verifier",
  "pytest>=8",
  "pytest-cov>=5",
  "pytest-asyncio>=1.2",
  "mypy>=1.10",
  "ruff>=0.5",
  "sqlfluff>=3.0",
  "testcontainers>=4.10",
  "types-pyyaml>=6.0.12.20260518",
  "httpx>=0.28",
]

[tool.ruff]
line-length = 100
src = [
  "packages/crawler/src",
  "packages/crawler/tests",
  "packages/verifier/src",
  "packages/verifier/tests",
]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
files = [
  "packages/crawler/src",
  "packages/crawler/tests",
  "packages/verifier/src",
  "packages/verifier/tests",
]

[[tool.mypy.overrides]]
module = "re2"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "testcontainers.*"
ignore_missing_imports = true
```

> **Justification (context7 uv — VALIDÉ) :** racine = workspace VIRTUEL (pas de `[project]` → `uv` ne tente pas de la builder). `[tool.uv.sources] … = {workspace=true}` + leur présence dans `dev` font que `uv sync` installe les DEUX membres en éditable dans le venv racine partagé (le test de contrat/e2e du crawler peut donc importer `download_verifier`). `[tool.ruff]`/`[tool.mypy]` à la racine → **un** `ruff check .` / **un** `mypy` couvrent les deux arbres (Fait empirique CLÉ). `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` restent PAR PAQUET (Step 2/3) → on lance pytest DEPUIS chaque paquet pour préserver le 100 %-branch par paquet (un pytest racine fusionnerait les deux sources et le défait). Un seul `uv.lock` racine.

- [ ] **Step 5 : régénérer le lockfile + sync**

```bash
uv lock
uv sync --dev
```
Expected : `uv.lock` régénéré (un seul, racine ; les deux membres + httpx 0.28.x / starlette 1.3.x / uvicorn 0.49.x résolus) ; `uv sync` installe les deux paquets en éditable + le groupe dev dans le venv racine.

- [ ] **Step 6 : fix des chemins de test cassés par la profondeur (+1 ou +2 niveaux)**

**Lire d'abord** `packages/crawler/tests/composition/test_main.py` : il calcule la racine du dépôt par `Path(__file__).resolve().parents[2] / "config"`. Après le `git mv`, le fichier descend de 2 niveaux (`packages/crawler/`) → `parents[2]` pointe désormais sur `packages/crawler` (où il n'y a pas de `config/`). Corriger en **`parents[4]`** (parents : `composition`[0], `tests`[1], `crawler`[2], `packages`[3], **racine**[4]) :
```python
_CONFIG = Path(__file__).resolve().parents[4] / "config"
```
> **Symptôme si oublié** (constaté par la revue) : `YamlLoadError … No such file or directory`, couverture 99.83 %, RED.

**Vérifier qu'aucun AUTRE test ne lit le `config/` RACINE par chemin** (les readers de `tests/fixtures/` ne bougent PAS — `fixtures` part avec `tests`, leur `parents[N]` reste correct) :
```bash
grep -rn "parents\[" packages/crawler/tests/ | grep -iE "config"
```
Expected : seul `test_main.py` (corrigé). Les hits `fixtures` (`test_golden_corpus.py`, `conftest.py`, `test_app.py`, `test_crawler_loop.py`) NE sont PAS touchés.

- [ ] **Step 7 : adapter la CI, le hook, CLAUDE.md, README.md (gate per-paquet)**

**(a) Le gate (5 checks, VALIDÉ) — à utiliser dans TOUTES les tâches suivantes :**
```bash
( cd packages/crawler  && uv run pytest -q )          # crawler tests, 100 % branch
( cd packages/verifier && uv run pytest -q )          # verifier tests, 100 % branch
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint packages/crawler/src
```
> `ruff`/`mypy`/`format` racine spannent les deux paquets (config racine) ; `pytest` lancé DEPUIS chaque paquet (sa `[tool.pytest]`/rootdir, `tests` importable pour `from tests.…`, 100 %-branch isolé) ; `sqlfluff` vise le SQL crawler. **Run focalisé** : `( cd packages/crawler && uv run pytest tests/<…>.py::<test> --no-cov -q )`.

**(b) `.github/workflows/ci.yml`** — remplacer les deux dernières lignes (`uv run sqlfluff lint src` et `uv run pytest`) :
```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run sqlfluff lint packages/crawler/src
      - run: uv run mypy
      - run: ( cd packages/crawler  && uv run pytest )
      - run: ( cd packages/verifier && uv run pytest )
```

**(c) `.githooks/pre-push`** — remplacer le corps par le gate validé :
```bash
#!/usr/bin/env bash
# Pré-push : refuse le push si un check échoue (mêmes checks que la CI, les 2 paquets).
set -euo pipefail

command -v uv >/dev/null 2>&1 || { echo "[pre-push] ERROR: 'uv' introuvable. Installe-le : https://docs.astral.sh/uv/"; exit 1; }

echo "[pre-push] ruff check…";          uv run ruff check .
echo "[pre-push] ruff format --check…"; uv run ruff format --check .
echo "[pre-push] sqlfluff lint…";       uv run sqlfluff lint packages/crawler/src
echo "[pre-push] mypy…";                uv run mypy
echo "[pre-push] pytest crawler…";      ( cd packages/crawler  && uv run pytest )
echo "[pre-push] pytest verifier…";     ( cd packages/verifier && uv run pytest )
echo "[pre-push] OK"
```

**(d) `CLAUDE.md`** — section « Commands » : remplacer l'ancien gate 5-checks single-package par le gate validé (a) ; remplacer l'exemple « single test » (`uv run pytest "tests/…::test" --no-cov -q`) par `( cd packages/crawler && uv run pytest tests/…::test --no-cov -q )`. Ajouter « Workspace uv VIRTUEL : `packages/crawler` (paquet `emule_indexer`, dist `emule-indexer`) + `packages/verifier` (paquet `download_verifier`) ; `[tool.ruff]`/`[tool.mypy]` à la racine, `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` par paquet ; un seul `uv.lock` ; `config/` reste racine. » Ne PAS encore décrire D-verify (Task 13).

**(e) `README.md`** — si une commande de gate / d'install y figure, la passer au gate validé (a) + `uv sync --dev` racine.

- [ ] **Step 8 : Vérifier (le gate EST la preuve)**

```bash
( cd packages/crawler && uv run pytest -q )
```
Expected : `614 passed, 6 deselected` (4 ec + 1 orchestration + 1 download ; `verify_integration` n'a pas encore de test → pas compté), **100.00 % branch** sur `emule_indexer`.
```bash
( cd packages/verifier && uv run pytest -q )
```
Expected : `1 passed`, **100.00 %** sur `download_verifier`.
Puis `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint packages/crawler/src` → tout vert. Vérifier que les e2e existants se collectent encore :
```bash
( cd packages/crawler && uv run pytest -m download_integration --collect-only -q )
```
Expected : `1 test collected` (le marqueur + le chemin de l'e2e ont survécu au `git mv`).

> **Note couverture (Task 1) :** aucune nouvelle branche de prod ; la preuve est que les 614 tests crawler (après le fix `parents[4]`) + le 1 test verifier passent à 100 % branch par paquet. Si un test crawler casse à cause d'un chemin codé en dur, corriger le test (chemin relatif à sa rootdir / `tmp_path`) — JAMAIS baisser le seuil.

- [ ] **Step 9 : Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: workspace uv virtuel (racine pure + packages/crawler + packages/verifier)

git mv du crawler sous packages/crawler (dist emule-indexer inchangé) ; squelette
packages/verifier (download_verifier) ; pyproject racine = workspace virtuel
([tool.uv.workspace] + sources + ruff/mypy racine) ; pytest/coverage/sqlfluff
par paquet ; gate/CI/hook/CLAUDE.md/README adaptés ; httpx ajouté au crawler ;
fix test_main parents[2]->parents[4]. 614 tests crawler verts inchangés.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 : Verifier `check.py` (logique NO-OP)

**Files :**
- Create: `packages/verifier/src/download_verifier/check.py`
- Create: `packages/verifier/tests/test_check.py`

> DÉCISION DV1 : `verify_file(quarantine_path, expected)` fait UNIQUEMENT un `stat` RO. Présent → `("unverified", {}, [])` ; absent → `("error", {}, [])`. Il **ne lit JAMAIS les octets** et **ignore `expected`**. La forme de résultat (verdict, real_meta, checks) est définie ICI, **indépendamment** du DTO crawler (DÉCISION DV4). Vérifié à l'écriture : `Path.exists()` / `Path.is_file()` ne lisent pas le contenu.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_check.py` :
```python
from pathlib import Path

from download_verifier.check import verify_file


def test_existing_file_is_unverified_noop(tmp_path: Path) -> None:
    target = tmp_path / "a" * 32
    target.write_bytes(b"\x00\x01\x02")  # le verifier ne lit JAMAIS ces octets
    verdict, real_meta, checks = verify_file(target, {"target_id": "S2E062A"})
    assert verdict == "unverified"
    assert real_meta == {}
    assert checks == []


def test_missing_file_is_error(tmp_path: Path) -> None:
    verdict, real_meta, checks = verify_file(tmp_path / "absent", {})
    assert verdict == "error"
    assert real_meta == {}
    assert checks == []


def test_directory_is_error_not_unverified(tmp_path: Path) -> None:
    # une quarantaine "fichier" qui est en fait un répertoire n'est pas un fichier vérifiable.
    directory = tmp_path / "dir"
    directory.mkdir()
    verdict, _real_meta, _checks = verify_file(directory, {})
    assert verdict == "error"


def test_expected_is_ignored_in_noop(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"data")
    # même verdict quel que soit expected (le NO-OP ne l'exploite pas).
    assert verify_file(target, {})[0] == "unverified"
    assert verify_file(target, {"anything": 1})[0] == "unverified"
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_check.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.check'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/check.py` :
```python
"""Logique de vérification NO-OP (spec verify §4 — DÉCISION DV1).

Le verifier est trivial : il confirme l'EXISTENCE du fichier en quarantaine (``stat`` RO)
et rend ``unverified`` — il ne lit JAMAIS les octets, n'exécute rien dessus, et ignore
``expected``. Le VRAI travail d'analyse (confinement jetable + checks type_sniff/ffprobe/
clamav remplissant ``real_meta``) est D-analysis. La forme de résultat (verdict, real_meta,
checks) est définie ICI, indépendamment du DTO crawler (frontière de paquet, DÉCISION DV4) ;
le contrat de fil JSON les garde en phase (test de contrat + e2e).

Le verifier est stateless, no-DB, no-domain, no-Internet : il ne connaît que le dossier de
quarantaine (config du service) et le hash demandé.
"""

from collections.abc import Mapping
from pathlib import Path

# Verdict NO-OP : un fichier présent est "unverified" (existence prouvée, contenu non analysé) ;
# absent (ou non-fichier) est "error" (rien à vérifier — la boucle l'enregistre + complète).
_VERDICT_UNVERIFIED = "unverified"
_VERDICT_ERROR = "error"


def verify_file(
    quarantine_path: Path, expected: Mapping[str, object]
) -> tuple[str, dict[str, object], list[object]]:
    """Vérifie (NO-OP) un fichier en quarantaine. Rend ``(verdict, real_meta, checks)``.

    Ne lit JAMAIS les octets (``is_file`` ne touche que les métadonnées d'inode) et ignore
    ``expected`` (le NO-OP n'en fait rien ; D-analysis l'exploitera pour comparer aux attendus).
    Fichier régulier présent → ``("unverified", {}, [])`` ; absent ou non-fichier (répertoire,
    lien cassé…) → ``("error", {}, [])``.
    """
    if quarantine_path.is_file():
        return _VERDICT_UNVERIFIED, {}, []
    return _VERDICT_ERROR, {}, []
```

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_check.py -q --no-cov )` → PASS (4 tests).
Run : gate du paquet verifier (`( cd packages/verifier && uv run pytest -q )` + ruff/mypy racine) → tout vert, 100 %.

> **Note couverture :** `is_file()` True (fichier présent → unverified) / False (absent OU répertoire → error) — les deux côtés exercés (`test_existing_file…` / `test_missing_file…` + `test_directory…`). `expected` ignoré : couvert par `test_expected_is_ignored_in_noop` (aucune branche dépendant de `expected`, donc pas de couverture supplémentaire requise — c'est une garantie comportementale).

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/check.py packages/verifier/tests/test_check.py
git commit -m "$(cat <<'EOF'
feat(verifier): check.verify_file NO-OP (stat RO — unverified/error, jamais les octets)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 : Verifier `app.py` (Starlette) + `__main__.py` (uvicorn)

**Files :**
- Create: `packages/verifier/src/download_verifier/app.py`
- Create: `packages/verifier/src/download_verifier/__main__.py`
- Create: `packages/verifier/tests/test_app.py`

> Spec §4 : `POST /verify {hash, expected}` (validation STRICTE + bornée → 400 sur corps invalide/trop gros/champ manquant ; appelle `check.verify_file` ; rend `{verdict, real_meta, checks}`) ; `GET /health` → 200. Testé via `httpx.ASGITransport` in-process (DÉCISION DV2). Le dossier racine de quarantaine vient d'une variable d'environnement (config du service ; défaut `/quarantine`).

- [ ] **Step 0 : RE-confirmer l'API starlette 1.3.x + httpx 0.28.x via context7 (avant de coder)**

`uv lock` a résolu **starlette 1.3.1** (bien au-delà du floor spec `>=1.3`) et **httpx 0.28.1**. Avant d'écrire le code, interroger context7 (`/kludex/starlette`, `/encode/httpx`) pour CONFIRMER les formes : `Starlette(routes=[Route(path, ep, methods=[...])])`, `async def ep(request: Request) -> JSONResponse`, `await request.body()`/`await request.json()`, `JSONResponse(payload, status_code=…)`, `app.state` ; et côté test `httpx.ASGITransport(app=app)` + `httpx.AsyncClient(transport=…, base_url=…)`. (Vérifié à l'écriture du plan : ces signatures sont INCHANGÉES en starlette 1.3.1 / httpx 0.28.1 — mais re-confirmer au cas où.) Si une signature a bougé, adapter le code des Steps suivants en conséquence (ne PAS deviner).

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_app.py` :
```python
import json
from pathlib import Path

import httpx
import pytest

from download_verifier.app import build_app


@pytest.fixture
def quarantine(tmp_path: Path) -> Path:
    directory = tmp_path / "quarantine"
    directory.mkdir()
    return directory


def _client(quarantine: Path) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=build_app(quarantine))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_health_returns_200(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_verify_existing_file_returns_unverified(quarantine: Path) -> None:
    (quarantine / ("a" * 32)).write_bytes(b"\x00\x01")
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", json={"hash": "a" * 32, "expected": {"target_id": "S2E062A"}}
        )
    assert response.status_code == 200
    body = response.json()
    assert body == {"verdict": "unverified", "real_meta": {}, "checks": []}


@pytest.mark.asyncio
async def test_verify_missing_file_returns_error_verdict(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "b" * 32, "expected": {}})
    assert response.status_code == 200
    assert response.json()["verdict"] == "error"


@pytest.mark.asyncio
async def test_verify_rejects_invalid_json(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=b"{not json", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_missing_hash_field(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_string_hash(quarantine: Path) -> None:
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": 123, "expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_oversized_body(quarantine: Path) -> None:
    # corps > borne (le verifier ne charge pas un corps illimité en mémoire).
    huge = json.dumps({"hash": "c" * 32, "expected": {"pad": "x" * 200_000}})
    async with _client(quarantine) as client:
        response = await client.post(
            "/verify", content=huge.encode(), headers={"content-type": "application/json"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_non_canonical_hash(quarantine: Path) -> None:
    # un hash hors-canon (traversal/slash) ne doit jamais sortir du dossier de quarantaine.
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "../etc/passwd", "expected": {}})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_defaults_expected_to_empty_mapping(quarantine: Path) -> None:
    (quarantine / ("d" * 32)).write_bytes(b"x")
    async with _client(quarantine) as client:
        response = await client.post("/verify", json={"hash": "d" * 32})  # expected omis
    assert response.status_code == 200
    assert response.json()["verdict"] == "unverified"
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_app.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.app'`.

- [ ] **Step 3 : Écrire `app.py`**

`packages/verifier/src/download_verifier/app.py` :
```python
"""App Starlette du verifier (spec verify §4 — DÉCISION DV1/DV2).

``POST /verify {hash, expected}`` → ``{verdict, real_meta, checks}`` : validation STRICTE et
BORNÉE (corps lu en bytes, taille plafonnée AVANT parse → 400 ; hash canonique exigé pour ne
jamais sortir du dossier de quarantaine — pas de traversal) ; délègue à ``check.verify_file``.
``GET /health`` → 200 (le crawler fail-fast au démarrage si ce health-check échoue, §7).

Stateless / no-DB / no-domain / no-Internet (spec §4) : ne lit que ``quarantine/<hash>`` en RO.
Le dossier de quarantaine vient de la config du service (``QUARANTINE_DIR`` env, défaut
``/quarantine``). ``build_app(quarantine_dir)`` est la fabrique testable ; ``app`` (module-level)
est l'instance que ``uvicorn`` charge par chemin d'import (``download_verifier.app:app``).
"""

import json
import os
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from download_verifier.check import verify_file

# Hash eD2k canonique (32 hex minuscules) : la SEULE forme acceptée → jamais de traversal hors
# du dossier de quarantaine (un "../" ou un "/" ne matche pas et donne 400).
_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")

# Corps borné : un /verify légitime est minuscule ({hash, expected}). 64 Kio est généreux et
# protège d'un corps illimité chargé en mémoire (parsing défensif côté service aussi, §8).
_MAX_BODY_BYTES = 65536


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse({"error": detail}, status_code=400)


async def verify_endpoint(request: Request) -> JSONResponse:
    """``POST /verify`` : valide (strict + borné), vérifie (NO-OP), rend le résultat."""
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return _bad_request("corps trop volumineux")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _bad_request("JSON invalide")
    if not isinstance(payload, dict):
        return _bad_request("objet JSON attendu")
    ed2k_hash = payload.get("hash")
    if not isinstance(ed2k_hash, str) or _CANONICAL_HASH_RE.fullmatch(ed2k_hash) is None:
        return _bad_request("hash canonique requis (32 hex minuscules)")
    expected = payload.get("expected", {})
    if not isinstance(expected, dict):
        return _bad_request("expected doit être un objet")
    verdict, real_meta, checks = verify_file(_quarantine_dir(request) / ed2k_hash, expected)
    return JSONResponse({"verdict": verdict, "real_meta": real_meta, "checks": checks})


async def health_endpoint(request: Request) -> JSONResponse:
    """``GET /health`` → 200 (vivacité du service ; gate full-mode du crawler, §7)."""
    return JSONResponse({"status": "ok"})


def _quarantine_dir(request: Request) -> Path:
    """Dossier de quarantaine injecté dans l'état de l'app (``build_app``)."""
    directory: Path = request.app.state.quarantine_dir
    return directory


def build_app(quarantine_dir: Path) -> Starlette:
    """Fabrique l'app Starlette liée à un dossier de quarantaine (testable in-process)."""
    application = Starlette(
        routes=[
            Route("/verify", verify_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
        ]
    )
    application.state.quarantine_dir = quarantine_dir
    return application


def _quarantine_from_env() -> Path:
    return Path(os.environ.get("QUARANTINE_DIR", "/quarantine"))


app = build_app(_quarantine_from_env())
```

- [ ] **Step 4 : Écrire `__main__.py`**

`packages/verifier/src/download_verifier/__main__.py` :
```python
"""Entrée dev du verifier : ``python -m download_verifier`` (spec verify §4).

Lance uvicorn sur l'app Starlette. L'IMAGE Docker + le compose + le réseau ``internal: true``
sont Plan F ; ici c'est l'entrée locale/dev (et le support de l'e2e si lancé en socket). Le
dossier de quarantaine vient de ``QUARANTINE_DIR`` (lu par ``app.py`` à l'import).
"""

import os

import uvicorn


def main() -> None:
    """Sert l'app verifier (host/port depuis l'environnement, défauts dev)."""
    uvicorn.run(
        "download_verifier.app:app",
        host=os.environ.get("VERIFIER_HOST", "127.0.0.1"),
        port=int(os.environ.get("VERIFIER_PORT", "8000")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
```

> **Note couverture `__main__.py` :** `main()` appelle `uvicorn.run` (effet de bord réseau) — il n'est PAS testé unitairement (comme les entrées CLI du crawler). Pour que `--cov-fail-under=100` passe SANS exclure `main`, ajouter UN test qui appelle `main()` avec `uvicorn.run` monkeypatché (cf. Step 5 ci-dessous). La ligne `if __name__ == "__main__":` porte `# pragma: no cover`.

- [ ] **Step 4bis : Test de `__main__.main` (pour le gate 100 %)**

`packages/verifier/tests/test_main.py` :
```python
import pytest

import download_verifier.__main__ as entry


def test_main_invokes_uvicorn_with_app_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def _fake_run(target: object, **kwargs: object) -> None:
        calls.append((target, kwargs))

    monkeypatch.setattr(entry.uvicorn, "run", _fake_run)
    monkeypatch.setenv("VERIFIER_HOST", "0.0.0.0")
    monkeypatch.setenv("VERIFIER_PORT", "9100")
    entry.main()
    assert calls == [("download_verifier.app:app", {"host": "0.0.0.0", "port": 9100})]
```

- [ ] **Step 5 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest -q --no-cov )` → PASS (check + app + main + package = 4 + 9 + 1 + 1).
Run : gate complet (`( cd packages/verifier && uv run pytest -q )` + ruff/format/mypy racine) → tout vert, **100 % branch** sur `download_verifier`.

> **Note couverture (app) :** `_MAX_BODY_BYTES` dépassé→400 / sous la borne→continue ; `json.loads` lève→400 / OK→continue ; payload non-dict→400 / dict→continue ; `hash` non-str OU non-canonique→400 / canonique→continue ; `expected` non-dict→400 / dict (ou défaut `{}`)→continue ; `verify_file` unverified/error (via fichier présent/absent). `/health`→200. Chaque branche a son test ci-dessus (le `test_verify_defaults_expected…` couvre `expected` omis → `{}`).

- [ ] **Step 6 : Commit**

```bash
git add packages/verifier/src/download_verifier/app.py packages/verifier/src/download_verifier/__main__.py packages/verifier/tests/test_app.py packages/verifier/tests/test_main.py
git commit -m "$(cat <<'EOF'
feat(verifier): app Starlette (POST /verify strict+borné, GET /health) + entrée uvicorn

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 : Port `ContentVerifier` + `VerificationResult` DTO + `VerifierUnavailableError`

**Files :**
- Create: `packages/crawler/src/emule_indexer/ports/content_verifier.py`
- Create: `packages/crawler/src/emule_indexer/ports/verifier_errors.py`
- Create: `packages/crawler/tests/ports/test_content_verifier.py`

> Spec §5 : `ContentVerifier` (Protocol async) `verify(ed2k_hash, expected) -> VerificationResult` ; `health() -> bool`. `VerificationResult` (DTO frozen : `verdict: str`, `real_meta: Mapping[str, object]`, `checks: tuple[object, ...]`). Le contrat d'erreur transitoire `VerifierUnavailableError` vit dans un module ports DÉDIÉ (motif `MuleUnreachableError` : l'adapter http en HÉRITE, la boucle l'attrape sans importer l'adapter, règle de dépendance §4). DÉCISION DV6. Stubs Protocol sur UNE ligne.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/crawler/tests/ports/test_content_verifier.py` :
```python
import dataclasses
from collections.abc import Mapping

import pytest

from emule_indexer.ports.content_verifier import ContentVerifier, VerificationResult
from emule_indexer.ports.verifier_errors import VerifierUnavailableError


class _StubVerifier:
    """Satisfait ContentVerifier structurellement (sans l'importer)."""

    def __init__(self) -> None:
        self.verified: list[tuple[str, Mapping[str, object]]] = []

    async def verify(self, ed2k_hash: str, expected: Mapping[str, object]) -> VerificationResult:
        self.verified.append((ed2k_hash, expected))
        return VerificationResult(verdict="unverified", real_meta={}, checks=())

    async def health(self) -> bool:
        return True


def test_result_is_frozen() -> None:
    result = VerificationResult(verdict="unverified", real_meta={}, checks=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verdict = "error"  # type: ignore[misc]


def test_result_carries_verdict_meta_checks() -> None:
    result = VerificationResult(
        verdict="error", real_meta={"k": 1}, checks=("type_sniff",)
    )
    assert result.verdict == "error"
    assert result.real_meta == {"k": 1}
    assert result.checks == ("type_sniff",)


def test_unavailable_error_is_an_exception() -> None:
    assert issubclass(VerifierUnavailableError, Exception)


@pytest.mark.asyncio
async def test_protocol_is_satisfied_structurally() -> None:
    verifier: ContentVerifier = _StubVerifier()
    result = await verifier.verify("a" * 32, {"target_id": "S2E062A"})
    assert await verifier.health() is True
    assert result.verdict == "unverified"
    assert isinstance(verifier, _StubVerifier)
    assert verifier.verified == [("a" * 32, {"target_id": "S2E062A"})]
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/ports/test_content_verifier.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: …ports.content_verifier`.

- [ ] **Step 3 : Écrire le port d'erreur**

`packages/crawler/src/emule_indexer/ports/verifier_errors.py` :
```python
"""Contrat d'erreur du verifier (spec verify §5/§8 — DÉCISION DV6).

Couche PORTS : le CONTRAT d'erreur transitoire que la boucle de vérification attrape vit au
niveau du port, JAMAIS d'un adapter (règle de dépendance §4, motif ``MuleUnreachableError``).
L'adapter http (``HttpContentVerifier``) LÈVE ``VerifierUnavailableError`` quand le service est
injoignable (connexion refusée / timeout / 5xx) — une panne TRANSITOIRE : la boucle
``fail_verification`` (lease → retry), n'invente AUCUN verdict. Une réponse 200 simplement
malformée n'est PAS transitoire → l'adapter rend un ``VerificationResult(verdict="error")``.
"""


class VerifierUnavailableError(Exception):
    """Le service verifier est injoignable (transitoire) → retry par la boucle (spec §8)."""
```

- [ ] **Step 4 : Écrire le port `ContentVerifier`**

`packages/crawler/src/emule_indexer/ports/content_verifier.py` :
```python
"""Port ``ContentVerifier`` : la vérification d'un fichier en quarantaine (spec verify §5).

Protocol ASYNC (l'adapter fait un RPC HTTP). ``verify`` rend un ``VerificationResult`` (DTO
frozen) ; ``health`` un booléen (vivacité, pour le gate full-mode au démarrage, §7). Le port
n'importe RIEN du verifier (frontière de paquet, DÉCISION DV4) : le DTO ``VerificationResult``
est défini ICI, indépendamment de la forme de résultat du service ; le contrat de fil JSON les
garde en phase (test de contrat + e2e). Le stub ``health`` tient sur UNE ligne ; ``verify``
est WRAPPÉ (signature > 100 cols sur une ligne → ruff E501) mais GARDE le ``: ...`` final sur
la ligne du ``->`` (idiome de couverture : le ``def`` s'exécute à la création de la classe).

``verify`` ne LÈVE pas pour une mauvaise réponse déterministe (→ ``VerificationResult(verdict=
"error")``, enregistré) ; il LÈVE ``VerifierUnavailableError`` (``ports/verifier_errors``)
seulement quand le service est injoignable (transitoire → retry), DÉCISION DV6.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VerificationResult:
    """Résultat d'une vérification (DTO de port, spec §5).

    ``verdict`` : chaîne (en NO-OP : ``unverified``/``error`` ; D-analysis ajoutera ``clean``/
    ``suspicious``/``malicious``). ``real_meta`` : métadonnées média extraites (vide en NO-OP).
    ``checks`` : trace des checks exécutés (vide en NO-OP). Gelé → comparaison par valeur en test.
    Ces trois champs sont EXACTEMENT les colonnes que ``file_verifications`` persiste (verdict/
    real_meta/checks) — ``verified_at``/``node_id`` sont stampés par l'adapter (pas le domaine).
    """

    verdict: str
    real_meta: Mapping[str, object]
    checks: tuple[object, ...]


class ContentVerifier(Protocol):
    """Contrat async de vérification (spec §5). ``verify`` RPC ; ``health`` vivacité (gate §7)."""

    async def verify(
        self, ed2k_hash: str, expected: Mapping[str, object]
    ) -> VerificationResult: ...

    async def health(self) -> bool: ...
```

- [ ] **Step 5 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/ports/test_content_verifier.py -q --no-cov )` → PASS (4 tests).
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture :** DTO frozen (mutation→FrozenInstanceError) ; champs portés ; `VerifierUnavailableError` sous-classe d'`Exception` ; Protocol satisfait structurellement (les deux `def` couverts au chargement de `_StubVerifier`).

- [ ] **Step 6 : Commit**

```bash
git add packages/crawler/src/emule_indexer/ports/content_verifier.py packages/crawler/src/emule_indexer/ports/verifier_errors.py packages/crawler/tests/ports/test_content_verifier.py
git commit -m "$(cat <<'EOF'
feat(ports): ContentVerifier (Protocol async) + VerificationResult + VerifierUnavailableError

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 : Adapter `HttpContentVerifier` (httpx, parsing défensif) + test de contrat

**Files :**
- Create: `packages/crawler/src/emule_indexer/adapters/verifier_http.py`
- Create: `packages/crawler/tests/adapters/test_verifier_http.py`

> Spec §5/§8 : `HttpContentVerifier` (httpx `AsyncClient`) `POST /verify`, `GET /health`. **Parsing défensif** : corps borné, schéma strict ; 200 malformé/hors-schéma/trop gros → `VerificationResult(verdict="error", …)` ; connexion/timeout/5xx → `VerifierUnavailableError` (DÉCISION DV6). **Test de contrat** : testé CONTRE la vraie app Starlette via `ASGITransport` in-process (prouve le contrat de fil DTO↔réponse sans socket/mock). DÉCISION DV2.

- [ ] **Step 0 : RE-confirmer l'API httpx 0.28.x via context7 (avant de coder)**

`uv lock` a résolu **httpx 0.28.1**. Avant d'écrire le code, interroger context7 (`/encode/httpx`) pour CONFIRMER : `httpx.AsyncClient(transport=…, base_url=…)`/`httpx.AsyncClient(base_url=…, timeout=httpx.Timeout(10.0))`, `httpx.ASGITransport(app=app)`, `httpx.MockTransport(handler)`, `response.raise_for_status()`, et la hiérarchie d'exceptions : `httpx.HTTPError` (base de connect/timeout/network), `httpx.HTTPStatusError` (de `raise_for_status()`), `httpx.ConnectError`/`httpx.ReadTimeout`/`httpx.TimeoutException`. (Vérifié à l'écriture : INCHANGÉ en httpx 0.28.1 — re-confirmer au cas où.) `MockTransport` n'appartient PAS au contrat de prod (test seul). Si une signature a bougé, adapter le code en conséquence.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/crawler/tests/adapters/test_verifier_http.py` :
```python
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from download_verifier.app import build_app
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.ports.content_verifier import VerificationResult
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_HASH = "a" * 32


def _verifier_against(app: object) -> HttpContentVerifier:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    return HttpContentVerifier(client)


# ----------------------------------------------------- test de CONTRAT (vraie app Starlette)


@pytest.mark.asyncio
async def test_contract_verify_against_real_app(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"\x00")
    verifier = _verifier_against(build_app(quarantine))
    try:
        result = await verifier.verify(_HASH, {"target_id": "S2E062A"})
    finally:
        await verifier.aclose()
    assert result == VerificationResult(verdict="unverified", real_meta={}, checks=())


@pytest.mark.asyncio
async def test_contract_health_against_real_app(tmp_path: Path) -> None:
    verifier = _verifier_against(build_app(tmp_path))
    try:
        assert await verifier.health() is True
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_contract_missing_file_is_error_verdict(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    verifier = _verifier_against(build_app(quarantine))
    try:
        result = await verifier.verify("b" * 32, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


# ----------------------------------------------------- réponses fabriquées (MockTransport)


def _verifier_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> HttpContentVerifier:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://verifier")
    return HttpContentVerifier(client, max_response_bytes=1024)


@pytest.mark.asyncio
async def test_well_formed_200_maps_to_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"verdict": "unverified", "real_meta": {"x": 1}, "checks": ["c"]}
        )

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result == VerificationResult(verdict="unverified", real_meta={"x": 1}, checks=("c",))


@pytest.mark.asyncio
async def test_malformed_200_missing_verdict_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"real_meta": {}, "checks": []})  # pas de verdict

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_non_json_200_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_oversized_200_body_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        big = {"verdict": "unverified", "real_meta": {"pad": "x" * 5000}, "checks": []}
        return httpx.Response(200, json=big)  # > max_response_bytes=1024

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_verdict_not_a_string_is_error_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"verdict": 5, "real_meta": {}, "checks": []})

    verifier = _verifier_with_handler(handler)
    try:
        result = await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_5xx_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    verifier = _verifier_with_handler(handler)
    try:
        with pytest.raises(VerifierUnavailableError):
            await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_connect_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    verifier = _verifier_with_handler(handler)
    try:
        with pytest.raises(VerifierUnavailableError):
            await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_timeout_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    verifier = _verifier_with_handler(handler)
    try:
        with pytest.raises(VerifierUnavailableError):
            await verifier.verify(_HASH, {})
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_health_returns_false_on_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    verifier = _verifier_with_handler(handler)
    try:
        assert await verifier.health() is False
    finally:
        await verifier.aclose()


@pytest.mark.asyncio
async def test_health_returns_false_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    verifier = _verifier_with_handler(handler)
    try:
        assert await verifier.health() is False
    finally:
        await verifier.aclose()
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/adapters/test_verifier_http.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: …adapters.verifier_http`.

- [ ] **Step 3 : Écrire l'adapter**

`packages/crawler/src/emule_indexer/adapters/verifier_http.py` :
```python
"""Adapter ``HttpContentVerifier`` : RPC HTTP vers le service verifier (spec verify §5/§8).

httpx ``AsyncClient`` sur l'URL du verifier. ``verify`` ``POST /verify {hash, expected}`` ;
``health`` ``GET /health``. PARSING DÉFENSIF (DÉCISION DV6) — deux familles d'échec :
  - service INJOIGNABLE (connexion refusée / timeout / réseau / 5xx) → TRANSITOIRE :
    ``VerifierUnavailableError`` (la boucle ``fail_verification`` → retry via lease) ;
  - réponse 200 MALFORMÉE / hors-schéma / trop grosse → DÉTERMINISTE : on rend un
    ``VerificationResult(verdict="error")`` (enregistré + ``complete`` — pas de boucle infinie).
Le contrat d'erreur transitoire vit dans le PORT (``ports/verifier_errors``) — l'adapter en
hérite/le lève, l'application l'attrape sans importer cet adapter (règle de dépendance §4).

``aclose`` ferme le client httpx (appelé par la composition à l'arrêt). Le DTO crawler est
``ports.content_verifier.VerificationResult`` — défini indépendamment de la réponse du verifier
(frontière de paquet) ; ce module PROUVE le contrat de fil par son test contre la vraie app.
"""

import json
import logging
from collections.abc import Mapping

import httpx

from emule_indexer.ports.content_verifier import VerificationResult
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_logger = logging.getLogger("emule_indexer.adapters.verifier_http")

# Réponse bornée : un /verify NO-OP rend un corps minuscule. 64 Kio protège d'une réponse
# pathologique chargée en mémoire (parsing défensif côté crawler aussi, §8).
_DEFAULT_MAX_RESPONSE_BYTES = 65536

_ERROR_RESULT = VerificationResult(verdict="error", real_meta={}, checks=())


class HttpContentVerifier:
    """Implémentation httpx du port ``ContentVerifier`` (satisfaction STRUCTURELLE)."""

    def __init__(
        self, client: httpx.AsyncClient, *, max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    ) -> None:
        self._client = client
        self._max_response_bytes = max_response_bytes

    async def verify(
        self, ed2k_hash: str, expected: Mapping[str, object]
    ) -> VerificationResult:
        """``POST /verify`` ; injoignable→``VerifierUnavailableError`` ; mauvaise réponse→error."""
        try:
            response = await self._client.post(
                "/verify", json={"hash": ed2k_hash, "expected": dict(expected)}
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            # 4xx/5xx : un 5xx est transitoire ; un 4xx (notre payload rejeté) est un bug de
            # contrat — dans les deux cas on ne fabrique pas de verdict, on remonte transitoire
            # (le 4xx ne se résoudra pas au retry mais finira en dead_letter, visible, §8).
            raise VerifierUnavailableError(
                f"verifier a répondu {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise VerifierUnavailableError(f"verifier injoignable ({error})") from error
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> VerificationResult:
        """Parse défensif d'un 200 : malformé/hors-schéma/trop gros → verdict ``error``."""
        body = response.content
        if len(body) > self._max_response_bytes:
            _logger.warning("réponse verifier trop volumineuse (%d o) — verdict error", len(body))
            return _ERROR_RESULT
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            _logger.warning("réponse verifier non-JSON — verdict error")
            return _ERROR_RESULT
        if not isinstance(payload, dict):
            return _ERROR_RESULT
        verdict = payload.get("verdict")
        if not isinstance(verdict, str):
            return _ERROR_RESULT
        real_meta = payload.get("real_meta", {})
        checks = payload.get("checks", [])
        if not isinstance(real_meta, dict) or not isinstance(checks, list):
            return _ERROR_RESULT
        return VerificationResult(verdict=verdict, real_meta=real_meta, checks=tuple(checks))

    async def health(self) -> bool:
        """``GET /health`` ; ``True`` ssi 2xx, ``False`` sur tout échec (gate full-mode, §7)."""
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def aclose(self) -> None:
        """Ferme le client httpx (appelé par la composition à l'arrêt)."""
        await self._client.aclose()
```

> **Note signature `expected` :** `verify` signe `Mapping[str, object]` (EXACTEMENT le type du port — un `dict` paramètre serait un SOUS-type et NE satisfait PAS le Protocol → erreur mypy au slot composition) ; `dict(expected)` est fait dans le corps pour le JSON du POST. C'est la forme VERBATIM ci-dessus.

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/adapters/test_verifier_http.py -q --no-cov )` → PASS (3 contrat + 10 fabriqués = 13).
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture (hot branches, les deux côtés) :** `raise_for_status` lève `HTTPStatusError` (5xx)→Unavailable / ne lève pas→continue ; `HTTPError` (connect/timeout)→Unavailable / pas d'erreur→continue ; `_parse` : taille > borne→error / ≤→continue ; `json.loads` lève→error / OK→continue ; payload non-dict→error / dict→continue ; `verdict` non-str→error / str→continue ; `real_meta`/`checks` mauvais type→error / OK→VerificationResult. `health` : 2xx→True / HTTPError→False / non-2xx (raise_for_status)→False. Le test de contrat couvre le chemin nominal CONTRE la vraie app (DTO↔réponse).

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/adapters/verifier_http.py packages/crawler/tests/adapters/test_verifier_http.py
git commit -m "$(cat <<'EOF'
feat(adapters): HttpContentVerifier (httpx, parsing défensif) + test de contrat vs vraie app

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

> **Note frontière de paquet (test de contrat) :** le test du crawler importe `download_verifier.app` — c'est un import CÔTÉ TEST uniquement (jamais dans `src/emule_indexer/`, où la frontière de paquet reste stricte). Il fonctionne parce que le workspace venv partagé installe les deux membres (les deux sont dans `[dependency-groups] dev` racine, DÉCISION DV3) ; `( cd packages/crawler && uv run pytest )` voit donc `download_verifier`. C'est délibéré (DÉCISION DV4 : l'e2e/contrat garde les DTO en phase) et NE viole PAS la règle de dépendance (aucun import verifier dans le code de prod du crawler — à vérifier au grep, Task 13).

---

## Task 6 : `record_verification` sur le port catalogue + l'adapter SQLite

**Files :**
- Modify: `packages/crawler/src/emule_indexer/ports/catalog_repository.py` (+ `record_verification`)
- Modify: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`
- Modify: `packages/crawler/tests/ports/test_catalog_repository.py` (le stub gagne la méthode)
- Create: `packages/crawler/tests/adapters/persistence_sqlite/test_catalog_verification.py`

> Spec §5 : `record_verification(ed2k_hash, verdict, real_meta, checks)` → table `file_verifications` (catalogue, **APPEND-ONLY** via trigger, mergeable, taguée `node_id`). Template = `record_decision` : `_CANONICAL_HASH_RE` guard, `wrap_sqlite_errors`, INSERT seul (autocommit), `verified_at=utc_iso(self._clock())`, `node_id=self._node_id`. `real_meta`/`checks` sérialisés via `json.dumps(..., ensure_ascii=False)`. Colonnes existantes (catalog/0001) : `ed2k_hash, verdict, real_meta (nullable JSON), checks (nullable JSON), verified_at, node_id`.

- [ ] **Step 1 : Étendre le test du port (stub + assertion)**

Dans `packages/crawler/tests/ports/test_catalog_repository.py` — ajouter dans la classe `_StubRepository` (après `last_observation`) :
```python
    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: Sequence[object],
    ) -> None:
        self.verifications.append((ed2k_hash, verdict, dict(real_meta), list(checks)))
```
Ajouter l'attribut dans `__init__` du stub : `self.verifications: list[tuple[str, str, dict[str, object], list[object]]] = []`. Étendre les imports du fichier de test : `from collections.abc import Mapping, Sequence`. Dans `test_protocol_is_satisfied_structurally`, juste avant les assertions finales :
```python
    repository.record_verification(observation.ed2k_hash, "unverified", {"k": 1}, ["c"])
    assert repository.verifications == [(observation.ed2k_hash, "unverified", {"k": 1}, ["c"])]
```

- [ ] **Step 2 : Écrire le test de l'adapter (échoue)**

`packages/crawler/tests/adapters/persistence_sqlite/test_catalog_verification.py` :
```python
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.observation import FileObservation

_A = "a" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(hash_hex: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=hash_hex,
        filename="Keroro.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())


def test_record_verification_inserts_a_row(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))  # FK : le fichier doit exister
    repository.record_verification(_A, "unverified", {"duration": 42}, ["type_sniff"])
    row = connection.execute(
        "SELECT ed2k_hash, verdict, real_meta, checks, node_id FROM file_verifications"
    ).fetchone()
    assert row[0] == _A
    assert row[1] == "unverified"
    assert json.loads(row[2]) == {"duration": 42}
    assert json.loads(row[3]) == ["type_sniff"]
    assert row[4] == _NODE


def test_record_verification_stamps_verified_at(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_verification(_A, "unverified", {}, [])
    stamped = connection.execute("SELECT verified_at FROM file_verifications").fetchone()[0]
    assert stamped is not None


def test_record_verification_serializes_empty_meta_and_checks(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_verification(_A, "error", {}, [])
    row = connection.execute("SELECT real_meta, checks FROM file_verifications").fetchone()
    assert json.loads(row[0]) == {}
    assert json.loads(row[1]) == []


def test_record_verification_preserves_non_ascii(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_verification(_A, "unverified", {"titre": "accentué"}, [])
    real_meta = connection.execute("SELECT real_meta FROM file_verifications").fetchone()[0]
    assert "accentué" in real_meta  # ensure_ascii=False

def test_record_verification_rejects_non_canonical_hash(
    repository: SqliteCatalogRepository,
) -> None:
    with pytest.raises(PersistenceError):
        repository.record_verification("NOT-canonical", "unverified", {}, [])


def test_record_verification_unknown_file_raises(repository: SqliteCatalogRepository) -> None:
    # FK violée (fichier jamais observé) → PersistenceError (wrap_sqlite_errors).
    with pytest.raises(PersistenceError):
        repository.record_verification("f" * 32, "unverified", {}, [])
```

- [ ] **Step 3 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_verification.py tests/ports/test_catalog_repository.py -q --no-cov )`
Expected : FAIL — `AttributeError: 'SqliteCatalogRepository' object has no attribute 'record_verification'` (+ stub du port).

- [ ] **Step 4 : Modifier le port `catalog_repository.py`**

(a) Étendre l'import de tête :
```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
```
(b) Ajouter la méthode au Protocol `CatalogRepository` (après `last_observation`) et compléter la docstring de classe d'une phrase :
```python
    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: Sequence[object],
    ) -> None: ...
```
> Mettre à jour la docstring de `CatalogRepository` : ajouter « ``record_verification`` (spec verify §5) append une ligne ``file_verifications`` (catalogue append-only, taguée ``node_id``) — la décision du verdict est prise ailleurs (le verifier), l'adapter ne fait que persister. »

- [ ] **Step 5 : Modifier l'adapter `catalog_repository.py`**

(a) Après `_INSERT_DECISION`, ajouter :
```python
_INSERT_VERIFICATION = """
INSERT INTO file_verifications (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)
VALUES (?, ?, ?, ?, ?, ?)
"""
```
(b) À la FIN de la classe `SqliteCatalogRepository` (après `last_observation`), ajouter :
```python
    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: Sequence[object],
    ) -> None:
        """INSERT seul (autocommit) d'un verdict (spec verify §5). Append-only (trigger).

        Template ``record_decision`` : garde canonique du hash AVANT l'INSERT (un hash non
        canonique est un bug appelant → ``PersistenceError`` clair, pas un diagnostic FK
        opaque) ; ``real_meta``/``checks`` sérialisés JSON (``ensure_ascii=False``, le verdict
        NO-OP les rend vides mais D-analysis les remplira) ; ``verified_at``/``node_id`` stampés
        par l'adapter (le domaine ignore les colonnes de persistance). Fichier inconnu → FK
        violée → ``PersistenceError`` via ``wrap_sqlite_errors``.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"hash eD2k non canonique : {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_VERIFICATION,
                (
                    ed2k_hash,
                    verdict,
                    json.dumps(real_meta, ensure_ascii=False),
                    json.dumps(list(checks), ensure_ascii=False),
                    utc_iso(self._clock()),
                    self._node_id,
                ),
            )
```
(c) Étendre l'import de tête de l'adapter : `from collections.abc import Mapping, Sequence` (à côté des imports existants ; `json` est déjà importé).

> **Note `json.dumps(real_meta)` :** `real_meta` est un `Mapping` ; `json.dumps` accepte un `Mapping`/`dict`. `checks` est une `Sequence` (tuple en prod via le DTO) → `json.dumps(list(checks))` pour un type sérialisable explicite. Vérifié à l'écriture : `json.dumps({}, ensure_ascii=False) == "{}"`, `json.dumps([], …) == "[]"`.

- [ ] **Step 6 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_verification.py tests/ports/test_catalog_repository.py -q --no-cov )` → PASS.
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture :** garde canonique (hash valide→INSERT / invalide→PersistenceError) ; sérialisation meta/checks non vides ET vides ET non-ASCII ; FK violée→PersistenceError. Le stub du port mis à jour garde le Protocol satisfait.

- [ ] **Step 7 : Commit**

```bash
git add packages/crawler/src/emule_indexer/ports/catalog_repository.py packages/crawler/src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py packages/crawler/tests/ports/test_catalog_repository.py packages/crawler/tests/adapters/persistence_sqlite/test_catalog_verification.py
git commit -m "$(cat <<'EOF'
feat(adapters): CatalogRepository.record_verification (file_verifications, append-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 : `get_target_id` sur `SqliteDownloadRepository` (concrétisation 3)

**Files :**
- Modify: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/download_repository.py` (+ `get_target_id`)
- Modify: `packages/crawler/tests/adapters/persistence_sqlite/test_download_repository.py`

> DÉCISION DV11 : la boucle de vérif a besoin du `target_id` d'un hash claimé, mais `ClaimedTask` n'en porte pas et le repo n'avait pas de lookup. Ajouter `get_target_id(ed2k_hash) -> str | None` (`SELECT target_id FROM downloads WHERE ed2k_hash=?`). Le Protocol narrow consommé par la boucle arrive au Task 9.

- [ ] **Step 1 : Étendre le test (échoue)**

Dans `packages/crawler/tests/adapters/persistence_sqlite/test_download_repository.py`, ajouter :
```python
def test_get_target_id_returns_target_for_known_hash(
    repository: SqliteDownloadRepository,
) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    assert repository.get_target_id(_A) == "S2E062A"


def test_get_target_id_is_none_for_unknown_hash(repository: SqliteDownloadRepository) -> None:
    assert repository.get_target_id(_A) is None
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_download_repository.py -q --no-cov )`
Expected : FAIL — `AttributeError: …has no attribute 'get_target_id'`.

- [ ] **Step 3 : Écrire l'implémentation**

Dans `download_repository.py`, après `_ACTIVE_STATES`, ajouter :
```python
_GET_TARGET_ID = "SELECT target_id FROM downloads WHERE ed2k_hash = ?"
```
À la FIN de la classe `SqliteDownloadRepository`, ajouter :
```python
    def get_target_id(self, ed2k_hash: str) -> str | None:
        """``target_id`` d'un hash téléchargé, ou ``None`` (jamais enfilé) — LECTURE.

        La boucle de vérification (spec verify §6, DÉCISION DV11) s'en sert pour bâtir un
        ``expected`` minimal ; le NO-OP l'ignore, D-analysis l'enrichira. ``None`` est un cas
        normal (une tâche peut être claimée pour un hash dont la ligne download a été promue/
        purgée — la boucle bâtit alors ``expected={}``).
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_GET_TARGET_ID, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return str(row[0])
```

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_download_repository.py -q --no-cov )` → PASS.
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture :** `row is None` (hash inconnu→None) / `row is not None` (présent→target_id) — les deux côtés.

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/adapters/persistence_sqlite/download_repository.py packages/crawler/tests/adapters/persistence_sqlite/test_download_repository.py
git commit -m "$(cat <<'EOF'
feat(adapters): SqliteDownloadRepository.get_target_id (lookup hash→target, pour expected)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 : Config — `VerifyConfig` (crawler.yaml) + `verifier_url` (local.yaml)

**Files :**
- Modify: `packages/crawler/src/emule_indexer/adapters/config/crawler_config.py` (+ `VerifyConfig`)
- Modify: `packages/crawler/src/emule_indexer/adapters/config/local_config.py` (+ `verifier_url`)
- Modify: `packages/crawler/tests/adapters/config/test_crawler_config.py`
- Modify: `packages/crawler/tests/adapters/config/test_local_config.py`
- Modify: `config/crawler.yaml`
- Modify: `config/local.example.yaml`

> DÉCISION DV12 (motif D11) : `verify` (crawler) et `verifier_url` (local) sont OPTIONNELS (`None` par défaut) — le crawler observateur tourne sans eux. Présents → validés fail-fast (`ConfigError`, helpers existants). Le câblage live (Task 11) exige leur présence avant d'activer.

- [ ] **Step 1 : Étendre les tests crawler_config (échoue)**

Dans `packages/crawler/tests/adapters/config/test_crawler_config.py`, étendre l'import :
```python
from emule_indexer.adapters.config.crawler_config import (
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
    VerifyConfig,
    parse_crawler_config,
)
```
Ajouter ces tests :
```python
def test_verify_section_is_optional() -> None:
    config = parse_crawler_config(_valid_raw())  # pas de section verify
    assert config.verify is None


def test_verify_section_is_parsed_when_present() -> None:
    raw = _valid_raw()
    raw["verify"] = {"poll_interval_seconds": 5.0}
    config = parse_crawler_config(raw)
    assert config.verify == VerifyConfig(poll_interval_seconds=5.0)


def test_verify_poll_interval_must_be_positive() -> None:
    raw = _valid_raw()
    raw["verify"] = {"poll_interval_seconds": 0.0}
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_verify_poll_interval_key_is_required() -> None:
    raw = _valid_raw()
    raw["verify"] = {}
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        parse_crawler_config(raw)


def test_verify_section_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["verify"] = [1, 2]
    with pytest.raises(ConfigError, match="section 'verify'"):
        parse_crawler_config(raw)
```

- [ ] **Step 2 : Étendre les tests local_config (échoue)**

Dans `packages/crawler/tests/adapters/config/test_local_config.py`, ajouter :
```python
def test_verifier_url_is_optional() -> None:
    config = parse_local_config(_valid_raw())
    assert config.verifier_url is None


def test_verifier_url_is_parsed_when_present() -> None:
    raw = _valid_raw()
    raw["verifier_url"] = "http://verifier:8000"
    config = parse_local_config(raw)
    assert config.verifier_url == "http://verifier:8000"


def test_verifier_url_empty_string_is_fatal() -> None:
    raw = _valid_raw()
    raw["verifier_url"] = ""
    with pytest.raises(ConfigError, match="verifier_url"):
        parse_local_config(raw)


def test_verifier_url_non_string_is_fatal() -> None:
    raw = _valid_raw()
    raw["verifier_url"] = 1234
    with pytest.raises(ConfigError, match="verifier_url"):
        parse_local_config(raw)
```

- [ ] **Step 3 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/adapters/config -q --no-cov )`
Expected : FAIL — `ImportError: cannot import name 'VerifyConfig'` puis attributs absents.

- [ ] **Step 4 : Modifier `crawler_config.py`**

(a) Après la dataclass `DownloadConfig`, ajouter :
```python
@dataclass(frozen=True)
class VerifyConfig:
    """Politique de vérification (spec verify §6). OPTIONNELLE (DÉCISION DV12).

    ``poll_interval_seconds`` : cadence à laquelle la boucle de vérif ``claim`` la file quand
    elle est vide (la file durable est le couplage — pas de nudge dédié, DÉCISION DV5).
    """

    poll_interval_seconds: float
```
(b) Ajouter le champ à `CrawlerConfig` (après `download`) :
```python
    verify: VerifyConfig | None = None
```
(c) Dans `parse_crawler_config`, après le bloc `download`, ajouter :
```python
    verify: VerifyConfig | None = None
    if "verify" in raw:
        verify_raw = _require_mapping(raw["verify"], "section 'verify'")
        verify = VerifyConfig(
            poll_interval_seconds=_positive(verify_raw, "poll_interval_seconds", "verify")
        )
```
et ajouter `verify=verify,` au `CrawlerConfig(...)` retourné.

- [ ] **Step 5 : Modifier `local_config.py`**

(a) Ajouter le champ à `LocalConfig` (après `quarantine_dir`) :
```python
    verifier_url: str | None = None
```
(b) Dans `parse_local_config`, AVANT le `return`, ajouter :
```python
    verifier_url = _require_str(raw, "verifier_url", "local") if "verifier_url" in raw else None
```
et ajouter `verifier_url=verifier_url,` au `LocalConfig(...)` retourné.

> **Note :** `_require_str` rejette déjà la chaîne vide ET le non-str (« chaîne non vide attendue ») → `test_verifier_url_empty_string_is_fatal` et `…non_string…` passent avec le message contenant `verifier_url` (le `what="local"` + la clé). Vérifier que le message d'erreur de `_require_str` inclut la clé `'verifier_url'` (il fait `f"{what}.{key} : …"` → `local.verifier_url`, qui matche `verifier_url`).

- [ ] **Step 6 : Étendre les fichiers de config**

Dans `config/crawler.yaml`, ajouter à la fin :
```yaml

verify:                              # pipeline de vérification (D-verify ; activé live si verifier_url défini)
  poll_interval_seconds: 10.0        # cadence de claim de la file de vérification quand elle est vide
```
Dans `config/local.example.yaml`, ajouter à la fin :
```yaml

# URL du service verifier (D-verify) — sa présence ACTIVE le mode full (download + vérif) ;
# absente = mode observateur (les deux boucles restent OFF). Au démarrage en full, le crawler
# health-check le verifier et REFUSE de démarrer s'il est injoignable (pas de download sans vérif).
# verifier_url: http://verifier:8000
```

- [ ] **Step 7 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/adapters/config -q --no-cov )` → PASS.
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture :** `verify`/`verifier_url` absents (branche `if … in raw` fausse, via `_valid_raw()`) ; présents (nouveaux tests) ; `poll_interval` ≤0 / clé manquante / section non-mapping ; `verifier_url` vide / non-str.

- [ ] **Step 8 : Commit**

```bash
git add packages/crawler/src/emule_indexer/adapters/config/crawler_config.py packages/crawler/src/emule_indexer/adapters/config/local_config.py packages/crawler/tests/adapters/config/test_crawler_config.py packages/crawler/tests/adapters/config/test_local_config.py config/crawler.yaml config/local.example.yaml
git commit -m "$(cat <<'EOF'
feat(config): VerifyConfig (verify.poll_interval) + verifier_url (optionnels)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 : Application — `run_verification_cycle` + `verification_loop`

**Files :**
- Create: `packages/crawler/src/emule_indexer/application/run_verification_cycle.py`
- Create: `packages/crawler/tests/application/test_run_verification_cycle.py`
- Create: `packages/crawler/tests/application/test_verification_loop.py`

> Spec §6/§8 : la boucle MIRROR de `run_download_cycle`/`download_loop`. Une itération : `reclaim_expired()` → `claim_verification()` → (None → sleep `verify.poll_interval` / return) → `get_target_id` → bâtir `expected` minimal → `verify` → `record_verification` → `complete_verification`. Transitoire (`VerifierUnavailableError`/`RepositoryError`) → `fail_verification` (retry/dead-letter). 200 malformé → l'adapter rend déjà `verdict="error"` → enregistré + `complete` (pas un échec, DÉCISION DV6). Déterminisme : `Clock`/`sleep` injectés. `verification_loop` répète jusqu'à un `asyncio.Event` d'arrêt, cancellable au prochain `await`. NarrowDeps + LoopDeps comme download.

> **DÉCISION DV13 — `run_verification_cycle` traite UNE tâche par appel.** Comme `run_download_cycle` fait une itération. La boucle `verification_loop` répète : un cycle traite la tâche claimée (ou dort si la file est vide) ; pas de boucle interne « vider toute la file » (garde l'annulation simple et le test isolé). Le `reclaim_expired` est fait À CHAQUE cycle (au fil de l'eau + au démarrage, spec §8).

- [ ] **Step 1 : Écrire le test de cycle (échoue)**

`packages/crawler/tests/application/test_run_verification_cycle.py` :
```python
import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from emule_indexer.application.run_verification_cycle import VerifyDeps, run_verification_cycle
from emule_indexer.ports.content_verifier import VerificationResult
from emule_indexer.ports.local_state_repository import ClaimedTask
from emule_indexer.ports.repository_errors import RepositoryError
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_A = "a" * 32


class FakeQueue:
    """File de vérification scriptée (sous-ensemble de SqliteLocalStateRepository)."""

    def __init__(self, *, claims: list[ClaimedTask | None] | None = None) -> None:
        self._claims = list(claims or [None])
        self.reclaimed = 0
        self.completed: list[int] = []
        self.failed: list[int] = []

    def reclaim_expired(self) -> int:
        self.reclaimed += 1
        return 0

    def claim_verification(self) -> ClaimedTask | None:
        return self._claims.pop(0) if self._claims else None

    def complete_verification(self, task_id: int) -> None:
        self.completed.append(task_id)

    def fail_verification(self, task_id: int) -> None:
        self.failed.append(task_id)


class FakeTargets:
    """get_target_id scripté (sous-ensemble de SqliteDownloadRepository)."""

    def __init__(self, *, mapping: dict[str, str] | None = None) -> None:
        self._mapping = mapping or {}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        return self._mapping.get(ed2k_hash)


class FakeWriter:
    """record_verification capturé (sous-ensemble de SqliteCatalogRepository)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[tuple[str, str]] = []
        self._fail = fail

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: "list[object] | tuple[object, ...]",
    ) -> None:
        if self._fail:
            raise RepositoryError("écriture verdict échouée")
        self.records.append((ed2k_hash, verdict))


class FakeVerifier:
    """ContentVerifier scripté : verdict en conserve ou erreur transitoire injectée."""

    def __init__(
        self,
        *,
        result: VerificationResult | None = None,
        verify_error: Exception | None = None,
        healthy: bool = True,
    ) -> None:
        self._result = result or VerificationResult(verdict="unverified", real_meta={}, checks=())
        self._verify_error = verify_error
        self._healthy = healthy
        self.verified: list[tuple[str, Mapping[str, object]]] = []

    async def verify(self, ed2k_hash: str, expected: Mapping[str, object]) -> VerificationResult:
        self.verified.append((ed2k_hash, expected))
        if self._verify_error is not None:
            raise self._verify_error
        return self._result

    async def health(self) -> bool:
        return self._healthy


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def _deps(
    *,
    queue: FakeQueue,
    verifier: FakeVerifier,
    writer: FakeWriter,
    targets: FakeTargets,
    clock: FakeClock | None = None,
) -> VerifyDeps:
    return VerifyDeps(
        queue=queue,
        verifier=verifier,
        writer=writer,
        targets=targets,
        poll_interval_seconds=10.0,
        clock=clock or FakeClock(),
    )


@pytest.mark.asyncio
async def test_empty_queue_reclaims_then_sleeps() -> None:
    queue = FakeQueue(claims=[None])
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)
    assert queue.reclaimed == 1
    assert clock.sleeps == [10.0]  # file vide → dort le poll
    assert queue.completed == []


@pytest.mark.asyncio
async def test_claimed_task_is_verified_recorded_completed() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=7, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(
        result=VerificationResult(verdict="unverified", real_meta={}, checks=())
    )
    writer = FakeWriter()
    deps = _deps(
        queue=queue,
        verifier=verifier,
        writer=writer,
        targets=FakeTargets(mapping={_A: "S2E062A"}),
    )
    await run_verification_cycle(deps)
    assert verifier.verified == [(_A, {"target_id": "S2E062A"})]
    assert writer.records == [(_A, "unverified")]
    assert queue.completed == [7]
    assert queue.failed == []


@pytest.mark.asyncio
async def test_expected_is_empty_when_target_unknown() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=1, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier()
    deps = _deps(
        queue=queue,
        verifier=verifier,
        writer=FakeWriter(),
        targets=FakeTargets(mapping={}),  # pas de target connu
    )
    await run_verification_cycle(deps)
    assert verifier.verified == [(_A, {})]  # expected minimal vide (DÉCISION DV11)
    assert queue.completed == [1]


@pytest.mark.asyncio
async def test_error_verdict_is_recorded_and_completed_not_failed() -> None:
    # une réponse 200 malformée arrive en VerificationResult(verdict="error") (adapter) :
    # DÉTERMINISTE → enregistrée + complete, JAMAIS fail (pas de boucle infinie, DÉCISION DV6).
    queue = FakeQueue(claims=[ClaimedTask(task_id=2, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(result=VerificationResult(verdict="error", real_meta={}, checks=()))
    writer = FakeWriter()
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets())
    await run_verification_cycle(deps)
    assert writer.records == [(_A, "error")]
    assert queue.completed == [2]
    assert queue.failed == []


@pytest.mark.asyncio
async def test_unavailable_verifier_fails_the_task() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=3, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier(verify_error=VerifierUnavailableError("down"))
    writer = FakeWriter()
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets())
    await run_verification_cycle(deps)  # ne lève pas
    assert writer.records == []  # pas de verdict inventé
    assert queue.completed == []
    assert queue.failed == [3]  # lease → retry / dead-letter


@pytest.mark.asyncio
async def test_record_failure_fails_the_task() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=4, ed2k_hash=_A, attempts=1)])
    verifier = FakeVerifier()
    writer = FakeWriter(fail=True)  # record_verification lève RepositoryError
    deps = _deps(queue=queue, verifier=verifier, writer=writer, targets=FakeTargets())
    await run_verification_cycle(deps)  # ne lève pas
    assert queue.completed == []
    assert queue.failed == [4]  # retry (le verifier est idempotent/stateless)


@pytest.mark.asyncio
async def test_non_empty_queue_does_not_sleep() -> None:
    queue = FakeQueue(claims=[ClaimedTask(task_id=5, ed2k_hash=_A, attempts=1)])
    clock = FakeClock()
    deps = _deps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        clock=clock,
    )
    await run_verification_cycle(deps)
    assert clock.sleeps == []  # une tâche traitée → pas de sleep de poll
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/application/test_run_verification_cycle.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: …application.run_verification_cycle`.

- [ ] **Step 3 : Écrire l'implémentation (cycle)**

`packages/crawler/src/emule_indexer/application/run_verification_cycle.py` :
```python
"""La boucle de vérification : reclaim → claim → verify → record → complete (spec verify §6).

Couche APPLICATION. CONSOMMATEUR de la file ``verification_tasks`` (le download en est le
PRODUCTEUR — la file durable EST le couplage, DÉCISION DV5 : pas de nudge dédié, le poll est
le filet). ``run_verification_cycle`` traite UNE tâche (ou dort si la file est vide) ;
``verification_loop`` répète jusqu'à un événement d'arrêt — câblé par ``CrawlerApp`` (Task 11).

Flux d'un cycle (spec §6, DÉCISION DV13) :
  1. ``reclaim_expired()`` (récupère les leases expirés au fil de l'eau + au démarrage).
  2. ``claim_verification()`` → ``None`` (file vide) → dort ``poll_interval`` et rend.
  3. Tâche claimée : ``get_target_id`` → ``expected`` MINIMAL (``{"target_id": …}`` ou ``{}``
     si inconnu — le NO-OP l'ignore, D-analysis enrichira, DÉCISION DV11).
  4. ``verify`` → ``VerificationResult`` ; ``record_verification`` ; ``complete_verification``.

Erreurs (DÉCISION DV6, spec §8) : ``VerifierUnavailableError`` (service injoignable) ou
``RepositoryError`` (écriture du verdict échouée) → ``fail_verification`` (lease → retry ;
après ``max_attempts`` → dead-letter, le repo s'en charge). On n'invente JAMAIS de verdict.
Une réponse 200 malformée arrive DÉJÀ en ``VerificationResult(verdict="error")`` (parsing
défensif de l'adapter) → enregistrée + ``complete`` (déterministe, pas de retry). Déterminisme
: ``Clock``/``sleep`` injectés. Writer unique sur l'event loop → aucun verrou.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from emule_indexer.ports.clock import Clock
from emule_indexer.ports.content_verifier import ContentVerifier
from emule_indexer.ports.local_state_repository import ClaimedTask
from emule_indexer.ports.repository_errors import RepositoryError
from emule_indexer.ports.verifier_errors import VerifierUnavailableError

_logger = logging.getLogger("emule_indexer.application.run_verification_cycle")


class VerificationTaskQueue(Protocol):
    """Sous-ensemble de ``LocalStateRepository`` consommé par la boucle (typage local).

    La boucle ne dépend QUE de reclaim/claim/complete/fail (pas de node_id/enqueue) ; le vrai
    ``SqliteLocalStateRepository`` le satisfait, le fake minimal aussi. Stubs sur UNE ligne.
    """

    def reclaim_expired(self) -> int: ...

    def claim_verification(self) -> ClaimedTask | None: ...

    def complete_verification(self, task_id: int) -> None: ...

    def fail_verification(self, task_id: int) -> None: ...


class TargetIdLookup(Protocol):
    """Sous-ensemble de ``SqliteDownloadRepository`` : le lookup hash→target (DÉCISION DV11)."""

    def get_target_id(self, ed2k_hash: str) -> str | None: ...


class VerificationWriter(Protocol):
    """Sous-ensemble de ``CatalogRepository`` : l'écriture du verdict (spec §5)."""

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: tuple[object, ...],
    ) -> None: ...


@dataclass
class VerifyDeps:
    """Dépendances de la boucle de vérification (la composition les assemble une fois).

    ``targets`` est le repo downloads (lookup hash→target pour ``expected``) ; ``writer`` le
    catalogue (``record_verification``) ; ``queue`` la file locale (consommée). Tous typés aux
    Protocols NARROW ci-dessus → les fakes minimaux de test ET les vrais repos les satisfont.
    """

    queue: VerificationTaskQueue
    verifier: ContentVerifier
    writer: VerificationWriter
    targets: TargetIdLookup
    poll_interval_seconds: float
    clock: Clock


def _build_expected(deps: VerifyDeps, ed2k_hash: str) -> dict[str, object]:
    """``expected`` MINIMAL en NO-OP (DÉCISION DV11) : ``{"target_id": …}`` ou ``{}`` si inconnu.

    Le verifier NO-OP l'ignore ; D-analysis l'enrichira (taille/durée/codec attendus). Un
    ``target_id`` absent (tâche pour un hash dont la ligne download a été promue/purgée) → ``{}``.
    """
    target_id = deps.targets.get_target_id(ed2k_hash)
    if target_id is None:
        return {}
    return {"target_id": target_id}


async def run_verification_cycle(deps: VerifyDeps) -> None:
    """UN cycle (spec §6). Reclaim → claim → (vide : sleep) → verify → record → complete.

    Ne lève jamais : un échec transitoire (verifier injoignable / écriture en échec) →
    ``fail_verification`` (retry via lease). La file est la vérité durable, le RPC la vivacité.
    """
    deps.queue.reclaim_expired()
    task = deps.queue.claim_verification()
    if task is None:
        await deps.clock.sleep(deps.poll_interval_seconds)
        return
    expected = _build_expected(deps, task.ed2k_hash)
    try:
        result = await deps.verifier.verify(task.ed2k_hash, expected)
        deps.writer.record_verification(
            task.ed2k_hash, result.verdict, result.real_meta, result.checks
        )
    except VerifierUnavailableError as error:
        _logger.warning(
            "verifier injoignable pour task=%d hash=%s (%s) — fail (retry via lease)",
            task.task_id,
            task.ed2k_hash,
            error,
        )
        deps.queue.fail_verification(task.task_id)
        return
    except RepositoryError as error:
        _logger.error(
            "écriture du verdict échouée pour task=%d hash=%s (%s) — fail (retry)",
            task.task_id,
            task.ed2k_hash,
            error,
        )
        deps.queue.fail_verification(task.task_id)
        return
    deps.queue.complete_verification(task.task_id)
    _logger.info(
        "task=%d hash=%s vérifiée (verdict=%s)", task.task_id, task.ed2k_hash, result.verdict
    )
```

- [ ] **Step 4 : Écrire le test de boucle (échoue)**

`packages/crawler/tests/application/test_verification_loop.py` :
```python
import asyncio

import pytest

from emule_indexer.application.run_verification_cycle import (
    VerifyLoopDeps,
    verification_loop,
)
from emule_indexer.ports.local_state_repository import ClaimedTask

from tests.application.test_run_verification_cycle import (
    FakeClock,
    FakeQueue,
    FakeTargets,
    FakeVerifier,
    FakeWriter,
)

_A = "a" * 32


def _loop_deps(
    *, queue: FakeQueue, shutdown: asyncio.Event, clock: FakeClock | None = None
) -> VerifyLoopDeps:
    return VerifyLoopDeps(
        queue=queue,
        verifier=FakeVerifier(),
        writer=FakeWriter(),
        targets=FakeTargets(),
        poll_interval_seconds=10.0,
        clock=clock or FakeClock(),
        shutdown=shutdown,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_shutdown_is_set_before_start() -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    queue = FakeQueue(claims=[None])
    deps = _loop_deps(queue=queue, shutdown=shutdown)
    await asyncio.wait_for(verification_loop(deps), timeout=1.0)
    assert queue.reclaimed == 0  # aucun cycle


@pytest.mark.asyncio
async def test_loop_runs_cycles_then_stops() -> None:
    shutdown = asyncio.Event()
    clock = FakeClock()
    queue = FakeQueue(claims=[None, None, None])  # file vide → dort à chaque cycle
    deps = _loop_deps(queue=queue, shutdown=shutdown, clock=clock)

    async def stop_after_first_sleep() -> None:
        while not clock.sleeps:
            await asyncio.sleep(0)
        shutdown.set()

    await asyncio.gather(
        asyncio.wait_for(verification_loop(deps), timeout=2.0), stop_after_first_sleep()
    )
    assert queue.reclaimed >= 1
    assert clock.sleeps  # au moins un sleep de poll


class _ShutdownDuringCycleQueue(FakeQueue):
    """Pose ``shutdown`` au 1er reclaim (PENDANT le cycle) → break sans sleep résiduel."""

    def __init__(self, shutdown: asyncio.Event) -> None:
        super().__init__(claims=[ClaimedTask(task_id=1, ed2k_hash=_A, attempts=1)])
        self._shutdown = shutdown

    def reclaim_expired(self) -> int:
        self._shutdown.set()
        return super().reclaim_expired()


@pytest.mark.asyncio
async def test_loop_breaks_when_shutdown_is_set_during_the_cycle() -> None:
    shutdown = asyncio.Event()
    clock = FakeClock()
    queue = _ShutdownDuringCycleQueue(shutdown)
    deps = _loop_deps(queue=queue, shutdown=shutdown, clock=clock)
    await asyncio.wait_for(verification_loop(deps), timeout=1.0)
    assert queue.reclaimed == 1  # un seul cycle, puis break (shutdown posé pendant)
```

- [ ] **Step 5 : Écrire la boucle (étend `run_verification_cycle.py`)**

À la FIN de `run_verification_cycle.py`, ajouter :
```python
@dataclass
class VerifyLoopDeps(VerifyDeps):
    """``VerifyDeps`` + l'arrêt (DÉCISION DV13). La file est le couplage → pas de nudge dédié."""

    shutdown: "asyncio.Event"


async def verification_loop(deps: VerifyLoopDeps) -> None:
    """Répète ``run_verification_cycle`` jusqu'à l'arrêt (spec §6/§7).

    Câblée par ``CrawlerApp`` (Task 11) dans le ``TaskGroup`` ; l'annulation (arrêt) atterrit
    au prochain ``await`` (le RPC ``verify`` ou le sleep de poll), jamais en pleine écriture DB
    (repos sync). Le ``if deps.shutdown.is_set(): break`` post-cycle évite un cycle de plus
    quand l'arrêt est demandé PENDANT le cycle.
    """
    while not deps.shutdown.is_set():
        await run_verification_cycle(deps)
        if deps.shutdown.is_set():
            break
```
Et ajouter `import asyncio` en tête du module (pour `asyncio.Event` dans l'annotation — annotée en chaîne `"asyncio.Event"` ou nue puisque `asyncio` est importé).

> **Note typage :** `VerifyLoopDeps` hérite de `VerifyDeps` (dataclass) ; `run_verification_cycle(deps)` accepte un `VerifyLoopDeps` (sous-type). L'`import asyncio` en tête suffit pour `asyncio.Event` (annotation nue ou string). Le test `from tests.application.test_run_verification_cycle import …` réutilise les fakes (laisser `ruff --fix` classer l'import).

- [ ] **Step 6 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/application/test_run_verification_cycle.py tests/application/test_verification_loop.py -q --no-cov )` → PASS (7 + 3).
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture (cycle) :** `claim` None→sleep+return / tâche→suite ; `_build_expected` target trouvé→{target_id} / None→{} ; `verify`+`record` OK→complete ; `VerifierUnavailableError`→fail / `RepositoryError`→fail ; verdict "error" (200 malformé) → record+complete (pas fail). `verification_loop` : shutdown avant start→0 cycle / cycle→re-test while / shutdown pendant cycle→break.

- [ ] **Step 7 : Commit**

```bash
git add packages/crawler/src/emule_indexer/application/run_verification_cycle.py packages/crawler/tests/application/test_run_verification_cycle.py packages/crawler/tests/application/test_verification_loop.py
git commit -m "$(cat <<'EOF'
feat(application): run_verification_cycle + verification_loop (consommateur de la file, tolérant)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 : Câblage du nudge download producteur (concrétisation 1)

**Files :**
- Modify: `packages/crawler/src/emule_indexer/application/record_observations.py` (+ `signal("download")` sur tier=download)
- Modify: `packages/crawler/tests/application/test_record_observations.py`

> DÉCISION DV9 (handoff §3.2) : la boucle de download s'abonne au sujet FIXE `DOWNLOAD_NUDGE_SUBJECT="download"`. Ajouter un `signal(DOWNLOAD_NUDGE_SUBJECT)` dans `record_observations` **quand le verdict enregistré est de tier "download"** (en plus du `signal(ed2k_hash)` existant pour le consommateur Plan C). Minimal et inoffensif (le poll de repli couvre déjà ; un nudge perdu est sans conséquence).

- [ ] **Step 1 : Mettre à jour les assertions EXISTANTES + écrire les 2 nouveaux tests (échoue)**

> **D'ABORD relire** `packages/crawler/tests/application/test_record_observations.py` : le faux hub est `RecordingSignal` (de `tests.application.fakes`), il capture les sujets dans une **liste PLATE** `signalled` ; les fixtures `catalog`/`catalog_connection`/`engine` viennent de `tests/application/conftest.py` ; helpers `_obs(ed2k_hash, filename)`, `_HASH_DL = "31d6cfe0…"` (le moteur lui donne tier `download` avec `_DL_NAME`), `_HASH_CAT = "aaaa…"` (tier `catalog`). Le nouveau `signal(DOWNLOAD_NUDGE_SUBJECT)` sur tier=download CASSE deux assertions d'égalité exacte existantes — il FAUT les corriger.

**(a) Étendre l'import** en tête du fichier :
```python
from emule_indexer.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
```

**(b) Corriger l'assertion de `test_new_verdict_is_persisted_and_nudged`** (verdict tier=download → le sujet download s'ajoute APRÈS le sujet par hash) :
```python
    assert signal.signalled == [_HASH_DL, DOWNLOAD_NUDGE_SUBJECT]
```
(remplace l'ancien `assert signal.signalled == [_HASH_DL]`.)

**(c) Corriger l'assertion de `test_changed_verdict_is_reappended_and_nudged_again`** (1re vue = tier catalog → PAS de nudge download ; 2e vue = tier download → nudge download) :
```python
    assert signal.signalled == [_HASH_DL, _HASH_DL, DOWNLOAD_NUDGE_SUBJECT]
```
(remplace l'ancien `assert signal.signalled == [_HASH_DL, _HASH_DL]`.)

> `test_unchanged_verdict_is_not_reappended_or_nudged` (tier catalog, `_HASH_CAT`) → `signalled == [_HASH_CAT]` INCHANGÉ (pas de nudge download). `test_observation_is_always_recorded_even_when_discarded` (`_HASH_DISCARD`) → `[]` INCHANGÉ. `test_persistence_error_is_absorbed…` → `[]` INCHANGÉ.

**(d) Ajouter les deux NOUVEAUX tests** (à la fin du fichier ; mêmes fixtures que les tests existants) :
```python
def test_download_tier_verdict_also_nudges_the_download_subject(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    # un NOUVEAU verdict de tier "download" signale le sujet par hash PUIS le sujet "download"
    # (réveille la boucle de download, DÉCISION DV9) — dans cet ordre.
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is True
    assert signal.signalled == [_HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


def test_non_download_tier_verdict_does_not_nudge_the_download_subject(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    # un verdict de tier "catalog" signale le sujet par hash mais JAMAIS le sujet "download".
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_CAT, "keroro something.avi"), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is True
    assert signal.signalled == [_HASH_CAT]
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/application/test_record_observations.py -q --no-cov )`
Expected : FAIL — les deux assertions corrigées (b)/(c) + `test_download_tier_verdict_also_nudges_the_download_subject` échouent (le sujet "download" n'est pas encore signalé : `signalled == [_HASH_DL]`, pas `[_HASH_DL, DOWNLOAD_NUDGE_SUBJECT]`).

- [ ] **Step 3 : Modifier `record_observations.py`**

(a) Étendre les imports :
```python
from emule_indexer.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
```
(b) Juste après `signal.signal(observation.ed2k_hash)` (avant `return True`), ajouter :
```python
    if decision.tier == "download":
        # Nudge le sujet conventionnel "download" (DÉCISION DV9) : la boucle de download s'y
        # abonne et rejoue le journal dès qu'un verdict download change. Best-effort (le poll
        # de repli reste le filet) — un nudge perdu est inoffensif (même contrat que le hash).
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)
```

> **Note règle de dépendance :** `record_observations` (application) importe `DOWNLOAD_NUDGE_SUBJECT` depuis `application/run_download_cycle.py` (même couche application) — LICITE (pas un adapter). Le sujet est une constante d'application, pas un port. Vérifié : pas de cycle d'import (`run_download_cycle` n'importe pas `record_observations`).

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/application/test_record_observations.py -q --no-cov )` → PASS.
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

> **Note couverture :** `decision.tier == "download"` (vrai→signal download / faux→pas de signal download) — les deux côtés, par les deux nouveaux tests. Les tests existants restent verts APRÈS les corrections (b)/(c) des assertions d'égalité (l'ajout du sujet "download" change `signalled` pour les verdicts tier=download — c'est attendu, pas une régression).

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/application/record_observations.py packages/crawler/tests/application/test_record_observations.py
git commit -m "$(cat <<'EOF'
feat(application): nudge le sujet "download" quand un verdict tier=download change

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11 : Composition — gate full-mode + câblage LIVE des DEUX boucles

**Files :**
- Modify: `packages/crawler/src/emule_indexer/composition/app.py`
- Modify: `packages/crawler/src/emule_indexer/composition/__main__.py` (factories de download/verifier par défaut)
- Modify: `packages/crawler/tests/composition/test_app.py`

> DÉCISION DV7/DV8/DV10. `verifier_url` présent → **full** : valider l'ensemble complet de la config download (lacune unidirectionnelle T10), construire `HttpContentVerifier` → `health()` → injoignable ⇒ **fail-fast** (lève → `run` remonte → refus de démarrer) ; construire le client EC download (connexion distincte, tolère `MuleUnreachableError`), `FilesystemQuarantine`, `SqliteDownloadRepository`, le `staging_path_for` (DÉCISION DV10), et lancer `download_loop` + `verification_loop` comme tâches du `TaskGroup` de `_supervise`. `verifier_url` absent → **observateur** : les deux boucles OFF (comportement Plan C inchangé). Repos UNIQUES partagés. L'arrêt observable existant annule les nouvelles tâches au prochain `await` (déjà la mécanique du `loop_task`).

> **DÉCISION DV14 — Factories injectables pour download/verifier (testabilité).** Comme `client_factory` (search) est injecté pour substituer un faux, `CrawlerApp` gagne `download_client_factory: ClientFactory` (réutilise le type, un `MuleClient`/`MuleDownloadClient` — `AmuleEcClient` satisfait les deux) et `verifier_factory: Callable[[str], ContentVerifier]` (`str` = `verifier_url`). Les tests passent un faux verifier (santé scriptable) + un faux client download. Les défauts (composition réelle) construisent `AmuleEcClient` et `HttpContentVerifier(httpx.AsyncClient(base_url=url, timeout=…))`.

> **DÉCISION DV15 — Validation full-mode = fail-fast au montage (handoff §3.5).** En full, AVANT d'activer les boucles, `CrawlerApp` exige l'ensemble : `crawler_config.verify` ET `crawler_config.download` ET `local_config.download_endpoint` ET `local_config.staging_dir` ET `local_config.quarantine_dir`. Un manque → `ConfigError` (refus de démarrer) — le crawler ne télécharge JAMAIS sans pouvoir vérifier ni sans staging/quarantaine. La couture est : `verifier_url` est le DÉCLENCHEUR du mode full ; le reste DOIT suivre.

- [ ] **Step 1 : Écrire les tests (échouent)**

Dans `packages/crawler/tests/composition/test_app.py`, ajouter (réutilise `_make_app`/fakes ; étend les helpers pour passer la config download/verify + les factories). Bloc de helpers en tête (après `_local_config`) :
```python
from emule_indexer.adapters.config.crawler_config import DownloadConfig, VerifyConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint  # déjà importé
from emule_indexer.ports.content_verifier import ContentVerifier, VerificationResult
from emule_indexer.ports.mule_download_client import DownloadEntry


class FakeContentVerifier:
    """ContentVerifier de test : santé scriptable, verdict NO-OP."""

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy
        self.closed = False

    async def verify(self, ed2k_hash: str, expected: object) -> VerificationResult:
        return VerificationResult(verdict="unverified", real_meta={}, checks=())

    async def health(self) -> bool:
        return self._healthy

    async def aclose(self) -> None:
        self.closed = True


class FakeDownloadClient(FakeMuleClient):
    """Client download de test : satisfait aussi add_link/download_queue (no-op)."""

    async def add_link(self, ed2k_link: str) -> None:
        return None

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        return ()


class _UnreachableDownloadClient(FakeDownloadClient):
    """Client download dont ``connect`` lève ``MuleUnreachableError`` (daemon down au démarrage)."""

    async def connect(self) -> None:
        raise MuleUnreachableError("download daemon down")


def _full_local_config(tmp_path: Path, *, verifier_url: str | None) -> LocalConfig:
    base = _local_config(tmp_path)
    staging = tmp_path / "staging"
    quarantine = tmp_path / "quarantine"
    staging.mkdir(exist_ok=True)
    quarantine.mkdir(exist_ok=True)
    return LocalConfig(
        amules=base.amules,
        catalog_db_path=base.catalog_db_path,
        local_db_path=base.local_db_path,
        node_id=base.node_id,
        download_endpoint=AmuleEndpoint(name="dl", host="h", port=4799, password="p"),
        staging_dir=str(staging),
        quarantine_dir=str(quarantine),
        verifier_url=verifier_url,
    )


def _full_crawler_config() -> CrawlerConfig:
    base = _crawler_config()
    return CrawlerConfig(
        cycle_interval_seconds=base.cycle_interval_seconds,
        search_poll_budget_seconds=base.search_poll_budget_seconds,
        search_poll_interval_seconds=base.search_poll_interval_seconds,
        keyword_pause_min_seconds=base.keyword_pause_min_seconds,
        keyword_pause_max_seconds=base.keyword_pause_max_seconds,
        backoff=base.backoff,
        decision_poll_interval_seconds=base.decision_poll_interval_seconds,
        shutdown_deadline_seconds=base.shutdown_deadline_seconds,
        download=DownloadConfig(poll_interval_seconds=30.0, disk_cap_bytes=1_000_000_000),
        verify=VerifyConfig(poll_interval_seconds=10.0),
    )
```
Tests (réutilisent `_ShutdownOnStatusClient` pour borner le run à un cycle) :
```python
@pytest.mark.asyncio
async def test_observer_mode_runs_without_download_or_verify_loops(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # verifier_url absent → observateur : démarre, tourne un cycle, s'arrête ; aucun verifier
    # construit, aucune boucle download/verif. (Comportement Plan C inchangé.)
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier()

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(),
        local_config=_local_config(tmp_path),  # pas de verifier_url
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=factory,  # type: ignore[arg-type]
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert verifier.closed is False  # observateur : le verifier n'est jamais utilisé/fermé


@pytest.mark.asyncio
async def test_full_mode_health_ok_runs_both_loops(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier(healthy=True)

    def search_factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=search_factory,  # type: ignore[arg-type]
        download_client_factory=lambda endpoint: FakeDownloadClient(),  # type: ignore[arg-type]
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    # full : le verifier a été health-checké et fermé proprement à l'arrêt.
    assert verifier.closed is True


@pytest.mark.asyncio
async def test_full_mode_health_failure_is_fail_fast(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    verifier = FakeContentVerifier(healthy=False)  # health() → False → fail-fast

    def search_factory(endpoint: AmuleEndpoint) -> FakeMuleClient:
        return FakeMuleClient()

    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=search_factory,  # type: ignore[arg-type]
        download_client_factory=lambda endpoint: FakeDownloadClient(),  # type: ignore[arg-type]
        verifier_factory=lambda url: verifier,
    )
    with pytest.raises(ConfigError, match="verifier"):
        await app.run()
    assert verifier.closed is True  # le client verifier est fermé même en fail-fast


@pytest.mark.asyncio
async def test_full_mode_missing_download_config_is_fail_fast(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # verifier_url présent MAIS l'ensemble download incomplet (download_endpoint absent) →
    # fail-fast au montage (handoff §3.5 : on ne télécharge jamais sans la config complète).
    local = _local_config(tmp_path)
    local = LocalConfig(
        amules=local.amules,
        catalog_db_path=local.catalog_db_path,
        local_db_path=local.local_db_path,
        node_id=local.node_id,
        verifier_url="http://verifier:8000",  # full déclenché, mais pas d'endpoint/dirs
    )
    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=local,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=lambda endpoint: FakeMuleClient(),  # type: ignore[arg-type]
        verifier_factory=lambda url: FakeContentVerifier(),
    )
    with pytest.raises(ConfigError, match="download"):
        await app.run()


@pytest.mark.asyncio
async def test_full_mode_tolerates_download_daemon_unreachable_at_startup(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # le daemon download injoignable au démarrage est TOLÉRÉ (handoff / DV8) : on n'échoue
    # PAS, les boucles sont quand même armées (le backoff de la boucle gouverne les retries).
    holder: dict[str, CrawlerApp] = {}
    verifier = FakeContentVerifier(healthy=True)

    def search_factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def download_factory(endpoint: AmuleEndpoint) -> _UnreachableDownloadClient:
        return _UnreachableDownloadClient()

    app = CrawlerApp(
        crawler_config=_full_crawler_config(),
        local_config=_full_local_config(tmp_path, verifier_url="http://verifier:8000"),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=search_factory,  # type: ignore[arg-type]
        download_client_factory=download_factory,  # type: ignore[arg-type]
        verifier_factory=lambda url: verifier,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)  # ne lève pas : connect toléré
    assert verifier.closed is True  # full a démarré (boucles armées), verifier fermé à l'arrêt
```
> **Consigne implémenteur :** importer `ConfigError` (depuis `emule_indexer.adapters.config.crawler_config`) + `MuleUnreachableError` (`emule_indexer.ports.mule_client`, déjà importé dans ce fichier) dans le test. Adapter les `# type: ignore` au strict nécessaire.

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/crawler && uv run pytest tests/composition/test_app.py -q --no-cov )`
Expected : FAIL — `TypeError: CrawlerApp.__init__() got an unexpected keyword argument 'verifier_factory'`.

- [ ] **Step 3 : Modifier `composition/app.py`**

(a) Étendre les imports (en tête) :
```python
import httpx

from emule_indexer.adapters.config.crawler_config import ConfigError, CrawlerConfig
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.quarantine_fs import FilesystemQuarantine
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.application.run_download_cycle import (
    CatalogReader,
    DownloadLoopDeps,
    download_loop,
)
from emule_indexer.application.run_verification_cycle import VerifyLoopDeps, verification_loop
from emule_indexer.ports.content_verifier import ContentVerifier
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient
```
(`asyncio`, `Callable`, `Path`, `MuleClient`, `MuleUnreachableError`, etc. déjà importés ; ajouter `from pathlib import Path` si absent.)

(b) Après `ClientFactory = Callable[[AmuleEndpoint], MuleClient]`, ajouter les types/factories :
```python
# Factory du client de DOWNLOAD : même type d'endpoint, mais le client satisfait
# MuleDownloadClient (AmuleEcClient satisfait les deux Protocols structurellement, DÉCISION D3).
DownloadClientFactory = Callable[[AmuleEndpoint], MuleDownloadClient]
# Factory du verifier : prend l'URL (verifier_url) et rend un ContentVerifier.
VerifierFactory = Callable[[str], ContentVerifier]


def default_download_client_factory(endpoint: AmuleEndpoint) -> MuleDownloadClient:
    """Un ``AmuleEcClient`` dédié au download (connexion EC distincte, DÉCISION D3)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


def default_verifier_factory(verifier_url: str) -> ContentVerifier:
    """Un ``HttpContentVerifier`` httpx sur l'URL du verifier (timeout dev raisonnable)."""
    client = httpx.AsyncClient(base_url=verifier_url, timeout=httpx.Timeout(10.0))
    return HttpContentVerifier(client)
```

(c) Étendre `__init__` : ajouter les paramètres et les stocker :
```python
        download_client_factory: DownloadClientFactory = default_download_client_factory,
        verifier_factory: VerifierFactory = default_verifier_factory,
```
(après `client_factory`), avec :
```python
        self._download_client_factory = download_client_factory
        self._verifier_factory = verifier_factory
```

(d) Ajouter une méthode de gate full-mode + de construction des deps des deux boucles. Insérer (après `default_client_factory` au niveau classe, p.ex. après `_build_policy`/avant `run`) :
```python
    def _require_full_config(self) -> None:
        """Fail-fast au montage si le mode full est déclenché sans config complète (DV15).

        ``verifier_url`` est le DÉCLENCHEUR du mode full ; l'ensemble download DOIT suivre
        (handoff §3.5 — la lacune unidirectionnelle du parser : des dirs sans endpoint sont
        ignorés, mais un crawler full SANS endpoint/dirs/section download/verify ne doit PAS
        démarrer : on ne télécharge jamais sans pouvoir vérifier ni sans staging/quarantaine).
        """
        missing: list[str] = []
        if self._crawler_config.verify is None:
            missing.append("crawler.verify")
        if self._crawler_config.download is None:
            missing.append("crawler.download")
        if self._local_config.download_endpoint is None:
            missing.append("local.download_endpoint")
        if self._local_config.staging_dir is None:
            missing.append("local.staging_dir")
        if self._local_config.quarantine_dir is None:
            missing.append("local.quarantine_dir")
        if missing:
            raise ConfigError(
                "mode full (verifier_url défini) exige aussi : "
                + ", ".join(missing)
                + " (refus de télécharger sans config complète)"
            )
```

(e) Modifier `run` : entre la construction des repos partagés et l'entrée dans `asyncio.timeout`, brancher le mode full. La logique : si `verifier_url` est défini → `_require_full_config()` ; construire le verifier via la factory, push son `aclose` sur le stack, `health()` → si `False` lève `ConfigError` (fail-fast) ; construire le client download (push close, connect tolérant `MuleUnreachableError`), `SqliteDownloadRepository(local_conn)`, `FilesystemQuarantine(Path(quarantine_dir))`, le `staging_path_for` (DÉCISION DV10), assembler `DownloadLoopDeps` et `VerifyLoopDeps`. Passer ces deps (ou `None`) à `_supervise`.

Remplacer le bloc d'appel à `_supervise` (lignes ~266-274 actuelles) et l'amont par :
```python
            verifier: ContentVerifier | None = None
            download_deps: DownloadLoopDeps | None = None
            verify_deps: VerifyLoopDeps | None = None
            if self._local_config.verifier_url is not None:
                self._require_full_config()
                verifier = self._verifier_factory(self._local_config.verifier_url)
                # Ferme le client verifier au teardown. Le port ``ContentVerifier`` ne déclare
                # PAS ``aclose`` (détail d'adapter http) → ``# type: ignore`` documenté ; toute
                # impl passée à la composition (HttpContentVerifier, faux de test) l'expose
                # (DÉCISION DV16 : pas de getattr/branche → pas de branche partielle à couvrir).
                stack.push_async_callback(verifier.aclose)  # type: ignore[attr-defined]
                if not await verifier.health():
                    raise ConfigError(
                        "verifier injoignable au démarrage (health-check KO) — "
                        "refus de démarrer en mode full"
                    )
                download_deps, verify_deps = await self._build_full_loops(
                    stack=stack,
                    catalog_repo=catalog_repo,
                    local_repo=local_repo,
                    local_conn=local_conn,
                    verifier=verifier,
                )
                _logger.info("mode full : boucles download + vérification armées")

            async with asyncio.timeout(None) as shutdown_timeout:
                await self._supervise(
                    shutdown_timeout=shutdown_timeout,
                    workers=workers,
                    clients=clients,
                    node_id=node_id,
                    scheduler_state=scheduler_state,
                    backoff=backoff,
                    download_deps=download_deps,
                    verify_deps=verify_deps,
                )
                _human(f"{len(clients)} connexion(s) EC en fermeture…")
                await stack.aclose()
                _human("Bases fermées — sortie.")
```
> Le `node_id` et `catalog_repo` sont déjà construits plus haut (réutilisés). Le teardown du verifier est `stack.push_async_callback(verifier.aclose)` direct (DV16) — PAS de helper conditionnel.

(f) Ajouter la méthode `_build_full_loops` (construit les deps des deux boucles ; repos uniques partagés) :
```python
    async def _build_full_loops(
        self,
        *,
        stack: AsyncExitStack,
        catalog_repo: SqliteCatalogRepository,
        local_repo: SqliteLocalStateRepository,
        local_conn: "sqlite3.Connection",
        verifier: ContentVerifier,
    ) -> tuple[DownloadLoopDeps, VerifyLoopDeps]:
        """Assemble les deps des boucles download + vérification (mode full, spec §7).

        Repos UNIQUES partagés (``catalog_repo``/``local_repo`` déjà construits ; un
        ``SqliteDownloadRepository`` sur la MÊME ``local_conn`` — writer unique sur l'event
        loop, aucune course). Une 2e connexion EC (``download_endpoint``) connectée en tolérant
        ``MuleUnreachableError`` (un daemon down au démarrage ne tue pas le crawler ; le backoff
        de la boucle gouverne). ``staging_path_for`` dérive le chemin du fichier complété du
        ``staging_dir`` configuré + le filename de la dernière observation (DÉCISION DV10 ;
        ``None`` → chemin best-effort qui échouera à ``os.replace``, laissant ``completed``).
        """
        endpoint = self._local_config.download_endpoint
        assert endpoint is not None  # garanti par _require_full_config (mypy : narrow)
        staging_dir = self._local_config.staging_dir
        quarantine_dir = self._local_config.quarantine_dir
        assert staging_dir is not None and quarantine_dir is not None
        download_config = self._crawler_config.download
        verify_config = self._crawler_config.verify
        assert download_config is not None and verify_config is not None

        download_client = self._download_client_factory(endpoint)
        stack.push_async_callback(download_client.close)
        try:
            await download_client.connect()
        except MuleUnreachableError as error:
            _logger.warning(
                "daemon download injoignable au démarrage (%s) — toléré, retry par la boucle",
                error,
            )
        downloads_repo = SqliteDownloadRepository(local_conn)
        quarantine = FilesystemQuarantine(Path(quarantine_dir))
        staging_base = Path(staging_dir)
        # ``resolve_staging_path`` est une fonction MODULE-LEVEL (unit-testée à 100 % branch,
        # test_staging_resolver.py) — le lambda ne fait que la lier au staging + catalogue
        # (DÉCISION DV10 ; observation None → chemin sous staging par hash, best-effort).
        download_deps = DownloadLoopDeps(
            client=download_client,
            quarantine=quarantine,
            downloads=downloads_repo,
            catalog=catalog_repo,
            local=local_repo,
            targets=self._targets,
            disk_cap_bytes=download_config.disk_cap_bytes,
            staging_path_for=lambda entry: resolve_staging_path(staging_base, catalog_repo, entry),
            clock=self._clock,
            signal=self._signal,
            poll_interval_seconds=download_config.poll_interval_seconds,
            shutdown=self._shutdown,
        )
        verify_deps = VerifyLoopDeps(
            queue=local_repo,
            verifier=verifier,
            writer=catalog_repo,
            targets=downloads_repo,
            poll_interval_seconds=verify_config.poll_interval_seconds,
            clock=self._clock,
            shutdown=self._shutdown,
        )
        return download_deps, verify_deps
```

(g) Étendre `_supervise` pour lancer les deux boucles dans le `TaskGroup`. Ajouter les paramètres `download_deps: DownloadLoopDeps | None` et `verify_deps: VerifyLoopDeps | None` à sa signature, et DANS le `async with asyncio.TaskGroup() as group:`, après la création de `loop_task` :
```python
            if download_deps is not None:
                group.create_task(download_loop(download_deps))
            if verify_deps is not None:
                group.create_task(verification_loop(verify_deps))
```
> Ces tâches s'arrêtent comme `loop_task` : elles surveillent `deps.shutdown` (le MÊME `self._shutdown`) et sont annulées au prochain `await` à la sortie du `async with` (l'arrêt observable existant). Le `loop_task.cancel()` post-`shutdown.wait()` n'annule QUE le search loop ; les deux nouvelles boucles voient `shutdown.is_set()` et sortent d'elles-mêmes (`while not deps.shutdown.is_set()`) — ou sont annulées par l'unwind du groupe si elles sont à un `await`. **Vérifié à l'écriture** : les deux boucles bouclent sur `self._shutdown`, donc `self._shutdown.set()` (via `_on_signal`) les fait sortir ; pas besoin de les `.cancel()` explicitement (mais l'unwind du groupe est le filet).

(h) Ajouter la fonction module-level `resolve_staging_path` (après `default_verifier_factory`) — testable directement (les DEUX branches couvertes sans e2e, DÉCISION DV10) :
```python
def resolve_staging_path(
    staging_base: Path, catalog: CatalogReader, entry: DownloadEntry
) -> Path:
    """Chemin du fichier complété en staging pour une entrée de file (DÉCISION DV10).

    Dérive le nom du fichier de la DERNIÈRE observation du hash (le vrai layout amuled est
    PENDING-homelab) ; si aucune observation n'a survécu, retombe sur ``staging_base/<hash>``
    (best-effort : ce chemin échouera simplement à ``os.replace`` → ``_promote_completion``
    laisse ``completed`` et retente, JAMAIS de crash). ``catalog`` est typé au Protocol narrow
    ``CatalogReader`` (``application.run_download_cycle``) → ``SqliteCatalogRepository`` ET le
    faux de test le satisfont.
    """
    observation = catalog.last_observation(entry.ed2k_hash)
    filename = observation.filename if observation is not None else entry.ed2k_hash
    return staging_base / filename
```
> `CatalogReader` (Protocol narrow) est importé dans l'import groupé de `run_download_cycle` (Step 3(a) : `CatalogReader, DownloadLoopDeps, download_loop`).

> **DÉCISION DV16 — `aclose` du verifier au teardown sans élargir le port (VERROUILLÉ).** Le port `ContentVerifier` ne déclare PAS `aclose` (détail d'adapter http). La composition ferme le verifier via `stack.push_async_callback(verifier.aclose)  # type: ignore[attr-defined]` (cf. (e), VERBATIM) — toute impl passée à la composition (`HttpContentVerifier`, `FakeContentVerifier`) expose `aclose`. PAS de `getattr`/branche conditionnelle → aucune branche partielle à couvrir (100 % branch sans test d'un verifier sans `aclose`, qui n'existe pas).

- [ ] **Step 4 : Modifier `composition/__main__.py`**

`build_app` passe déjà toute la config via `CrawlerApp(...)` ; il n'a RIEN à changer pour les factories (les défauts `default_download_client_factory`/`default_verifier_factory` s'appliquent). Vérifier seulement qu'aucune assertion de signature ne casse (les nouveaux params ont des défauts). **Aucune modification de code nécessaire** sauf si un test l'exige ; sinon, ne pas toucher `__main__.py`. (Le retirer de la liste Files si inchangé.)

- [ ] **Step 5 : Vérifier puis gate**

Run : `( cd packages/crawler && uv run pytest tests/composition/test_app.py tests/composition/test_staging_resolver.py -q --no-cov )` → PASS (existants + 5 nouveaux : observateur / full-ok / health-fail / config-incomplète / daemon-unreachable-toléré + 2 resolver).
Run : gate complet (gate validé Task 1) → tout vert, 100 %.

Le `test_staging_resolver.py` (DÉCISION DV10/DV16 — couvre les DEUX branches de `resolve_staging_path` SANS e2e) :
```python
from pathlib import Path

from emule_indexer.composition.app import resolve_staging_path
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.mule_download_client import DownloadEntry


class _Cat:
    """Satisfait le Protocol narrow CatalogReader (last_observation seul)."""

    def __init__(self, obs: ObservedFile | None) -> None:
        self._obs = obs

    def download_decisions(self) -> tuple[object, ...]:
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return self._obs


def test_resolve_uses_observation_filename() -> None:
    entry = DownloadEntry(ed2k_hash="a" * 32, size_done=1, size_full=1)
    path = resolve_staging_path(Path("/staging"), _Cat(ObservedFile("Keroro.avi", 1)), entry)
    assert path == Path("/staging/Keroro.avi")


def test_resolve_falls_back_to_hash_when_no_observation() -> None:
    entry = DownloadEntry(ed2k_hash="b" * 32, size_done=1, size_full=1)
    path = resolve_staging_path(Path("/staging"), _Cat(None), entry)
    assert path == Path("/staging") / ("b" * 32)
```
> **Note `_Cat` :** le Protocol narrow `CatalogReader` (de `run_download_cycle`) déclare `download_decisions` + `last_observation` ; `_Cat` les fournit (le premier renvoie `()`, jamais appelé par `resolve_staging_path`) pour satisfaire mypy au passage `resolve_staging_path(…, _Cat(…), …)`.

> **Note couverture (composition) :** `verifier_url is None`→observateur (boucles None, `_supervise` saute les deux `if … is not None`) ; `verifier_url` présent→full (`_require_full_config` OK→continue / manquant→ConfigError) ; `health()` True→continue / False→ConfigError ; `_build_full_loops` : connect OK / `MuleUnreachableError` toléré — **AJOUTER au Step 1 un test** où `download_client_factory` rend un client dont `connect` lève `MuleUnreachableError` → toléré, boucles quand même armées (les deux côtés du `try/except`) ; `resolve_staging_path` : filename trouvé / None→hash (les deux côtés, via `test_staging_resolver.py` ci-dessus) ; `_supervise` : `download_deps`/`verify_deps` None (observateur) vs non-None (full), les deux côtés des deux `if`.

- [ ] **Step 6 : Commit**

```bash
git add packages/crawler/src/emule_indexer/composition/app.py packages/crawler/tests/composition/test_app.py packages/crawler/tests/composition/test_staging_resolver.py
git commit -m "$(cat <<'EOF'
feat(composition): gate full-mode (verifier_url + health fail-fast) + câblage des 2 boucles

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12 : E2e `verify_integration` (boucle de vérif ↔ vrai service, sans Docker)

**Files :**
- Create: `packages/crawler/tests/integration/test_verify_loop.py`
- (Le marqueur `verify_integration` est DÉJÀ déclaré dans `packages/crawler/pyproject.toml` au Task 1.)

> Spec §9 : e2e opt-in (`verify_integration`, hors coverage, déselectionné par défaut), **Docker-free** : un fichier **pré-placé** en quarantaine + une tâche enfilée → la boucle de vérif, branchée sur le **vrai service verifier** via `HttpContentVerifier` + `ASGITransport` (in-process, sans socket), produit une ligne `file_verifications` `unverified`. Prouve le RPC réel + l'écriture catalogue sans dépendre d'un vrai download (le download→verify complet reste la validation homelab manuelle). C'est le filet « réel » du jalon (méthode reconduite : l'e2e fait foi).

- [ ] **Step 1 : Écrire l'e2e**

`packages/crawler/tests/integration/test_verify_loop.py` :
```python
"""E2e DE VÉRIFICATION : la boucle ↔ le VRAI service verifier (spec verify §9 — option A).

Run dédié : ( cd packages/crawler && uv run pytest -m verify_integration --no-cov )
Sans Docker : le service ``download_verifier`` tourne IN-PROCESS via ``httpx.ASGITransport``.
Un fichier est PRÉ-PLACÉ en quarantaine + une tâche enfilée → ``run_verification_cycle`` (avec
un VRAI ``HttpContentVerifier``, de VRAIS repos SQLite sur ``tmp_path``) produit une ligne
``file_verifications`` ``unverified``. Prouve le contrat de fil DTO↔réponse + l'écriture
durable, sans vrai download (le download→verify complet = validation homelab manuelle).
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from download_verifier.app import build_app
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog, open_local
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.adapters.verifier_http import HttpContentVerifier
from emule_indexer.application.run_verification_cycle import VerifyDeps, run_verification_cycle
from emule_indexer.domain.observation import FileObservation

pytestmark = pytest.mark.verify_integration

_A = "a" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _RealClock:
    """Horloge réelle minimale (now aware + sleep no-op) pour la boucle de l'e2e."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        return None


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture
def local(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_local(tmp_path / "local.db")
    yield connection
    connection.close()


@pytest.mark.asyncio
async def test_verify_loop_produces_unverified_row(
    tmp_path: Path, catalog: sqlite3.Connection, local: sqlite3.Connection
) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _A).write_bytes(b"\x00\x01\x02")  # fichier PRÉ-PLACÉ (jamais lu par le crawler)

    catalog_repo = SqliteCatalogRepository(catalog, _NODE)
    catalog_repo.record_observation(
        FileObservation(
            ed2k_hash=_A,
            filename="Keroro.avi",
            size_bytes=3,
            source_count=1,
            complete_source_count=0,
            keyword="keroro",
        )
    )
    downloads_repo = SqliteDownloadRepository(local)
    downloads_repo.record_queued(_A, "S2E062A", 3)
    local_repo = SqliteLocalStateRepository(local)
    assert local_repo.enqueue_verification(_A) is True  # tâche enfilée (le download le ferait)

    transport = httpx.ASGITransport(app=build_app(quarantine))
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    verifier = HttpContentVerifier(client)
    deps = VerifyDeps(
        queue=local_repo,
        verifier=verifier,
        writer=catalog_repo,
        targets=downloads_repo,
        poll_interval_seconds=1.0,
        clock=_RealClock(),
    )
    try:
        await run_verification_cycle(deps)  # claim → verify (RPC réel) → record → complete
    finally:
        await verifier.aclose()

    row = catalog.execute(
        "SELECT ed2k_hash, verdict FROM file_verifications WHERE ed2k_hash = ?", (_A,)
    ).fetchone()
    assert row == (_A, "unverified")
    # la tâche est complétée (plus claimable).
    assert local_repo.claim_verification() is None
```

- [ ] **Step 2 : Vérifier**

Run (collection, run par défaut → déselectionné) :
```bash
( cd packages/crawler && uv run pytest -q )
```
Expected : `… passed, 7 deselected` (4 ec + 1 orchestration + 1 download + **1 verify**), 100 % branch.
Run (dédié, sans Docker — fait foi) :
```bash
( cd packages/crawler && uv run pytest -m verify_integration --no-cov -q )
```
Expected : `1 passed` (la boucle produit la ligne `unverified` contre le vrai service in-process).

> **Note :** l'e2e est HORS coverage (`--no-cov` au run dédié ; déselectionné au run par défaut donc ne compte pas dans le 100 %). Il importe `download_verifier.app` — légal (workspace venv partagé, côté test).

- [ ] **Step 3 : Commit**

```bash
git add packages/crawler/tests/integration/test_verify_loop.py
git commit -m "$(cat <<'EOF'
test(integration): e2e verify_integration (boucle de vérif ↔ vrai service, sans Docker)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13 : Revue holistique finale + handoff + CLAUDE.md + TAG (clôt le jalon Plan D)

**Files :** (aucune création de code — vérification + handoff + CLAUDE.md + tag annoté)

> La revue holistique attrape les bugs cross-cutting que le suivi à la lettre ne voit pas (méthode reconduite : elle a attrapé LE bug critique à chaque jalon). D-verify **CLÔT le jalon « Plan D »** (auto-download + verifier) → cette tâche **POSE le tag annoté**. Greps de la règle de dépendance PAR PAQUET, gate complet des deux paquets, e2e, handoff, CLAUDE.md, puis tag.

- [ ] **Step 1 : Greps de la règle de dépendance (PAR PAQUET, DOIVENT être CLEAN sauf whitelist)**

Run (le domaine n'importe que des deps pur-calcul whitelistées) :
```bash
grep -rnE "^(from|import) (emule_indexer\.(ports|adapters|application|composition)|re2|rapidfuzz)" packages/crawler/src/emule_indexer/domain/
```
Expected (le moteur seul — inchangé depuis Plan C ; AUCUNE ligne nouvelle de D-verify, le domaine n'est pas touché) :
```
packages/crawler/src/emule_indexer/domain/matching/interpolation.py:6:import re2
packages/crawler/src/emule_indexer/domain/matching/matchers.py:3:import re2
packages/crawler/src/emule_indexer/domain/matching/matchers.py:4:from rapidfuzz import fuzz
packages/crawler/src/emule_indexer/domain/matching/validation.py:12:import re2
```

Run (l'application n'importe JAMAIS un adapter ni la composition) :
```bash
grep -rnE "^(from|import) emule_indexer\.(adapters|composition)" packages/crawler/src/emule_indexer/application/
```
Expected : **AUCUNE sortie**. `run_verification_cycle.py` ne dépend que des ports + ses Protocols narrow ; `record_observations.py` n'importe `DOWNLOAD_NUDGE_SUBJECT` que depuis `application.run_download_cycle` (même couche, licite).

Run (les ports n'importent jamais adapters/application/composition ; ni httpx/starlette) :
```bash
grep -rnE "^(from|import) (emule_indexer\.(adapters|application|composition)|httpx|starlette)" packages/crawler/src/emule_indexer/ports/
```
Expected : **AUCUNE sortie** (le port `ContentVerifier` ne dépend QUE du domaine/stdlib ; httpx vit dans l'adapter).

Run (CRITIQUE — frontière de paquet : le CODE DE PROD du crawler n'importe JAMAIS le verifier) :
```bash
grep -rn "download_verifier" packages/crawler/src/
```
Expected : **AUCUNE sortie** (seuls les TESTS du crawler importent `download_verifier` : contrat + e2e). Vérifier l'inverse aussi :
```bash
grep -rn "emule_indexer" packages/verifier/src/
```
Expected : **AUCUNE sortie** (le verifier ne connaît PAS le crawler — frontière de paquet, DÉCISION DV4).

Run (déterminisme : aucun `random`/horloge/sleep direct dans la boucle de vérif) :
```bash
grep -nE "(^import random|^import time|datetime\.now|asyncio\.sleep\()" packages/crawler/src/emule_indexer/application/run_verification_cycle.py
```
Expected : **AUCUNE sortie** (`deps.clock.sleep` via le port `Clock`).

Run (le crawler ne lit JAMAIS les octets d'un fichier ; le verifier ne fait qu'un stat) :
```bash
grep -rnE "(\.read_(bytes|text)\(|\.open\(|open\()" packages/crawler/src/emule_indexer/application/run_verification_cycle.py packages/crawler/src/emule_indexer/adapters/verifier_http.py
grep -rnE "(\.read\(|\.read_(bytes|text)\()" packages/verifier/src/download_verifier/check.py
```
Expected : **AUCUNE sortie** (la boucle/adapter crawler ne touchent pas d'octets ; `check.py` ne fait que `is_file()` — pas de lecture de contenu).

- [ ] **Step 2 : Revue de cohérence (lecture humaine/subagent, bugs cross-cutting)**

Points à confirmer explicitement (chacun couvert par un test, la revue confirme la cohérence) :
- **Contrat de fil DTO↔réponse** : `VerificationResult(verdict/real_meta/checks)` (crawler) ↔ `{verdict, real_meta, checks}` (verifier) — gardés en phase par le test de contrat (Task 5) + l'e2e (Task 12), DÉFINIS dans des paquets séparés (DÉCISION DV4).
- **Transitoire vs déterministe** (DÉCISION DV6) : injoignable/timeout/5xx → `VerifierUnavailableError` → `fail_verification` (retry/dead-letter) ; 200 malformé → `VerificationResult(verdict="error")` → record + `complete` (PAS de boucle infinie). Vérifier qu'aucun chemin n'invente de verdict sur un service injoignable.
- **Gate full-mode** : `verifier_url` absent → observateur (boucles OFF, comportement Plan C strictement inchangé) ; présent → `_require_full_config` (ensemble complet) + `health()` fail-fast ; mi-parcours, le verifier qui tombe est TOLÉRÉ (la boucle fail+retry, le download continue) — pas de fail-fast à chaud.
- **File = couplage** : la vérif est CONSOMMATEUR (`claim`/`complete`/`fail`/`reclaim`) ; le download est PRODUCTEUR (`enqueue`). Aucun nudge dédié pour la vérif (la file durable + le poll).
- **`expected` minimal** (DÉCISION DV11) : `{target_id}` ou `{}` ; le NO-OP l'ignore ; la branche `None` est couverte.
- **Arrêt** : les deux nouvelles boucles bouclent sur `self._shutdown` (même Event) → `_on_signal` les fait sortir ; tâches du `TaskGroup` annulables au prochain `await`, jamais en pleine écriture DB (repos sync).
- **`staging_path_for`** (DÉCISION DV10) : filename trouvé→path ; None→hash (best-effort, échoue à `os.replace` → `completed` retry, jamais crash). `resolve_staging_path` unit-testé.

- [ ] **Step 3 : Gate complet final (les DEUX paquets)**

Run (le gate validé du Task 1 — ruff/format/mypy racine spannent les deux paquets, pytest par paquet) :
```bash
uv run ruff check .
uv run ruff format --check .
uv run sqlfluff lint packages/crawler/src
uv run mypy
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
```
Expected : tout vert. Crawler : `… passed, 7 deselected`, **100.00 % branch** sur `emule_indexer`. Verifier : `… passed`, **100.00 %** sur `download_verifier`.

Run (e2e dédiés, fait foi) :
```bash
( cd packages/crawler && uv run pytest -m verify_integration --no-cov -q )
```
Expected : `1 passed`. (Les e2e Docker `ec/orchestration/download_integration` restent verts inchangés si Docker dispo.)

- [ ] **Step 4 : Mettre à jour `CLAUDE.md` (état courant)**

Mettre à jour le paragraphe « Current state » : le **pipeline de vérification full-mode (D-verify)** est construit — workspace uv (`packages/crawler` + `packages/verifier`) ; service `download_verifier` (Starlette `POST /verify`/`GET /health`, `check.py` NO-OP, entrée uvicorn) ; port `ContentVerifier` + `HttpContentVerifier` (httpx, parsing défensif) + `VerifierUnavailableError` ; `CatalogRepository.record_verification` ; `SqliteDownloadRepository.get_target_id` ; config `VerifyConfig`/`verifier_url` ; boucle `run_verification_cycle`/`verification_loop` ; câblage LIVE des DEUX boucles (download + vérif) dans `CrawlerApp` avec gate full-mode (`verifier_url` + health fail-fast). Le jalon « Plan D » (auto-download + verifier) est **COMPLET et taggé `v0.8.0-auto-download`**. **Mettre à jour la section « Commands »** avec le gate per-paquet (déjà fait au Task 1 ; revérifier la cohérence). Noter le marqueur `verify_integration` (opt-in, sans Docker) et que **D-analysis** (confinement réel + vrais checks remplissant `real_meta`) reste à faire (Plan suivant). Mettre à jour l'arborescence si elle liste `src/`/`tests/` à la racine → `packages/crawler/…`.

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: CLAUDE.md — pipeline de vérification full-mode construit (D-verify, jalon Plan D complet)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5 : Écrire le handoff**

Créer `docs/handoffs/2026-06-13 - handoff - verification pipeline.md` (format des handoffs précédents) :
- **TL;DR** : la boucle full-mode est FERMÉE de bout en bout. Workspace uv (crawler + verifier séparés). Le verifier NO-OP (Starlette) rend `unverified` ; le crawler consomme la file `verification_tasks` (`run_verification_cycle`/`verification_loop`), appelle le verifier par RPC (`HttpContentVerifier`), écrit `file_verifications`. `CrawlerApp` câble LIVE les deux boucles (download + vérif) en mode full (`verifier_url` + health fail-fast) ; absent → observateur (boucles OFF). E2e `verify_integration` (sans Docker, RPC réel in-process) VERT. **Jalon Plan D COMPLET, taggé `v0.8.0-auto-download`.**
- **État vérifiable** : gate per-paquet (crawler + verifier, 100 % branch chacun) ; e2e `verify_integration` ; tag posé (non poussé).
- **Contrats / ce que D-analysis doit faire** :
  1. **Remplacer le NO-OP** : `check.verify_file` doit lancer le vrai pipeline (type_sniff → ffprobe → clamav, agrégation worst-status) dans un **enfant jetable** (`net=none`/rlimits/timeout/RO/non-root), remplir `real_meta` (durée/bitrate/codec — le trou qu'EC ne comble pas) et rendre des verdicts réels (`clean`/`suspicious`/`malicious`). Le contrat de fil (`{verdict, real_meta, checks}`) et le DTO crawler `VerificationResult` ne changent PAS (frontière de paquet stable).
  2. **Exploiter `expected`** : la boucle bâtit déjà un `expected` minimal (`{target_id}`) ; D-analysis l'enrichira (taille/durée attendues de la cible) pour comparer.
  3. **Plan F** : image Docker du verifier + réseau `internal: true` + compose + durcissement (gVisor/nsjail) — le service tourne en dev (uvicorn) ici.
- **Pièges appris** (à remplir au fil de l'exécution) : p.ex. la satisfaction de Protocol (`expected: Mapping`, pas `dict`) ; la frontière de paquet stricte côté prod (verifier jamais importé par le crawler) ; le `staging_path_for` best-effort (None→hash) ; le `aclose` du verifier au teardown sans élargir le port.
- **Notes reportées de D-download (toujours ouvertes)** : I1 (re-émission add_link bénigne), I2 (granularité d'erreur par-étape dans `run_download_cycle`), T12 (couverture d'arrêt en intégration des boucles câblées) — voir le handoff D-download §5. **Validation homelab manuelle** du download→verify COMPLET (le vrai layout amuled de `staging_path_for`, DÉCISION DV10) reste à faire.
- **Prochaine étape** : **D-analysis** (confinement + vrais checks), brainstormer d'abord. Plan E (observabilité Prometheus/apprise), Plan F (packaging) ensuite.

```bash
git add "docs/handoffs/2026-06-13 - handoff - verification pipeline.md"
git commit -m "$(cat <<'EOF'
docs: handoff — pipeline de vérification full-mode (D-verify ; contrats pour D-analysis)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6 : Poser le TAG annoté (clôt le jalon Plan D — NON poussé)**

D-verify clôt « Plan D » (auto-download + verifier). Poser le tag annoté (proposition : **`v0.8.0-auto-download`** — le jalon couvre download + vérification, donc « auto-download » est plus représentatif que « download » seul) :
```bash
git tag -a v0.8.0-auto-download -m "$(cat <<'EOF'
v0.8.0-auto-download : auto-download + pipeline de vérification full-mode (Plan D complet)

D-download (capacité de téléchargement) + D-verify (workspace uv, verifier NO-OP,
boucle de vérification, câblage live des deux boucles, gate full-mode). NON POUSSÉ.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
git tag --list | grep -E "0\.8|auto-download"
```
Expected : `v0.8.0-auto-download`. **NE PAS pousser** (les jalons restent locaux sur `main`).

---

## Self-Review : couverture de la spec (section → tâche)

| Spec verify | Couvert par |
|---|---|
| §1 But : fermer la boucle full-mode (file → verifier RPC → `file_verifications` → câblage live des 2 boucles), verifier NO-OP | Tasks 2-3 (verifier), 4-5 (port+adapter), 6 (record_verification), 9 (boucle), 11 (câblage live) |
| §1 Hors périmètre : D-analysis (confinement+vrais checks), Plan F (image/compose), upgrades, quota infra | Header HORS PÉRIMÈTRE + Task 13 (handoff renvoie à D-analysis) ; verifier reste NO-OP (Task 2) |
| §2.1 Verifier NO-OP trivial (pas de confinement) | Task 2 (`check.verify_file` stat RO → unverified), DÉCISION DV1 |
| §2.2 Stack HTTP (httpx / starlette+uvicorn ; ASGITransport) figée context7 | Tasks 3 (Starlette/uvicorn), 5 (httpx), DÉCISION DV2 (formes context7) |
| §2.3 / §3 Workspace uv (racine pure + crawler + verifier ; 100 % branch par paquet ; sqlfluff crawler seul ; config racine) | Task 1 (migration complète), DÉCISION DV3 |
| §2.4 / §4 Verifier ne partage que le contrat de fil (DTO définis indépendamment) | Tasks 2/4 (DTO séparés), 5 (test de contrat), 12 (e2e), DÉCISION DV4 ; grep frontière (Task 13) |
| §2.5 / §6 La file EST le couplage (consommateur, pas de nudge) | Task 9 (`run_verification_cycle` consomme la file), DÉCISION DV5/DV13 |
| §2.6 / §8 Transitoire vs mauvaise réponse | Task 5 (adapter : Unavailable vs verdict=error), 9 (boucle : fail vs record+complete), DÉCISION DV6 |
| §2.7 / §7 Gate full-mode (`verifier_url` + health fail-fast ; tolérance à chaud) | Task 11 (gate + health fail-fast + boucles), DÉCISION DV7/DV8/DV15 |
| §4 Service verifier (Starlette POST /verify + GET /health ; check.py ; uvicorn) | Tasks 2 (check.py), 3 (app.py + __main__.py) |
| §5 Ports & adapter crawler (`ContentVerifier`/`VerificationResult` ; `HttpContentVerifier` parsing défensif ; `record_verification`) | Tasks 4 (port + DTO + erreur), 5 (adapter), 6 (record_verification) |
| §6 Boucle `run_verification_cycle` (reclaim→claim→expected→verify→record→complete) | Task 9 (cycle + loop), DÉCISION DV11/DV13 ; `get_target_id` Task 7 |
| §7 Câblage live des DEUX boucles + repos uniques + nudge download producteur + staging_path_for | Task 11 (câblage), 10 (nudge producteur DV9), 7 (get_target_id), DÉCISION DV10 (staging) |
| §8 Erreurs/résilience (injoignable→fail/retry→dead-letter ; malformé→error enregistré ; reclaim ; arrêt) | Tasks 5 (adapter), 9 (boucle : fail/complete + reclaim chaque cycle + arrêt), 11 (arrêt via TaskGroup) |
| §9 Tests (verifier check+app ASGITransport ; boucle faux verifier + vrais SQLite ; gate full-mode ; test de contrat ; e2e verify_integration) | Tasks 2/3 (verifier), 5 (contrat), 9 (boucle), 11 (gate), 12 (e2e) |
| §10 DoD (workspace vert 2 paquets ; service ; port+adapter+record_verification ; boucle ; gate full + câblage ; config ; gate+e2e ; deps httpx/starlette/uvicorn) | Tasks 1-13 ; deps ajoutées Task 1 (httpx) / Task 1 (starlette/uvicorn dans verifier pyproject) |
| §11 Suite = D-analysis (+ Plan F) | Task 13 (handoff) ; hors périmètre (header) |

**Self-review — résultats :**

1. **Couverture spec §1–§11** : chaque section est mappée à au moins une tâche (table ci-dessus). Le seul élément volontairement NON livré est D-analysis (confinement + vrais checks), explicitement hors périmètre (spec §1/§11) — le verifier reste NO-OP. Le **tag EST posé** ici (D-verify clôt le jalon, contrairement à D-download qui le différait) : `v0.8.0-auto-download` (Task 13 Step 6).

2. **Placeholder scan** : AUCUN « TBD », « add error handling », « … (à compléter) » dans le CODE de production NI dans les tests des tâches. Le code de chaque fichier source ET de chaque test est complet et copiable. Les renvois restants sont (a) des consignes de RÉDACTION de docs (handoff Task 13 Step 5, contenu spécifié point par point ; CLAUDE.md Step 4), (b) **une consigne de lecture du fichier existant** au Task 10 Step 1 (les fakes de `test_record_observations.py` existent déjà — l'implémenteur réutilise leurs noms réels ; le squelette des deux tests + l'assertion exacte sont donnés). Ce n'est PAS du code laissé en blanc : c'est une adaptation aux fakes existants, bornée par les assertions données.

3. **Cohérence des types/signatures (vérifiée transversalement)** :
   - `VerificationResult(verdict, real_meta, checks)` : défini Task 4 (port), produit par `HttpContentVerifier.verify` (Task 5) et le fake (Task 9), consommé par `run_verification_cycle` → `record_verification` (Task 9/6). ✔
   - `ContentVerifier.verify(ed2k_hash, expected: Mapping[str, object])` (Task 4) : l'adapter (Task 5) et les fakes (Tasks 9/11) signent `expected: Mapping[str, object]` (PAS `dict` — note explicite Task 5 sur la satisfaction de Protocol). ✔
   - `VerifierUnavailableError` (Task 4, port dédié) : levé par l'adapter (Task 5), attrapé par la boucle (Task 9) sans importer l'adapter. ✔
   - `record_verification(ed2k_hash, verdict, real_meta: Mapping, checks: Sequence)` : port + impl (Task 6), narrow `VerificationWriter` (Task 9, `checks: tuple[object, ...]`), fake (Task 9). **Cohérence checks** : le port catalog déclare `Sequence[object]` ; le narrow `VerificationWriter` déclare `tuple[object, ...]` ; `SqliteCatalogRepository.record_verification` accepte `Sequence` (supertype de `tuple`) → satisfait le narrow. Le `VerificationResult.checks` est `tuple[object, ...]` → compatible avec les deux. ✔
   - `get_target_id(ed2k_hash) -> str | None` : impl (Task 7), narrow `TargetIdLookup` (Task 9), appelé par `_build_expected` (Task 9). ✔
   - `VerifyDeps`/`VerifyLoopDeps` (Task 9) : assemblés par la composition (Task 11) avec les vrais repos (`local_repo`→queue, `catalog_repo`→writer, `downloads_repo`→targets, `HttpContentVerifier`→verifier). ✔
   - `DownloadLoopDeps` (D-download, inchangé) : assemblé par la composition (Task 11) — signature reprise verbatim du digest. ✔
   - `VerifyConfig(poll_interval_seconds)` / `CrawlerConfig.verify` (Task 8) ; `LocalConfig.verifier_url` (Task 8) : lus par la composition (Task 11). ✔
   - `check.verify_file(quarantine_path, expected) -> tuple[str, dict, list]` (Task 2) ↔ réponse `app.py` `{verdict, real_meta, checks}` (Task 3) ↔ DTO crawler `VerificationResult` (Task 4) : contrat de fil prouvé par le test de contrat (Task 5) + l'e2e (Task 12). ✔
   - `DOWNLOAD_NUDGE_SUBJECT` (D-download) : importé par `record_observations` (Task 10) et la composition/loop. ✔
   - Gate per-paquet (Task 1) : utilisé verbatim dans TOUS les Steps « Vérifier » suivants. ✔

4. **Stack HTTP figée via context7 (formes RE-vérifiées sur les versions résolues)** : Starlette **1.3.1** `Starlette(routes=[Route(path, ep, methods=[...])])` + `async def endpoint(request: Request) -> JSONResponse` + `await request.body()` + `app.state` ; httpx **0.28.1** `ASGITransport(app=app)` + `AsyncClient(transport=transport, base_url=…)` + `MockTransport(handler)` + `httpx.HTTPError`/`httpx.HTTPStatusError`/`ConnectError`/`ReadTimeout`/`TimeoutException` ; uvicorn **0.49.0** `uvicorn.run("download_verifier.app:app", host=, port=)`. uv workspace VIRTUEL : racine SANS `[project]`, `[tool.uv.workspace] members=["packages/*"]` + `[tool.uv.sources] {workspace=true}` pour les DEUX membres + les deux dans `[dependency-groups] dev` (venv partagé → le test de contrat/e2e importe `download_verifier`) ; `[tool.ruff]`/`[tool.mypy]` RACINE, `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` PAR PAQUET ; un seul `uv.lock`. **Tasks 3 & 5 RE-confirment l'API via context7 avant d'écrire le code** (starlette 1.3.x bien au-delà du floor `>=1.3`). ✔

5. **Risques de la migration workspace (Task 1) traités — layout VALIDÉ EMPIRIQUEMENT (uv 0.8.11)** : (a) `from tests.…` (8 fichiers) → on lance pytest DEPUIS `packages/crawler` (`cd packages/crawler && uv run pytest`) → rootdir = `packages/crawler/` + `testpaths=["tests"]` ; `tests/__init__.py` survit au `git mv`. (b) migrations chargées par `importlib.resources.files("emule_indexer.adapters.persistence_sqlite")` → inchangé (paquet stable). (c) `config/` reste racine → défauts `config/*.yaml` de `__main__` relatifs au CWD, inchangés ; **MAIS** `tests/composition/test_main.py` lit le `config/` racine par chemin absolu → `parents[2]` devient **`parents[4]`** (Task 1 Step 6, sinon RED à 99.83 %). (d) `tests/fixtures/` part avec `tests/` → readers de `fixtures` inchangés. (e) sqlfluff crawler seul (verifier sans SQL). (f) `[tool.ruff]`/`[tool.mypy]` RACINE (un `ruff check .`/`mypy`), `[tool.pytest]`/`[tool.coverage]`/`[tool.sqlfluff]` PAR PAQUET (sinon le 100 %-branch par paquet est défait). (g) httpx ajouté au crawler ; starlette/uvicorn au verifier ; un seul lock régénéré. (h) le test de contrat/e2e crawler importe `download_verifier` (venv partagé, les deux membres dans `dev`) — légal côté test, interdit côté prod (grep Task 13).

**Nombre de tâches : 13** (1 workspace ; 2 check.py ; 3 app.py+uvicorn ; 4 port ContentVerifier ; 5 HttpContentVerifier+contrat ; 6 record_verification ; 7 get_target_id ; 8 config ; 9 boucle de vérif ; 10 nudge download producteur ; 11 composition full-mode+2 boucles ; 12 e2e verify_integration ; 13 revue holistique+handoff+CLAUDE.md+**TAG**).

