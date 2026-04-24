# Stage 2 Efficiency — Sprint 1: Foundations

**Spec:** `docs/superpowers/specs/2026-04-23-stage2-simulator-efficiency-design.md`
**Status:** Task 1 applied (uncommitted); Tasks 2–4 pending
**Estimate:** 3–5 days
**Goal:** lay the foundation for the rewrite without touching the active simulator. At sprint end we have a package skeleton, a tested rules library, and a parity harness — but the legacy simulator is still doing all the work.

**File budget:** ≤5 Python scripts added across the sprint.

---

## Rationale for keeping Sprint 1 small

Everything in this sprint is **strictly additive**. No production code path changes. If the sprint is abandoned mid-way, the pipeline is unaffected. This gives us a measuring stick (the parity harness) and a clean substrate (rules + context) before we touch the hot loop.

---

## File budget (5 scripts, across all tasks)

| # | File | Task | Role |
|---|---|---|---|
| 1 | `src/pipeline/sweep_simulator/__init__.py` | 1 | Re-exports from `_legacy` (now) and `_rewrite` (as sections land) |
| 2 | `src/pipeline/sweep_simulator/_legacy.py` | 1 | The current monolithic simulator, renamed via `git mv` (0-byte move) |
| 3 | `src/pipeline/sweep_simulator/_rewrite.py` | 2, 3 | All new code (SimContext, Decision enum, rule helpers) in one file with section headers |
| 4 | `tests/test_sweep_rewrite.py` | 2, 3 | Unit tests for `SimContext`, `SimFlags`, `Decision`, every rule helper |
| 5 | `tests/test_sweep_parity.py` | 4 | Parity harness: `diff_simulations`, baseline capture helpers, legacy-vs-legacy smoke pass |

Baseline fixtures under `tests/regression/baselines/` are data, not scripts — they don't count against the budget.

---

## Tasks

### Task 1 — Package skeleton (0.5 day)

**Change.**
Create `src/pipeline/sweep_simulator/` as a package:

```
src/pipeline/sweep_simulator/
    __init__.py       # re-exports from _legacy
    _legacy.py        # renamed existing monolithic simulator (git mv)
    _rewrite.py       # empty (docstring only) — populated in Tasks 2–3
```

`__init__.py` re-exports the current public surface (`simulate_partition`, `simulate_sweep_matching`, `simulate_resting_phase`, `load_and_prepare_orders`, `build_remainder_df`) plus constants (`SWEEP_ORDER_TYPE`, `ELIGIBLE_MATCHING_ORDER_TYPES`, `INT64_SENTINEL`) and the private helpers used by `tests/test_resting_phase.py` (`_calc_resting_price`, `_calc_lit_resting_price`, `_get_session_end_time`, `_build_lit_order_book`) so every existing caller keeps working.

**Test.** Run existing pipeline end-to-end on DRR/20240905 via Stage 1–2 and confirm output is byte-identical to pre-sprint output.

**Verification command.**
```bash
bash scripts/verify_sprint1_task1.sh
```

The script stashes the refactor, runs the pre-sprint pipeline as baseline, pops the stash, re-runs, and `diff -r`s both `data/outputs/` and `data/processed/`.

**Acceptance.** Script exits 0. Zero byte differences.

---

### Task 2 — `SimContext`, `SimFlags`, `Decision` enum (0.5 day)

**Change.**
Add to `_rewrite.py`, under a `# ── Sprint 1 — Foundations ──` section header:

- `SimFlags` dataclass — one field per config flag the simulator reads today (`nbbo_source`, `simulate_resting_phase`, `simulate_lit_resting`, `resting_lit_book_mode`, `resting_use_midtick`, `resting_lit_use_limit`, `resting_model_cancellation`, `resting_apply_crossing_keys`, `resting_apply_session_filter`, `resting_apply_maq`, `resting_apply_preferencing`, `resting_apply_iceberg`, `use_polars_transforms`, `use_duckdb_io`, `min_block_size`).
- `SimContext` dataclass per spec Section 2 — numpy-array fields for sweeps and contras, session arrays, optional NBBO arrays, reference scalars, `cfg_flags: SimFlags`.
- `Decision(IntEnum)` with `OK`, `SKIP`, `BREAK`.

Both dataclasses are `frozen=True, slots=True`.

**Test.** Add to `tests/test_sweep_rewrite.py`:
1. Construct a `SimContext` from synthetic numpy arrays.
2. Mutation raises (frozen).
3. No `__dict__` (slots).
4. `dataclasses.replace` round-trip preserves all fields.
5. `Decision.OK < Decision.SKIP < Decision.BREAK` ordering.

**Verification command.**
```bash
PYTHONPATH=src python -m pytest tests/test_sweep_rewrite.py -v
```

**Acceptance.** All new tests pass.

---

### Task 3 — Rule helpers (1.5 days)

**Change.**
Add to `_rewrite.py`, appended after the Sprint 1 section — pure functions of primitives, each returning a `Decision` or a primitive:

- `check_maq(sweep_remaining, sweep_matched, sweep_maq, sweep_sfmq, contra_maq, contra_sfmq, potential_match_qty) -> Decision`
- `check_crossing(sweep_participant, contra_participant, sweep_crossingkey, contra_crossingkey) -> Decision`
- `validate_price_limit(order_type, order_price, execution_price, sweep_side, matched_qty, first_fill_price) -> bool`
- `is_valid_session(state_enum: int) -> bool`
- `iceberg_available(display_qty, slice_consumed, remaining_qty) -> int`
- `apply_midtick(midpoint, nbbo_bid, nbbo_offer, side, midtick_flag, tick_size) -> int`
- `is_apb(contra_ordertype, contra_midtick) -> bool`

Legacy simulator is untouched — `_rewrite.py`'s rules are unused in production this sprint.

**Test.** Table-driven tests in `tests/test_sweep_rewrite.py`, one class per function. Must include:
- MAQ: zero passes all; SFMQ + potential<MAQ → SKIP; multi-fill + remaining<MAQ + matched>0 → **BREAK**; multi-fill + potential<MAQ + matched==0 → SKIP.
- Crossing: same participant + no keys → SKIP; same participant + matching keys → OK; mismatched keys → SKIP; different participants → OK.
- Price limit: LIMIT crossed → OK; LIMIT uncrossed → False; MARKET → True; MTL first fill → OK; MTL later fill with bad price → False.
- Session: only OPEN/CONTINUOUS → True.
- Iceberg: non-iceberg → remaining qty; iceberg consumed<display → `display-consumed`; consumed≥display → 0.
- Midtick: `MIDTICK_NO` → midpoint; `MIDTICK_YES` + buy → midpoint + half-tick toward contra.
- APB: (4096, 5) → True; (4096, 6) → True; (64, 5) → False.

**Verification command.**
```bash
PYTHONPATH=src python -m pytest tests/test_sweep_rewrite.py \
  --cov=src/pipeline/sweep_simulator/_rewrite --cov-report=term-missing
```

**Acceptance.** Every test passes; `_rewrite.py` branch coverage 100% for the Sprint 1 section (rules are the only live code).

---

### Task 4 — Parity-harness scaffolding (1.5 days)

**Change.**
Single file: `tests/test_sweep_parity.py`. Contains:

- `diff_simulations(old_result, new_result) -> DiffReport` per spec §7.2 diff rules.
- `capture_baseline(partition_key, processed_dir)` helper that runs the current simulator and writes `simulated_trades.parquet`, `order_summary.parquet`, `sweep_utilization.parquet` to `tests/regression/baselines/{partition_key}/`.
- `test_parity_legacy_vs_legacy(partition_key)` — runs the legacy simulator twice on the same partition (no refactor), diffs; must be zero-diff. Proves the harness + diff rules are sane.
- A module-level `BASELINE_PARTITIONS` list (`20240905/drr` + 3 auto-discovered: one tiny, one medium, one heavy).

New simulator parity tests come in a later sprint — this task just stands up the harness and proves it self-consistent.

**Verification command.**
```bash
PYTHONPATH=src python tests/test_sweep_parity.py --capture-baselines
PYTHONPATH=src python -m pytest tests/test_sweep_parity.py -v
```

**Acceptance.**
1. Baselines captured for all 4 partitions (3 Parquet files × 4 dirs).
2. Parity test runs legacy-vs-legacy and passes with zero diffs on all 4 baselines.

---

## Out of scope for Sprint 1

- Any change to the legacy simulator's behaviour.
- Stage 1 Parquet emission (Sprint 2).
- Kernel / prep / emit implementation (Sprints 2–5).
- Benchmark runs (Sprint 5 rollout).

## Entry criteria

- Spec `2026-04-23-stage2-simulator-efficiency-design.md` is approved.
- `pytest` runs in the current env (`source activate.sh` works).

## Exit criteria

- All 4 tasks' acceptance boxes ticked.
- `git diff main...` shows ≤5 new Python files under the sprint's budget.
- Pipeline end-to-end smoke test on DRR/20240905 still passes byte-identical.
