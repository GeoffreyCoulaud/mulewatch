# Spec — catalog re-evaluation ("the catalogue follows the matcher")

Date: 2026-07-04
Status: proposed (awaiting review)
Scope: crawler + webui. One implementation plan.

## 1. Goal

Let the operator change the matcher policy and have the **catalogue follow it**: on the
next crawler start, every already-catalogued file is re-evaluated against the current
matcher, and each tier's action actually happens.

Concretely, three effects the operator wants:

- **Exclude** — a file the new matcher no longer matches stops being shown as matched.
- **Re-tier** — a file whose tier changes (e.g. `catalog` → `notify`, or `notify` →
  `download`) gets the new tier recorded.
- **Act** — a re-tiered file triggers the tier's real action: `download` gets queued,
  `notify` sends a notification.

## 2. Why this does not happen today (verified)

- `match_decisions(ed2k_hash, target_id, rule_name, tier, decided_at, node_id)` is
  **append-only** (DB triggers) and stores the tier **per file**. The webui shows each
  file's **latest** decision; it never re-runs the engine for the tier.
- A decision is (re)written only on **re-observation**, by the crawler, and only if
  `(target_id, rule_name, tier)` changed (`application/record_observations.py:51-57`). The
  engine is built once at startup, so a `matcher.yml` change needs a restart even to affect
  newly-observed files.
- **Exclusion is unrepresentable**: when `engine.evaluate()` returns `None`,
  `record_observation` does `return False` and writes nothing
  (`record_observations.py:52-53`). The append-only model has no "un-match" row, so an
  excluded file keeps its stale decision as "latest" forever.
- **`notify` currently does nothing**: `domain/observability/policy.py:207` sets the
  COMMUNITY audience only for `tier == "download"`.
- **Notifications are in-process, never replayed** from the DB (verified in
  `adapters/observability/dispatcher.py`). Downloads survive a crash via a DB replay
  (`download_decisions()`); notifications do not. Consequence: to fire notifications, the
  re-evaluation must run **inside the crawler process** and emit `DecisionRecorded`.

## 3. Design overview

A **startup backfill**: one pass, run once at crawler start, before the crawl loops, in
**both** run modes (observer and full). It re-evaluates every catalogued file against the
current engine and appends only *changed* decisions — including a new **retraction** row
when a file is now excluded. Because it runs in-process and reuses the existing
decision-recording + event-emission path, the tier actions (download queue replay, notify
notification) happen for free.

The whole pass is **skipped when the policy has not changed** since the last successful
backfill (a policy-fingerprint check — §7.1), so an ordinary restart does zero work; a
matcher/targets edit is the only thing that triggers a re-evaluation.

Five pieces:

1. **Shared decision helper** (refactor) — extract the "evaluate → compare → record →
   emit → nudge" block so both the worker and the backfill call it.
2. **Retraction** — represent exclusion as a sentinel `match_decisions` row (`tier =
   "retracted"`), per the append-only invariant.
3. **Catalog read** — a new port method to enumerate each file's latest observation with
   enough columns to rebuild a `FileCandidate`.
4. **Backfill use-case + startup wiring** — the pass itself, called from `run()`, guarded by
   a policy-fingerprint skip so an unchanged policy does zero work.
5. **Actions** — `notify → apprise` (one-line policy change) and webui handling of the
   `retracted` tier.

## 4. Component: shared decision helper (refactor)

Extract `record_observations.py:51-76` into a pure-orchestration helper:

```
async def record_decision_if_changed(
    ed2k_hash: str,
    candidate: FileCandidate,
    *, catalog: CatalogRepository, engine: MatchingEngine,
    signal: DecisionSignal, telemetry: Telemetry,
) -> bool:
    """Evaluate `candidate`; if the resulting verdict differs from the file's latest
    persisted decision, append it (or a retraction) + emit DecisionRecorded + nudge.
    Returns True iff a new row was written."""
```

`record_observation` becomes: `catalog.record_observation(observation)` +
`emit(ObservationRecorded)` + `return await record_decision_if_changed(observation.ed2k_hash,
observation.to_candidate(), ...)`.

New logic inside the helper (the retraction branch is the only behavioural addition).
The helper returns `True` iff it wrote a row (a real decision **or** a retraction):

```
decision = engine.evaluate(candidate)
last = catalog.last_decision(ed2k_hash)              # DecisionRecord | None
if decision is None:
    if last is None or last.tier == RETRACTED_TIER:
        return False                                 # never matched, or already retracted
    catalog.record_retraction(ed2k_hash)             # append sentinel row
    await telemetry.emit(DecisionRecorded(target_id="", tier=RETRACTED_TIER))
    return True                                      # a row was written; no nudge (never download)
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

- The helper reads `last_decision` in both branches now (one extra read on the exclusion
  path).
- **The retraction branch also improves the live path**: today a re-observed file that now
  evaluates to `None` silently returns `False`; with the helper it retracts. This is the
  same desirable behaviour for live re-observation as for the backfill — a positive,
  intended consequence, not backfill-only.
- **`RepositoryError` is absorbed by each caller**, not by the helper (the helper is pure
  orchestration and may raise): `record_observation` keeps its existing `try/except` around
  `record_observation(...)` + the helper call; the backfill wraps each per-file helper call
  in its own `try/except` (per-item isolation, cycle continues).

## 5. Component: retraction (sentinel `match_decisions` row)

**Decision (approved):** represent exclusion as an appended `match_decisions` row with
`tier = "retracted"`, `target_id = ""`, `rule_name = ""`. No schema migration (the `tier`
column is free TEXT; the append-only triggers are unchanged).

- **Constant:** `RETRACTED_TIER = "retracted"` in the crawler domain. It is **not** a
  matcher tier (`catalog_matching.config.TIERS` stays `{catalog, notify, download}`); it is
  synthesized by the crawler, never by the engine.
- **New repo method** `record_retraction(ed2k_hash)` on the `CatalogRepository` port + the
  SQLite adapter: a bare `INSERT` (autocommit) of `(ed2k_hash, "", "", "retracted",
  decided_at, node_id)`, mirroring `record_decision` (same canonical-hash guard).
- **`last_decision`** already returns `DecisionRecord(target_id, rule_name, tier)`; a
  retracted latest has `tier == "retracted"`, which the helper checks to avoid re-appending.
- **Downstream, for free:** `download_decisions()` filters `tier='download'`, so a
  `retracted` latest excludes the file from downloads automatically.

**Cross-package contract:** the tier strings are already a stringly-typed contract the
webui hardcodes (`"catalog"` → "unidentified"). `"retracted"` joins that contract; the
webui hardcodes it too (the packages do not import each other).

## 6. Component: catalog read for re-evaluation

The engine needs a `FileCandidate(filename, size_mb, duration_sec, bitrate_kbps)`. The data
exists in `file_observations` (`media_length_sec`, `bitrate_kbps`), but no read returns it
for the whole catalogue (`last_observation` returns only `filename` + `size_bytes`).

- **New port method** `iter_reevaluation_rows() -> Iterable[ReevalRow]` (streaming), where
  `ReevalRow` carries `ed2k_hash, filename, size_bytes, media_length_sec, bitrate_kbps`.
- **SQLite adapter:** one query returning each hash's **latest** observation (the same
  "latest per hash" window the webui uses), streamed via a cursor (catalogue may be large).
- **Candidate reconstruction:** the byte→MiB and int→float conversion currently lives in
  `FileObservation.to_candidate()` and is duplicated in the webui's `MatchingExplainer`.
  Centralise it once (e.g. a module-level `candidate_from_fields(...)` in
  `domain/observation.py`) and have both `to_candidate` and the backfill call it — avoids a
  third copy.

## 7. Component: backfill use-case + startup wiring

- **Use-case** `application/reevaluate_catalog.py`: iterate
  `catalog.iter_reevaluation_rows()`, build a candidate per row, call
  `record_decision_if_changed(...)`. Count changed/retracted for a summary log. Absorb a
  per-file `RepositoryError` (one bad file must not abort the sweep), same discipline as the
  crawl cycle.
- **Wiring:** in `composition/app.py` between the `CrawlerStarted` emit (`:566`) and
  `_supervise` (`:572`), run the policy-fingerprint gate (§7.1); if it says "changed", call
  the backfill once, to completion, before the loops. Deps (`catalog_repo`, `engine`,
  `telemetry`, `self._signal`) are already in scope and built in both modes.
- **Log line** at the end: e.g. `re-evaluated N files: M re-tiered, K retracted`.

### 7.1 Startup-skip: policy fingerprint (approved: global marker)

Avoid re-evaluating on every restart when the policy has not changed.

- **Policy fingerprint** = `sha256` over the **source bytes of both `matcher.yml` and
  `targets.yml`** (both feed `MatchingEngine`; a target/title edit changes decisions too).
  Computed once at config load (deterministic; a comment/whitespace-only edit changes the
  fingerprint → one harmless extra backfill that writes nothing, then the marker updates).
- **Global marker in `local.db`** (mutable — `local.db` is *not* append-only, unlike
  `catalog.db`): a single stored value `last_backfill_policy_sha256`. New methods on the
  local-state repository: `last_backfill_policy() -> str | None` and
  `set_last_backfill_policy(sha256)`. A small `local.db` migration adds the storage (a
  one-row table or a KV row).
- **Gate logic** at startup:
  ```
  fingerprint = policy_fingerprint(matcher.yml, targets.yml)
  if local_repo.last_backfill_policy() == fingerprint:
      log "catalogue already consistent with current policy — backfill skipped"
  else:
      changed, retracted = await run_backfill(...)
      local_repo.set_last_backfill_policy(fingerprint)   # only AFTER a full pass
      log "re-evaluated ...; policy fingerprint stored"
  ```
- **Crash safety:** the marker is written **only after** a full backfill completes. A crash
  mid-backfill leaves the marker unchanged → the next start re-runs it (idempotent via the
  anti-redundancy guard).

## 8. Component: `notify → apprise`

- `domain/observability/policy.py` `DecisionRecorded` arm: `download` → `{Audience.COMMUNITY}`
  (unchanged); **`notify` → `{Audience.OPERATIONS}`** (notify is inherently ambiguous — a
  manual-review flag — so it pings the operator, not the community); `catalog` and `retracted`
  → no audience (silent).
- Both audiences already exist (`Audience.COMMUNITY`, `Audience.OPERATIONS`). For `notify` to be
  delivered, a `tag: operations` channel must exist in `crawler.yml` (`download` already uses
  `tag: community`). No wiring change.

## 9. Component: webui handling of `retracted`

A file whose **latest** decision tier is `retracted` is treated as **unmatched** — identical
to having no decision:

- Hidden by the matched-only default; counted as unmatched in the summary.
- In the all-view it appears like any other unmatched file (placeholder `·` cells).
- No new UI string is required; `retracted` is normalised to "no current match" in the read
  layer (`catalog_read.py`) and/or the display resolver (`composition/app.py`).

(The exact SQL/display change is left to the plan; the behaviour is: retracted == unmatched.)

## 10. Behaviour details

- **Idempotent / append-only:** a restart with an **unchanged policy** skips the backfill
  entirely (fingerprint match — §7.1), so zero work. When the policy *did* change, the
  anti-redundancy guard still means only genuine tier changes append a row; unchanged
  verdicts write nothing and notify nothing.
- **Both modes:** the backfill runs in observer and full mode. In observer mode the download
  nudge has no subscriber (harmless); notifications still fire if the relevant channel is
  configured (community for `download`, operations for `notify`); decisions update for display.
- **Notification burst:** a large matcher change can re-tier many files at once, each firing
  a notification. This is bounded by "genuinely changed to notify/download" and is desirable
  for a lost-media hunt. A digest/cap is a future option (YAGNI now).
- **Retractions are silent** (point 1, approved): log + `decisions{tier=retracted}` metric,
  no notification.
- **download → notify downgrade notifies (accepted marginal case):** a file previously
  `download` (already announced to the community) that the new matcher downgrades to `notify`
  will emit a `notify` (operator) notification. Odd but marginal; not special-cased for now.

## 11. Out of scope (approved boundaries)

- Never "un-download": a retraction does not cancel an in-flight download nor delete a
  completed file (append-only; completion is a positive signal).
- No config toggle for the backfill; it self-gates on the policy fingerprint (§7.1), so it
  costs nothing when the policy is unchanged. Add a toggle only if a real need appears.
- No new confinement / no change to the append-only triggers.

## 12. Testing plan (strict TDD, 100 % branch per package)

- **matching:** unchanged (the engine already returns `None`/decision). No new tests here.
- **crawler:**
  - `record_decision_if_changed` helper: both matched paths (new / changed / unchanged) and
    the retraction paths (was-matched→None writes a retraction; already-retracted→None is a
    no-op; never-matched→None is a no-op). Assert the emitted `DecisionRecorded` tier and the
    download nudge only on `download`.
  - `record_observation`: unchanged behaviour (still records observation + delegates); its
    existing tests must stay green.
  - `record_retraction` adapter: inserts the sentinel row; append-only trigger still holds;
    canonical-hash guard.
  - `iter_reevaluation_rows` adapter: latest-per-hash selection + the media columns; empty
    catalogue; a file with multiple observations returns the latest.
  - backfill use-case: iterates all rows, calls the helper, counts changed/retracted, absorbs
    a per-file `RepositoryError`, empty catalogue.
  - policy fingerprint: `policy_fingerprint(...)` is stable for identical bytes and differs
    when either file's bytes differ; the local-state repo round-trips
    `set/last_backfill_policy` (and returns `None` when never set).
  - startup gate: fingerprint **match** → backfill NOT run (assert no re-evaluation, marker
    untouched); fingerprint **mismatch / absent** → backfill runs, then the marker is stored;
    a backfill that raises does NOT store the marker (crash safety).
  - policy: add a `tier="notify"` case (→ OPERATIONS) and a `tier="retracted"` case (→ no
    audience); keep the `tier="download"` case (→ COMMUNITY); fix the existing negative case
    to use `tier="catalog"` (a real silent tier).
  - startup wiring: the backfill is invoked once before the loops (composition test / a
    seam that lets a test assert the single call).
- **webui:** a file whose latest decision is `retracted` is hidden by matched-only, counted
  as unmatched, and rendered as an unmatched row in the all-view.

## 13. Invariants respected

- **Append-only:** retraction is an *append*, never a mutate/delete. `catalog.db` triggers
  and schema are unchanged (no migration there). The fingerprint marker lives in `local.db`,
  which is *mutable* by design (it already holds backoff/downloads state) — a small migration
  there, no append-only conflict.
- **Clean/Hexagonal:** the helper and backfill are `application/`; the read is a port +
  adapter; the domain stays pure. `RETRACTED_TIER` is a domain constant.
- **Package boundary:** crawler and webui still do not import each other; `"retracted"` is a
  stringly-typed tier contract like the existing tiers.
- **`deploy/config` single source:** no policy/config duplication; `matcher.yml` unchanged.
- **Boundary discipline (E-D13):** per-file `RepositoryError` absorbed in the sweep; a
  `notify` channel failure absorbed by the dispatcher (already the case).

## 14. Open questions

None. Decided: retraction as a sentinel row; silent retractions; `notify → OPERATIONS`
(not community); webui treats `retracted` as unmatched; the backfill self-gates on a
policy fingerprint over `matcher.yml` + `targets.yml` (global marker in `local.db`); the
`download → notify` downgrade notifies (accepted marginal case); and the no-un-download
boundary.
