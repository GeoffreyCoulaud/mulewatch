# Handoff — emule-indexer (Plan F : packaging — 2 images Docker, compose observer/full, smoke e2e, CI GHCR)

> Continuation guide. Le plus récent des handoffs = point d'entrée. Lis aussi le précédent
> (`2026-06-14 - handoff - analysis (real verifier).md`) pour le contexte D-analysis (le VRAI
> verifier que les images empaquettent), la spec de packaging
> (`docs/superpowers/specs/...packaging...`) et le **runbook opérationnel détaillé**
> `docs/runbook-deployment.md` (toutes les incantations, pièges volumes, GHCR, gVisor).

## 1. TL;DR

Le projet est **désormais déployable**. Plan F ajoute **ZÉRO Python de prod** — le gate **100 %
branch des deux paquets est intact** (rien sous `src/` n'a bougé). Ce qui a été construit :

- **2 images Docker** multi-stage uv (builder `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` →
  runtime `python:3.12-slim-bookworm`, non-root **uid/gid 999**) : `packages/verifier/Dockerfile`
  (+ `ffmpeg`/ffprobe pour D-analysis) et `packages/crawler/Dockerfile` (zéro lib apt — re2/rapidfuzz
  s'importent sur slim).
- **`compose.yaml`** : profils **observer** (gluetun+amuled+crawler) / **full** (+verifier) ; réseaux
  isolés `ec` / `verify-internal` (**`internal: true`** — le verifier n'a **pas d'egress**, lit la
  quarantaine en RO) / `egress` ; volumes nommés `quarantine`/`catalog-db`/`local-db`/`amule-state` ;
  durcissement conteneur (non-root 999, `cap_drop: ALL`, `no-new-privileges`, `read_only` +
  `tmpfs /tmp`, limites pids/mem) ; amuled en `network_mode: service:gluetun` (hôte EC prod =
  `gluetun`).
- **`compose.smoke.yaml`** + `deploy/smoke/*.yaml` : la stack **sans VPN** (amuled directement sur
  `ec`, hôte EC = `amuled`), via les tags de fusion Compose `!reset`/`!override`, **vrais volumes
  nommés**, configs smoke validées contre les **vrais parsers**.
- **`compose.hardening.yml`** : durcissement **gVisor** opt-in (`runtime: runsc`) sur crawler+verifier
  (ring noyau reporté de D-analysis ; jamais requis pour démarrer).
- **smoke e2e** `packages/crawler/tests/integration/test_compose_smoke.py` + marqueur
  **`compose_integration`** (Docker requis, désélectionné par défaut, **n'importe aucun module
  `emule_indexer`** → 100 % branch préservé).
- **CI `.github/workflows/images.yml`** : job `smoke` (build amd64 + `compose_integration`) qui
  **GATE** le job `publish` (buildx multi-arch amd64+arm64 → **GHCR**) ; déclencheurs push `main` /
  tags `v*` / `workflow_dispatch` (PAS sur PR) ; `ci.yml` (le gate) **INCHANGÉ** ; **dormant tant
  que non poussé**.
- **runbook** `docs/runbook-deployment.md` (+ `.env.example`, `.gitignore` couvre `.env`, pointeur
  README, fix `config/local.example.yaml` réconcilié avec les montages de volumes).

Verdict de revue holistique : **READY TO TAG**. Jalon **`v0.10.0-packaging`** (annoté, **non
poussé**) sur `main`. **Plan F COMPLET.**

## 2. État vérifiable

Gate **PAR PAQUET** (le `pytest` nu depuis la racine est neutralisé par un `conftest.py` racine) —
les six checks verts :

```bash
( cd packages/crawler  && uv run pytest -q )   # 100.00% branch — INCHANGÉ (zéro Python de prod ajouté)
( cd packages/verifier && uv run pytest -q )   # 100.00% branch — INCHANGÉ
uv run ruff check . && uv run ruff format --check . && uv run mypy   # racine, span les 2 paquets
uv run sqlfluff lint packages/crawler/src
```

Smoke compose (Docker requis, désélectionné du run par défaut, hors coverage) :

```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )   # 4 passed
```

Les 3 fichiers compose valident :

```bash
docker compose config >/dev/null                                   # base (compose.yaml)
docker compose -f compose.yaml -f compose.smoke.yaml config >/dev/null
docker compose -f compose.yaml -f compose.hardening.yml config >/dev/null
```

Tag : `git tag --list | grep packaging` → `v0.10.0-packaging` (**posé par le contrôleur après revue,
NON poussé**).

## 3. Inconnus empiriques RÉSOLUS au build (recap du runbook)

- **Incantation uv workspace** (validée **verbatim**, deux couches) : couche dépendances avant le
  `COPY` (`uv sync --locked --no-install-workspace --package <dist>` après bind-mount de `uv.lock`,
  du `pyproject.toml` racine **et des DEUX** membres), puis couche projet
  (`uv sync --locked --no-editable --package <dist>`). `--locked` (PAS `--frozen`) ; `--package`
  marche **sans** `--all-packages`. Aucune adaptation depuis le squelette du workspace.
- **Libs système** : **verifier** = `ffmpeg` **uniquement** (fournit `ffprobe`) ; **crawler** =
  **zéro** lib apt (les wheels manylinux de `google-re2`/`rapidfuzz` embarquent le natif, `libstdc++6`
  est déjà dans slim).
- **Propriété des volumes `/data`** (le défaut réel, voir §4b) : l'image pré-crée
  `/data/{catalog,local,quarantine}` en `nonroot` → un volume nommé **vide** hérite de 999 au premier
  montage.
- **amuled est une image tierce** (`ngosang/amule`) lancée **telle quelle** : on ne durcit pas ce
  qu'on ne construit pas (aucune relaxation nécessaire pour le smoke). **À surveiller en prod** : le
  volume `quarantine` est écrit par amuled (fichiers complétés) ET le crawler (`os.replace`) — le
  premier conteneur qui monte le volume vide fixe sa propriété, accroc cross-uid possible (non exercé,
  pas de vrai download dans le smoke).
- **Entrypoint exec-form** `["python","-m","<pkg>"]` : un `docker run IMAGE python -c ...` **n'override
  pas** l'entrypoint, il **ajoute** ses args → utiliser `--entrypoint python` pour une commande
  ponctuelle. (`compose exec` et le `CMD` du healthcheck ne traversent PAS l'entrypoint.)

## 4. Pièges appris pendant l'exécution (RÉELS — à garder)

**(a) Le `compose.smoke.yaml` littéral du plan était cassé — seul `docker compose config` le révèle.**
La fusion Compose n'est PAS une simple substitution :
- `network_mode: null` **ne désactive PAS** le champ → il faut **`network_mode: !reset null`**.
- `profiles: [disabled]` **fusionne par APPEND** (n'écrase pas) → **`profiles: !override [disabled]`**
  pour neutraliser un service (ici gluetun) hors profils.
- `depends_on: verifier (service_healthy)` **casse le profil observer** (le verifier est full-only) —
  à retirer/scoper.
- `amuled.depends_on: !reset []` est nécessaire car, gluetun neutralisé, son dépendant devient
  indéfini.
**Leçon : pour toute surcharge compose, `docker compose config` est l'oracle — la fusion a des règles
non intuitives (reset vs override vs append).**

**(b) Défaut réel : bases SQLite sous non-root + `read_only`.** Le crawler tourne `user: 999` +
`read_only: true` ; Docker crée les volumes nommés vides en **propriété root** → `unable to open
database file`. **Fix dans `packages/crawler/Dockerfile`** : `mkdir -p /data/{catalog,local,quarantine}`
+ `chown` nonroot → un volume nommé **vide** hérite de la propriété 999 **au premier montage** (ne
joue qu'au premier montage d'un volume vide ; un volume déjà peuplé reste root-owned, voir le runbook
pour le `chown` manuel). Le smoke utilise de **vrais volumes nommés** → une régression serait
attrapée.

**(c) `docker compose down` est scopé par profil (compose v5).** Un teardown doit utiliser un
**superset de profils** sinon le verifier (full-only) **persiste**. Le smoke démonte avec un profil
englobant ; à savoir aussi en exploitation.

**(d) Course au démarrage du mode full.** `compose.yaml` ne pose **pas** de `depends_on: verifier`
(le verifier est full-only) → si le crawler démarre avant que le verifier soit sain, il **fail-fast**
(health-gate du verifier au boot) puis **converge par restart** (`restart: unless-stopped`,
acceptable en long-running). Pour la **déterminisme du smoke** : le test full ajoute une surcharge
**par scénario** `depends_on: verifier (service_healthy)` ; le test fail-fast utilise
`restart: "no"`. (En exploitation : démarrer `verifier` d'abord pour éviter les redémarrages — voir
runbook.)

**(e) Append de l'entrypoint exec-form** (cf. §3) : la même mécanique qui fait qu'un `docker run`
ponctuel a besoin de `--entrypoint`.

## 5. Follow-ups ouverts

- **Synchronisation de port / High-ID** : lire le port forwardé par gluetun → le pousser en EC, repli
  `amule.conf`. **Remplace glueforward (abandonné).** Tant qu'il n'est pas là, le **mode full tourne
  en Low-ID** (connectivité OK mais sous-optimale).
- **clamav** : seconde source `malicious` (signatures) — **après Plan F** ; `freshclam` exige un
  egress, en **tension avec le `internal: true`** du verifier (un slot de registre est réservé dans
  `pipeline.run`, non implémenté).
- **Ring noyau bwrap par-enfant** : namespace `net=none` / seccomp / RO mounts / tmpfs réel par enfant
  d'analyse — **changement de code (D-analysis), hors Plan F**. (Le **ring conteneur** est livré :
  non-root + `cap_drop`/`no-new-privileges`/`read_only` toujours ; **gVisor** déjà dispo en opt-in via
  `compose.hardening.yml`.)
- **Sous-commandes CLI** : ergonomie d'exploitation.
- **Visibilité GHCR** : packages **privés par défaut** → soit les rendre publics, soit `docker login
  ghcr.io` (PAT `read:packages`) avant le pull. Références (lowercase) :
  `ghcr.io/geoffreycoulaud/emule-indexer-crawler` / `-verifier`.
- **(mineur)** Le pre-build du smoke en CI a un **nom de projet Compose différent** de celui du smoke
  pytest → la stack est build deux fois (cosmétique, perte de temps de cache, pas de bug).
- **(mineur)** `mem_limit` legacy (v2) vs `deploy.resources.limits` (v3+) — uniformiser un jour.

## 6. Prochaine étape recommandée

**Plan E (observabilité — Prometheus/apprise)** OU le follow-up **port-sync** (débloque le High-ID en
full). **clamav après** (dépend du Plan F : egress freshclam). Comme toujours : **brainstormer
d'abord** (HARD GATE design — spec → plan → exécution subagent-driven — **avant toute implémentation**).
