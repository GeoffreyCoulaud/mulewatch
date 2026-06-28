# Plan F — Packaging (2 images Docker, compose profils observer/full, smoke e2e, CI GHCR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Empaqueter le crawler (`emule_indexer`) et le verifier (`download_verifier`) en **deux images Docker multi-stage (uv)**, les câbler dans un `docker compose` fidèle à la topologie MVP (gluetun/amuled, réseaux isolés, verifier sans Internet) avec **profils `observer`/`full`** et un **durcissement niveau conteneur** (non-root, `cap_drop: ALL`, `no-new-privileges`, rootfs `read_only` + tmpfs, `verify-internal: { internal: true }`), livrer un **smoke test e2e automatisé** (marqueur pytest `compose_integration`, Docker requis, `--no-cov`, désélectionné) qui monte la stack assemblée **sans VPN** et asserte le câblage (verifier `/health` 200, crawler observer Up, full fail-fast sans verifier), un **override durcissement opt-in** (`compose.hardening.yml`, gVisor `runtime: runsc`), un **workflow CI** (`.github/workflows/images.yml` : job `smoke` amd64 gate → job `publish` multi-arch GHCR), et un **runbook** de déploiement. **CONTRAINTE DURE : Plan F n'ajoute AUCUN code PROD Python** — les artefacts sont des Dockerfiles, des fichiers compose, un workflow, UN test d'intégration, un runbook, et des ajustements `.gitignore`/`.dockerignore`. Le gate existant (6 checks, 100 % branch verifier + crawler) ne BOUGE PAS. Spec : `docs/superpowers/specs/2026-06-14-packaging-design.md`.

**Architecture:** Clean/Hexagonal **inchangée** (aucun fichier sous `packages/*/src/` n'est touché). Le packaging vit ENTIÈREMENT à la racine du dépôt (`compose.yaml`, `compose.smoke.yaml`, `compose.hardening.yml`, `.dockerignore`, `.env.example`) + dans `packages/<pkg>/Dockerfile` + `packages/crawler/tests/integration/test_compose_smoke.py` + `.github/workflows/images.yml` + `docs/runbook-deployment.md`. **Build context = racine du dépôt** (un seul `uv.lock` de workspace VIRTUEL) ; chaque Dockerfile n'installe que SON paquet. Le mode du crawler (observer/full) et son hôte EC sont pilotés **uniquement par un `local.yaml` monté** (config 100 % fichiers, le crawler n'a AUCUNE variable d'env — voir `composition/__main__.py`) : le smoke fournit des `local.yaml` de test dédiés montés en RO et choisis via l'arg `--local`. Le verifier, lui, lit `QUARANTINE_DIR`/`VERIFIER_HOST`/`VERIFIER_PORT` dans l'environnement (son `__main__.py`/`app.py` existants). La frontière de paquet (crawler prod n'importe jamais `download_verifier`, et inversement) reste intacte : Plan F ne touche aucun import.

**Tech Stack:** Docker (Buildx / BuildKit) + `docker compose` v2 ; images de base **`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`** (builder) et **`python:3.12-slim-bookworm`** (runtime) ; idiome **uv-in-Docker** multi-stage avec cache mount (`UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, `UV_NO_DEV=1`, `UV_PYTHON_DOWNLOADS=0`) — **l'incantation workspace exacte (`--package` / `--no-install-workspace` / `--no-editable`) DOIT être validée EMPIRIQUEMENT au build** (voir Tasks 1-2). Images tierces : `qmcgaw/gluetun` (VPN), `ngosang/amule:3.0.0-1` (amuled, port EC 4712, log readiness « listening on 0.0.0.0:4712 »). CI : `docker/setup-qemu-action@v3`, `docker/setup-buildx-action@v3`, `docker/login-action@v3`, `docker/metadata-action@v5`, `docker/build-push-action@v6`, multi-arch `linux/amd64,linux/arm64` → GHCR. Test : `pytest` (marqueur `compose_integration`, `--no-cov`, désélectionné par défaut, Docker requis), shell-out `docker compose` via `subprocess`. **AUCUNE dépendance Python ajoutée** (`subprocess`/`json`/`urllib` stdlib).

> **Référence spec :** `docs/superpowers/specs/2026-06-14-packaging-design.md` — §1 (but/périmètre), §2 (décisions F-D1..F-D6), §3 (2 images), §4 (compose topologie + profils), §5 (smoke + `compose_integration`), §6 (durcissement), §7 (CI GHCR multi-arch), §8 (runbook), §9 (tests/discipline), §10 (hors-périmètre), §11 (risques). Plans précédents de référence (style/densité/format/trailer) : `docs/superpowers/plans/2026-06-13-crawler-mvp-07-verification-pipeline.md`, `docs/superpowers/plans/2026-06-14-crawler-mvp-08-analysis.md`. Handoff le plus récent : `docs/handoffs/2026-06-14 - handoff - analysis (real verifier).md`.

> **HORS PÉRIMÈTRE (spec §1/§10 — RIEN de tout ceci ici) :** **Port-sync / High-ID** (lire le port forwardé de gluetun → le poser sur amuled via EC ; on abandonne glueforward) → **follow-up dédié** ; le profil `full` tourne en **Low-ID** d'ici là. **Sous-commandes CLI** (`merge`/`rebuild-local`/`validate-config`) → différées. **clamav** → follow-up APRÈS Plan F. **Ring noyau OBLIGATOIRE** (gVisor/bwrap requis) → livré en **override opt-in documenté** (`compose.hardening.yml`), JAMAIS requis pour démarrer/smoke-tester. **e2e contre un serveur eD2k local + fichier planté** (MVP §16) → non retenu (le smoke suffit comme filet automatisé ; la validation réelle = homelab manuel, runbook). **Aucun code PROD Python** : aucun fichier sous `packages/crawler/src/` ou `packages/verifier/src/` n'est créé ni modifié.

---

## Décisions verrouillées (spec §2 — F-D1..F-D6, ne PAS relitiger)

> **F-D1 — e2e = smoke test compose automatisé.** Marqueur pytest `compose_integration` (Docker requis, désélectionné par défaut, `--no-cov`), montant la stack **assemblée sans gluetun** (verifier + crawler + amuled via `compose.smoke.yaml`). Asserte le **câblage** : `docker compose build` OK, verifier `/health` 200 (via son healthcheck `python -c urllib`), crawler **observer** Up sans verifier, crawler **full** **fail-fast** quand verifier absent (exit ≠ 0 via `docker inspect`). **AUCUN téléchargement réel** (amuled n'a ni serveurs eD2k ni VPN — seul son serveur EC est sollicité).

> **F-D2 — durcissement conteneur bâti + testé ; ring noyau opt-in.** Niveau conteneur sur tous les services bâtis : `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `user:` non-root (`999:999`), `read_only: true` (rootfs) + `tmpfs:` pour le scratch, `pids_limit`, `mem_limit`, seccomp **par défaut** (jamais `unconfined`) ; verifier **seul** sur `verify-internal` (`internal: true` → pas d'Internet). Le ring noyau (gVisor `runtime: runsc` / bwrap) = `compose.hardening.yml` opt-in + runbook, NON requis (dépend du support hôte).

> **F-D3 — glueforward abandonné ; port-sync = follow-up.** Pas de conteneur glueforward. Profil `full` en Low-ID pour l'instant. Le port-sync est un sous-projet suivant (hors plan).

> **F-D4 — sous-commandes CLI différées.** Le daemon de crawl reste le seul point d'entrée du crawler (`python -m emule_indexer`, args `--crawler/--local/--targets/--matcher`).

> **F-D5 — clamav après Plan F.** Inchangé.

> **F-D6 — CI build + GHCR multi-arch.** Workflow `images.yml` : job `smoke` (`ubuntu-latest`, amd64, `docker compose build` + `compose_integration`) **gate** un job `publish` (`needs: smoke`, buildx multi-arch `amd64+arm64` → GHCR via `metadata-action` + `build-push-action`). Triggers : push `main` + tags `v*` + `workflow_dispatch` (**PAS** sur PR). `ci.yml` (le gate) **INCHANGÉ**. **Zéro code PROD Python ajouté** → le gate 100 % branch reste intact. **Dormant** tant que le dépôt reste full-local (ne tourne qu'au push).

> **CONCRÉTISATION CONTRÔLEUR — config observer/full/EC-host par `local.yaml` montés (spec §4/§11).** Le crawler n'a AUCUNE variable d'env (`composition/__main__.py` : config 100 % fichiers YAML). Donc le compose **monte un `local.yaml` dédié par scénario** et le passe via l'arg `--local`. Le smoke fournit `deploy/smoke/local.observer.yaml` (pas de `verifier_url`, EC host = `amuled`) et `deploy/smoke/local.full.yaml` (`verifier_url: http://verifier:8000`, EC host = `amuled`). Ces fichiers de test sont versionnés sous `deploy/smoke/` (PAS sous `config/`, qui reste réservé au runtime réel gitignoré). **AUCUN ajout au config loader** (l'interpolation d'env n'existe pas et n'est pas ajoutée — contrainte dure).

> **CONCRÉTISATION CONTRÔLEUR — noms canoniques (cohérence inter-tâches, à utiliser VERBATIM partout).**
> - Images GHCR : `ghcr.io/${{ github.repository }}-crawler` et `…-verifier` ; en local/compose `image: ghcr.io/geoffreycoulaud/emule-indexer-crawler` et `…-verifier` (le repo est `GeoffreyCoulaud/emule-indexer` → **lowercase imposé** par GHCR ; `metadata-action` sanitise en CI, on FIGE le lowercase dans le compose).
> - Services compose : `gluetun`, `amuled`, `crawler`, `verifier`.
> - Réseaux : `ec`, `verify-internal` (`internal: true`), `egress`.
> - Volumes nommés : `quarantine`, `catalog-db`, `local-db`, `amule-state`.
> - Chemins conteneur : crawler config bind-mount RO `/app/config` ; bases `/data/catalog/catalog.db` (volume `catalog-db`), `/data/local/local.db` (volume `local-db`) ; staging+quarantine `/data/quarantine` (même FS, volume `quarantine`) ; verifier quarantaine `/quarantine` (RO, MÊME volume `quarantine`) ; amuled état `/home/amule/.aMule` (volume `amule-state`) + staging `/data/quarantine` (volume `quarantine`).
> - Fichiers compose : `compose.yaml`, `compose.smoke.yaml`, `compose.hardening.yml`.
> - User non-root : `999:999` (groupe/user `nonroot`) pour crawler + verifier.

> **Le gate (6 checks, par paquet — INCHANGÉ, Plan F ne le touche PAS ; rappelé dans CHAQUE step « Vérifier ») :**
> ```bash
> ( cd packages/crawler  && uv run pytest -q )          # crawler tests, 100 % branch
> ( cd packages/verifier && uv run pytest -q )          # verifier tests, 100 % branch
> uv run ruff check .
> uv run ruff format --check .
> uv run mypy
> uv run sqlfluff lint packages/crawler/src
> ```
> Plan F **n'ajoute aucun code PROD Python** → ces 6 checks restent VERTS sans modification. Le SEUL ajout pytest est le marqueur `compose_integration` (Task 5) déselectionné par défaut + le test sous `tests/integration/` (Docker requis, `--no-cov`, donc hors coverage). Run dédié du smoke : `( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )`.

> **Note ordonnancement & convention de run :** Plan F n'est PAS du TDD Python (zéro code PROD) — la « vérification » de CHAQUE tâche est concrète et déterministe : `docker build` réussit / `docker compose config` valide / `compose_integration` passe ET le run par défaut affiche « … deselected » avec 100 % branch INCHANGÉ. Steps bite-sized. Chaque tâche se termine par un commit conventionnel (`build(docker):`, `ci:`, `test(integration):`, `docs:`) dont le message se termine par le trailer HEREDOC `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. **Travailler sur `main`** (le projet le fait) ; le tag jalon est posé en dernière tâche.

> **INCONNUS EMPIRIQUES FLÉCHÉS (à RÉSOUDRE au build, NE PAS DEVINER — résultats à inscrire dans le runbook Task 8) :**
> 1. **Incantation uv workspace** (Tasks 1-2) : quel enchaînement exact installe UN SEUL membre du workspace virtuel dans l'image ? Les docs uv (context7) confirment : couche deps = `uv sync --locked --no-install-workspace` (avec `--frozen` toléré, mais on a le lock à jour → `--locked`), puis couche projet = `uv sync --locked --no-editable --package <dist>`. Le squelette ci-dessous est le POINT DE DÉPART ; l'implémenteur `docker build` réellement et AJUSTE les flags (`--package emule-indexer` vs `download-verifier`, `--no-install-workspace`, `--no-editable`, `--no-dev`) jusqu'à ce que l'image contienne le BON paquet (vérif : `docker run … python -c "import <module>"`) ET PAS l'autre.
> 2. **Libs système re2/rapidfuzz au runtime slim** (Task 2) : `google-re2` (importé `re2`) et `rapidfuzz` embarquent des wheels avec C++/SIMD ; le runtime `python:3.12-slim-bookworm` peut manquer `libstdc++` ou un runtime SIMD. À VALIDER au build : si `import emule_indexer` (qui tire `re2`/`rapidfuzz`) échoue au runtime, ajouter le paquet apt manquant (probablement `libstdc++6`, déjà dans slim — sinon `apt-get install -y --no-install-recommends <lib>`). Documenter le cap réel trouvé.
> 3. **User/PUID de `ngosang/amule`** (Tasks 3-4) : l'image tierce peut imposer son propre user/PUID/`s6`/entrypoint ; le durcissement (non-root, `read_only`) doit COMPOSER avec ce qu'elle permet. À valider au `docker compose config`/`up` : si `read_only`/`user:` casse amuled au démarrage, RELÂCHER ces options POUR amuled SEUL (documenté dans le compose + runbook) — amuled n'est pas une image qu'on bâtit, on ne durcit que ce qu'elle tolère.

---

## File Structure

```
emule-indexer/                                   # RACINE = workspace VIRTUEL
├── compose.yaml                                 # Create (Task 3) : topologie prod, profils observer/full, réseaux/volumes, durcissement
├── compose.smoke.yaml                           # Create (Task 4) : override sans VPN (amuled sur réseau ec, configs smoke)
├── compose.hardening.yml                        # Create (Task 6) : override opt-in (gVisor runtime: runsc)
├── .dockerignore                                # Create (Task 1) : exclut .venv/.git/caches/*.db/config/local.yaml du build context
├── .env.example                                 # Create (Task 8) : modèle secrets ProtonVPN (gluetun) — .env gitignoré
├── .gitignore                                   # Modify (Task 8) : + .env
├── deploy/
│   └── smoke/
│       ├── local.observer.yaml                  # Create (Task 4) : local.yaml smoke observer (EC host=amuled, pas de verifier_url)
│       ├── local.full.yaml                      # Create (Task 4) : local.yaml smoke full (EC host=amuled, verifier_url=http://verifier:8000)
│       ├── crawler.yaml                         # Create (Task 4) : crawler.yaml smoke (cadences courtes pour un smoke rapide)
│       ├── targets.yaml                         # Create (Task 4) : targets.yaml smoke minimal (1 cible)
│       └── matcher.yaml                         # Create (Task 4) : matcher.yaml smoke minimal (1 règle)
├── packages/
│   ├── crawler/
│   │   ├── Dockerfile                           # Create (Task 2) : multi-stage uv, runtime slim, non-root, ENTRYPOINT python -m emule_indexer
│   │   ├── pyproject.toml                       # Modify (Task 5) : + marqueur compose_integration (markers + addopts -m "not …")
│   │   └── tests/integration/
│   │       └── test_compose_smoke.py            # Create (Task 5) : pytestmark = compose_integration (shell-out docker compose)
│   └── verifier/
│       └── Dockerfile                           # Create (Task 1) : multi-stage uv + ffmpeg (ffprobe), non-root, ENTRYPOINT python -m download_verifier
├── .github/workflows/
│   ├── ci.yml                                   # INCHANGÉ (le gate)
│   └── images.yml                               # Create (Task 7) : job smoke (amd64) gate → job publish (multi-arch GHCR)
├── docs/
│   ├── runbook-deployment.md                    # Create (Task 8) : prérequis, .env/local.yaml, observer/full, hardening, validation homelab
│   └── superpowers/plans/
│       └── 2026-06-14-crawler-mvp-09-packaging.md  # CE FICHIER
├── README.md                                    # Modify (Task 8) : pointeur vers le runbook + statut packaging
└── CLAUDE.md                                    # Modify (Task 9) : état courant — Plan F construit
```

> **Carte de cohérence (vérifiée à l'écriture du plan) :**
> - Le crawler n'a AUCUNE variable d'env → son mode vient du `local.yaml` monté + `--local`. `compose.yaml` lance le crawler avec `command: ["--local", "/app/config/local.yaml", "--crawler", "/app/config/crawler.yaml", "--targets", "/app/config/targets.yaml", "--matcher", "/app/config/matcher.yaml"]` (l'`ENTRYPOINT` du Dockerfile = `["python","-m","emule_indexer"]`). Le bind-mount `./config:/app/config:ro` fournit ces fichiers en prod ; le smoke override `command:` + monte `deploy/smoke/*.yaml`.
> - Le verifier lit `QUARANTINE_DIR` (à l'import de `app.py`) + `VERIFIER_HOST`/`VERIFIER_PORT` (dans `__main__.main`). `compose.yaml` pose `environment: { QUARANTINE_DIR: /quarantine, VERIFIER_HOST: 0.0.0.0, VERIFIER_PORT: 8000 }`. Le healthcheck frappe `http://localhost:8000/health`.
> - Le verifier fail-fast côté crawler : si `verifier_url` est dans le `local.yaml` ET le verifier est injoignable au `health()`, `python -m emule_indexer` rend exit 1 (`composition/__main__.main` attrape le `ConfigError` runtime). Le smoke exploite ça (full sans verifier → exit ≠ 0).
> - Build context = racine (`build: { context: ., dockerfile: packages/<pkg>/Dockerfile }`) car un seul `uv.lock` racine ; `.dockerignore` réduit le context.

---

(Les tâches numérotées suivent. Chaque tâche est autonome : artefact écrit → vérification concrète (build/config/marqueur) → commit conventionnel. Le gate 6-checks reste INTACT dans chaque vérification.)

---

## Task 1 : Dockerfile du verifier + `.dockerignore` (valider l'incantation uv workspace)

**Files :**
- Create: `packages/verifier/Dockerfile`
- Create: `.dockerignore`

> Spec §3 (image verifier) + §11 (risque uv workspace + libs système). On commence par le verifier (le plus simple : starlette/uvicorn/puremagic, pas de re2/rapidfuzz) pour **valider empiriquement l'incantation uv workspace** une fois, puis la réutiliser pour le crawler (Task 2). Le runtime ajoute `ffmpeg` (fournit `ffprobe`, requis par D-analysis). **AUCUN code PROD touché.**

- [ ] **Step 1 : Créer `.dockerignore` (racine) — réduire le build context**

`.dockerignore` :
```
.venv/
**/__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/
dist/
*.db
*.db-wal
*.db-shm
.git/
.github/
config/local.yaml
.env
docs/
```
> Le build context = racine (un seul `uv.lock`). On exclut les caches, les bases, le `.git`, la config locale secrète (`config/local.yaml`, `.env`) et `docs/`. On NE PEUT PAS exclure `packages/*/pyproject.toml` ni `uv.lock` (uv en a besoin) ni les `src/` du membre installé. **Ne PAS exclure `deploy/`** (le smoke en a besoin via bind-mount, mais ces fichiers sont montés au runtime, pas copiés dans l'image — les exclure ici est sûr ; on les laisse hors `.dockerignore` par simplicité et parce que le compose les bind-monte depuis l'hôte).

- [ ] **Step 2 : Écrire `packages/verifier/Dockerfile` (squelette uv workspace à VALIDER)**

`packages/verifier/Dockerfile` :
```dockerfile
# syntax=docker/dockerfile:1
# --- Builder : installe download-verifier (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Couche deps : sync SANS le projet/workspace (cache des deps indépendant des sources).
# --no-install-workspace exclut TOUS les membres (le lock ne peut être asserté qu'avec tous
# les pyproject.toml présents → on bind-monte uv.lock + les pyproject racine et membres).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/crawler/pyproject.toml,target=packages/crawler/pyproject.toml \
    --mount=type=bind,source=packages/verifier/pyproject.toml,target=packages/verifier/pyproject.toml \
    uv sync --locked --no-install-workspace --package download-verifier

# Sources + sync du SEUL membre verifier, non-éditable (venv autonome, sans les sources).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package download-verifier

# --- Runtime : python slim + ffmpeg (ffprobe), non-root, venv copié ---
FROM python:3.12-slim-bookworm

# ffmpeg fournit ffprobe (D-analysis). --no-install-recommends + purge des listes apt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "download_verifier"]
```

> **VALIDATION EMPIRIQUE OBLIGATOIRE (inconnu fléché #1) :** le bloc `uv sync` ci-dessus est le POINT DE DÉPART (confirmé par les docs uv context7 : `--no-install-workspace` sur la couche deps, `--no-editable --package` sur la couche projet). Si `docker build` échoue (p. ex. uv refuse `--package` sans `--all-packages`, ou exige `--frozen` au lieu de `--locked`, ou ne trouve pas un pyproject membre), AJUSTER les flags jusqu'à un build vert ET une image qui importe `download_verifier` (Step 3). NE PAS DEVINER l'incantation finale : la dériver du build réel. Inscrire l'incantation EXACTE retenue dans le runbook (Task 8).

- [ ] **Step 3 : Vérifier — build réussit + import du bon paquet**

```bash
docker build -f packages/verifier/Dockerfile -t emule-verifier-test .
docker run --rm -e QUARANTINE_DIR=/tmp emule-verifier-test python -c "import download_verifier, download_verifier.app; print('verifier OK')"
docker run --rm emule-verifier-test ffprobe -version | head -1
```
Expected : le build réussit ; `verifier OK` s'affiche ; `ffprobe version …` s'affiche (ffmpeg présent). Si l'import échoue (`ModuleNotFoundError`) → l'incantation uv n'a pas installé le membre : ajuster les flags (Step 2) et reconstruire. Vérifier que le crawler n'est PAS embarqué (frontière propre) :
```bash
docker run --rm emule-verifier-test python -c "import emule_indexer" 2>&1 | grep -q "ModuleNotFoundError" && echo "frontiere OK (emule_indexer absent du verifier)"
```
Expected : `frontiere OK …` (le verifier ne contient QUE son paquet).

> **Le gate 6-checks reste INTACT** (aucun code PROD touché) — pas besoin de le relancer ici, mais ne JAMAIS le casser : aucune modif sous `packages/*/src/`.

- [ ] **Step 4 : Commit**

```bash
git add packages/verifier/Dockerfile .dockerignore
git commit -m "$(cat <<'EOF'
build(docker): Dockerfile verifier (multi-stage uv + ffmpeg, non-root) + .dockerignore

Image download-verifier : builder uv (workspace virtuel, --no-install-workspace puis
--package download-verifier --no-editable), runtime python:3.12-slim-bookworm + ffmpeg
(ffprobe), user non-root 999. Incantation uv workspace validée au build (import OK,
emule_indexer absent). Zero code PROD touché — gate 6-checks intact.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 : Dockerfile du crawler (re2/rapidfuzz au runtime slim)

**Files :**
- Create: `packages/crawler/Dockerfile`

> Spec §3 (image crawler). Réutilise l'incantation uv workspace VALIDÉE au Task 1, en visant `--package emule-indexer`. Le runtime n'a pas besoin de `ffmpeg` (le crawler ne probe pas), mais **doit faire tourner `google-re2` (importé `re2`) + `rapidfuzz`** : valider au build qu'aucune lib système ne manque (inconnu fléché #2). `ENTRYPOINT ["python","-m","emule_indexer"]`. **AUCUN code PROD touché.**

- [ ] **Step 1 : Écrire `packages/crawler/Dockerfile`**

`packages/crawler/Dockerfile` :
```dockerfile
# syntax=docker/dockerfile:1
# --- Builder : installe emule-indexer (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/crawler/pyproject.toml,target=packages/crawler/pyproject.toml \
    --mount=type=bind,source=packages/verifier/pyproject.toml,target=packages/verifier/pyproject.toml \
    uv sync --locked --no-install-workspace --package emule-indexer

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package emule-indexer

# --- Runtime : python slim, non-root, venv copié ---
FROM python:3.12-slim-bookworm

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "emule_indexer"]
```

> **VALIDATION EMPIRIQUE (inconnu fléché #2) :** `python:3.12-slim-bookworm` embarque `libstdc++6` (suffit généralement pour les wheels manylinux de `google-re2`/`rapidfuzz`). Si l'import au Step 2 échoue avec une erreur de symbole/lib partagée, ajouter AVANT le `COPY --from` un `RUN apt-get update && apt-get install -y --no-install-recommends <lib> && rm -rf /var/lib/apt/lists/*` (la lib réellement manquante, p. ex. `libgomp1` pour SIMD). NE PAS ajouter de lib « au cas où » : n'ajouter QUE ce que le build prouve nécessaire, et le documenter (runbook Task 8).

- [ ] **Step 2 : Vérifier — build + import (re2/rapidfuzz inclus) + entrypoint**

```bash
docker build -f packages/crawler/Dockerfile -t emule-crawler-test .
docker run --rm emule-crawler-test python -c "import emule_indexer, re2, rapidfuzz, httpx, yaml; print('crawler OK')"
docker run --rm emule-crawler-test python -m emule_indexer --help
```
Expected : build réussit ; `crawler OK` (re2/rapidfuzz/httpx/yaml chargent au runtime slim) ; `--help` affiche l'usage argparse (`--crawler/--local/--targets/--matcher`). Vérifier la frontière inverse :
```bash
docker run --rm emule-crawler-test python -c "import download_verifier" 2>&1 | grep -q "ModuleNotFoundError" && echo "frontiere OK (download_verifier absent du crawler)"
```
Expected : `frontiere OK …`. **Gate 6-checks intact** (aucune modif `src/`).

- [ ] **Step 3 : Commit**

```bash
git add packages/crawler/Dockerfile
git commit -m "$(cat <<'EOF'
build(docker): Dockerfile crawler (multi-stage uv, runtime slim, non-root)

Image emule-indexer : builder uv (--no-install-workspace puis --package emule-indexer
--no-editable), runtime python:3.12-slim-bookworm, user non-root 999, ENTRYPOINT
python -m emule_indexer. re2/rapidfuzz/httpx validés au runtime slim. Zero code PROD
touché — gate 6-checks intact.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 : `compose.yaml` — topologie prod (profils observer/full, réseaux, volumes, durcissement)

**Files :**
- Create: `compose.yaml`

> Spec §4 (topologie) + §6 (durcissement F-D2). Quatre services (`gluetun`, `amuled`, `crawler`, `verifier`), profils `observer`/`full`, réseaux `ec`/`verify-internal`(`internal: true`)/`egress`, volumes nommés, durcissement niveau conteneur. Les services bâtis déclarent `image:` ET `build:` (build local/smoke + pull homelab). **AUCUN code PROD touché.** Vérif = `docker compose config` valide (sans monter le VPN).

- [ ] **Step 1 : Écrire `compose.yaml`**

`compose.yaml` :
```yaml
# Topologie de déploiement emule-indexer (spec packaging §4/§6).
# Profils : `observer` (gluetun+amuled+crawler, pas de download/verif) ; `full` (+ verifier,
# auto-download + vérification). Secrets ProtonVPN dans .env (gitignoré ; voir .env.example).
# Le mode du crawler vient de config/local.yaml monté (verifier_url présent => full).
# Durcissement niveau conteneur (F-D2) sur les services bâtis ; gluetun/amuled = exceptions.

services:
  gluetun:
    image: qmcgaw/gluetun:latest
    profiles: [observer, full]
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      VPN_SERVICE_PROVIDER: protonvpn
      VPN_TYPE: wireguard
      WIREGUARD_PRIVATE_KEY: ${WIREGUARD_PRIVATE_KEY}
      SERVER_COUNTRIES: ${SERVER_COUNTRIES:-}
      # NAT-PMP/port-forwarding ProtonVPN : le port-sync (High-ID) est un follow-up (F-D3).
      VPN_PORT_FORWARDING: "on"
    networks:
      - ec
    restart: unless-stopped

  amuled:
    image: ngosang/amule:3.0.0-1
    profiles: [observer, full]
    # Partage la netns de gluetun (killswitch : tout le P2P sort par le VPN). Pas de `networks:`
    # quand network_mode est `service:` — amuled est joignable via gluetun sur le port EC 4712.
    network_mode: "service:gluetun"
    depends_on:
      - gluetun
    environment:
      # Mot de passe EC (secret) — partagé avec le crawler via local.yaml. PUID/PGID : voir
      # l'inconnu fléché #3 (l'image impose son propre user ; on ne durcit que ce qu'elle tolère).
      GUI_PWD: ${AMULE_EC_PASSWORD}
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine        # staging : amuled écrit les fichiers complétés ici
    restart: unless-stopped

  crawler:
    image: ghcr.io/geoffreycoulaud/emule-indexer-crawler:latest
    build:
      context: .
      dockerfile: packages/crawler/Dockerfile
    profiles: [observer, full]
    depends_on:
      - amuled
    command:
      - "--local"
      - "/app/config/local.yaml"
      - "--crawler"
      - "/app/config/crawler.yaml"
      - "--targets"
      - "/app/config/targets.yaml"
      - "--matcher"
      - "/app/config/matcher.yaml"
    volumes:
      - ./config:/app/config:ro                # config (local.yaml décide observer/full)
      - quarantine:/data/quarantine            # cible du os.replace (même FS que staging)
      - catalog-db:/data/catalog               # catalog.db (chemin local.yaml : /data/catalog/catalog.db)
      - local-db:/data/local                   # local.db   (chemin local.yaml : /data/local/local.db)
    networks:
      - ec
      - verify-internal
      - egress
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    pids_limit: 256
    mem_limit: 512m
    restart: unless-stopped

  verifier:
    image: ghcr.io/geoffreycoulaud/emule-indexer-verifier:latest
    build:
      context: .
      dockerfile: packages/verifier/Dockerfile
    profiles: [full]                            # full UNIQUEMENT
    environment:
      QUARANTINE_DIR: /quarantine
      VERIFIER_HOST: 0.0.0.0
      VERIFIER_PORT: "8000"
    volumes:
      - quarantine:/quarantine:ro               # lit la quarantaine en RO (jamais d'écriture)
    networks:
      - verify-internal                         # SEUL sur le réseau interne (pas d'Internet)
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)"
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 10s
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp                                     # mkdtemp de l'enfant d'analyse (D-analysis)
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    pids_limit: 256
    mem_limit: 768m
    restart: unless-stopped

networks:
  ec: {}
  verify-internal:
    internal: true                              # pas d'Internet pour le verifier (ni son enfant)
  egress: {}

volumes:
  quarantine: {}
  catalog-db: {}
  local-db: {}
  amule-state: {}
```

> **Note `depends_on` du crawler vs verifier (full) :** en `full`, le crawler doit attendre que le verifier soit `service_healthy` (sinon le `health()` fail-fast pourrait courir avant que le verifier soit prêt et tuer le crawler à tort). MAIS `depends_on: { verifier: { condition: service_healthy } }` en prod référencerait un service du profil `full` — ce qui est correct car le crawler full tourne avec le verifier. On l'ajoute ICI conditionnellement : la dépendance vers `verifier` ne s'applique QUE quand le profil `full` est actif (compose ignore une dépendance vers un service non démarré dans le profil courant ? NON — compose v2 exige que le service dépendant soit dans le même profil). **Décision (cohérente avec le smoke) :** la dépendance `verifier (service_healthy)` est posée dans `compose.smoke.yaml` (Task 4) pour le scénario full, PAS dans `compose.yaml` (où le crawler doit pouvoir démarrer en `observer` SANS verifier). En prod full, l'utilisateur lance `--profile full` ; le crawler tolère un verifier qui démarre en parallèle grâce à son `health()` + retry implicite ? NON — le `health()` est fail-fast. **Donc** : on FIGE le `depends_on` verifier dans `compose.smoke.yaml` (full smoke) et le runbook documente, pour le full prod, de lancer `--profile full` (les deux services montent ; le `restart: unless-stopped` du crawler le fait redémarrer si le verifier n'était pas encore prêt — acceptable en prod long-running, et le runbook le note). NE PAS mettre `depends_on: verifier` inconditionnel dans `compose.yaml` (casserait le profil observer).

- [ ] **Step 2 : Vérifier — `docker compose config` valide (les deux profils)**

```bash
docker compose -f compose.yaml --profile observer config >/dev/null && echo "observer config OK"
docker compose -f compose.yaml --profile full config >/dev/null && echo "full config OK"
```
Expected : `observer config OK` et `full config OK` (le rendu compose est valide ; les `${…}` non définis donnent un warning mais pas une erreur de schéma — fournir `.env` ou exporter des valeurs bidon si compose se plaint d'une var requise : `WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x docker compose -f compose.yaml --profile full config >/dev/null`). Vérifier la présence des réseaux/volumes :
```bash
docker compose -f compose.yaml --profile full config | grep -E "internal: true|verify-internal|quarantine|read_only|no-new-privileges" | head
```
Expected : `verify-internal`/`internal: true`/`quarantine`/`read_only`/`no-new-privileges` présents. **Gate 6-checks intact.**

- [ ] **Step 3 : Commit**

```bash
git add compose.yaml
git commit -m "$(cat <<'EOF'
build(docker): compose.yaml — topologie prod (profils observer/full, reseaux isoles, durcissement)

gluetun (VPN, NET_ADMIN/tun) + amuled (network_mode service:gluetun, staging) + crawler
(bati, ec/verify-internal/egress, config montee) + verifier (full, verify-internal internal:true,
quarantine RO, healthcheck /health). Durcissement F-D2 : non-root 999, cap_drop ALL,
no-new-privileges, read_only + tmpfs, pids/mem limits. Volumes nommes quarantine/catalog-db/
local-db/amule-state. Zero code PROD touche.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 : `compose.smoke.yaml` + configs smoke (host EC = amuled, sans VPN)

**Files :**
- Create: `compose.smoke.yaml`
- Create: `deploy/smoke/local.observer.yaml`
- Create: `deploy/smoke/local.full.yaml`
- Create: `deploy/smoke/crawler.yaml`
- Create: `deploy/smoke/targets.yaml`
- Create: `deploy/smoke/matcher.yaml`

> Spec §5 (smoke sans VPN) + concrétisation contrôleur (configs montées par scénario). Override qui RETIRE gluetun, fait tourner amuled directement sur le réseau `ec`, et fournit au crawler des `local.yaml` smoke (EC host = `amuled`) montés via `--local`. Volumes éphémères (pas de persistance). Tourne **partout, sans secrets ProtonVPN**. **AUCUN code PROD touché.** Vérif = `docker compose -f compose.yaml -f compose.smoke.yaml config` valide.

- [ ] **Step 1 : Écrire les configs smoke (versionnées sous `deploy/smoke/`, PAS `config/`)**

> Lire d'abord `config/crawler.yaml` et `config/local.example.yaml` pour la FORME exacte des champs (cadences, `amules[].host/port/password`, `catalog_db_path`/`local_db_path`, `verifier_url`). Les chemins de bases pointent sous `/data/catalog/` et `/data/local/` (volumes montés au Task 3).

`deploy/smoke/local.observer.yaml` (mode observateur : PAS de `verifier_url`) :
```yaml
# local.yaml SMOKE — mode OBSERVATEUR (pas de verifier_url => boucles download/verif OFF).
# EC host = amuled (le smoke retire gluetun ; amuled est sur le reseau ec).
amules:
  - name: amule-smoke
    host: amuled
    port: 4712
    password: smoke-ec-password

catalog_db_path: /data/catalog/catalog.db
local_db_path: /data/local/local.db
```

`deploy/smoke/local.full.yaml` (mode full : `verifier_url` présent → câblage download + vérif) :
```yaml
# local.yaml SMOKE — mode FULL (verifier_url present => health-check fail-fast au demarrage).
# EC host = amuled ; download_endpoint sur amuled ; staging+quarantine = meme FS (/data/quarantine).
amules:
  - name: amule-smoke
    host: amuled
    port: 4712
    password: smoke-ec-password

catalog_db_path: /data/catalog/catalog.db
local_db_path: /data/local/local.db

download_endpoint:
  name: amule-smoke-dl
  host: amuled
  port: 4712
  password: smoke-ec-password
staging_dir: /data/quarantine
quarantine_dir: /data/quarantine

verifier_url: http://verifier:8000
```

`deploy/smoke/crawler.yaml` (cadences courtes pour un smoke rapide ; même schéma que `config/crawler.yaml`) :
```yaml
# crawler.yaml SMOKE — cadences courtes (le smoke verifie le cablage, pas la duree).
cycle_interval_seconds: 5.0
search_poll_budget_seconds: 3.0
search_poll_interval_seconds: 1.0
keyword_pause_min_seconds: 0.1
keyword_pause_max_seconds: 0.5
decision_poll_interval_seconds: 2.0
shutdown_deadline_seconds: 5.0

backoff:
  base_seconds: 1.0
  cap_seconds: 10.0
  factor: 2.0
  jitter_ratio: 0.3

download:
  poll_interval_seconds: 3.0
  disk_cap_bytes: 53687091200

verify:
  poll_interval_seconds: 3.0
```

`deploy/smoke/targets.yaml` (minimal — 1 cible ; respecter le schéma de `config/targets.yaml`) :
```yaml
# targets.yaml SMOKE — 1 cible minimale (le smoke n'evalue pas le matching, juste le cablage).
targets:
  - season: 2
    episode: 62
    part: A
    title: "Keroro mission Titar"
    air_date: "2008-09-13"
```
> **Lire `config/targets.yaml` AVANT d'écrire ceci** : si le schéma réel diffère (noms de champs, format), reprendre EXACTEMENT sa forme — un targets invalide ferait fail-fast le crawler au démarrage (ce qui casserait le scénario observer du smoke, qui attend un crawler Up). Le but est un targets MINIMAL mais VALIDE.

`deploy/smoke/matcher.yaml` (minimal — 1 règle ; respecter le schéma de `config/matcher.yaml`) :
```yaml
# matcher.yaml SMOKE — 1 regle minimale valide (le smoke ne teste pas le matching).
# Reprendre EXACTEMENT la forme de config/matcher.yaml (tokens/rules/tiers) — lire le fichier.
```
> **Lire `config/matcher.yaml` AVANT d'écrire ceci** et y copier une version MINIMALE mais structurellement VALIDE (la `validate_config` fail-fast au démarrage sinon). Si `config/matcher.yaml` est déjà minimal, le réutiliser tel quel.

- [ ] **Step 2 : Écrire `compose.smoke.yaml` (override sans VPN)**

`compose.smoke.yaml` :
```yaml
# Override SMOKE (spec packaging §5) : stack assemblee SANS gluetun, sans secrets ProtonVPN.
# amuled tourne directement sur le reseau `ec` ; le crawler vise amuled (configs deploy/smoke/).
# Volumes ephemeres (pas de persistance). Usage :
#   docker compose -f compose.yaml -f compose.smoke.yaml --profile full up -d --build
# Le test compose_integration (Task 5) pilote cette stack et asserte le cablage.

services:
  # gluetun retire du smoke : on neutralise le service (profil inexistant => jamais demarre).
  gluetun:
    profiles: [disabled]

  amuled:
    # Plus de network_mode service:gluetun : amuled rejoint directement le reseau `ec`.
    network_mode: null
    networks:
      - ec
    depends_on: []
    environment:
      GUI_PWD: smoke-ec-password

  crawler:
    # Le mode (observer/full) est choisi par le local.yaml monte (override au lancement) :
    # le test bind-monte deploy/smoke/local.observer.yaml ou local.full.yaml sur ce chemin.
    volumes:
      - ./deploy/smoke/local.full.yaml:/app/config/local.yaml:ro
      - ./deploy/smoke/crawler.yaml:/app/config/crawler.yaml:ro
      - ./deploy/smoke/targets.yaml:/app/config/targets.yaml:ro
      - ./deploy/smoke/matcher.yaml:/app/config/matcher.yaml:ro
      - quarantine:/data/quarantine
      - catalog-db:/data/catalog
      - local-db:/data/local
    depends_on:
      amuled:
        condition: service_started
      verifier:
        condition: service_healthy

  verifier:
    # En smoke le verifier reste sur verify-internal ; le crawler l'atteint via ce reseau.
    volumes:
      - quarantine:/quarantine:ro
```

> **Note sur le choix observer/full dans le test :** `compose.smoke.yaml` câble par défaut le `local.full.yaml` + le `depends_on: verifier (service_healthy)`. Le test (Task 5) exerce DEUX scénarios en surchargeant la config montée :
> - **full nominal** : `--profile full up` (verifier présent + healthy) → crawler Up.
> - **observer** : monter `local.observer.yaml` à la place + `--profile observer up` (pas de verifier) → crawler Up sans verifier.
> - **full fail-fast** : `local.full.yaml` mais verifier ABSENT (lancer le crawler sans le profil full / sans verifier, ou stopper le verifier) → crawler exit ≠ 0.
> Le test réalise ces surcharges via des fichiers d'override pytest-générés (`tmp_path`) OU via des variantes de `command`/montage — la mécanique exacte est dans le test (Task 5). `compose.smoke.yaml` fournit la BASE full ; les variantes observer/fail-fast sont des overrides additionnels écrits par le test.

- [ ] **Step 3 : Vérifier — config combinée valide**

```bash
docker compose -f compose.yaml -f compose.smoke.yaml --profile full config >/dev/null && echo "smoke full config OK"
docker compose -f compose.yaml -f compose.smoke.yaml --profile observer config >/dev/null && echo "smoke observer config OK"
```
Expected : les deux affichent `… config OK` ; aucune référence à gluetun dans le rendu full (profil `disabled` non actif) :
```bash
docker compose -f compose.yaml -f compose.smoke.yaml --profile full config | grep -c "service:gluetun"
```
Expected : `0` (amuled n'utilise plus la netns gluetun en smoke). Valider AUSSI que les configs smoke sont des YAML valides + que le crawler démarre dessus (montage réel — sera prouvé au Task 5 ; ici juste la forme). **Gate 6-checks intact.**

- [ ] **Step 4 : Commit**

```bash
git add compose.smoke.yaml deploy/smoke/
git commit -m "$(cat <<'EOF'
build(docker): compose.smoke.yaml + configs smoke (host EC=amuled, sans VPN)

Override smoke : retire gluetun, amuled sur le reseau ec, crawler vise amuled via
deploy/smoke/local.{observer,full}.yaml montes. crawler.yaml cadences courtes, targets/
matcher minimaux valides. Volumes ephemeres. Tourne partout sans secrets ProtonVPN.
Zero code PROD touche.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 : Test `compose_integration` (smoke e2e) + enregistrement du marqueur

**Files :**
- Modify: `packages/crawler/pyproject.toml` (ajout du marqueur `compose_integration` : `markers=[...]` + `addopts -m "not …"`)
- Create: `packages/crawler/tests/integration/test_compose_smoke.py`

> Spec §5 (F-D1). Le marqueur `compose_integration` est enregistré (désélectionné par défaut) ; le test shell-out `docker compose -f compose.yaml -f compose.smoke.yaml …` dans un `try/finally` avec `docker compose down -v`. **CECI N'EST PAS du code PROD** (test sous `tests/`, `--no-cov`, Docker requis). Le SEUL changement au `pyproject.toml` crawler est l'ajout du marqueur (motif identique à `ec_integration`/etc.) — **le gate 100 % branch reste intact** (le marqueur est déselectionné par défaut, le test est hors coverage).

- [ ] **Step 1 : Enregistrer le marqueur `compose_integration` dans `packages/crawler/pyproject.toml`**

> Lire d'abord `packages/crawler/pyproject.toml`. Ajouter `and not compose_integration` à la fin du `-m "…"` de `addopts`, et une entrée `markers`. Le bloc `[tool.pytest.ini_options]` devient :
```toml
[tool.pytest.ini_options]
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration and not orchestration_integration and not download_integration and not verify_integration and not compose_integration"'
testpaths = ["tests"]
markers = [
    "ec_integration: tests d'intégration contre un amuled réel (Docker requis) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m ec_integration --no-cov",
    "orchestration_integration: boucle de crawl réelle contre un amuled testcontainers (Docker requis) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m orchestration_integration --no-cov",
    "download_integration: add_link + lecture de la file de download contre un amuled réel (Docker requis) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m download_integration --no-cov",
    "verify_integration: boucle de vérification contre le vrai service verifier (ASGITransport, sans Docker) — déselectionnés par défaut ; run dédié : cd packages/crawler && uv run pytest -m verify_integration --no-cov",
    "compose_integration: smoke e2e de la stack docker compose assemblée (sans VPN) — Docker+compose requis ; déselectionné par défaut ; run dédié : cd packages/crawler && uv run pytest -m compose_integration --no-cov",
]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"
```
(Le reste du fichier — `[project]`, `[build-system]`, `[tool.hatch...]`, `[tool.coverage...]`, `[tool.sqlfluff...]` — INCHANGÉ.)

- [ ] **Step 2 : Écrire `packages/crawler/tests/integration/test_compose_smoke.py`**

> **Lire d'abord** `packages/crawler/tests/integration/test_amuled_ec.py` (modèle de test d'intégration : `pytestmark`, fixtures, readiness). Le smoke n'utilise PAS testcontainers (il pilote `docker compose`) mais reprend la discipline : marqueur, `try/finally`, timeouts bornés. Le test calcule la racine du dépôt depuis `Path(__file__)` (`parents[3]` : `integration`[0], `tests`[1], `crawler`[2], `packages`[3]… → racine = `parents[4]` ; VÉRIFIER en lisant `test_main.py` qui utilise `parents[4]` pour atteindre `config/` racine). Le `cwd` des commandes `docker compose` = la racine du dépôt (où vivent `compose.yaml`/`compose.smoke.yaml`/`deploy/`).

`packages/crawler/tests/integration/test_compose_smoke.py` :
```python
"""Smoke e2e de la stack docker compose ASSEMBLÉE, sans VPN (spec packaging §5 — F-D1).

Run dédié : ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 requis. Monte verifier + crawler + amuled (gluetun retiré via
compose.smoke.yaml) et asserte le CÂBLAGE — AUCUN téléchargement réel (amuled n'a ni serveur
eD2k ni VPN ; seul son serveur EC est sollicité) :
  1. `docker compose build` réussit (les 2 images se construisent).
  2. full : verifier devient healthy (/health 200) ET le crawler reste Up.
  3. observer : crawler démarre SANS verifier et reste Up.
  4. full fail-fast : crawler full avec verifier_url mais verifier ABSENT => exit != 0.
Volumes éphémères : chaque scénario fait `docker compose down -v` dans un finally.
"""

import json
import subprocess
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

pytestmark = pytest.mark.compose_integration

# Racine du dépôt (où vivent compose.yaml / compose.smoke.yaml / deploy/). Depuis
# tests/integration/<file> : integration[0], tests[1], crawler[2], packages[3], racine[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Projet compose isolé (évite de heurter une stack existante de la machine).
_PROJECT = "emule-smoke-test"
_BASE: tuple[str, ...] = (
    "docker", "compose",
    "-p", _PROJECT,
    "-f", "compose.yaml",
    "-f", "compose.smoke.yaml",
)
# Variables d'env requises par compose.yaml (gluetun) — valeurs bidon : gluetun est désactivé
# en smoke, mais compose.yaml référence ces vars au rendu.
_ENV_STUB = {
    "WIREGUARD_PRIVATE_KEY": "smoke",
    "SERVER_COUNTRIES": "",
    "AMULE_EC_PASSWORD": "smoke-ec-password",
}


def _run(args: Sequence[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    """Exécute une commande docker compose à la racine du dépôt, env stubé."""
    import os

    env = {**os.environ, **_ENV_STUB}
    return subprocess.run(
        args, cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )


def _down() -> None:
    """Teardown : stoppe la stack + supprime les volumes éphémères (best-effort)."""
    _run((*_BASE, "down", "-v", "--remove-orphans"), timeout=180.0)


@pytest.fixture
def compose_stack() -> Iterator[None]:
    """Garantit un teardown même si le test échoue (volumes éphémères)."""
    _down()  # nettoie un éventuel résidu d'un run précédent
    try:
        yield None
    finally:
        _down()


def _service_state(service: str) -> str:
    """État courant d'un service (`running`/`exited`/…) via `compose ps --format json`."""
    result = _run((*_BASE, "ps", "-a", "--format", "json", service), timeout=60.0)
    states: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        states.append(str(json.loads(line).get("State", "")))
    return states[0] if states else ""


def _exit_code(service: str) -> int:
    """Code de sortie d'un service arrêté (via `docker inspect` du conteneur compose)."""
    name = f"{_PROJECT}-{service}-1"
    result = _run(("docker", "inspect", "-f", "{{.State.ExitCode}}", name), timeout=60.0)
    return int(result.stdout.strip() or "-1")


def _wait_running(service: str, *, deadline_s: float = 90.0) -> str:
    """Attend qu'un service soit `running` (ou retourne son dernier état au deadline)."""
    end = time.monotonic() + deadline_s
    state = ""
    while time.monotonic() < end:
        state = _service_state(service)
        if state == "running":
            return state
        if state == "exited":
            return state
        time.sleep(2.0)
    return state


def test_build_succeeds(compose_stack: None) -> None:
    """Les deux images bâties se construisent (crawler + verifier)."""
    result = _run((*_BASE, "--profile", "full", "build"))
    assert result.returncode == 0, result.stderr


def test_full_verifier_healthy_and_crawler_up(compose_stack: None) -> None:
    """Full : verifier healthy (/health 200) ET le crawler reste Up (pas de fail-fast)."""
    up = _run((*_BASE, "--profile", "full", "up", "-d", "--build"))
    assert up.returncode == 0, up.stderr
    # depends_on: verifier (service_healthy) => si le crawler est `running`, le verifier est sain.
    assert _wait_running("verifier") == "running"
    # Le crawler full a passé son health-gate (verifier sain) et tourne en régime permanent.
    assert _wait_running("crawler") == "running"
    # Confirme directement le /health (200) depuis le conteneur verifier.
    probe = _run(
        (
            *_BASE, "exec", "-T", "verifier", "python", "-c",
            "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health').status)",
        ),
        timeout=60.0,
    )
    assert probe.returncode == 0 and probe.stdout.strip() == "200", probe.stderr


def test_observer_starts_without_verifier(compose_stack: None) -> None:
    """Observer : crawler démarre SANS verifier et reste Up (local.observer.yaml monté)."""
    # Override : monte le local.yaml observateur à la place du full, sans le service verifier.
    override = _REPO_ROOT / "deploy" / "smoke" / "compose.observer-override.yaml"
    override.write_text(
        "services:\n"
        "  crawler:\n"
        "    depends_on:\n"
        "      amuled:\n"
        "        condition: service_started\n"
        "    volumes:\n"
        "      - ./deploy/smoke/local.observer.yaml:/app/config/local.yaml:ro\n"
        "      - ./deploy/smoke/crawler.yaml:/app/config/crawler.yaml:ro\n"
        "      - ./deploy/smoke/targets.yaml:/app/config/targets.yaml:ro\n"
        "      - ./deploy/smoke/matcher.yaml:/app/config/matcher.yaml:ro\n"
        "      - quarantine:/data/quarantine\n"
        "      - catalog-db:/data/catalog\n"
        "      - local-db:/data/local\n",
        encoding="utf-8",
    )
    base = (
        "docker", "compose", "-p", _PROJECT,
        "-f", "compose.yaml", "-f", "compose.smoke.yaml",
        "-f", str(override.relative_to(_REPO_ROOT)),
    )
    try:
        up = _run((*base, "--profile", "observer", "up", "-d", "--build"))
        assert up.returncode == 0, up.stderr
        assert _wait_running("crawler") == "running"
    finally:
        override.unlink(missing_ok=True)


def test_full_without_verifier_fails_fast(compose_stack: None) -> None:
    """Full fail-fast : verifier_url présent mais verifier ABSENT => crawler exit != 0."""
    # On démarre amuled + crawler en full SANS le verifier (scale verifier à 0).
    up = _run(
        (*_BASE, "--profile", "full", "up", "-d", "--build", "--scale", "verifier=0",
         "amuled", "crawler"),
    )
    # `up` peut réussir (conteneurs créés) ; le crawler full health-gate le verifier absent
    # et SORT promptement en erreur (composition/__main__ : ConfigError runtime => exit 1).
    assert up.returncode == 0, up.stderr
    state = _wait_running("crawler", deadline_s=60.0)
    assert state == "exited", f"attendu exited (fail-fast), obtenu {state}"
    assert _exit_code("crawler") != 0
```

> **Notes de robustesse (à confirmer au premier run réel) :**
> - Le format de `docker compose ps --format json` (une ligne JSON par service en compose v2 récent ; clé `State`). Si la version locale émet un tableau JSON unique, adapter `_service_state` (parser `json.loads(result.stdout)` comme liste). Le confirmer au run et figer.
> - `--scale verifier=0` : si la version de compose ne le supporte pas avec un profil, alternative = ne PAS activer le profil full pour le verifier (lancer `up amuled crawler` avec un override qui retire `verifier` des `depends_on`). Choisir la variante qui marche au run et la figer.
> - Timeouts bornés (`deadline_s`) : ajuster si le build/pull est lent en local (le build est caché entre scénarios). Le `try/finally` garantit le `down -v`.

- [ ] **Step 3 : Vérifier — marqueur déselectionné par défaut + smoke passe (Docker requis)**

Run par défaut (le smoke est DÉSELECTIONNÉ ; coverage INCHANGÉE) :
```bash
( cd packages/crawler && uv run pytest -q )
```
Expected : `… passed, N deselected` où **N inclut désormais `compose_integration`** (ec + orchestration + download + verify + **compose**), **100.00 % branch** sur `emule_indexer` (le test smoke est hors coverage — déselectionné). Le verifier inchangé :
```bash
( cd packages/verifier && uv run pytest -q )
```
Expected : inchangé, 100 %. Run DÉDIÉ du smoke (Docker requis — fait foi) :
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
```
Expected : `4 passed` (build + full healthy + observer + full fail-fast). Puis le RESTE du gate :
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint packages/crawler/src
```
Expected : tout vert (le test est typé `-> None`, params typés, conforme ruff/mypy). **Le gate 6-checks reste 100 % branch** (aucun code PROD ; le marqueur est déselectionné).

> **Note couverture :** `compose_integration` est déselectionné par `addopts` → le fichier `test_compose_smoke.py` n'est PAS collecté au run par défaut → il ne participe PAS au calcul de coverage `emule_indexer`. Le 100 % branch est donc strictement préservé. Le test n'importe AUCUN module `emule_indexer` (il shell-out docker) → zéro impact sur la coverage même s'il était collecté.

- [ ] **Step 4 : Commit**

```bash
git add packages/crawler/pyproject.toml packages/crawler/tests/integration/test_compose_smoke.py
git commit -m "$(cat <<'EOF'
test(integration): smoke compose_integration (stack assemblee sans VPN) + marqueur

Marqueur compose_integration enregistre (deselectionne par defaut, --no-cov, Docker requis).
test_compose_smoke : build OK, full verifier healthy + crawler Up, observer sans verifier,
full fail-fast quand verifier absent (exit != 0 via docker inspect). Shell-out docker compose,
try/finally down -v. Zero code PROD touche ; 100% branch inchange (test hors coverage).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 : `compose.hardening.yml` (ring noyau opt-in — gVisor)

**Files :**
- Create: `compose.hardening.yml`

> Spec §6 (F-D2 ring noyau opt-in). Override NON requis qui ajoute `runtime: runsc` (gVisor) sur les services bâtis (crawler + verifier) — la couche reportée de D-analysis (DA1). Exige un support hôte (gVisor installé) ; jamais nécessaire pour démarrer/smoke-tester. **AUCUN code PROD touché.** Vérif = `docker compose -f compose.yaml -f compose.hardening.yml config` valide.

- [ ] **Step 1 : Écrire `compose.hardening.yml`**

`compose.hardening.yml` :
```yaml
# Override DURCISSEMENT OPT-IN (spec packaging §6 — ring noyau, F-D2 / D-analysis DA1).
# NON requis : ajoute le runtime gVisor (runsc) aux services batis pour confiner l'enfant
# d'analyse (verifier) et le crawler au niveau noyau. Exige gVisor installe sur l'hote
# (`runsc` enregistre comme runtime Docker). Usage :
#   docker compose -f compose.yaml -f compose.hardening.yml --profile full up -d
# Si l'hote ne supporte pas runsc, NE PAS utiliser cet override (la stack de base est deja
# durcie au niveau conteneur : non-root, cap_drop ALL, no-new-privileges, read_only, internal).

services:
  crawler:
    runtime: runsc

  verifier:
    runtime: runsc
```

> **Note bwrap (alternative documentée, non un service compose) :** le runbook (Task 8) mentionne que, pour un confinement par-enfant SANS gVisor, l'enfant d'analyse de D-analysis pourrait être ré-encapsulé via `bwrap --net none` — mais c'est un changement CODE (hors Plan F, hors périmètre) ; l'override compose ici ne propose QUE gVisor (niveau runtime, sans toucher le code).

- [ ] **Step 2 : Vérifier — config combinée valide**

```bash
WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x docker compose -f compose.yaml -f compose.hardening.yml --profile full config | grep -E "runtime: runsc" | wc -l
```
Expected : `2` (crawler + verifier portent `runtime: runsc`). Que le rendu reste valide :
```bash
WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x docker compose -f compose.yaml -f compose.hardening.yml --profile full config >/dev/null && echo "hardening config OK"
```
Expected : `hardening config OK`. (On ne lance PAS `up` ici : `runsc` n'est pas garanti présent sur la machine de dev — c'est précisément pourquoi l'override est opt-in.) **Gate 6-checks intact.**

- [ ] **Step 3 : Commit**

```bash
git add compose.hardening.yml
git commit -m "$(cat <<'EOF'
build(docker): compose.hardening.yml — ring noyau opt-in (gVisor runtime: runsc)

Override NON requis (F-D2 / D-analysis DA1) : runtime: runsc sur crawler + verifier pour
confiner au niveau noyau. Exige gVisor sur l'hote ; jamais necessaire pour demarrer
(la base est deja durcie niveau conteneur). Zero code PROD touche.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 : `.github/workflows/images.yml` (CI build + publish GHCR multi-arch)

**Files :**
- Create: `.github/workflows/images.yml`

> Spec §7 (F-D6). Nouveau workflow (le `ci.yml` existant = gate, **INCHANGÉ**). Job `smoke` (amd64, `docker compose build` + `compose_integration`) **gate** job `publish` (`needs: smoke`, buildx multi-arch `amd64+arm64` → GHCR). Triggers : push `main` + tags `v*` + `workflow_dispatch` (**PAS** sur PR). `permissions: { contents: read, packages: write }`. **Dormant** tant que rien n'est poussé. **AUCUN code PROD touché.**

- [ ] **Step 1 : Écrire `.github/workflows/images.yml`**

`.github/workflows/images.yml` :
```yaml
name: Images

on:
  push:
    branches: [main]
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --dev
      # Build local (amd64 natif) des 2 images + smoke e2e de la stack assemblee (sans VPN).
      - name: Build images (smoke)
        run: |
          WIREGUARD_PRIVATE_KEY=ci AMULE_EC_PASSWORD=smoke-ec-password \
            docker compose -p emule-smoke-ci -f compose.yaml -f compose.smoke.yaml --profile full build
      - name: Run compose smoke e2e
        run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )

  publish:
    needs: smoke
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - package: crawler
            image: ghcr.io/${{ github.repository }}-crawler
            dockerfile: packages/crawler/Dockerfile
          - package: verifier
            image: ghcr.io/${{ github.repository }}-verifier
            dockerfile: packages/verifier/Dockerfile
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ matrix.image }}
          tags: |
            type=ref,event=branch
            type=sha
            type=semver,pattern={{version}}
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: ${{ matrix.dockerfile }}
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

> **Notes :**
> - `metadata-action` sanitise automatiquement en **lowercase** le nom d'image dérivé de `${{ github.repository }}` (`GeoffreyCoulaud/emule-indexer` → `geoffreycoulaud/emule-indexer-crawler`) — c'est pourquoi on s'appuie dessus pour les tags GHCR plutôt que de coder le lowercase à la main dans le workflow.
> - Le job `smoke` réutilise EXACTEMENT les mêmes Dockerfiles + `compose.smoke.yaml` + le marqueur `compose_integration` que le dev local → coût marginal nul (mêmes images déjà construites localement).
> - `publish` ne tourne QUE si `smoke` réussit (`needs: smoke`) → on ne pousse jamais une stack cassée.
> - **PAS de `pull_request`** dans `on:` (F-D6 : lourd ; le gate `ci.yml` couvre les PR).
> - Le job `smoke` exécute le smoke en CI (Docker dispo sur `ubuntu-latest`). Si le smoke est trop lent/flaky en CI, le RUNBOOK note l'option de réduire `smoke` à un `docker compose build` seul (sans le marqueur) — mais le défaut FIGÉ ici est build + smoke (gate réel de la publication).

- [ ] **Step 2 : Vérifier — lint du workflow (actionlint/yamllint si dispo) + relecture**

```bash
# Si actionlint est dispo (sinon, relecture manuelle + yamllint) :
command -v actionlint >/dev/null 2>&1 && actionlint .github/workflows/images.yml || echo "actionlint absent — relecture manuelle"
command -v yamllint >/dev/null 2>&1 && yamllint .github/workflows/images.yml || echo "yamllint absent"
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/images.yml')); print('YAML images.yml valide')"
```
Expected : `actionlint` clean (ou relecture manuelle confirmant : versions `@v4/@v3/@v5/@v6` correctes, `needs: smoke`, `permissions: packages: write`, `platforms: linux/amd64,linux/arm64`, PAS de `pull_request`) ; `YAML images.yml valide`. Confirmer que `ci.yml` n'a PAS bougé :
```bash
git diff --stat .github/workflows/ci.yml
```
Expected : **AUCUNE ligne** (`ci.yml` strictement inchangé — le gate ne bouge pas). Le workflow `images.yml` est dormant (ne tourne qu'au push `main`/tag/dispatch). **Gate 6-checks intact.**

- [ ] **Step 3 : Commit**

```bash
git add .github/workflows/images.yml
git commit -m "$(cat <<'EOF'
ci: images.yml — build smoke (amd64) gate -> publish GHCR multi-arch (amd64+arm64)

Job smoke (compose build + compose_integration) gate le job publish (buildx multi-arch
crawler+verifier -> GHCR via metadata-action lowercase + build-push-action). Triggers : push
main + tags v* + workflow_dispatch (pas sur PR). ci.yml (le gate) inchange. Dormant tant que
rien n'est pousse. Zero code PROD touche.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 : Runbook de déploiement + `.env.example` + `.gitignore`/README

**Files :**
- Create: `docs/runbook-deployment.md`
- Create: `.env.example`
- Modify: `.gitignore` (+ `.env`)
- Modify: `README.md` (pointeur runbook + statut packaging)

> Spec §8 (runbook) + §11 (notes : GHCR privé par défaut, libs système, user amuled). Documente les prérequis, le setup `.env`/`local.yaml`, le démarrage observer/full, l'override durcissement, la validation homelab manuelle, et **les inconnus empiriques résolus aux Tasks 1-4** (incantation uv workspace, libs système, user amuled). **AUCUN code PROD touché.**

- [ ] **Step 1 : Créer `.env.example` (modèle secrets ProtonVPN/amuled)**

`.env.example` :
```bash
# Secrets de déploiement emule-indexer — COPIER en .env (gitignoré), renseigner les vraies valeurs.
# Le .env est lu automatiquement par `docker compose` (variables ${...} de compose.yaml).

# --- gluetun (ProtonVPN, WireGuard) ---
WIREGUARD_PRIVATE_KEY=changeme-protonvpn-wireguard-private-key
SERVER_COUNTRIES=Switzerland

# --- amuled (mot de passe EC, partagé avec le crawler via config/local.yaml) ---
AMULE_EC_PASSWORD=changeme-ec-password
```

- [ ] **Step 2 : Ajouter `.env` au `.gitignore`**

> Lire d'abord `.gitignore`. Ajouter une ligne `.env` (les secrets de déploiement ne sont JAMAIS versionnés ; `.env.example` l'est comme modèle). Ajouter à la fin :
```
# Secrets de deploiement (ProtonVPN, EC password) — JAMAIS versionnes. Seul .env.example l'est.
.env
```

- [ ] **Step 3 : Écrire `docs/runbook-deployment.md`**

> Contenu COMPLET (sections fixées par la spec §8 + les inconnus résolus). Structure :
> - **Prérequis** : Docker + Buildx + docker compose v2 ; identifiants ProtonVPN (clé WireGuard) ; `/dev/net/tun` disponible sur l'hôte ; (opt-in) gVisor pour `compose.hardening.yml`.
> - **Incantation uv workspace (résolue au build)** : inscrire l'enchaînement EXACT retenu aux Tasks 1-2 (`uv sync --locked --no-install-workspace` puis `uv sync --locked --no-editable --package <dist>`, avec les éventuels ajustements). Noter les libs système ajoutées au runtime crawler (inconnu #2) si une l'a été.
> - **User amuled** (inconnu #3) : noter si `read_only`/`user:` a dû être relâché pour `amuled` (image tierce) et comment.
> - **Setup** : `cp .env.example .env` (renseigner WireGuard + EC password) ; `cp config/local.example.yaml config/local.yaml` (renseigner amules[].host=`gluetun`, port 4712, password = `AMULE_EC_PASSWORD` ; chemins bases ; pour le mode **full** décommenter `download_endpoint`/`staging_dir`/`quarantine_dir` (`/data/quarantine`) + `verifier_url: http://verifier:8000`).
> - **Démarrage** :
>   - Observer : `docker compose --profile observer up -d` (gluetun + amuled + crawler ; pas de download/verif).
>   - Full : `docker compose --profile full up -d` (+ verifier ; auto-download + vérification). Note : le crawler full health-gate le verifier au démarrage — si le verifier n'est pas encore prêt, le `restart: unless-stopped` le relance jusqu'à ce que le verifier soit sain (acceptable en long-running ; ou démarrer le verifier d'abord : `docker compose --profile full up -d verifier && … up -d`).
>   - Pull GHCR (homelab) : `docker compose pull` puis `up` (les images bâties ont `image:` + `build:`) ; build local : `docker compose --profile full build`.
> - **Durcissement opt-in** : `docker compose -f compose.yaml -f compose.hardening.yml --profile full up -d` (exige gVisor `runsc` sur l'hôte ; sinon ne pas l'utiliser — la base est déjà durcie niveau conteneur).
> - **GHCR visibilité** (spec §11) : les packages GHCR sont **privés par défaut** ; les rendre publics dans les settings du package, OU `docker login ghcr.io -u <user>` (PAT avec `read:packages`) avant `docker compose pull`.
> - **Validation homelab manuelle** : monter `full`, `docker compose logs -f crawler`, confirmer le déroulé recherche → download → quarantaine → vérif sur le vrai eMule. **Low-ID pour l'instant** (le High-ID attend le follow-up port-sync — F-D3). Où vivent les données : volumes nommés `quarantine`/`catalog-db`/`local-db`/`amule-state` (`docker volume inspect emule-indexer_quarantine`, `docker compose exec crawler ls /data`). Inspecter la quarantaine : `docker compose exec verifier ls /quarantine`.
> - **Smoke local** : `( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )` (Docker requis ; monte la stack sans VPN et asserte le câblage).
> - **Limites connues / follow-ups** : port-sync/High-ID (F-D3), clamav (après Plan F, F-D5), ring noyau bwrap par-enfant (changement code, hors Plan F), sous-commandes CLI (F-D4).

`docs/runbook-deployment.md` (rédiger le contenu complet ci-dessus en Markdown structuré ; renseigner les VALEURS empiriques réelles trouvées aux Tasks 1-4 — incantation uv, libs système, user amuled).

- [ ] **Step 4 : Pointeur dans le README**

> Lire d'abord `README.md`. Dans la section « Pour les chercheurs » (qui dit déjà « ⚙️ Le packaging docker compose arrive dans un incrément ultérieur »), remplacer cette note par un pointeur vers le runbook + le statut « packaging livré ». Ajouter sous « Conception » une ligne :
```markdown
- Déploiement (Docker / compose) : [`docs/runbook-deployment.md`](docs/runbook-deployment.md)
```
Et remplacer la note « ⚙️ Le packaging … arrive dans un incrément ultérieur » par :
```markdown
> 🐳 Déploiement `docker compose` (profils `observer`/`full`) disponible — voir
> [`docs/runbook-deployment.md`](docs/runbook-deployment.md). Le mode `observer` ne télécharge rien.
```

- [ ] **Step 5 : Vérifier**

```bash
python -c "import pathlib; assert pathlib.Path('docs/runbook-deployment.md').read_text(encoding='utf-8').strip(); print('runbook non vide')"
grep -q "^\.env$" .gitignore && echo ".env gitignore OK"
grep -q "runbook-deployment" README.md && echo "README pointeur OK"
```
Expected : `runbook non vide`, `.env gitignore OK`, `README pointeur OK`. Confirmer qu'aucun secret réel n'est commité (`.env.example` ne contient que des placeholders `changeme-…`). **Gate 6-checks intact** (docs/config seulement).

- [ ] **Step 6 : Commit**

```bash
git add docs/runbook-deployment.md .env.example .gitignore README.md
git commit -m "$(cat <<'EOF'
docs: runbook de deploiement + .env.example + .gitignore (.env) + pointeur README

Runbook : prerequis, incantation uv workspace resolue, libs systeme/user amuled,
setup .env/local.yaml, demarrage observer/full, durcissement opt-in, GHCR visibilite,
validation homelab manuelle (Low-ID). .env.example (ProtonVPN/EC) ; .env gitignore.
Zero code PROD touche.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 : Revue holistique finale + handoff + CLAUDE.md + TAG (clôt Plan F)

**Files :** (aucun artefact Docker créé — vérification + handoff + CLAUDE.md + tag annoté)

> La revue holistique attrape les bugs cross-cutting (méthode reconduite — elle a attrapé un bug réel à chaque jalon). Plan F clôt le packaging ; cette tâche POSE le tag annoté. Greps de cohérence (zéro code PROD, frontière de paquet), gate complet INCHANGÉ, smoke, handoff, CLAUDE.md, puis tag. **AUCUN code PROD touché.**

- [ ] **Step 1 : Greps — ZÉRO code PROD Python ajouté (CRITIQUE)**

```bash
git diff v0.9.0-analysis --stat -- 'packages/crawler/src/**' 'packages/verifier/src/**'
```
Expected : **AUCUNE ligne** (aucun fichier source modifié par Plan F depuis le tag précédent). Vérifier que la frontière de paquet reste intacte (inchangée par Plan F) :
```bash
grep -rn "download_verifier" packages/crawler/src/ ; grep -rn "emule_indexer" packages/verifier/src/
```
Expected : **AUCUNE sortie** (les deux). Vérifier que `ci.yml` n'a pas bougé :
```bash
git diff v0.9.0-analysis -- .github/workflows/ci.yml
```
Expected : **AUCUNE sortie**.

- [ ] **Step 2 : Revue de cohérence (lecture humaine/subagent — noms canoniques + câblage)**

Confirmer explicitement (chacun figé dans une tâche, la revue confirme la cohérence inter-tâches) :
- **Noms canoniques identiques partout** : images `ghcr.io/…-crawler`/`…-verifier` (compose Task 3 + workflow Task 7) ; services `gluetun/amuled/crawler/verifier` ; réseaux `ec`/`verify-internal`(`internal: true`)/`egress` ; volumes `quarantine`/`catalog-db`/`local-db`/`amule-state` ; chemins `/app/config` (RO), `/data/quarantine` (crawler+amuled), `/quarantine` (verifier RO), `/data/catalog`+`/data/local` ; fichiers `compose.yaml`/`compose.smoke.yaml`/`compose.hardening.yml`.
- **Crawler config-only** : aucune var d'env crawler ajoutée ; mode observer/full piloté par `local.yaml` monté + `--local` (Tasks 3-4). Le verifier lit bien `QUARANTINE_DIR`/`VERIFIER_HOST`/`VERIFIER_PORT` (cohérent avec `app.py`/`__main__.py`).
- **Smoke** : sans gluetun, amuled sur `ec`, configs `deploy/smoke/` montées ; full healthy + observer + fail-fast couverts ; `down -v` en finally ; marqueur déselectionné par défaut → 100 % branch préservé.
- **CI** : `smoke` gate `publish` ; multi-arch `amd64+arm64` ; triggers push main/tag/dispatch (pas PR) ; `ci.yml` intact.
- **Durcissement** : non-root 999, `cap_drop ALL`, `no-new-privileges`, `read_only` + `tmpfs /tmp`, `internal: true` sur verify-internal ; gVisor opt-in (`compose.hardening.yml`). Exceptions documentées (gluetun NET_ADMIN/tun ; amuled image tierce).
- **Inconnus empiriques** : incantation uv workspace, libs système crawler, user amuled — résolus au build et **inscrits dans le runbook** (Task 8).

- [ ] **Step 3 : Gate complet final (INCHANGÉ — les 6 checks) + smoke dédié**

```bash
uv run ruff check .
uv run ruff format --check .
uv run sqlfluff lint packages/crawler/src
uv run mypy
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
```
Expected : tout vert. Crawler : `… passed, N deselected` (N inclut `compose_integration`), **100.00 % branch**. Verifier : `… passed`, **100.00 %**. Smoke dédié (Docker requis — fait foi) :
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
```
Expected : `4 passed`. Et la config compose des 3 fichiers valide :
```bash
WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x docker compose -f compose.yaml --profile full config >/dev/null \
  && WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x docker compose -f compose.yaml -f compose.smoke.yaml --profile full config >/dev/null \
  && WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x docker compose -f compose.yaml -f compose.hardening.yml --profile full config >/dev/null \
  && echo "toutes configs compose OK"
```
Expected : `toutes configs compose OK`.

- [ ] **Step 4 : Mettre à jour `CLAUDE.md` (état courant)**

Mettre à jour le paragraphe « Current state » : le **packaging (Plan F)** est construit — 2 Dockerfiles multi-stage uv (`packages/{crawler,verifier}/Dockerfile`, build context racine, runtime slim non-root ; verifier + ffmpeg/ffprobe), `compose.yaml` (profils `observer`/`full`, réseaux `ec`/`verify-internal`(`internal: true`)/`egress`, volumes nommés, durcissement niveau conteneur), `compose.smoke.yaml` (sans VPN, configs `deploy/smoke/`), `compose.hardening.yml` (gVisor opt-in), smoke e2e `compose_integration` (Docker requis, désélectionné, `--no-cov`), workflow `images.yml` (smoke gate → publish GHCR multi-arch, dormant), runbook `docs/runbook-deployment.md`. Noter le marqueur `compose_integration` dans la liste des intégrations. Noter les follow-ups restants : **port-sync/High-ID** (remplace glueforward), **clamav**, **observabilité (Plan E)**. Mettre à jour les commandes d'intégration si nécessaire. **Préciser que Plan F n'a ajouté AUCUN code PROD** (gate 100 % branch inchangé).

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: CLAUDE.md — packaging (Plan F) construit (2 images, compose profils, smoke, CI GHCR)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5 : Écrire le handoff**

Créer `docs/handoffs/2026-06-14 - handoff - packaging.md` (format des handoffs précédents) :
- **TL;DR** : le projet est déployable. 2 images Docker (crawler + verifier, multi-stage uv, runtime slim non-root) + `docker compose` (profils `observer`/`full`, gluetun/amuled, verifier sur `internal: true`, durcissement conteneur) + smoke e2e `compose_integration` (sans VPN, asserte le câblage) + CI `images.yml` (smoke gate → publish GHCR multi-arch, dormant) + runbook. **AUCUN code PROD ajouté** — gate 100 % branch des 2 paquets intact. **Plan F COMPLET, taggé `v0.10.0-packaging`.**
- **État vérifiable** : gate 6-checks vert ; `compose_integration` vert (Docker) ; `docker compose config` valide pour les 3 fichiers ; tag posé (non poussé).
- **Inconnus empiriques RÉSOLUS au build** (à recopier du runbook) : incantation uv workspace exacte ; libs système crawler (re2/rapidfuzz) ; user/PUID amuled (durcissement relâché ou non pour amuled).
- **Follow-ups ouverts** : **port-sync/High-ID** (lire le port forwardé gluetun → EC `set_listen_port`, repli `amule.conf` ; inconnu : EC règle-t-il le port à chaud ?) — profil full en Low-ID d'ici là ; **clamav** (tension `freshclam` egress vs `internal: true`) ; **ring noyau bwrap par-enfant** (changement code D-analysis, hors Plan F) ; **sous-commandes CLI** ; **GHCR public vs login** (visibilité) ; **smoke CI lent/flaky** (option : réduire à `build` seul).
- **Pièges appris** (remplir au fil de l'exécution) : p. ex. format de `docker compose ps --format json` (lignes vs tableau) ; `--scale verifier=0` vs override `depends_on` ; un targets/matcher smoke INVALIDE casse le scénario observer (fail-fast au démarrage) ; le crawler full health-gate le verifier (ordre de démarrage / `restart`).
- **Prochaine étape** : **Plan E (observabilité — Prometheus/apprise)** ou le **follow-up port-sync** ; clamav après. Brainstormer d'abord.

```bash
git add "docs/handoffs/2026-06-14 - handoff - packaging.md"
git commit -m "$(cat <<'EOF'
docs: handoff — packaging (Plan F ; 2 images, compose, smoke, CI ; follow-ups port-sync/clamav)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6 : Poser le TAG annoté (clôt Plan F — NON poussé)**

```bash
git tag -a v0.10.0-packaging -m "$(cat <<'EOF'
v0.10.0-packaging : 2 images Docker + compose (observer/full) + smoke e2e + CI GHCR (Plan F)

Dockerfiles multi-stage uv (crawler + verifier non-root, verifier+ffprobe), compose.yaml
(profils, reseaux isoles, verifier internal:true, durcissement conteneur), compose.smoke.yaml
(sans VPN) + smoke compose_integration, compose.hardening.yml (gVisor opt-in), images.yml
(smoke gate -> publish GHCR multi-arch), runbook. Zero code PROD ajoute — gate 100% branch
intact. NON POUSSE.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
git tag --list | grep -E "0\.9|packaging"
```
Expected : `v0.10.0-packaging`. **NE PAS pousser** (les jalons restent locaux sur `main`).

---

## Self-Review : couverture de la spec (section → tâche)

| Spec packaging | Couvert par |
|---|---|
| §1 But : 2 images, compose observer/full, durcissement conteneur, smoke e2e, CI GHCR, runbook | Tasks 1-2 (images), 3-4 (compose), 5 (smoke), 6 (hardening), 7 (CI), 8 (runbook) |
| §2 F-D1 smoke `compose_integration` (sans gluetun, asserte câblage, full fail-fast/observer, pas de download réel) | Task 5 (4 tests : build, full healthy, observer, fail-fast) |
| §2 F-D2 durcissement conteneur bâti+testé ; ring noyau opt-in | Task 3 (durcissement dans compose.yaml), 5 (smoke le vérifie), 6 (compose.hardening.yml gVisor) |
| §2 F-D3 glueforward abandonné ; full Low-ID ; port-sync follow-up | Header HORS PÉRIMÈTRE + Task 8 (runbook : Low-ID) + Task 9 (handoff : follow-up port-sync) |
| §2 F-D4 sous-commandes CLI différées | Header HORS PÉRIMÈTRE ; crawler ENTRYPOINT = daemon seul (Task 2) |
| §2 F-D5 clamav après Plan F | Header HORS PÉRIMÈTRE + Task 9 (handoff follow-up) |
| §2 F-D6 `images.yml` (smoke gate publish, multi-arch GHCR, triggers push main/tag/dispatch, pas PR, ci.yml inchangé) | Task 7 ; grep ci.yml intact (Task 9 Step 1) |
| §3 Deux images (multi-stage uv, build context racine, --package par membre ; verifier+ffmpeg ; non-root ; ENTRYPOINT) | Tasks 1 (verifier), 2 (crawler) ; incantation uv validée empiriquement |
| §4 compose.yaml (gluetun NET_ADMIN/tun, amuled network_mode service:gluetun + staging, crawler multi-homed + config montée, verifier full internal:true RO + healthcheck, réseaux, volumes ; image:+build:) | Task 3 |
| §4 mode observer/full/EC-host par local.yaml monté (config files-only, aucun code PROD) | Task 4 (deploy/smoke/local.{observer,full}.yaml) + concrétisation contrôleur |
| §5 compose.smoke.yaml (sans VPN, amuled sur ec, config smoke) + test compose_integration (build, /health 200, crawler Up, full fail-fast, observer) | Tasks 4 (override+configs), 5 (test) |
| §6 Durcissement (cap_drop ALL, no-new-privileges, user non-root, read_only+tmpfs, pids/mem, verifier internal:true + tmpfs /tmp ; crawler read_only+volumes RW ; gluetun/amuled exceptions ; ring noyau opt-in) | Task 3 (compose.yaml), 6 (compose.hardening.yml) ; inconnu user amuled fléché (Task 3/8) |
| §7 CI (job smoke amd64 gate ; job publish needs:smoke buildx multi-arch login GHCR metadata-action build-push-action ; permissions packages:write ; dormant) | Task 7 |
| §8 Runbook (prérequis, .env/local.yaml, observer/full, override durcissement, validation homelab Low-ID, où vivent les données, GHCR visibilité) | Task 8 |
| §9 Aucun code PROD Python → gate 100 % branch inchangé ; nouveaux artefacts = Dockerfiles/compose/test/workflow ; smoke filet auto, homelab manuel | Tasks 1-8 (zéro src/ touché) ; grep Task 9 Step 1 ; marqueur déselectionné (Task 5) |
| §10 Hors périmètre (port-sync/High-ID, clamav, ring noyau obligatoire, sous-commandes CLI, e2e ed2k-local) | Header HORS PÉRIMÈTRE + Task 9 (handoff) |
| §11 Risques (uv workspace, read_only/écritures, amuled non-root, smoke CI, GHCR privé, config observer/full) | Inconnus fléchés (header) ; Tasks 1-2 (uv), 3/6 (read_only+tmpfs), 3/8 (amuled), 7 (smoke CI), 8 (GHCR), 4 (config montée) |

**Self-review — résultats :**

1. **Couverture spec §1–§11 + F-D1..F-D6** : chaque section/décision est mappée à au moins une tâche (table ci-dessus). Aucun manque : les éléments hors périmètre (port-sync/High-ID, clamav, ring noyau obligatoire, sous-commandes CLI, e2e ed2k-local) sont explicitement dans le header HORS PÉRIMÈTRE + renvoyés au handoff. Le tag jalon EST posé (Task 9 Step 6, `v0.10.0-packaging`).

2. **Placeholder scan** : AUCUN « TBD »/« similar to »/« … (à compléter) » dans les artefacts. Dockerfiles, compose (3 fichiers), workflow, test, `.env.example`, `.dockerignore` sont COMPLETS et copiables. Les SEULS renvois sont (a) des **consignes de validation empirique** explicitement requises par la spec (incantation uv workspace, libs système, user amuled — qui NE PEUVENT PAS être devinées sans build, comme la spec §11 et le brief l'imposent), avec un squelette de départ concret + l'instruction d'ajuster au build ; (b) des **consignes de lecture du fichier existant** (`config/matcher.yaml`/`config/targets.yaml` pour copier leur schéma exact dans les configs smoke — un schéma deviné casserait le fail-fast) ; (c) des consignes de RÉDACTION de docs (runbook Task 8, CLAUDE.md/handoff Task 9, contenu spécifié point par point). Ce ne sont PAS des blancs de code : ce sont les inconnus empiriques que le brief demande explicitement de flécher.

3. **Cohérence des noms (vérifiée transversalement)** : images `ghcr.io/geoffreycoulaud/emule-indexer-{crawler,verifier}` (compose) / `ghcr.io/${{ github.repository }}-{crawler,verifier}` (workflow, lowercase via metadata-action) ✔ ; services `gluetun/amuled/crawler/verifier` (compose.yaml ↔ compose.smoke.yaml ↔ test `_PROJECT-<service>-1`) ✔ ; réseaux `ec`/`verify-internal`(`internal: true`)/`egress` ✔ ; volumes `quarantine`/`catalog-db`/`local-db`/`amule-state` ✔ ; chemins `/app/config`(RO)/`/data/quarantine`(crawler+amuled)/`/quarantine`(verifier RO)/`/data/catalog`+`/data/local` cohérents entre compose, configs smoke (`catalog_db_path: /data/catalog/catalog.db`) et le bind-mount ✔ ; fichiers `compose.yaml`/`compose.smoke.yaml`/`compose.hardening.yml` identiques dans tasks/test/CI/runbook ✔ ; marqueur `compose_integration` (pyproject + test + CI + runbook) ✔.

4. **ZÉRO code PROD Python** : aucune tâche ne crée/modifie un fichier sous `packages/crawler/src/` ou `packages/verifier/src/`. Les seuls fichiers Python touchés sont (a) `packages/crawler/pyproject.toml` (ajout du marqueur `compose_integration` — config pytest, pas du code PROD) et (b) `packages/crawler/tests/integration/test_compose_smoke.py` (TEST, `--no-cov`, déselectionné). Le gate 6-checks et le 100 % branch restent strictement intacts (vérifié par grep Task 9 Step 1 + le run par défaut « … deselected » Task 5 Step 3).

5. **Idiomes externes figés** : uv-in-Docker multi-stage (cache mount, `UV_*` env, `--no-install-workspace` couche deps puis `--no-editable --package` couche projet) confirmé via context7 (`/astral-sh/uv` — « Intermediate layers in workspaces » + « Non-editable installs ») — l'incantation FINALE reste à valider au build (membre installé). docker compose v2 (profils, `internal: true`, healthcheck `python -c urllib`, `network_mode: service:gluetun`, `read_only`/`tmpfs`/`cap_drop`/`security_opt`/`user`, `image:`+`build:`) ✔. GitHub Actions docker/* épinglés (`@v4/@v3/@v5/@v6`, `metadata-action` lowercase, multi-arch via qemu+buildx) ✔.

**Nombre de tâches : 9** (1 Dockerfile verifier + .dockerignore + valid. uv ; 2 Dockerfile crawler ; 3 compose.yaml ; 4 compose.smoke.yaml + configs smoke ; 5 test compose_integration + marqueur ; 6 compose.hardening.yml ; 7 images.yml CI ; 8 runbook + .env.example + .gitignore + README ; 9 revue holistique + handoff + CLAUDE.md + **TAG**).
