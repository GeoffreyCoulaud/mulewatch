# Handoff: v1.0.0 prep - coverage/ops fixes + approved versioning spec

Branch `fix/coverage-and-ops-nits`, full gate green (`uv run poe check`: ruff, format, mypy strict
316 files, sqlfluff, templates, and all four package suites at 100% branch coverage: matching 239,
crawler 1000, verifier 176, vex_guards 73). Touches crawler code + tests + `deploy/`, so it goes
through a PR. Three results are **not** yet validated on the real node (see below).

Context: this is the pre-1.0.0 cleanup pass. Its sibling deliverable, the git-driven versioning
design, is written and approved but deliberately NOT implemented here:
`docs/specs/2026-07-10-git-driven-versioning.md` (Status: APPROVED). Implementing it is the
suggested next step.

## What this builds (five independent fixes, one per commit)

1. **A1 - dashboard coverage stops counting the catch-all** (`fix(webui)`, `webui/domain/coverage.py`).
   The `keroro_large` catch-all rule (tier `catalog`) matches any keroro file and, having no numeric
   token, resolves to the smallest `target_id` `001A` via the engine tie-break. The dashboard counted
   those catalog-tier decisions, so `001A` read `partial / catalog / 10 files` while nothing was
   actually identified (confirmed on the real node). `coverage_for` now filters
   `d[1] != "catalog"` before computing status/best_tier/file_count, the same criterion the `/files`
   "unidentified" mask already uses. A catalog-only target now reads `none / · / 0`. Exclusion only,
   no new UI (decided).

2. **A2 - the merge tool rejects off-version sources** (`fix(merge)`, `merge/merger.py`,
   `merge/errors.py`). `merge_catalogs` migrates the OUTPUT via `open_catalog` but used to `ATTACH`
   and copy each source without checking its schema. With catalog migrations `0002`/`0003` now
   present, an older or future source could silently mis-copy. New `SchemaVersionMismatchError`
   (a `MergeError` subtype, so the CLI exits non-zero): after ATTACH and before BEGIN, each source's
   `PRAGMA <src>.user_version` must equal the expected version, which is READ BACK from the migrated
   output (`PRAGMA user_version`), never hardcoded, so it tracks the migration set automatically.
   Strict equality (older and newer both refused). The tool still never migrates a source in place.

3. **A3 - em-dashes gone from runtime-emitted strings** (`fix(observability)`). The repo had ~481
   em/en-dashes, but only the ones inside emitted strings (logs, exception messages, CLI output) are
   user-facing. An AST classifier (not grep) found 42 emitted sites across 13 source files (grep's
   naive list mislabeled `__main__.py:113`, which is a docstring, and missed several real sites). All
   `—` separators became `:` (or `,`/`.`/parens). `tools/ec_probe.py` placeholder `"—"` became
   `"n/a"`. Two tests that asserted a changed message verbatim were updated
   (`test_app.py`, `test_ec_probe.py`). Boy-scout: comments/docstrings in the 15 touched files were
   also de-em-dashed (typographic only). Left alone: a real eMule filename fixture and a pytest skip
   reason (test infra, not prod runtime).

4. **freshclam healthcheck override** (`fix(deploy)`, `deploy/base.compose.yml`). The
   `clamav/clamav:1.4` image ships a HEALTHCHECK that pings clamd over a socket. We run only
   `freshclam` (no clamd daemon: the verifier runs clamscan on demand in its confined child), so the
   inherited check always failed and the container reported unhealthy despite an up-to-date DB. New
   override probes what matters for freshclam: the signature DB is present
   (`test -f /var/lib/clamav/daily.cld || .cvd`), with a 120s `start_period` for the initial download.

5. **`emule_search_capable` gauge** (`feat(observability)`). `emule_search_blind_cycles` is a
   cumulative COUNTER, poor at "am I blind NOW?". Added a binary current-state gauge (1 when at least
   one instance is search-capable, 0 when all blind), sampled EVERY cycle via a new
   `SearchCapabilitySampled(capable)` event through the normal event->policy->sink pipeline
   (`float(capable)` -> gauge `set`). This is the Grafana-facing signal (alert on "capable == 0 for
   N minutes"); we deliberately did NOT put blind state on the container healthcheck (a crawler
   restart cannot cure a blind node: the cause is amuled/VPN downstream). See the decision writeup in
   the versioning spec's neighbours and the discussion that produced this branch.

## Learned pitfalls

- **A blind node stays Docker-`healthy`.** The recent real-node incident (node blind for a while)
  was operator misconfig PLUS a netns trap: restarting `gluetun` orphans `amuled` (it shares
  gluetun's netns), so EC becomes `Connection refused` until `amuled` is restarted too. The crawler
  healthcheck is liveness-only by design, so nothing flipped unhealthy. The new
  `emule_search_capable` gauge is the intended visibility (Grafana), not the healthcheck.
- **The catch-all pollutes any per-target aggregate.** `001A` is the tie-break sink for every
  unidentified keroro file. Any future per-target view must apply the `tier == "catalog"` mask, or
  `001A` will look falsely recovered. The mask now lives in both `/files` and the dashboard.
- **setuptools-scm / the version must NOT be pinned in the VEX.** The `security/*.vex.openvex.json`
  products use a non-versioned purl on purpose (claims are structural, version-independent); the
  document `"version": N` is a doc-revision counter, not the product version. Recorded in the
  versioning spec section 5.6 so it is not re-litigated.
- **Changing an emitted message can break a verbatim test assertion.** A3 had to update two. When
  editing log/exception text, grep the tests for the old fragment.

## Suggested next step

Implement the approved versioning spec (`docs/specs/2026-07-10-git-driven-versioning.md`):
hatch-vcs on the four packages, `VERSION` build-arg feeding `SETUPTOOLS_SCM_PRETEND_VERSION` +
the OCI label, startup log on both services, `build_info` gauge, `fetch-depth: 0` in the version
step. Then the deferred `v1.0.0` product tag (a pushed, pure-semver tag) becomes meaningful.

## NOT validated against real hardware

- **freshclam healthcheck**: only `docker compose config` (syntax) passed here. Relaunch the
  freshclam service on the node and confirm it reports `healthy` once the DB is present.
- **`emule_search_capable` gauge**: unit-tested (0/1 through the sink registry), but not yet seen in
  the live Prometheus/Grafana. Confirm it scrapes and that a panel/alert on `== 0` behaves.
- **A1 dashboard**: unit-tested, but confirm on the deployed webui that `001A` no longer shows
  `partial / catalog / N` once redeployed.
