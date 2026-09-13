# Handoff: scope reduction to catalog, notify, download

Date: 2026-09-13
Branch: `feat/scope-reduction`
Spec: `docs/specs/2026-09-13-scope-reduction-catalog-notify-download.md`
Plan: `docs/plans/2026-09-13-scope-reduction.md`

## Current state

`mulewatch` now keeps three verbs and nothing else: **catalog, notify, download**. The gate is
green (ruff, `ruff format`, `mypy --strict` over 270 files, sqlfluff, template-check, and three
package suites at 100% branch coverage: matching 255, crawler 988, vex_guards 74).

164 files changed, 1963 insertions, 9384 deletions.

## What was built

Gone: the `download_verifier` package and its image, clamav and the `freshclam` sidecar, the
bundled `prometheus` and `grafana` containers, the quarantine staging step and its promotion, the
`download` compose profile, two SQLite tables, and four config keys.

Kept, deliberately:

- **The `/metrics` endpoint and the whole observability pipeline.** Only the bundled containers
  left. An operator who wants dashboards points their own Prometheus at the crawler. Port 9090 is
  not published by compose.
- **Both compose stacks**, in their existing roles: `deploy/compose.yaml` (direct) and
  `deploy/gluetun.compose.yml` (VPN + port-sync).
- **`vex_guards`**, now policing one image and eight claims.
- **The ability to run without downloading.** `download.enabled: false` is now the only switch;
  there is no longer an "observer" compose topology. The shipped config defaults to `true`.
- **The disk cap**, unchanged. It is accounted in local.db, never measured on the filesystem, so
  the crawler mounts nothing of the output directory.

The download lifecycle is now `queued -> downloading -> completed | failed`. amuled writes
straight into its own IncomingDir (bind-mounted to `./downloads/incoming`, TempDir to
`./downloads/temp`); completion is still detected from the shared-files list. Nothing moves the
file, and nothing opens it: the "never reads the bytes" invariant is now literally true, since the
crawler does not touch the filesystem at all.

Schema versions: catalog 4 -> 5, local 3 -> 4.

## Learned pitfalls

**SQLite `DROP TABLE` takes its own triggers and indices with it.** The spec originally claimed the
append-only triggers would abort the drop and prescribed dropping them first. That was wrong, and
was disproved empirically on a seeded catalog with `foreign_keys=ON` and `recursive_triggers=ON`.
The migration is one statement. A comment in the file records this so nobody restores the
redundant DDL. The spec was corrected.

**A hand-copied test fixture can hide a schema change completely.** The webui test suite builds its
database from DDL copied by hand into `tests/webui/conftest.py`, not from the real migrations. When
the two tables were dropped, that suite stayed green: the fixture kept creating them. Only
`test_latest_obs_seeks_per_file_instead_of_scanning_observations`, which runs the real migrations,
went red. A fixture that duplicates production DDL is a test that cannot see a migration.

**A dead field's validation outlives its purpose and becomes a fault injector.** `SharedFileEntry`
carried a `name` used only by the promotion step. Removing promotion left the field unread but kept
`_map_shared_file` rejecting any entry whose `EC_TAG_PARTFILE_NAME` was missing or undecodable. The
rejection had its own passing test, so nothing flagged it. The effect: amuled shares the finished
file, the crawler discards the entry, the hash never registers as complete, the download stays
`downloading` forever and its bytes keep counting against the disk cap. Fixed in `76a676d` by
deleting the field and both rejections. **When a consumer disappears, check what still validates
its input.**

**Parallel agents on disjoint files still share seams.** Five lots ran concurrently, each verified
its own scope, and the gate was green before the cross-cutting review found the bug above. The
holistic review is what caught it. Do not skip it.

## Known limits

Both limits this handoff originally listed were fixed the same day: see
`docs/specs/2026-09-13-real-disk-cap-and-lost-download-ttl.md` and commit `7f504b3`. The disk cap
now measures the filesystem, and a row amuled has forgotten for 24 h becomes `failed`. What
remains:

**Nothing deletes anything.** The free-space floor refuses new downloads when the disk is tight; it
never removes a file. `./downloads/incoming` still grows without bound, and that is deliberate.

**A `failed` row blocks re-queuing forever.** `is_downloaded()` is state-blind by design (if
absence means the operator removed the entry, re-adding it automatically would fight them). To
retry one file, delete its row.

**A nascent queue entry contributes 0 to `outstanding`.** amuled reports `size_full == 0` until it
knows the size, so for at most one cycle (30 s) the commitment is under-counted by that file's
size. Bounded and self-correcting.

**`check_image_claims` is a gate with nothing to assert.** No image-family guard survives (the
three that existed covered ffmpeg, nghttp2 and clamav). It stays wired for the first one added.

## Migration of the live node

Breaking, handled by hand once. No compatibility code was written. Verified by execution: the
config parser reads only keys it knows and ignores the rest, so an old `crawler.yml` will not fail
at startup.

1. `docker compose down`
2. Edit `crawler.yml`: remove `download.{staging_dir,quarantine_dir,verifier_url,verify}`
   (optional, they are inert) and **remove `download.disk_cap_bytes`, which no longer exists**.
   Add the three keys that replace it, copying the shipped
   `deploy/config/crawler/crawler.yml`: `min_free_bytes: 10737418240`, `lost_after_seconds: 86400`
   and `output_dir: /data/downloads`.
2b. **Add the crawler's read-only mount** to your compose file, or it cannot measure free space:
   `- ./downloads:/data/downloads:ro`. One `statvfs` on the parent covers `incoming/` and `temp/`,
   which share a filesystem.
3. **Align amuled on the image's own paths.** The shipped stack no longer overrides `IncomingDir`
   or `TempDir`: it binds `./downloads` to `/downloads`, where the image already puts them, and
   sets no amuled variable. A node created before 2026-09-13 carries a hand-edited `amule.conf`
   pointing at `/data/quarantine`, and the image never rewrites that file once it exists, so the
   old value survives every restart. Align it once, with amuled stopped:

   ```bash
   docker compose stop amuled
   # Back up, then point both settings at the image defaults.
   docker compose run --rm --entrypoint sh amuled -c \
     'cp /home/amule/.aMule/amule.conf /home/amule/.aMule/amule.conf.before-align &&
      sed -i "s#^IncomingDir=.*#IncomingDir=/downloads/incoming#;
              s#^TempDir=.*#TempDir=/downloads/temp#" /home/amule/.aMule/amule.conf'
   ```

   Then replace the two old bind mounts in your compose file with the single
   `- ./downloads:/downloads`. Your host layout already matches (`downloads/incoming` and
   `downloads/temp` exist), so nothing needs moving. Verify after restart:

   ```bash
   docker compose exec amuled sh -c 'grep -E "^(Incoming|Temp)Dir" /home/amule/.aMule/amule.conf'
   ```

   Do not skip this: with the old `amule.conf` and the new single bind, amuled would write to
   `/data/quarantine`, which is no longer mounted, and finished files would land in the container
   layer.
4. Pull the new image and start **without** `--profile download`. The profile no longer exists.
5. Both migrations run at startup. Legacy `quarantined` rows are rewritten to `completed`.

**What you lose in the process:** `_handle_completions` now skips rows already in `COMPLETED`
(without that, a finished file stays in amuled's shared list forever and would re-fire the
community notification every cycle). So any download currently sitting at `completed`, plus the
migrated `quarantined` rows, are terminal and will never emit their `DownloadCompleted`
notification. Count them before migrating if you care:

```sql
SELECT state, COUNT(*) FROM downloads GROUP BY state;
```

## NOT validated against real hardware

- ~~The compose stacks have never been started.~~ **Done, 2026-09-13.** The operator ran
  `cd packages/crawler && uv run pytest -m compose_integration --no-cov` from a real shell: 4
  passed in 23s. That covers the image build, the rendered service sets on both entry points, and
  the crawler staying up and serving its webui. The remaining integration markers still need a real
  amuled.
- **The migrations have never run against the live node's databases.** They were exercised against
  synthetic databases built from the real migration files, including a version-3 local.db carrying
  a `quarantined` row. The live catalog holds no verdict rows (confirmed with the operator), so the
  catalog drop should be inert there.
- **The completion path has not been observed end to end** since the promotion step was removed.
  The `_map_shared_file` fix in particular changes what amuled entries are accepted, and only unit
  tests cover it.
- `ec_integration`, `orchestration_integration` and `download_integration` **cannot run on this
  machine at all**, and that is not a sandbox limitation: attempted from a real shell on
  2026-09-13, every one errors at fixture setup with `failed to create endpoint
  testcontainers-ryuk-... on network bridge: failed to add the host (veth...) <=> sandbox (veth...)
  pair interfaces: operation not supported`. It hits the Ryuk container and the amuled container
  alike, with or without `TESTCONTAINERS_RYUK_DISABLED=true` (already established in the
  2026-06-15 and 2026-09-11 handoffs: do not retry it). `compose_integration` passes because
  compose builds its own networks instead of using the default `bridge`. These three need a host
  whose Docker can create veth pairs.

## Suggested next step

Bring the stack up on the real node and watch one download go from queued to completed with its
notification. That is the only remaining way to validate the completion path, since the
testcontainers suites cannot run here. Remember the two new migration steps: the three replacement
config keys, and the crawler's read-only `./downloads:/data/downloads:ro` mount.

After that, `v2.0.0` is the version to tag (breaking: schema, compose topology, config schema, one
distribution removed).
