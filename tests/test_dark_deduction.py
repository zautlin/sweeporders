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
