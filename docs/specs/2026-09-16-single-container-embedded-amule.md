# Single container with an embedded aMule

- Date: 2026-09-16
- Status: APPROVED (operator sign-off 2026-09-16, discussion phase)
- Scope: collapse the crawler and amuled into one image, one compose service, one process tree
- Release: `v2.0.0` — breaking, with a manual migration of the live node
- Related: `packages/crawler/Dockerfile`, `deploy/**`, `security/**`, `packages/vex_guards/`,
  `docs/runbooks/**`, AGENTS.md invariants

## 1. Goal

Deploying a mulewatch node currently means two images, two to four compose services, a Docker
socket proxy, and a bind-mount layout split between named volumes nobody can read. The crawler is
useless without amuled and amuled is pointless without the crawler. Collapsing them removes a
whole class of deployment mistakes.

The result is **one container without a VPN, two with gluetun**.

This knowingly breaks "one container, one process". That rule buys independent lifecycles and
independent scaling. We have neither: the two processes are born and die together. What it costs
us is the thing we actually want, which is a node an operator can start, read and debug without
a diagram.

## 2. Strategy: build on ngosang's image

The image is `FROM ngosang/amule:3.0.1-2`, pinned by digest.

Why not build aMule ourselves: no distribution packages aMule 3.0.x (Debian trixie and sid ship
2.3.3, Alpine ships nothing). Building it means CMake, wxWidgets and ffmpeg from source, a
Dockerfile three times longer, and owning that CVE surface with one maintainer.

Why this tag and not `3.1.0-1`: upstream aMule has no `3.1.0` tag. Its latest release is `3.0.1`
(2026-06-24). `amuleapi` — the modern Web UI plus the REST API — is real upstream code living in
`master`, but unreleased; ngosang's `3.1.0-1` is marked *upcoming* and would build from an
untagged branch under a version number that does not exist. We do not need the REST API: we speak
EC, which is richer and already implemented. Moving to `3.1.0-1` later is a one-line `FROM` bump.

What `3.0.1-2` gives us: aMule 3.0.1, which carried the WebUI modernization pass (HTML5, CSS
rework, XSS hardening). This is what was missing from `3.0.0-1`.

Facts about that image, verified against the tag:

| | |
|---|---|
| Runtime base | `debian:trixie-slim` |
| Supervisor | plain s6 (apt `s6`), **not** s6-overlay; `/etc/services.d/<svc>/{run,finish}` |
| Entrypoint | `/home/amule/entrypoint.sh`, ending in `exec /usr/bin/s6-svscan /etc/services.d` |
| PID 1 | root; each service drops privileges with `setpriv` |
| Web UI | amuleweb on 4711, `WEBUI_PWD`, optional `WEBUI_GUEST_PWD` |
| Paths | `/downloads/incoming`, `/downloads/temp`, config in `/home/amule/.aMule` |
| Variables | `GUI_PWD`, `WEBUI_PWD`, `PUID`, `PGID`, `INCOMING_DIR`, `TEMP_DIR` |

## 3. Python 3.13, installed by apt

Debian trixie has no `python3.14` package (`python3` is 3.13.5; 3.14 only appears in forky/sid).
Copying `/usr/local` from a python image, or letting uv install a standalone build, both work but
both put C libraries outside dpkg — and a uv standalone build bundles its own OpenSSL and SQLite,
which is exactly the kind of blind spot section 8 refuses to widen.

So the node moves to **Python 3.13, installed with `apt-get install python3 python3-venv`**.
Every C library stays dpkg-managed, therefore visible to Syft and scanned by Grype, and CPython
security fixes arrive through a base rebuild.

This was measured, not assumed. Against the snapshot at `329f905`, with `requires-python`
relaxed to `>=3.13`:

- no 3.14-only construct anywhere in the sources;
- every dependency resolves on 3.13;
- matching 255 passed / 100% branch, vex_guards 74 passed / 100% branch, crawler 1007 passed;
- the single crawler failure is `test_connect_refused_raises_connect_error`, whose own comment
  says "deterministic RST (Linux)". It fails identically under 3.14 on macOS. Platform, not
  version.

Consequence: `requires-python = ">=3.13"` in the three packages, `python_version = "3.13"` for
mypy, and the AGENTS.md hard rule becomes "Python only (>=3.13)". When ngosang's base moves to
forky, `python3` becomes 3.14 with no work on our side.

## 4. Process supervision

Two services under the inherited s6-svscan: `amuled` (ngosang's, untouched) and `mulewatch`
(ours, at `/etc/services.d/mulewatch/`).

**`run`** executes as root, then drops to the `amule` user:

1. Grant the amule group control over amuled: `s6-svperms -G <amule group>
   /etc/services.d/amuled`. Without this the control FIFO stays root-owned, mode 0600, and
   mulewatch cannot restart amuled.
2. `chown` the mulewatch data directory to `PUID:PGID`.
3. `exec setpriv --reuid ... --regid ... --init-groups python -m mulewatch ...`

Known race: `s6-svperms` needs `/etc/services.d/amuled/supervise/` to exist, which happens only
once s6-supervise has started amuled. `run` must wait for that path with a bounded retry loop
rather than assume ordering.

**`finish`** receives the exit code and decides the container's fate:

| exit code | action | why |
|---|---|---|
| `0` | nothing; s6 restarts mulewatch alone | this is `/controls/restart`. amuled keeps its eD2k/Kad sessions. |
| non-zero | `s6-svscanctl -t /etc/services.d` | a crash — invalid config above all — must kill the container, so `restart: unless-stopped` produces a visible backoff loop instead of a silent crashloop |

amuled stays supervised normally: it crashes, s6 restarts it, the crawler's EC backoff absorbs
the gap and the observability pipeline reports it.

`docker stop` needs no work: s6-svscan's default SIGTERM behaviour stops every service, waits for
the tree, then runs `finish`.

The "daemon unreachable at startup is tolerated" logic in `composition/app.py` becomes **more**
load-bearing, not less: both processes now start at the same instant, so mulewatch will routinely
reach EC before amuled is listening. Keep it.

## 5. Image layout

Builder stage: `debian:trixie-slim` + `python3` + `python3-venv` + the uv binary, producing
`/app/.venv` from `uv.lock` exactly as today (deps layer first, then the workspace member).

Runtime stage: `FROM ngosang/amule:3.0.1-2@sha256:…`, plus

- `apt-get install -y --no-install-recommends python3` (its dependencies pull `libsqlite3-0`,
  `libssl3` and the rest — do not hand-list them);
- `COPY --from=builder /app/.venv /app/.venv`;
- `ENV PATH="/app/.venv/bin:$PATH"` — required so the documented
  `docker compose run --rm --entrypoint python …` diagnostic recipe keeps working;
- `COPY` of `/etc/services.d/mulewatch/{run,finish}`;
- `LABEL org.opencontainers.image.version` from the `VERSION` build-arg, unchanged.

We do **not** override `ENTRYPOINT`: ngosang's entrypoint creates the user, writes `amule.conf`
when absent, and hands off to s6. That is config management we would otherwise have to write.

Measured sizes (compressed, amd64): base 46.1 MB + python ≈ 16 MB + our venv 12.2 MB ≈ 75 MB,
against 29.9 MB + 46.1 MB = 76 MB pulled today across two images. **Operator download is
unchanged.** Build time is unchanged too: nothing is compiled.

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

**Observability**: the `instance` field on the two events that carry it, and its Prometheus
label, are removed. With one daemon the label is constant, and a breaking release is the free
moment to drop it.

## 7. Deployment

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

The merged container runs a root PID 1 that creates users and chowns directories, so
`user:`, `read_only:` and `cap_drop: ALL` cannot survive. This reverses the crawler half of the
2026-06-17 posture decision, deliberately and with operator sign-off: the crawler descends to
amuled's confinement level rather than amuled rising to the crawler's.

Kept: `security_opt: no-new-privileges:true` (`setpriv` only drops privileges, so this should
hold — **to validate on real hardware**), `pids_limit: 512` and `mem_limit: 2g`, both raised
because one container now holds both processes, and both to be tuned on the live node.

### Health

```yaml
healthcheck:
  test: ["CMD-SHELL", 'test "$(/usr/bin/s6-svstat -u /etc/services.d/amuled)" = true']
```

Note the pitfall: `s6-svstat` exits 0 even for a stopped service (it prints `false`; exit 1 means
s6-supervise itself is not running), so the output must be tested, not the exit code.

Probing amuled only is deliberate — it is the one process nothing else covers, and it is the one
probe that works when `webui.enabled: false`, unlike today's HTTP check. Probing mulewatch would
be near-tautological now that a crash kills the container.

### Two web surfaces

Both are published by default: mulewatch on 8080, amuleweb on 4711.

Neither amuleweb nor amuleapi can run without a password, so authentication cannot be delegated
entirely to a reverse proxy. mulewatch's own UI keeps its existing posture — **no built-in
auth** — and `.env.example` plus the runbook must state plainly that `WEBUI_PWD` protects 4711
only, and that 8080 exposes the catalog, the crawl controls and a read-only SQL console to
anyone who can reach it.

`WEBUI_PWD` gets a `:?` guard like `AMULE_EC_PASSWORD`, so ngosang's "random password printed to
the logs" path can never trigger.

The mulewatch navigation gains a link to the aMule UI, with a configurable base so it survives a
reverse proxy in front of 8080.

### Port sync

`HttpMuleRestarter` is deleted along with the Docker API surface. `S6MuleRestarter` replaces it
and runs `s6-svc -r /etc/services.d/amuled`. Use the documented `s6-svc` command rather than
writing the control FIFO directly: the byte-level protocol is an internal detail not worth
depending on.

This is also more correct than what it replaces. The port was never re-bindable at runtime, so
port-sync needed a *process* restart; it was only ever restarting a *container* because the
process was out of reach.

## 8. Supply chain

Kept in full: cosign signature, the three attestations, the daily Grype scan, `vex_guards`.

Moving base changes two of the eight claims, and the existing guards catch both:
`CVE-2026-12003` names Alpine in its own impact statement, and `CVE-2025-60876` is about the
busybox wget applet, which no longer exists — `check_stale_claims` flags it once Grype stops
reporting it. Both claims and their guards are removed.

`vex_guards.sbom.load_apk_packages` must read dpkg packages instead of apk. No image guard
currently exists, so this is an unused path being repointed.

Coverage improves overall: amuled is a third-party image we scan, attest and sign **not at all**
today. Merged, its whole apt layer becomes visible.

The one real hole: aMule, wxWidgets and ffmpeg are built from source in the base, carry no
package metadata, and Syft will not see them. This is not a false claim — it is a silent absence,
and neither VEX nor a guard can express it.

**Declare it, do not fill it.** Add `compositions` with `aggregate: incomplete` to the CycloneDX
SBOM and a paragraph in `SECURITY.md` naming the three binaries and stating that their security
tracking runs through the base image tag. A signed SBOM that declares itself incomplete is
honest; a signed SBOM that stays silent is not.

Rejected: injecting the three components with probed versions so Grype can match CPEs. aMule
3.0.x is a three-month-old rewrite with no NVD entries — it would cost a guard and half a day to
scan against nothing.

The VEX product purl follows the rename to `pkg:oci/mulewatch`.

## 9. Renaming

| | from | to |
|---|---|---|
| Image | `ghcr.io/geoffreycoulaud/mulewatch-crawler` | `ghcr.io/geoffreycoulaud/mulewatch` |
| Compose service | `crawler` | `mulewatch` |
| VEX product | `pkg:oci/mulewatch-crawler` | `pkg:oci/mulewatch` |

The artifact is no longer "the crawler", it is the whole node. The Python package `mulewatch` and
`python -m mulewatch` do not move: they still name the crawler, which remains one process of two.

**Do not delete the `mulewatch-crawler` GHCR package.** It stays at 1.x and is the rollback path
while the live node migrates.

## 10. Live node migration

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

Rollback: repoint `IMAGE_TAG` at the 1.x `mulewatch-crawler` image and restore the old compose
file. The named volumes are not destroyed by this procedure.

## 11. Work packages

Lot 1 lands first — it touches every package. Lots 2 to 5 are independent of each other. Lot 6
needs 2 and 4. Lot 7 is last.

| Lot | Content | Depends on |
|---|---|---|
| **1. Python 3.13** | three `requires-python`, mypy `python_version`, `uv.lock`, the AGENTS.md hard rule | — |
| **2. Image** | Dockerfile (builder + runtime), `/etc/services.d/mulewatch/{run,finish}`, the `s6-svperms` wait loop | 1 |
| **3. Crawler code** | delete the `amules` pool and `download.endpoint` parsing, EC constants, `amule_ec_password`, `S6MuleRestarter`, delete `HttpMuleRestarter` and `restarter_url`, drop the `instance` field and label | 1 |
| **4. Deploy** | the three compose files, `.env.example`, config files moved to `deploy/` root, the matching golden corpus `parents[N]` paths, AGENTS.md's `deploy/config/` invariant | 1 |
| **5. Supply chain** | remove two VEX claims and their guards, `load_apk_packages` → dpkg, SBOM `compositions`, `SECURITY.md` | 1 |
| **6. Tests** | `tests/smoke/compose.yaml` down to one service, `test_compose_smoke.py` (service name, no docker-proxy) | 2, 4 |
| **7. Docs** | runbooks (deployment, administration, troubleshooting), `docs/README.md`, `architecture.md`, the migration procedure, the RE2 note in section 13, the handoff | 2–6 |

Every lot keeps the gate green on its own: `uv run poe check`, 100% branch coverage per package.

## 12. Not validated against real hardware

- gluetun firewall behaviour for ports published on the gluetun service — sources disagree on
  whether `FIREWALL_INPUT_PORTS` is needed for ports reached from the LAN.
- `no-new-privileges:true` alongside `setpriv` in the merged container.
- `pids_limit` and `mem_limit` values.
- The `s6-svperms` timing against a cold container start.
- Whether amuleweb 3.0.1 behaves as expected where `3.0.0-1` did not.

## 13. Recorded, not acted on

The 2026-07-03 decision to drop `google-re2` rested explicitly on the absence of musllinux
wheels, which blocked Alpine. **Moving to Debian removes that blocker.** We are not reverting:
stdlib `re` works, the tests pass, the ReDoS risk was accepted, and `matcher.yml` stays
operator-owned. But the AGENTS.md note must be amended, or it will keep justifying a choice with
a constraint that no longer exists.

## 14. Out of scope

- Re-adding RE2 (see 13).
- `amuleapi` and the REST API (see 2).
- Authentication on the mulewatch web UI.
- Deleting downloaded files; the free-space floor still only refuses new work.
- The multi-node story: N nodes stay N containers, merged by `mulewatch.merge`.
