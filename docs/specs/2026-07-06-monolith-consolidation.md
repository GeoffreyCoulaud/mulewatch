# Spec: crawler + webui monolith consolidation

Date: 2026-07-06
Status: approved (2026-07-06)
Scope: crawler + webui. Livrable 2 of 3 (livrable 1, the read-path perf fix, is shipped;
livrable 3, heavy maintenance actions + versioned backups, is a separate later spec).

## 1. Goal

Make **one Python program, in one container, one compose service**, run both the crawler and
the read-only webui, kept structurally distinct, with a hard invariant: **neither paralyses
the other**. The webui becomes presentation plus light control over the same process, and the
two derive from a single configuration and a single connection-management layer.

Concretely this buys, beyond "one less container":

- **Config correctness.** The webui re-runs the matcher explainer on `/files/{hash}`. Today it
  reads its own `MATCHER_CONFIG` env var, independent of the crawler's `matcher.yml`. If they
  drift, the explanation shown contradicts the persisted decision. One config kills that class
  of bug.
- **A live window on the running crawler**, not just a DB viewer: current cycle, per-channel
  backoff, in-flight downloads, verification queue, read from the process's own memory.
- **Light runtime control** (force a cycle, pause/resume, re-evaluate, requeue a download,
  full restart), impossible today across two processes.
- **Centralized connection management**: one place owns open policies (writer vs reader),
  reader reuse (warm page cache), and a future `quiesce()` seam that livrable 3's maintenance
  swap needs.

## 2. Non-goals (explicit, do not creep)

- **No authentication.** Deliberate, permanent non-goal. Trust boundary is the network:
  operator-only, private network, VPN or authenticated reverse proxy at the perimeter, never
  internet-exposed. Controls and the SQL console are defensible only under this posture and
  MUST be documented as firmly as the read-only stance already is.
- **No standalone RDBMS.** Stays SQLite, single-writer by doctrine.
- **Heavy maintenance actions and versioned backups are livrable 3**, not here (merge/compact
  from the UI, timestamped catalogs + active symlink, retention, pinning, import/export, the
  decision record relaxing "standalone tools never touch prod code"). This spec must not build
  any of it, only leave the `quiesce()` seam.
- **No change to the writer/reader SQLite safety split.** The crawler stays the single writer;
  the webui stays strictly read-only to the database. Centralization is of connection
  *management*, never a shared writer/reader connection object.

## 3. Current state (verified, livrable-1 recon)

- Two packages: `mulewatch` (crawler, one long-lived asyncio process, single writer holding two
  long-lived autocommit WAL connections) and `catalog-webui` (its own Starlette/uvicorn
  process, opens a fresh `mode=ro` + `query_only` connection per request, no DB writes).
- They are siblings: both import `catalog-matching`, neither imports the other. The webui even
  reimplements the crawler's `size_bytes -> size_mb` conversion to avoid a webui->crawler
  dependency (`matching_read.py`).
- The webui takes config from env vars (`CATALOG_DB`, `LOCAL_DB`, `TARGETS_CONFIG`,
  `MATCHER_CONFIG`, `WEBUI_HOST/PORT`), independent of the crawler YAML.
- The crawler has no inbound HTTP surface; it sits on EC/egress networks. The webui publishes a
  host port behind a reverse proxy. Both containers are hardened (`read_only`, `cap_drop: ALL`,
  `no-new-privileges`).

## 4. Target architecture: modular monolith

`mulewatch` absorbs the webui as a **structurally distinct inward-depending subpackage**
`mulewatch/webui/`. One image, one compose service, one entrypoint `python -m mulewatch` that
starts the crawler and the webui together. `python -m catalog_webui` and the `catalog-webui`
package/image/Dockerfile/compose-service are removed.

Dependency direction (the boundary that replaces the old "webui never imports crawler"):

```
mulewatch/webui/  (HTTP adapters, read models, templates, control adapter)
      |  depends inward on
      v
mulewatch/application ports  +  mulewatch/adapters/persistence (read side)  +  catalog_matching
      ^
      |  the crawler core does NOT import mulewatch/webui
composition/  (the only layer that wires crawler core + webui together)
```

Invariant: **the crawler core never imports `mulewatch.webui`; the webui subpackage never opens
a write connection.** All state mutation the webui triggers goes through crawler application
use-cases via a thread-safe command channel (section 10), never a DB write from the webui.

## 5. Process / runtime model (the load-bearing decision)

**Decision: uvicorn runs in its own thread with its own event loop; the crawler keeps its
main-thread asyncio loop unchanged.**

Rationale. The crawler deliberately runs synchronous SQLite writes on its event loop (safe
because single-writer), and its startup backfill iterates the whole catalog synchronously. If
the webui shared that loop, a backfill or a slow read would stall HTTP, and vice versa. Giving
the webui its own thread + loop yields true isolation with minimal disruption to the
battle-tested crawler loop:

- Crawler: main thread, its asyncio TaskGroup, single writer connection on that thread.
- WebUI: a dedicated thread runs `uvicorn.Server.serve()` on its own loop. Blocking SQLite
  reads run on a bounded threadpool; each pool thread gets its own reused RO connection
  (thread-affine, `check_same_thread`). Nothing the webui does touches the crawler's loop.
- They share only process memory (for the live-state snapshot, section 9) and the WAL files.

Alternatives rejected: single loop with webui reads offloaded to `to_thread` (still lets the
crawler's synchronous backfill block HTTP; more invasive to the crawler loop). Two processes in
one container via a supervisor (reintroduces the "two things" the user wants gone, complicates
the `cap_drop: ALL` / PID-1 signal handling).

Lifecycle: the composition root starts the crawler loop and the webui thread, and wires a
single shutdown path (SIGINT/SIGTERM) that stops both. **Open decision (section 17): a webui
thread crash should degrade (log loudly, crawler keeps crawling), not kill the process** since
the webui is presentation, not mission-critical. This is a considered exception to E-D13's
"in-process tested code crashes loudly" and needs sign-off.

## 6. Package & module layout

- Move `packages/webui/src/catalog_webui/**` into `packages/crawler/src/mulewatch/webui/**`,
  renamespaced `catalog_webui` -> `mulewatch.webui`.
- Move `packages/webui/tests/**` into `packages/crawler/tests/webui/**`.
- Delete the `catalog-webui` workspace member, its `pyproject.toml`, its Dockerfile.
- Add `starlette`, `uvicorn`, `jinja2` to `mulewatch` dependencies (they leave the removed
  package). Accepted cost of consolidation.
- Remove the duplication the old boundary forced: the webui reuses the crawler's real
  conversions and read models directly instead of reimplementing them.

This is the largest mechanical piece. It is a pure move+renamespace with no behaviour change,
guarded by moving the existing (green, 100%-coverage) webui tests wholesale. Best executed
subagent-driven in one focused step, gate-verified.

## 7. Centralized connection management

One module (`mulewatch/adapters/persistence_sqlite/`, extended) owns every open policy:

- `open_writer` (today's `open_catalog` / `open_local`): WAL, migrations, `foreign_keys`,
  `recursive_triggers`, long-lived, main thread, single writer. Unchanged.
- **Reader provider** for the webui: `mode=ro` + `PRAGMA query_only=ON`, `row_factory = Row`,
  **thread-local and reused** across requests (warms the SQLite page cache, removing today's
  per-request cold open). No WAL pragma (that is a write); the reader inherits WAL from the
  writer. Autocommit reads hold no persistent read lock, so a long-lived reader does not block
  the writer's WAL checkpointing.
- A `quiesce()` interface: close all reader connections and block new ones, for livrable 3's
  maintenance swap. Defined here now; only minimally exercised in livrable 2 (full maintenance
  use is livrable 3).

The webui's tiny `db.py open_ro` and its duplicated conversions are deleted in favour of this.

## 8. Unified configuration

`crawler.yml` gains a `webui:` section; the webui stops reading env vars of its own:

```yaml
webui:
  enabled: true          # false => headless crawler, no HTTP surface
  host: 0.0.0.0
  port: 8080
  page_size: 50
```

DB paths, targets and matcher come from the same parsed crawler config, so the webui explainer
uses the exact matcher the crawler decided with (the drift bug is gone). `${ENV}` interpolation
stays in the config adapter; the domain never sees env vars.

## 9. Live state window

The crawler publishes a thread-safe, read-only `RuntimeSnapshot` (immutable value re-published
by the crawler loop under a lock, or an atomic reference swap). The webui reads it from its own
thread for a `/status` view:

- current cycle index and last-cycle timestamp, per-channel backoff, in-flight downloads,
  pending verification count, per-endpoint search-worker liveness.

Persisted state already in `local.db` (scheduler_state, downloads, verification_tasks) may be
read RO from the DB; the in-memory snapshot adds freshness (a cycle in progress) and
non-persisted signals. Exact field list is an open question (section 17).

## 10. Runtime controls

The webui exposes POST endpoints that **dispatch intents to the crawler loop**, never touching
the DB directly. Cross-thread hand-off uses `loop.call_soon_threadsafe`; the crawler side owns
execution and the writer connection.

- **Force a cycle now**: set an `asyncio.Event` that interrupts the inter-cycle sleep.
- **Pause / resume crawl**: a flag checked before each cycle; pause lets the current cycle
  finish, then idles.
- **Re-evaluate**: trigger the existing backfill/re-evaluation use-case.
- **Requeue a download**: call the download use-case for a given `(ed2k_hash, target_id)`.
- **Full restart**: graceful shutdown + process exit, relying on the container
  `restart: unless-stopped` to bring it back. The webui goes down briefly with it (acceptable:
  one container). Needs sign-off (section 17).

The state view is first-class and ships even if some controls lag; controls are additive.

## 11. Custom read-only SQL

A power-user console, structurally safe:

- **Structural read-only**: a dedicated `mode=ro` connection (OS-level RO handle) plus
  `PRAGMA query_only=ON`. A write is impossible even if the SQL says otherwise.
- **DoS guardrails** (the real risks, since writes are impossible): a wall-clock timeout via
  `connection.set_progress_handler` (abort after N VM steps if elapsed exceeds the budget ->
  `OperationalError`); a returned-row cap (fetch at most K rows, report truncation); single
  statement only. Runs on the webui threadpool, so a heavy query blocks neither the crawler
  (other thread) nor other webui requests beyond its pool slot.
- **UI**: textarea + Run, result table, query time + row count, inline errors, CSV export.
- **Always enabled, no toggle** (decided 2026-07-06): the console is structurally read-only and
  the trust boundary is the network (section 12), so a disable switch would only be a
  false-safety knob.

## 12. Security posture

Reaffirmed and documented: no built-in auth (permanent non-goal), operator-only on a private
network, VPN or authenticated reverse proxy at the perimeter, never internet-exposed.
**Consolidation adds an inbound HTTP port to the crawler container**, which previously had none;
the container keeps its hardening (`read_only`, `cap_drop: ALL`, `no-new-privileges`) and the
webui port is its only inbound. This exposure change is a direct consequence of "one container"
and is called out for the runbooks.

## 13. Clean architecture and invariants respected

- Domain stays pure; the webui is adapters + read models + templates + a control adapter, all
  depending inward on application ports.
- The command channel (webui -> crawler) is an application-layer port with an adapter; no new
  domain impurity, DAG preserved.
- Writer/reader SQLite split preserved; the webui never holds a write connection.
- Boundary discipline: the crawler core does not import `mulewatch.webui`.
- The `deploy/config/` single-source rule holds: the webui derives from the same
  operator-owned config, never a second copy.

## 14. Deployment / compose changes

- Remove the `catalog-webui` image, Dockerfile, and its compose service.
- The `mulewatch` image gains `starlette`/`uvicorn`/`jinja2` and serves the webui.
- The crawler compose service publishes the webui port (behind the operator's reverse proxy).
- `deploy/config/crawler/crawler.yml` gains the `webui:` section; the webui env vars are
  removed from compose.
- Runbooks updated: single service, the new inbound port, controls + SQL console under the
  trust-boundary posture.

## 15. Testing plan (strict TDD, 100% branch per package)

- **Move first, green throughout**: relocate the webui tests into the crawler suite; the
  package's 100% coverage now spans the webui code. No behaviour change in the move.
- **Connection manager**: reader reuse (same connection handed to the same thread), RO
  enforcement (writes raise), thread affinity, `quiesce()` closes readers and blocks new ones.
- **Unified config**: the webui derives DB paths/targets/matcher from the parsed crawler config;
  a drift test proving the explainer uses the crawler's matcher.
- **Live state**: the snapshot reflects crawler state; thread-safe read returns a consistent
  value.
- **Controls**: each intent reaches the crawler side (force cycle sets the event; pause gates a
  cycle; requeue calls the use-case) via a fake loop/command channel; the webui holds no write
  connection (guard test).
- **SQL console**: a SELECT returns rows; a write is rejected structurally; the timeout aborts a
  runaway query; the row cap truncates; multi-statement rejected.
- Integration: a smoke test that one process serves HTTP and crawls without mutual stalling.

## 16. Risks

- **Thread-safety** of the live-state snapshot and the cross-thread command channel (the main
  new hazard). Mitigate with an immutable snapshot + `call_soon_threadsafe`, and tests.
- **The big move** (renamespace) risks breakage; mitigate with wholesale test relocation and
  the gate.
- **Exposure change**: the crawler container gains an inbound port. Mitigate by keeping
  hardening and documenting the posture.
- **Scope creep toward livrable 3**: resist; only the `quiesce()` seam is built here.

## 17. Decisions (approved 2026-07-06)

1. **WebUI thread crash policy: degrade.** A webui thread crash logs loudly and the crawler
   keeps crawling; it does not kill the process. The webui is presentation, not mission. This
   is a considered exception to E-D13 for the auxiliary surface only.
2. **SQL console: always enabled, no toggle** (structurally read-only + network trust boundary).
   Runtime controls are likewise available whenever the webui is on; there is no separate
   controls toggle. Only `webui.enabled` gates the whole HTTP surface.
3. **Full restart: graceful process exit**, relying on the container `restart: unless-stopped`.
   The webui goes down briefly with the process (acceptable: one container).
4. **Live-state fields (`/status`)**: current cycle index + last-cycle timestamp, per-channel
   backoff, in-flight downloads, pending verification count, per-endpoint search-worker liveness
   (section 9). Extendable later.
5. **Process model: uvicorn in its own thread** with its own loop (section 5), over the
   single-loop alternative.

## 18. Out of scope (livrable 3, separate spec)

Heavy maintenance from the UI (merge/compact), downloadable artifacts, timestamped catalog
versions + active symlink, backup retention/pinning/import/export, and the decision record
relaxing "standalone tools never touch prod code". Livrable 2 only leaves the `quiesce()` seam.
