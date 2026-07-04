# Plan — catalog re-evaluation (startup backfill + retraction + notify)

> **For agentic workers:** implement task-by-task with strict TDD (red → green),
> one commit per task minimum. Steps use `- [ ]` checkboxes.

**Goal:** On crawler start, re-evaluate the whole catalogue against the current matcher —
appending changed decisions, retracting now-excluded files, queueing downloads and
notifying — skipping the pass entirely when the policy is unchanged.

**Architecture:** an in-process startup pass reusing the existing decision-recording +
event-emission path (so tier actions fire for free); exclusion is a sentinel append-only
`match_decisions` row; the pass self-gates on a policy fingerprint stored in `local.db`.

**Spec:** `docs/specs/2026-07-04-catalog-reevaluation.md` (read it first).

## Global Constraints (bind every task)

- **100 % branch coverage per package**, gated (`--cov-fail-under=100`, `branch=true`). Test
  both sides of every conditional. `-> None` on every test, typed params.
- **Strict TDD:** failing test first, watch it fail, minimal impl, watch it pass.
- **`mypy --strict`** over `src` + `tests`; **`ruff`** `E,F,I,UP,B,SIM`, line length 100.
- **Clean/Hexagonal:** `domain/` pure (no I/O); I/O in `adapters/`; `application/`
  orchestrates ports. DAG only.
- **English** everywhere (identifiers + prose + commits). **No em-dashes in UI text**
  (Task 8). Conventional commits.
- **Per-package gate:** `( cd packages/<pkg> && uv run pytest )`. Single test: add `--no-cov`.
- **`catalog.db` is append-only** (triggers). Retraction is an *append*. No schema change to
  `catalog.db`.
- **`deploy/config/` is the single source** — `matcher.yml` / `targets.yml` unchanged.
- Run `uv run poe fix` before hand-fixing lint/format/sql.

Task dependency graph: `1 → 2 → 5 → 6`; `3 → 5`; `4 → 6`. Tasks **7** and **8** are
independent of the chain (any order). Task **9** last.

---

## Task 1 — Retraction primitive: `RETRACTED_TIER` + `record_retraction`

**Package:** `crawler`.

**Files:**
- Create: `packages/crawler/src/emule_indexer/domain/retraction.py`
- Modify: `packages/crawler/src/emule_indexer/ports/catalog_repository.py` (add port method)
- Modify: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`
- Test: `packages/crawler/tests/adapters/persistence_sqlite/test_catalog_repository.py`
  (extend the existing suite)

**Interfaces produced:**
- `RETRACTED_TIER: str = "retracted"` (in `domain/retraction.py`).
- `CatalogRepository.record_retraction(self, ed2k_hash: str) -> None` (port Protocol method).
- Adapter `SqliteCatalogRepository.record_retraction(ed2k_hash)`.

**The change.** `record_retraction` appends a sentinel decision row reusing `_INSERT_DECISION`:
`(ed2k_hash, "", "", RETRACTED_TIER, utc_iso(self._clock()), self._node_id)`. Mirror
`record_decision` exactly: the canonical-hash guard first
(`_CANONICAL_HASH_RE.fullmatch` → `PersistenceError`), then the bare `INSERT` inside
`wrap_sqlite_errors()`. Add the Protocol method to the port with a one-line `...` body.

**TDD order:**
1. Test `record_retraction` inserts a row whose latest decision is
   `DecisionRecord(target_id="", rule_name="", tier="retracted")` — assert via
   `repo.last_decision(hash)` after seeding a `files` row (reuse the suite's existing
   observation-seeding helper so the FK holds).
2. Test the canonical-hash guard: `record_retraction("NOTAHASH")` raises `PersistenceError`.
3. Test append-only holds: a direct `UPDATE match_decisions ...` still raises (the existing
   trigger test pattern — extend or assert the sentinel row cannot be updated/deleted).
4. Implement to green.

**Done when:** `( cd packages/crawler && uv run pytest )` green at 100 %. Commit
`feat(crawler): add record_retraction sentinel decision`.

---

## Task 2 — Shared helper `record_decision_if_changed` + retraction branch

**Package:** `crawler`. **Depends on Task 1.**

**Files:**
- Create: `packages/crawler/src/emule_indexer/application/decisions.py`
- Modify: `packages/crawler/src/emule_indexer/application/record_observations.py`
- Test: create `packages/crawler/tests/application/test_decisions.py`; keep
  `tests/application/test_record_observations.py` green (migrate assertions if needed).

**Interface produced:**
```python
async def record_decision_if_changed(
    ed2k_hash: str,
    candidate: FileCandidate,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
) -> bool:
    """True iff a row was written (a real decision OR a retraction)."""
```

**The logic** (verbatim from spec §4):
```python
decision = engine.evaluate(candidate)
last = catalog.last_decision(ed2k_hash)
if decision is None:
    if last is None or last.tier == RETRACTED_TIER:
        return False
    catalog.record_retraction(ed2k_hash)
    await telemetry.emit(DecisionRecorded(target_id="", tier=RETRACTED_TIER))
    return True
fresh = to_record(decision)
if last == fresh:
    return False
catalog.record_decision(ed2k_hash, decision)
await telemetry.emit(DecisionRecorded(target_id=decision.target_id, tier=decision.tier))
signal.signal(ed2k_hash)
if decision.tier == "download":
    signal.signal(DOWNLOAD_NUDGE_SUBJECT)
return True
```
`record_observation` becomes: canonical try/except around
`catalog.record_observation(observation)` + `emit(ObservationRecorded)` + `return await
record_decision_if_changed(observation.ed2k_hash, observation.to_candidate(), catalog=...,
engine=..., signal=..., telemetry=...)`. Keep the `RepositoryError` → log + `return False`
absorption around the whole body (unchanged discipline).

**TDD order** (test the helper directly with fakes for `catalog`/`engine`/`signal`/`telemetry`,
mirroring `test_record_observations.py`'s existing fakes):
1. new decision (last is None): writes via `record_decision`, emits `DecisionRecorded` with
   the decision's tier, signals the hash; nudge iff tier == "download"; returns True.
2. changed decision (last != fresh): same as above.
3. unchanged decision (last == fresh): no write, no emit, no signal; returns False.
4. was-matched → None: calls `record_retraction`, emits `DecisionRecorded(target_id="",
   tier="retracted")`, **no** hash signal, **no** download nudge; returns True.
5. already-retracted → None (last.tier == "retracted"): no write, no emit; returns False.
6. never-matched → None (last is None): no write, no emit; returns False.
7. download tier: asserts the `DOWNLOAD_NUDGE_SUBJECT` nudge fired; a non-download tier does
   not nudge.
8. `record_observation` still records the observation and delegates (existing tests green;
   add one asserting a re-observed now-`None` file is retracted — the live-path improvement).

**Done when:** crawler gate green. Commit `refactor(crawler): extract
record_decision_if_changed + retract on exclusion`.

---

## Task 3 — Read `iter_reevaluation_rows` + centralize candidate conversion

**Package:** `crawler`. Independent of Tasks 1/2 (needed by Task 5).

**Files:**
- Modify: `packages/crawler/src/emule_indexer/domain/observation.py` (add
  `candidate_from_fields`, have `to_candidate` call it)
- Modify: `packages/crawler/src/emule_indexer/ports/catalog_repository.py` (add `ReevalRow`
  frozen dataclass + `iter_reevaluation_rows` Protocol method)
- Modify: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`
- Test: `tests/domain/test_observation.py` (or wherever `to_candidate` is tested) +
  `tests/adapters/persistence_sqlite/test_catalog_repository.py`

**Interfaces produced:**
```python
# domain/observation.py — pure, single source of the conversion
def candidate_from_fields(
    filename: str, size_bytes: int,
    media_length_sec: int | None, bitrate_kbps: int | None,
) -> FileCandidate: ...
# FileObservation.to_candidate now delegates to it.

# ports/catalog_repository.py
@dataclass(frozen=True)
class ReevalRow:
    ed2k_hash: str
    filename: str
    size_bytes: int
    media_length_sec: int | None
    bitrate_kbps: int | None

class CatalogRepository(Protocol):
    def iter_reevaluation_rows(self) -> Iterator[ReevalRow]: ...
```

**The SQL** (latest observation per hash — same "latest per hash" window the webui uses),
new module constant, streamed via the cursor:
```sql
SELECT o.ed2k_hash, o.filename, o.size_bytes, o.media_length_sec, o.bitrate_kbps
FROM file_observations AS o
WHERE (
    SELECT COUNT(*) FROM file_observations AS o2
    WHERE o2.ed2k_hash = o.ed2k_hash
      AND (o2.observed_at > o.observed_at
           OR (o2.observed_at = o.observed_at AND o2.id > o.id))
) = 0
ORDER BY o.ed2k_hash
```
The adapter method yields `ReevalRow(...)` per cursor row (a generator, wrapped in
`wrap_sqlite_errors()`).

**TDD order:**
1. `candidate_from_fields`: bytes → MiB (`/ 1024**2`), `int → float | None` for duration and
   bitrate; assert against a known example. `to_candidate` still returns the same
   `FileCandidate` (existing tests green).
2. `iter_reevaluation_rows`: seed two hashes, one with two observations → assert the LATEST
   observation's fields are returned, one row per hash; media columns present (incl. `None`).
3. empty catalogue → empty iterator.
4. Implement to green.

**Done when:** crawler gate green. Commit `feat(crawler): iter_reevaluation_rows +
centralize candidate conversion`.

---

## Task 4 — Policy fingerprint + `local.db` marker

**Package:** `crawler`. Independent of Tasks 1–3 (needed by Task 6).

**Files:**
- Create: `packages/crawler/src/emule_indexer/domain/policy_fingerprint.py`
- Create: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/migrations/local/0003_backfill_policy.sql`
- Modify: `packages/crawler/src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`
  + its port `packages/crawler/src/emule_indexer/ports/local_state_repository.py`
- Test: `tests/domain/test_policy_fingerprint.py`; extend the local-state repo test.

**Interfaces produced:**
```python
# domain/policy_fingerprint.py — pure (hashlib on given bytes)
def policy_fingerprint(matcher_bytes: bytes, targets_bytes: bytes) -> str:
    """sha256 hex over both policy files; order-fixed (matcher then targets),
    length-separated so concatenation is unambiguous."""

# local-state repository (port + adapter)
def last_backfill_policy(self) -> str | None: ...
def set_last_backfill_policy(self, sha256: str) -> None: ...
```

**Migration** (`local.db` is mutable — a one-row table, upserted):
```sql
CREATE TABLE backfill_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    policy_sha256 TEXT NOT NULL
);
```
Adapter: `set_last_backfill_policy` = `INSERT INTO backfill_state (id, policy_sha256)
VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET policy_sha256 = excluded.policy_sha256`;
`last_backfill_policy` = `SELECT policy_sha256 FROM backfill_state WHERE id = 1` → value or
`None`.

**Fingerprint impl:** `sha256(len(matcher_bytes).to_bytes(8,"big") + matcher_bytes +
targets_bytes).hexdigest()` (length prefix removes concat ambiguity). Keep it pure.

**TDD order:**
1. `policy_fingerprint`: deterministic for identical bytes; differs when the matcher bytes
   differ; differs when the targets bytes differ; a byte moved from one file to the other
   yields a different hash (length-prefix guard).
2. local-state repo: `last_backfill_policy()` is `None` before any set; after
   `set_last_backfill_policy("abc")` it returns `"abc"`; a second set overwrites.
3. Migration applies cleanly (the repo test opens `open_local` which runs migrations).
4. Implement to green.

**Done when:** crawler gate green. Commit `feat(crawler): policy fingerprint + local.db
backfill marker`.

---

## Task 5 — Backfill use-case `reevaluate_catalog`

**Package:** `crawler`. **Depends on Tasks 2 and 3.**

**Files:**
- Create: `packages/crawler/src/emule_indexer/application/reevaluate_catalog.py`
- Test: `packages/crawler/tests/application/test_reevaluate_catalog.py`

**Interface produced:**
```python
@dataclass(frozen=True)
class ReevalSummary:
    evaluated: int    # rows iterated
    written: int      # rows appended by the helper (re-tiered or retracted)

async def reevaluate_catalog(
    *, catalog: CatalogRepository, engine: MatchingEngine,
    signal: DecisionSignal, telemetry: Telemetry,
) -> ReevalSummary:
    ...
```

**The logic:** iterate `catalog.iter_reevaluation_rows()`; per row build the candidate via
`candidate_from_fields(row.filename, row.size_bytes, row.media_length_sec, row.bitrate_kbps)`;
call `record_decision_if_changed(row.ed2k_hash, candidate, catalog=..., engine=...,
signal=..., telemetry=...)`. `evaluated += 1` per row; `written += 1` when the helper returns
`True`. No separate retracted count (a retraction is just a written row — avoids re-evaluating
to distinguish). Wrap each per-row call in `try/except RepositoryError` → log + continue
(per-item isolation).

**TDD order:**
1. Two rows, both changed → `evaluated == 2`, `written == 2`, helper called per row.
2. A row whose verdict is unchanged → counted in `evaluated`, not in `written`.
3. A row that raises `RepositoryError` in the helper → absorbed, loop continues, the other
   rows still processed.
4. empty catalogue → `ReevalSummary(0, 0)`.
5. Implement to green.

**Done when:** crawler gate green. Commit `feat(crawler): reevaluate_catalog backfill
use-case`.

---

## Task 6 — Startup gate + wiring in `run()`

**Package:** `crawler`. **Depends on Tasks 4 and 5.**

**Files:**
- Create: `packages/crawler/src/emule_indexer/application/run_backfill.py`
  (`run_backfill_if_policy_changed`) — the testable gate
- Modify: `packages/crawler/src/emule_indexer/composition/app.py` (`run()`, and
  `CrawlerApp.__init__` to accept `policy_fingerprint`)
- Modify: `packages/crawler/src/emule_indexer/composition/__main__.py` (compute the
  fingerprint from the two files' bytes, pass to `CrawlerApp`)
- Test: `tests/application/test_run_backfill.py`; extend composition test if a seam is added.

**Interface produced:**
```python
async def run_backfill_if_policy_changed(
    *, fingerprint: str, local_repo: LocalStateRepository,
    run_backfill: Callable[[], Awaitable[ReevalSummary]],
) -> ReevalSummary | None:
    """None if skipped (fingerprint unchanged); the summary if it ran. Stores the marker
    only AFTER a successful run (run_backfill raising propagates, marker untouched)."""
```
Logic: if `local_repo.last_backfill_policy() == fingerprint` → return `None`. Else `summary =
await run_backfill()`; `local_repo.set_last_backfill_policy(fingerprint)`; return `summary`.

**Wiring in `composition/__main__.py`** (~lines 65-73): after loading configs, read the raw
bytes and compute the fingerprint, then pass it to `CrawlerApp`:
```python
matcher_bytes = Path(args.matcher).read_bytes()
targets_bytes = Path(args.targets).read_bytes()
fingerprint = policy_fingerprint(matcher_bytes, targets_bytes)
# CrawlerApp(..., policy_fingerprint=fingerprint)
```
(Do the same in `validate-config` path only if it constructs a `CrawlerApp`; otherwise skip.)

**Wiring in `composition/app.py` `run()`** between the `CrawlerStarted` emit (`:566`) and
`async with asyncio.timeout(None)` (`:572`):
```python
summary = await run_backfill_if_policy_changed(
    fingerprint=self._policy_fingerprint,
    local_repo=local_repo,
    run_backfill=lambda: reevaluate_catalog(
        catalog=catalog_repo, engine=engine, signal=self._signal, telemetry=telemetry
    ),
)
if summary is None:
    _logger.info("policy unchanged — catalogue re-evaluation skipped")
else:
    _logger.info("catalogue re-evaluated: %d files, %d rows written", summary.evaluated, summary.written)
```

**TDD order** (test `run_backfill_if_policy_changed` with a fake `local_repo` + a spy
`run_backfill`):
1. marker == fingerprint → `run_backfill` NOT awaited, `set_last_backfill_policy` NOT called,
   returns `None`.
2. marker is `None` (never set) → `run_backfill` awaited once, then
   `set_last_backfill_policy(fingerprint)` called; returns the summary.
3. marker != fingerprint → same as (2).
4. `run_backfill` raises → the exception propagates and `set_last_backfill_policy` is NOT
   called (crash safety).
5. Implement to green. For `composition/app.py`, add/extend the existing composition test to
   assert the backfill is wired once before `_supervise` (a light seam — e.g. assert the log
   line, or inject a fake `reevaluate` if the composition test already fakes deps).

**Done when:** crawler gate green. Commit `feat(crawler): run startup backfill, gated by
policy fingerprint`.

---

## Task 7 — `notify → OPERATIONS`

**Package:** `crawler`. Independent (any time).

**Files:**
- Modify: `packages/crawler/src/emule_indexer/domain/observability/policy.py`
  (`DecisionRecorded` arm, ~lines 202-208)
- Test: `packages/crawler/tests/domain/observability/test_policy.py`

**The change:**
```python
case DecisionRecorded():
    audiences: frozenset[Audience]
    if event.tier == "download":
        audiences = frozenset({Audience.COMMUNITY})
    elif event.tier == "notify":
        audiences = frozenset({Audience.OPERATIONS})
    else:
        audiences = frozenset()
    return Report(
        Severity.INFO,
        f"decision {event.tier} for {event.target_id}",
        (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", event.tier),)),),
        audiences,
    )
```

**TDD order** — update the table-driven `CASES` in `test_policy.py`:
1. keep `tier="download"` → `frozenset({Audience.COMMUNITY})`.
2. add `tier="notify"` → `frozenset({Audience.OPERATIONS})`.
3. add `tier="retracted"` → `frozenset()` (silent).
4. change the existing negative case from `tier="candidate"` to `tier="catalog"` (a real
   silent tier).
5. Run (red on the new cases), implement to green.

**Done when:** crawler gate green. Commit `feat(crawler): notify tier notifies the operator`.

---

## Task 8 — webui: `retracted` treated as unmatched

**Package:** `webui`. Independent (any time). **No em-dashes in UI.**

**Files:**
- Modify: `packages/webui/src/catalog_webui/adapters/catalog_read.py` (matched-only clause,
  count, coverage SQL)
- Modify: `packages/webui/src/catalog_webui/composition/app.py` (`_resolve_target_display` /
  `_to_display_rows`)
- Test: `packages/webui/tests/test_webui_app.py`, `tests/test_webui_catalog_read.py`

**Behaviour:** a file whose LATEST decision tier is `"retracted"` is treated exactly like a
file with **no decision**.

**The changes:**
- `_filter_clauses` matched-only (`catalog_read.py`): the matched-only clause becomes
  `dec.target_id IS NOT NULL AND dec.tier != 'retracted'`.
- `count_files` matched count: replace `COUNT(dec.target_id)` with
  `SUM(CASE WHEN dec.target_id IS NOT NULL AND dec.tier != 'retracted' THEN 1 ELSE 0 END)`.
- `_SQL_COVERAGE` (`target_coverage`): after the latest-per-hash filter, add
  `AND md.tier != 'retracted'` so a retracted file drops out of coverage.
- Display (`composition/app.py`): in `_resolve_target_display` / `_to_display_rows`, when
  `row.tier == "retracted"`, render target/title/tier/verdict as the empty placeholder `·`
  (same as no decision). Add `"retracted"` alongside the existing `None`/`catalog` handling.

**TDD order** (add a `retracted`-latest fixture — a file whose latest `match_decisions` row is
`("", "", "retracted", ...)`):
1. `catalog_read`: matched-only list excludes the retracted file; `count_files` counts it as
   unmatched; `target_coverage` omits it.
2. `app`: the all-view (`show_unmatched=1`) shows the retracted file as an unmatched row
   (`·` cells), never `<td>unidentified</td>` or a tier badge; the matched-only default hides
   it.
3. Implement to green.

**Done when:** `( cd packages/webui && uv run pytest )` green at 100 %. Commit
`feat(webui): treat retracted decisions as unmatched`.

---

## Task 9 — Docs: operator runbook note

**Package:** docs only.

**Files:**
- Modify: `docs/runbooks/administration.md` (a short "Catalogue re-evaluation" subsection)

**Content:** on every start the crawler re-evaluates the catalogue against the current
`matcher.yml` + `targets.yml` **only if they changed** since the last run (a stored
fingerprint); a now-excluded file is *retracted* (hidden from the webui, dropped from
downloads) but never un-downloaded; `notify`-tier decisions notify the **operations** channel
(configure a `tag: operations` target under `observability.notifications` in `crawler.yml`),
`download`-tier the **community** channel. No em-dashes in the prose.

**Done when:** the doc reads correctly. Commit `docs: runbook note for catalogue
re-evaluation`.

---

## Self-review checklist (run before execution)

- Spec §4 helper → Task 2. §5 retraction → Task 1 (+2). §6 read → Task 3. §7 backfill+wiring
  → Tasks 5+6. §7.1 fingerprint → Tasks 4+6. §8 notify → Task 7. §9 webui → Task 8.
- Interfaces consistent: `record_decision_if_changed` (T2) consumed by T5; `ReevalRow` +
  `iter_reevaluation_rows` (T3) consumed by T5; `candidate_from_fields` (T3) consumed by T5;
  `policy_fingerprint` (T4) consumed by T6; `run_backfill_if_policy_changed` (T6) consumes T5
  `reevaluate_catalog` + T4 `last/set_backfill_policy`; `RETRACTED_TIER` (T1) consumed by
  T2/T8 (T8 hardcodes the string, no import across packages).
- `ReevalSummary` fields settled as `{evaluated, written}` (Task 5) and used verbatim in
  Task 6's log line.
