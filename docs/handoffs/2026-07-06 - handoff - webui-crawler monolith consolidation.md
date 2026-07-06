# Handoff: crawler + webui monolith consolidation (livrable 2)

- Date: 2026-07-06
- Spec: `docs/specs/2026-07-06-monolith-consolidation.md` · Plan: `docs/plans/2026-07-06-monolith-consolidation.md`
- Merged to `main`: P1-P4b (prior session, webui folded in + one container, tip `cbcb183`), P6a runtime controls (PR #28, `5643e0e`), P7 SQL console (PR #29, `8d37f4e`), P8 docs finalization (this handoff's branch, docs-only local merge).

## Current state

`python -m mulewatch` now runs the crawler AND the read-only webui in **one process, one image, one compose service**. The webui runs on its own thread with its own event loop; the crawler keeps its main-thread asyncio loop unchanged. Neither paralyses the other (the load-bearing decision, spec §5). A webui-thread crash degrades (loud log, the crawler keeps crawling, spec §17.1). A single shutdown path (SIGINT/SIGTERM or the restart control) stops both within the bounded deadline.

The whole workspace is green: matching 234, crawler 996, verifier 176 tests, 100% branch coverage per package, `mypy --strict` over src + tests, ruff, sqlfluff, template-check all clean. The `validate / gate` CI (lint + test + build-and-verify amd64/arm64 + gate) passed on PR #28 and #29.

The workspace is now **three packages** (`mulewatch`, `download_verifier`, `catalog_matching`); the old `catalog-webui` package/image/Dockerfile/service is gone. The webui lives under `packages/crawler/src/mulewatch/webui/`.

## What was built (by phase)

- **P1-P3 (prior session):** moved `catalog_webui` -> `mulewatch.webui` (package/image/Dockerfile/service removed, deps `starlette`/`uvicorn`/`jinja2` added to `mulewatch`); centralized read connection management (`adapters/persistence_sqlite/reader.py`: `open_reader` = `mode=ro` + `query_only` + `temp_store=MEMORY` + `Row`, and `ReaderProvider` = thread-affine reuse + a `quiesce()` seam for livrable 3); unified config (`crawler.yml` `webui:` section; the webui derives DB paths / targets / matcher from the crawler's ALREADY-PARSED config, so the explainer can no longer drift from the persisted decisions - spec §8).
- **P4b (prior session, `cbcb183`):** the webui is started in-process by `CrawlerApp._start_webui` (own daemon thread running `uvicorn.Server.serve()` on a fresh loop; graceful stop on the `AsyncExitStack`). The separate `webui` compose service was removed and the port published on the `crawler` service. **This is where "one container" was reached.** The compose smoke polls `/health` on the crawler.
- **P5 - deliberately NOT built (re-scoped):** the plan called for an in-memory `RuntimeSnapshot` on `/status`. Recon showed the existing **`/node` page already reads the persisted live state** from `local.db` (downloads, verification queue, `scheduler_state` = cycle index + per-channel backoff + last-cycle timestamp, node_id). The in-memory snapshot would only add the cycle *currently in progress* + search-worker liveness - marginal value for a thread-shared mutable snapshot. Skipped; can be revisited if that freshness ever matters.
- **P6a runtime controls (PR #28, `5643e0e`):** a thread-safe control channel + three loop-primitive controls.
  - `ports/crawler_control.py` - `CrawlerControl` port (`force_cycle` / `pause` / `resume` / `restart`); the webui depends on the port only.
  - `adapters/crawler_control_loop.py` - `LoopCrawlerControl` hands each intent onto the crawler loop via `loop.call_soon_threadsafe` (calling `Event.set()`/`.clear()` from the webui thread is unsafe). Holds no DB connection: the structural guarantee the webui can never write (spec §4).
  - `composition/app.py` - new `_force_cycle` + `_resumed` events; a pause gate `await self._resumed.wait()` at the top of `_run_loop`; `_sleep_or_forced` (modeled exactly on `run_download_cycle._sleep_or_nudge`) makes the inter-cycle sleep interruptible.
  - webui `/controls` page (logic-free template, PRG redirect, 0-or-1 status banner) + four POST routes; `Controls` nav link.
- **P6b - deliberately NOT built (re-scoped):** the plan's remaining two controls do not earn their place in the current architecture:
  - **Re-evaluate is a no-op today.** The matcher is parsed once at boot (no hot config reload) and every observation already decides live, so re-evaluating the catalog against the same in-memory engine reproduces identical decisions. The only way the matcher changes is editing `matcher.yml` + restarting, and the policy-fingerprint-gated **startup backfill already re-evaluates then** - the restart control (P6a) covers that case. On-demand re-eval would only help with hot config reload (absent) or catalog import (livrable 3).
  - **Requeue is premature.** It acts on download decisions, whose correctness depends on the multi-match matcher tuning the operator deferred (strange decisions on the real catalog). It also needs a per-`(hash, target)` UI that belongs to a download-management surface that does not exist yet. Revisit after the matcher is tuned.
- **P7 read-only SQL console (PR #29, `8d37f4e`):** `/console` - run one SELECT against `catalog.db` or `local.db`, see a result table + timing + row count, export CSV.
  - `adapters/sql_console.py` - `run_query` on a fresh `open_reader` connection per query. Structural read-only (`mode=ro` + `query_only`); DoS guardrails are the real protection: a wall-clock timeout via `set_progress_handler`, a returned-row cap (`ROW_CAP=1000`), single-statement-only. Runs on the webui threadpool (`run_in_threadpool`) so a slow query blocks neither the crawler thread nor the webui event loop. Arbitrary operator-SQL failures are absorbed into an error result (documented boundary, not a swallowed bug).
  - Urlencoded body parsed with stdlib `parse_qs` to avoid pulling in `python-multipart`.
- **P8 docs finalization (this branch, docs-only):** `CLAUDE.md` (four -> three packages), the three runbooks (in-process webui, one `crawler` service, no `WEBUI_HOST` env var, reverse-proxy target `crawler:8080`, `/controls` + `/console` documented under the trust-boundary posture), and this handoff.

## Security posture (reaffirmed, spec §12)

No built-in authentication, ever (permanent non-goal). Consolidation adds ONE inbound HTTP port to the crawler container, which previously had none; the container keeps its hardening (`read_only`, `cap_drop: ALL`, `no-new-privileges`). The webui is defensible only behind the operator's perimeter: private network / VPN / authenticated reverse proxy, never internet-exposed. P6a adds **state-changing POST controls** (the first non-read surface) with **no CSRF token** - a deliberate consequence of the no-auth posture (a token without sessions/auth adds nothing; the perimeter is the boundary). The SQL console is structurally read-only.

## Learned pitfalls (for the next effort)

- **Read the persisted state before building an in-memory mirror.** P5 nearly built a thread-shared `RuntimeSnapshot` before recon showed `/node` already surfaces the persisted runtime state. Most "live state" is in `local.db`; only cycle-in-progress + worker-liveness are memory-only.
- **A control is only worth building if it does something the architecture can act on.** Re-evaluate looked useful in the spec but is a no-op without hot config reload. Requeue is coupled to deferred matcher work. Reason about the effect, not the label.
- **Cross-thread event mutation MUST go through `call_soon_threadsafe`.** `asyncio.Event.set()`/`.clear()` are not safe to call from the webui thread directly (they may wake waiters affine to the crawler loop). The pause gate + shutdown compose cleanly: a shutdown cancels the `_run_loop` task at the pause-gate `await` with no deadlock.
- **`_sleep_or_forced` reuses the proven `_sleep_or_nudge` shape** (race `clock.sleep` vs an `asyncio.Event.wait()` with `FIRST_COMPLETED`, cancel the loser, then clear the flag) so one force triggers exactly one immediate cycle.
- **Logic-free templates:** the status/error/truncation banners use the `{% for x in (v,) if v %}` (0-or-1) idiom; any `selected`/attribute string is precomputed handler-side. The guard (`check_templates`) forbids `{% if %}`, filters, expressions, calls.
- **`build_app` gained a required `control` kwarg (P6a):** every test app-builder passes a `_RecordingControl` fake; mypy flags any missed call site.
- **Avoided a new dependency (`python-multipart`)** by parsing the console's urlencoded body with stdlib `parse_qs` - consistent with the project's supply-chain hygiene.

## NOT validated against real hardware

Everything above is validated by **unit tests (100% branch) + the compose `/health` smoke only**. Not yet exercised on a live node:

- **Single-process graceful shutdown under load:** SIGTERM (or the restart control) stopping both the crawler loop and the webui thread within `shutdown_deadline_seconds` on a real node with in-flight searches/downloads.
- **The runtime controls end to end:** force-cycle interrupting a real inter-cycle sleep; pause/resume gating real cycles; restart actually triggering the container's `restart: unless-stopped`.
- **The SQL console against a real large `catalog.db` in the hardened container.** WATCH THIS: `temp_store=MEMORY` means a heavy console query's intermediate b-trees live in the process heap bounded by the container `mem_limit`, not spilled to `/tmp` (this is the same trade-off as the livrable-1 `SQLITE_FULL` fix). The wall-clock timeout bounds *time*, not *memory* - a runaway query with a huge intermediate result could hit `mem_limit` and be OOM-killed rather than time out. Reproduce a few realistic queries via `docker exec` on the real DB at real scale (per the memory note "verify DB changes in the hardened container") before trusting the guardrails.
- **CSV formula-injection** is an accepted, un-mitigated residual: eMule filenames are hostile input, and a cell beginning with `= + - @` exported to CSV could be a spreadsheet formula. The apostrophe defense mangles legitimate values, which is wrong for a fidelity-critical power-user console; under the operator-only posture the risk is low. **Operator decision (2026-07-06): the export stays faithful** - nobody is going to weaponize Excel formula injection against the admin tool of a lost-media search tool, and mangling real values is worse than the residual risk.

## Suggested next step

Deploy the consolidated single-container stack to the operator's node (sync `deploy/` config so the webui is served in-process and the port is published on `crawler`). Confirm: one container serves crawl + webui; `/controls` force/pause/resume/restart behave; `/console` runs a SELECT and exports CSV; a graceful restart cycles the container. Then the deferred track: **tune the multi-match matcher** against the real catalog (the strange whole-file decisions the operator reported), after which **requeue** can be revisited; and **livrable 3** (heavy maintenance actions + versioned backups) using the `quiesce()` seam already left in `ReaderProvider`.
