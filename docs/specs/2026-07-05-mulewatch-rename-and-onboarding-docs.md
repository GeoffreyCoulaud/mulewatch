# Mulewatch rename and first-user onboarding docs

Date: 2026-07-05. Status: approved design, pre-plan.

## Goal

Make the first documents a new user sees (README, deployment runbook) good enough that a
motivated, low-technical fan deploys a node without friction. Rename the project to a name
that says what the tool does.

## Decisions (settled during design discussion)

| # | Decision | Rationale |
|---|---|---|
| D1 | New name: **mulewatch**, tool-oriented | The engine is generic (targets live in `targets.yml`); the name describes continuous eMule watching, not one quest. |
| D2 | **Full-depth rename**: repo, ghcr images, Python package, dist, entrypoints | No external users yet, cheapest moment. A partial rename would leave a visible mismatch (docs say mulewatch, `python -m emule_indexer.merge` says otherwise). |
| D3 | All user-facing docs stay **French** | Current audience is French-speaking (Keroro VF searchers). GitHub has no native multilingual README; a bilingual README means permanent double maintenance. Revisit if the tool is ever promoted beyond this quest. |
| D4 | Target persona: **level 1** (motivated fan, low technical skill) | Every command given verbatim, every step ends with a checkpoint. Third-party installs (Docker Desktop, VPN clients) are delegated to official docs, never duplicated. |
| D5 | Royal road: **observer, no VPN, webui + monitoring on by default** | Minimal friction recruits the most searchers. An observer node downloads and shares nothing. Webui and Grafana are the visible reward that the node works. VPN, download mode and High-ID become annexes. |
| D6 | Scope: README + `deployment.md` rewritten at level 1; `troubleshooting.md` gets a beginner pass limited to first-deployment failures; everything else changes only where the rename forces it | `administration.md` legitimately targets someone whose node already runs. |
| D7 | Royal-road stack file renamed to `deploy/compose.yaml` | Compose's default file name. The launch command becomes `docker compose up -d` from `deploy/`, nothing to explain. |
| D8 | `webui` and `monitoring` profiles are **removed**; those services always run in both stacks. `download` remains the only profile | Consequences accepted: two required secrets on the royal road (`AMULE_EC_PASSWORD`, `GRAFANA_PWD`) and a higher default RAM footprint. Opting out is an administration concern (`docker compose stop <service>`), documented there. |
| D9 | Obtaining the files must not require git | Dedicated guide step: GitHub ZIP download by default (click path described), `git clone` as the alternative for those who know it. |
| D10 | Commands must not intimidate | Short by construction where possible (D7). Where a variant command stays long, wrap it multi-line with `\` and note that PowerShell users put it on one line. |
| D11 | No em-dashes or en-dashes in any rewritten user-facing text | Global user rule. Existing docs violate it; rewritten ones must not. |

## Phase 1: rename (code PR)

Every artifact carrying the old name moves to `mulewatch`:

- **GitHub repo** `emule-indexer` renamed `mulewatch` (`gh repo rename`, done by the operator).
  GitHub redirects old repo URLs; **ghcr does NOT redirect image names**, so images must be
  republished and docs must point only to the new paths. Old ghcr packages can be deleted
  manually later.
- **Python package** `packages/crawler/src/emule_indexer/` becomes `src/mulewatch/`; dist name
  `emule-indexer` becomes `mulewatch`; all imports in crawler src + tests; entrypoints become
  `python -m mulewatch`, `python -m mulewatch.merge`, `python -m mulewatch.compact`.
- **Config keys tied to the name**: root `pyproject.toml` (workspace member + dependency),
  `packages/crawler/pyproject.toml` (`name`, `packages`, `--cov=`, `[tool.coverage] source`).
- **CI**: `IMAGE_PREFIX: ghcr.io/geoffreycoulaud/emule-indexer` in `release.yml` and
  `validate.yml` becomes `ghcr.io/geoffreycoulaud/mulewatch`.
- **Compose**: image references `emule-indexer-{crawler,verifier,webui}` become
  `mulewatch-{crawler,verifier,webui}` in `deploy/base.compose.yml` and `tests/smoke/`.
- **Living docs only**: README, runbooks, `docs/README.md`, `docs/testing-guide.md`,
  `docs/legal-and-privacy.md`, CLAUDE.md. Dated historical docs (`docs/handoffs/`,
  `docs/specs/`, `docs/plans/`, `docs/reference/`) keep the old name: they record history.
- The other three packages (`download_verifier`, `catalog_matching`, `catalog_webui`) do not
  carry the old name and do not change.

The full gate (mypy strict, per-package 100 % branch coverage, ruff, sqlfluff) catches any
broken reference. A plan-time grep inventory (`emule.indexer` case-insensitive, both separators)
drives the mechanical pass; the local checkout directory rename is the operator's choice and out
of scope.

## Phase 2: deploy restructure for the royal road (code PR)

- `deploy/direct.compose.yml` renamed `deploy/compose.yaml` (D7). `deploy/gluetun.compose.yml`
  keeps its name and its explicit `-f` invocation.
- `webui` and `monitoring` profile markers removed from `deploy/base.compose.yml` (D8).
- `deploy/.env.example` reordered for the royal road: the two required variables
  (`AMULE_EC_PASSWORD`, `GRAFANA_PWD`) first with choose-your-own guidance, VPN variables
  clearly marked as annex-only.
- Anything referencing `direct.compose.yml` or the removed profiles follows: smoke stack,
  compose integration tests, CI, runbooks.

## Phase 3: README rewrite (French, recruitment landing page)

Section order:

1. Title + mission pitch (2-3 sentences: the lost media, why permanent distributed watching).
2. Ethics callout: the catalogue's subject is the file, never the person.
3. **"Monter un nœud"**: prominent link to the deployment guide, with the promise
   "~15 minutes; installing Docker is the hardest step".
4. "Comment ça marche" in plain words (the 5-step loop, no jargon).
5. Functional status table (kept from the current README, refreshed).
6. Compact developer section: stack, gate command, links to specs and testing guide.
7. One line on the name: mulewatch is the tool; Keroro mission Titar VF is its first mission.

## Phase 4: deployment.md rewrite (French, royal-road guide)

Numbered steps, each closed by an explicit checkpoint ("you must see X; otherwise open
troubleshooting entry Y"):

1. What you need: a machine that stays on, permanent Internet, disk/RAM orders of magnitude.
2. Install Docker: link to the official per-OS doc only. Checkpoint: `docker compose version`
   prints v2.x.
3. Get mulewatch: GitHub ZIP by default (click path), `git clone` as alternative (D9).
4. Choose your two passwords: copy `deploy/.env.example` to `deploy/.env`, set
   `AMULE_EC_PASSWORD` and `GRAFANA_PWD`. The current "no change-me left" warning becomes an
   active checkpoint.
5. Launch: `docker compose up -d` from `deploy/`.
6. Check the node lives: what `ps` and the logs must show, normal delays (1-3 min for server
   lists), "Low-ID is not a failure".
7. See the catalogue: webui URL, what an empty-then-filling catalogue looks like; Grafana URL.
8. Living with the node: update and clean-stop short forms, links to administration for the rest.

Then annexes, each written as a delta from the royal road: A. behind a VPN (gluetun),
B. download mode, C. High-ID, D. tuning monitoring. The glossary stays at the end.

## Phase 5: troubleshooting beginner pass + validation

- First-deployment failures rewritten symptom-first, as the beginner sees them on screen:
  command not found, compose v1, container restart loop, `change-me` left in `.env`, amuled
  without network, port already in use. Deeper entries keep their current level.
- Validation: a blind walkthrough of the finished guide, step by step against the smoke stack,
  checking every checkpoint matches reality. Compose integration suites run outside the
  sandbox (operator-run, as usual).

## Execution notes

- Order matters: phase 1 merges before phases 3-5 are written, so the new texts are born with
  the right names (no correction pass). Phases 1 and 2 are separate PRs (mechanical churn vs
  semantic change). Phases 3-5 are docs-only and may merge locally per the workflow exception,
  except the `deploy/.env.example` and compose touches which ride phase 2.
- Subagent-driven execution per project workflow; holistic review before each PR.

## Non-goals

- English or bilingual docs (D3 records the revisit trigger).
- Rewriting `administration.md` at level 1.
- Renaming inside dated historical docs.
- A central hub, auto-update, or any new runtime feature.
