# Handoff — amuleapi migration, lot 1

- Date: 2026-09-22
- Branch: `feat/amuleapi-migration` (not merged at the time of writing, not pushed, not tagged)
- Spec: `agents/specs/2026-09-22-amuleapi-migration.md` (APPROVED, D1 to D7)
- Release this prepares: **`v3.0.0` — breaking** (one variable to rename in `.env`, one key in
  `crawler.yml`), migration page written at `docs/migration-2x.md`
- Lot 1 is §3 + §4 of the spec, one branch, one PR. Lots 2 to 5 stay in `BACKLOG.md`.

---

## 1. Where the project stands

The crawler no longer speaks EC. It drives `amuled` through **amuleapi**, the REST daemon aMule
3.1.0 ships, and the hand-written binary EC client is deleted. The process tree gained a level and
lost a supervised service:

```
PID 1  entrypoint.sh (root)
         └─ amule-config.py        one-shot: create the `amule` user, chown the mount points,
         │                         write amule.conf (now with [AmuleApi]), then
         │                         `amuleapi --set-admin-pass` as the amule user
         └─ exec s6-svscan /etc/services.d
              ├─ amuled            aMule 3.1.0                             (setpriv → amule)
              │    └─ amuleapi     REST + web UI on 4711, started by amuled in OnInit
              └─ mulewatch         the crawler + the in-process webui, 8080 (setpriv → amule)
```

- **`WEBUI_PWD` is gone, replaced by `AMULE_API_PASSWORD`** (D6, no shim). It is amuleapi's admin
  password: it guards the web UI on 4711 **and** it is how the crawler authenticates.
- **`crawler.yml`'s `amule_ec_password` became `amule_api_password`.** `AMULE_EC_PASSWORD` stays in
  `.env`: EC is now the internal link between amuleapi and amuled.
- **The published ports do not move.** 8080 (catalog) and 4711 (aMule's UI, amuleapi's now) (D2).
- **The media columns start filling.** `media_length_sec`, `bitrate_kbps` and `codec` have been
  `None` since `catalog/0001_initial.sql`; search results now carry them when the responding server
  advertises them.

## 2. What landed, commit by commit

| Commit | What |
|---|---|
| `a1c6def` (pre-existing) | `amule.nix` → aMule 3.1.0, `BUILD_AMULEAPI=ON`, amuleweb dropped. Built and run for real with the host's nix (spec §9). |
| `5b1e50f` | Dockerfile exports `amuled amuleapi ed2k`; `services.d/amuleweb/` deleted; `amule-config.py` writes `[AmuleApi] Enabled=1 / BindAddress=0.0.0.0 / HttpPort=4711` and runs `amuleapi --set-admin-pass` through `setpriv`; `amule-config.test.sh` covers all of it including the negative path. |
| `4d5c7f8` | `adapters/mule_api/` (client, mapping, errors) behind the unchanged ports; `adapters/mule_ec/`, `tools/ec_probe.py`, `tools/download_probe.py` and their suites deleted; config, composition, deploy and smoke renames; the `ec_integration` marker became `api_integration`. |
| `73a1a94` | CI: the three daemon-facing suites moved into `build-and-verify` and now run on both arches. |
| `24e2270` | `docs/` (French): the new `migration-2x.md` plus every page that named amuleweb, `WEBUI_PWD` or the EC protocol; nav updated. |
| `4ca0288` | `AGENTS.md`: subsystem table, invariants, and the gotchas section rewritten around amuleapi. |

## 3. Where the implementation diverged from the spec

The spec is an approved record, not a live document, and it was **not** edited. Reality wins; this
is the reconciliation.

1. **The adapter is three files, not four.** §4.1 sketches `client.py / auth.py / mapping.py /
   errors.py`. `auth.py` would have held one function that needs `client.py`'s send-and-decode
   helpers, and the 401 rule it names lives in `_request` by construction. The login is
   `AmuleApiClient._login`. No decision (D1 to D7) is touched.
2. **`set_listen_port` writes `udp_port` as well as `tcp_port`.** §6's mapping table names only
   `tcp_port`; the EC adapter moved both, and port-sync exists so the one forwarded port is the one
   amuled binds. Dropping the UDP half would have been a silent behaviour change.
3. **The `ec_integration` marker was renamed `api_integration`**, and the env vars with it
   (`MULEWATCH_TEST_API_{HOST,PORT,PASSWORD}`, default port 4711). §4.4 says the suites are
   "rewritten against HTTP" without naming the marker; leaving "ec" in it would have been a lie.
4. **CI had to move.** The old `ec-integration` job started `ngosang/amule:3.0.0-1`, which cannot
   serve amuleapi (it exists only in 3.1.0). The three suites now run inside `build-and-verify`,
   against a throwaway container started from the image that job just built, on both arches. This
   is outside the scope the lot was given, but leaving it would have failed the gate on the PR.
5. **A search is never freed.** The EC `start_search` wiped the previous results; `POST /search`
   does not, and the adapter does not `DELETE` the old id either. amuled's 20-entry ring evicts them
   on its own, and at one search every few minutes that costs nothing. It becomes a scheduler
   concern the moment lot 2 lands (spec §7.2).

## 4. Pitfalls learned (the ones worth a reader's time)

- **aMule 3.1.0 ships its own API reference, and it is the source of truth.** `docs/api/
  REFERENCE.md` in the source tree (3331 lines) documents every endpoint, every error code and
  every paging rule. The built source tree is a nix store path; `nix-store --query --outputs` on the
  `source.drv` of the `amule` derivation finds it. The static web frontend's `js/api.js` is a second
  reading of the same contract, from the client side.
- **`limit` defaults to 100 on every list route**, and omitting it returns the first hundred rows
  with no error and no marker. The fake server in `tests/adapters/mule_api/api_fakes.py` reproduces
  that default on purpose, so a client that forgets `limit` fails the 501-row fixtures.
- **`offset` is a position, not an anchor.** A row deleted below the cursor slides one row past the
  window, and nothing ever reports it again. The adapter keyset-pages on `hash`, which all three
  list routes we read accept as their identity column.
- **A `401` is terminal, and retrying it is how you get banned** (30 rejected tokens in 60 s → a
  300 s per-IP lockout). One re-login per request, never a loop. Note that a `401` also means the
  request did *not* execute, which is why re-sending a `POST /downloads` after re-login cannot
  double-queue a link.
- **`ok` is inside the 2xx.** `POST /downloads` is a bulk route: a refused link comes back as
  `202`/`207` with `results[0].ok == false`. Branching on the status code alone would swallow it.
- **`media` is an advertisement, not a measurement.** Server-advertised, `null` on most global and
  Kad hits, and free to contradict the file. Lot 3 (duration rules) waits for a measured fill rate
  on the real node, which is exactly why it is still in `BACKLOG.md`.
- **amuleapi's console output is not in `docker logs`.** amuled starts it with redirected pipes it
  never drains; amuleapi writes `amuleapi.log` into the config dir, which is the bind-mounted
  `deploy/amule/`. `docs/operate.md` says so now. If a node ever wedges with amuleapi alive but
  unresponsive, a full 64 KiB pipe is the first hypothesis (spec D1).

## 5. What is NOT validated against real hardware

The gate is green (`uv run poe check`: 916 + 255 + 66 tests, 100 % branch coverage per package,
ruff, mypy over src and tests, sqlfluff, templates) and `uv run poe docs-build --strict` passes. All
of that is host-side. What follows has never run:

- **The image itself.** At the time of writing a `docker build` of `packages/crawler/Dockerfile` was
  started on the development machine; whatever its outcome, no node has run this image.
- **`amuleapi --set-admin-pass` from the root one-shot, through `setpriv`, as the `amule` user**,
  and the resulting `amuleapi-passwords` file mode on a bind mount. The shell test stubs `setpriv`
  and only asserts the argv.
- **The ephemeral EC token handoff** between amuled and amuleapi when both run as the same
  unprivileged user with the config dir on a bind mount.
- **The whole adapter against a real amuleapi.** Every unit test runs against
  `httpx.MockTransport` and a fake modelled on the upstream reference. The three daemon-facing
  suites (`api_integration`, `download_integration`, `orchestration_integration`) were rewritten but
  have not been run: per earlier sessions they do not work in this environment.
- **`no-new-privileges` alongside `setpriv`**, inherited from the single-container work
  (`agents/specs/2026-09-16-single-container-embedded-amule.md` §13). Unchanged by this lot, and
  still unvalidated.
- **The real fill rate of `media`** on Keroro searches. Unknown, and it is what lot 3 waits on.

## 6. Suggested next step

Build the image and run the smoke stack
(`cd packages/crawler && uv run pytest -m compose_integration --no-cov`), then the three
daemon-facing suites against a throwaway container from that image, exactly as
`docs/contributing/testing.md` §3.0 now describes. That closes most of section 5 in one sitting.

Then open the PR, let `validate / gate` run on both arches, merge with a squash, and tag
`v3.0.0 - amuleapi migration`. The live node then needs the two edits of `docs/migration-2x.md`.

After that, `BACKLOG.md` is the way in: lot 2 (multi-search, Kad "more", disk space) is the one the
API opens up first, and lot 5 (judge a hash on the union of its names) is a bug this migration's
analysis uncovered rather than caused.
