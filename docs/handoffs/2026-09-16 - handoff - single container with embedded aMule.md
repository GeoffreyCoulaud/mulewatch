# Handoff — single container with an embedded aMule

- Date: 2026-09-16
- Branch: `feat/single-container` (not merged at the time of writing)
- Spec: `docs/specs/2026-09-16-single-container-embedded-amule.md` (APPROVED, operator sign-off)
- Release this prepares: **`v2.0.0` — breaking**, with a hand-run migration of the live node
- Work packages: 7 of 7 landed on the branch (lot 7 is this document and the runbooks)

> **Read section 5 before anything else if you are about to merge or deploy.** There is **no
> container runtime on the development machine**, so **the image was never built and never run**.
> CI is its first real execution. Everything in section 5 is unverified.

---

## 1. Where the project stands

A node used to be two images and up to four compose services (`crawler`, `amuled`, `docker-proxy`,
`gluetun`) with named volumes the operator could not read. It is now **one image, one compose
service, one process tree**:

```
PID 1  entrypoint.sh (root)
         └─ amule-config.sh        one-shot: create the `amule` user from PUID/PGID,
         │                         chown the mount points, write amule.conf if absent
         └─ exec s6-svscan /etc/services.d
              ├─ amuled            aMule 3.0.1, EC on 127.0.0.1:4712       (setpriv → amule)
              ├─ amuleweb          aMule's own web UI, port 4711           (setpriv → amule)
              └─ mulewatch         the crawler + the in-process webui, 8080 (setpriv → amule)
```

- Image: `ghcr.io/geoffreycoulaud/mulewatch` (was `mulewatch-crawler`). Compose service: `mulewatch`.
- Base: `debian:trixie-slim`, `python3` (3.13.x) and `s6` from Debian's own packages, plus aMule
  3.0.1 built from a **pinned nixpkgs** (`c7def046…`) and copied in as a bare store closure.
- Stacks: `deploy/compose.yml` (direct, **one** service) and `deploy/gluetun.compose.yml` (VPN,
  **two**), both `include:`-ing `deploy/base.compose.yml`.
- State: **bind mounts only**. `deploy/{data,amule,downloads}/` are plain folders. No named volume
  survives anywhere.
- Python floor dropped to **3.13** (Trixie has no 3.14) across all three packages and mypy.

## 2. What was built, lot by lot

| Lot | What landed |
|---|---|
| 1 | `requires-python = ">=3.13"` in three packages, mypy `python_version`, `uv.lock`, the AGENTS.md hard rule. |
| 2 | `packages/crawler/docker/`: the pinned `amule.nix`, the three-stage `Dockerfile`, `entrypoint.sh`, `amule-config.sh`, `services.d/{amuled,amuleweb,mulewatch}` and the crawler's `finish`. |
| 3 | Deleted the amuled pool and `HttpMuleRestarter`; `AMULE_EC_HOST`/`AMULE_EC_PORT`/`AMULE_INSTANCE_NAME` became code constants; added `amule_ec_password`; added `S6MuleRestarter`; dropped the `instance` field from two events and its Prometheus label. |
| 4 | The three compose files, `.env.example`, config files moved to the `deploy/` root, the matching corpus `parents[N]` paths, **and the AGENTS.md confinement-posture and `deploy/` invariants** (already rewritten — do not redo them). |
| 5 | Two Alpine-era VEX claims and their guards removed, `load_apk_packages` → dpkg, the VEX product purl → `pkg:oci/mulewatch`, the `pname` rule written into `SECURITY.md`, and a release gate on the SBOM. |
| 6 | `tests/smoke/compose.yaml` down to one service; `test_compose_smoke.py` rewritten around it (bind mounts, PUID/PGID, `s6-svstat`, both entry points rendered with `docker compose config`). |
| 7 | This handoff, the three runbooks, `docs/README.md`, `docs/architecture.md`, root `README.md`, the AGENTS.md RE2 note. |

## 3. Where the implementation diverged from the spec

The spec is an approved record of a decision, not a live document, and it was **not** edited. These
are the places where what shipped differs from what §1–§15 describe. **Reality wins; this list is
the reconciliation.**

1. **amuleweb does not take its password from `amule.conf`** (this answers an open question in
   spec §13). `--amule-config-file` is a trap: in `WebInterface.cpp` it makes amuleweb stop parsing
   the rest of the command line and go quiet — `--admin-pass` and its own logging included. The s6
   service therefore passes `--password="$AMULE_EC_PASSWORD" --admin-pass="$WEBUI_PWD"` on the
   command line, which is why both variables must be in the container's environment rather than in
   a file.
2. **`./downloads` is bound whole**, not as the two subdirectories spec §9's tree draws. With only
   the subdirectories mounted, `/downloads` itself would be the container's writable layer, and the
   free-space floor's `statvfs` on `/downloads` would measure the wrong filesystem. The host-side
   tree is unchanged, so an operator sees no difference.
3. **The operator's three config files mount read-only** at `/app/config/{crawler,targets,matcher}.yml`
   and the crawler's s6 `run` passes those paths explicitly. The CLI defaults (now `deploy/*.yml`)
   exist for running from a checkout, not in the image.
4. **The nix cataloger did not need enabling.** It is already default-on in Syft's image cataloger
   set at the version `anchore/sbom-action` actually ships (**v1.42.3**, not the 1.51.1 spec §8 was
   measured with). Instead, `release.yml` now **hard-fails before signing** if no `pkg:nix/amule@`
   purl appears in either SBOM. That one guard catches three separate silent failures: a cataloger
   default drifting, the closure no longer being copied, and a `pname` regression.
5. **The standing "renaming a package silently stops it matching" rule lives in `SECURITY.md`**, not
   in a spec section, because that is where a future maintainer renaming something will look.
6. **`webui.amule_url`** is a new optional `crawler.yml` key (default `http://localhost:4711`) that
   the spec does not mention. It is the target of the "aMule" link in the webui navigation, and only
   needs setting when a reverse proxy sits in front of 8080 — the browser resolves it, not the
   container.
7. **Behaviour change the spec never called out: persisted backoff resets once on upgrade.** The
   amuled instance name is now the code constant `"amuled"`; 1.x read it from `crawler.yml`
   (typically `amule-1`). Backoff rows and scheduler-state channel keys are keyed on that name, so
   the pre-upgrade rows are orphaned and the first 2.0 cycle starts clean. Harmless for a breaking
   v2.0.0 — the visible effect is one cycle retrying a channel it would otherwise have paused — but
   an operator should not have to guess. It is written into the migration procedure.
8. **The direct stack file is `deploy/compose.yml`**, was `compose.yaml`. Anything that told an
   operator to look for `compose.yaml` is now wrong.
9. **`security/crawler.vex.openvex.json` keeps its filename**, and the Code scanning SARIF
   categories keep their `-crawler` suffix. Renaming a Code scanning category orphans its existing
   findings; the cosmetic inconsistency is deliberate and preferable.
10. **Four environment variables are hard-required**: `PUID`, `PGID`, `AMULE_EC_PASSWORD`,
    `WEBUI_PWD`. The startup one-shot exits 1 and the container dies if any is missing. The compose
    files carry `:?` guards so the usual failure is a clear `compose up` error instead.

## 4. Learned pitfalls (the ones that cost time)

- **`s6-svstat` exits 0 for a stopped service.** It prints `false`; exit 1 means `s6-supervise`
  itself is not running. A healthcheck written on the exit code reports a dead amuled as healthy.
  Test the printed output — both the deploy and the smoke healthchecks do.
- **`s6-svperms` needs a directory that does not exist yet.** It operates on
  `/etc/services.d/amuled/supervise/`, which `s6-supervise` creates only once it has started amuled
  — and all three services start simultaneously. The crawler's `run` waits for
  `supervise/control` in a bounded loop (100 × 0.1 s) and degrades loudly rather than failing:
  without the group permission we lose the ability to restart amuled for a port change, not the
  crawl itself.
- **The `pname` of a nix derivation is a security control, not cosmetics.** Syft derives the CPE
  from the package name, and `httpServer = true` renames the derivation to `amule-web-daemon` — a
  name the NVD has never heard of. Grype then returns **zero findings on a vulnerable aMule**, with
  no error. Measured: `amule 2.3.1` → 2 findings, `amule-web-daemon 2.3.1` → 0.
- **`--disable-gui` is not an exposed wxWidgets option**, and `--enable-mediactrl` (which is what
  drags GStreamer in) is unconditional upstream. Filtering the configure flags by hand is what takes
  the closure from 634 MB to 101 MB.
- **`ECPassword` must be written already hashed.** It is a `Cfg_Str_Encrypted` field, but aMule only
  hashes on GUI input; on load it reads the string as it stands. The one-shot writes a
  `printf | md5sum` digest.
- **The crawler's exit code is a protocol now.** Exit 0 means "restart me alone" (the webui's
  `/controls` restart, which lets amuled keep its eD2k and Kad sessions); anything else runs
  `s6-svscanctl -t` and takes the whole container down, so an invalid config shows up as a visible
  `restart: unless-stopped` backoff loop instead of a silent crash loop in a container that still
  reports healthy.
- **A container at `Up (healthy)` does not prove the crawler is running.** The healthcheck probes
  amuled only — deliberately, since it works with `webui.enabled: false` and a crawler crash already
  kills the container. But an operator reading `healthy` is reading about amuled.
- **`base.compose.yml` must declare no `ports:` and no networking.** Compose merges `ports`
  additively and cannot remove an entry an included fragment added, so each stack publishes its own.
- **There is no supported way to rotate `AMULE_EC_PASSWORD`.** `amule-config.sh` writes the MD5
  digest into `amule.conf` **only when the file is absent**, while the crawler and amuleweb both
  read the live environment variable. Changing `.env` on a running node therefore desynchronises the
  three processes *silently*: amuled keeps the old digest and refuses both clients. The
  troubleshooting runbook documents the two manual workarounds (hand-edit the digest with
  `printf %s '…' | md5sum | cut -d' ' -f1`, or delete `amule.conf` and lose aMule's other settings).
  This is a genuine gap, listed as a follow-up in §7.

## 5. NOT VALIDATED AGAINST REAL HARDWARE

**There is no container runtime on the development machine** — no docker, no podman, no colima, no
OrbStack; verified, not assumed. **The image was therefore never built and never run.** CI will be
its first real execution.

Agents worked around the gap where they could, and it is worth being precise about what that buys:
the nix expression **was** evaluated for real; `amule-config.sh` **was** run end to end against
stubs; all the compose files **were** validated with a real `docker compose config` pulled through
nix. **None of that is a running container.** Every item below is unverified.

### The image itself

1. **That the image builds at all.** Not one stage has been executed. The three-stage Dockerfile,
   the `nix-build`, the closure export, the `COPY --from` of `/nix/store`, the apt layers — all of
   it is unrun.
2. **The final image size.** Only the aMule closure was measured (101 MB, and on a different
   harness). The Debian base, python3, s6 and our venv sit on top, unmeasured.
3. **That nix store paths are runnable after the copy into Debian.** The closure is asserted to
   carry its own glibc, so a plain `COPY` should suffice. Never executed. If it does not, `amuled`
   fails to start with a loader error and the container is healthy-less from the first second.
4. **That the runtime stage's `python3` really is 3.13.x.** The Debian tag is digest-pinned, but no
   one has read `python3 --version` out of a built image. The whole Python-floor lot rests on it.
5. **The closure grep.** Spec §2 reports an empty result for `gtk`, `gst`, `gspell`, `cups`,
   `at-spi`, `webkit`, `python3` and `perl` **in CI, on the closure** — not on the final image.
   Re-run it against the built image, and add `nix` itself to the list (the builder stage must not
   leak into the runtime).
6. **The arm64 build.** The nix step's timings and cache behaviour come from a separate CI
   experiment, not from this Dockerfile on either architecture.

### The processes

7. **That `amuled` and `amuleweb` actually start**, under s6, as the `amule` user, via `setpriv`,
   with `HOME=/home/amule`. Nothing has run.
8. **That amuleweb 3.0.1 behaves where 3.0.0 did not** (spec §13's open question, still open). The
   `--password` / `--admin-pass` command line is derived from reading `WebInterface.cpp`, not from
   a successful login.
9. **That the MD5 digest written into `amule.conf` authenticates.** Read from aMule's source; never
   put through a real EC handshake. If it is wrong, the crawler loops on `EcAuthError` and the node
   never catalogues anything.
10. **That `s6-svstat -u` prints literally `true`/`false`** in Debian Trixie's s6. Both healthchecks
    and the smoke test compare against that exact string.
11. **The `finish` contract**: that exit 0 restarts the crawler alone, that a non-zero exit really
    takes the container down through `s6-svscanctl -t`, and that `finish` receives `256` on a signal
    as assumed.
12. **The whole port-sync restart path.** That `s6-svc -r /etc/services.d/amuled`, run by the
    crawler as the unprivileged `amule` user *after* `s6-svperms -G amule`, actually succeeds. This
    is the one place where the `s6-svperms` race and the privilege drop meet.
13. **`s6-svperms` timing on a cold start** (spec §13). The bounded wait is 10 s; nobody knows the
    real figure.
14. **`docker stop`.** That SIGTERM to `s6-svscan` stops all three services and reaps the tree
    inside Docker's grace period. Assumed from s6's documented default handling.

### The compose surface

15. **`no-new-privileges:true` alongside `setpriv`** (spec §13). `setpriv` only drops privileges, so
    this *should* hold — untested. If it does not, every service fails at its `setpriv` line and the
    container never starts.
16. **The `pids_limit: 512` and `mem_limit: 2g` values** (spec §13). Both were raised for three
    processes by judgement, not measurement, and both need tuning on the live node. Note that the
    OOM troubleshooting entry's "about 4.5 million rows" landmark was computed at the **old** 512 MB
    limit.
17. **gluetun's `FIREWALL_INPUT_PORTS`** (spec §13). Sources disagree on whether it is needed for
    ports published on the gluetun service and reached **from the LAN**. Symptom to watch for: 8080
    and 4711 answer on the host itself but not from another machine.
18. **That `docker compose ps --format json` exposes a `Health` field.** `test_compose_smoke.py`
    reads it via `_ps_field("Health", …)` and waits on `"healthy"`. If the field is named or shaped
    differently in the CI runner's compose version, that test hangs until its timeout rather than
    failing cleanly.
19. **That host-created bind directories end up writable by the `amule` user** after PID 1's chown.
    The chown is deliberately **not recursive** (the mounts can hold hundreds of gigabytes), so it
    fixes the mount points and nothing below them. A pre-existing `downloads/incoming` owned by
    someone else keeps its ownership. Failure mode: `unable to open database file`, or downloads
    that never start.

### The docs themselves

20. **The migration procedure (deployment runbook, annex E) has never been run against a real 1.x
    node.** The volume names come from the old compose files and the flatten step
    (`/data/catalog/catalog.db` → `data/catalog.db`) is inferred from the config diff. Run it on a
    copy first, or at minimum verify `docker volume ls` and the resulting `ls data/` by hand. The
    rollback path is real and must stay real: **do not delete the `mulewatch-crawler` GHCR
    package** — it is frozen at 1.x precisely for this.
21. **The backoff reset (§3 item 7) is reasoned, not observed.** It follows from the instance-name
    change and the key structure; nobody has watched a 1.x `local.db` boot under 2.0.
22. **The webui's aMule nav link behind a reverse proxy.** `webui.amule_url` is rendered as an
    absolute href; the proxy scenario it exists for has not been exercised.

### Known, expected, not a regression

`test_connect_refused_raises_connect_error`
(`packages/crawler/tests/adapters/mule_ec/test_transport.py`) **fails on macOS** and only on macOS.
It expects the Linux behaviour of a closed port — an immediate RST, surfaced as `EcConnectError` —
where macOS silently drops the SYN, so the call times out and raises `EcTimeoutError` instead. It
drags local crawler coverage to **99.95 %**, which makes `uv run poe check` red on a Mac. It passes
on Linux CI, and the test's own comment already says "deterministic RST (Linux)". Do not "fix" it by
loosening the assertion.

## 6. Suggested next step

**Merge the branch through a PR and let CI build the image — that is the first real validation.**
Then, in order:

1. Watch the `validate / gate` run: the build stages, the `compose_integration` smoke, and the
   release job's new `pkg:nix/amule@` SBOM gate. Items 1–6 and 10–11 of section 5 are answered by a
   green run; several of the rest are answered by the smoke test.
2. On a **copy** of the live node, run the migration of annex E end to end, and record what it
   actually took. That closes item 20 and is the only item that can destroy data if it is wrong.
3. On the live node after migration: read the image size, `python3 --version`, the closure grep,
   `docker compose exec mulewatch s6-svstat` for each of the three services, and both web surfaces.
   Then exercise port-sync once (item 12) and watch the memory ceiling for a week (item 16).
4. Fold the measured numbers back into the administration runbook's disk/memory sections — they
   currently carry judgement values.

The scope-reduction backlog from `2026-09-13 - handoff - scope reduction to catalog notify
download.md` is unchanged by this work and is still the source of what comes after.

## 7. Inconsistencies knowingly left behind

Found while writing the runbooks, all **outside lot 7's scope** (docs-only), all still true of the
branch as it stands. None of them breaks anything; each is a small, self-contained follow-up.

1. **`AGENTS.md`'s orientation paragraph contradicts its own invariants.** Its "What this is"
   section still reads "one image, one compose service `crawler`, one process", while the
   confinement invariant lower down (rewritten by lot 4) correctly describes one `mulewatch` service
   with three processes. Lot 7 was scoped to the RE2 note only and deliberately did not touch it.
   **Fix this before the next agent reads the file as its orientation.**
2. **`connection.py`'s `_apply_migrations` docstring is stale.** It justifies `temp_store=MEMORY`
   with "in the container that is a 64m tmpfs (`/var/tmp` is not writable under `read_only: true`)".
   `base.compose.yml` now declares neither `tmpfs:` nor `read_only:`, so `/tmp` is the container's
   writable layer. The remedy is still *wanted* (it avoids spilling into that layer, and the OOM
   ceiling reasoning holds), but its stated reason no longer exists.
3. **`port_sync_loop.py`'s module docstring still says "`SetPort` + restart the container".** It is
   a process restart through `S6MuleRestarter` now.
4. **Nothing enforces that `port_sync.enabled: true` only makes sense under the gluetun stack.**
   `_parse_port_sync` has no cross-check and `port_sync_loop` never raises. The old administration
   runbook claimed a fail-fast on incoherent combined settings; no such fail-fast exists, so the
   runbook now describes the real behaviour instead (a degraded loop: Low-ID, backoff, an
   edge-triggered OPERATIONS alert). If the fail-fast was intended, it was never implemented.
5. **A port-sync failure mode worth knowing.** If `/etc/services.d/amuled/supervise/control` never
   appears within the crawler `run` script's bounded 10 s wait, `s6-svperms` is skipped and **every**
   subsequent `s6-svc -r` from `S6MuleRestarter` fails. The tell is the log line
   `mulewatch: … never appeared; amuled restarts will be refused`. This is §5 item 13 seen from the
   operator's side, and it is in the troubleshooting runbook.
