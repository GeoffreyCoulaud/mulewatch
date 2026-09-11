# Handoff: download chain stalls on the live node (evidence for a spec)

Date: 2026-09-11
Branch: `main` (no code change yet, this is an evidence dump)
Spec: none yet. This handoff is the INPUT of a spec to be written in `docs/specs/`.
Node: the operator's local deployment, `~/Projets/2026-06-29 keroro emule` (Docker Desktop for
Linux, gluetun stack, `--profile download`, full mode).

## Why this document

The operator asked whether the files that reached tier `download` between 2026-09-01 and
2026-09-11 had been downloaded locally. **They have not, and nothing in the pipeline noticed.**
This records the evidence, the four defects found (two in the code, two in the deployment), and
what is still unverified. It is deliberately raw: numbers, commands, raw log lines. The spec
comes next.

## Verdict in one paragraph

Between 2026-09-01 and 2026-09-11, 11 decisions reached tier `download` (7 distinct files).
Only 2 of those files ever entered amuled's queue. The shared `quarantine` volume holds **zero
bytes**, so nothing was promoted and nothing was verified. The download loop has been inert
since **2026-09-04 12:20:25**: it aborts at step 1 of `run_download_cycle` and never reaches
candidate queueing or completion handling. Root trigger: `port_sync` restarted `amuled`
(2026-09-04 12:19:54, VPN port 65386 -> 65368), which killed the download EC stream. The search
worker reconnected on its own 5 minutes later; the download loop never reconnects. Two further
deployment defects (amuled's IncomingDir/TempDir outside the shared volume, and a write
permission mismatch) would have blocked the promotion anyway, and a fourth defect (a false
completion signal) already marked a 20 % download as `completed` in `local.db`.

## The node as observed

| Container | User | Notes |
|---|---|---|
| `mulewatch-crawler-1` | 999:999 | serves the webui, runs the search/download/verify loops |
| `amuled` | 1000 (`amule`) | shares gluetun's netns, EC on 4712, eD2k on 65368 |
| `mulewatch-verifier-1` | 999:999 | mounts `quarantine` read-only |

- Docker Desktop for Linux (context `desktop-linux`), so bind-mounted host files are presented
  inside a container as owned by **that container's UID** (measured, see "Deployment fix" below).
- Crawler restarts recorded: 2026-08-18, 08-19, 08-27 (x2), 08-31, 09-03, 09-04 12:14:55.
  **The 2026-09-04 12:14:55 one is the last.**
- `port_sync` restarted `amuled` 5 times: 2026-08-18 13:52, 08-18 19:07, 08-18 20:47,
  08-26 13:06, 2026-09-04 12:19:54.

## Finding 1 (code): the download EC client is connected once and never reconnected

**Contract vs implementation.** `run_download_cycle`'s module docstring promises the opposite of
what the code does:

> `MuleUnreachableError` (EC stream dead) -> tolerate, skip the iteration
> (**the client reconnects next round**; amuled persists the downloads).
> (`packages/crawler/src/mulewatch/application/run_download_cycle.py:22`)

Nothing reconnects. `connect()` is called exactly once, at wiring time:

- `composition/app.py:411-419`: `download_client = self._download_client_factory(endpoint)` then
  `await download_client.connect()`, tolerated once at startup.
- `application/run_download_cycle.py:347-354`: on `MuleUnreachableError`, the iteration `return`s.
- Nothing in `download_loop` (`:405`) or `_sleep_or_nudge` (`:386`) ever calls `connect()` again.
- `adapters/mule_ec/client.py:326-331`: when a request fails, `_request` calls `await self.close()`,
  which sets `_transport = None`. Every later call then hits
  `_require_transport()` -> `EcConnectError("EC client not connected (call connect() first)")`
  (`client.py:306-309`), mapped to `MuleUnreachableError` (`adapters/mule_ec/errors.py`).
- `AmuleEcClient.connect()` is idempotent by design (`client.py:84-86`: "a second call on an
  ALREADY-connected client is a no-op"), so re-calling it from the loop is free.

**The asymmetry, same daemon, same minute.** The search worker has the missing piece:

- `application/search_worker.py:209-228` `_ensure_connected()` calls `client.connect()` again and
  backs off on failure. Reconnection is asserted by tests
  (`tests/application/test_search_worker.py:290`, `:321`).

Raw log, 2026-09-04:

```
12:19:54,996 INFO  mulewatch.observability port-sync: 65386 → 65368 (restart amuled)
12:19:55,658 INFO  mulewatch.adapters.docker_restart_http amuled restart requested (status 204)
12:20:25,038 WARN  run_download_cycle download daemon unreachable
                   (EC connection lost: 0 bytes read on a total of 8 expected bytes)
12:20:55,049 WARN  run_download_cycle download daemon unreachable
                   (EC client not connected (call connect() first))     <- and every 30 s after
12:24:55,453 INFO  search_worker instance amule-1 connected            <- search healed itself
```

**Proof the daemon is fine.** From inside the crawler container, a brand-new EC client connects
immediately and answers (`AmuleEcClient("amuled", 4712, $AMULE_EC_PASSWORD)`, `download_queue()`
returns the 3 partfiles). The daemon is reachable; only the long-lived client's state is broken.

**The fix already exists twice, in prose.** The port-sync loop carries an explicit
`await deps.ports.connect()` and names this very failure mode
(`application/port_sync_loop.py:104-110`):

> ESSENTIAL after a restart: our own restart() - or a VPN renegotiation - kills the connection,
> and the client self-heals by nulling its transport on the next failed read. Without this call
> the loop would stay stuck "EC client not connected" forever (**the field deadlock**).

The same shape was therefore already found and repaired in the search worker and in the port-sync
loop; the download loop is the third caller of the same adapter and was missed.

**Consequence: step 1 aborts the whole iteration.** `_monitor` is the first step, so the early
`return` skips completions (step 2), candidate queueing (step 3) and `add_link` (step 4). That is
why 5 of the 7 files found since 2026-09-01 were never even added to amuled's queue.

**This is recurrent, not a one-off.** Counts of the same warning line, per day:

| Window | Lines | Duration at 30 s | Healed by |
|---|---|---|---|
| 2026-08-18 | 1 100 | ~9.2 h | crawler restart 08-19 07:00 |
| 2026-08-26 -> 08-27 | 3 680 | ~30.7 h | crawler restart 08-27 19:47 |
| **2026-09-04 -> 09-11 (ongoing)** | **20 616** | **~7 days** | nothing yet |

Pattern: **any `amuled` restart that is not accompanied by a crawler restart kills the download
pipeline until the next manual crawler restart.**

## Finding 2 (deployment): amuled's IncomingDir and TempDir are outside the shared volume

`amule.conf` in the `amule-state` volume:

```
IncomingDir=/downloads/incoming
TempDir=/downloads/temp
```

Both are container-internal paths (the container's overlay layer). The `quarantine` volume is
mounted at `/data/quarantine` in `amuled` but aMule never writes there. The crawler, meanwhile,
is configured with `staging_dir: /data/quarantine` and `quarantine_dir: /data/quarantine`
(`config/crawler/crawler.yml`), and promotes with `staging_dir / <real amuled name>`.

This is deployment constraint n°1 of
[`reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md)
("`staging_dir` = `quarantine_dir` = amuled's **IncomingDir**, same volume"). It was never
applied on this node. The observable signature, 2 814 times between 2026-09-02 12:31:42 and
2026-09-04 12:19:55, once every 30 s:

```
WARNING run_download_cycle quarantine failed for hash=e6af4963…
([Errno 2] No such file or directory:
 '/data/quarantine/[TV] KERORO MISSION TITAR N°065B « Le visiteur de Titar » […].avi'
 -> '/data/quarantine/e6af496363b10f61856958859a105731'): stays completed, retry
```

`administration.md` already warns that this chain is "functional by code reading but **not
validated on a real transfer**" (the e2e suite was abandoned). This node is its first real
exercise, and it fails.

**Secondary risk.** Because `TempDir` is on the overlay layer, the only bytes of a Keroro VF
episode this node ever received (22 MB, see below) live in an ephemeral layer: a
`docker compose up --force-recreate` or an image update loses them.

## Finding 3 (deployment): amuled cannot write into the shared quarantine

`amule` is uid 1000, the `quarantine` volume is `drwxr-xr-x 999 crontab` (created by the crawler,
uid 999), and there is no group write:

```
$ docker exec -u amule amuled touch /data/quarantine/x
touch: cannot touch '/data/quarantine/x': Permission denied
```

So even with Finding 2 fixed, the completion move (`IncomingDir/<name>`) would fail. **This one
disappears if `quarantine` becomes a bind mount**: Docker Desktop presents host files as owned by
the reading container's UID (measured: a host file owned by 1000:1000 shows as `999 999` inside
the uid-999 container and as `1000 1000` inside the uid-1000 container, and both could write into
the shared directory). A bind mount is therefore the fix with the least privilege churn.

## Finding 4 (logic): `shared_files()` is not a completion proof

The completion signal is "hash present in amuled's shared files", on the premise that aMule
auto-shares a file only once it is complete and moved to IncomingDir
(`reference/2026-06-17-amuled-completion-behavior.md`: "seeing the shared file guarantees it is
complete at its final path"). Measured on this node, that premise is **false**: aMule also shares
partially downloaded files (standard eMule behaviour: you upload the chunks you have).

Probed live from the crawler container against the real daemon:

```
== download_queue() : 3 entries
   d798ff4d9cbdd6b060f2f0019f676aeb  0/79841280        (0.0%)   is_complete=False
   e6af496363b10f61856958859a105731  22046740/109608960 (20.1%)  is_complete=False
   0a6c732c1331a9faef406252460379f8  0/152494080       (0.0%)   is_complete=False

== shared_files() : 1 entry
   e6af496363b10f61856958859a105731  '[TV] KERORO MISSION TITAR N°065B « Le visiteur de Titar » […].avi'
```

The single shared entry is the 20.1 % download. `_handle_completions` therefore set 065B to
`completed` in `local.db` at 2026-09-02 12:31:42 (`_promote_completion` marks COMPLETED *before*
promoting), and has been retrying a promotion that cannot succeed ever since. Note that
`DownloadEntry.is_complete` already exists and is correct on the port
(`ports/mule_download_client.py:33-37`); it is simply unused for completion detection.

If Finding 2 were fixed without this one, the next false completion would be **promoted and sent
to verification**: a 20 % file would be catalogued as a recovered episode.

## The stranded downloads

`local.db.downloads` (4 rows) and `local.db.verification_tasks` (**0 rows**):

| ed2k hash (short) | target | queued at | state | size | real progress | on disk |
|---|---|---|---|---|---|---|
| `57b7ddcb` | 062A | 2026-07-10 18:49 | downloading | 96 411 648 | unknown (not in amuled's queue) | no |
| `d798ff4d` | 001B | 2026-08-31 23:24 | downloading | 79 841 280 | 0.0 % (`001.part`) | no |
| `e6af4963` | 065B | 2026-09-01 23:04 | **completed** (false) | 109 608 960 | **20.1 %** (`002.part`, 22 MB real, sparse) | in amuled's overlay only |
| `0a6c732c` | 005B | 2026-09-03 11:38 | downloading | 152 494 080 | 0.0 % (`003.part`) | no |

Tier `download` decisions between 2026-09-01 and 2026-09-11 (11 rows in `match_decisions`,
7 distinct files). "Queued" means present in `local.db.downloads`:

| File (as observed) | Targets | Decided | Queued | Why not queued |
|---|---|---|---|---|
| N°065B « Le visiteur de Titar » | 065B | 09-01 23:04 | yes | (queued before the break) |
| N°066A « L'opération de sauvetage pour l'amour » | 005B, 045A, 048B, 049B, 066A | 09-03 11:38 | yes (as 005B) | one file, 5 targets, one queue entry |
| N°068A « Attention, travaux exceptionnels » | 068A | 09-07 21:20 | **no** | loop dead since 09-04 12:20 |
| N°067B « Et si on créait un jeu vidéo » | 067B | 09-07 21:20 | **no** | idem |
| N°068B « Le duel du destin » | 068B | 09-09 01:40 | **no** | idem |
| N°069A « Le voyage d'April et Artus » | 069A | 09-09 22:50 | **no** | idem |
| N°069B « Le concours estival de duos comiques » | 069B | 09-11 00:25 | **no** | idem |

All five pending targets have status `lost` in `targets.yml`, so once the loop runs again they
pass `download_policy` (committed bytes at that point: ~438 MB against a 50 GB cap) and reach
`add_link`. **They will be queued on the first healthy cycle after the deployment fix.**

Side observation, out of scope here: `0a6c732c` (a file named N°066A) carries 4 additional
targets (005B, 045A, 048B, 049B) matched by `title_confirmed`. Harmless for downloading (the loop
dedups by hash) but it is a matcher-precision signal worth a separate look.

## Why the tests missed all of this

- `tests/application/test_run_download_cycle.py` and `tests/application/fakes.py` define a
  `FakeDownloadClient` **with** a `connect()` method and a `connect_calls` counter, but no
  download-loop test ever asserts that the loop calls it. `connect_calls` is asserted only for
  the search worker and the port-sync loop.
- The fakes are scripted: they answer the same queue/shared snapshot whether or not `connect()`
  was called. They model a client whose transport never dies, so the live failure mode (transport
  discarded, every later call raising) is structurally unreachable in the current suite.
- No test covers the deployment invariants (IncomingDir == staging_dir, writability), and there
  is no e2e transfer test (abandoned on purpose, cf. `testing-guide.md`).

## What is NOT verified

- No transfer has ever been verified end to end on this node: nothing has passed through
  `promote` -> `enqueue_verification` -> verifier. The verifier, clamav and the dead-letter path
  are still unexercised in production.
- The bytes of 065B (20.1 %) have not been inspected (no container can decode them yet, and
  ffprobe has never seen a file from this chain).
- Whether aMule would put a completed file in a bind-mounted IncomingDir with the inherited
  ownership has not been tested (only the generic write test above).
- Amuled's Low-ID state since 2026-09-04 was a **stale WireGuard key** (see below), not a code
  defect. Resolved on 2026-09-11.
- The `download_integration` suite (the `-m download_integration` marker, deselected by default)
  **cannot run on this machine**: testcontainers fails at container start with
  `failed to create endpoint … on network bridge: failed to add the host (veth…) <=> sandbox
  (veth…) pair interfaces: operation not supported`, on the Ryuk container and on the amuled
  container alike, with `TESTCONTAINERS_RYUK_DISABLED=true` or not. Plain `docker run` (published
  ports included) works, so this is a Docker Desktop for Linux limitation, not a repo defect. The
  adapter-level validation against a real amuled therefore has to run in CI or on a Linux host.

## Deployment fix (DONE 2026-09-11 16:23, before any code change)

Goal: unblock the node, and make the downloaded files directly inspectable on the host.
The operator approved a bind mount of a working-folder directory for aMule's download area.

What was done, in order (all in `~/Projets/2026-06-29 keroro emule`):

1. `mkdir downloads/incoming downloads/temp`, then `docker cp amuled:/downloads/temp/.
   downloads/temp/` while the container was still up (9 files: the three `.part`, their `.met`
   and `.met.bak`; the 105 MB `002.part` de-sparsifies, harmless).
2. `docker stop amuled`, then edit `amule.conf` inside the `amule-state` volume:
   `IncomingDir=/data/quarantine`, `TempDir=/data/amule-temp`. Backup kept as
   `amule.conf.before-bindmount`. (Do not edit this file while amuled runs: it rewrites its
   config on shutdown.)
3. Replace the `quarantine` named volume with bind mounts in `base.compose.yml` (crawler:
   `./downloads/incoming:/data/quarantine`, verifier: the same read-only) and in `compose.yml` /
   `simple.compose.yaml` (amuled: `./downloads/incoming:/data/quarantine` plus
   `./downloads/temp:/data/amule-temp`). In-container paths are unchanged, so `crawler.yml` and
   `verifier.yml` needed no edit. The same pass synced the deployment copy back with
   `deploy/base.compose.yml`: the stale `build:` sections are gone (they made a missing image a
   build failure instead of a pull) and the `freshclam` healthcheck override is back (it fixes
   the permanently `unhealthy` sidecar; it pings clamd, which this stack does not run).
4. `docker compose -f compose.yml --profile download up -d` (recreates the changed services).

Result, observed within one cycle of the crawler restarting (final state 16:31):

- The download EC client reconnected (no `call connect() first` after the 16:30:17 restart).
- `local.db.downloads` went from 4 to 9 rows: the five pending files (068A, 069B, 068B, 069A,
  067B) were queued at 16:23:48, first cycle after the restart, all with status `lost` and
  well under the disk cap. They then reconciled to `downloading`: 8 `downloading` + 1
  `completed` (the false 065B).
- amuled's queue now holds 8 partfiles: the three originals (065B still at 20.1 %) plus the five
  new ones. Nothing was lost in the two container recreations.
- `005.part` (the 152 MB N°066A file) moved from `Waiting` with zero sources to `Downloading`
  with one source as soon as High-ID came back.
- Cross-container check: a file dropped in the host folder is visible in the crawler and in the
  verifier (read-only, `Read-only file system` on write), and a file written by the crawler
  appears on the host. The `quarantine` named volume is now unused and empty.

Notes and residual risks:

- **The bind mount removed Finding 3 entirely.** Docker Desktop for Linux presents bind-mounted
  host files as owned by the reading container's UID: measured, a host file owned by 1000:1000
  shows as `999 999` in a uid-999 container and as `1000 1000` in a uid-1000 container, and both
  could write into the shared directory. The crawler (999) could also rename a file created by
  the uid-1000 container, which is exactly the promote operation. No chown, no shared group, and
  no change to the `user:` directives.
- **The recurrence trap fired for real during this session.** Recreating `gluetun` with the new
  WireGuard key moved the forwarded port (65368 -> 63123), `port_sync` restarted `amuled` at
  16:30:00 to rebind, and the download EC client died immediately and stayed dead until the
  crawler was restarted at 16:30:17. That is Finding 1 reproducing on demand, in a recovery path
  the operator will exercise every time the VPN renegotiates. Until the code fix, the rule is:
  after any `port-sync: ... (restart amuled)` line, restart the crawler.
- **Finding 4 spams once per 30 s** (`quarantine failed for hash=e6af4963…`), because 065B is
  still marked `completed` while its bytes sit at 20.1 % in `downloads/temp/002.part`. Log noise
  only, but it must not be mistaken for a new failure.
- The two deployment templates in the repo (`deploy/base.compose.yml`,
  `deploy/gluetun.compose.yml`) still mount `quarantine` as a named volume with no IncomingDir
  wiring, so a freshly deployed node reproduces Finding 2 by construction. Whether the templates
  should ship this bind-mount layout (or a documented first-boot step) is a spec decision.
- The `mulewatch_quarantine` named volume is now orphaned and empty; it can be removed with
  `docker volume rm mulewatch_quarantine`. The deployment's `base.compose.yml` no longer declares
  it, and `amule.conf.before-bindmount` keeps the pre-change config in the `amule-state` volume.

Note that the crawler renames a promoted file to its bare hash inside `/data/quarantine`, so the
host folder is an inspection surface for bytes, not a nicely named library. The human-readable
name is the one recorded in the catalog.

## Network / High-ID (resolved 2026-09-11, same session)

While the deployment fix was being validated, the operator replaced the WireGuard private key in
`.env`, suspecting it was stale. That turned out to be the cause of the Low-ID state that had
held since 2026-09-04. The sequence, all observed:

1. Recreating `gluetun` with the new key brought the tunnel up (public IP in the Netherlands) and
   moved the forwarded port 65368 -> 63123.
2. Within one poll, `port_sync` did its job: `port-sync: 65368 → 63123 (restart amuled)`
   (16:30:00), which writes the preference over EC and restarts the container so aMule rebinds.
   Verified afterwards: `amule.conf` has `Port=63123` and `UDPPort=63123`, and aMule listens on
   `0.0.0.0:63123` inside the gluetun netns (`/proc/net/tcp` shows `F693`).
3. **aMule then connected with High-ID** (`Connected to eMule Sunrise with HighID`), after five
   weeks of Low-ID with Kad firewalled.

Worth keeping for the next diagnosis: with the *old* key, gluetun still reported a forwarded
port and aMule still listened on exactly that port, so the local configuration looked correct
while the servers insisted on Low-ID. Neither an `amuled` restart nor the ipfilter was at fault
(the eD2k server IPs are not in `ipfilter.dat`). The tunnel being up is therefore **not** proof
that inbound traffic reaches the node: only the ID does.

Consequence for the mission: High-ID means sources are no longer acquired through Kad only, so
the eight queued downloads (065B at 20.1 % plus the five new candidates and the two older ones)
now have a real chance of progressing.

## Code fix (DONE 2026-09-11, gate green)

Both defects are fixed in one commit-sized change, "simple and robust" per the operator: no new
dependency, no new port, no state machine rework.

1. **The loop reconnects (Finding 1).** `run_download_cycle` gained a step 0 that does
   `await deps.client.connect()` then the single `download_queue()` read, in one `try` whose
   `MuleUnreachableError` aborts the iteration with the existing message. `connect()` is
   idempotent, so on a live stream this is a no-op; on a dead one it re-arms the transport the
   adapter threw away. The queue snapshot is then shared by steps 1 and 2 (`_monitor` takes it as
   an argument instead of reading it), so the cycle still performs exactly ONE
   `download_queue()` per iteration. No backoff was added: the loop's own 30 s cadence is the
   retry policy, which is what it already did for every other client failure.
2. **Completion detection is sound (Finding 4).** `_handle_completions` now skips any shared hash
   that is still in the queue snapshot: "shared AND gone from the queue" is the completion, which
   is precisely the `PS_COMPLETE` semantics (a finished file leaves `m_filelist`; a partial stays).
   The queue is read before the shared list, so a file completing in between is promoted one cycle
   later (the shared signal persists, the delay is free).
3. **Tests that would have failed here** (written first, red before the fix):
   `test_cycle_reconnects_a_dead_transport_before_any_io`,
   `test_reconnect_failure_skips_the_iteration_without_raising`,
   `test_shared_hash_still_in_the_download_queue_is_not_a_completion` (reproduces 065B at 20.1 %),
   `test_shared_hash_absent_from_the_download_queue_is_a_completion` (positive control with a
   non-empty queue). `FakeDownloadClient` gained a `disconnected` mode that mirrors the real
   adapter: every I/O raises until `connect()` succeeds.
   `test_monitor_repo_error_still_promotes_completions_in_same_cycle` had its fixture adjusted: it
   scripted the completing hash as STILL QUEUED, i.e. it encoded the very assumption that turned
   out to be false. The completing hash was removed from that scripted queue; the test's intent
   (a repo failure at step 1 must not starve a completion at step 2) is unchanged.
4. **Docs corrected where they now lie.** The reference
   `2026-06-17-amuled-completion-behavior.md` claimed "seeing the shared file guarantees it is
   complete at its final path"; it now carries a dated CORRECTION and the corrected rule.
   `administration.md`'s DV10 paragraph was updated the same way, with a pointer to this handoff.

Gate: `uv run poe check` EXIT 0 (ruff, ruff format, mypy strict on 317 files, sqlfluff, template
check, then matching 253 / crawler 1069 (+4) / verifier 177 / vex_guards 73, 100 % branch coverage
per package).

Still open, deliberately: the fastapi/composition startup `connect()` in `_build_full_loops` was
kept (a no-op once step 0 exists, and it still logs an unreachable daemon at boot). The `Backoff`
machinery was NOT imported into the download loop. The pre-existing audit finding
`2026-06-23-codebase-audit-findings.md:156` (post-promote `enqueue_verification` failure loops
forever) is untouched: it is a different defect and out of this scope.

Not yet deployed: the node runs `ghcr.io/geoffreycoulaud/mulewatch-crawler:latest` built from
`8b658b7`, so this fix needs a new image (push to `main` triggers `release.yml`; the node then
pulls). Nothing in the fix changes the matcher policy, and `matcher.yml` is bind-mounted from the
working folder, so a pull does NOT trigger a catalogue re-evaluation.

Out of scope of this handoff, but implied by Findings 2 and 4: the deployment templates in
`deploy/` mount `quarantine` as a named volume with no IncomingDir wiring, so a fresh node
reproduces Finding 2 by construction. Whether the templates should ship the bind-mount layout (or
a documented first-boot step that sets IncomingDir) is a spec decision.

Side observation, unrelated to the two defects: the working folder's `matcher.yml` is AHEAD of the
repo template (`deploy/config/crawler/matcher.yml`): its `not_episode` regex has `onlyfans` added
and `movie` unanchored, and that edit was never committed. Copying the template over the working
folder would silently revert it.
