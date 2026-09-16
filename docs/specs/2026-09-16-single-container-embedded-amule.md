# Single container with an embedded aMule

- Date: 2026-09-16
- Status: APPROVED (operator sign-off 2026-09-16, discussion phase)
- Scope: collapse the crawler and amuled into one image, one compose service, one process tree
- Release: `v2.0.0` — breaking, with a manual migration of the live node
- Related: `packages/crawler/Dockerfile`, `deploy/**`, `security/**`, `packages/vex_guards/`,
  `docs/runbooks/**`, AGENTS.md invariants

## 1. Goal

Deploying a mulewatch node currently means two images, two to four compose services, a Docker
socket proxy, and a layout split between named volumes nobody can read. The crawler is useless
without amuled and amuled is pointless without the crawler. Collapsing them removes a whole class
of deployment mistakes.

The result is **one container without a VPN, two with gluetun**.

This knowingly breaks "one container, one process". That rule buys independent lifecycles and
independent scaling. We have neither: the two processes are born and die together. What it costs
us is the thing we actually want — a node an operator can start, read and debug without a diagram.

A second goal came out of the design discussion and is now load-bearing: **the SBOM must stop
lying.** Today amuled is a third-party image we neither scan, attest nor sign. Section 8 explains
why the chosen strategy is the only one that fixes that instead of relabelling it.

## 2. Strategy: our own image, aMule from nixpkgs

We build the image ourselves. aMule comes from **nixpkgs**, which packages 3.0.1 from the upstream
GitHub tag, with build knobs for exactly what we need.

Three routes were examined and two rejected:

- **Build aMule from source by hand** (CMake, wxWidgets, ffmpeg in our Dockerfile). Long, and we
  would own the whole dependency graph with one maintainer.
- **Base on `ngosang/amule:3.0.1-2`.** Shortest path, and its config generation and s6 layer come
  free. Rejected because aMule, wxWidgets and ffmpeg are source-built there: no package metadata,
  so Syft cannot see them, so Grype never scans them. That is a permanent blind spot on the one
  process that parses hostile input from strangers.
- **nixpkgs.** Chosen. Every component carries a name and a version, so the SBOM is complete.

No distribution ships aMule 3.0.x as a binary package (Debian trixie and sid are on 2.3.3, Alpine
has none), but nixpkgs builds it from source with pinned, cached dependencies — which is the
difference between packaging aMule ourselves and letting someone else's expression do it.

### Why 3.0.1 and not 3.1

Upstream aMule has no `3.1.0` tag; its latest release is `3.0.1` (2026-06-24). `amuleapi` — the
modern Web UI plus the REST API — is real upstream code in `master`, but unreleased. We do not
need the REST API: we speak EC, which is richer and already implemented. aMule 3.0.1 carries the
WebUI modernization pass (HTML5, CSS rework, XSS hardening), which is what was missing from 3.0.0.

**When bumping to 3.1, re-check the password handling.** In `master` the EC and web passwords
moved to a separate store and are no longer read from `amule.conf`. Section 7 depends on 3.0.1
behaviour.

### The build, measured

The pinned expression (nixpkgs `c7def046b9a883d46974757852106483d741586f`):

```nix
wxBase = (wxwidgets_3_2.override { withWebKit = false; withMesa = false; }).overrideAttrs (old: {
  # --disable-gui is not an exposed option. --enable-mediactrl is unconditional upstream
  # and is what drags GStreamer in, so it goes with the GUI, not alongside it.
  configureFlags =
    (builtins.filter (f: f != "--enable-mediactrl" && f != "--with-nanosvg") old.configureFlags)
    ++ [ "--disable-gui" ];
  buildInputs = [ zlib pcre2 expat curl ];
});

amule' = (amule.override {
  monolithic = false; enableDaemon = true; httpServer = true; wxwidgets_3_2 = wxBase;
}).overrideAttrs (_: { pname = "amule"; });   # see section 8 — the pname is load-bearing
```

Built on both architectures in CI (run `35135831426`):

| | closure | build |
|---|---|---|
| `nixpkgs#amule-daemon`, straight from the binary cache | 922 MB (amd64) / 975 MB (arm64) | fetched |
| without webkit and mesa | 634 MB | 13 min |
| **wxBase, no GUI** | **101 MB** | **2 min 14 s** (wxBase itself: 98 s) |

89% off the baseline. The whole job runs in 4 min 35 s (amd64) and 3 min 28 s (arm64). It produces
`amuled`, `amuleweb` and `ed2k`. A closure check for `gtk`, `gst`, `gspell`, `cups`, `at-spi`,
`webkit`, `python3` and `perl` comes back empty: GStreamer, the spell checkers, printing and
accessibility are gone with the GUI.

The compile happens once per nixpkgs bump, not per CI run.

## 3. Python 3.13, installed by apt

The runtime base is `debian:trixie-slim`. Debian trixie has no `python3.14` package (`python3` is
3.13.5; 3.14 only appears in forky/sid), and pulling Python from nix would mean shipping a second,
much larger Python for no gain — Debian's is dpkg-managed and covered by Debian's own security
feed, which matches better than the CPE route section 8 describes.

So the node moves to **Python 3.13, `apt-get install python3 python3-venv`**.

This was measured, not assumed. Against the snapshot at `329f905`, with `requires-python` relaxed
to `>=3.13`:

- no 3.14-only construct anywhere in the sources;
- every dependency resolves on 3.13;
- matching 255 passed / 100% branch, vex_guards 74 passed / 100% branch, crawler 1007 passed;
- the single crawler failure is `test_connect_refused_raises_connect_error`, whose own comment
  says "deterministic RST (Linux)". It fails identically under 3.14 on macOS. Platform, not
  version.

Consequence: `requires-python = ">=3.13"` in the three packages, `python_version = "3.13"` for
mypy, and the AGENTS.md hard rule becomes "Python only (>=3.13)". When trixie's successor lands,
`python3` becomes 3.14 with no work on our side.

## 4. Process supervision

We now own the whole supervision layer. **s6 comes from Debian's `s6` package** — not s6-overlay,
whose release tarball is not dpkg-managed and would open a small blind spot of exactly the kind
section 8 exists to close. `s6-svscan` runs as PID 1 via our entrypoint.

Three services under `/etc/services.d/`: `amuled`, `amuleweb`, `mulewatch`. Each `run` executes as
root, then drops privileges with `setpriv --reuid --regid --init-groups`.

`mulewatch`'s `run` additionally:

1. grants the amule group control over amuled — `s6-svperms -G <group> /etc/services.d/amuled`.
   Without it the control FIFO stays root-owned, mode 0600, and mulewatch cannot restart amuled.
2. `chown`s the data directory to `PUID:PGID`.

Known race: `s6-svperms` needs `/etc/services.d/amuled/supervise/` to exist, which happens only
once s6-supervise has started amuled. `run` must wait for that path with a bounded retry loop
rather than assume ordering.

**`finish`** receives the exit code and decides the container's fate:

| exit code | action | why |
|---|---|---|
| `0` | nothing; s6 restarts mulewatch alone | this is `/controls/restart`. amuled keeps its eD2k/Kad sessions. |
| non-zero | `s6-svscanctl -t /etc/services.d` | a crash — invalid config above all — must kill the container, so `restart: unless-stopped` produces a visible backoff loop instead of a silent crashloop |

amuled and amuleweb stay supervised normally: they crash, s6 restarts them, the crawler's EC
backoff absorbs the gap and the observability pipeline reports it.

`docker stop` needs no work: s6-svscan's default SIGTERM behaviour stops every service, waits for
the tree, then runs `finish`.

The "daemon unreachable at startup is tolerated" logic in `composition/app.py` becomes **more**
load-bearing, not less: all three processes start at the same instant, so mulewatch will routinely
reach EC before amuled is listening. Keep it.

## 5. Image layout

Three stages.

**aMule stage** — runs `nix build` on the pinned expression of section 2 and exports the runtime
closure. `nix` itself never reaches the final image; only the store paths do. Syft reads store
path names, so a plain `COPY` of those paths is enough (verified — see section 8).

**Python stage** — `debian:trixie-slim` + `python3` + `python3-venv` + the uv binary, producing
`/app/.venv` from `uv.lock` exactly as today (deps layer first, then the workspace member).

**Runtime** — `debian:trixie-slim`, plus:

- `apt-get install -y --no-install-recommends python3 s6` (their dependencies pull `libsqlite3-0`,
  `libssl3` and the rest — do not hand-list them);
- `COPY` of the aMule closure store paths;
- `COPY --from=<python stage> /app/.venv /app/.venv`;
- `ENV PATH="/app/.venv/bin:$PATH"` — required so the documented
  `docker compose run --rm --entrypoint python …` diagnostic recipe keeps working;
- our entrypoint, the three `/etc/services.d/*` units, and the config script of section 7;
- `LABEL org.opencontainers.image.version` from the `VERSION` build-arg, unchanged.

## 6. Configuration

The container embeds exactly one amuled at `127.0.0.1:4712`. The multi-daemon pool can never be
exercised, so it goes.

**Removed from `crawler.yml`**: the whole `amules:` list, `download.endpoint`, and
`port_sync.restarter_url`.

**Added**: a single `amule_ec_password: ${AMULE_EC_PASSWORD}`, resolved by the config adapter as
today, so the domain still never touches the environment.

**Host and port become code constants** (`127.0.0.1`, `4712`). Precedent: AGENTS.md already fixes
the webui bind at `0.0.0.0:8080` in code and lets YAML only gate the surface.

`AmuleEndpoint` survives as a code-level dataclass — the integration suites build it in Python
from environment variables (`tests/integration/conftest.py`), never from YAML, so they are
unaffected. Only its YAML surface disappears.

Other value changes: `download.output_dir: /downloads`, `port_sync.gluetun_control_url:
http://localhost:8000`.

**Observability**: the `instance` field on the two events that carry it, and its Prometheus label,
are removed. With one daemon the label is constant, and a breaking release is the free moment to
drop it.

## 7. aMule's own configuration

This is the work we take on by not using a third-party image. It is smaller than it looks: one
oneshot script, run before the services start.

It must create the `amule` user from `PUID`/`PGID`, `chown` the three bind-mounted directories,
and write `amule.conf` **if absent**. Nothing else — no auto-restart cron, no auto-share, no
remote-GUI config, no random password generation (the compose file requires the variables).

aMule is built on wxConfig, so a key that is absent takes its declared default. Verified against
the 3.0.1 tag: every setting is registered with a default and read through `Read(key, &value,
default)`. So the file needs **four keys**, not a full config:

| key (section / name) | default in 3.0.1 | write it? |
|---|---|---|
| `ExternalConnect / ECPort` | **4712** | no — already our value |
| `ExternalConnect / AcceptExternalConnections` | `false` | yes |
| `ExternalConnect / ECPassword` | empty | yes, as an MD5 hex digest |
| `eMule / IncomingDir` | under the home dir | yes → `/downloads/incoming` |
| `eMule / TempDir` | under the home dir | yes → `/downloads/temp` |

`ECPassword` is a `Cfg_Str_Encrypted` field, but it only hashes on input from the GUI; on load it
reads the string as-is. So the value on disk must already be the digest — a `printf | md5sum`.

aMule rewrites the complete file itself on its first save (`SaveAllItems`), so our minimal file
becomes a normal one on its own.

The download paths are written explicitly rather than mounting the host directories onto aMule's
defaults. Mounting onto the defaults would work and save two keys, but those defaults sit inside
`/home/amule/.aMule`, which is itself a bind mount — the operator would see `deploy/amule/`
holding empty directories shadowed by other mounts. Two generated lines are cheaper than that
confusion.

## 8. Supply chain

This is the section the strategy was chosen for. Everything below was measured with Syft 1.51.1
and Grype 0.118.0 against a database built 2026-09-16.

**Nix packages are visible without the nix database.** A rootfs holding only store paths yields
12/12 components, each with a `pkg:nix/...` purl and a CPE. A plain `COPY` of the closure is
therefore enough; no `nix copy`, no database. (A variant carrying a hand-made database returned
zero, but that is an artefact of the test harness — its absolute paths cannot match a relocated
scan root. Inconclusive, so: copy the paths, and if a database is ever shipped, verify detection
instead of assuming it.)

**Grype matches nix packages.** A control run on deliberately old versions returned 16 findings
(libpng 11, zlib 2, **amule 2**, boost 1). Matching goes through CPEs against the NVD, not a
distribution advisory feed, so expect more noise than dpkg gives — libpng 1.6.37 alone produced 11
hits. Current versions return zero findings because they are clean, which is the correct result.

**The package name is load-bearing.** Syft derives the CPE from the package name, and a renamed
package looks up a name the NVD does not have — no error, just zero findings. Measured on the same
vulnerable version:

```
amule            2.3.1  ->  cpe:...:amule:amule:2.3.1           ->  2 findings
amule-web-daemon 2.3.1  ->  cpe:...:amule-web-daemon:...:2.3.1  ->  0 findings
```

Our override renames the derivation to `amule-web-daemon`, which is why section 2 forces `pname`
back to `amule`. Confirmed in the real closure built in CI: `amule 3.0.1`,
`pkg:nix/amule@3.0.1`, `cpe:2.3:a:amule:amule:3.0.1`. Nothing else in the nixpkgs expression
derives from `pname` (only `mainProgram`, a separate parameter, and a darwin-only `postInstall`),
so the override is safe.

**Every component whose name we alter must be re-checked the same way.** This is a standing rule,
not a one-off fix.

What this replaces: with the ngosang base, aMule, wxWidgets and ffmpeg would have been absent from
the SBOM entirely, and the honest response would have been to declare the SBOM incomplete. That is
no longer needed — the closure enumerates itself.

Kept unchanged: cosign signature, the three attestations, the daily Grype scan, `vex_guards`.

Consequences for the existing claims:

- `CVE-2026-12003` names Alpine in its own impact statement — remove it with its guard.
- `CVE-2025-60876` is about the busybox wget applet, which no longer exists — `check_stale_claims`
  flags it once Grype stops reporting it. Remove it with its guard.
- The six remaining claims are about our own source, verified by `check_source_claims` against our
  tree, and survive the base change untouched.
- `vex_guards.sbom.load_apk_packages` must read dpkg packages instead of apk. No image guard
  currently exists, so this is an unused path being repointed.

The VEX product purl follows the rename to `pkg:oci/mulewatch`.

## 9. Deployment

`deploy/config/crawler/` is dismantled; the operator's files sit next to the compose file.

```
deploy/
  compose.yml              direct stack
  gluetun.compose.yml      VPN stack
  base.compose.yml         shared service fragment (include:)
  .env
  crawler.yml
  targets.yml
  matcher.yml
  amule/                   -> /home/amule/.aMule   (amule.conf, editable)
  data/                    -> /data               (catalog.db, local.db)
  downloads/incoming/      -> /downloads/incoming
  downloads/temp/          -> /downloads/temp
```

Everything is a relative bind mount. Named volumes are gone: an operator must be able to run
`sqlite3 deploy/data/catalog.db` from the host without `docker cp`. `PUID`/`PGID` are surfaced in
`.env.example` — they are what keeps those directories readable without `sudo`.

`base.compose.yml` keeps the shared `mulewatch` service definition but must **not** declare
`ports:` or networking; compose merges `ports` additively and cannot remove them. Each stack adds
its own:

- `compose.yml` publishes 8080, 4711 and the eD2k ports on a default network.
- `gluetun.compose.yml` gives mulewatch `network_mode: service:gluetun` and publishes 8080 and
  4711 **on the gluetun service**.

**Deleted**: the `docker-proxy` service, the `ec` and `egress` networks, every named volume.
Service count: 4 → 2 with a VPN, 2 → 1 without.

### Container hardening

The merged container runs a root PID 1 that creates users and chowns directories, so `user:`,
`read_only:` and `cap_drop: ALL` cannot survive. This reverses the crawler half of the 2026-06-17
posture decision, deliberately and with operator sign-off: the crawler descends to amuled's
confinement level rather than amuled rising to the crawler's.

Kept: `security_opt: no-new-privileges:true` (`setpriv` only drops privileges, so this should
hold — **to validate on real hardware**), `pids_limit: 512` and `mem_limit: 2g`, both raised
because one container now holds three processes, and both to be tuned on the live node.

### Health

```yaml
healthcheck:
  test: ["CMD-SHELL", 'test "$(s6-svstat -u /etc/services.d/amuled)" = true']
```

Note the pitfall: `s6-svstat` exits 0 even for a stopped service (it prints `false`; exit 1 means
s6-supervise itself is not running), so the output must be tested, not the exit code.

Probing amuled only is deliberate — it is the process nothing else covers, and it is the one probe
that works when `webui.enabled: false`, unlike today's HTTP check. Probing mulewatch would be
near-tautological now that a crash kills the container.

### Two web surfaces

Both are published by default: mulewatch on 8080, amuleweb on 4711.

amuleweb cannot run without a password, so authentication cannot be delegated entirely to a
reverse proxy. mulewatch's own UI keeps its existing posture — **no built-in auth** — and
`.env.example` plus the runbook must state plainly that `WEBUI_PWD` protects 4711 only, and that
8080 exposes the catalog, the crawl controls and a read-only SQL console to anyone who can reach
it. `WEBUI_PWD` gets a `:?` guard like `AMULE_EC_PASSWORD`.

The mulewatch navigation gains a link to the aMule UI, with a configurable base so it survives a
reverse proxy in front of 8080.

### Port sync

`HttpMuleRestarter` is deleted along with the Docker API surface. `S6MuleRestarter` replaces it
and runs `s6-svc -r /etc/services.d/amuled`. Use the documented `s6-svc` command rather than
writing the control FIFO directly: the byte-level protocol is an internal detail not worth
depending on.

This is also more correct than what it replaces. The port was never re-bindable at runtime, so
port-sync needed a *process* restart; it was only ever restarting a *container* because the process
was out of reach.

## 10. Renaming

| | from | to |
|---|---|---|
| Image | `ghcr.io/geoffreycoulaud/mulewatch-crawler` | `ghcr.io/geoffreycoulaud/mulewatch` |
| Compose service | `crawler` | `mulewatch` |
| VEX product | `pkg:oci/mulewatch-crawler` | `pkg:oci/mulewatch` |

The artifact is no longer "the crawler", it is the whole node. The Python package `mulewatch` and
`python -m mulewatch` do not move: they still name the crawler, which remains one process of three.

**Do not delete the `mulewatch-crawler` GHCR package.** It stays at 1.x and is the rollback path
while the live node migrates.

## 11. Live node migration

Breaking, done by hand once, no compatibility code. The procedure belongs in the handoff and the
deployment runbook:

1. `docker compose down`
2. Copy each named volume out to its new bind mount (`docker cp` from a throwaway container):
   `catalog-db` and `local-db` → `deploy/data/`, `amule-state` → `deploy/amule/`.
3. Move `deploy/config/crawler/*.yml` to `deploy/`.
4. Edit `crawler.yml`: drop `amules:`, `download.endpoint`, `port_sync.restarter_url`; add
   `amule_ec_password`; set `output_dir: /downloads` and the gluetun control URL to localhost.
5. Add `PUID`/`PGID` and `WEBUI_PWD` to `.env`, then `chown` the bind mounts to that uid.
6. `docker compose up -d` against the new `compose.yml`.

The existing `amule.conf` is carried over and is **not** rewritten by the new config script, which
only writes the file when absent. Check that its `IncomingDir` and `TempDir` point at
`/downloads/incoming` and `/downloads/temp`.

Rollback: repoint `IMAGE_TAG` at the 1.x `mulewatch-crawler` image and restore the old compose
file. The named volumes are not destroyed by this procedure.

## 12. Work packages

Lot 1 lands first — it touches every package. Lots 2 to 5 are independent of each other. Lot 6
needs 2 and 4. Lot 7 is last.

| Lot | Content | Depends on |
|---|---|---|
| **1. Python 3.13** | three `requires-python`, mypy `python_version`, `uv.lock`, the AGENTS.md hard rule | — |
| **2. Image** | the pinned nix expression, the three-stage Dockerfile, the entrypoint, `/etc/services.d/{amuled,amuleweb,mulewatch}`, the `s6-svperms` wait loop, the `amule.conf` oneshot of section 7 | 1 |
| **3. Crawler code** | delete the `amules` pool and `download.endpoint` parsing, EC constants, `amule_ec_password`, `S6MuleRestarter`, delete `HttpMuleRestarter` and `restarter_url`, drop the `instance` field and label | 1 |
| **4. Deploy** | the three compose files, `.env.example`, config files moved to `deploy/` root, the matching golden corpus `parents[N]` paths, AGENTS.md's `deploy/config/` invariant | 1 |
| **5. Supply chain** | remove two VEX claims and their guards, `load_apk_packages` → dpkg, the nix cataloger in the release SBOM, the `pname` rule of section 8 written down where it will be read | 1 |
| **6. Tests** | `tests/smoke/compose.yaml` down to one service, `test_compose_smoke.py` (service name, no docker-proxy) | 2, 4 |
| **7. Docs** | runbooks (deployment, administration, troubleshooting), `docs/README.md`, `architecture.md`, the migration procedure, the RE2 note in section 14, the handoff | 2–6 |

Every lot keeps the gate green on its own: `uv run poe check`, 100% branch coverage per package.

## 13. Not validated against real hardware

- gluetun firewall behaviour for ports published on the gluetun service — sources disagree on
  whether `FIREWALL_INPUT_PORTS` is needed for ports reached from the LAN.
- `no-new-privileges:true` alongside `setpriv` in the merged container.
- `pids_limit` and `mem_limit` values.
- The `s6-svperms` timing against a cold container start.
- How `amuleweb` 3.0.1 takes its password — `-A` on the command line, or a config file of its own.
  Section 7 covers `amule.conf` only.
- Whether amuleweb 3.0.1 behaves as expected where 3.0.0 did not.
- The final image size. Only the aMule closure (101 MB) was measured; the Debian base, Python and
  our venv are on top of it.

## 14. Recorded, not acted on

The 2026-07-03 decision to drop `google-re2` rested explicitly on the absence of musllinux wheels,
which blocked Alpine. **Moving to Debian removes that blocker.** We are not reverting: stdlib `re`
works, the tests pass, the ReDoS risk was accepted, and `matcher.yml` stays operator-owned. But the
AGENTS.md note must be amended, or it will keep justifying a choice with a constraint that no
longer exists.

## 15. Out of scope

- Re-adding RE2 (see 14).
- `amuleapi` and the REST API (see 2).
- Authentication on the mulewatch web UI.
- Trimming the aMule closure further. curl and its transitive tree (krb5, ngtcp2, libssh2,
  openssl, zstd) is now the largest contributor; at 101 MB it is not worth chasing.
- Deleting downloaded files; the free-space floor still only refuses new work.
- The multi-node story: N nodes stay N containers, merged by `mulewatch.merge`.
