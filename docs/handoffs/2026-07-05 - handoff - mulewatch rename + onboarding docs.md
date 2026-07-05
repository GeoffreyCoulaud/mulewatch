# Handoff: mulewatch rename + first-user onboarding docs

Date: 2026-07-05. Tag: `v0.26.0-mulewatch-onboarding` (not pushed, as usual).
Spec: `docs/specs/2026-07-05-mulewatch-rename-and-onboarding-docs.md` (decisions D1-D11).
Plan: `docs/plans/2026-07-05-mulewatch-rename-and-onboarding.md`.

## What just landed (3 integration units, all on main)

1. **PR #19, full-depth rename to `mulewatch`** (D1/D2). GitHub repo renamed
   `GeoffreyCoulaud/emule-indexer` → `GeoffreyCoulaud/mulewatch` (old URLs redirect). Crawler
   package `emule_indexer` → `mulewatch` (dist, entrypoints `python -m mulewatch{,.merge,.compact}`,
   Dockerfile, EC client name). Images republished as
   `ghcr.io/geoffreycoulaud/mulewatch-{crawler,verifier,webui}` (verified present). Grafana
   dashboard uid/title/filename + provisioning provider renamed. Living docs renamed; dated
   historical docs deliberately keep the old name. Prometheus metric names `emule_*` are domain
   names and did NOT change.
2. **PR #20, royal-road deploy restructure** (D5/D7/D8). `deploy/direct.compose.yml` →
   `deploy/compose.yaml`; top-level `name: mulewatch` on both stacks; webui + prometheus +
   grafana always on; `download` is the only remaining profile; `.env.example` reordered
   (2 required secrets, `WEBUI_PORT` gap fixed); volume-migration note for pre-rename nodes in
   the administration runbook; compose smoke test follows (now asserts rendered service sets,
   including docker-proxy iff download on gluetun).
3. **Docs branch (local merge, docs-only)**: README rewritten as a French recruitment landing
   page; `deployment.md` rewritten as an 8-step royal-road guide (each step ends with a
   *Point de contrôle* naming exactly what to see and which troubleshooting entry to open
   otherwise) + annexes A-D as deltas + glossary; `troubleshooting.md` got a beginner-first
   tier (7 symptom-first entries, titles are a 1:1 anchor contract with the guide) above the
   preserved operator tier.

## How it was validated

- Per-task reviews + whole-branch reviews (one caught a CRITICAL: the compose smoke
  integration test still iterated over `("gluetun", "direct")`; local unit gates cannot see
  compose_integration, only CI runs it. Lesson: any deploy-file rename must grep
  `tests/integration/` too).
- **Blind persona walkthrough** (fresh-context agent, Windows 11 persona, docs-only access):
  found 10 real frictions, all fixed (Windows `notepad`/`Select-String` equivalents, Docker
  engine-not-started detection + new troubleshooting entry, ZIP double-folder trap, honest
  15-minutes-once-Docker-installed framing, Grafana first-boot password remedy).
- **Live-node checkpoint audit**: the guide's step 5-7 checkpoints were compared against a
  real running stack (5 services Up, log lines verbatim incl. the `effective_coverage=blind`
  transient, webui/grafana HTTP 200). Sample log lines in the guide are real emissions.

## Pitfalls learned

- **ghcr does not redirect image names** (unlike repo URLs). Old `emule-indexer-*` packages
  are orphaned on ghcr; the operator can delete them whenever.
- **Compose project name**: with no top-level `name:`, compose derives it from the compose
  file's directory (`deploy`), NOT the repo folder. Docs claiming `<project>_volume` names were
  wrong before `name: mulewatch` landed. Existing nodes migrating across the rename get FRESH
  volumes; the copy recipe lives in administration.md (lifecycle section).
- **Grafana applies `GF_SECURITY_ADMIN_PASSWORD` only on first boot**; with the persistent
  `grafana-data` volume, fixing `.env` alone never repairs a typo'd password. Both remedy
  blocks document the volume reset.
- **`grep change-me deploy/.env` can never be the checkpoint**: the copied `.env` legitimately
  keeps `change-me` in comments and the unused VPN section. The precise per-variable grep is
  the correct check.
- GitHub repo settings allow **rebase merges only** (squash disabled); linear history via
  rebase. A PR built on unpushed local main commits gets those commits rebased with new SHAs.

## Not validated against real conditions

- The guide's Windows 11 path (notepad, Select-String, ZIP double-folder) was validated by
  persona simulation only, never on a physical Windows machine.
- The full ZIP-download walkthrough end-to-end by a human who has never seen the repo.
- The volume-migration recipe (old `deploy_*` → `mulewatch_*`) was reviewed, not executed
  against a node with real data.

## Known follow-ups (out of scope, tracked here)

- Prod code emits an em-dash in a WARNING (`run_search_cycle`: `… — not capable`), violating
  the no-long-dash rule for CLI copy. One-line fix + test wording check.
- "laisser derrière" anglicism in administration.md:210 (pre-existing on main).
- Old `emule-indexer-*` ghcr packages: delete when convenient.
- `docs/architecture.md` and `docs/README.md` were only mechanically renamed; a content
  refresh pass would bring them in line with the new deploy reality (not urgent, operator docs
  of record are the runbooks).

## Suggested next step

Recruit the first external searcher and watch them follow the guide for real (the one
validation no simulation replaces). Alternatively, resume the catalog re-evaluation thread
from the 2026-07-04 handoff.
