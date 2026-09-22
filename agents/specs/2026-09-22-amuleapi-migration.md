# Spec: migrate from EC to amuleapi (aMule 3.1.0)

Date: 2026-09-22
Status: awaiting operator approval
Supersedes the transport half of `agents/specs/2026-06-10-crawler-mvp-design.md` §4 and the
whole of `agents/reference/ec-protocol.md` (which becomes a historical record).

## 1. Context

aMule 3.1.0 shipped 2026-09-21 (893 merged PRs on top of 3.0.1). Two things in it change what
mulewatch can do:

- **`amuleapi`**, a new daemon that exposes amuled over `/api/v1/*` REST plus a Server-Sent
  Events stream, and serves a complete Web UI. It replaces `amuleweb`, which upstream now
  declares deprecated and may remove in 3.2.
- **Media metadata travels end to end.** `EC_TAG_KNOWNFILE_MEDIA_{LENGTH,BITRATE,CODEC,
  ARTIST,ALBUM,TITLE}` (0x0410 to 0x0415) are emitted on search results, not just on shared
  files (`src/ECSpecialCoreTags.cpp:609`, `AddMediaTagsPresent`). The values come from the
  `FT_MEDIA_*` tags the network hit already carries (`src/SearchFile.cpp:105` keeps the
  server's tag list), so they arrive without any local probing.

The operator's decision: adopt amuleapi fully, both as the aMule-facing Web UI and as the
crawler's way of talking to amuled. The hand-written EC binary client goes away.

### 1.1 Why this is an adapter swap, not a rewrite

Every operation `AmuleEcClient` performs has a REST counterpart, and the two ports
(`ports/mule_client.py`, `ports/mule_download_client.py`) do not change shape. See §6 for the
full mapping. The dependency DAG is unchanged: a new `adapters/mule_api/` replaces
`adapters/mule_ec/` behind the same Protocols.

### 1.2 What it deletes

| File | Lines |
|---|---|
| `adapters/mule_ec/codec.py` | 263 |
| `adapters/mule_ec/client.py` | 423 |
| `adapters/mule_ec/mapping.py` | 139 |
| `adapters/mule_ec/codes.py` | 138 |
| `adapters/mule_ec/transport.py` | 77 |
| `adapters/mule_ec/errors.py`, `__init__.py` | 48 |
| `tools/ec_probe.py` | 276 |
| `tools/download_probe.py` | (whole file) |

Plus the matching test suites. `httpx` is already a dependency
(`packages/crawler/pyproject.toml`), so nothing new is added.

The EC-specific gotchas in `AGENTS.md` retire with the code: the partfile hash hidden in the
`EC_TAG_PARTFILE_HASH` (0x031E) child tag, the volatile ECIDs, the IP byte order, the flags
mask. The API answers in dotted quads, hex hashes and typed JSON, and documents every endpoint
as idempotent.

## 2. Decisions

### D1. amuled launches amuleapi (autorun), rather than an s6 service of its own

`amule.conf` `[AmuleApi] Enabled=1` makes amuled start amuleapi in `OnInit`
(`src/amule.cpp:1297`), passing `--bind` and `--http-port` on the command line
(`amule.cpp:1349-1352`) and handing it a one-off EC token that the child unlinks as soon as
it has read it (with a backstop in amuled that removes it unread after a timeout,
`amule.cpp:2045`).

This is the option with the least to maintain, and by a wider margin than it first looks:

- **No `amuleapi.conf` at all.** Bind address and port arrive on the command line, so
  `docker/amule-config.py` needs three keys in `amule.conf`, a file it already writes, rather
  than a second config-file writer and its tests.
- **No second copy of the EC password.** The ephemeral token replaces it. `amuleweb` today
  takes the password on its command line, so this is strictly better than what it replaces.
- **One s6 service directory removed, none added.**

The risks were checked against the 3.1.0 source rather than assumed, and are narrower than
the supervision argument suggests:

| Risk | Verified behaviour | Why it is acceptable |
|---|---|---|
| amuleapi is not restarted if it dies | `TerminationProcessAmuleApi.cpp:36` sets the pid to 0 and nothing respawns; the launch is a one-shot in `OnInit` | amuled dying is the common failure and s6 still covers it: restarting amuled respawns amuleapi. Only "amuleapi dies alone" is uncovered, and the existing unreachable-daemon alerting sees it |
| its output is never drained | the constructor calls `Redirect()`, creating pipes, and `CTerminationProcess` only logs on terminate: nothing reads them, so a full 64 KiB pipe would block the child on `write()` | `src/webapi/HttpServer.cpp` has no per-request logging. Across `src/webapi/` there are 13 console write sites: startup, `WARN`, `FATAL`, and `500 from handler`. Filling 64 KiB needs a sustained error loop, which is already a broken state |
| its output is not in `docker logs` | same cause | amuleapi writes `amuleapi.log` in its config dir, which is the bind-mounted `deploy/amule/`. The output moves, it is not lost |
| an orphaned process on shutdown | `amule.cpp:364-372` sends SIGTERM then SIGKILL | not a risk |

**Condition.** amuled passes no `--static-root`, so this decision holds only if amuleapi
finds its `amuleapi-static` folder unaided (see §3.1). If it does not, `StaticRoot` forces an
`amuleapi.conf` back into existence and most of the saving above evaporates; revisit D1 then
rather than writing the config file and keeping autorun.

### D2. amuleweb is removed

Upstream declares it deprecated, prints a notice at startup, and names amuleapi its intended
replacement. amuleapi's Web UI covers the same sections as the desktop GUI (Networks, Search
with tabs, Downloads, Shared Files, Clients, Messages, Statistics, Preferences), in light and
dark, responsive. Keeping both would mean three HTTP surfaces for two jobs.

Port 4711 becomes 4713. The image drops the `amuleweb` binary from the closure and the
`amuleweb` s6 service.

### D3. mulewatch's own webui stays

`mulewatch.webui` serves the catalog, the match decisions and the SQL console. amuleapi knows
nothing about any of that. After the migration the node publishes 8080 (catalog) and 4713
(aMule), against 8080 and 4711 today.

### D4. Alternate filenames are flattened back into observations

In EC, a hit advertised under several filenames arrives as several `EC_TAG_SEARCHFILE`
entries, each becoming its own `FileObservation`. In REST, amuled folds them: only parents
appear at the top level and the others ride `alternate_names[]`. A naive mapping therefore
loses filenames.

That is not acceptable, and it is not a theoretical concern. Measured on the production
catalog (`/home/geoffrey/Projets/mulewatch/data/catalog.db`, 1837 hashes, 8 272 687
observations) by running the production policy over every distinct filename per hash:

```
602 hashes carry more than one distinct filename            (33 %)
 43 of them: one name matches a target, another matches nothing at all
```

and the 43 are the subject matter itself:

```
08fd5637...   no match        Sgt. Frog - 095 [Keroro] (640x480 XviD) [F93E9688].avi
              095A / notify   [Keroro].095.[Xvid.Mp3].[F93E9688].avi
```

Same CRC, same file. amuled picks the **most sourced** name as the parent, not the most
descriptive one, so dropping the alternatives is a coin flip on those 43.

The mapper therefore emits one `FileObservation` per name: the parent, then one per
`alternate_names[]` entry, each with that entry's own `sources` and the parent's hash and
size. This reproduces today's behaviour exactly.

### D5. `raw_meta` survives as unmapped JSON keys

The capture-all invariant ("we NEVER lose a metadata field, even an unknown one") does not
fall out of typed JSON on its own. The mapper keeps it explicitly: every key of the result
object that is not mapped to a structured field is rendered into `raw_meta` as a
`(key, value)` pair, same as the EC adapter did with unmapped tags.

### D6. `WEBUI_PWD` becomes `AMULE_API_PASSWORD`, breaking, no shim

Accepting both names for a release would mean carrying a fallback in
`docker/amule-config.py`, a second branch in its test script, and a deprecation notice
somewhere, for a variable an operator edits once in a `.env` file.

The break is already loud by construction: `amule-config.py`'s `required()` exits with
`AMULE_API_PASSWORD is required` before any service starts, so a node upgraded without
editing `.env` fails at boot with the name of the missing variable, rather than starting and
misbehaving. That is a better failure than a silent fallback.

This makes the release **3.0.0**, alongside the other breaking surface changes: port 4711
becomes 4713, and `amuleweb` is gone. `docs/migration-2x.md` is written for it, modelled on
`docs/migration-1x.md` (French, same shape: stop the node, edit, restart, with a rollback
section). Three steps only, since no data moves:

1. rename `WEBUI_PWD` to `AMULE_API_PASSWORD` in `.env`;
2. change the published port from `4711` to `4713`;
3. the aMule web UI is at a new address and is a different UI.

### D7. REST polling first, SSE later

The crawl loop keeps its current polling shape in this migration. SSE is a separate lot
(§8.3): it changes the loop's state machine and adds a long-lived connection to manage, for a
gain that is small at one node and one search every few minutes.

## 3. Lot 1: the image

Branch scope: `packages/crawler/Dockerfile`, `packages/crawler/docker/`, `deploy/`,
`tests/smoke/`, `docs/`.

### 3.1 aMule 3.1.0 in `amule.nix`

nixpkgs unstable is still at 3.0.1
(`pkgs/by-name/am/amule/package.nix`), so the derivation overrides `version` and `hash`
against the `3.1.0` tag of `amule-org/amule`.

Build deltas between 3.0.1 and 3.1.0, both small:

- the glib probe becomes `pkg_check_modules(GLIB QUIET glib-2.0 gio-2.0)`; `gio` ships with
  `glib`, which is already in `buildInputs`.
- `find_package(Python3 COMPONENTS Interpreter REQUIRED)` at configure time, so `python3`
  joins `nativeBuildInputs`.

CMake flags change: `BUILD_AMULEAPI=ON`, `BUILD_WEBSERVER=OFF`.

The `pname = "amule"` override stays: Syft builds the CPE from it and the NVD does not know
`amule-web-daemon`. Rename `amule-web-daemon` to whatever the new flag combination produces
only in the override, never in `pname`.

Exported binaries become `amuled`, `amuleapi`, `ed2k`.

**The one thing to verify before anything else is built on top of it** (it is D1's
condition): that amuleapi finds its `amuleapi-static` frontend folder unaided. It tries, in
order, the macOS bundle, the directory its own executable lives in, the path configured at
build time, and the platform shared-data directory. The third should resolve, because CMake
bakes the nix store path in, and it is the one that does not care that `/usr/local/bin/
amuleapi` is a symlink. If it does not resolve, `/` answers 404 while the REST API keeps
working, and setting `StaticRoot` means reintroducing `amuleapi.conf`: reopen D1 at that
point rather than quietly adding the file back.

amuled resolves the binary through `thePrefs::GetAmuleApiPath()`, so the closure's real store
path is the safer value to configure there, not the `/usr/local/bin` symlink.

### 3.2 Configuration

Per D1, there is no `amuleapi.conf`. `docker/amule-config.py` adds one section to the
`amule.conf` it already writes:

```ini
[AmuleApi]
Enabled=1
BindAddress=0.0.0.0
HttpPort=4713
```

`BindAddress=0.0.0.0` requires an admin password to be set first, or amuleapi refuses to
start. The one-shot therefore also runs `amuleapi --set-admin-pass="$WEBUI_PWD"` as the
`amule` user before s6 starts anything, reusing the variable `amuleweb` uses today for the
same purpose. `--set-admin-pass` writes `amuleapi-passwords` (mode 0600, in the config dir)
and exits. There is deliberately no `--password` option in amuleapi, because a password on a
command line is visible through `ps`.

No guest password: guest is enabled precisely by having one, and read-only access to this
surface exposes the daemon's filesystem paths, our `user_hash` and the raw log.

The crawler authenticates against amuleapi with that same admin password, so
`deploy/crawler.yml` gains a reference to it. `WEBUI_PWD` is now a misleading name for a
variable that also gates the crawler's own transport, so it is renamed to
`AMULE_API_PASSWORD`.

**This is a deliberate breaking change, with no compatibility shim** (D6).

### 3.3 s6

`docker/services.d/amuleweb/` is deleted and nothing replaces it: amuled brings amuleapi up.
The image goes from three supervised services to two (`amuled`, `mulewatch`).

### 3.4 Deployment surface

- `deploy/compose.yml`, `deploy/gluetun.compose.yml`, `deploy/base.compose.yml`: `4711:4711`
  becomes `4713:4713`, and `WEBUI_PWD` becomes `AMULE_API_PASSWORD` (D6).
- `deploy/.env.example`: same rename.
- `deploy/crawler.yml`: `amule_url` moves from `http://localhost:4711` to `:4713`, and the
  crawler's own amuleapi credential is wired in.
- `tests/smoke/compose.yaml` and the `compose_integration` suite: same port and variable,
  plus a probe of `GET /api/v1/health` (no auth, no EC roundtrip, answers while amuled is
  busy) as the readiness check.
- `docker/amule-config.test.sh`: the `[AmuleApi]` section and the renamed variable, including
  the **negative path** (the variable absent must abort the boot with its name, per the
  project's quality bar of proving a guard fails on bad input, not only that it passes).
- `docs/`: a new `migration-2x.md` (D6), plus `install.md`, `operate.md`, `settings.md`,
  `vpn.md`, `troubleshooting.md`, `troubleshooting-start.md`, `index.md`, `glossary.md`,
  `limits.md`, `contributing/architecture.md`, `contributing/testing.md`, which all name
  amuleweb, 4711 or `WEBUI_PWD`. French, as everything under `docs/` is. The nav gains the
  new page; `poe docs-build` runs `--strict`, so a stale link fails the build rather than
  shipping.

The confinement posture does not move: `no-new-privileges:true`, `pids_limit: 512`,
`mem_limit: 2g`, `setpriv` on each service. The process count is unchanged at three, but the
tree is one level deeper: s6 supervises `amuled` and `mulewatch`, and `amuled` owns
`amuleapi` (D1). `pids_limit: 512` has ample room; `no-new-privileges` is inherited by the
grandchild, so amuleapi is covered by it without anything being added.

`docs/limits.md` and the residual-risk paragraph in `AGENTS.md` need one word changed: a
compromise of any of the three processes still reaches the same bind mounts, and amuleapi is
now one of the three.

## 4. Lot 2: the adapter

Branch scope: `packages/crawler/src/mulewatch/adapters/mule_api/` and the deletion of
`adapters/mule_ec/` plus the two probe tools.

Lots 1 and 2 must reach `main` together (removing amuleweb without the adapter leaves the
crawler talking EC to a daemon that no longer has a web UI), but they are two PRs.

### 4.1 Shape

```
adapters/mule_api/
    client.py      AmuleApiClient: implements MuleClient + MuleDownloadClient structurally
    auth.py        login, token lifetime, the 401 rule (§7.3)
    mapping.py     JSON result objects -> FileObservation (incl. alternate_names flattening)
    errors.py      HTTP status + error.code -> the ports' error contract
```

The ports keep their current contracts, including `MuleClient` having no notion of a
`search_id`: the adapter holds the id of the single in-flight search per instance, exactly
reproducing today's one-search-at-a-time behaviour. Multi-search is lot 3.

The error contract maps as follows (`ports/mule_client.py`):

| Condition | Raise |
|---|---|
| connection refused, timeout, `503 ec_unavailable` | `MuleUnreachableError` |
| `400 amuled_rejected` on a search op | `MuleSearchFailedError` |
| `401` after a re-login attempt | `MuleUnreachableError` |
| `429 rate_limited` | `MuleUnreachableError`, honour `Retry-After` |

`503 ec_unavailable` is a **new degradation state**: amuleapi is up but its link to amuled is
down. `GET /api/v1/health` distinguishes the two (`ec_connected`, `snapshot_ready`) and is
worth an explicit metric, because today "the daemon is unreachable" is one state and it
becomes two.

### 4.2 Media metadata

`FileObservation.media_length_sec`, `.bitrate_kbps` and `.codec` have existed since
`catalog/0001_initial.sql:23-25` and have always been `None`: `mapping.py`'s own docstring
records that no media tag transits over EC. They now fill from the result's `media` object:

```json
"media": { "duration_seconds": 5400, "bitrate_kilobits_per_second": 1500,
           "codec": "H.264", "artist": "", "album": "", "title": "" }
```

`media` is `null` for most global and Kad hits, and the key is always present, so test for
`null` rather than for the key. When present it is **what the responding server advertised**,
not a local probe: upstream's own documentation notes that a `.pdf` reporting a runtime and a
video codec is a real observed result. That matches what `FileObservation` already documents
("self-declared, unreliable metadata"). `artist`, `album` and `title` have no column today
and go to `raw_meta`.

### 4.3 Queue and completion

- `download_queue()` calls `GET /downloads?status=all`. The default is `status=active`, which
  **excludes completed entries**; the disk-cap admission rule sums `remaining_bytes` over the
  queue and would under-count without `all`.
- `shared_files()` calls `GET /shared` and keeps only `hash`, as `SharedFileEntry` already
  does. Completion stays the positive signal it is today: the file appearing in the shared
  list. No byte is read, nothing touches the output directory.
- `add_link()` calls `POST /downloads`. `POST /search/results/{hash}/download` would let us
  skip building an ed2k link entirely, but it only works while the search that produced the
  hit is still live in amuled's 20-entry ring, which our loop does not guarantee. Keep the
  link path; `catalog_matching/ed2k_link.py` stays.

### 4.4 Tests

The `ec_integration` and `download_integration` markers are rewritten against HTTP. Per
`agents/handoffs/` and prior sessions, those suites do not run in this environment (testcontainers
networking), so they stay on-demand and out of the gate, like today. The unit suites keep
100 % branch coverage per package: a JSON mapper is considerably easier to cover exhaustively
than a binary codec, so this should be a net simplification rather than a cost.

## 5. Out of scope, and why

- **EC AEAD encryption** (`EC_FLAG_ENCRYPTED`, X25519 + AES-128-GCM). amuleapi's own
  `[EC]/Encryption=1` default handles the amuleapi-to-amuled link for free. Both ends are on
  loopback inside one container, so there is nothing further to buy.
- **ip2country / `EC_TAG_CLIENT_COUNTRY` / `GET /geoip`**. Contradicts the invariant that the
  catalog's subject is the file, never the person.
- **Local ffprobe extraction** (`files.media_metadata_enabled`,
  `POST /shared/media/refresh`). It would have amuled read the downloaded bytes and would put
  ffmpeg in the closure, to learn something we already know once the file is in hand. It also
  brushes against the "never infer from the bytes" invariant. Leave it off.
- **Chat, friends, client history, UPnP, proxy, IP filter.** Not our problem.
- **Re-adding RE2.** Still explicitly out of scope
  (`agents/specs/2026-09-16-single-container-embedded-amule.md` §14/§15).
- **`GET /shared/{hash}/content`.** amuleapi can stream the file's bytes. We never will.

## 6. Endpoint mapping

| `AmuleEcClient` | amuleapi v1 |
|---|---|
| `connect` | `POST /auth/login` (`?include_token=true`) |
| `close` | `POST /auth/logout` |
| `start_search(keyword, channel)` | `POST /search` `{query, type: global\|kad}` -> `202` + `search_id` |
| `fetch_results` | `GET /search/{id}/results` -> `results[]` + `progress` |
| `search_progress` | same response, `progress.state` / `progress.percent` |
| `stop_search` | `POST /search/{id}/stop` (keeps results) |
| `network_status` | `GET /status` |
| `get_listen_port` | `GET /preferences` -> `connection.tcp_port` |
| `set_listen_port` | `PATCH /preferences` `{connection: {tcp_port}}` |
| `add_link` | `POST /downloads` |
| `download_queue` | `GET /downloads?status=all` |
| `shared_files` | `GET /shared` |

`NetworkStatus` maps from `GET /status`: `ed2k.user_id` -> `ed2k_id`, `ed2k.high_id` ->
`ed2k_high`, `kad.state` + `kad.firewalled_tcp` -> `KadStatus`, `ed2k.server_name` /
`server_ip:server_port` -> `server_name` / `server_addr`. Read `high_id` **together with**
`ed2k.state`: `false` means LowID only once `state == "connected"`, and "no id yet"
otherwise.

## 7. Pitfalls

### 7.1 `media` is a hint, never a measurement

See §4.2. It is server-advertised and can contradict the file. This is why lot 4 (matching
rules on duration) waits for a measured fill rate rather than shipping with lots 1 and 2.

### 7.2 amuled keeps only 20 searches

A search evicted from amuled's ring reads `404`. Irrelevant at one search at a time; a
scheduler constraint the moment lot 3 lands.

### 7.3 A `401` is terminal for the session

A client that retries a `401` burns the generic limiter (30 rejected tokens per 60 s, then a
300 s lockout keyed by IP) and locks itself out. Restarting amuleapi invalidates every issued
token, and so does any password change. The adapter re-logs in **once** on a `401`, with
backoff, and never loops. Only `POST /auth/login` clears that state.

### 7.4 One more hop

amuleapi down means the crawler is blind even when amuled is fine. `GET /health` answers
without touching EC, which is what lets the two failures be told apart. Per boundary
discipline (E-D13) both degrade rather than crash.

### 7.5 `EC_FLAG_UNKNOWN_MASK` moved

3.1.0 changed it from `0xff7f7f08` to `0xff7f7f00`, bit `0x08` becoming `EC_FLAG_ENCRYPTED`.
Only relevant to the EC adapter, which this spec deletes, and harmless in the interim: we
never negotiate `EC_TAG_CAN_AEAD`, so amuled never sets the bit. Recorded so a future reader
of `agents/reference/ec-protocol.md` knows that document stopped at 3.0.0.

## 8. Follow-up lots

These are tracked here so a later session can pick them up without re-deriving the analysis.
None of them belongs in lots 1 and 2.

### 8.1 Lot 3: capabilities the API opens up

- **Multi-search.** Our EC client has no notion of a `search_id`: one search at a time.
  amuleapi runs several concurrently, and a global and a Kad search never disturb each other.
  `GET /search` enumerates everything amuled holds, including searches another client
  started. This is a throughput change for the coverage loop, not a convenience, and it
  changes the scheduler's core. Watch the 20-entry ring (§7.2).
- **`POST /search/{id}/more`**, the desktop's "More" button: re-asks the Kad clients already
  queried for a wider frontier. Kad-only, running-only. `409 kad_more_exhausted` is the
  terminal answer (Kad allows at most 4 reasks and refuses once the search enters its
  stopping window, roughly the second half of its 45 s life); a `202` promises nothing was
  terminal, not that a reask went out. A daemon predating the feature always answers `202`,
  so absent means unknown, never exhausted.
- **Disk space.** `GET /status` carries `disk.temp_free_bytes` and `disk.incoming_free_bytes`,
  `null` when the daemon has no figure (never `0`, which would read as a full disk). There is
  already a `ports/disk_space.py`.

### 8.2 Lot 4: matching rules on duration

`FileCandidate.duration_sec` and `.bitrate_kbps` exist, `AttrBetweenMatcher` exists, and
`deploy/matcher.yml` uses neither, because the data never arrived. Once lot 2 is on the real
node, measure the fill rate of `media` across the catalog, then write `attr_between` rules. A
24-minute runtime is a strong discriminator against a film or a compilation, but only if the
field is populated often enough to be worth a rule. Do not write the rules before the
measurement.

### 8.3 Lot 5: SSE

`GET /api/v1/events` streams typed frames on filterable channels. What is actually worth
having, in order:

- **`shared_added`**: our completion signal, pushed, instead of polling the whole shared list.
  This is the only one that replaces a dumb poll with an exact signal, and it is worth doing
  on its own even if the rest never happens.
- `search_progress`: its terminal frame (`state: "finished"`) is the authoritative end of a
  search. There is no separate `search_finished` event.
- `search_result_added` / `_updated`: results as they land. `_updated` deliberately does not
  fire on `sources` or `alternate_names[]` (too noisy); `search_progress` is the cue to
  re-read the results endpoint for those.
- `download_updated`: live progress without polling.

Cost: a long-lived connection with reconnect, `Last-Event-ID` and `resync` frames to handle,
and §7.3's `401` rule applies to the stream too. At one node with a search every few minutes,
the polling shape is adequate, which is why this is deferred rather than folded in.

### 8.4 Lot 6: union of names per hash in the decision layer

A bug this migration's analysis uncovered, independent of the migration itself.

`application/decisions.py:39` (`record_decision_if_changed`) evaluates one candidate and
**retracts every target absent from that evaluation**. Since each filename of a hash arrives
as its own observation (§2 D4), the names of a multi-name hash fight each other on every
cycle. Measured on the production catalog:

```
hash 08fd5637... / target 095A : 309 decision rows, 154 of them retractions
```

A perfect alternation. Across 7205 rows of `match_decisions`, several thousand are this
flapping: `088A` 442 rows / 221 retractions, `001A` 366/180, `100A` 335/167, `091B` 285/142.
By contrast `062A` has 837 rows and zero retractions, because it only ever had one name.

The fix is neither to drop the alternates nor to keep the current behaviour: a decision is
about the **file**, so the engine should evaluate the union of the names known for a hash and
retract a target only when no name matches it. In shape that is passing a list of candidates
where one is passed today. It touches the append-only decision semantics and the
`DecisionSignal` path, so it gets its own branch, its own tests and its own handoff.

## 9. Not validated against real hardware

- The nix override of `version` and `hash` to 3.1.0 has not been built. The two build deltas
  (§3.1) are read from the CMake diff, not from a successful build.
- **Whether `amuleapi-static` is found automatically** (§3.1). Inferred from upstream's
  documented lookup order against our nix closure, not observed. This is D1's condition, so
  it is the first thing to check once the image builds.
- `amuleapi --set-admin-pass` running as the `amule` user from the root one-shot, and the
  resulting `amuleapi-passwords` file mode, have not been exercised.
- amuleapi's console output going into a pipe amuled never drains (D1) is read from the
  source, and the conclusion that it cannot realistically fill 64 KiB rests on counting
  console write sites in `src/webapi/`, not on watching a node run for a month. If a node
  ever wedges with amuleapi alive but unresponsive, this is the first hypothesis.
- That the ephemeral EC token handoff works when amuled and amuleapi run as the same
  unprivileged `amule` user inside the container, with the config dir on a bind mount.
- `no-new-privileges` alongside `setpriv` remains unvalidated on real hardware from the
  single-container work (`agents/specs/2026-09-16-single-container-embedded-amule.md` §13);
  swapping amuleweb for amuleapi does not change that, but it does not clear it
  either.
- The fill rate of `media` on real Keroro searches is unknown. §8.2 exists because of that.

## 10. Open question for the operator

None blocking. D1 through D7 are settled; approve or amend them and lot 1 can start.
