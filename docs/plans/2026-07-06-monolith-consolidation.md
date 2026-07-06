# Plan: crawler + webui monolith consolidation

Follows `docs/specs/2026-07-06-monolith-consolidation.md` (approved 2026-07-06). Eight ordered
phases, each a reviewable PR, TDD, gate green (100% branch per package) between each. Executed
subagent-driven. A phase lands before the next starts; later phases assume the earlier ones.

## P1. Move + renamespace (foundation, pure mechanical)

Move `packages/webui/src/catalog_webui/**` -> `packages/crawler/src/mulewatch/webui/**` and
`packages/webui/tests/**` -> `packages/crawler/tests/webui/**`, renamespacing
`catalog_webui` -> `mulewatch.webui`. Delete the `catalog-webui` workspace member + its
`pyproject.toml`. Add `starlette`, `uvicorn`, `jinja2` to `mulewatch` deps. Keep
`python -m catalog_webui` working *temporarily* as `python -m mulewatch.webui` (standalone
uvicorn, still reading its own env vars until P3) so nothing else breaks yet; the single-process
wiring is P4.

**No separate webui image (decided 2026-07-06):** the code is in `mulewatch`, so the crawler
image already contains the webui. There is no point publishing a second image. The webui
compose service uses the **crawler image** with an entrypoint override
`python -m mulewatch.webui`; the webui image build is removed from CI (`validate.yml`, the
`docker-image` action) and from Release (`publish-manifest` matrix), and its Dockerfile is
deleted. The webui stays a separate compose **service** (running the crawler image) only until
P4 folds it into the crawler process. So: one image from P1; one service from P4.

Config merge to get right: the moved webui tests run under the crawler's pytest, so the
crawler coverage `source` (`mulewatch`) now spans `mulewatch.webui` and must stay 100%; the
webui `conftest` (its `catalog_db`/`local_db` fixtures + the autouse connection-closer) moves to
`packages/crawler/tests/webui/conftest.py`. Regenerate `uv.lock` (`uv sync`).

- No behaviour change. The moved webui tests are the guard; they must stay green wholesale.
- Done when: `catalog-webui` is gone from the workspace, `mulewatch` owns the webui code, the
  full gate is green, there is no separate webui image (the webui service runs the crawler image
  via the entrypoint override), `python -m mulewatch.webui` serves the same pages, and the
  compose smoke still passes.

## P2. Centralized connection management

Extend `mulewatch/adapters/persistence_sqlite/` with a reader provider: `mode=ro` +
`query_only=ON` + `temp_store=MEMORY` + `row_factory=Row`, **thread-local and reused** across
requests. Add a `quiesce()` seam (close readers, block new ones) for livrable 3. Delete the
webui `db.py open_ro` and the duplicated `size_mb` conversion; the webui uses the crawler's
real read models.

- Tests: reader reuse per thread, RO enforced (writes raise), `temp_store=MEMORY` (carries the
  hotfix forward), thread affinity, `quiesce()` closes readers and blocks new ones.
- Done when: the webui reads through the shared provider; the gate is green.

## P3. Unified configuration

Add a `webui:` section to `crawler.yml` (`enabled`, `host`, `port`, `page_size`). The webui
derives DB paths / targets / matcher from the parsed crawler config; drop the webui env vars.
`${ENV}` interpolation stays in the config adapter.

- Tests: the webui explainer uses the crawler's parsed matcher (a drift guard proving one
  source); `webui.enabled: false` yields no HTTP surface; env interpolation resolved before the
  domain.
- Done when: the webui takes no env of its own; the gate is green.

## P4. Process model + composition (the integration)

`python -m mulewatch` starts the crawler loop (main thread, unchanged) AND a webui thread
running `uvicorn.Server.serve()` on its own loop. Single shutdown path stops both. A webui
thread crash degrades (loud log, crawler survives). Remove `python -m mulewatch.webui` as the
deployed entrypoint (may stay as a dev-only convenience). **Remove the separate webui compose
service** and publish the webui port on the crawler service: this is where "one container" is
actually reached.

- Tests: composition starts and stops both cleanly; a webui-thread exception does not stop the
  crawler; the crawler's synchronous work never runs on the webui thread and vice versa
  (structural: distinct loops/threads).
- Integration: one process serves HTTP while a crawl cycle runs, no mutual stall (smoke).
- Done when: one entrypoint, one process, both live; gate green.

## P5. Live state window

The crawler publishes an immutable `RuntimeSnapshot` (atomic reference swap) with the section-9
fields. The webui reads it from its thread for `/status`.

- Tests: the snapshot reflects crawler state; a cross-thread read returns a consistent value;
  `/status` renders it.
- Done when: `/status` shows live crawler state; gate green.

## P6. Runtime controls

A thread-safe command channel (`loop.call_soon_threadsafe`) carries webui POST intents to the
crawler side, which owns execution and the writer. Controls: force cycle, pause/resume,
re-evaluate, requeue a download, full restart (graceful exit). The webui holds no write
connection.

- Tests: each intent reaches the crawler side (force cycle sets the event; pause gates a cycle;
  requeue calls the use-case; restart triggers graceful shutdown) via a fake channel; guard
  test that the webui adapter opens no write connection.
- Done when: the five controls work end to end; gate green.

## P7. Custom read-only SQL console

A dedicated `mode=ro` + `query_only` connection. Guardrails: single statement, wall-clock
timeout via `set_progress_handler` -> abort, returned-row cap. Runs on the webui threadpool.
UI: textarea + Run, result table, time + row count, inline errors, CSV export. Always enabled.

- Tests: a SELECT returns rows; a write is rejected structurally; a runaway query is aborted by
  the timeout; the row cap truncates and flags it; multi-statement rejected.
- Done when: the console is usable and safe; gate green.

## P8. Deployment

The separate image is already gone (P1) and the separate service folded into the crawler (P4),
so P8 finalises: drop any remaining webui env vars from compose (now that config is unified,
P3), confirm the crawler service publishes the webui port behind the operator's reverse proxy,
and update the runbooks (one service, the new inbound port, controls + SQL console under the
trust-boundary posture).

- Integration: the compose smoke stack comes up with one service serving HTTP and crawling.
- Done when: one image, one service; smoke green; runbooks updated.

## Notes

- Fold the livrable-1 follow-up here: P2's reader plus, if the temp footprint at scale warrants
  it, an index-assisted latest-per-group in the read path (re-adding the observations composite
  index) to bound temp memory. Decide with a measurement during P2.
- Heavy maintenance + versioned backups stay out (livrable 3); P2 only leaves the `quiesce()`
  seam.
