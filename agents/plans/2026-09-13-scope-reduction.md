# Plan: scope reduction (catalog, notify, download)

Follows `agents/specs/2026-09-13-scope-reduction-catalog-notify-download.md`. Branch:
`feat/scope-reduction`, in place on the working tree (no worktree), so lots must stay
file-disjoint when run concurrently.

## Lot ordering

```
wave 1 (parallel)   A1 crawler core   C verifier+CI+supply-chain   D deploy+smoke yaml   F journal banners
wave 2              A2 webui                  (after A1: same package, same coverage run)
wave 3              E living docs             (after 1+2: needs the final state to be accurate)
wave 4              gate + holistic review + handoff + PR
```

Concurrency rule: only one agent at a time may run `pytest` inside `packages/crawler`
(package-wide `.coverage` file, `--cov-fail-under=100`). A1 and A2 are therefore sequential.
C, D and F touch no crawler test.

## Lot A1: crawler core

Files owned: everything under `packages/crawler/src/mulewatch/` except `webui/`, plus the
matching tests under `packages/crawler/tests/` except `tests/webui/`, plus
`packages/crawler/pyproject.toml`.

Delete outright:
- `application/run_verification_cycle.py`, `adapters/verifier_http.py`,
  `ports/content_verifier.py`, `ports/verifier_errors.py`, `adapters/quarantine_fs.py`,
  `ports/quarantine.py`
- their tests, plus `tests/application/test_verification_loop.py`,
  `tests/adapters/persistence_sqlite/test_catalog_verification.py`,
  `tests/integration/test_verify_loop.py`

Edit:
- `domain/download/states.py`: drop `QUARANTINED`, `_TERMINAL_STATES` becomes
  `{completed, failed}`
- `domain/observability/events.py` + `policy.py`: drop `PromotionFailed`,
  `VerificationCompleted`, `VerifierUnavailable`, `VerificationQueueDepthSampled`, the
  `_VERDICT_*` tables and `_verification`, and the four metric names
- `adapters/config/crawler_config.py`: drop `staging_dir`, `quarantine_dir`, `verifier_url`,
  `verify` from `DownloadConfig` and `_parse_download`
- `application/run_download_cycle.py`: drop the promotion step; `completed` is terminal
- `composition/app.py` + `__main__.py`: unwire the verification loop, the quarantine adapter and
  the verifier health check fail-fast
- `adapters/persistence_sqlite/`: drop the verification repo methods; keep everything else
- `pyproject.toml`: drop the `verify_integration` marker and its `addopts` exclusion

New migrations:
- `migrations/catalog/0005_drop_file_verifications.sql`: drop the two append-only triggers,
  then `idx_file_verifications_hash_verified`, then the table
- `migrations/local/0004_drop_verification_tasks.sql`: drop the table and its two indices, then
  `UPDATE downloads SET state = 'completed' WHERE state = 'quarantined'`

TDD: the migration tests come first, including a red test proving a legacy `quarantined` row is
readable through `DownloadState` after migration (spec §7).

## Lot A2: webui

Files owned: `packages/crawler/src/mulewatch/webui/**`, `packages/crawler/tests/webui/**`.

Remove the verification column from `files.html`, the verification section from
`file_detail.html`, the queue-depth panel from `node.html`, and the backing reads in
`catalog_read.py`, `local_read.py`, `views.py`, `format.py`, `composition/app.py`. No redesign:
the surrounding layout stays as it is.

## Lot C: verifier package, CI, supply chain

- delete `packages/verifier/` and `security/verifier.vex.openvex.json`
- root `pyproject.toml`: drop the workspace member and the `analysis_integration` marker
- `.github/workflows/release.yml`, `grype-scan.yml`, `validate.yml`, `pr.yml`,
  `.github/actions/docker-image/`, `.github/dependabot.yml`: one image, three packages
- `SECURITY.md`: rewrite for a single image
- `deploy/config/verifier.yml`: delete

## Lot D: deploy and smoke stack

- `deploy/base.compose.yml`: drop `verifier`, `freshclam`, `prometheus`, `grafana`, the
  `clamav-db` / `prometheus-data` / `grafana-data` / `quarantine` volumes, the `verify-internal`
  network, and the crawler's quarantine mount
- `deploy/compose.yaml` and `deploy/gluetun.compose.yml`: drop the `download` profile everywhere,
  bind-mount `./downloads/incoming` and `./downloads/temp` on amuled, refresh the header comments
- `deploy/config/crawler/crawler.yml`: `download.enabled: true`, drop the four dead keys,
  `port_sync.enabled` stays `false`
- `deploy/config/prometheus.yml` and `deploy/config/grafana/`: delete
- `deploy/.env.example`: drop `GRAFANA_PWD`, `GRAFANA_PORT`
- `tests/smoke/compose.yaml` and `tests/smoke/*.yml`: single profile-less stack, amuled + crawler

`tests/smoke/compose.yaml` is lot D; `packages/crawler/tests/integration/test_compose_smoke.py`
is lot A1. Contract between them: one stack, no profiles, services `amuled` and `crawler`.

## Lot F: journal banners

Prepend to each fully obsolete dated document, below its title:

```markdown
> **Obsolete since 2026-09-13.** The subsystem described here left the project's scope.
> See `agents/specs/2026-09-13-scope-reduction-catalog-notify-download.md`.
```

Candidates (to confirm by reading each): specs `2026-06-13-verification-pipeline-design`,
`2026-06-14-analysis-design`, `2026-06-15-clamav-design`, `2026-06-15-e2e-suite-design`,
`2026-06-15-ring-noyau-design`; plans `2026-06-10-crawler-mvp-07-verification-pipeline`,
`2026-06-10-crawler-mvp-08-analysis`, `2026-06-15-observability-e3-verifier`; handoffs
`2026-06-14 - verification pipeline`, `2026-06-14 - analysis (real verifier)`,
`2026-06-15 - observabilite E3 verifier`, `2026-07-08 - clamav 1.4.5 CVE batch`.

Partially obsolete documents get no banner: the banner means "this subsystem is gone", not "some
details drifted".

## Lot E: living docs

`README.md`, `docs/README.md`, `docs/architecture.md`, `docs/testing-guide.md`,
`docs/legal-and-privacy.md`, the three runbooks, `AGENTS.md`. Rewrite to the post-change state:
three packages, one image, no verifier, no bundled metrics stack, no quarantine, no compose
profile, bind-mounted output.

`AGENTS.md` needs the confinement and third-party-hardening decision records trimmed to what
still exists (the freshclam record goes; the amuled one stays).
