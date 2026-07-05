# Multi-target matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A whole-episode video file (one ed2k hash covering both A and B segments) produces a decision for each segment target, so a single recovered file resolves both lost segments instead of showing as unidentified.

**Architecture:** `MatchingEngine.evaluate` moves from one `MatchDecision | None` to a `list[MatchDecision]` with a per-episode fan-out (spec §3/§4). The crawler's record path becomes a set diff keyed by `(hash, target_id)` with per-target retraction; two persistence reads move to latest-per-`(hash, target_id)` (no DDL). The read-only web UI renders one row per file with its targets aggregated. The existing policy-gated startup backfill re-evaluates the catalog, so no reset is required.

**Tech Stack:** Python ≥3.14, uv workspace (4 packages), stdlib `re` (`re.ASCII`), rapidfuzz, SQLite (append-only journal + triggers), Starlette + logic-free templates, pytest, mypy --strict, ruff, sqlfluff.

**Reference:** spec `docs/specs/2026-07-06-multi-target-matching.md` (approved 2026-07-06).

## Global Constraints

Every task's requirements implicitly include this section.

- **Python ≥3.14.** Domain (`domain/`) stays PURE: no I/O, no yaml/DB/network/clock/logging. All I/O in `adapters/`.
- **Strict TDD:** write the failing test first, run it, watch it fail, then the minimal implementation. Every test function is annotated `-> None` with typed params.
- **100% branch coverage PER PACKAGE** (`--cov-fail-under=100`, `branch=true`) — exercise BOTH sides of every conditional. Never lower the threshold; add the missing test.
- **`mypy --strict`** over both `src` AND `tests`. **`ruff`** selects `E,F,I,UP,B,SIM`, line length **100**. Run `uv run poe fix` (ruff --fix + format + sqlfluff fix) before hand-fixing anything mechanical; review its diff.
- **Per-package gate.** Single test with coverage off: `( cd packages/<pkg> && uv run pytest tests/<path>::<name> --no-cov -q )`. Full package gate: `( cd packages/<pkg> && uv run pytest )`. Full repo gate: `uv run poe check`.
- **Language: all code is English** — identifiers, comments, docstrings, runtime messages, commit messages. The only French left is genuine domain data (VF episode titles, eMule filenames). Conventional commits (`feat(domain):`, `fix(webui):`, `test:`, `refactor:`, `docs:`).
- **No em-dashes / en-dashes in any user-facing string** (labels, cells, titles, CLI). Use `·`, `:`, `/`, or a short hyphen. Applies to every web-UI cell.
- **`deploy/config/` is the operator-owned single source of truth.** The matcher policy has exactly ONE copy (`deploy/config/crawler/matcher.yml`), read by the matching golden corpus + engine unit tests via `parents[N]`. Do NOT add a duplicate policy fixture or inline policy dict.
- **`match_decisions` is append-only** (DB triggers `RAISE(ABORT)` on UPDATE/DELETE): exclusion is an APPENDED row, never a mutation. No DDL migration is added by this plan.

## Spec coverage (self-review)

| Spec section | Task(s) |
|---|---|
| §3 decision semantics | P1-T3 (engine), P1-T4 (golden corpus) |
| §4 engine algorithm | P1-T1, P1-T2, P1-T3 |
| §4.1 retire `{mono_gate}` | P1-T3 (policy), P1-T5 (interpolation), P1-T6 (`sole_segment`) |
| §5 policy change (fingerprint) | P1-T3 |
| §6 persistence (no DDL, 2 reads, retraction) | P2-T1, P2-T2, P2-T3 |
| §7 application set-diff + per-target retraction | P2-T3 |
| §8 download once (dedup by hash) | P2-T6 |
| §9 web UI rendering A + `/targets/{id}` + counters + detail | P3-T1, P3-T2, P3-T3, P3-T4 |
| §10 no-reset backfill migration + legacy-row tolerance | P2-T5 (guard), P3-T1 (legacy sentinel) |
| §11 edge cases | P1-T3 tests, P1-T4 |
| §12 testing | throughout |
| §13 phasing | the three phases below |

## Self-review findings (verified before hand-off)

- **Fixtures confirmed real.** `_TARGET_62A.title = "Les demoiselles cambrioleuses"`, `_TARGET_62B.title = "Le grand combat sous-marin"` (`test_engine.py:77,133`); `golden_targets.yaml` carries 062A/062B with those exact titles plus mono 094A. The fan-out and "both titles" cases match the real fixtures.
- **Interface contract holds end to end:** `evaluate -> list[MatchDecision]` (P1) → `last_decisions(hash) -> dict[str, DecisionRecord]`, `record_retraction(hash, target_id)`, `record_decision_if_changed -> int` (P2) → web UI reads the same table (P3, decoupled adapter).
- **One doc fix folded into P1-T6:** the comment in `golden_targets.yaml:10-12` still explains the `mono_gate` neutralization ("a multi-segment target's mono_gate always neutralizes segment_id_loose"). It is stale once `{mono_gate}`/`sole_segment` are removed — update it there (the mono target 094A stays, but for the fan-out grouping, not the mono_gate boundary).

## Execution order

Phases 1 and 2 **ship together in one PR** (the `evaluate` return-type change is a hard cut across the workspace; after P1 alone the `packages/crawler` gate is red by design). Validate P1 with the matching gate only. Phase 3 is read-side and can be a second commit/PR in the same stack. Within each phase, the "atomic cut" tasks (P1-T2, P2-T3, P3-T2) are red mid-refactor and green at commit; the rest commit green independently.

---

# Phase 1 — Matching engine: `evaluate → list[MatchDecision]` fan-out (`packages/matching`)

**Scope.** Only `packages/matching` + the shared `deploy/config/crawler/matcher.yml`. Because `evaluate`'s return type changes, the `packages/crawler` gate goes red on its own — expected, resolved by Phase 2 (they ship in one PR, spec §13). **Validate Phase 1 with the matching gate only:** `( cd packages/matching && uv run pytest )`.

**Contract locked by this phase:** `MatchDecision(target_id, rule_name, tier, explanation)` unchanged · `MatchingEngine.evaluate(candidate: FileCandidate) -> list[MatchDecision]` (`[]` = discarded) · `DecisionRecord` / `to_record` unchanged · `DownloadCandidate(ed2k_hash, target_id)` unchanged.

## Task 1 — Declare the attributable / segment-level / episode-level rule-name sets

**Files**
- Modify: `packages/matching/src/catalog_matching/engine.py` (insert after line 99, `_TIER_RANK = TIER_RANK`).
- Modify (Test): `packages/matching/tests/test_engine.py` (import block lines 8-14; add tests after line 32).

**Interfaces**
- Produces: module constants `_SEGMENT_LEVEL: frozenset[str]`, `_EPISODE_LEVEL: frozenset[str]`, `_ATTRIBUTABLE: frozenset[str]` in `catalog_matching.engine`.
- Consumes: nothing.

- [ ] **Step 1 (RED):** extend the `test_engine.py` import and add invariant tests. Replace the import block (lines 8-14):

```python
from catalog_matching.engine import (
    _ATTRIBUTABLE,
    _EPISODE_LEVEL,
    _SEGMENT_LEVEL,
    _TIER_RANK,
    Explanation,
    MatchDecision,
    MatchingEngine,
    _first_matching_rule,
)
```

Add after `test_tier_rank_covers_exactly_the_valid_tiers` (after line 32):

```python
def test_attributable_is_the_union_of_segment_and_episode_level() -> None:
    assert _ATTRIBUTABLE == _SEGMENT_LEVEL | _EPISODE_LEVEL


def test_segment_and_episode_level_sets_are_disjoint() -> None:
    assert _SEGMENT_LEVEL.isdisjoint(_EPISODE_LEVEL)


def test_attributable_names_match_the_spec() -> None:
    assert _SEGMENT_LEVEL == frozenset({"id_segment_exact", "title_confirmed", "title_review"})
    assert _EPISODE_LEVEL == frozenset({"numero_nu_confirmed", "numero_nu"})
```

- [ ] **Step 2 (RED run):** `( cd packages/matching && uv run pytest tests/test_engine.py -q --no-cov )` → collection error: `ImportError: cannot import name '_ATTRIBUTABLE' from 'catalog_matching.engine'`.

- [ ] **Step 3 (GREEN):** in `engine.py`, insert directly after line 99 (`_TIER_RANK = TIER_RANK`):

```python
# Rule-name sets driving the multi-target fan-out (spec §4). A rule is ATTRIBUTABLE when its
# win pins the file to a concrete target via a number/title video signal. SEGMENT_LEVEL rules
# pin one specific segment (a title, or a lettered number); EPISODE_LEVEL rules designate the
# whole episode (a bare number) and thus every one of its segments. ATTRIBUTABLE is exactly
# their union.
_SEGMENT_LEVEL: frozenset[str] = frozenset({"id_segment_exact", "title_confirmed", "title_review"})
_EPISODE_LEVEL: frozenset[str] = frozenset({"numero_nu_confirmed", "numero_nu"})
_ATTRIBUTABLE: frozenset[str] = _SEGMENT_LEVEL | _EPISODE_LEVEL
```

- [ ] **Step 4 (GREEN run):** `( cd packages/matching && uv run pytest tests/test_engine.py -k "attributable or segment_and_episode" --no-cov -q )` → `3 passed`.
- [ ] **Step 5:** full gate `( cd packages/matching && uv run pytest )` → all pass, coverage 100%.
- [ ] **Step 6 (commit):** `git commit -am "feat(domain): declare attributable rule-name sets for the fan-out"`

## Task 2 — `evaluate → list[MatchDecision]`: the return-type hard cut (single-winner preserved)

Change the signature workspace-wide WITHOUT the fan-out yet: `evaluate` returns `[]` or `[single-winner]` via the existing min-key. Migrate every call-site; delete the obsolete `{mono_gate}` mono-routing test block.

**Files**
- Modify: `packages/matching/src/catalog_matching/engine.py` (`evaluate`, lines 186-221).
- Modify (Test): `packages/matching/tests/test_engine.py` (canonical `evaluate` tests lines 148-308; **delete** the mono-routing block lines 311-426).
- Modify (Test): `packages/matching/tests/test_golden_corpus.py` (`test_golden_corpus` lines 40-49).
- Modify (Test): `packages/matching/tests/test_engine_properties.py` (imports + `test_property_higher_priority_rule_never_lowers_tier`).

**Interfaces**
- Produces: `MatchingEngine.evaluate(candidate: FileCandidate) -> list[MatchDecision]` (0 or 1 element).
- Consumes: `_first_matching_rule`, `_explain`, `_TIER_RANK` (unchanged).

- [ ] **Step 1 (RED):** migrate two canonical tests to the list contract. Replace `test_evaluate_real_62a_is_download_via_first_rule_on_62a` (lines 148-153) and `test_evaluate_discards_non_keroro_file` (lines 156-158):

```python
def test_evaluate_real_62a_is_download_via_first_rule_on_62a() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert len(decisions) == 1
    assert decisions[0].tier == "download"
    assert decisions[0].rule_name == "id_segment_exact"
    assert decisions[0].target_id == "062A"


def test_evaluate_discards_non_keroro_file() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename="Naruto épisode 062 VF.avi"))
    assert decisions == []
```

- [ ] **Step 2 (RED run):** `( cd packages/matching && uv run pytest tests/test_engine.py::test_evaluate_real_62a_is_download_via_first_rule_on_62a --no-cov -q )` → FAIL: `TypeError: object of type 'MatchDecision' has no len()`.

- [ ] **Step 3 (GREEN):** in `engine.py`, replace the whole `evaluate` method (lines 186-221) with:

```python
    def evaluate(self, candidate: FileCandidate) -> list[MatchDecision]:
        """All decisions for ``candidate`` (spec §4); ``[]`` = file discarded.

        Length-bound first. Then, per target, its first true rule; the deterministic
        single winner (highest tier, then smallest rule index, then smallest ``target_id``)
        is returned wrapped in a one-element list. No true rule anywhere -> ``[]``.
        """
        if len(candidate.filename) > self._max_filename_length:
            return []
        best: tuple[int, int, str] | None = None  # (-tier_rank, rule_index, target_id)
        best_resolved: ResolvedTarget | None = None
        best_rule_name = ""
        best_tier = ""
        for resolved in self._resolved:
            outcome = _first_matching_rule(self._config, resolved, candidate)
            if outcome is None:
                continue
            index, rule_name, tier = outcome
            key = (-_TIER_RANK[tier], index, resolved.target.target_id)
            if best is None or key < best:
                best = key
                best_resolved = resolved
                best_rule_name = rule_name
                best_tier = tier
        if best_resolved is None:
            return []
        return [
            MatchDecision(
                target_id=best_resolved.target.target_id,
                rule_name=best_rule_name,
                tier=best_tier,
                explanation=_explain(self._config, best_resolved, candidate),
            )
        ]
```

- [ ] **Step 4 (GREEN run):** `( cd packages/matching && uv run pytest tests/test_engine.py::test_evaluate_real_62a_is_download_via_first_rule_on_62a tests/test_engine.py::test_evaluate_discards_non_keroro_file --no-cov -q )` → `2 passed`.

- [ ] **Step 5 (migrate remaining canonical `evaluate` tests):** replace `test_evaluate_highest_tier_comes_from_a_different_target` through `test_evaluate_accepts_filename_at_or_below_max_length` (lines 161-249) and the two explanation tests (lines 260-308) with:

```python
def test_evaluate_highest_tier_comes_from_a_different_target() -> None:
    # "keroro N°062B.avi": 62A -> catalog (keroro_large, not attributable); 62B -> download
    # (id_segment_exact). Only 62B is attributable -> the sole emitted decision is 62B.
    decisions = _canonical_engine().evaluate(FileCandidate(filename="keroro N°062B.avi"))
    assert len(decisions) == 1
    assert decisions[0].tier == "download"
    assert decisions[0].rule_name == "id_segment_exact"
    assert decisions[0].target_id == "062B"


def test_evaluate_notify_tier_via_title_review() -> None:
    # Close title, NO source marker -> title_review (notify, segment-level) on 062A only.
    candidate = FileCandidate(filename="KERORO Les demoiselles cambrioleuses.avi")
    decisions = _canonical_engine().evaluate(candidate)
    assert len(decisions) == 1
    assert decisions[0].tier == "notify"
    assert decisions[0].rule_name == "title_review"
    assert decisions[0].target_id == "062A"


def test_evaluate_tiebreak_same_tier_lowest_target_id_wins() -> None:
    # "Keroro" filler only -> 62A and 62B BOTH give keroro_large (catalog, not attributable)
    # -> single-winner fallback -> tie-break by target_id: 062A < 062B.
    decisions = _canonical_engine().evaluate(FileCandidate(filename="Keroro rediffusion.mkv"))
    assert len(decisions) == 1
    assert decisions[0].tier == "catalog"
    assert decisions[0].rule_name == "keroro_large"
    assert decisions[0].target_id == "062A"


def test_evaluate_tiebreak_same_tier_lowest_rule_index_wins_over_target_id() -> None:
    config = parse_matcher_config(_INDEX_TIEBREAK_RAW)
    target_high = TargetSegment(
        season=2, seasonal_number=99, absolute_number=99, segment="z", title="zzz unrelated"
    )
    target_low = TargetSegment(
        season=2,
        seasonal_number=1,
        absolute_number=1,
        segment="a",
        title="Les demoiselles cambrioleuses",
    )
    engine = MatchingEngine(config, (target_low, target_high))
    candidate = FileCandidate(filename="N°099Z Les demoiselles cambrioleuses.avi")
    # by_segment / by_title are NOT attributable rule names -> single-winner fallback.
    # Index 0 (by_segment on 099Z) prevails over index 1 (by_title on 001A), DESPITE
    # 001A < 099Z: the rule index breaks the tie BEFORE the target_id.
    decisions = engine.evaluate(candidate)
    assert len(decisions) == 1
    assert decisions[0].rule_name == "by_segment"
    assert decisions[0].target_id == "099Z"


def test_evaluate_rejects_filename_over_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=16)
    assert engine.evaluate(FileCandidate(filename="Keroro N°062A.avi")) == []


def test_evaluate_accepts_filename_at_or_below_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=4096)
    decisions = engine.evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert len(decisions) == 1
    assert decisions[0].tier == "download"


def test_evaluate_explanation_lists_coverage_value_even_below_threshold() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename="keroro rediffusion.mkv"))
    assert len(decisions) == 1
    coverage_names = [name for name, _ in decisions[0].explanation.coverage_values]
    assert "title_hit" in coverage_names
    assert "title_hit" not in decisions[0].explanation.tokens_matched


def test_explanation_on_real_62a_lists_fired_rules_tokens_and_coverage() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert len(decisions) == 1
    explanation = decisions[0].explanation
    assert explanation.target_id == "062A"
    # The real 62A fires on 4 rules. segment_id_loose does not fire (the trailing letter of
    # "062A" fails its digit boundary), so numero_nu_confirmed / numero_nu are absent;
    # archive_candidate is absent (no archive).
    assert explanation.rules_fired == (
        "id_segment_exact",
        "title_confirmed",
        "title_review",
        "keroro_large",
    )
    assert "title_hit" in explanation.tokens_matched
    assert "keroro" in explanation.tokens_matched
    assert "segment_id" in explanation.tokens_matched
    assert explanation.tokens_matched == tuple(sorted(explanation.tokens_matched))
    assert explanation.coverage_values == (("title_hit", 1.0),)


def test_explanation_single_rule_fired_and_no_coverage_token() -> None:
    raw: dict[str, object] = {
        "tokens": {
            "is_video": {"regex": r"\.(avi|mkv)$"},
            "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        },
        "rules": [{"name": "only", "tier": "download", "all": ["is_video", "seg"]}],
    }
    engine = MatchingEngine(parse_matcher_config(raw), (_TARGET_62A,))
    decisions = engine.evaluate(FileCandidate(filename="N°062A.avi"))
    assert len(decisions) == 1
    assert decisions[0].explanation.rules_fired == ("only",)
    assert decisions[0].explanation.coverage_values == ()
    assert decisions[0].explanation.tokens_matched == ("is_video", "seg")
```

- [ ] **Step 6 (delete obsolete block):** remove `test_engine.py` **lines 311-426** in full (the `{mono_gate}` mono-routing banner, `_TARGET_MONO`, `_MONO_ROUTING_RAW`, `_mono_routing_engine`, and all six `test_evaluate_bare_number_*` / `test_evaluate_lettered_*` / `test_evaluate_opening_*` tests). Their replacements arrive in Task 3.

- [ ] **Step 7 (empty-list contract test):** append after `test_evaluate_discards_non_keroro_file`:

```python
def test_evaluate_returns_empty_list_not_none_for_discard() -> None:
    # The discard sentinel is now [] (a list), never None (spec §4).
    decisions = _canonical_engine().evaluate(FileCandidate(filename="totally unrelated.txt"))
    assert decisions == []
    assert isinstance(decisions, list)
```

- [ ] **Step 8 (golden-corpus harness):** replace `test_golden_corpus` (lines 40-49) in `test_golden_corpus.py`:

```python
@pytest.mark.parametrize("case", _CASES, ids=[str(c["id"]) for c in _CASES])
def test_golden_corpus(case: dict[str, Any]) -> None:
    engine = _engine()
    decisions = engine.evaluate(FileCandidate(filename=str(case["filename"])))
    if case.get("discarded", False):
        assert decisions == [], f"{case['id']}: expected discarded, got {decisions}"
        return
    assert len(decisions) == 1, f"{case['id']}: expected one decision, got {decisions}"
    decision = decisions[0]
    assert decision.tier == case["tier"], f"{case['id']}: tier"
    assert decision.target_id == case["target_id"], f"{case['id']}: target"
    assert decision.rule_name == case["rule_name"], f"{case['id']}: rule"
```

- [ ] **Step 9 (property test):** in `test_engine_properties.py` replace the import (line 3):

```python
from catalog_matching.engine import _TIER_RANK, MatchDecision, MatchingEngine
```

Remove the inner `from catalog_matching.engine import _TIER_RANK` (line 107) and replace `test_property_higher_priority_rule_never_lowers_tier` (lines 103-131) with a max-tier form (leave `test_property_decision_invariant_under_target_reordering` untouched — it already compares lists). Insert `_max_tier` at module scope above the function:

```python
def _max_tier(decisions: list[MatchDecision]) -> int | None:
    return max((_TIER_RANK[d.tier] for d in decisions), default=None)


def test_property_higher_priority_rule_never_lowers_tier() -> None:
    # P2: prepending a higher-priority rule never lowers the strongest resulting tier (§16).
    config_base = parse_matcher_config(_CANONICAL_RAW)
    raw_boosted = {
        "tokens": dict(_CANONICAL_RAW["tokens"]),  # type: ignore[call-overload]
        "rules": [
            {"name": "boost_keroro_download", "tier": "download", "any": ["keroro_titar"]},
            *_CANONICAL_RAW["rules"],  # type: ignore[misc]
        ],
    }
    config_boosted = parse_matcher_config(raw_boosted)
    targets = _targets()
    engine_base = MatchingEngine(config_base, targets)
    engine_boosted = MatchingEngine(config_boosted, targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        base_tier = _max_tier(engine_base.evaluate(candidate))
        if base_tier is None:
            continue  # a discarded file may stay discarded
        boosted_tier = _max_tier(engine_boosted.evaluate(candidate))
        assert boosted_tier is not None, f"{filename!r}: decided without boost, discarded with it?!"
        assert boosted_tier >= base_tier, f"{filename!r}: a higher-priority rule LOWERED the tier"
```

- [ ] **Step 10:** `uv run poe fix` (import-sort/format), review the diff.
- [ ] **Step 11:** full gate `( cd packages/matching && uv run pytest )` → all pass, coverage 100%.
- [ ] **Step 12 (commit):** `git commit -am "refactor(domain): MatchingEngine.evaluate returns list[MatchDecision]"`

## Task 3 — Multi-target fan-out + drop `{mono_gate}` from the policy

**Files**
- Modify: `deploy/config/crawler/matcher.yml` (line 18, `segment_id_loose`).
- Modify: `packages/matching/src/catalog_matching/engine.py` (`evaluate` from Task 2; add `_fan_out` + `_single_winner`).
- Modify (Test): `packages/matching/tests/test_engine.py` (add fan-out engine + §11 tests).

**Interfaces**
- Consumes: `_ATTRIBUTABLE`, `_SEGMENT_LEVEL` (Task 1); `ResolvedTarget.target.absolute_number`, `.target_id`; `_first_matching_rule`, `_explain`, `_TIER_RANK`.
- Produces: `MatchingEngine.evaluate(candidate) -> list[MatchDecision]` with N-decision fan-out; private methods `_fan_out(self, candidate, matches) -> list[MatchDecision]` and `_single_winner(self, candidate, matches) -> list[MatchDecision]` where `matches: list[tuple[ResolvedTarget, int, str, str]]`.

- [ ] **Step 1 (RED):** append the fan-out fixtures + tests to `test_engine.py`:

```python
# --- Multi-target fan-out (spec §3/§4/§11) ------------------------------------------------
_TARGET_94A = TargetSegment(
    season=2,
    seasonal_number=43,
    absolute_number=94,
    segment="a",
    title="La Terre est à nous !",
)


def _fanout_engine() -> MatchingEngine:
    # Canonical prod policy over a bi-segment episode (62A/62B) plus a mono episode (94A):
    # enough to exercise every §3/§11 fan-out branch against the shipped matcher.yml.
    config = parse_matcher_config(_CANONICAL_RAW)
    return MatchingEngine(config, (_TARGET_62A, _TARGET_62B, _TARGET_94A))


def _triples(decisions: list[MatchDecision]) -> list[tuple[str, str, str]]:
    return [(d.target_id, d.tier, d.rule_name) for d in decisions]


def test_evaluate_bare_number_fans_out_to_both_segments() -> None:
    # §11 clean bare number: no segment-level signal -> both segments emitted (rule 2).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 62.avi"))
    assert _triples(decisions) == [
        ("062A", "notify", "numero_nu"),
        ("062B", "notify", "numero_nu"),
    ]


def test_evaluate_title_a_plus_bare_number_pins_segment_a_only() -> None:
    # §11 title A + bare number: the segment-level title cuts the fan-out (rule 1) -> A only.
    decisions = _fanout_engine().evaluate(
        FileCandidate(filename="Keroro 62 Les demoiselles cambrioleuses.avi")
    )
    assert _triples(decisions) == [("062A", "notify", "title_review")]


def test_evaluate_both_segment_titles_emit_both_segments() -> None:
    # §11 both titles present: each title pins its own segment (rule 1) -> both emitted.
    decisions = _fanout_engine().evaluate(
        FileCandidate(
            filename="Keroro Les demoiselles cambrioleuses Le grand combat sous-marin.avi"
        )
    )
    assert _triples(decisions) == [
        ("062A", "notify", "title_review"),
        ("062B", "notify", "title_review"),
    ]


def test_evaluate_bare_number_with_source_marker_fans_out_to_download() -> None:
    # §3 row 3: bare number + source marker -> both segments in download (no tier cap).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 62 teletoon.avi"))
    assert _triples(decisions) == [
        ("062A", "download", "numero_nu_confirmed"),
        ("062B", "download", "numero_nu_confirmed"),
    ]


def test_evaluate_mono_episode_bare_number_emits_single_segment() -> None:
    # §11 mono episode: a single-segment episode fans out to exactly one segment.
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 94.avi"))
    assert _triples(decisions) == [("094A", "notify", "numero_nu")]


def test_evaluate_out_of_range_number_falls_back_to_catalog() -> None:
    # §11 out of range: no number rule matches any target -> step-6 fallback -> keroro_large,
    # tie-broken to the smallest target_id (062A).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 130.avi"))
    assert _triples(decisions) == [("062A", "catalog", "keroro_large")]


def test_evaluate_lettered_segment_pins_that_segment_only() -> None:
    # §3 row 4: a lettered number (N°062A) is segment-level -> only that segment (rule 1).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert _triples(decisions) == [("062A", "download", "id_segment_exact")]
```

- [ ] **Step 2 (RED run):** `( cd packages/matching && uv run pytest tests/test_engine.py::test_evaluate_bare_number_fans_out_to_both_segments --no-cov -q )` → FAIL: `AssertionError` (with `{mono_gate}` still present, `segment_id_loose` never fires → single-winner falls to `keroro_large` catalog, not the fan-out expectation).

- [ ] **Step 3 (GREEN part A):** in `deploy/config/crawler/matcher.yml` line 18, remove the leading `{mono_gate}`:

```yaml
  segment_id_loose: { regex: "(?:^|[^0-9A-Za-z])0*(?:{absolute_number}|{seasonal_number})(?:[^0-9A-Za-z]|$)" }
```

- [ ] **Step 4 (GREEN part B):** in `engine.py`, replace `evaluate` (Task 2 version) with the fan-out dispatcher plus two helpers:

```python
    def evaluate(self, candidate: FileCandidate) -> list[MatchDecision]:
        """All decisions for ``candidate`` (spec §4); ``[]`` = file discarded.

        Length-bound first. Then, per target, its first true rule. The attributable matches
        (number/title video rules) fan out per episode: a segment-level signal on any segment
        of an episode emits only those segments, otherwise the episode-level signal emits
        every segment (spec §3). With no attributable match, the single-winner min-key over
        ALL matches yields one catch-all decision (the ``keroro_large`` catalog row or an
        ``archive_candidate`` row), or ``[]`` if nothing matched at all.
        """
        if len(candidate.filename) > self._max_filename_length:
            return []
        # entry = (resolved_target, rule_index, rule_name, tier)
        matches: list[tuple[ResolvedTarget, int, str, str]] = []
        for resolved in self._resolved:
            outcome = _first_matching_rule(self._config, resolved, candidate)
            if outcome is None:
                continue
            index, rule_name, tier = outcome
            matches.append((resolved, index, rule_name, tier))
        attributable = self._fan_out(candidate, matches)
        if attributable:
            return attributable
        return self._single_winner(candidate, matches)

    def _fan_out(
        self,
        candidate: FileCandidate,
        matches: list[tuple[ResolvedTarget, int, str, str]],
    ) -> list[MatchDecision]:
        """Selects the emitted segments from the attributable matches (spec §3/§4)."""
        by_episode: dict[int, list[tuple[ResolvedTarget, int, str, str]]] = {}
        for entry in matches:
            if entry[2] not in _ATTRIBUTABLE:
                continue
            by_episode.setdefault(entry[0].target.absolute_number, []).append(entry)
        emitted: list[tuple[ResolvedTarget, int, str, str]] = []
        for group in by_episode.values():
            segment_level = [entry for entry in group if entry[2] in _SEGMENT_LEVEL]
            emitted.extend(segment_level or group)
        emitted.sort(key=lambda entry: entry[0].target.target_id)
        return [
            MatchDecision(
                target_id=resolved.target.target_id,
                rule_name=rule_name,
                tier=tier,
                explanation=_explain(self._config, resolved, candidate),
            )
            for resolved, _index, rule_name, tier in emitted
        ]

    def _single_winner(
        self,
        candidate: FileCandidate,
        matches: list[tuple[ResolvedTarget, int, str, str]],
    ) -> list[MatchDecision]:
        """Existing min-key: one catch-all decision over ALL matches, or ``[]`` (spec §4 step 6).

        Key = (highest tier, smallest rule index, smallest ``target_id``); ``target_id`` is
        unique, so the key is a strict total order -> a unique, target-order-independent winner.
        """
        best: tuple[int, int, str] | None = None
        best_entry: tuple[ResolvedTarget, int, str, str] | None = None
        for entry in matches:
            resolved, index, _rule_name, tier = entry
            key = (-_TIER_RANK[tier], index, resolved.target.target_id)
            if best is None or key < best:
                best = key
                best_entry = entry
        if best_entry is None:
            return []
        resolved, _index, rule_name, tier = best_entry
        return [
            MatchDecision(
                target_id=resolved.target.target_id,
                rule_name=rule_name,
                tier=tier,
                explanation=_explain(self._config, resolved, candidate),
            )
        ]
```

- [ ] **Step 5:** confirm no `{mono_gate}` mention remains in `test_engine.py` (Task 2 already reworded the 62A explanation comment).
- [ ] **Step 6 (GREEN run):** `( cd packages/matching && uv run pytest tests/test_engine.py -k "fans_out or pins_ or emit_both or mono_episode or out_of_range or lettered_segment" --no-cov -q )` → `7 passed`.
- [ ] **Step 7:** full gate `( cd packages/matching && uv run pytest )` → all pass, coverage 100% (both `if best is None or key < best` sides; `entry[2] not in _ATTRIBUTABLE` via the mixed 94A-catalog + 62 attributable cases; `segment_level or group` via title-A vs bare-number cases).
- [ ] **Step 8:** `uv run poe fix`, review diff.
- [ ] **Step 9 (commit):** `git commit -am "feat(domain): fan out whole-episode files to per-segment decisions"`

## Task 4 — Golden corpus: multi-decision support + §3 table-row cases

**Files**
- Modify (Test): `packages/matching/tests/test_golden_corpus.py` (`test_golden_corpus`; add a guard test).
- Modify (Test data): `packages/matching/tests/fixtures/golden_corpus.yaml` (append cases).

**Interfaces**
- Consumes: `engine.evaluate -> list[MatchDecision]`; golden targets fixture already provides `062A`, `062B`, `094A`.
- Produces: corpus cases with an optional `decisions:` list alongside the existing scalar / `discarded` forms.

- [ ] **Step 1 (RED):** append to `golden_corpus.yaml`:

```yaml
  # --- Multi-target fan-out (spec §3/§11): a whole-episode file resolves BOTH segments. ---
  - id: bare_number_fans_out_both_segments
    # §3 row 1 / §11 clean bare number: no segment-level signal -> 062A + 062B (rule 2).
    filename: "Keroro 62.avi"
    decisions:
      - { target_id: 062A, tier: notify, rule_name: numero_nu }
      - { target_id: 062B, tier: notify, rule_name: numero_nu }

  - id: bare_number_with_source_marker_fans_out_download
    # §3 row 3: bare number + source marker -> both segments in download (no tier cap).
    filename: "Keroro 62 teletoon.avi"
    decisions:
      - { target_id: 062A, tier: download, rule_name: numero_nu_confirmed }
      - { target_id: 062B, tier: download, rule_name: numero_nu_confirmed }

  - id: both_segment_titles_fan_out_both_segments
    # §11 both titles present: each title pins its own segment (rule 1) -> 062A + 062B.
    filename: "Keroro Les demoiselles cambrioleuses Le grand combat sous-marin.avi"
    decisions:
      - { target_id: 062A, tier: notify, rule_name: title_review }
      - { target_id: 062B, tier: notify, rule_name: title_review }

  - id: title_a_plus_bare_number_pins_a_only
    # §3 row 2 / §11: title A cuts the fan-out (rule 1) -> a single decision on 062A.
    filename: "Keroro 62 Les demoiselles cambrioleuses.avi"
    tier: notify
    target_id: 062A
    rule_name: title_review
```

- [ ] **Step 2 (RED run):** `( cd packages/matching && uv run pytest "tests/test_golden_corpus.py::test_golden_corpus[bare_number_fans_out_both_segments]" --no-cov -q )` → FAIL: `AssertionError: ... expected one decision` (harness has no `decisions:` branch).

- [ ] **Step 3 (GREEN):** insert the multi-decision branch into `test_golden_corpus` (between the `discarded` early-return and `assert len(decisions) == 1`):

```python
    if "decisions" in case:
        expected = [
            (str(d["target_id"]), str(d["tier"]), str(d["rule_name"])) for d in case["decisions"]
        ]
        got = [(d.target_id, d.tier, d.rule_name) for d in decisions]
        assert got == expected, f"{case['id']}: fan-out mismatch: {got} != {expected}"
        return
```

- [ ] **Step 4 (guard):** append near `test_corpus_covers_every_tier_and_a_discard`:

```python
def test_corpus_has_a_multi_decision_fan_out_case() -> None:
    # The fan-out contract (spec §3) is exercised: at least one case emits >1 decision.
    assert any("decisions" in c and len(c["decisions"]) > 1 for c in _CASES)
```

- [ ] **Step 5 (GREEN run):** `( cd packages/matching && uv run pytest tests/test_golden_corpus.py --no-cov -q )` → all corpus cases pass.
- [ ] **Step 6:** full gate `( cd packages/matching && uv run pytest )` → all pass, coverage 100%.
- [ ] **Step 7 (commit):** `git commit -am "test(matching): golden-corpus multi-decision fan-out cases"`

## Task 5 — Retire the `{mono_gate}` interpolation placeholder

**Files**
- Modify: `packages/matching/src/catalog_matching/interpolation.py` (docstring lines 17-24; branch lines 39-40).
- Modify (Test): `packages/matching/tests/test_interpolation.py` (replace the two `mono_gate` tests, lines 49-58).

**Interfaces**
- Produces: `interpolate(pattern, target)` raises `InterpolationError` on `{mono_gate}`.

- [ ] **Step 1 (RED):** in `test_interpolation.py`, delete `test_interpolate_mono_gate_empty_for_sole_segment` and `test_interpolate_mono_gate_never_match_for_multi_segment` (lines 49-58) and add:

```python
def test_interpolate_mono_gate_is_now_unknown() -> None:
    # {mono_gate} was retired with the multi-target fan-out: now an unknown placeholder.
    with pytest.raises(InterpolationError, match="mono_gate"):
        interpolate(r"{mono_gate}KEROW", _target())
```

- [ ] **Step 2 (RED run):** `( cd packages/matching && uv run pytest tests/test_interpolation.py::test_interpolate_mono_gate_is_now_unknown --no-cov -q )` → FAIL: `DID NOT RAISE InterpolationError`.

- [ ] **Step 3 (GREEN):** in `interpolation.py`, remove the `mono_gate` branch (lines 39-40) so the tail reads:

```python
        if name == "title":
            return str(re.escape(target.title))
        raise InterpolationError(f"unknown placeholder: {{{name}}}")
```

- [ ] **Step 4:** update the `interpolate` docstring (lines 17-24) to drop the `{mono_gate}` mention:

```python
def interpolate(pattern: str, target: TargetSegment) -> str:
    """Substitutes the whitelist ``{season} {seasonal_number} {absolute_number} {segment}
    {title}``.

    All values are inserted ``re.escape``-d (literal). Any other placeholder raises
    :class:`InterpolationError`.
    """
```

- [ ] **Step 5 (GREEN run):** `( cd packages/matching && uv run pytest tests/test_interpolation.py --no-cov -q )` → all pass.
- [ ] **Step 6:** full gate `( cd packages/matching && uv run pytest )` → all pass, coverage 100%.
- [ ] **Step 7:** `uv run poe fix`, review diff.
- [ ] **Step 8 (commit):** `git commit -am "refactor(domain): retire {mono_gate} interpolation placeholder"`

## Task 6 — Remove the unused `sole_segment` target field + refresh the stale fixture comment

After Task 5 nothing reads `sole_segment` (grep to confirm: only `validation.parse_targets` sets it and tests assert it).

**Files**
- Modify: `packages/matching/src/catalog_matching/models.py` (docstring lines 24-29; field line 37).
- Modify: `packages/matching/src/catalog_matching/validation.py` (`parse_targets` lines 411-424).
- Modify (Test): `packages/matching/tests/test_models.py` (`test_target_segment_defaults`, line 30).
- Modify (Test): `packages/matching/tests/test_validation.py` (the two `sole_segment` tests, lines 321-353).
- Modify (Test fixture comment): `packages/matching/tests/fixtures/golden_targets.yaml` (lines 10-12, the stale `mono_gate` explanation).

**Interfaces**
- Produces: `TargetSegment(season, seasonal_number, absolute_number, segment, title, status="lost")` — `sole_segment` removed; `.target_id` unchanged.

- [ ] **Step 1 (RED):** convert the two `test_validation.py` `sole_segment` tests (lines 321-353) into field-free assertions:

```python
def test_parse_targets_builds_mono_episode_target() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 1,
                    "seasonal_number": 10,
                    "absolute_number": 10,
                    "segments": [{"letter": "A", "title": "x"}],
                }
            ]
        }
    )
    assert [t.target_id for t in targets] == ["010A"]


def test_parse_targets_builds_two_segment_targets() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 2,
                    "seasonal_number": 11,
                    "absolute_number": 62,
                    "segments": [
                        {"letter": "A", "title": "x"},
                        {"letter": "B", "title": "y"},
                    ],
                }
            ]
        }
    )
    assert [t.target_id for t in targets] == ["062A", "062B"]
```

And in `test_models.py`, drop the `sole_segment` assertion from `test_target_segment_defaults` (line 30):

```python
def test_target_segment_defaults() -> None:
    target = TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="a", title="Les demoiselles"
    )
    assert target.status == "lost"
```

- [ ] **Step 2 (RED run):** `( cd packages/matching && uv run pytest tests/test_validation.py --no-cov -q )` → the OLD names `test_parse_targets_marks_sole_segment_for_mono_episode` are gone (no collection error); the field-removal RED is next.

- [ ] **Step 3 (GREEN):** in `models.py`, remove the field (line 37) and reword the docstring (lines 24-29):

```python
@dataclass(frozen=True)
class TargetSegment:
    """A target episode segment (segment granularity, cf. spec §7).

    Provides ``{season} {seasonal_number} {absolute_number} {segment} {title}`` to the
    interpolation of regex patterns.
    """

    season: int
    seasonal_number: int
    absolute_number: int
    segment: str
    title: str
    status: str = "lost"

    @property
    def target_id(self) -> str:
        """Stable segment id: zero-padded absolute number + segment letter, e.g. ``062A``."""
        return f"{self.absolute_number:03d}{self.segment.upper()}"
```

- [ ] **Step 4 (GREEN):** in `validation.py` `parse_targets`, drop the `sole` derivation and the `sole_segment=sole` argument (lines 411-424):

```python
        seg_list = ep.get("segments", [])
        for seg in seg_list:
            seg_map = _require_mapping(seg, "segment")
            segments.append(
                TargetSegment(
                    season=season,
                    seasonal_number=seasonal_number,
                    absolute_number=absolute_number,
                    segment=str(_require_key(seg_map, "letter", "segment")),
                    title=str(_require_key(seg_map, "title", "segment")),
                    status=str(seg_map.get("status", "lost")),
                )
            )
```

- [ ] **Step 5 (refresh stale comment):** in `golden_targets.yaml` replace the mono-target comment (lines 10-12) so it no longer references `mono_gate`:

```yaml
  # Mono-segment target (single letter A, no sibling B): exercises the fan-out over an episode
  # that has exactly one segment (spec §11 "mono episode" row).
```

- [ ] **Step 6 (GREEN run):** `( cd packages/matching && uv run pytest tests/test_models.py tests/test_validation.py --no-cov -q )` → all pass. Confirm types: `( cd packages/matching && uv run poe type-check )` → no `unexpected keyword argument "sole_segment"`.
- [ ] **Step 7:** full gate `( cd packages/matching && uv run pytest )` → all pass, coverage 100%.
- [ ] **Step 8:** `uv run poe fix`; sanity grep `grep -rn sole_segment packages/matching` → no matches.
- [ ] **Step 9 (commit):** `git commit -am "refactor(domain): remove unused sole_segment target field"`

### Phase 1 exit check
- `( cd packages/matching && uv run pytest )` green + `uv run poe lint-all` green.
- `test_prod_policy_has_exactly_one_catalog_tier_rule_named_keroro_large` remains green (policy still has exactly one `catalog` rule).
- The `packages/crawler` gate is expected RED (its `evaluate` caller still assumes `MatchDecision | None`); resolved in Phase 2 (ship in one PR, spec §13).

---

# Phase 2 — Persistence + Application (`packages/crawler`)

**Preconditions (Phase 1, lands together):** `MatchingEngine.evaluate(candidate) -> list[MatchDecision]` (empty = discarded); `matcher.yml` has had `{mono_gate}` removed so the real-engine conftest fans out `Keroro 062 ...` → `[062A, 062B]`. Full gate: `( cd packages/crawler && uv run pytest )`. After any SQL edit: `uv run poe sql-fix && uv run poe sql-lint`.

**Internal hard cut.** `last_decision → last_decisions` and `record_retraction(hash) → record_retraction(hash, target_id)` are consumed atomically by `decisions.py`, whose return type also changes `bool → int` and ripples to `record_observation`/`reevaluate_catalog`. Tasks 1-2 are additive (new read alongside the old). Task 3 is the atomic application+retraction cut. Task 4 removes the now-dead `last_decision`. Tasks 5-6 are §10/§8 guard tests (spec mandates no code change there — expected green on first run).

## Task 1 — Persistence read: `last_decisions` (latest verdict per target)

Additive: add `last_decisions` (dict per `target_id`), keep `last_decision`. No DDL (`migrations/catalog/0001_initial.sql:63-73` — append-only, no UNIQUE).

**Files**
- Modify `src/mulewatch/adapters/persistence_sqlite/catalog_repository.py` (add const after `_SELECT_LAST_DECISION` ~line 70; method after `last_decision` ~line 208).
- Modify `src/mulewatch/ports/catalog_repository.py` (Protocol method after `last_decision` ~line 73).
- Create `tests/adapters/persistence_sqlite/test_catalog_last_decisions.py`.
- Modify `tests/ports/test_catalog_repository.py` (stub + assertion).

**Interfaces**
- Produces: `CatalogRepository.last_decisions(ed2k_hash: str) -> dict[str, DecisionRecord]` — latest row per `(ed2k_hash, target_id)`, INCLUDING targets whose latest tier is `retracted`, EXCLUDING legacy `target_id = ''` sentinel rows; `{}` when never decided / unknown hash.
- Consumes: `DecisionRecord(target_id, rule_name, tier)`.

- [ ] **Step 1a (RED):** create `tests/adapters/persistence_sqlite/test_catalog_last_decisions.py`:

```python
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import DecisionRecord, Explanation, MatchDecision
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER

_A = "a" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(hash_hex: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=hash_hex,
        filename="Keroro.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


def _decision(rule_name: str, tier: str, target_id: str = "062A") -> MatchDecision:
    return MatchDecision(
        target_id=target_id,
        rule_name=rule_name,
        tier=tier,
        explanation=Explanation(
            target_id=target_id, rules_fired=(rule_name,), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())


def test_last_decisions_is_empty_when_never_decided(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    assert repository.last_decisions(_A) == {}


def test_last_decisions_returns_the_latest_record_per_target(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("keroro_large", "catalog", "062A"))
    repository.record_decision(_A, _decision("id_segment_exact", "download", "062A"))  # newer
    repository.record_decision(_A, _decision("numero_nu", "notify", "062B"))
    assert repository.last_decisions(_A) == {
        "062A": DecisionRecord(target_id="062A", rule_name="id_segment_exact", tier="download"),
        "062B": DecisionRecord(target_id="062B", rule_name="numero_nu", tier="notify"),
    }


def test_last_decisions_includes_a_target_whose_latest_tier_is_retracted(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("id_segment_exact", "download", "062A"))
    repository.record_decision(_A, _decision("", RETRACTED_TIER, "062A"))
    assert repository.last_decisions(_A) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }


def test_last_decisions_excludes_the_legacy_empty_sentinel(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("numero_nu", "notify", "062A"))
    repository.record_decision(_A, _decision("", RETRACTED_TIER, ""))  # legacy target_id="" row
    assert repository.last_decisions(_A) == {
        "062A": DecisionRecord(target_id="062A", rule_name="numero_nu", tier="notify")
    }


def test_last_decisions_for_unknown_hash_is_empty(
    repository: SqliteCatalogRepository,
) -> None:
    assert repository.last_decisions(_A) == {}
```

- [ ] **Step 1b (RED run):** `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_last_decisions.py --no-cov -q )` → `AttributeError: 'SqliteCatalogRepository' object has no attribute 'last_decisions'`.

- [ ] **Step 1c (GREEN):** in `catalog_repository.py`, add the const after the `_SELECT_LAST_DECISION` block (after line 70):

```python
# Latest verdict per (ed2k_hash, target_id) for one hash (set-diff anti-redundancy, spec §7).
# ROW_NUMBER per target, order (decided_at, id) DESCENDING (most recent = rank 1); keep rank 1.
# INCLUDES a target whose latest tier is 'retracted' (no tier filter); EXCLUDES the legacy
# target_id='' sentinel (not a real target). The idx_match_decisions_ed2k_hash index serves
# the filter.
_SELECT_LAST_DECISIONS = """
SELECT target_id, rule_name, tier FROM (
    SELECT
        target_id, rule_name, tier,
        ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY decided_at DESC, id DESC) AS rn
    FROM match_decisions
    WHERE ed2k_hash = ? AND target_id <> ''
) WHERE rn = 1
"""
```

Add the method after `last_decision` (after line 208):

```python
    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]:
        """Latest verdict per target for this hash (set-diff anti-redundancy, spec §7) — READ.

        Maps ``target_id`` → its latest :class:`DecisionRecord`. INCLUDES a target whose latest
        tier is ``retracted`` (the application's set-diff skips re-retracting it); EXCLUDES the
        legacy ``target_id=""`` sentinel. The hash is NOT validated canonical (harmless read: a
        non-canonical hash matches nothing → ``{}``).
        """
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_LAST_DECISIONS, (ed2k_hash,)).fetchall()
        return {
            row[0]: DecisionRecord(target_id=row[0], rule_name=row[1], tier=row[2])
            for row in rows
        }
```

- [ ] **Step 1d (GREEN run):** `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_last_decisions.py --no-cov -q )` → passes; then `uv run poe sql-lint`.

- [ ] **Step 1e (Protocol + stub):** in `ports/catalog_repository.py`, add after `last_decision` (line 73):

```python
    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]: ...
```

In `tests/ports/test_catalog_repository.py`, add to `_StubRepository` (after `last_decision`, line 33):

```python
    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]:
        return {}
```

and add an assertion after line 85 (`assert repository.last_decision(...) is None`):

```python
    assert repository.last_decisions(observation.ed2k_hash) == {}
```

- [ ] **Step 1f (GREEN run):** `( cd packages/crawler && uv run pytest tests/ports/test_catalog_repository.py --no-cov -q )` → passes; `uv run poe type-check`.
- [ ] **Step 1g (commit):** `feat(persistence): add last_decisions read (latest verdict per target)`

## Task 2 — Persistence read: `download_decisions` per `(hash, target_id)`

`_SELECT_DOWNLOAD_DECISIONS` window `PARTITION BY ed2k_hash` → `PARTITION BY ed2k_hash, target_id`, so a two-segment file yields BOTH download candidates.

**Files**
- Modify `src/mulewatch/adapters/persistence_sqlite/catalog_repository.py` (const `_SELECT_DOWNLOAD_DECISIONS`, lines 77-85).
- Modify `tests/adapters/persistence_sqlite/test_catalog_download_reads.py` (generalize `_decision`, add two tests).

**Interfaces**
- Produces (unchanged signature): `CatalogRepository.download_decisions() -> tuple[DownloadCandidate, ...]` — one `DownloadCandidate(ed2k_hash, target_id)` per `(hash, target_id)` whose latest tier is `download`, ordered by `(ed2k_hash, target_id)`.

- [ ] **Step 2a (RED):** in `test_catalog_download_reads.py`, generalize the helper (backward-compatible default) — replace lines 41-49:

```python
def _decision(tier: str, target_id: str = "062A") -> MatchDecision:
    return MatchDecision(
        target_id=target_id,
        rule_name="r",
        tier=tier,
        explanation=Explanation(
            target_id=target_id, rules_fired=("r",), tokens_matched=(), coverage_values=()
        ),
    )
```

Then append two tests:

```python
def test_download_decisions_returns_both_segments_of_one_hash(
    repository: SqliteCatalogRepository,
) -> None:
    # spec §6: a whole-episode file both segments in download must NOT lose a candidate.
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("download", "062A"))
    repository.record_decision(_A, _decision("download", "062B"))
    assert repository.download_decisions() == (
        DownloadCandidate(ed2k_hash=_A, target_id="062A"),
        DownloadCandidate(ed2k_hash=_A, target_id="062B"),
    )


def test_download_decisions_isolates_per_target_within_one_hash(
    repository: SqliteCatalogRepository,
) -> None:
    # same hash, two targets: 062A latest=download, 062B latest=catalog → only 062A.
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("download", "062A"))
    repository.record_decision(_A, _decision("catalog", "062B"))
    assert repository.download_decisions() == (
        DownloadCandidate(ed2k_hash=_A, target_id="062A"),
    )
```

- [ ] **Step 2b (RED run):** `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_download_reads.py::test_download_decisions_returns_both_segments_of_one_hash --no-cov -q )` → FAIL: old `PARTITION BY ed2k_hash` returns only `(DownloadCandidate(_A, '062B'),)`.

- [ ] **Step 2c (GREEN):** replace `_SELECT_DOWNLOAD_DECISIONS` (lines 77-85):

```python
# Latest verdict per (ed2k_hash, target_id), kept when tier=download (download spec §5,
# multi-target §6). Window: ROW_NUMBER per (hash, target_id), order (decided_at, id)
# DESCENDING (most recent = rank 1); keep rank 1 AND tier='download'. PARTITION BY the FULL
# key so a whole-episode file with BOTH segments in download yields BOTH candidates. Stable
# sort by (hash, target_id) for a deterministic result.
_SELECT_DOWNLOAD_DECISIONS = """
SELECT ed2k_hash, target_id FROM (
    SELECT
        ed2k_hash, target_id, tier,
        ROW_NUMBER() OVER (
            PARTITION BY ed2k_hash, target_id ORDER BY decided_at DESC, id DESC
        ) AS rn
    FROM match_decisions
) WHERE rn = 1 AND tier = 'download'
ORDER BY ed2k_hash, target_id
"""
```

- [ ] **Step 2d (GREEN run):** `( cd packages/crawler && uv run pytest tests/adapters/persistence_sqlite/test_catalog_download_reads.py --no-cov -q )` → all pass; `uv run poe sql-lint`.
- [ ] **Step 2e (commit):** `feat(persistence): download_decisions partitions by (hash, target_id)`

## Task 3 — Application set-diff + per-target retraction (atomic cut)

The coupled change: `record_retraction` gains `target_id`; `decisions.py` moves to a set diff over `last_decisions` returning `int`; `record_observations`/`reevaluate_catalog` consume the `int`; every impacted test migrates. `domain/retraction.py` keeps `RETRACTED_TIER`, docstring updated.

**Files**
- Modify `src/mulewatch/domain/retraction.py` (docstring; `RETRACTED_TIER` unchanged).
- Modify `src/mulewatch/adapters/persistence_sqlite/catalog_repository.py` (`record_retraction`, lines 179-194).
- Modify `src/mulewatch/ports/catalog_repository.py` (`record_retraction` signature line 71 + docstring).
- Rewrite `src/mulewatch/application/decisions.py`.
- Modify `src/mulewatch/application/record_observations.py` (return `int`).
- Modify `src/mulewatch/application/reevaluate_catalog.py` (`written += count`).
- Rewrite `tests/application/test_decisions.py`.
- Modify `tests/application/test_record_observations.py` (bool→int + one multi-segment test).
- Modify `tests/application/test_reevaluate_catalog.py` (list `evaluate`, `last_decision`→`last_decisions`).
- Modify `tests/adapters/persistence_sqlite/test_catalog_repository.py` (retraction tests → new signature + `last_decisions`).
- Modify `tests/ports/test_catalog_repository.py` (stub `record_retraction` signature + assertion).

**Interfaces**
- Produces: `CatalogRepository.record_retraction(ed2k_hash: str, target_id: str) -> None` — appends `(target_id, rule_name="", tier=RETRACTED_TIER)`.
- Produces: `record_decision_if_changed(...) -> int` (rows written 0..N).
- Consumes: `MatchingEngine.evaluate -> list[MatchDecision]`, `to_record`, `last_decisions`, `record_decision`, `record_retraction`.

- [ ] **Step 3a (RED):** rewrite `tests/application/test_decisions.py`:

```python
"""Tests for the shared decision helper (spec §7): set diff keyed by (hash, target_id).

Real engine + real SQLite catalog repo (mirrors ``test_record_observations.py``: "real repos
on tmp_path"); only ``signal``/``telemetry`` are fakes. ``record_decision_if_changed`` writes
to ``match_decisions`` (FK-constrained on ``files``), so each hash is seeded via
``record_observation`` first, EXCEPT the never-matched case (never writes → FK never applies).
"""

import sqlite3

import pytest

from catalog_matching.engine import DecisionRecord, MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application.decisions import record_decision_if_changed
from mulewatch.application.record_observations import record_observation
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.observability.events import DecisionRecorded
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER
from tests.application.fakes import RecordingSignal, RecordingTelemetry

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_HASH_NEVER = "cccccccccccccccccccccccccccccccc"
_HASH_MULTI = "dddddddddddddddddddddddddddddddd"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_CAT_NAME = "keroro something.avi"
_DISCARD_NAME = "random.txt"
_MULTI_NAME = "Keroro 062 teletoon.avi"  # bare number + source marker → 062A + 062B download


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


async def _record(
    ed2k_hash: str,
    filename: str,
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    signal: RecordingSignal,
    telemetry: RecordingTelemetry,
) -> int:
    return await record_decision_if_changed(
        ed2k_hash,
        _obs(ed2k_hash, filename).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )


@pytest.mark.asyncio
async def test_new_decision_is_recorded_emitted_signalled_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_DL, _DL_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert catalog_connection.execute(
        "SELECT target_id, tier FROM match_decisions"
    ).fetchone() == ("062A", "download")
    assert telemetry.events == [DecisionRecorded(target_id="062A", tier="download")]
    assert signal.signalled == [_HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_multi_segment_file_records_both_segments_then_is_idempotent(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    assert written == 2
    assert catalog_connection.execute(
        "SELECT target_id, rule_name, tier FROM match_decisions ORDER BY id"
    ).fetchall() == [
        ("062A", "numero_nu_confirmed", "download"),
        ("062B", "numero_nu_confirmed", "download"),
    ]
    assert telemetry.events == [
        DecisionRecorded(target_id="062A", tier="download"),
        DecisionRecorded(target_id="062B", tier="download"),
    ]
    assert signal.signalled == [
        _HASH_MULTI, DOWNLOAD_NUDGE_SUBJECT, _HASH_MULTI, DOWNLOAD_NUDGE_SUBJECT
    ]
    again = await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    assert again == 0
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_changed_decision_is_reappended_emitted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_DL, _CAT_NAME, catalog, engine, signal, telemetry)
    written = await _record(_HASH_DL, _DL_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["catalog", "download"]
    assert [type(e).__name__ for e in telemetry.events] == ["DecisionRecorded", "DecisionRecorded"]
    assert signal.signalled == [_HASH_DL, _HASH_DL, DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_unchanged_decision_is_not_reappended_emitted_or_signalled(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    first = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    second = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    assert (first, second) == (1, 0)
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert len(telemetry.events) == 1
    assert signal.signalled == [_HASH_CAT]


@pytest.mark.asyncio
async def test_was_matched_then_none_retracts_that_target_without_nudge(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    signalled_before = list(signal.signalled)
    written = await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert catalog.last_decisions(_HASH_CAT) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }
    assert telemetry.events[-1] == DecisionRecorded(target_id="062A", tier=RETRACTED_TIER)
    assert signal.signalled == signalled_before
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled


@pytest.mark.asyncio
async def test_multi_segment_then_discard_retracts_both_segments(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    written = await _record(_HASH_MULTI, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 2
    assert catalog.last_decisions(_HASH_MULTI) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER),
        "062B": DecisionRecord(target_id="062B", rule_name="", tier=RETRACTED_TIER),
    }
    assert telemetry.events[-2:] == [
        DecisionRecorded(target_id="062A", tier=RETRACTED_TIER),
        DecisionRecorded(target_id="062B", tier=RETRACTED_TIER),
    ]


@pytest.mark.asyncio
async def test_already_retracted_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    rows_after = catalog_connection.execute(
        "SELECT count(*) FROM match_decisions"
    ).fetchone()[0]
    events_after = len(telemetry.events)
    written = await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 0
    assert catalog_connection.execute(
        "SELECT count(*) FROM match_decisions"
    ).fetchone()[0] == rows_after
    assert len(telemetry.events) == events_after


@pytest.mark.asyncio
async def test_never_matched_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_NEVER, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 0
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_non_download_tier_decision_does_not_nudge_the_download_subject(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert signal.signalled == [_HASH_CAT]
    assert DOWNLOAD_NUDGE_SUBJECT not in signal.signalled


@pytest.mark.asyncio
async def test_record_observation_retracts_a_reobserved_now_discarded_file(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    first = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry, network="ed2k",
    )
    second = await record_observation(
        _obs(_HASH_DL, _DISCARD_NAME),
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry, network="ed2k",
    )
    assert (first, second) == (1, 1)
    assert catalog.last_decisions(_HASH_DL) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }
    assert catalog_connection.execute(
        "SELECT count(*) FROM file_observations"
    ).fetchone()[0] == 2
```

- [ ] **Step 3b (RED run):** `( cd packages/crawler && uv run pytest tests/application/test_decisions.py --no-cov -q )` → FAIL (old `decisions.py` iterates a single `MatchDecision | None`; `record_retraction` takes one arg; returns `bool`).

- [ ] **Step 3c (GREEN — `domain/retraction.py`):** replace the docstring body (keep `RETRACTED_TIER = "retracted"`):

```python
"""Retraction: the sentinel tier recorded when a file stops matching a target (spec §7).

PURE domain. ``catalog.db``'s ``match_decisions`` table is append-only (DB triggers) — there
is no "delete" primitive for a stale decision. When the current matcher policy no longer
matches a previously-catalogued file for a given target, exclusion is represented as an
*appended* row carrying THAT ``target_id``: ``rule_name=""``, ``tier=RETRACTED_TIER``.
Retraction is PER TARGET: a whole-episode file can retract ``(hash, 072A)`` while leaving
``(hash, 072B)`` intact. (The legacy ``target_id=""`` sentinel — a whole-file retraction —
is no longer written; it survives in old catalogs and is simply ignored by the read side.)

``RETRACTED_TIER`` is deliberately NOT a member of ``catalog_matching.config.TIERS``
(``{"catalog", "notify", "download"}``): the matching engine never produces it — it is
synthesized by the crawler alone, on the "matched → no longer matched" transition.
"""

RETRACTED_TIER = "retracted"
```

- [ ] **Step 3d (GREEN — adapter `record_retraction`, lines 179-194):**

```python
    def record_retraction(self, ed2k_hash: str, target_id: str) -> None:
        """Appends a per-target ``retracted`` decision (spec §7).

        Mirrors ``record_decision`` (same canonical-hash guard, same autocommit ``INSERT``): a
        file that no longer matches ``target_id`` gets an appended
        ``(target_id, rule_name="", tier=RETRACTED_TIER)`` row instead of a mutation, per the
        append-only invariant. Retracting one target leaves the file's other targets intact.
        Unknown file → FK violated → ``PersistenceError``.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (ed2k_hash, target_id, "", RETRACTED_TIER, utc_iso(self._clock()), self._node_id),
            )
```

- [ ] **Step 3e (GREEN — port):** replace `ports/catalog_repository.py` line 71 and update the `record_retraction` docstring paragraph:

```python
    def record_retraction(self, ed2k_hash: str, target_id: str) -> None: ...
```

Docstring: replace the sentence about the `target_id=""` sentinel marking "a previously-matched file as no longer matching any target" with "appends a per-target `match_decisions` row (`rule_name=""`, `tier="retracted"`) marking `target_id` as no longer matching this file — the append-only table has no delete, so exclusion is an appended row."

- [ ] **Step 3f (GREEN — rewrite `application/decisions.py`):**

```python
"""Shared decision helper: evaluate → set-diff → record / retract → emit → nudge (spec §7).

APPLICATION layer, PURE orchestration (no ``try/except`` here — a ``RepositoryError`` is a
port contract each CALLER absorbs on its own terms, cf. ``record_observation`` and the backfill
use-case). Used by BOTH the per-observation pipeline (``record_observations.py``) and the
startup catalogue re-evaluation, so the set-diff + retraction + nudge logic is written once.

Set diff keyed by ``(ed2k_hash, target_id)`` (spec §7). ``engine.evaluate`` returns a LIST of
:class:`MatchDecision` — one per segment target the file covers, empty = discarded. A fresh
decision is persisted (and nudged) only when it differs from the file's LATEST persisted
:class:`DecisionRecord` for THAT target; a target that dropped out of the fresh set (was
matched, now absent) is retracted — unless it is already retracted (no-op). Returns the number
of rows written (0..N; a decision OR a retraction each counts as one).
"""

from catalog_matching.engine import MatchingEngine, to_record
from catalog_matching.models import FileCandidate
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.observability.events import DecisionRecorded
from mulewatch.domain.retraction import RETRACTED_TIER
from mulewatch.ports.catalog_repository import CatalogRepository
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.telemetry import Telemetry


async def record_decision_if_changed(
    ed2k_hash: str,
    candidate: FileCandidate,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
) -> int:
    """Evaluate ``candidate``; append new/changed decisions and retract dropped targets.

    Returns the number of rows written (0..N). May propagate ``RepositoryError`` (pure
    orchestration; the caller absorbs it)."""
    fresh = engine.evaluate(candidate)
    persisted = catalog.last_decisions(ed2k_hash)
    written = 0
    fresh_ids: set[str] = set()
    for decision in fresh:
        fresh_ids.add(decision.target_id)
        if persisted.get(decision.target_id) == to_record(decision):
            continue
        catalog.record_decision(ed2k_hash, decision)
        written += 1
        await telemetry.emit(DecisionRecorded(target_id=decision.target_id, tier=decision.tier))
        signal.signal(ed2k_hash)
        if decision.tier == "download":
            signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    for target_id in sorted(persisted):
        if persisted[target_id].tier == RETRACTED_TIER or target_id in fresh_ids:
            continue
        catalog.record_retraction(ed2k_hash, target_id)
        written += 1
        await telemetry.emit(DecisionRecorded(target_id=target_id, tier=RETRACTED_TIER))
    return written
```

- [ ] **Step 3g (GREEN — `application/record_observations.py`):** change the signature return to `int`, the docstring, keep the `return await record_decision_if_changed(...)`, and change the `except` branch `return False` → `return 0`:

```python
    network: str,
) -> int:
    """Process ONE observation (spec §7). Returns the number of rows written (0..N).

    Emits ``ObservationRecorded`` (always) and one ``DecisionRecorded`` per recorded verdict /
    retraction. A ``RepositoryError`` is absorbed (log + ``0``), the cycle continues (spec §7)."""
    try:
        catalog.record_observation(observation)
        await telemetry.emit(ObservationRecorded(network=network))
        return await record_decision_if_changed(
            observation.ed2k_hash,
            observation.to_candidate(),
            catalog=catalog,
            engine=engine,
            signal=signal,
            telemetry=telemetry,
        )
    except RepositoryError as error:
        _logger.error(
            "persistence failed on hash=%s (%s) — observation skipped, cycle continues",
            observation.ed2k_hash,
            error,
        )
        return 0
```

(`search_worker.py:252` `if await record_observation(...): changed += 1` is unchanged — a positive `int` is truthy, semantics identical, mypy-clean.)

- [ ] **Step 3h (GREEN — `application/reevaluate_catalog.py`):** replace the loop body (lines 60-77):

```python
        try:
            count = await record_decision_if_changed(
                row.ed2k_hash,
                candidate,
                catalog=catalog,
                engine=engine,
                signal=signal,
                telemetry=telemetry,
            )
        except RepositoryError as error:
            _logger.error(
                "persistence failed on hash=%s (%s) — re-evaluation skipped, sweep continues",
                row.ed2k_hash,
                error,
            )
            continue
        written += count
    return ReevalSummary(evaluated=evaluated, written=written)
```

- [ ] **Step 3i (migrate remaining tests):**
  - `tests/application/test_record_observations.py`: change every bool assertion to `int` (`changed is False` → `changed == 0`; `changed is True` → `changed == 1`; likewise lines 47, 69, 85-95, 97-107, 139, 171, 215, 235). Add one test:

```python
@pytest.mark.asyncio
async def test_multi_segment_observation_records_two_rows(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_DL, "Keroro 062 teletoon.avi"),
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry, network="ed2k",
    )
    assert changed == 2
    assert catalog_connection.execute(
        "SELECT target_id FROM match_decisions ORDER BY id"
    ).fetchall() == [("062A",), ("062B",)]
```

  - `tests/application/test_reevaluate_catalog.py`: migrate line 68-70 (list `evaluate`) and line 105 (`last_decision`→`last_decisions`):

```python
    decisions = engine.evaluate(candidate)
    assert decisions  # non-empty
    catalog.record_decision(_HASH_CAT, decisions[0])
```
```python
    assert catalog.last_decisions(_HASH_DL) == {}
```

  - `tests/adapters/persistence_sqlite/test_catalog_repository.py`: migrate the four retraction tests (lines 273-315) to the new signature + `last_decisions`:

```python
def test_record_retraction_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_retraction(_HASH, "062A")
    row = connection.execute(
        "SELECT ed2k_hash, target_id, rule_name, tier, decided_at, node_id FROM match_decisions"
    ).fetchone()
    assert row == (_HASH, "062A", "", RETRACTED_TIER, _FROZEN_ISO, _NODE)
    assert repository.last_decisions(_HASH) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }


def test_record_retraction_rejects_non_canonical_hash(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    with pytest.raises(PersistenceError, match="non-canonical eD2k hash"):
        repository.record_retraction("NOTAHASH", "062A")
    assert connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0


def test_record_retraction_for_unknown_file_raises_persistence_error(
    repository: SqliteCatalogRepository,
) -> None:
    with pytest.raises(PersistenceError, match="FOREIGN KEY"):
        repository.record_retraction("0" * 32, "062A")


def test_record_retraction_row_is_append_only(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_retraction(_HASH, "062A")
    with pytest.raises(sqlite3.IntegrityError, match="match_decisions is append-only"):
        connection.execute("UPDATE match_decisions SET tier = 'catalog'")
    with pytest.raises(sqlite3.IntegrityError, match="match_decisions is append-only"):
        connection.execute("DELETE FROM match_decisions")
    assert repository.last_decisions(_HASH) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }
```

  - `tests/ports/test_catalog_repository.py`: change stub `record_retraction` (line 28) to `def record_retraction(self, ed2k_hash: str, target_id: str) -> None:` appending `(ed2k_hash, target_id)`; update the `retractions` field type to `list[tuple[str, str]]`; call site line 84 → `repository.record_retraction(observation.ed2k_hash, "062A")`; assertion line 100 → `assert stub.retractions == [(observation.ed2k_hash, "062A")]`.

- [ ] **Step 3j (GREEN run):** `( cd packages/crawler && uv run pytest tests/application/test_decisions.py tests/application/test_record_observations.py tests/application/test_reevaluate_catalog.py tests/adapters/persistence_sqlite/test_catalog_repository.py tests/ports/test_catalog_repository.py --no-cov -q )` → all pass. Then `uv run poe fix` and full gate `( cd packages/crawler && uv run pytest )`.
- [ ] **Step 3k (commit):** `feat(application): per-target set-diff record path + retraction`

## Task 4 — Cleanup: drop the now-dead `last_decision`

After Task 3, `last_decision` (singular) has no prod caller.

**Files**
- Modify `src/mulewatch/adapters/persistence_sqlite/catalog_repository.py` (remove `_SELECT_LAST_DECISION` const lines 61-70 + `last_decision` method lines 196-208).
- Modify `src/mulewatch/ports/catalog_repository.py` (remove `last_decision` line 73 + its docstring sentence).
- Delete `tests/adapters/persistence_sqlite/test_catalog_last_decision.py`.
- Modify `tests/ports/test_catalog_repository.py` (remove stub `last_decision` + its assertion line 85).

**Interfaces:** Removes `CatalogRepository.last_decision`. No behavior change.

- [ ] **Step 4a:** delete the `_SELECT_LAST_DECISION` const + comment and the `last_decision` method from the adapter; delete the `last_decision` Protocol line + its docstring sentence; delete `test_catalog_last_decision.py`; in the ports stub delete the `last_decision` method (lines 31-32) and the `assert repository.last_decision(...) is None` line.
- [ ] **Step 4b:** grep guard `grep -rn "last_decision\b" packages/crawler/src packages/crawler/tests | grep -v last_decisions | grep -v __pycache__` → prints nothing. Then `( cd packages/crawler && uv run pytest )` → green (100% branch), `uv run poe lint-all`.
- [ ] **Step 4c (commit):** `refactor(persistence): drop single last_decision read (superseded by last_decisions)`

## Task 5 — §10 legacy-row tolerance via backfill (guard tests)

§10 mandates NO code change to the backfill trigger. Guard tests proving the Task-3 set-diff tolerates the two legacy artifacts on a real `reevaluate_catalog` pass. Expected GREEN on first run (no red step); a failure signals a real Task-3 defect.

**Files**
- Modify `tests/application/test_reevaluate_catalog.py` (imports + `_HASH_MULTI`/`_MULTI_NAME` + helper + two tests). No `src` change.

- [ ] **Step 5a:** extend imports:

```python
from catalog_matching.engine import DecisionRecord, Explanation, MatchDecision, MatchingEngine
from mulewatch.domain.retraction import RETRACTED_TIER
```

Add constants + helper:

```python
_HASH_MULTI = "dddddddddddddddddddddddddddddddd"
_MULTI_NAME = "Keroro 062 teletoon.avi"  # → 062A + 062B download (post-fan-out)


def _legacy_row(target_id: str, rule_name: str, tier: str) -> MatchDecision:
    return MatchDecision(
        target_id=target_id,
        rule_name=rule_name,
        tier=tier,
        explanation=Explanation(
            target_id=target_id, rules_fired=(), tokens_matched=(), coverage_values=()
        ),
    )
```

Add tests:

```python
@pytest.mark.asyncio
async def test_backfill_retracts_a_legacy_arbitrary_target_row(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # §10: an old "unidentified" row under an arbitrary target_id (001A/catalog) is retracted
    # by the set-diff when the file becomes identified (062A + 062B) on the backfill pass.
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    catalog.record_decision(_HASH_MULTI, _legacy_row("001A", "keroro_large", "catalog"))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=1, written=3)  # 062A + 062B + retract 001A
    assert catalog.last_decisions(_HASH_MULTI) == {
        "062A": DecisionRecord(target_id="062A", rule_name="numero_nu_confirmed", tier="download"),
        "062B": DecisionRecord(target_id="062B", rule_name="numero_nu_confirmed", tier="download"),
        "001A": DecisionRecord(target_id="001A", rule_name="", tier=RETRACTED_TIER),
    }


@pytest.mark.asyncio
async def test_backfill_ignores_the_legacy_empty_sentinel(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # §10: the old whole-file retraction sentinel (target_id="") is invisible to the set-diff.
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    catalog.record_decision(_HASH_MULTI, _legacy_row("", "", RETRACTED_TIER))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=1, written=2)  # only 062A + 062B; "" untouched
    assert set(catalog.last_decisions(_HASH_MULTI)) == {"062A", "062B"}
    assert catalog_connection.execute(
        "SELECT count(*) FROM match_decisions WHERE target_id = ''"
    ).fetchone()[0] == 1
```

- [ ] **Step 5b (run, expect PASS):** `( cd packages/crawler && uv run pytest tests/application/test_reevaluate_catalog.py --no-cov -q )` → passes; then full gate.
- [ ] **Step 5c (commit):** `test(application): legacy-row tolerance on backfill re-eval (spec §10)`

## Task 6 — §8 download dedup: two-segment candidates → one download (guard test)

§8 mandates NO code change to `run_download_cycle.py` — the fixed `download_decisions` (Task 2) yields both `(hash, 062A)` and `(hash, 062B)`, and the existing `is_downloaded(hash)` dedups to one queued download. Guard test only (expected green on first run).

**Files**
- Modify `tests/application/test_run_download_cycle.py` (add one test). No `src` change.

- [ ] **Step 6a:** add (matching the file's existing fakes/helpers — adapt names if the local fixtures differ):

```python
@pytest.mark.asyncio
async def test_two_segment_candidates_same_hash_dedup_to_one_download() -> None:
    # spec §8: a whole-episode file yields BOTH (hash,062A) and (hash,062B) in
    # download_decisions; is_downloaded(hash) dedups them to ONE physical download.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"), _candidate(_A, "062B")),
        observations={_A: ObservedFile(filename="Keroro 062.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert list(downloads.states) == [_A]
    assert downloads.states[_A] is DownloadState.QUEUED
    assert len(client.added_links) == 1 and _A in client.added_links[0]
```

- [ ] **Step 6b (run, expect PASS):** `( cd packages/crawler && uv run pytest tests/application/test_run_download_cycle.py::test_two_segment_candidates_same_hash_dedup_to_one_download --no-cov -q )` → passes; then full package gate.
- [ ] **Step 6c (commit):** `test(application): two-segment candidates dedup to one download (spec §8)`

### Phase 2 exit checklist
- `( cd packages/crawler && uv run pytest )` green (100% branch, mypy strict src+tests).
- `uv run poe lint-all` green (ruff · sqlfluff on the two rewritten SQL consts · templates).
- No DDL migration added; `migrations/catalog/0001_initial.sql` untouched.
- `grep -rn "last_decision\b" packages/crawler | grep -v last_decisions | grep -v __pycache__` → empty.
- Composition wiring (`composition/app.py:578-583`) needs NO change (`ReevalSummary` unchanged).

---

# Phase 3 — Web UI (`packages/webui`): multi-target rendering (spec §9)

**Goal:** the read-only viewer now reads a `match_decisions` table holding multiple current decisions per file (`(hash,072A)` + `(hash,072B)`), per-target `retracted` rows, and a legacy `target_id=''` sentinel. It renders **one row per file with its targets aggregated** (rendering A), keeps counters file-based, and lets a file appear under each of its targets.

**Package facts (verified):**
- SQLite ≥3.53 → `group_concat(x, sep ORDER BY y)` available. The webui embeds SQL in Python string constants; `sql-lint` targets `packages/crawler/src` only, so these strings are NOT sqlfluff-gated (still write clean SQL).
- Templates are logic-free (`_dev/check_templates`): all aggregation is precomputed in Python into `str` fields, so `files.html` needs NO edit and `file_detail.html` already iterates `decisions`.
- Baseline suite green; every commit returns to green + 100% coverage.

**Ordering.** The `FileRow` shape change is an atomic cut across `catalog_read.list_files` (producer) + `app._to_display_rows` (consumer) + their tests → one task (Task 2). Coverage (Task 1) and detail (Task 3) are independent and commit green on their own. Task 4 is additive end-to-end tests.

## Task 1 — Dashboard coverage: latest per `(hash, target_id)` + per-target retraction + legacy sentinel

**Files**
- Modify `src/catalog_webui/adapters/catalog_read.py`: `_SQL_COVERAGE` (37-55) partition by `(ed2k_hash, target_id)` + `AND md.target_id != ''`; module docstring (1-15).
- Modify (Test) `tests/test_webui_catalog_read.py`: rewrite `_seed_retracted` (380-416) to per-target; add `_seed_whole_episode`; add two coverage tests.

**Interfaces**
- Produces: `CatalogReader.target_coverage() -> dict[str, list[tuple[str, str]]]` (signature unchanged; a whole-episode hash appears under two keys).

- [ ] **Step 1a (RED):** add `_seed_whole_episode` near the other seed helpers (after `_seed_with_verdict`, ~line 74) and the two tests:

```python
def _seed_whole_episode(db: Path) -> None:
    """A single whole-episode file (hash a*32) satisfying BOTH segments 072A + 072B (two
    current decisions at tier ``download``) plus one per-file ``clean`` verdict — the core
    multi-target fixture (spec §9). Standalone: never combine with ``_seed`` (same hash)."""
    h = "a" * 32
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)",
            (h, 170_000_000),
        )
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h, "keroro_072.avi", 170_000_000, 7, 3, "[]", "keroro",
                "2026-07-01T10:00:00.000000+00:00", "n1",
            ),
        )
        for tid in ("072A", "072B"):
            conn.execute(
                "INSERT INTO match_decisions"
                " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (h, tid, "numero_nu_confirmed", "download", "2026-07-01T10:00:01.000000+00:00", "n1"),
            )
        conn.execute(
            "INSERT INTO file_verifications"
            " (ed2k_hash, verdict, real_meta, checks, verified_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "clean", None, None, "2026-07-01T12:00:00.000000+00:00", "n1"),
        )
        conn.commit()


def test_target_coverage_whole_episode_contributes_to_both_targets(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    coverage = CatalogReader(open_ro(catalog_db)).target_coverage()
    assert coverage["072A"] == [("a" * 32, "download")]
    assert coverage["072B"] == [("a" * 32, "download")]


def test_target_coverage_ignores_legacy_empty_target_sentinel(catalog_db: Path) -> None:
    h = "e" * 32
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 100))
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "090A", "id_segment_exact", "download", "2026-07-05T10:00:00.000000+00:00", "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "", "", "retracted", "2026-07-05T11:00:00.000000+00:00", "n1"),
        )
        conn.commit()
    coverage = CatalogReader(open_ro(catalog_db)).target_coverage()
    assert coverage["090A"] == [(h, "download")]
    assert "" not in coverage
```

- [ ] **Step 1b (RED run):** `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py::test_target_coverage_whole_episode_contributes_to_both_targets --no-cov -q )` → FAIL (`KeyError: '072A'` — latest-per-hash keeps only the last decision).

- [ ] **Step 1c (GREEN — `_SQL_COVERAGE`, lines 37-55):**

```python
_SQL_COVERAGE = """\
SELECT
    md.ed2k_hash,
    md.target_id,
    md.tier
FROM match_decisions AS md
WHERE (
    SELECT COUNT(*)
    FROM match_decisions AS md2
    WHERE
        md2.ed2k_hash = md.ed2k_hash
        AND md2.target_id = md.target_id
        AND (
            md2.decided_at > md.decided_at
            OR (md2.decided_at = md.decided_at AND md2.id > md.id)
        )
) = 0
AND md.target_id != ''
AND md.tier != 'retracted'
ORDER BY md.target_id, md.ed2k_hash
"""
```

(Only changes vs current: `AND md2.target_id = md.target_id` in the subquery, and `AND md.target_id != ''`.)

- [ ] **Step 1d (migrate `_seed_retracted`, lines 380-416)** to the per-target model — the retraction is a `(hash, 063A, tier="retracted")` marker appended after the real 063A decision (same partition), not the old `('', retracted)` sentinel:

```python
def _seed_retracted(db: Path) -> None:
    """Add a third file (c*32) that WAS matched (063A) then had that target retracted
    per-target: a ``(hash, 063A, tier="retracted")`` marker appended after the real decision
    (the new per-target retraction model, spec §6). Its latest 063A row is a retraction, so
    it must be treated as unmatched everywhere."""
    h = "c" * 32
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (h, 300))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                h, "keroro_063.avi", 300, 2, 1, "[]", "keroro",
                "2026-06-22T09:00:00.000000+00:00", "n1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "063A", "id_segment_exact", "download", "2026-06-22T10:00:00.000000+00:00", "n1"),
        )
        conn.execute(
            "INSERT INTO match_decisions"
            " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, "063A", "", "retracted", "2026-06-22T11:00:00.000000+00:00", "n1"),
        )
        conn.commit()
```

This keeps the three existing `_seed_retracted`-based tests green (their latest 063A row is a retraction → still excluded / no decision) and makes `test_target_coverage_omits_retracted` pass under the new `_SQL_COVERAGE`.

- [ ] **Step 1e (GREEN run):** `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py --no-cov -q )` then `( cd packages/webui && uv run pytest )` → green, 100%. `uv run poe fix` if style nits.
- [ ] **Step 1f (commit):** `fix(webui): dashboard coverage counts each segment target per file`

## Task 2 — Files list + counters: one row per file, targets aggregated (the atomic `FileRow` cut)

Replace the latest-decision-per-hash join with latest-per-`(hash, target_id)` aggregated (via `group_concat`) back to one row per file; reshape `FileRow` to carry a sequence of decisions; aggregate Target/Title/Tier cells (joined with ` · `); keep counters file-based with `COUNT(DISTINCT ed2k_hash)`; make the `target`/`tier` filters match ANY of a file's decisions.

**Files**
- Modify `src/catalog_webui/domain/views.py`: add `FileDecision`; `FileRow` (36-47) `target_id`/`tier` → `decisions: tuple[FileDecision, ...]`; `FileRowDisplay` docstring (156-183).
- Modify `src/catalog_webui/adapters/catalog_read.py`: import `FileDecision`; add `_SQL_LATEST_DEC_AGG`; `_SQL_FILES_SOURCE` (58-96) `dec` join → `dec_agg`; `_SQL_LIST_FILES_BASE` (98-112); `_SQL_COUNT_FILES_BASE` (114-128) `COUNT(DISTINCT …)`; `_filter_clauses` (182-206) `target`/`tier` → `EXISTS(latest_dec …)`; add `_split_concat`; `list_files` (242-292) build `FileRow.decisions`; `count_files` (294-315) body unchanged.
- Modify `src/catalog_webui/composition/app.py`: `_resolve_target_display` (42-59) → `list[tuple[str, str]]`; `_to_display_rows` (62-98) aggregate.
- Modify (Test) `tests/test_webui_app.py`: `app_retracted_decision` fixture (177-241) → per-target; rewrite `_file_row` (1021-1033) + unit tests (1011-1110); add multi-target unit tests.
- Modify (Test) `tests/test_webui_catalog_read.py`: add `list_files`/`count_files` whole-episode + empty-decisions tests.

**Interfaces**
- Produces: `FileDecision(target_id: str, tier: str)` (frozen). `FileRow(ed2k_hash, size_bytes, filename, source_count, last_seen, decisions: tuple[FileDecision, ...], last_verdict: str | None)`.
- `_resolve_target_display(row: FileRow, segment_by_id: Mapping[str, TargetSegment]) -> list[tuple[str, str]]`.
- `_to_display_rows(...)` signature unchanged; `FileRowDisplay` fields stay `str`, holding ` · `-joined aggregates.

- [ ] **Step 2a (RED):** in `test_webui_app.py`, add `FileDecision` to the imports and rewrite the unit-test block (1011-1110):

```python
_SEGMENT_062A = TargetSegment(
    season=2, seasonal_number=11, absolute_number=62, segment="a", title="La Grenouille Cosmique"
)
_SEGMENT_062B = TargetSegment(
    season=2, seasonal_number=11, absolute_number=62, segment="b", title="Duel Contre Giroro"
)
_SEGMENT_BY_ID = {_SEGMENT_062A.target_id: _SEGMENT_062A}
_SEGMENTS_AB = {s.target_id: s for s in (_SEGMENT_062A, _SEGMENT_062B)}


def _file_row(
    *, decisions: tuple[FileDecision, ...], last_verdict: str | None = None
) -> FileRow:
    return FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2024-01-01T00:00:00",
        decisions=decisions,
        last_verdict=last_verdict,
    )


def test_resolve_target_display_empty_decisions_is_empty_list() -> None:
    assert _resolve_target_display(_file_row(decisions=()), _SEGMENT_BY_ID) == []


def test_resolve_target_display_catalog_decision_is_unidentified() -> None:
    row = _file_row(decisions=(FileDecision(target_id="062A", tier="catalog"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [("unidentified", "·")]


def test_resolve_target_display_resolvable_id_joins_seasonal_locator_and_title() -> None:
    row = _file_row(decisions=(FileDecision(target_id="062A", tier="download"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [
        ("062A / S02E11A", "La Grenouille Cosmique")
    ]


def test_resolve_target_display_unknown_id_falls_back_to_raw_id() -> None:
    row = _file_row(decisions=(FileDecision(target_id="999Z", tier="download"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [("999Z", "·")]


def test_resolve_target_display_two_segments_returns_a_pair_each() -> None:
    row = _file_row(
        decisions=(FileDecision("062A", "download"), FileDecision("062B", "download"))
    )
    assert _resolve_target_display(row, _SEGMENTS_AB) == [
        ("062A / S02E11A", "La Grenouille Cosmique"),
        ("062B / S02E11B", "Duel Contre Giroro"),
    ]


def test_to_display_rows_empty_decisions_all_dashes() -> None:
    [display] = _to_display_rows([_file_row(decisions=())], _SEGMENTS_AB)
    assert display.target_display == "·"
    assert display.title_display == "·"
    assert display.tier_display == "·"
    assert display.verdict_display == "·"


def test_to_display_rows_two_segments_aggregate_cells_shared_tier() -> None:
    row = _file_row(
        decisions=(FileDecision("062A", "download"), FileDecision("062B", "download"))
    )
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.target_display == "062A / S02E11A · 062B / S02E11B"
    assert display.title_display == "La Grenouille Cosmique · Duel Contre Giroro"
    assert display.tier_display == "download"


def test_to_display_rows_two_segments_differing_tiers_lists_per_target() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "notify")))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.tier_display == "062A: download · 062B: notify"


def test_to_display_rows_verdict_pending_when_decision_without_verdict() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"),))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.verdict_display == "pending"


def test_to_display_rows_verdict_shows_actual_verdict() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"),), last_verdict="clean")
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.verdict_display == "clean"


def test_to_display_rows_computes_size_and_last_seen_display() -> None:
    row = FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2026-07-03T23:45:24.104990+00:00",
        decisions=(),
        last_verdict=None,
    )
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.size_display == "1 KB"
    assert display.last_seen_display == "2026-07-03 23:45Z"
```

Update the import to `from catalog_webui.domain.views import FileDecision, FileRow`. Delete the removed old tests: `test_resolve_target_display_no_decision_is_all_dashes`, `test_resolve_target_display_retracted_is_all_dashes`, `test_to_display_rows_verdict_dash_when_no_decision`, `test_to_display_rows_retracted_shows_dash_tier_and_verdict` (retracted decisions can no longer reach the Python layer).

Add reader tests to `test_webui_catalog_read.py`:

```python
def test_list_files_whole_episode_is_one_row_with_two_decisions(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    rows = CatalogReader(open_ro(catalog_db)).list_files(
        target=None, tier=None, verdict=None, query=None, page=1
    )
    assert len(rows) == 1
    assert [(d.target_id, d.tier) for d in rows[0].decisions] == [
        ("072A", "download"),
        ("072B", "download"),
    ]
    assert rows[0].last_verdict == "clean"


def test_list_files_filter_by_one_target_returns_whole_episode(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    rows = CatalogReader(open_ro(catalog_db)).list_files(
        target="072B", tier=None, verdict=None, query=None, page=1
    )
    assert len(rows) == 1
    assert [d.target_id for d in rows[0].decisions] == ["072A", "072B"]


def test_list_files_unmatched_file_has_empty_decisions(catalog_db: Path) -> None:
    _seed_unmatched(catalog_db)
    [row] = CatalogReader(open_ro(catalog_db)).list_files(
        target=None, tier=None, verdict=None, query=None, page=1
    )
    assert row.decisions == ()


def test_count_files_whole_episode_counts_as_one_file(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    matched, total = CatalogReader(open_ro(catalog_db)).count_files(
        target=None, tier=None, verdict=None, query=None
    )
    assert (matched, total) == (1, 1)
```

- [ ] **Step 2b (RED run):** `( cd packages/webui && uv run pytest tests/test_webui_app.py::test_resolve_target_display_two_segments_returns_a_pair_each --no-cov -q )` → collection error `ImportError: cannot import name 'FileDecision'`.

- [ ] **Step 2c (GREEN — `views.py`):** add before `FileRow` (~line 35):

```python
@dataclass(frozen=True)
class FileDecision:
    """One current decision on a file: the latest match decision for a given
    ``(ed2k_hash, target_id)``, already filtered to exclude retractions and the legacy
    ``target_id == ""`` sentinel (webui spec §9). A whole-episode file carries two (``072A``
    and ``072B``); an unidentified file carries one (``tier == "catalog"``); a file with no
    current match carries none."""

    target_id: str
    tier: str
```

Replace `FileRow` (36-47):

```python
@dataclass(frozen=True)
class FileRow:
    """Summary view of a file for the explorer (paginated list)."""

    ed2k_hash: str
    size_bytes: int
    filename: str  # latest observed name
    source_count: int  # source count (latest observation)
    last_seen: str  # observed_at of the latest observation (ISO-8601 UTC)
    decisions: tuple[FileDecision, ...]  # current decisions, latest per target, 0..N
    last_verdict: str | None  # latest verification verdict (per file, not per target)
```

Update the `FileRowDisplay` docstring (156-183) to describe per-decision resolution joined with ` · ` (each cell aggregates the file's decisions; the `catalog → "unidentified"` mask applies per decision; verdict is a single per-file value).

- [ ] **Step 2d (GREEN — `catalog_read.py`):** import (19-25) add `FileDecision`. Add `_SQL_LATEST_DEC_AGG` before `_SQL_FILES_SOURCE`:

```python
# Current decisions per file: latest per (ed2k_hash, target_id), excluding the legacy
# ``target_id == ''`` sentinel and any target whose latest row is a ``retracted`` marker.
# ``dec_agg`` folds them to ONE row per hash — target_ids/tiers are ``char(31)``-joined,
# both ordered by target_id so the two lists stay index-aligned (spec §9, rendering A).
_SQL_LATEST_DEC_AGG = """\
WITH latest_dec AS (
    SELECT
        md.ed2k_hash,
        md.target_id,
        md.tier
    FROM match_decisions AS md
    WHERE (
        SELECT COUNT(*)
        FROM match_decisions AS md2
        WHERE
            md2.ed2k_hash = md.ed2k_hash
            AND md2.target_id = md.target_id
            AND (
                md2.decided_at > md.decided_at
                OR (md2.decided_at = md.decided_at AND md2.id > md.id)
            )
    ) = 0
    AND md.target_id != ''
    AND md.tier != 'retracted'
),
dec_agg AS (
    SELECT
        ld.ed2k_hash,
        group_concat(ld.target_id, char(31) ORDER BY ld.target_id) AS target_ids,
        group_concat(ld.tier, char(31) ORDER BY ld.target_id) AS tiers
    FROM latest_dec AS ld
    GROUP BY ld.ed2k_hash
)
"""
```

In `_SQL_FILES_SOURCE`, replace the whole `LEFT JOIN match_decisions AS dec … = 0` block (72-83) with:

```python
LEFT JOIN dec_agg AS dec
    ON dec.ed2k_hash = f.ed2k_hash
```

Replace `_SQL_LIST_FILES_BASE` (98-112):

```python
_SQL_LIST_FILES_BASE = (
    _SQL_LATEST_DEC_AGG
    + """\
SELECT
    f.ed2k_hash,
    f.size_bytes,
    obs.filename,
    obs.source_count,
    obs.observed_at AS last_seen,
    dec.target_ids,
    dec.tiers,
    ver.verdict AS last_verdict
"""
    + _SQL_FILES_SOURCE
)
```

Replace `_SQL_COUNT_FILES_BASE` (114-128):

```python
# Counter for the /files summary: file-based totals over the same source + filters (the
# matched-only clause is deliberately absent). ``matched`` = files with at least one current
# decision (``dec.target_ids`` is non-NULL). COUNT(DISTINCT …) keeps both counts file-based
# and yields 0 (not NULL) on an empty catalogue.
_SQL_COUNT_FILES_BASE = (
    _SQL_LATEST_DEC_AGG
    + """\
SELECT
    COUNT(DISTINCT f.ed2k_hash) AS total,
    COUNT(DISTINCT CASE WHEN dec.target_ids IS NOT NULL THEN f.ed2k_hash END) AS matched
"""
    + _SQL_FILES_SOURCE
)
```

Replace `_filter_clauses` (182-206):

```python
def _filter_clauses(
    target: str | None,
    tier: str | None,
    verdict: str | None,
    query: str | None,
) -> tuple[list[str], list[str]]:
    """Shared WHERE clauses + params for the explorer list and its counter.

    ``target``/``tier`` match a file if ANY of its current decisions matches (EXISTS over the
    ``latest_dec`` CTE), so a whole-episode file appears under each of its targets. The
    matched-only clause and LIMIT/OFFSET are list-specific and are NOT built here.
    """
    clauses: list[str] = []
    params: list[str] = []
    if target is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.target_id = ?)"
        )
        params.append(target)
    if tier is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.tier = ?)"
        )
        params.append(tier)
    if verdict is not None:
        clauses.append("ver.verdict = ?")
        params.append(verdict)
    if query is not None:
        clauses.append("obs.filename LIKE ?")
        params.append(f"%{query}%")
    return clauses, params
```

Add the split helper right after `_filter_clauses`:

```python
def _split_concat(concat: str | None) -> list[str]:
    """Split a ``char(31)``-joined aggregate (``group_concat``) into parts. ``None`` (a file
    with no current decision → the LEFT JOIN yields NULL) → an empty list."""
    return concat.split("\x1f") if concat is not None else []
```

In `list_files`: update the docstring's `target`/`tier`/`matched_only` bullets; replace the matched-only clause (265-268):

```python
        if matched_only:
            # A file is matched iff it has at least one current (non-retracted) decision;
            # ``dec.target_ids`` is NULL for a file with none.
            clauses.append("dec.target_ids IS NOT NULL")
```

Replace the `return [...]` comprehension (280-292) with a decision-building loop:

```python
        rows = self._conn.execute(sql, params).fetchall()
        result: list[FileRow] = []
        for row in rows:
            target_ids = _split_concat(row["target_ids"])
            tiers = _split_concat(row["tiers"])
            decisions = tuple(
                FileDecision(target_id=t, tier=ti)
                for t, ti in zip(target_ids, tiers, strict=True)
            )
            result.append(
                FileRow(
                    ed2k_hash=row["ed2k_hash"],
                    size_bytes=row["size_bytes"],
                    filename=row["filename"] or "",
                    source_count=row["source_count"],
                    last_seen=row["last_seen"] or "",
                    decisions=decisions,
                    last_verdict=row["last_verdict"],
                )
            )
        return result
```

- [ ] **Step 2e (GREEN — `app.py`):** replace `_resolve_target_display` (42-59):

```python
def _resolve_target_display(
    row: FileRow, segment_by_id: Mapping[str, TargetSegment]
) -> list[tuple[str, str]]:
    """Per-decision ``(target_display, title_display)`` pairs for a file row, in the row's
    decision order (by target_id). Empty when the file has no current decision. The
    ``catalog → "unidentified"`` mask is applied per decision (``keroro_large`` is the only
    catalog-tier rule; cf. ``domain.views.FileRowDisplay``)."""
    resolved: list[tuple[str, str]] = []
    for dec in row.decisions:
        if dec.tier == "catalog":
            resolved.append(("unidentified", "·"))
            continue
        seg = segment_by_id.get(dec.target_id)
        if seg is None:
            resolved.append((dec.target_id, "·"))
            continue
        locator = seasonal_id(
            season=seg.season, seasonal_number=seg.seasonal_number, letter=seg.segment
        )
        resolved.append((f"{dec.target_id} / {locator}", seg.title))
    return resolved
```

Replace `_to_display_rows` (62-98):

```python
def _to_display_rows(
    file_rows: Iterable[FileRow], segment_by_id: Mapping[str, TargetSegment]
) -> list[FileRowDisplay]:
    """Convert catalog rows into ``FileRowDisplay`` view-models — one row per file, with the
    file's (usually two) segment decisions aggregated into each cell, joined with ``" · "``.
    Shared by ``handle_files`` and ``handle_target``."""
    rows = []
    for row in file_rows:
        if row.decisions:
            pairs = _resolve_target_display(row, segment_by_id)
            target_display = " · ".join(target for target, _ in pairs)
            title_display = " · ".join(title for _, title in pairs)
            tier_values = {dec.tier for dec in row.decisions}
            if len(tier_values) == 1:
                tier_display = row.decisions[0].tier
            else:
                tier_display = " · ".join(
                    f"{dec.target_id}: {dec.tier}" for dec in row.decisions
                )
            verdict_display = row.last_verdict if row.last_verdict is not None else "pending"
        else:
            target_display = "·"
            title_display = "·"
            tier_display = "·"
            verdict_display = "·"
        rows.append(
            FileRowDisplay(
                ed2k_hash=row.ed2k_hash,
                short_hash=short_hash(row.ed2k_hash),
                filename=row.filename,
                source_count=row.source_count,
                target_display=target_display,
                title_display=title_display,
                size_display=human_size(row.size_bytes),
                last_seen_display=short_timestamp(row.last_seen),
                tier_display=tier_display,
                verdict_display=verdict_display,
                ed2k_link=build_ed2k_link(row.filename, row.size_bytes, row.ed2k_hash),
            )
        )
    return rows
```

- [ ] **Step 2f (update `app_retracted_decision` fixture, 177-241):** replace the two `INSERT INTO match_decisions` statements (207-215) with a real 062A decision followed by a per-target 062A retraction:

```python
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (2, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "", "retracted", "2024-01-02T00:00:00", "node1"),
        )
```

Update the fixture docstring (drop the `target_id=''` sentinel wording). The three tests using it stay green (latest 062A row is a retraction → `latest_dec` yields nothing → empty decisions → hidden by default; all-"·" with `show_unmatched=1`; no decision in detail).

- [ ] **Step 2g (GREEN run):** `( cd packages/webui && uv run pytest tests/test_webui_app.py tests/test_webui_catalog_read.py --no-cov -q )` then `( cd packages/webui && uv run pytest )` → green + 100%. `uv run poe fix` first if ruff/format flags the new SQL wrapping.
- [ ] **Step 2h (commit):** `feat(webui): show one row per file with its targets aggregated`

## Task 3 — File detail: a decision LIST (latest per target)

`FileDetail.decision` (0-or-1) → `FileDetail.decisions` (0..N). The detail template already iterates `{% for d in file.decisions %}`, so no template change. The explanation stays single (from the first decision).

**Files**
- Modify `src/catalog_webui/domain/views.py`: `FileDetail.decision` (100) → `decisions: tuple[DecisionView, ...]`; `FileDetailDisplay.decisions` doc (221).
- Modify `src/catalog_webui/adapters/catalog_read.py`: replace `_SQL_LAST_DECISION` (149-160) with `_SQL_FILE_DECISIONS`; `file_detail` (321-372) build `decisions` tuple; module docstring bullet (10-11).
- Modify `src/catalog_webui/composition/app.py`: `handle_file_detail` (300-333) explanation from `detail.decisions[0]`.
- Modify (Test) `tests/test_webui_catalog_read.py`: update the three `file_detail` decision tests; add a whole-episode detail test.

**Interfaces**
- Produces: `FileDetail(…, decisions: tuple[DecisionView, ...], …)`. `file_detail(ed2k_hash) -> FileDetail | None` unchanged; `.decisions` replaces `.decision`.

- [ ] **Step 3a (RED):** in `test_webui_catalog_read.py` rewrite the three decision-detail tests and add one:

```python
def test_file_detail_carries_observations_and_decisions(catalog_db: Path) -> None:
    _seed(catalog_db)
    detail = CatalogReader(open_ro(catalog_db)).file_detail("a" * 32)
    assert detail is not None
    assert detail.size_bytes == 100
    assert len(detail.decisions) == 1
    assert detail.decisions[0].target_id == "062A"
    assert len(detail.observations) == 1


def test_file_detail_no_decision(catalog_db: Path) -> None:
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", ("b" * 32, 200))
        conn.execute(
            "INSERT INTO file_observations"
            " (ed2k_hash, filename, size_bytes, source_count,"
            " complete_source_count, raw_meta, keyword, observed_at, node_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("b" * 32, "unknown.avi", 200, 1, 0, "[]", "unknown", "2026-06-22T09:00:00.000000+00:00", "n2"),
        )
        conn.commit()
    detail = CatalogReader(open_ro(catalog_db)).file_detail("b" * 32)
    assert detail is not None
    assert detail.decisions == ()
    assert detail.size_bytes == 200


def test_file_detail_retracted_target_is_no_decision(catalog_db: Path) -> None:
    _seed_retracted(catalog_db)
    detail = CatalogReader(open_ro(catalog_db)).file_detail("c" * 32)
    assert detail is not None
    assert detail.decisions == ()


def test_file_detail_whole_episode_lists_both_decisions(catalog_db: Path) -> None:
    _seed_whole_episode(catalog_db)
    detail = CatalogReader(open_ro(catalog_db)).file_detail("a" * 32)
    assert detail is not None
    assert [d.target_id for d in detail.decisions] == ["072A", "072B"]
```

Delete the old `test_file_detail_carries_observations_and_decision` and `test_file_detail_retracted_latest_decision_is_no_decision`.

- [ ] **Step 3b (RED run):** `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py::test_file_detail_whole_episode_lists_both_decisions --no-cov -q )` → FAIL `AttributeError: 'FileDetail' object has no attribute 'decisions'`.

- [ ] **Step 3c (GREEN — `views.py`):** `FileDetail` line 100 → `decisions: tuple[DecisionView, ...]`; update `FileDetailDisplay.decisions` doc (~221) to "0..N elements".

- [ ] **Step 3d (GREEN — `catalog_read.py`):** replace `_SQL_LAST_DECISION` (149-160) with:

```python
# All current decisions of a file: latest per (ed2k_hash, target_id), excluding the legacy
# ``target_id == ''`` sentinel and any target whose latest row is a ``retracted`` marker.
_SQL_FILE_DECISIONS = """\
SELECT
    md.target_id,
    md.rule_name,
    md.tier,
    md.decided_at,
    md.node_id
FROM match_decisions AS md
WHERE md.ed2k_hash = ?
AND md.target_id != ''
AND md.tier != 'retracted'
AND (
    SELECT COUNT(*)
    FROM match_decisions AS md2
    WHERE
        md2.ed2k_hash = md.ed2k_hash
        AND md2.target_id = md.target_id
        AND (
            md2.decided_at > md.decided_at
            OR (md2.decided_at = md.decided_at AND md2.id > md.id)
        )
) = 0
ORDER BY md.target_id
"""
```

In `file_detail` (321-372), replace the single-decision block and the `FileDetail(... decision=decision ...)` field with (from line 327 onward):

```python
        obs_rows = self._conn.execute(_SQL_OBSERVATIONS, (ed2k_hash,)).fetchall()
        dec_rows = self._conn.execute(_SQL_FILE_DECISIONS, (ed2k_hash,)).fetchall()
        ver_rows = self._conn.execute(_SQL_VERIFICATIONS, (ed2k_hash,)).fetchall()

        decisions = tuple(
            DecisionView(
                target_id=row["target_id"],
                rule_name=row["rule_name"],
                tier=row["tier"],
                decided_at=row["decided_at"],
                node_id=row["node_id"],
            )
            for row in dec_rows
        )

        return FileDetail(
            ed2k_hash=file_row["ed2k_hash"],
            size_bytes=file_row["size_bytes"],
            aich_hash=file_row["aich_hash"],
            observations=tuple(
                ObservationRow(
                    id=row["id"],
                    filename=row["filename"],
                    size_bytes=row["size_bytes"],
                    source_count=row["source_count"],
                    complete_source_count=row["complete_source_count"],
                    media_length_sec=row["media_length_sec"],
                    bitrate_kbps=row["bitrate_kbps"],
                    keyword=row["keyword"],
                    observed_at=row["observed_at"],
                    node_id=row["node_id"],
                )
                for row in obs_rows
            ),
            decisions=decisions,
            verifications=tuple(
                VerificationRow(
                    id=row["id"],
                    verdict=row["verdict"],
                    verified_at=row["verified_at"],
                    node_id=row["node_id"],
                )
                for row in ver_rows
            ),
        )
```

(Removes the old `if dec_row is not None and dec_row["tier"] != "retracted":` branch — the SQL now excludes retracted/sentinel rows, removing a coverage branch.)

- [ ] **Step 3e (GREEN — `app.py` `handle_file_detail`, 305-319):**

```python
        first_decision = detail.decisions[0] if detail.decisions else None
        if first_decision is not None and last_obs is not None:
            explanation = explainer.explain(
                filename=last_obs.filename,
                size_bytes=last_obs.size_bytes,
                media_length_sec=last_obs.media_length_sec,
                bitrate_kbps=last_obs.bitrate_kbps,
                target_id=first_decision.target_id,
            )
            if explanation is not None:
                explanation_target_id = explanation.target_id
                explanation_rules_fired = explanation.rules_fired
                explanation_tokens_matched = explanation.tokens_matched
                explanation_notes = ("Evaluated against the current configuration",)

        display = FileDetailDisplay(
            ed2k_hash=detail.ed2k_hash,
            size_bytes=detail.size_bytes,
            aich_hash_display=detail.aich_hash if detail.aich_hash is not None else "·",
            observations=detail.observations,
            decisions=detail.decisions,
            verifications=detail.verifications,
            ed2k_link=link,
            explanation_target_id=explanation_target_id,
            explanation_rules_fired=explanation_rules_fired,
            explanation_tokens_matched=explanation_tokens_matched,
            explanation_notes=explanation_notes,
        )
```

(Removes the old `decisions = (detail.decision,) if detail.decision is not None else ()`. The explanation is computed for the first, lowest-target_id decision — acceptable for v1 per §9.)

- [ ] **Step 3f (GREEN run):** `( cd packages/webui && uv run pytest tests/test_webui_catalog_read.py tests/test_webui_app.py --no-cov -q )` then `( cd packages/webui && uv run pytest )`. Existing detail HTTP tests stay green.
- [ ] **Step 3g (commit):** `feat(webui): file detail lists all current decisions`

## Task 4 — Whole-episode end-to-end rendering (HTTP)

**Files**
- Modify (Test) `tests/test_webui_app.py`: add `_write_targets_yaml_ab`, `app_whole_episode` fixture, three HTTP tests.

- [ ] **Step 4a (add tests):**

```python
def _write_targets_yaml_ab(path: Path) -> Path:
    (path / "targets_ab.yaml").write_text(
        """\
episodes:
  - season: 3
    seasonal_number: 6
    absolute_number: 72
    segments:
      - letter: a
        title: "Le Defi"
      - letter: b
        title: "Duel Contre Giroro"
""",
        encoding="utf-8",
    )
    return path / "targets_ab.yaml"


@pytest.fixture
def app_whole_episode(catalog_db: Path, local_db: Path, tmp_path: Path) -> tuple[Starlette, str]:
    """One file matched to BOTH 072A and 072B (two current decisions, tier download) against a
    two-segment targets.yaml — the core multi-target end-to-end fixture (spec §9)."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files VALUES (?, ?, ?)", (TEST_HASH, 170_000_000, None))
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH, "keroro_072_vf.avi", 170_000_000, 7, 3,
                None, None, None, None, "{}", "keroro", "2024-01-01T00:00:00", "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "072A", "numero_nu_confirmed", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (2, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "072B", "numero_nu_confirmed", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-whole"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    targets_path = _write_targets_yaml_ab(tmp_path)
    matcher_path = _write_matcher_yaml(tmp_path)

    import catalog_webui

    templates_dir = Path(catalog_webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(catalog_webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        targets=targets_path,
        matcher=matcher_path,
        templates_dir=templates_dir,
        static_dir=static_dir,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_files_whole_episode_renders_one_row_with_aggregated_targets(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "<td>072A / S03E06A · 072B / S03E06B</td>" in resp.text
    assert "<td>Le Defi · Duel Contre Giroro</td>" in resp.text
    assert "<td>download</td>" in resp.text


@pytest.mark.asyncio
async def test_whole_episode_appears_under_each_target(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get("/targets/072A")
        resp_b = await client.get("/targets/072B")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert hash_[:8] in resp_a.text
    assert hash_[:8] in resp_b.text


@pytest.mark.asyncio
async def test_file_detail_whole_episode_shows_both_targets(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "072A" in resp.text
    assert "072B" in resp.text
```

- [ ] **Step 4b (run, expect PASS):** `( cd packages/webui && uv run pytest tests/test_webui_app.py -k whole_episode --no-cov -q )` then `( cd packages/webui && uv run pytest )`. If an assertion is off by whitespace, inspect the rendered cell and confirm the exact ` · ` join — no source change should be needed (a needed one is a Task-2 display bug).
- [ ] **Step 4c (commit):** `test(webui): end-to-end whole-episode multi-target rendering`

### Phase 3 exit checklist
- `files.html` / `file_detail.html` unchanged; `uv run poe template-check` green.
- `( cd packages/webui && uv run pytest )` green + 100% branch (`catalog_read.py`, `app.py`, `views.py` all 100%). Both sides covered: `list_files` split (present via `_seed_whole_episode` / absent via `_seed_unmatched`); `_to_display_rows` `if row.decisions`, `if len(tier_values) == 1`, verdict ternary; `handle_file_detail` `first_decision`/`explanation`.
- No em-dash/en-dash in any cell (` · ` / `:` / `/` only); `catalog → "unidentified"` mask per decision; counters `COUNT(DISTINCT ed2k_hash)`; the legacy `target_id=''` sentinel ignored everywhere via `AND … target_id != ''`; per-target `retracted` excluded via `AND … tier != 'retracted'` on the latest row.
- **Delimiter safety:** `char(31)` (US, `\x1f`) can never appear in a `target_id` (`\d{3}[A-Z]`) or a tier enum, so the `group_concat`/`_split_concat` round-trip is unambiguous; `zip(strict=True)` guards any length skew (structurally impossible — same aggregate cardinality + `ORDER BY`).

---

## Final verification (whole feature)

1. `uv run poe check` (lint-all + type-check + all four package suites) green.
2. Holistic cross-cutting review (Verify phase) over the full diff — the review regularly catches integration bugs the per-package gates miss.
3. Confirm the design invariants hold: crawler PROD never reads downloaded bytes; the package boundary (crawler ↔ verifier) is untouched; `keroro_large` stays the only `catalog` rule; no DDL migration was added.

