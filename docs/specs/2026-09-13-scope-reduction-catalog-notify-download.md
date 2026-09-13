# Scope reduction: catalog, notify, download

Date: 2026-09-13
Status: proposed
Supersedes (in part): `2026-06-13-verification-pipeline-design.md`, `2026-06-14-analysis-design.md`,
`2026-06-15-clamav-design.md`, `2026-06-15-observability-design.md` (Prometheus/Grafana deployment
sections only), `2026-06-20-deploiement-exemples-design.md`, `2026-06-29-simplification-deploiement-design.md`

## 1. Intent

`mulewatch` keeps three verbs and drops everything else: **catalog, notify, download**.

Out of scope as of this spec: content verification (the `download_verifier` service, ffprobe,
puremagic type sniffing, clamav), the bundled metrics stack (the `prometheus` and `grafana`
containers), and the quarantine staging step.

The one capability deliberately kept from the previous design: **downloading can still be turned
off** (`download.enabled: false`). It is now a config flag only. There is no longer an
"observer" compose topology.

## 2. What goes away

| Thing | Where | Note |
|---|---|---|
| `download_verifier` package | `packages/verifier/` | deleted, including its Dockerfile and image |
| Verification consumer | `application/run_verification_cycle.py`, `HttpContentVerifier`, ports `content_verifier.py` / `verifier_errors.py` | deleted |
| Quarantine | `adapters/quarantine_fs.py`, port `quarantine.py`, states `QUARANTINED` | deleted |
| `verifier` + `freshclam` services | `deploy/base.compose.yml`, `tests/smoke/compose.yaml` | deleted, with the `clamav-db` volume and the `verify-internal` network |
| `prometheus` + `grafana` services | `deploy/base.compose.yml` | deleted, with `prometheus-data` / `grafana-data` volumes and `deploy/config/{prometheus.yml,grafana/}` |
| `download` compose profile | both compose stacks, smoke stack | deleted; `docker-proxy` becomes unconditional in the gluetun stack |
| Verification tables | `file_verifications` (catalog.db), `verification_tasks` (local.db) | dropped by migration |
| Config keys | `download.{staging_dir,quarantine_dir,verifier_url,verify}` | removed from the schema |
| VEX claims for the verifier image | `security/verifier.vex.openvex.json` | deleted |
| Test markers | `verify_integration`, `analysis_integration` | deleted |

## 3. What stays

- **The `/metrics` endpoint stays in the crawler.** `PrometheusSink`, the `telemetry` port, the
  `prometheus_client` dependency and `observability.metrics` are untouched. Only the bundled
  Prometheus and Grafana *containers* go. An operator who wants dashboards points their own
  Prometheus at the crawler.
- **Both compose stacks stay as they are:** `deploy/compose.yaml` (direct, no VPN) and
  `deploy/gluetun.compose.yml` (VPN + port-sync). The dual-stack arrangement is not revisited.
- **`vex_guards` stays as it is**, now policing a single image.
- **Notification is unchanged.** `DownloadCompleted` already reports to `Audience.COMMUNITY` with
  "download completed: {target_id}". That was, and remains, the notification that matters.
- **The disk cap stays**, unchanged (see §6).
- The matching engine, `targets.yml`, `matcher.yml`, the `merge` and `compact` tools, port-sync
  and the High-ID logic: untouched.

## 4. The download lifecycle after this change

Before:

```
queued -> downloading -> completed -> (promote: os.replace to quarantine/<hash>)
       -> quarantined -> (verification queued) -> verdict recorded
```

After:

```
queued -> downloading -> completed
       -> failed
```

`completed` is detected exactly as before, from amuled's shared-files list (a positive signal:
amuled auto-shares a finished file, so its presence there means the file exists at its final
path). amuled writes straight into its own `IncomingDir`; nothing moves the file afterwards, and
nothing opens it.

`DownloadState` loses `QUARANTINED`. `_TERMINAL_STATES` becomes `{completed, failed}`.

Events removed: `PromotionFailed`, `VerificationCompleted`, `VerifierUnavailable`,
`VerificationQueueDepthSampled`, and their metrics (`PROMOTION_FAILURES`, `VERIFICATIONS`,
`VERIFIER_UNAVAILABLE`, `VERIFICATION_QUEUE_DEPTH`).

### Consequence, stated plainly

EC exposes no media metadata on search results: filename, size, hash and source count, nothing
else. The verifier was the only component that ever knew a file was a 24-minute H.264 stream, or
that its declared extension matched its actual content. After this change the catalog holds only
what eD2k search reports, and a completed file lands in `IncomingDir` without anything having
inspected it. Judging whether a download is really the episode is a manual step, performed by the
operator on the output directory.

## 5. Invariants

Preserved, and in fact strengthened: **the crawler never reads the bytes of a downloaded file.**
Previously it performed one metadata-only `os.replace`; now it touches the filesystem not at all.

The package boundary (`mulewatch` never imports `download_verifier`) becomes trivially true: the
package is gone, along with the contract test that crossed it.

`deploy/config/` remains the operator-owned single source of truth.

## 6. The disk cap

`download.disk_cap_bytes` is kept **unchanged and unmoved**. It is an accounting cap held in
local.db, not a filesystem measurement: the download policy admits a candidate only when
`committed_bytes + file_size <= disk_cap`, where `committed_bytes` is the sum of the sizes of
non-terminal downloads. There is no `statvfs` call anywhere in the codebase.

Therefore the crawler needs no mount of the output directory, and gets none.

**Known limit, pre-existing and unchanged by this work:** because terminal downloads leave the
accounting, this cap bounds bytes *in flight*, never total disk usage. Completed files accumulate
without bound. This was already true when promotion moved files to quarantine on the same volume.
Bounding total usage would require a real filesystem measurement and is not in scope here.

## 7. Persistence

Two new migrations.

`catalog/0005_drop_file_verifications.sql`: a single `DROP TABLE file_verifications`.

An earlier draft of this spec claimed the two append-only triggers would abort the drop, and
prescribed dropping them and the index first. That was wrong, and was disproved empirically during
implementation on a seeded catalog with `foreign_keys=ON` and `recursive_triggers=ON`: SQLite's
`DROP TABLE` removes the table's own triggers and indices with it. Explicit `DROP TRIGGER` and
`DROP INDEX` statements would be redundant DDL. The migration file carries a comment saying so, so
that nobody restores them.

Confirmed with the operator: the live node's catalog holds no verdict rows, so nothing of value is
destroyed.

`local/0004_drop_verification_tasks.sql`:
- drop `verification_tasks` and its two indices,
- **normalize legacy download rows**: `UPDATE downloads SET state = 'completed' WHERE state =
  'quarantined'`.

That `UPDATE` is not cosmetic. `download_repository.py` reads state back through
`DownloadState(row[1])`; once the enum member is gone, a surviving `quarantined` row raises
`ValueError` at read time and breaks the download cycle on the live node. Both values are terminal
for the cap, so the rewrite changes no behaviour.

## 8. Configuration

`deploy/config/crawler/crawler.yml`:
- `download.enabled` becomes `true` (downloading is now the default posture),
- `staging_dir`, `quarantine_dir`, `verifier_url` and the `verify:` block are removed,
- `port_sync.enabled` **stays `false`**: the file is shared by both compose stacks and port-sync
  only works under gluetun, which supplies `docker-proxy` and the control URL. The gluetun runbook
  keeps telling the operator to flip it.

The config parser reads keys it knows and ignores the rest (verified by execution against the live
node's config). Removing keys from the schema therefore cannot break an existing deployment at
startup.

## 9. Deployment

`deploy/compose.yaml` and `deploy/gluetun.compose.yml` keep their respective roles. Changes:

- no `download` profile anywhere; every remaining service starts unconditionally,
- amuled's output moves to **bind mounts**, matching what the operator already runs:
  `./downloads/incoming` as `IncomingDir` and `./downloads/temp` as `TempDir`. The completed media
  is the point of the tool, so it belongs in a directory the operator can open, not in a named
  volume.
- the `quarantine`, `clamav-db`, `prometheus-data` and `grafana-data` volumes and the
  `verify-internal` network are removed; the crawler keeps `catalog-db` and `local-db` only,
- `deploy/.env.example` loses `GRAFANA_PWD` and `GRAFANA_PORT`.

`tests/smoke/` becomes a single profile-less stack: amuled plus crawler. The
`download.enabled: false` case is covered by composition unit tests, not by a second compose
topology.

## 10. CI and supply chain

- `release.yml` builds and signs one image instead of two; `publish-manifest` follows.
- `grype-scan.yml` scans one image.
- `validate.yml` runs the gate over three packages instead of four.
- `dependabot.yml` loses the verifier entries.
- `security/verifier.vex.openvex.json` is deleted; `vex_guards` and the crawler claims stay.
- `SECURITY.md` is rewritten for a single image.

## 11. Documentation

Living docs are rewritten to describe the current state: `README.md`, `docs/architecture.md`,
`docs/testing-guide.md`, the three runbooks, `SECURITY.md`, `AGENTS.md`, `docs/legal-and-privacy.md`,
`docs/README.md`.

The dated journal (`docs/specs/`, `docs/plans/`, `docs/handoffs/`) is **not rewritten and not
deleted**. Entries made entirely obsolete by this change get a banner at the top:

```markdown
> **Obsolete since 2026-09-13.** The subsystem described here left the project's scope.
> See `docs/specs/2026-09-13-scope-reduction-catalog-notify-download.md`.
```

The journal records what was true on its date; a banner says it is no longer true without
falsifying the record.

## 12. Migration of the live node

A breaking change, handled by hand once. No compatibility code is written.

Procedure, to be repeated in the handoff:

1. `docker compose down`
2. Edit `crawler.yml`: drop `download.{staging_dir,quarantine_dir,verifier_url,verify}`. Optional,
   since unknown keys are ignored, but it keeps the file honest.
3. Confirm `amule.conf` still points `IncomingDir` at `/data/quarantine` (the container path
   already bound to `./downloads/incoming`). Renaming that container path is cosmetic and is not
   required.
4. Pull the new image and start without `--profile download`.
5. The two migrations run at startup; the legacy `quarantined` rows are rewritten to `completed`.

## 13. Version

`v2.0.0`. Breaking: schema, compose topology, config schema, and a removed distribution.

## 14. Out of scope

- Any replacement for media inspection, in-process or otherwise. Reintroducing ffprobe inside the
  crawler was considered and rejected: it would violate the "never reads the bytes" invariant and
  put an ffmpeg dependency back in the production image.
- Redesigning the webui. Verification columns and sections are removed from the seven files that
  carry them; nothing else is touched.
- Revisiting the dual compose stack, the matching engine, or the port-sync design.
