# Real disk cap and lost-download TTL

Date: 2026-09-13
Status: approved
Follows: `2026-09-13-scope-reduction-catalog-notify-download.md`

Two corrections to the download loop, both consequences of the scope reduction. They ship on the
same branch: they touch the same subsystem and the same config file, and splitting them would mean
migrating the live node twice.

## 1. The disk cap measures the disk

### Today

`download.disk_cap_bytes` sums the DECLARED size (`size_bytes`, taken from the search result) of
every non-terminal download and refuses a candidate when the sum would exceed the cap. Two flaws:

- it is an estimate, not a measurement. Nothing checks the disk.
- terminal downloads leave the accounting, so completed files are invisible to it. The cap bounds
  bytes in flight, never disk usage, and `downloads/incoming` grows without bound.

### After

The admission rule becomes:

```
free       = shutil.disk_usage(<output dir>).free
outstanding = sum(entry.size_full - entry.size_done for entry in amuled's queue)
admit candidate  iff  free - outstanding - candidate.size_bytes >= min_free_bytes
```

Both terms are measured, not declared. `free` is one stdlib call, no tree walk. `outstanding` is
amuled's real progress: `DownloadEntry` already carries `size_done` and `size_full` per queue
entry, read from the cycle's existing queue snapshot, so there is no extra EC round trip.

Free space alone would be wrong: ten downloads totalling 20 GB with 2 GB written look cheap to
`statvfs`, which knows nothing of the 18 GB still coming. `outstanding` is what models that
commitment, and it models it from reality rather than from a declared size.

**Config:** `download.disk_cap_bytes` is replaced by `download.min_free_bytes` (default 10 GiB,
`10737418240`). A cap on ourselves becomes a floor on the disk, which is the thing that actually
breaks: a full filesystem takes SQLite down with it, whatever filled it.

**Deployment:** the crawler mounts the output directory **read-only**, solely for `disk_usage`.
This reverses §6 of the scope-reduction spec, which concluded no mount was needed. The invariant
holds: `statvfs` reads filesystem metadata, never a file's bytes, and the mount is read-only.
`downloads/incoming` and `downloads/temp` sit on one filesystem, so one call covers both.

`committed_bytes()` and the `size_bytes` column lose their only consumer. Drop the method from the
repository and its port; leave the column (it is harmless catalog data and dropping it buys
nothing).

## 2. A download amuled no longer knows becomes `failed`

### The problem

No transition leaves `QUEUED` or `DOWNLOADING` without a positive signal from amuled. If a file
completes and is moved out of `IncomingDir` before the next poll, amuled stops sharing it: the hash
is in neither the queue nor the shared list, and the row stays `downloading` forever.

### Why a TTL is safe here

Established with the operator: **an entry stays in amuled's queue even with zero sources.** It goes
dormant; the sources disappear, the entry does not. So absence from amuled is a rare, strong signal
meaning the entry was actually removed, or the file completed and was moved. A dormant lost-media
download that has sat at 0% for months is never at risk, which is what makes the TTL acceptable.

Note also that `FAILED` has exactly one producer today: `add_link` rejected by amuled. It has never
meant "could not be downloaded".

### Design

New column `last_seen_at` on `downloads` (local.db), stamped each cycle in which the hash appears
in amuled's queue OR in its shared files. A row in `queued` or `downloading` whose `last_seen_at`
is older than `download.lost_after_seconds` (default `86400`, 24 h) becomes `failed`.

24 h absorbs an amuled restart, a VPN drop or a night of EC unavailability without condemning a
live download.

Backfill: the migration stamps `last_seen_at` with the row's `queued_at` for existing rows, so no
row is instantly declared lost on first boot.

### Resurrection

amuled is the authority on what it holds. A `failed` row that reappears:

- **in the queue** goes back to `downloading`. `_monitor` currently refuses to leave `FAILED`
  ("terminal: don't regress"); that guard is relaxed for `FAILED` only, never for `COMPLETED`.
- **in the shared files** becomes `completed` and fires its `DownloadCompleted` notification.

Missing a notification is not serious (the file was recovered either way; it is confusing, then
good news for whoever investigates), but resurrection costs two lines and removes the confusion.

### What deliberately does NOT change

`is_downloaded()` stays state-blind: any existing row, `failed` included, keeps blocking automatic
re-queuing. If absence means the operator removed the entry, re-adding it automatically would fight
them. Manual retry is deleting the row.

## 3. Persistence

`local/0005_last_seen_at.sql`:
- `ALTER TABLE downloads ADD COLUMN last_seen_at TEXT`
- `UPDATE downloads SET last_seen_at = queued_at`

Local schema version 4 -> 5. No catalog change.

## 4. Out of scope

- Bounding total disk usage by deleting old downloads. The floor refuses new work when the disk is
  tight; it never removes anything.
- An operator control to clear or retry one download. Deleting the row through the SQL console is
  the escape hatch, and the console is read-only today, so this means a manual `sqlite3` call.
