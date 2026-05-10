"""Tests for the orders_after dark-at-submission deduction logic.

The `orders_after` frame is synthesized post-dark/pre-lit: it carries the
same orders_before rows but with `leavesquantity` overridden to
(quantity − dark_at_submission). The simulator then uses
`orders_after.leavesquantity` as `sweep_qty`.

Pre-fix: only dealsource==47 (CentrePoint) trades at the order's NEW_ORDER
timestamp were deducted.
Post-fix: dealsources {46, 47, 49, 50, 51, 52} are all deducted.
"""
import sys
import os

# Ensure the repo root is on sys.path so `import process` / `import config`
# resolve to the flat top-level files, not any src/ package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np
import pandas as pd
import pytest

import aggregate
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

    # Call the function under test.
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


# ─── _derive_passive_aggressive (server-trades pa derivation) ────────────────

def test_derive_pa_no_op_when_already_present():
    """Local raw trades carry pa already — derivation must not modify them.
    Idempotent guard preserves parity for non-server runs."""
    trades = pd.DataFrame({
        'orderid':           [1],
        'matchgroupid':      [10],
        'passiveaggressive': [1],
    })
    out = process._derive_passive_aggressive(trades, pd.DataFrame())
    assert list(out['passiveaggressive']) == [1]


def test_derive_pa_aggressor_is_later_arriving():
    """Within a matchgroupid pair, the leg whose orderid has the LATER
    NEW_ORDER (cr=6) timestamp is the aggressor (pa=1). The earlier-arriving
    leg is passive (pa=0)."""
    orders = pd.DataFrame({
        'orderid':      [1, 2],
        'changereason': [6, 6],
        'timestamp':    [100, 200],   # order 2 arrived after order 1
    })
    trades = pd.DataFrame({
        'orderid':      [1, 2],
        'matchgroupid': [10, 10],
    })
    out = process._derive_passive_aggressive(trades, orders)
    pa_by_oid = dict(zip(out['orderid'], out['passiveaggressive']))
    assert pa_by_oid[1] == 0   # earlier arrival → passive
    assert pa_by_oid[2] == 1   # later arrival → aggressor


def test_derive_pa_missing_order_yields_neither():
    """If one leg's orderid isn't in the orders frame for this partition,
    pa for both legs of that match falls back to 2 (Neither). Conservative —
    excludes the match from the phantom-liquidity guard rather than
    guessing wrong."""
    orders = pd.DataFrame({
        'orderid':      [1],
        'changereason': [6],
        'timestamp':    [100],
    })
    trades = pd.DataFrame({
        'orderid':      [1, 999],          # 999 not in orders frame
        'matchgroupid': [10, 10],
    })
    out = process._derive_passive_aggressive(trades, orders)
    assert (out['passiveaggressive'] == 2).all()


def test_derive_pa_equal_timestamps_yields_neither():
    """Both legs sharing the exact same NEW_ORDER timestamp is rare but
    possible. Without a cleaner tie-break than equality, mark both pa=2."""
    orders = pd.DataFrame({
        'orderid':      [1, 2],
        'changereason': [6, 6],
        'timestamp':    [100, 100],
    })
    trades = pd.DataFrame({
        'orderid':      [1, 2],
        'matchgroupid': [10, 10],
    })
    out = process._derive_passive_aggressive(trades, orders)
    assert (out['passiveaggressive'] == 2).all()
