# Survivor-Filtered Real Metrics + Broader Dark-at-Submission Deduction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two correctness gaps in the `_parq` Centre Point sweep-counterfactual pipeline: (1) make real-vs-sim metric comparisons symmetric by computing real metrics on the same survivor population the simulator runs on; (2) broaden the dark-at-submission deduction beyond `dealsource == 47` so that APB and Preference fills at arrival are also netted out of `orders_after.leavesquantity`.

**Architecture:** Two localised changes plus shared test fixtures.

- **Fix #1** edits `aggregate.py:_process_partition_calculate_metrics` to load `last_execution_time.parquet` (the survivor list emitted by `process.py`'s three-level filter) and intersect it with `sweep_orderids` before passing them to `calculate_trade_metrics(..., is_simulated=False)`. Simulated metrics already key off survivors via `simulation_order_summary.parquet`, so only the real path needs the filter.
- **Fix #2** replaces the hard-coded `dealsource == 47` filter inside `process.py`'s inline `orders_after` synthesis (lines 1111–1133) with a centralised set `DARK_AT_SUBMISSION_DEALSOURCES` defined in `config.py`. The dead-but-similar `_compute_dark_at_submission_qty` helper at `process.py:533` is deleted (never called; replacement lives inline).

**Tech Stack:** Python, pandas, pytest, DuckDB-backed Parquet I/O.

---

## Decisions to confirm before coding

These are the only judgment calls in the plan. Confirm or override before Task 1 begins.

1. **Dealsource set for Fix #2.** The handoff names `{46, 47, 50, 51}`. Per `docs/dd.txt:1166–1174` the full Centre-Point-mediated set is:
   - 46 Preference Matched
   - 47 CentrePoint
   - 49 Preference Only Matched
   - 50 Any Price Block (APB)
   - 51 Preference APB
   - 52 Preference Only APB
   - (48 BookTradeCentrePoint — book-trade, separate mechanism)

   **Recommendation:** include all six `{46, 47, 49, 50, 51, 52}` — all six describe a fill that already happened inside the dark mechanism at arrival, which is exactly what we are deducting. Excluding 49/52 would create another partial-coverage gap symmetric to the current one. Excluding 48 because it is a book-trade rather than a continuous-match.

2. **Dead helper `_compute_dark_at_submission_qty` at `process.py:533`.** Confirmed dead (zero callers — see grep in plan prep). Delete as part of Task 5 to avoid two-source drift.

3. **Tests.** No prior unit tests exist on these functions. Plan adds:
   - 2 new pytest unit tests using crafted `DataFrame` fixtures (one per fix) — fast, no parquet I/O.
   - 1 integration smoke check on the existing CBA/2024-09-05 outputs — confirms survivor count drops to ~17k and dark-at-submission deduction grows.
   - Parity baseline regen is **out of scope** (blocked separately on filename-vs-trade-date mismatch — see handoff item 3).

---

## File Structure

| File | Change |
|------|--------|
| `config.py` | **Modify**: add `DARK_AT_SUBMISSION_DEALSOURCES = {46, 47, 49, 50, 51, 52}` constant near other dealsource constants (around line 43, the "Order type / dealsource / NBBO constants" section). |
| `process.py` | **Modify**: lines 1111–1133 — replace `trades_df[ds_col] == 47` with `.isin(DARK_AT_SUBMISSION_DEALSOURCES)`. **Delete**: lines 533–547 (`_compute_dark_at_submission_qty`, dead helper). |
| `aggregate.py` | **Modify**: `_process_partition_calculate_metrics` (lines 1900–1979) — load `last_execution_time.parquet`, intersect orderids before passing to `calculate_trade_metrics`. |
| `tests/test_dark_deduction.py` | **Create**: pytest module with two unit tests + one integration smoke check. |

---

## Task 1: Add `DARK_AT_SUBMISSION_DEALSOURCES` constant to `config.py`

**Files:**
- Modify: `config.py:43-50` (append to "Order type / dealsource / NBBO constants" block)

- [ ] **Step 1: Add the constant**

Open `config.py` and locate the comment header at line 43:

```python
# ── Order type / dealsource / NBBO constants ───────────────────────────────────
```

Within that block, add:

```python
# Dealsources that represent a Centre-Point-mediated fill at order arrival.
# Used to compute `dark_at_submission` quantity that must NOT be passed to the
# counterfactual simulator (the sim only asks about the lit-bound remainder).
# Per dd.txt §3.3.x: 46=Preference, 47=CentrePoint, 49=Preference Only,
# 50=APB, 51=Preference APB, 52=Preference Only APB. 48 (BookTradeCentrePoint)
# is intentionally excluded — it is a book-trade mechanism, not a continuous
# dark match.
DARK_AT_SUBMISSION_DEALSOURCES = frozenset({46, 47, 49, 50, 51, 52})
```

- [ ] **Step 2: Verify the constant imports cleanly**

Run: `python -c "import config; print(sorted(config.DARK_AT_SUBMISSION_DEALSOURCES))"`
Expected: `[46, 47, 49, 50, 51, 52]`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "config: add DARK_AT_SUBMISSION_DEALSOURCES set"
```

---

## Task 2: Write failing unit test for broader dark deduction

**Files:**
- Create: `tests/test_dark_deduction.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dark_deduction.py`:

```python
"""Tests for the orders_after dark-at-submission deduction logic.

The `orders_after` frame is synthesized post-dark/pre-lit: it carries the
same orders_before rows but with `leavesquantity` overridden to
(quantity − dark_at_submission). The simulator then uses
`orders_after.leavesquantity` as `sweep_qty`.

Pre-fix: only dealsource==47 (CentrePoint) trades at the order's NEW_ORDER
timestamp were deducted.
Post-fix: dealsources {46, 47, 49, 50, 51, 52} are all deducted.
"""
import pandas as pd
import pytest

import process
import config


def _make_orders_before(orderid: int, qty: int, ts: int):
    """Single sweep, single NEW_ORDER row at timestamp `ts`."""
    return pd.DataFrame([{
        'orderid': orderid,
        'quantity': qty,
        'leavesquantity': qty,
        'timestamp': ts,
        'sequence': 1,
        'changereason': 6,         # NEW_ORDER
        'exchangeordertype': 2048,
    }])


def _make_trade(orderid: int, qty: int, tradetime: int, dealsource: int):
    return {
        'orderid': orderid,
        'quantity': qty,
        'tradetime': tradetime,
        'dealsource': dealsource,
    }


def test_dark_at_submission_deducts_apb_and_preference():
    """Sweep with mixed-dealsource fills at submission → leavesquantity
    should reflect ALL CP-mediated dealsources, not just 47."""
    orderid, qty, ts = 1001, 1000, 100
    orders_before = _make_orders_before(orderid, qty, ts)
    trades = pd.DataFrame([
        _make_trade(orderid, 100, ts, 47),   # 100 CP at submission
        _make_trade(orderid, 200, ts, 50),   # 200 APB at submission
        _make_trade(orderid, 150, ts, 46),   # 150 Preference at submission
        _make_trade(orderid, 50,  ts + 1, 1),  # lit later — not at submission, not CP
    ])

    # Build orders_by_partition / trades_by_partition shape that
    # `synthesize_orders_after_state` (or whichever function wraps the inline
    # block at process.py:1111-1133) expects.
    pkey = '2024-09-05/85603'
    orders_by_partition = {pkey: orders_before}
    trades_by_partition = {pkey: trades}

    # Call the function under test. NOTE: if the inline block has not yet
    # been extracted into a named function, Task 4 extracts it as
    # `_synthesize_orders_after`. This test will fail to import until then.
    result = process._synthesize_orders_after(orders_before, trades)

    leaves = int(result.loc[result['orderid'] == orderid, 'leavesquantity'].iloc[0])
    # 1000 - (100 + 200 + 150) = 550
    assert leaves == 550, (
        f"Expected leavesquantity=550 (1000 - 450 dark at submission); "
        f"got {leaves}. Likely only DS=47 was deducted."
    )


def test_dark_at_submission_ignores_post_arrival_dark_fills():
    """A CP fill at tradetime > NEW_ORDER ts is post-arrival (resting), NOT
    at-submission. It must NOT be deducted from orders_after.leavesquantity
    (the simulator's window logic handles that fill separately)."""
    orderid, qty, ts = 1002, 1000, 200
    orders_before = _make_orders_before(orderid, qty, ts)
    trades = pd.DataFrame([
        _make_trade(orderid, 100, ts, 47),       # 100 at submission
        _make_trade(orderid, 300, ts + 50, 47),  # 300 later — DS=47 but not at submit
    ])

    result = process._synthesize_orders_after(orders_before, trades)

    leaves = int(result.loc[result['orderid'] == orderid, 'leavesquantity'].iloc[0])
    assert leaves == 900, (
        f"Expected leavesquantity=900 (1000 - 100 at submission only); "
        f"got {leaves}."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dark_deduction.py::test_dark_at_submission_deducts_apb_and_preference -v`
Expected: **FAIL** with `AttributeError: module 'process' has no attribute '_synthesize_orders_after'` (the function does not exist yet — it lives inline at `process.py:1111-1133`).

---

## Task 3: Extract the inline `orders_after` synthesis into a named function

This is a refactor only — no behaviour change. Pulling it out gives Task 4 a function to fix and the unit tests a target to call.

**Files:**
- Modify: `process.py:1111-1133` (the inline block)
- Modify: `process.py` — add new function `_synthesize_orders_after(orders_before, trades_df)` near the other helpers around line 600.

- [ ] **Step 1: Add the new function**

Insert after `_build_contra_rest_on_lit` (around line 605):

```python
def _synthesize_orders_after(orders_before, trades_df):
    """Synthesize `orders_after`: same rows as `orders_before` but with
    `leavesquantity` overridden to (quantity − dark_at_submission_qty).

    `dark_at_submission_qty` for an order is the sum of trade quantities
    whose dealsource is in DARK_AT_SUBMISSION_DEALSOURCES AND whose
    tradetime equals the order's NEW_ORDER timestamp. These represent the
    portion that already filled in dark on arrival — the simulator's
    counterfactual question is about the LIT-bound remainder only.

    If `trades_df` is empty/None, returns a copy of `orders_before` with
    leavesquantity == quantity (no dark fills to deduct).
    """
    orders_after = orders_before.copy()
    if trades_df is None or len(trades_df) == 0:
        return orders_after

    ds_col  = col.trades.dealsource
    tt_col  = col.common.tradetime
    qty_col = col.common.quantity
    ts_col  = col.common.timestamp
    oid_col = col.common.orderid

    new_ts_per_oid = orders_before[[oid_col, ts_col]].rename(columns={ts_col: '_new_ts'})
    dark = trades_df[
        trades_df[ds_col].isin(DARK_AT_SUBMISSION_DEALSOURCES)
    ][[oid_col, tt_col, qty_col]]
    dark = dark.merge(new_ts_per_oid, on=oid_col, how='inner')
    dark_at_new = dark[dark[tt_col] == dark['_new_ts']]
    dark_per_oid = (
        dark_at_new.groupby(oid_col)[qty_col]
        .sum()
        .rename('_dark_qty')
        .reset_index()
    )
    orders_after = orders_after.merge(dark_per_oid, on=oid_col, how='left')
    orders_after['_dark_qty'] = orders_after['_dark_qty'].fillna(0).astype('int64')
    orders_after[col.common.leavesquantity] = (
        (orders_after[qty_col] - orders_after['_dark_qty']).clip(lower=0).astype('int64')
    )
    orders_after = orders_after.drop(columns=['_dark_qty'])
    return orders_after
```

Note the import: `DARK_AT_SUBMISSION_DEALSOURCES` is in `config.py`. `process.py` already imports from `config` — confirm by grepping: `grep -n "from config\|import config\|DARK_AT_SUBMISSION" process.py`.

If imports use `from config import …`, append `DARK_AT_SUBMISSION_DEALSOURCES` to that line. Otherwise reference as `config.DARK_AT_SUBMISSION_DEALSOURCES`.

- [ ] **Step 2: Replace the inline block with a call**

Replace lines 1111–1133 (the block beginning `# AFTER state: synthesize "post-dark, pre-lit"…`) with:

```python
        # AFTER state: synthesize "post-dark, pre-lit" — see
        # `_synthesize_orders_after` for semantics.
        trades_df = (trades_by_partition or {}).get(partition_key)
        orders_after = _synthesize_orders_after(orders_before, trades_df)
```

- [ ] **Step 3: Re-run the failing test to confirm it now reaches the new function**

Run: `pytest tests/test_dark_deduction.py::test_dark_at_submission_deducts_apb_and_preference -v`
Expected: **FAIL** with `AssertionError: Expected leavesquantity=550 ... got 900` (or similar). The function exists but still uses `== 47`, so APB/Preference are not deducted yet. Wait — Task 3 already wrote `.isin(DARK_AT_SUBMISSION_DEALSOURCES)` in the function body. So the test should **PASS** after Task 3.

Re-stated: Task 3 *is* the fix (because the extracted function uses the constant from Task 1). The expected outcome is therefore **PASS** for both unit tests after Task 3. If they still fail, debug before Task 4.

- [ ] **Step 4: Verify nothing else broke**

Run: `pytest tests/ -v`
Expected: existing 5 tests still pass + 2 new tests pass = 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add process.py tests/test_dark_deduction.py
git commit -m "fix: dark_at_submission deducts {46,47,49,50,51,52}, not just 47"
```

---

## Task 4: Delete the dead `_compute_dark_at_submission_qty` helper

**Files:**
- Modify: `process.py:533-547` (delete)

- [ ] **Step 1: Confirm zero callers**

Run: `grep -rn "_compute_dark_at_submission_qty" /Users/agautam/workspace/python/sweeporders/ --include="*.py"`
Expected: only the definition line at `process.py:533`. (If any caller appears, STOP and re-plan.)

- [ ] **Step 2: Delete lines 533–547**

The block to delete starts with:

```python
def _compute_dark_at_submission_qty(order_df, trades_df):
    """Quantity of a sweep that real-life filled in dark at submission.
    ...
    """
    new_order_events = order_df[order_df[col.common.changereason] == 6]
    if len(new_order_events) == 0:
        return 0
    new_ts = int(new_order_events[col.common.timestamp].iloc[0])
    mask = (trades_df[col.trades.dealsource] == 47) & \
           (trades_df[col.common.tradetime] == new_ts)
    return int(trades_df.loc[mask, col.common.quantity].sum())
```

Remove the entire function. Leave one blank line between the surrounding helpers.

- [ ] **Step 3: Re-run all tests**

Run: `pytest tests/ -v`
Expected: 7 PASS (unchanged).

- [ ] **Step 4: Commit**

```bash
git add process.py
git commit -m "chore: drop dead _compute_dark_at_submission_qty helper"
```

---

## Task 5: Write failing unit test for survivor-filtered real metrics

**Files:**
- Modify: `tests/test_dark_deduction.py` — append a new test function.

This test asserts that `_process_partition_calculate_metrics` filters its real-metrics input to orderids that appear in `last_execution_time.parquet`, not all sweeps in `orders_before`.

Because `_process_partition_calculate_metrics` does I/O against partition directories, the cheapest test is a **focused unit** of the new helper extracted in Task 6 (`_filter_to_survivors`). We test the helper in isolation here; integration smoke (Task 8) confirms wiring.

- [ ] **Step 1: Append the failing test**

Append to `tests/test_dark_deduction.py`:

```python
import numpy as np
import aggregate


def test_filter_to_survivors_intersects_orderids():
    """Given a list of all sweep orderids and a survivors DataFrame from
    last_execution_time.parquet, the helper returns only the intersection."""
    all_sweeps = np.array([10, 20, 30, 40, 50], dtype='int64')
    survivors_df = pd.DataFrame({
        'orderid': [10, 30, 50, 99],   # 99 is junk; should be ignored
        'first_execution_time': [1, 1, 1, 1],
        'last_execution_time': [2, 2, 2, 2],
    })

    result = aggregate._filter_to_survivors(all_sweeps, survivors_df)

    assert sorted(result) == [10, 30, 50], (
        f"Expected intersection [10, 30, 50]; got {sorted(result)}."
    )


def test_filter_to_survivors_returns_empty_when_no_overlap():
    all_sweeps = np.array([1, 2, 3], dtype='int64')
    survivors_df = pd.DataFrame({
        'orderid': [4, 5, 6],
        'first_execution_time': [0, 0, 0],
        'last_execution_time': [0, 0, 0],
    })
    result = aggregate._filter_to_survivors(all_sweeps, survivors_df)
    assert list(result) == []


def test_filter_to_survivors_handles_none_survivors_df():
    """If last_execution_time.parquet is missing, return all_sweeps unchanged
    AND emit a warning (caller-visible). Behaviour: pass-through."""
    all_sweeps = np.array([10, 20], dtype='int64')
    result = aggregate._filter_to_survivors(all_sweeps, None)
    assert sorted(result) == [10, 20]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dark_deduction.py::test_filter_to_survivors_intersects_orderids -v`
Expected: **FAIL** with `AttributeError: module 'aggregate' has no attribute '_filter_to_survivors'`.

---

## Task 6: Add `_filter_to_survivors` helper + load `last_execution_time.parquet`

**Files:**
- Modify: `aggregate.py` — add helper near other loaders (around line 250).

- [ ] **Step 1: Add the loader**

After `load_simulation_order_summary` (around `aggregate.py:253`), add:

```python
def load_last_execution_times(partition_dir):
    """Load last_execution_time parquet (the survivor list) from partition.

    Returns a DataFrame with columns [orderid, first_execution_time,
    last_execution_time], or None if the file is missing.
    """
    filepath = Path(partition_dir) / "last_execution_time.parquet"
    return safe_read_csv(filepath, required=False)
```

- [ ] **Step 2: Add `_filter_to_survivors` helper**

In `aggregate.py`, near the other small helpers (e.g. after `get_sweep_orderids` at line 53), add:

```python
def _filter_to_survivors(sweep_orderids, survivors_df):
    """Intersect `sweep_orderids` with the survivor orderids in
    `survivors_df` (loaded from last_execution_time.parquet).

    The simulator runs only on survivors (sweeps that passed the three-level
    filter in process.py). Real metrics must run on the same population for
    a symmetric comparison.

    If `survivors_df` is None (file missing), pass-through with a warning —
    callers degrade to pre-fix behaviour rather than fail.
    """
    import numpy as np
    if survivors_df is None or len(survivors_df) == 0:
        print("  [WARN] last_execution_time.parquet missing; "
              "real metrics will use all orders_before sweeps "
              "(asymmetric vs simulator).")
        return np.asarray(sweep_orderids, dtype='int64')
    survivor_ids = set(survivors_df['orderid'].astype('int64').tolist())
    sweep_set = set(int(x) for x in sweep_orderids)
    return np.asarray(sorted(sweep_set & survivor_ids), dtype='int64')
```

- [ ] **Step 3: Run the unit tests**

Run: `pytest tests/test_dark_deduction.py -v`
Expected: 5 PASS (2 dark-deduction tests from Task 3 + 3 survivor-filter tests).

- [ ] **Step 4: Commit**

```bash
git add aggregate.py tests/test_dark_deduction.py
git commit -m "feat: add _filter_to_survivors helper + last_execution_time loader"
```

---

## Task 7: Wire survivor filter into `_process_partition_calculate_metrics`

**Files:**
- Modify: `aggregate.py:1900-1979` (`_process_partition_calculate_metrics`)

- [ ] **Step 1: Edit the function**

Replace the body around lines 1916–1937. The pre-fix block reads:

```python
        # Get sweep order IDs
        sweep_orderids = du.get_sweep_orderids(orders_before)

        if len(sweep_orderids) == 0:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No sweep orders'
            }

        # Filter trades to sweep orders only
        sweep_trades = trades_df[trades_df[col.common.orderid].isin(sweep_orderids)].copy()

        # Step 8: Calculate REAL trade metrics
        real_metrics_result = calculate_trade_metrics(
            trades_df=sweep_trades,
            orders_df=orders_before,
            filter_orderids=list(sweep_orderids),
            role_filter=None,
            prefix='',
            is_simulated=False
        )
```

Change to:

```python
        # Get sweep order IDs (all sweeps in orders_before)
        all_sweep_orderids = du.get_sweep_orderids(orders_before)

        if len(all_sweep_orderids) == 0:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No sweep orders'
            }

        # Restrict to SURVIVORS — the population the simulator ran on.
        # Without this, real metrics are computed on ~19k orders while
        # simulated metrics are on ~17k, breaking comparison symmetry.
        survivors_df = fu.load_last_execution_times(partition_dir)
        sweep_orderids = _filter_to_survivors(all_sweep_orderids, survivors_df)

        if len(sweep_orderids) == 0:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No survivor sweep orders'
            }

        # Filter trades to survivor sweep orders only
        sweep_trades = trades_df[trades_df[col.common.orderid].isin(sweep_orderids)].copy()

        # Step 8: Calculate REAL trade metrics (survivor population)
        real_metrics_result = calculate_trade_metrics(
            trades_df=sweep_trades,
            orders_df=orders_before,
            filter_orderids=list(sweep_orderids),
            role_filter=None,
            prefix='',
            is_simulated=False
        )
```

Note the references: `fu.load_last_execution_times` requires the file-utils module alias — check `aggregate.py:1906` uses `fu.load_orders_before` already, so `fu` is in scope. If the alias is not assigned at this scope, replace `fu.load_last_execution_times` with `load_last_execution_times` (same-file function call).

`_filter_to_survivors` is a same-file function — call it bare.

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: 7 PASS (5 from this plan + 2 pre-existing).

- [ ] **Step 3: Commit**

```bash
git add aggregate.py
git commit -m "fix: real metrics now run on survivor population (parity with simulator)"
```

---

## Task 8: Integration smoke check on CBA/2024-09-05

The unit tests cover the helpers; this task confirms wiring on real data and produces the comparison numbers the user can review.

**Files:**
- Create (transient): nothing committed; just run scripts and inspect stdout.

- [ ] **Step 1: Re-run process.py for CBA/2024-09-05**

Run:
```bash
python process.py --dates 20240905 --tickers cba
```
Expected: completes without error. Watch the line `  2024-09-05/85603: N before, M after, K sweep-type contras with rest_on_lit`. **N** should be unchanged from prior run (~19k); **M** should also be ~19k rows (same row count — only `leavesquantity` shifts). Note `M` rowcount equals `N` by construction.

- [ ] **Step 2: Spot-check that `orders_after.leavesquantity` shrank for at least one sweep**

Run:
```bash
python -c "
import pandas as pd
before = pd.read_parquet('data/processed/2024-09-05/85603/orders_before_matching.parquet')
after  = pd.read_parquet('data/processed/2024-09-05/85603/orders_after_matching.parquet')
m = before.merge(after[['orderid', 'leavesquantity']], on='orderid', suffixes=('_before','_after'))
shrunk = m[m.leavesquantity_after < m.leavesquantity_before]
print(f'Sweeps with dark-at-submit deduction: {len(shrunk):,} of {len(m):,}')
print(f'Total dark deducted: {(m.leavesquantity_before - m.leavesquantity_after).sum():,}')
"
```
Expected: deducted total **strictly greater** than the pre-fix total (which only counted DS=47). If unchanged, Fix #2 is not wired.

- [ ] **Step 3: Re-run aggregate.py for CBA/2024-09-05**

Run:
```bash
python aggregate.py --orderbookids 85603 --dates 20240905
```
Expected: completes; per-partition log line shows ~17k orders, **not** ~19k. (The exact 17,323 number from the handoff may shift slightly because Fix #2 changes `orders_after.leavesquantity`, which can drop orders to zero leaves and remove them from the survivor set — re-baseline the number off this run.)

- [ ] **Step 4: Inspect the trade-level comparison**

Run:
```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/outputs/2024-09-05/85603/trade_level_comparison.parquet')
print(f'Comparison rows: {len(df):,}')
print(df['match_type'].value_counts() if 'match_type' in df.columns else df.head())
"
```
Expected: row count similar order-of-magnitude to the pre-fix 6,489 overlap, but should now reflect the symmetric population. EXACT_MATCH share may shift.

- [ ] **Step 5: Report results to the user**

Stop. Report:
- New survivor count.
- New total dark-at-submission deducted (Step 2 number).
- New comparison row count + EXACT_MATCH share.

Do **not** commit anything in Task 8 — it is read-only verification.

---

## Self-review notes

- **Spec coverage:** Both handoff items #1 and #2 are covered by Tasks 5–7 and Tasks 1–4 respectively. Item #3 (parity baseline regen) is explicitly out of scope per the "Decisions to confirm" block.
- **Placeholder scan:** No TBDs / "implement later" / abstract instructions — every step has either code or a concrete command. Test data uses inline literals.
- **Type consistency:** `_filter_to_survivors` returns `np.ndarray[int64]`. `du.get_sweep_orderids` returns `np.ndarray` (line 53 — `.unique()` on int column). `calculate_trade_metrics(filter_orderids=list(...))` accepts a list — the wiring in Task 7 wraps with `list(...)`. `_synthesize_orders_after` consumes/returns pandas DataFrames matching the pre-existing inline block's contract (same columns).
- **Risk:** Task 3 changes `orders_after.leavesquantity` numbers globally — anything downstream that previously cached a number off the old DS=47-only behaviour will shift. The handoff's "871,578 sweep_qty" baseline number will move; this is expected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-survivor-filter-and-broader-dark-deduction.md`.

**Before executing**, please confirm the three "Decisions to confirm" at the top:
1. Dealsource set `{46, 47, 49, 50, 51, 52}` — accept, or restrict to handoff's `{46, 47, 50, 51}`?
2. Delete dead `_compute_dark_at_submission_qty` helper — accept?
3. Test scope: 5 unit tests + 1 integration smoke — accept, or want broader/narrower?

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
