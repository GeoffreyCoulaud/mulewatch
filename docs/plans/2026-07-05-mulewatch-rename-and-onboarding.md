# Mulewatch rename and onboarding docs: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project to `mulewatch` at full depth, restructure `deploy/` around a
no-decision royal road, then rewrite README + deployment runbook for a low-technical user.

**Architecture:** Three integration units. PR 1 is the mechanical rename (package, dist,
images, CI, living docs). PR 2 is the deploy restructure (`compose.yaml` default stack,
webui + monitoring always on). A final docs-only branch rewrites README, deployment.md and
the beginner troubleshooting entries, validated by a blind walkthrough.

**Tech Stack:** uv workspace, poethepoet gate, docker compose v2, GitHub Actions, ghcr.

**Spec:** `docs/specs/2026-07-05-mulewatch-rename-and-onboarding-docs.md` (decisions D1-D11).

## Global constraints

- Gate: `uv run poe check` green before every PR (mypy strict, per-package 100 % branch
  coverage, ruff, sqlfluff, template-check). The rename is a behavior-preserving refactor:
  the existing suites ARE its spec; no test may be weakened.
- Dated historical docs (`docs/handoffs/`, `docs/specs/`, `docs/plans/`, `docs/reference/`)
  keep the old name. Only living docs are renamed.
- Prometheus metric names (`emule_*`, e.g. `emule_crawler_up`) are DOMAIN names, not project
  names: they do not change. Grafana dashboard expressions therefore do not change either;
  only the dashboard uid/title/filename do.
- All rewritten user-facing French text: no em-dashes or en-dashes (D11), separators are
  `:` / `.` / `·`.
- New docs prose (this plan, handoff) in English; user-facing docs in French (D3).
- Conventional commits, English.

## Facts inventory (verified 2026-07-05, drives the mechanical pass)

- Python package: only `packages/crawler/src/emule_indexer/` carries the name; ~140 crawler
  files reference `emule_indexer` (imports, docstrings).
- Dist name `emule-indexer`: root `pyproject.toml` (lines 5, 12), `packages/crawler/pyproject.toml`
  (`name`, `packages`, `--cov=`, `[tool.coverage.run] source`), `packages/crawler/Dockerfile`
  (comment, 2 × `--package`, `ENTRYPOINT python -m emule_indexer`), `uv.lock`.
- Cross-package prose (comments/docstrings only, no imports): `packages/matching/src/catalog_matching/ed2k_link.py:13`,
  `packages/webui/src/catalog_webui/adapters/matching_read.py:8-9`, `packages/webui/tests/conftest.py:1`,
  `packages/verifier/src/download_verifier/obs_config.py:1`.
- Webui UI copy: `{% block title %}` in all 6 templates under
  `packages/webui/src/catalog_webui/adapters/templates/` says `emule-indexer`.
- Images: `ghcr.io/geoffreycoulaud/emule-indexer-{crawler,verifier,webui}` in
  `deploy/base.compose.yml` (lines 6, 53, 151) and `tests/smoke/compose.yaml` (lines 20, 55);
  `IMAGE_PREFIX` in `.github/workflows/release.yml:12` and `validate.yml:15`; example string in
  `.github/actions/docker-image/action.yml:16`.
- Grafana: `deploy/config/grafana/dashboards/emule-indexer.json` (uid + title line 2-3),
  `deploy/config/grafana/provisioning/dashboards/provider.yaml:3` (provider name).
- Living docs with old name: `README.md`, `docs/runbooks/{deployment,administration,troubleshooting}.md`,
  `docs/README.md`, `docs/testing-guide.md`, `docs/legal-and-privacy.md`, `docs/architecture.md`,
  `CLAUDE.md`. (`docs/architecture.md` was missing from the spec's list; it is living, include it.)
- `release.yml` triggers on push to `main` + tags `v*`: new-name images publish automatically
  once PR 1 merges. ghcr does NOT redirect old image names.
- Profiles today: `download` (verifier, freshclam, gluetun socket-proxy), `monitoring`
  (prometheus line 120, grafana line 134), `webui` (line 155), all in `deploy/base.compose.yml`.
  The smoke stack's `observer`/`download` profiles are its own and do not change.
- `deploy/direct.compose.yml` and `deploy/gluetun.compose.yml` both `include: base.compose.yml`
  and carry launch-command header comments mentioning the old profiles.
- `deploy/.env.example` has no `WEBUI_PORT` although `base.compose.yml` uses
  `${WEBUI_PORT:-8080}`.

---

## Task 0: land the spec, open the rename branch

**Files:** none created (branch bookkeeping).

- [ ] **Step 0.1**: On `docs/spec-mulewatch-rename-and-onboarding`, commit this plan file, then
  local-merge the branch into `main` (docs-only exception: spec + plan) and delete it:

```bash
git switch main && git merge --ff-only docs/spec-mulewatch-rename-and-onboarding \
  && git branch -d docs/spec-mulewatch-rename-and-onboarding
```

- [ ] **Step 0.2**: `git switch -c chore/rename-to-mulewatch`

## Task 1: rename the crawler package (PR 1, commit 1)

**Files:**
- Move: `packages/crawler/src/emule_indexer/` → `packages/crawler/src/mulewatch/`
- Modify: every crawler `src` + `tests` file referencing `emule_indexer`; `packages/crawler/pyproject.toml`; `packages/crawler/Dockerfile`; root `pyproject.toml`; `uv.lock` (regenerated)

**Interfaces:** Produces the importable package `mulewatch`, dist `mulewatch`, entrypoints
`python -m mulewatch{,.merge,.compact}`. Every later task assumes these names.

- [ ] **Step 1.1**: Move the package directory:

```bash
git mv packages/crawler/src/emule_indexer packages/crawler/src/mulewatch
```

- [ ] **Step 1.2**: Mechanical replace, module form then dist form:

```bash
grep -rl 'emule_indexer' packages/crawler --exclude-dir=__pycache__ \
  | xargs sed -i 's/emule_indexer/mulewatch/g'
sed -i 's/emule-indexer/mulewatch/g' packages/crawler/pyproject.toml \
  packages/crawler/Dockerfile pyproject.toml
```

- [ ] **Step 1.3**: Regenerate the lock and env: `uv sync --dev`. Expected: `uv.lock` diff swaps
  the workspace member name, nothing else.
- [ ] **Step 1.4**: Verify zero leftovers:
  `grep -rn 'emule_indexer\|emule-indexer' packages/crawler pyproject.toml uv.lock` → no output.
- [ ] **Step 1.5**: Run the crawler suite in isolation:
  `( cd packages/crawler && uv run pytest -q )`. Expected: PASS, coverage 100 %.
- [ ] **Step 1.6**: Commit: `chore(crawler): rename package emule_indexer to mulewatch`.

## Task 2: cross-package prose and webui titles (PR 1, commit 2)

**Files:**
- Modify: `packages/matching/src/catalog_matching/ed2k_link.py`,
  `packages/webui/src/catalog_webui/adapters/matching_read.py`,
  `packages/webui/tests/conftest.py`, `packages/verifier/src/download_verifier/obs_config.py`
  (docstrings referencing the now-renamed module), and the 6 templates under
  `packages/webui/src/catalog_webui/adapters/templates/` (`{% block title %}` UI copy).

- [ ] **Step 2.1**: Update the four docstring cross-references (module form):

```bash
sed -i 's/emule_indexer/mulewatch/g' \
  packages/matching/src/catalog_matching/ed2k_link.py \
  packages/webui/src/catalog_webui/adapters/matching_read.py \
  packages/webui/tests/conftest.py \
  packages/verifier/src/download_verifier/obs_config.py
```

- [ ] **Step 2.2**: Update the UI titles:

```bash
sed -i 's/emule-indexer/mulewatch/g' \
  packages/webui/src/catalog_webui/adapters/templates/*.html
```

- [ ] **Step 2.3**: Run the three package suites (matching, verifier, webui), each
  `( cd packages/<pkg> && uv run pytest -q )`. Expected: PASS.
- [ ] **Step 2.4**: Commit: `chore: rename cross-package references and webui titles to mulewatch`.

## Task 3: CI, images, compose, grafana (PR 1, commit 3)

**Files:**
- Modify: `.github/workflows/release.yml`, `.github/workflows/validate.yml`,
  `.github/actions/docker-image/action.yml`, `deploy/base.compose.yml`, `tests/smoke/compose.yaml`,
  `deploy/config/grafana/provisioning/dashboards/provider.yaml`
- Move: `deploy/config/grafana/dashboards/emule-indexer.json` → `mulewatch.json`

- [ ] **Step 3.1**:

```bash
git mv deploy/config/grafana/dashboards/emule-indexer.json \
       deploy/config/grafana/dashboards/mulewatch.json
sed -i 's/emule-indexer/mulewatch/g' \
  .github/workflows/release.yml .github/workflows/validate.yml \
  .github/actions/docker-image/action.yml deploy/base.compose.yml \
  tests/smoke/compose.yaml \
  deploy/config/grafana/provisioning/dashboards/provider.yaml \
  deploy/config/grafana/dashboards/mulewatch.json
```

- [ ] **Step 3.2**: Verify the dashboard expressions still read `emule_crawler_up` etc.
  (metric names unchanged): `grep '"expr"' deploy/config/grafana/dashboards/mulewatch.json`.
- [ ] **Step 3.3**: Syntax-check both stacks (needs the docker CLI; if unavailable in the
  sandbox, the operator runs it via `!`):

```bash
docker compose -f deploy/direct.compose.yml config -q
docker compose -f deploy/gluetun.compose.yml config -q
```

- [ ] **Step 3.4**: Commit: `chore(deploy,ci): rename images and grafana artifacts to mulewatch`.

## Task 4: living docs mechanical pass (PR 1, commit 4)

**Files:**
- Modify: `README.md`, `docs/runbooks/deployment.md`, `docs/runbooks/administration.md`,
  `docs/runbooks/troubleshooting.md`, `docs/README.md`, `docs/testing-guide.md`,
  `docs/legal-and-privacy.md`, `docs/architecture.md`, `CLAUDE.md`

- [ ] **Step 4.1**: Both name forms, living docs only:

```bash
sed -i 's/emule_indexer/mulewatch/g; s/emule-indexer/mulewatch/g' \
  README.md docs/README.md docs/testing-guide.md docs/legal-and-privacy.md \
  docs/architecture.md CLAUDE.md docs/runbooks/*.md
```

- [ ] **Step 4.2**: Review the diff by eye: entrypoint commands must read
  `python -m mulewatch.merge` / `.compact`; the CLAUDE.md package table must state package
  `mulewatch`, dist `mulewatch`; no dated doc touched (`git status` shows none under
  `docs/handoffs|specs|plans|reference`).
- [ ] **Step 4.3**: Commit: `docs: rename living docs to mulewatch`.

## Task 5: gate, PR 1, repo rename, image republication

- [ ] **Step 5.1**: `uv run poe check`. Expected: green.
- [ ] **Step 5.2**: Holistic review of the whole branch diff (fresh-eyes subagent): looks for
  missed occurrences (`grep -rn -i 'emule.indexer' . --exclude-dir=.git --exclude-dir=.venv --exclude=uv.lock`
  must only hit dated docs), broken doc links, seds that mangled prose.
- [ ] **Step 5.3**: Ask the operator to rename the GitHub repo BEFORE merging (his account):
  `gh repo rename mulewatch` (run from the repo; updates the local remote URL automatically).
- [ ] **Step 5.4**: Push, open PR 1 (`chore: rename project to mulewatch`), wait for
  `validate / gate` green, squash-merge.
- [ ] **Step 5.5**: After merge, `release.yml` runs on `main`: confirm
  `ghcr.io/geoffreycoulaud/mulewatch-{crawler,verifier,webui}:latest` exist
  (`gh api /user/packages?package_type=container` or the Packages page). Old `emule-indexer-*`
  packages remain; deleting them is the operator's later choice.

## Task 6: royal-road compose restructure (PR 2, commit 1)

- [ ] **Step 6.0**: `git switch main && git pull && git switch -c feat/royal-road-deploy`

**Files:**
- Move: `deploy/direct.compose.yml` → `deploy/compose.yaml`
- Modify: `deploy/base.compose.yml` (drop 2 profile lines), header comments of
  `deploy/compose.yaml` and `deploy/gluetun.compose.yml`, interim references in
  `docs/runbooks/{deployment,administration,troubleshooting}.md`

**Interfaces:** Produces the invariant later docs rely on: from `deploy/`,
`docker compose up -d` boots crawler + amuled + webui + prometheus + grafana;
`--profile download` is the only remaining profile in both stacks.

- [ ] **Step 6.1**: `git mv deploy/direct.compose.yml deploy/compose.yaml`
- [ ] **Step 6.2**: In `deploy/base.compose.yml`, delete line 120 `profiles: [monitoring]`
  (prometheus), line 134 `profiles: [monitoring]` (grafana), line 155 `profiles: [webui]`
  (webui service). Keep every `profiles: [download]`.
- [ ] **Step 6.3**: Rewrite the header comments of both stack files: royal road is
  `cd deploy && docker compose up -d`; gluetun stack is
  `docker compose -f gluetun.compose.yml up -d`; only `--profile download` remains.
- [ ] **Step 6.4**: Interim textual pass on the three runbooks so no command references
  `direct.compose.yml` or `--profile webui|monitoring` anymore (deployment.md gets fully
  rewritten in Task 9-10; here only correctness): default stack commands drop `-f`, webui and
  grafana described as always-on.
- [ ] **Step 6.5**: Syntax-check: `docker compose -f deploy/compose.yaml config -q` and
  `( cd deploy && docker compose config -q )` (proves the default-name resolution) and
  `docker compose -f deploy/gluetun.compose.yml config -q`.
- [ ] **Step 6.6**: Commit: `feat(deploy): default stack compose.yaml with webui+monitoring always on`.

## Task 7: .env.example royal-road reorder (PR 2, commit 2)

**Files:** Rewrite: `deploy/.env.example`

- [ ] **Step 7.1**: Replace the file content with (French kept, comments reordered for the
  royal road; adds the missing `WEBUI_PORT`):

```bash
# Secrets et variables du déploiement. Copier en `.env` (gitignoré) puis renseigner.
# Les flags applicatifs (download.enabled, port_sync.enabled) vivent dans
# config/crawler/crawler.yml, PAS ici.

# --- Requis (voie royale : observer, webui + monitoring) ---
# Deux mots de passe que VOUS choisissez. Aucun `change-me` ne doit rester.
AMULE_EC_PASSWORD=change-me            # crawler <-> amuled, >= 12 caracteres
GRAFANA_PWD=change-me                  # compte `admin` de Grafana

# --- Optionnel (voie royale) ---
IMAGE_TAG=latest                       # tag des images GHCR
WEBUI_PORT=8080                        # catalogue : http://localhost:8080
GRAFANA_PORT=3000                      # Grafana   : http://localhost:3000

# --- Annexe A : stack VPN (gluetun.compose.yml) uniquement ---
WIREGUARD_PRIVATE_KEY=change-me        # espace client du fournisseur VPN
VPN_SERVICE_PROVIDER=protonvpn
SERVER_COUNTRIES=                      # ex: Switzerland,France (noms anglais)
VPN_PORT_FORWARDING=off                # on => High-ID (+ port_sync.enabled: true)

# --- Annexe C : High-ID sans VPN ---
LISTEN_PORT=4662                       # port redirige sur la box (TCP+UDP)
```

- [ ] **Step 7.2**: Check every variable consumed by the compose files still exists:
  `grep -oh '\${[A-Z_]*' deploy/*.yml deploy/*.yaml | sort -u` and compare with the file.
- [ ] **Step 7.3**: Commit: `feat(deploy): reorder .env.example around the royal road`.

## Task 8: gate + PR 2

- [ ] **Step 8.1**: `uv run poe check` green; holistic review of the branch diff.
- [ ] **Step 8.2**: Operator smoke: `( cd deploy && docker compose up -d )` on his machine via
  `!` (sandbox has no docker network), checks webui on :8080 and Grafana on :3000, then
  `docker compose down`.
- [ ] **Step 8.3**: Push, open PR 2 (`feat(deploy): royal-road default stack`), CI green,
  squash-merge.

## Task 9: README rewrite (docs branch, commit 1)

- [ ] **Step 9.0**: `git switch main && git pull && git switch -c docs/onboarding-rewrite`

**Files:** Rewrite: `README.md`

- [ ] **Step 9.1**: Rewrite in French per spec phase 3, exact section order:
  1. `# mulewatch` + pitch: continuous eMule watching; first mission: the lost French dub of
     Keroro mission Titar (Teletoon 2008); manual searches miss intermittent reappearances,
     permanent distributed watching does not.
  2. Ethics callout (blockquote): the catalogue's subject is the file, never the person.
  3. `## Monter un nœud`: link to `docs/runbooks/deployment.md`, promise "~15 minutes;
     installer Docker est l'étape la plus difficile"; one sentence: observer mode downloads
     and shares nothing.
  4. `## Comment ça marche`: the 5-step loop in plain words (search continuously, score
     every sighting against the target list, catalogue, alert, optionally download in
     isolation; catalogues from several searchers merge without conflict).
  5. `## Statut fonctionnel` : current table, refreshed (observer stable; download built but
     not battle-tested; High-ID routes A/B; merge tool; hub non-goal), date bumped to
     juillet 2026.
  6. `## Pour les développeurs` : stack line, `uv run poe check`, links to
     `docs/specs/2026-06-10-crawler-mvp-design.md`, `docs/testing-guide.md`, `docs/plans/`.
  7. Closing line: mulewatch est l'outil ; Keroro mission Titar VF est sa première mission.
  Constraints: no em/en-dashes; all commands single-line short; no `--profile` on the royal
  road; link check at the end (`grep -o '\](\S*)' README.md` targets exist).
- [ ] **Step 9.2**: Commit: `docs: rewrite README as recruitment landing page`.

## Task 10: deployment.md rewrite (docs branch, commit 2)

**Files:** Rewrite: `docs/runbooks/deployment.md`

- [ ] **Step 10.1**: Rewrite in French. Royal road: numbered steps, each ending with
  `**Point de contrôle**` naming exactly what must be seen and the troubleshooting entry to
  open otherwise. Content per step (facts fixed here, wording free):
  1. *Ce qu'il vous faut* : une machine qui reste allumée (un vieux PC ou un mini-PC
     suffisent), Internet permanent, ~2 Go de RAM libres et ~5 Go de disque.
  2. *Installer Docker* : links only to official docs (Docker Desktop for Windows/macOS:
     `https://docs.docker.com/get-started/get-docker/`; Docker Engine for Linux servers:
     `https://docs.docker.com/engine/install/`). Checkpoint: `docker compose version` prints
     `v2.x`; otherwise entry "Docker introuvable ou compose v1".
  3. *Obtenir mulewatch* : default path WITHOUT git: GitHub page → green `Code` button →
     `Download ZIP` → unzip → open a terminal in the unzipped folder (note: it is named
     `mulewatch-main`). Alternative for git users: `git clone`. Checkpoint: `ls deploy`
     lists `compose.yaml`.
  4. *Choisir ses deux mots de passe* : `cp deploy/.env.example deploy/.env`, edit with any
     text editor, set `AMULE_EC_PASSWORD` (>= 12 chars) and `GRAFANA_PWD`. Checkpoint:
     `grep change-me deploy/.env` prints nothing; otherwise entry "J'ai laissé change-me".
  5. *Lancer* : `cd deploy` then `docker compose up -d`. Checkpoint: `docker compose ps`
     shows every service `Up` (list them: crawler, amuled, webui, prometheus, grafana);
     otherwise entry "Un conteneur redémarre en boucle".
  6. *Vérifier que le nœud vit* : `docker compose logs crawler` shows search cycles within
     1-3 min (amuled fetches server lists automatically on first boot); "Low-ID" in the logs
     is NOT a failure. Checkpoint wording included.
  7. *Voir le catalogue* : `http://localhost:8080` (webui; empty then filling over the first
     hours) and `http://localhost:3000` (Grafana, login `admin` + `GRAFANA_PWD`).
  8. *Vivre avec le nœud* : update = `docker compose pull` then `docker compose up -d`
     (manual, images do not self-update); stop = `docker compose down` (named volumes, so
     catalogue and state persist); deeper lifecycle → administration runbook link.
  Then annexes, each a delta from the royal road:
  - *Annexe A. Passer derrière un VPN* : stack `gluetun.compose.yml`, the 3 extra `.env`
    variables, provider must support WireGuard; long commands wrapped multi-line with `\`
    plus the note "sous Windows/PowerShell, tout sur une seule ligne". Launch:
    `docker compose -f gluetun.compose.yml up -d` (from `deploy/`).
  - *Annexe B. Activer le mode download* : `download.enabled: true` in
    `deploy/config/crawler/crawler.yml` + `--profile download`; keep the existing pointer to
    the 4 deployment constraints of
    `docs/reference/2026-06-17-amuled-completion-behavior.md`; transient behaviors kept
    (crawler restart loop until verifier healthy; `suspicious` verdicts while clamav
    downloads its DB 5-20 min).
  - *Annexe C. High-ID* : current two-route table (direct + box port forward on
    `LISTEN_PORT` 4662 TCP+UDP; gluetun + `VPN_PORT_FORWARDING=on` +
    `port_sync.enabled: true`), link to administration for trade-offs.
  - *Annexe D. Régler le monitoring* : `GRAFANA_PORT`/`WEBUI_PORT`, opting out
    (`docker compose stop grafana prometheus`), administration link.
  - Glossary kept at the end (eD2k/Kad, Low-ID/High-ID, EC, quarantine).
  Constraints: no em/en-dashes; every command copy-pastable as shown; French.
- [ ] **Step 10.2**: Cross-check every fact against the merged state: service names from
  `deploy/base.compose.yml`, ports from `.env.example`, log wordings from the crawler
  (grep the actual startup log lines in `packages/crawler/src/mulewatch/composition/`).
- [ ] **Step 10.3**: Commit: `docs: rewrite deployment runbook as royal-road guide`.

## Task 11: troubleshooting beginner pass (docs branch, commit 3)

**Files:** Modify: `docs/runbooks/troubleshooting.md`

- [ ] **Step 11.1**: Add or rewrite symptom-first entries matching EXACTLY the checkpoint
  pointers written in Task 10 (titles must match what the guide references):
  - "Docker introuvable ou compose v1" (command not found / v1 output → official install
    links, distro packages often ship v1).
  - "J'ai laissé change-me dans .env" (silent auth failure symptom: crawler logs show EC
    auth errors, catalogue stays empty → fix `.env`, `docker compose up -d` again).
  - "Un conteneur redémarre en boucle" (`docker compose ps` shows Restarting → `docker
    compose logs <service>`; the download-mode crawler/verifier case cross-referenced).
  - "Le port est déjà pris" (`bind: address already in use` on 8080/3000/4662 → change
    `WEBUI_PORT`/`GRAFANA_PORT`/`LISTEN_PORT` in `.env`).
  - "amuled ne se connecte à rien" (keep the existing entry, retitle symptom-first).
  - "La webui reste vide" (first hours are normal; check crawler logs for search cycles).
  Existing deeper entries keep their level; only titles/ordering may change so beginner
  entries come first.
- [ ] **Step 11.2**: Verify guide → troubleshooting anchors resolve (manual link check).
- [ ] **Step 11.3**: Commit: `docs: beginner-first troubleshooting entries for first deployment`.

## Task 12: blind walkthrough validation

- [ ] **Step 12.1**: Dispatch a fresh-context subagent that reads ONLY the new README +
  deployment.md and simulates a first deployment step by step, flagging every assumption,
  ambiguity, or missing prerequisite. Fix findings.
- [ ] **Step 12.2**: Real run by the operator (sandbox cannot run compose networking): from a
  clean directory, follow the guide literally (ZIP download included), checking each
  checkpoint text against reality via `!`-commands. Fix any checkpoint that does not match
  observed output, commit fixes.
- [ ] **Step 12.3**: Commit: `docs: walkthrough fixes` (if any).

## Task 13: wrap

- [ ] **Step 13.1**: Full gate one last time (`uv run poe check`), then local-merge
  `docs/onboarding-rewrite` into `main` (docs-only diff) and delete the branch.
- [ ] **Step 13.2**: Annotated tag on `main`: next minor bump with suffix `-onboarding`
  (check `git tag | sort -V | tail -1` for the current version), not pushed.
- [ ] **Step 13.3**: Handoff `docs/handoffs/2026-07-05 - handoff - mulewatch rename + onboarding docs.md`
  (English): state, pitfalls (ghcr non-redirect, old images orphaned, dated docs keep the
  old name), what is NOT validated (real ZIP-download walkthrough on Windows), next step.
- [ ] **Step 13.4**: Update Claude memory: project memory file renamed mission statement
  (`project-emule-indexer.md` content mentions the project name; note the rename and the new
  repo URL).
