# Testing guide: mulewatch

This guide describes **how to run the integration suites** (the heavy ones, deselected by default),
their **exact prerequisites**, and **what to expect** on output. It complements the
[deployment runbook](runbooks/deployment.md): the runbook explains how to make the stack run, this
guide explains how to **validate** it.

Audience: **local dev and CI**. Not for operators (who have no reason to run the test suites).
Everything below is **extracted from the real code** (test files, `pyproject.toml`, compose files).
Where a prerequisite cannot be checked in the code, it is marked "to be confirmed".

> **The "real transfer" e2e suite was abandoned** (and its scaffolding deleted from the repo). The
> reason: making a real `amuled` report a finished download would mean orchestrating and
> reverse-engineering third-party tools (`amuled`, `ed2kd`), which mostly validates trusted
> third-party behaviour rather than our code (the same argument as for the gluetun port-forwarding
> layer). Completion detection stays covered by **unit tests** plus the deployment constraints
> documented in `docs/reference/2026-06-17-amuled-completion-behavior.md`.

---

## 1. Overview: the test pyramid

The project has **two levels**:

1. **The unit gate** (run by default, **100 % branch coverage** enforced). This is what the pre-push
   hook and CI check, through a single source of truth in `pyproject.toml`
   (`[tool.poe.tasks]`):

   ```bash
   uv run poe check     # the full gate: lint-all + test (what pre-push and CI run)
   uv run poe test      # the 3 unit suites alone, each in its own process
   ```

   > The `test` task stays **per package**: it runs `pytest` with `cwd = packages/<pkg>` for each of
   > the 3 packages, in separate processes, to keep coverage isolated. A bare `uv run pytest` from
   > the repo root is **not** the gate (the root has no pytest config, and a root `conftest.py`
   > neutralises collection, giving `exit 5`).

   Each package's `addopts` **deselect** every integration marker
   (`-m "not ec_integration and not …"`), so the gate never runs them, and they are excluded from
   coverage measurement.

2. **The integration suites** (deselected by default, run **on demand**). Each one carries a pytest
   **marker**. Run them one at a time with `--no-cov` (otherwise the package-wide 100 % threshold
   makes a focused run "fail" even when the tests pass):

   ```bash
   ( cd packages/<pkg> && uv run pytest -m <marker> --no-cov )
   ```

   These suites need external resources (Docker). **They do not run in a sandbox without full
   network and Docker access**: run them on a real machine.

---

## 2. Marker summary

| Marker | Package | What it validates | Docker? | Other prerequisites | Command |
|---|---|---|---|---|---|
| `ec_integration` | crawler | The EC adapter (auth, network status, search cycle, get/set port) against a real amuled | **Yes** (you start it) | An amuled you provide, pointed at by `MULEWATCH_TEST_EC_HOST` (§3.0) | `( cd packages/crawler && uv run pytest -m ec_integration --no-cov )` |
| `download_integration` | crawler | The EC mechanics of downloading (`add_link` into the download queue) against a real amuled | **Yes** (you start it) | Same amuled as above (§3.0) | `( cd packages/crawler && uv run pytest -m download_integration --no-cov )` |
| `orchestration_integration` | crawler | A full crawl loop (one cycle plus a bounded shutdown) against a real amuled | **Yes** (you start it) | Same amuled as above (§3.0) | `( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )` |
| `compose_integration` | crawler | Smoke e2e of the assembled docker compose stack (no VPN): wiring only | **Yes** (compose v2) | docker compose v2; one image build | `( cd packages/crawler && uv run pytest -m compose_integration --no-cov )` |

---

## 3. One section per marker (lightest to heaviest)

### 3.0 The amuled the three EC suites need (start it yourself)

`ec_integration`, `download_integration` and `orchestration_integration` all talk to the SAME
`amuled`, and **none of them starts it**: the caller provides one and points the suites at it with
three environment variables. They used to start their own container through `testcontainers`, which
is unusable on hosts where Docker cannot create a veth pair on its default `bridge` network (the
failure mode we hit for months: `failed to add the host (veth...) <=> sandbox (veth...) pair
interfaces: operation not supported`). A plain `docker run` with a published port works everywhere.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `MULEWATCH_TEST_EC_HOST` | **Yes** | none | Host of the EC server. **Absent, and the three suites SKIP** with a message repeating the command below. |
| `MULEWATCH_TEST_EC_PORT` | No | `4712` | EC port. |
| `MULEWATCH_TEST_EC_PASSWORD` | No | `indexer-ec-test` | EC password (`GUI_PWD` of the daemon). |

Start a throwaway daemon, wait for its EC server, run the suites, throw it away:

```bash
docker run -d --rm --name mulewatch-test-amuled \
    -e GUI_PWD=indexer-ec-test -p 4712:4712 ngosang/amule:3.0.0-1
until docker logs mulewatch-test-amuled 2>&1 | grep -q 'listening on 0.0.0.0:4712'; do sleep 2; done

export MULEWATCH_TEST_EC_HOST=127.0.0.1
export MULEWATCH_TEST_EC_PORT=4712
export MULEWATCH_TEST_EC_PASSWORD=indexer-ec-test
( cd packages/crawler && uv run pytest -m "ec_integration or download_integration or orchestration_integration" --no-cov )

docker rm -f mulewatch-test-amuled
```

The daemon is stateful (it persists preferences and the download queue in its container), so the
suites are only reliably repeatable against a FRESH one: recreate it rather than reusing a
long-lived container.

---

### 3.1 `ec_integration` (crawler, **Docker required**)

**What it proves.** The EC adapter talks to a **real `amuled`**: the auth hash formula is validated
against the daemon, auth fails with a wrong password, the network status decodes, and the full
search, progress, fetch, stop cycle runs. The second file (`test_amuled_preferences.py`) validates
the **listen-port get/set** (High-ID port-sync): `get_listen_port()` reads a plausible port, and the
`set -> get` round trip returns the value that was set.

**Exact prerequisites.** An amuled started per **§3.0** and `MULEWATCH_TEST_EC_HOST` exported.
Without it the suite skips (it never fails on absence, and never silently passes).

> The ephemeral container **has no eD2k network access**: a search may return `EC_OP_FAILED` or
> empty results. The tests **tolerate that explicitly**: what is validated is the **request/response
> cycle**, not the richness of the results.

**Command.**
```bash
( cd packages/crawler && uv run pytest -m ec_integration --no-cov )
```

**Expected.** 6 tests passed (4 in `test_amuled_ec.py` + 2 in `test_amuled_preferences.py`), no
skips. `EC_OP_FAILED` is tolerated internally (the test still passes).

---

### 3.2 `download_integration` (crawler, **Docker required**)

**What it proves.** The EC mechanics of downloading against a real `amuled`: `add_link` is accepted
and the link shows up in `download_queue` with a readable status. This is the **regression guard**
for the partfile-hash decoding bug (the hash lives in the `EC_TAG_PARTFILE_HASH 0x031E` child tag,
not in the parent's own value), hence a realistic hash and size (~700 MiB), **never** the MD4 of the
empty file (which amuled treats as instantly complete and does not list).

**Exact prerequisites.** Same as `ec_integration` (§3.0).

**Command.**
```bash
( cd packages/crawler && uv run pytest -m download_integration --no-cov )
```

**Expected.** 2 tests passed (`test_add_link_then_appears_in_download_queue` and
`test_shared_files_round_trips`). Real completion is not
reachable (no eD2k sources): only the add_link, queue, status cycle is validated.

---

### 3.3 `orchestration_integration` (crawler, **Docker required**)

**What it proves.** A real `CrawlerApp` (real `AmuleEcClient` + real SQLite databases on `tmp_path`)
runs **one full cycle** against the provided `amuled` then **shuts down cleanly** within a 120 s
`wait_for`. The key assertion: the cycle index advanced (`read_cycle_index() >= 1`), proving a cycle
really completed.

**Exact prerequisites.** Same as `ec_integration` (§3.0). The test loads the matcher config from
the single source of truth, `deploy/matcher.yml`, and runs with the webui disabled
(its bind is a fixed `0.0.0.0:8080`, which would collide with whatever already listens there).

**Command.**
```bash
( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )
```

**Expected.** 1 test passed (`test_real_loop_runs_one_cycle_and_stops`). Search results may well be
empty: what is validated is the **loop** (startup, search, cataloguing, bounded shutdown).

---

### 3.4 `compose_integration`: the smoke stack (crawler, **Docker + compose v2 required**)

**What it proves.** The **assembled** `docker compose` stack — **one** service since 2026-09-16,
holding the crawler, amuled and amuleweb under s6 — starts and wires itself correctly. **No content
byte is ever downloaded** (amuled has neither an eD2k server nor a VPN; only its EC server is
exercised). Four things:
1. `docker compose build` succeeds (the image builds);
2. the container stays `Up`, turns **`healthy`**, `s6-svstat` reports all three services up, and the
   in-process webui answers `/health` (polled through `docker compose exec`, so no host port is
   needed);
3. a file amuled shares and that has left its queue is recorded `completed` by the crawler — the
   real EC path over loopback, against the real amuled of the shipped image;
4. both deployment entry points render with `docker compose config`, and the rendered topology is
   asserted: one `mulewatch` service, the VPN stack adding only `gluetun`, **nothing** left of
   `crawler` / `amuled` / `docker-proxy`, and **no named volume** anywhere.

The smoke **deliberately** exercises the real ownership path: state lives in **bind mounts** under a
throwaway `SMOKE_STATE` directory created as the invoking user, whose own uid/gid are passed as
`PUID`/`PGID`. The container's root PID 1 chowns those mount points and every service then drops to
the `amule` user. A regression there shows up as `unable to open database file`.

**Exact prerequisites.**
- **Docker** + **docker compose v2** (the test drives `docker compose …` through `subprocess`).
- Builds run **from the repo root** (the test pins `cwd = repo root` and `--project-directory`).
- Every interpolated variable is **stubbed by the test itself**: the four the image hard-requires
  (`PUID`, `PGID`, `AMULE_EC_PASSWORD`, `WEBUI_PWD` — without them the startup one-shot exits 1 and
  the container dies), plus gluetun's (`WIREGUARD_PRIVATE_KEY`, `SERVER_COUNTRIES`), which compose
  interpolates at parse time even when gluetun is not part of the stack. Ports and the image tag are
  written directly in `deploy/`'s compose files, so they interpolate nothing. **Nothing for
  the operator to set.**
- Compose files used: `tests/smoke/compose.yaml` (standalone) plus `deploy/compose.yml` and
  `deploy/gluetun.compose.yml` for `test_entrypoint_config_renders`; the smoke configs live under
  `tests/smoke/`.
- The test imports **no** `mulewatch` module (this preserves the package's 100 % branch coverage).

**Command.**
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
```

**Expected.** **5 tests passed** locally: `test_build_succeeds`,
`test_one_container_supervises_the_three_services`,
`test_a_file_amuled_shares_is_recorded_completed`, and the 2 parametrized
`test_entrypoint_config_renders` cases (`compose` and `gluetun`). In CI the image is prebuilt and
`IMAGE_TAG` is set, so `test_build_succeeds` **skips** (4 passed, 1 skipped) and the `up` reuses the
prebuilt image. Tear-down is a `docker compose down -v` plus the throwaway state directory, in a
`finally`. Budget several minutes (the build and the up sit under 900 s timeouts).

> **Never executed.** As of 2026-09-16 there is no container runtime on the development machine, so
> this suite — and the image it builds — has not been run once. See the
> [single-container handoff](handoffs/2026-09-16%20-%20handoff%20-%20single%20container%20with%20embedded%20aMule.md),
> section 5.

---

## 4. Machine prerequisites (installable summary)

To be able to run **every** suite:

- **Docker** + **docker compose v2**. The EC suites talk to an amuled **you** start (§3.0, image
  `ngosang/amule:3.0.0-1`); the compose suite drives `docker compose` directly.
- A **`.env`** (copied from `deploy/.env.example`) for **manual** compose commands:
  `WIREGUARD_PRIVATE_KEY`, `SERVER_COUNTRIES`, `AMULE_EC_PASSWORD`. Note that the
  `compose_integration` test **stubs these itself**, so the `.env` is not required to run it.

---

## 5. CI integration

Already in CI:

- `.github/workflows/validate.yml` is the reusable **gate**, called by `pr.yml` (on pull requests)
  and by `release.yml` (on a tag push). Its jobs:
  - `lint`: `uv run poe lint-all` (ruff, format, mypy, sqlfluff, template check);
  - `test`: `uv run poe test` (the 3 per-package unit suites, 100 % branch each);
  - `build-and-verify`: one job **per architecture on its native runner** (`amd64` on
    `ubuntu-latest`, `arm64` on `ubuntu-24.04-arm`). Each builds the crawler image and then runs
    **`compose_integration`** against that locally built image (`IMAGE_TAG=ci-<sha>`);
  - `ec-integration`: starts one `ngosang/amule:3.0.0-1` with `docker run -p 4712:4712`, waits for
    its EC log line, then runs **`ec_integration`, `download_integration` and
    `orchestration_integration`** in one pytest call against it. Runner Docker can create a veth,
    so the network failure that blocks these suites on some developer machines does not apply;
  - `gate`: the single aggregation check required by branch protection.
- `.github/workflows/pr.yml` also runs the `vex-checks` job (`poe vex-source-claims` +
  `poe vex-claim-coverage`).
- `.github/workflows/grype-scan.yml` scans the published image daily and reports into Code scanning.

Every marker now runs in CI. The only suites still absent are the ones belonging to other
packages (see their sections above).

The `ec-integration` job runs on `ubuntu-latest` only, not on both arches: it exercises the EC
protocol code, which is pure Python and architecture-independent. The arch-sensitive artefact is
the crawler image, and that is what `build-and-verify` covers on both runners.

---

## 6. Diagnostic tools (measurement, dev)

One-off tools aimed at the **developer** (measurement and diagnosis, not operation):

- **EC richness probe**: `uv run python -m mulewatch.tools.ec_probe --all-tags …` dumps **every** raw
  tag of a real search result (mapped and unmapped). It is what measured the fill rate of the fields
  EC exposes. This is a **diagnostic** tool: a deployment does not need it (see the finding "EC
  exposes no media metadata on search results").

---

## 7. See also

- [Deployment runbook](runbooks/deployment.md): to **deploy and run** a node (it points back here
  for in-depth validation).
- [Documentation index](README.md): routing by audience (operator / developer / history).
