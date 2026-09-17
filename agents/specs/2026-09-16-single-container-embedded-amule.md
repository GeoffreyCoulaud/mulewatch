# Single container with an embedded aMule

- Date: 2026-09-16
- Status: APPROVED (operator sign-off 2026-09-16, discussion phase)
- Scope: collapse the crawler and amuled into one image, one compose service, one process tree
- Release: `v2.0.0` — breaking, with a manual migration of the live node
- Related: `packages/crawler/Dockerfile`, `deploy/**`, `security/**`, `packages/vex_guards/`,
  `docs/runbooks/**`, AGENTS.md invariants

## 1. Goals

**Simplify deployment.** A node needs two images, up to four compose services, a Docker socket
proxy, and named volumes the operator cannot read. The crawler is useless without amuled, and
amuled is pointless without the crawler. One container without a VPN, two with gluetun.

This breaks "one container, one process". That rule buys independent lifecycles and independent
scaling. We have neither: the processes are born and die together.

**Make the SBOM honest.** Today amuled is a third-party image we do not scan, attest or sign.
Section 8 explains why the chosen strategy fixes that instead of relabelling it.

## 2. Strategy: our own image, aMule from nixpkgs

| Route | Verdict |
|---|---|
| Build aMule by hand (CMake, wxWidgets, ffmpeg in our Dockerfile) | Rejected. We would own the whole dependency graph, with one maintainer. |
| Base on `ngosang/amule:3.0.1-2` | Rejected. aMule, wxWidgets and ffmpeg are source-built there, with no package metadata. Syft cannot see them, so Grype never scans them. |
| **nixpkgs** | **Chosen.** Every component has a name and a version, so the SBOM is complete. |

No distribution ships aMule 3.0.x as a binary package (Debian is on 2.3.3, Alpine has none).
nixpkgs builds it from source with pinned, cached dependencies — that is the difference between
packaging aMule ourselves and using someone else's expression.

### Why 3.0.1 and not 3.1

Upstream has no `3.1.0` tag. The latest release is `3.0.1` (2026-06-24). `amuleapi` — the modern
Web UI and the REST API — is real upstream code in `master`, but unreleased. We do not need the
REST API: we speak EC, which is richer and already implemented. aMule 3.0.1 carries the WebUI
modernization pass (HTML5, CSS rework, XSS hardening), which 3.0.0 lacked.

**When bumping to 3.1, re-check the passwords.** In `master` they moved to a separate store and
are no longer read from `amule.conf`. Section 7 depends on 3.0.1 behaviour.

### The build

Pinned to nixpkgs `c7def046b9a883d46974757852106483d741586f`:

```nix
wxBase = (wxwidgets_3_2.override { withWebKit = false; withMesa = false; }).overrideAttrs (old: {
  # --disable-gui is not an exposed option. --enable-mediactrl is unconditional upstream
  # and is what pulls GStreamer in, so it goes with the GUI, not alongside it.
  configureFlags =
    (builtins.filter (f: f != "--enable-mediactrl" && f != "--with-nanosvg") old.configureFlags)
    ++ [ "--disable-gui" ];
  buildInputs = [ zlib pcre2 expat curl ];
});

amule' = (amule.override {
  monolithic = false; enableDaemon = true; httpServer = true; wxwidgets_3_2 = wxBase;
}).overrideAttrs (_: { pname = "amule"; });   # the pname is load-bearing — see section 8
```

Measured in CI on both architectures (run `35135831426`):

| | closure | build |
|---|---|---|
| `nixpkgs#amule-daemon`, from the binary cache | 922 MB (amd64) / 975 MB (arm64) | fetched |
| without webkit and mesa | 634 MB | 13 min |
| **wxBase, no GUI** | **101 MB** | **2 min 14 s** (wxBase alone: 98 s) |

89% off the baseline. The job runs in 4 min 35 s (amd64) and 3 min 28 s (arm64). It produces
`amuled`, `amuleweb` and `ed2k`. A closure check for `gtk`, `gst`, `gspell`, `cups`, `at-spi`,
`webkit`, `python3` and `perl` comes back empty.

The compile happens once per nixpkgs bump, not per CI run.

## 3. Python 3.13, from apt

The runtime base is `debian:trixie-slim`. Trixie has no `python3.14` package (`python3` is 3.13.5;
3.14 lands in forky). Taking Python from nix instead would ship a second, much larger Python for
no gain: Debian's is dpkg-managed and covered by Debian's security feed, which matches better than
the CPE route of section 8.

So: **`apt-get install python3 python3-venv`**.

Measured, not assumed. Against `329f905`, with `requires-python` relaxed to `>=3.13`:

- no 3.14-only construct in the sources;
- every dependency resolves;
- matching 255 passed / 100% branch, vex_guards 74 passed / 100% branch, crawler 1007 passed;
- the one crawler failure is `test_connect_refused_raises_connect_error`, whose own comment says
  "deterministic RST (Linux)". It fails identically under 3.14 on macOS. Platform, not version.

Changes: `requires-python = ">=3.13"` in three packages, `python_version = "3.13"` for mypy, and
the AGENTS.md hard rule becomes "Python only (>=3.13)".

## 4. Process supervision

We own the supervision layer. **s6 comes from Debian's `s6` package**, not s6-overlay, whose
release tarball is not dpkg-managed and would open the kind of blind spot section 8 exists to
close. `s6-svscan` runs as PID 1.

Three services under `/etc/services.d/`: `amuled`, `amuleweb`, `mulewatch`. Each `run` starts as
root, then drops privileges with `setpriv --reuid --regid --init-groups`.

`mulewatch`'s `run` also:

1. runs `s6-svperms -G <group> /etc/services.d/amuled`, so the amule group can control amuled.
   Without it the control FIFO stays root-owned, mode 0600, and mulewatch cannot restart amuled.
2. `chown`s the data directory to `PUID:PGID`.

**Race to handle:** `s6-svperms` needs `/etc/services.d/amuled/supervise/`, which appears only
once s6-supervise has started amuled. `run` must wait for that path with a bounded retry loop.

`finish` reads the exit code and decides the container's fate:

| exit code | action | why |
|---|---|---|
| `0` | nothing; s6 restarts mulewatch alone | this is `/controls/restart`. amuled keeps its eD2k/Kad sessions. |
| non-zero | `s6-svscanctl -t /etc/services.d` | a crash — invalid config above all — kills the container, so `restart: unless-stopped` gives a visible backoff loop instead of a silent crashloop |

amuled and amuleweb stay supervised normally: they crash, s6 restarts them, the crawler's EC
backoff absorbs the gap, the observability pipeline reports it.

`docker stop` needs no work: s6-svscan's default SIGTERM handling stops every service, waits for
the tree, then runs `finish`.

Keep the "daemon unreachable at startup is tolerated" logic in `composition/app.py`. It matters
more now, not less: all three processes start at once, so mulewatch will routinely reach EC before
amuled listens.

## 5. Image layout

Three stages.

**aMule stage** — `nix build` on the pinned expression, then export the runtime closure. `nix`
itself never reaches the final image; only the store paths do. Syft reads store path names, so a
plain `COPY` is enough (verified, section 8).

**Python stage** — `debian:trixie-slim` + `python3` + `python3-venv` + the uv binary, producing
`/app/.venv` from `uv.lock` as today (deps layer first, then the workspace member).

**Runtime** — `debian:trixie-slim`, plus:

- `apt-get install -y --no-install-recommends python3 s6` — their dependencies pull
  `libsqlite3-0`, `libssl3` and the rest; do not hand-list them;
- the aMule closure store paths;
- `/app/.venv` from the Python stage;
- `ENV PATH="/app/.venv/bin:$PATH"`, so the documented
  `docker compose run --rm --entrypoint python …` recipe keeps working;
- our entrypoint, the three `/etc/services.d/*` units, the config script of section 7;
- `LABEL org.opencontainers.image.version` from the `VERSION` build-arg, unchanged.

## 6. Crawler configuration

The container holds exactly one amuled at `127.0.0.1:4712`, so the multi-daemon pool can never be
exercised. It goes.

**Removed from `crawler.yml`**: the `amules:` list, `download.endpoint`,
`port_sync.restarter_url`.

**Added**: `amule_ec_password: ${AMULE_EC_PASSWORD}`, resolved by the config adapter as today, so
the domain still never reads the environment.

**Host and port become code constants.** Precedent: AGENTS.md already fixes the webui bind at
`0.0.0.0:8080` in code and lets YAML gate only the surface.

`AmuleEndpoint` stays as a code-level dataclass. The integration suites build it in Python from
environment variables (`tests/integration/conftest.py`), never from YAML, so they are unaffected.
Only its YAML surface disappears.

Other values: `download.output_dir: /downloads`, `port_sync.gluetun_control_url:
http://localhost:8000`.

**Observability**: drop the `instance` field from the two events that carry it, and its Prometheus
label. With one daemon the label is constant, and a breaking release is the free moment to remove
it.

## 7. aMule's own configuration

This is the work we take on by not using a third-party image. One oneshot script, run before the
services start. It must:

- create the `amule` user from `PUID`/`PGID`;
- `chown` the three bind-mounted directories;
- write `amule.conf` **only if absent**.

Nothing else. No auto-restart cron, no auto-share, no remote-GUI config, no random password
generation — the compose file requires the variables.

aMule uses wxConfig, so an absent key takes its declared default. Verified against the 3.0.1 tag:
every setting is registered with a default and read through `Read(key, &value, default)`. The file
therefore needs **four keys**, not a full config:

| key (section / name) | default in 3.0.1 | write it? |
|---|---|---|
| `ExternalConnect / ECPort` | **4712** | no — already our value |
| `ExternalConnect / AcceptExternalConnections` | `false` | yes |
| `ExternalConnect / ECPassword` | empty | yes, as an MD5 hex digest |
| `eMule / IncomingDir` | under the home dir | yes → `/downloads/incoming` |
| `eMule / TempDir` | under the home dir | yes → `/downloads/temp` |

`ECPassword` is a `Cfg_Str_Encrypted` field, but it hashes only on GUI input; on load it reads the
string as-is. So the value on disk must already be the digest — a `printf | md5sum`.

aMule rewrites the full file itself on its first save, so our minimal file becomes a normal one.

We write the download paths instead of mounting the host directories onto aMule's defaults.
Mounting onto the defaults works and saves two keys, but those defaults sit inside
`/home/amule/.aMule`, which is itself a bind mount — the operator would see `deploy/amule/`
holding empty directories shadowed by other mounts. Two generated lines are cheaper than that.

## 8. Supply chain

Measured with Syft 1.51.1 and Grype 0.118.0, database built 2026-09-16.

**Nix packages are visible without the nix database.** A rootfs holding only store paths gives
12/12 components, each with a `pkg:nix/...` purl and a CPE. A plain `COPY` of the closure is
enough. (A variant carrying a hand-made database returned zero, but that is a harness artefact —
its absolute paths cannot match a relocated scan root. Inconclusive, so: copy the paths, and if a
database is ever shipped, verify detection rather than assume it.)

**Grype matches nix packages.** A control on deliberately old versions returned 16 findings
(libpng 11, zlib 2, **amule 2**, boost 1). Matching goes through CPEs against the NVD, not a
distribution advisory feed, so expect more noise than dpkg gives: libpng 1.6.37 alone produced 11
hits. Current versions return zero because they are clean.

**The package name is load-bearing.** Syft derives the CPE from the package name. A renamed package
looks up a name the NVD does not have — no error, just zero findings. On the same vulnerable
version:

```
amule            2.3.1  ->  cpe:...:amule:amule:2.3.1           ->  2 findings
amule-web-daemon 2.3.1  ->  cpe:...:amule-web-daemon:...:2.3.1  ->  0 findings
```

Our override renames the derivation to `amule-web-daemon`, which is why section 2 forces `pname`
back to `amule`. Confirmed in the closure built in CI: `amule 3.0.1`, `pkg:nix/amule@3.0.1`,
`cpe:2.3:a:amule:amule:3.0.1`. Nothing else in the nixpkgs expression derives from `pname` (only
`mainProgram`, a separate parameter, and a darwin-only `postInstall`), so the override is safe.

**Standing rule: any component whose name we alter must be re-checked the same way.**

With the ngosang base, aMule, wxWidgets and ffmpeg would have been absent from the SBOM, and the
honest response would have been to declare it incomplete. That is no longer needed.

Kept unchanged: cosign signature, the three attestations, the daily Grype scan, `vex_guards`.

Claim changes:

- `CVE-2026-12003` names Alpine in its impact statement — remove it with its guard.
- `CVE-2025-60876` covers the busybox wget applet, which no longer exists — remove it with its
  guard. `check_stale_claims` flags it once Grype stops reporting it.
- The six other claims are about our own source, checked by `check_source_claims` against our tree.
  They survive untouched.
- `vex_guards.sbom.load_apk_packages` must read dpkg instead of apk. No image guard exists today,
  so this is an unused path being repointed.

The VEX product purl follows the rename to `pkg:oci/mulewatch`.

## 9. Deployment

`deploy/config/crawler/` is dismantled. The operator's files sit next to the compose file.

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

All relative bind mounts. Named volumes are gone: the operator must be able to run
`sqlite3 deploy/data/catalog.db` from the host. `PUID`/`PGID` go in `.env.example` — they are what
keeps those directories readable without `sudo`.

`base.compose.yml` holds the shared `mulewatch` service but must **not** declare `ports:` or
networking: compose merges `ports` additively and cannot remove them. Each stack adds its own.

- `compose.yml` publishes 8080, 4711 and the eD2k ports on a default network.
- `gluetun.compose.yml` gives mulewatch `network_mode: service:gluetun` and publishes 8080 and
  4711 **on the gluetun service**.

**Deleted**: the `docker-proxy` service, the `ec` and `egress` networks, every named volume.
Service count: 4 → 2 with a VPN, 2 → 1 without.

### Container hardening

PID 1 runs as root to create users and chown directories, so `user:`, `read_only:` and
`cap_drop: ALL` cannot survive. This reverses the crawler half of the 2026-06-17 posture decision,
deliberately and with operator sign-off: the crawler descends to amuled's confinement level rather
than amuled rising to the crawler's.

Kept: `security_opt: no-new-privileges:true` (`setpriv` only drops privileges, so this should hold
— **to validate**), `pids_limit: 512` and `mem_limit: 2g`, both raised for three processes and both
to be tuned on the live node.

### Health

```yaml
healthcheck:
  test: ["CMD-SHELL", 'test "$(s6-svstat -u /etc/services.d/amuled)" = true']
```

**Pitfall:** `s6-svstat` exits 0 even for a stopped service — it prints `false`, and exit 1 means
s6-supervise itself is not running. Test the output, not the exit code.

Probing amuled only is deliberate. It is the process nothing else covers, and it works when
`webui.enabled: false`, unlike today's HTTP check. Probing mulewatch would be near-tautological
now that a crash kills the container.

### Two web surfaces

Both published by default: mulewatch on 8080, amuleweb on 4711.

amuleweb cannot run without a password, so authentication cannot be delegated entirely to a
reverse proxy. mulewatch's own UI keeps its posture — **no built-in auth**. `.env.example` and the
runbook must say plainly that `WEBUI_PWD` protects 4711 only, and that 8080 exposes the catalog,
the crawl controls and a read-only SQL console to anyone who can reach it. `WEBUI_PWD` gets a `:?`
guard like `AMULE_EC_PASSWORD`.

The mulewatch navigation gains a link to the aMule UI, with a configurable base so it survives a
reverse proxy in front of 8080.

### Port sync

`HttpMuleRestarter` and the Docker API surface are deleted. `S6MuleRestarter` replaces them and
runs `s6-svc -r /etc/services.d/amuled`. Use the documented `s6-svc` command, not a direct write to
the control FIFO: the byte-level protocol is an internal detail.

This is also more correct. The port was never re-bindable at runtime, so port-sync always needed a
*process* restart; it restarted a *container* only because the process was out of reach.

## 10. Renaming

| | from | to |
|---|---|---|
| Image | `ghcr.io/geoffreycoulaud/mulewatch-crawler` | `ghcr.io/geoffreycoulaud/mulewatch` |
| Compose service | `crawler` | `mulewatch` |
| VEX product | `pkg:oci/mulewatch-crawler` | `pkg:oci/mulewatch` |

The artifact is the whole node now, not the crawler. The Python package `mulewatch` and
`python -m mulewatch` do not move: they still name the crawler, one process of three.

**Do not delete the `mulewatch-crawler` GHCR package.** It stays at 1.x and is the rollback path
during migration.

## 11. Live node migration

Breaking, done by hand once, no compatibility code. Belongs in the handoff and the deployment
runbook.

1. `docker compose down`
2. Copy each named volume to its new bind mount (`docker cp` from a throwaway container):
   `catalog-db` and `local-db` → `deploy/data/`, `amule-state` → `deploy/amule/`.
3. Move `deploy/config/crawler/*.yml` to `deploy/`.
4. Edit `crawler.yml`: drop `amules:`, `download.endpoint`, `port_sync.restarter_url`; add
   `amule_ec_password`; set `output_dir: /downloads` and the gluetun control URL to localhost.
5. Add `PUID`/`PGID` and `WEBUI_PWD` to `.env`, then `chown` the bind mounts to that uid.
6. `docker compose up -d` against the new `compose.yml`.

The existing `amule.conf` is carried over and **not** rewritten — the config script only writes it
when absent. Check that `IncomingDir` and `TempDir` point at `/downloads/incoming` and
`/downloads/temp`.

Rollback: repoint `IMAGE_TAG` at the 1.x `mulewatch-crawler` image and restore the old compose
file. This procedure does not destroy the named volumes.

## 12. Work packages

Lot 1 first — it touches every package. Lots 2 to 5 are independent. Lot 6 needs 2 and 4. Lot 7 is
last.

| Lot | Content | Needs |
|---|---|---|
| **1. Python 3.13** | three `requires-python`, mypy `python_version`, `uv.lock`, the AGENTS.md hard rule | — |
| **2. Image** | the pinned nix expression, the three-stage Dockerfile, the entrypoint, `/etc/services.d/{amuled,amuleweb,mulewatch}`, the `s6-svperms` wait loop, the `amule.conf` oneshot (section 7) | 1 |
| **3. Crawler code** | delete the `amules` pool and `download.endpoint` parsing, EC constants, `amule_ec_password`, `S6MuleRestarter`, delete `HttpMuleRestarter` and `restarter_url`, drop the `instance` field and label | 1 |
| **4. Deploy** | the three compose files, `.env.example`, config files moved to `deploy/` root, the matching golden corpus `parents[N]` paths, AGENTS.md's `deploy/config/` invariant | 1 |
| **5. Supply chain** | remove two VEX claims and their guards, `load_apk_packages` → dpkg, the nix cataloger in the release SBOM, the `pname` rule of section 8 written where it will be read | 1 |
| **6. Tests** | `tests/smoke/compose.yaml` down to one service, `test_compose_smoke.py` (service name, no docker-proxy) | 2, 4 |
| **7. Docs** | runbooks, `docs/README.md`, `architecture.md`, the migration procedure, the RE2 note (section 14), the handoff | 2–6 |

Every lot keeps the gate green on its own: `uv run poe check`, 100% branch coverage per package.

## 13. Not validated against real hardware

- gluetun firewall behaviour for ports published on the gluetun service. Sources disagree on
  whether `FIREWALL_INPUT_PORTS` is needed for ports reached from the LAN.
- `no-new-privileges:true` alongside `setpriv`.
- The `pids_limit` and `mem_limit` values.
- `s6-svperms` timing on a cold start.
- How `amuleweb` 3.0.1 takes its password: `-A` on the command line, or its own config file.
  Section 7 covers `amule.conf` only.
- Whether amuleweb 3.0.1 behaves as expected where 3.0.0 did not.
- The final image size. Only the aMule closure (101 MB) was measured; the Debian base, Python and
  our venv sit on top.

## 14. Recorded, not acted on

The 2026-07-03 decision to drop `google-re2` rested on the absence of musllinux wheels, which
blocked Alpine. **Moving to Debian removes that blocker.** We are not reverting: stdlib `re` works,
the tests pass, the ReDoS risk was accepted, and `matcher.yml` stays operator-owned. But amend the
AGENTS.md note, or it will keep justifying a choice with a constraint that no longer exists.

## 15. Out of scope

- Re-adding RE2 (section 14).
- `amuleapi` and the REST API (section 2).
- Authentication on the mulewatch web UI.
- Trimming the aMule closure further. curl and its tree (krb5, ngtcp2, libssh2, openssl, zstd) is
  the largest contributor now; at 101 MB it is not worth chasing.
- Deleting downloaded files. The free-space floor still only refuses new work.
- The multi-node story: N nodes stay N containers, merged by `mulewatch.merge`.
