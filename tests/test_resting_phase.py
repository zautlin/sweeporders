"""
Tests for Phase 2 resting simulation.

Covers all config-flag scenarios:
  S2  — dark-only resting
  S3  — dual-venue resting (dark + lit)
  midtick on/off
  cancellation on/off
  crossing keys on/off (block and allow)
  session filter on/off
  MAQ block and allow
  preferencing
  lit full book (Option A) vs scan (Option B)
  no remainder (fully filled in Phase 1 → no Phase 2)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pandas as pd
import numpy as np
import pytest
import config.config as cfg
from pipeline.sweep_simulator import (
    simulate_resting_phase,
    build_remainder_df,
    _calc_resting_price,
    _calc_lit_resting_price,
    _get_session_end_time,
    _build_lit_order_book,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture helpers
# ─────────────────────────────────────────────────────────────────────────────

T0  = 1_725_448_000_000_000_000   # OPEN session start
T1  = T0 + 10_000_000_000         # T0 + 10 s  (rest entry)
T2  = T1 + 20_000_000_000         # T0 + 30 s  (contra arrives)
T3  = T1 + 50_000_000_000         # T0 + 60 s  (after session end for cancellation tests)
SESSION_END = T1 + 30_000_000_000  # session ends between T2 and T3

OB   = 110621
TICK = 100
LIMIT_BUY  = 3000   # buy sweep limit
LIMIT_SELL = 3200   # sell sweep limit
MIDPOINT   = 3000   # NBBO mid = (2950+3050)/2 = 3000 -- inside buy limit
NBBO_BID   = 2950
NBBO_OFFER = 3050


def make_session_df(open_start=T0, close_start=SESSION_END):
    """Minimal session states: OPEN then PRE_CSPA."""
    return pd.DataFrame([
        {'timestamp': open_start,  'session_state': 'OPEN'},
        {'timestamp': close_start, 'session_state': 'PRE_CSPA'},
    ])


def make_nbbo_df(bid=NBBO_BID, offer=NBBO_OFFER, ts=T0):
    return pd.DataFrame([{
        'timestamp': ts,
        'orderbookid': OB,
        'bid': bid,
        'offer': offer,
        'national_bid': bid,
        'national_offer': offer,
    }])


def make_remainder(orderid=1, rest_entry_time=T1, remaining_qty=500,
                   limit_price=LIMIT_BUY, side=1, participantid=10,
                   crossingkey=0, minimumquantity=0, singlefillminimumquantity=0,
                   preferenceonly=0):
    return pd.DataFrame([{
        'orderid': orderid,
        'rest_entry_time': rest_entry_time,
        'remaining_qty': remaining_qty,
        'limit_price': limit_price,
        'side': side,
        'orderbookid': OB,
        'midtick': 2,
        'timevaliditydecoded': 'Rest of Day',
        'participantid': participantid,
        'crossingkey': crossingkey,
        'minimumquantity': minimumquantity,
        'singlefillminimumquantity': singlefillminimumquantity,
        'preferenceonly': preferenceonly,
    }])


def make_cp_contra(orderid=99, ts=T2, side=2, qty=300, national_bid=NBBO_BID,
                   national_offer=NBBO_OFFER, participantid=20, crossingkey=0,
                   midtick=2, orderbookid=OB):
    """One CP contra order on the sell side (default) arriving after rest_entry."""
    return pd.DataFrame([{
        'orderid': orderid,
        'timestamp': ts,
        'effective_timestamp': ts,
        'sequence': 1,
        'side': side,
        'quantity': qty,
        'orderbookid': orderbookid,
        'national_bid': national_bid,
        'national_offer': national_offer,
        'bid': national_bid,
        'offer': national_offer,
        'participantid': participantid,
        'crossingkey': crossingkey,
        'midtick': midtick,
        'minimumquantity': 0,
        'singlefillminimumquantity': 0,
        'ordertype': 1,
        'exchangeordertype': 4096,
        'price': 3000,
        'timevalidity': 1300,
        'display_quantity': None,
    }])


def make_lit_contra(orderid=200, ts=T2, side=2, qty=300, price=NBBO_BID,
                    participantid=30, crossingkey=0, orderbookid=OB):
    """One lit contra order (type 0) on the sell side."""
    return pd.DataFrame([{
        'orderid': orderid,
        'timestamp': ts,
        'effective_timestamp': ts,
        'sequence': 1,
        'side': side,
        'quantity': qty,
        'orderbookid': orderbookid,
        'price': price,
        'participantid': participantid,
        'crossingkey': crossingkey,
        'exchangeordertype': 0,
        'orderstatus': 1,
        'ordertype': 1,
        'national_bid': NBBO_BID,
        'national_offer': NBBO_OFFER,
        'changereason': 6,
        'orderbookposition': 0,
    }])


def run_resting(remainder_df, cp_orders, lit_orders=None, session_df=None,
                sweep_orders=None):
    """Convenience wrapper — pass None sweep_orders since we only test Phase 2."""
    if sweep_orders is None:
        sweep_orders = pd.DataFrame(columns=['orderid'])
    return simulate_resting_phase(
        sweep_orders=sweep_orders,
        remainder_df=remainder_df,
        all_cp_orders=cp_orders,
        lit_orders_raw=lit_orders,
        nbbo_data=None,           # INTERNAL NBBO — taken from contra order fields
        session_states_df=session_df,
        tick_size_override=TICK,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: save & restore config flags
# ─────────────────────────────────────────────────────────────────────────────

class ConfigPatch:
    """Context manager to temporarily override config flags."""
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.originals = {}

    def __enter__(self):
        for k, v in self.kwargs.items():
            self.originals[k] = getattr(cfg, k)
            setattr(cfg, k, v)
        return self

    def __exit__(self, *_):
        for k, v in self.originals.items():
            setattr(cfg, k, v)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Helper function unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_calc_resting_price_midtick_on():
    with ConfigPatch(RESTING_USE_MIDTICK=True):
        # BUY: limit + half-tick
        assert _calc_resting_price(3000, 100, side=1) == 3050
        # SELL: limit - half-tick
        assert _calc_resting_price(3200, 100, side=2) == 3150


def test_calc_resting_price_midtick_off():
    with ConfigPatch(RESTING_USE_MIDTICK=False):
        assert _calc_resting_price(3000, 100, side=1) == 3000
        assert _calc_resting_price(3200, 100, side=2) == 3200


def test_calc_lit_resting_price_use_limit():
    with ConfigPatch(RESTING_LIT_USE_LIMIT=True):
        assert _calc_lit_resting_price(3000, 100, side=1) == 3000
        assert _calc_lit_resting_price(3200, 100, side=2) == 3200


def test_calc_lit_resting_price_half_tick():
    with ConfigPatch(RESTING_LIT_USE_LIMIT=False):
        assert _calc_lit_resting_price(3000, 100, side=1) == 3050
        assert _calc_lit_resting_price(3200, 100, side=2) == 3150


def test_get_session_end_time():
    session_df = make_session_df(open_start=T0, close_start=SESSION_END)
    end = _get_session_end_time(session_df, after_timestamp=T1)
    assert end == SESSION_END


def test_get_session_end_time_no_data():
    end = _get_session_end_time(None, after_timestamp=T1)
    assert end == int(2**62)


def test_build_lit_order_book():
    lit = make_lit_contra(orderid=1, side=2, price=2900)
    lit2 = make_lit_contra(orderid=2, side=2, price=3100)
    lit3 = make_lit_contra(orderid=3, side=1, price=3000)
    all_lit = pd.concat([lit, lit2, lit3], ignore_index=True)
    book = _build_lit_order_book(all_lit, OB)
    assert len(book['sell']) == 2
    assert len(book['buy']) == 1
    # Sell side sorted price ASC
    assert book['sell'][0]['price'] <= book['sell'][1]['price']
    # Buy side sorted price DESC
    assert book['buy'][0]['price'] == 3000


# ─────────────────────────────────────────────────────────────────────────────
# 2. S2 — Dark-only resting (SIMULATE_LIT_RESTING=False)
# ─────────────────────────────────────────────────────────────────────────────

def test_s2_basic_dark_fill():
    """BUY sweep resting in CP, contra SELL arrives → should fill."""
    rem = make_remainder(side=1, limit_price=LIMIT_BUY)
    cp  = make_cp_contra(side=2, national_bid=NBBO_BID, national_offer=NBBO_OFFER)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    trades = result['resting_trades']
    summary = result['resting_summary']
    assert len(trades) == 2, f"Expected 2 trade rows (paired), got {len(trades)}"
    assert summary.iloc[0]['dark_filled_qty'] == 300
    assert summary.iloc[0]['lit_filled_qty'] == 0
    assert all(trades['venue'] == 'dark')
    assert all(trades['phase'] == 2)


def test_s2_no_fill_when_midpoint_outside_resting_price():
    """
    RESTING_USE_MIDTICK=True: dark_resting_price for BUY at 3000 = 3050.
    Midpoint = 3100 > 3050 → no fill.
    """
    rem = make_remainder(side=1, limit_price=LIMIT_BUY)
    # NBBO: bid=3050, offer=3150 → midpoint=3100
    cp = make_cp_contra(side=2, national_bid=3050, national_offer=3150)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 0


def test_s2_midtick_off_uses_limit_price():
    """
    RESTING_USE_MIDTICK=False: dark resting price = limit = 3000.
    Midpoint = 3000 → exactly on limit → should fill.
    """
    rem = make_remainder(side=1, limit_price=3000)
    cp  = make_cp_contra(side=2, national_bid=2950, national_offer=3050)  # mid=3000

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=False, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 3. Cancellation
# ─────────────────────────────────────────────────────────────────────────────

def test_cancellation_on_blocks_late_contra():
    """Contra arrives after session end → no fill."""
    session_df = make_session_df(open_start=T0, close_start=T1 + 5_000_000_000)
    # Contra at T2 which is after T1+5s session end
    cp = make_cp_contra(ts=T2, side=2, national_bid=NBBO_BID, national_offer=NBBO_OFFER)
    rem = make_remainder(side=1)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=True, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, session_df=session_df)

    assert len(result['resting_trades']) == 0


def test_cancellation_off_allows_late_contra():
    """Same setup but cancellation disabled → fills."""
    session_df = make_session_df(open_start=T0, close_start=T1 + 5_000_000_000)
    cp = make_cp_contra(ts=T2, side=2, national_bid=NBBO_BID, national_offer=NBBO_OFFER)
    rem = make_remainder(side=1)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, session_df=session_df)

    assert len(result['resting_trades']) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. Crossing Keys
# ─────────────────────────────────────────────────────────────────────────────

def test_crossing_keys_same_participant_key_zero_blocked():
    """Same participant, crossing key = 0 on both → blocked."""
    rem = make_remainder(participantid=10, crossingkey=0, side=1)
    cp  = make_cp_contra(participantid=10, crossingkey=0, side=2)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=True, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 0


def test_crossing_keys_same_participant_matching_keys_allowed():
    """Same participant, matching non-zero crossing keys → allowed."""
    rem = make_remainder(participantid=10, crossingkey=5, side=1)
    cp  = make_cp_contra(participantid=10, crossingkey=5, side=2)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=True, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 2


def test_crossing_keys_same_participant_mismatched_keys_blocked():
    """Same participant, different crossing keys → blocked."""
    rem = make_remainder(participantid=10, crossingkey=5, side=1)
    cp  = make_cp_contra(participantid=10, crossingkey=9, side=2)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=True, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 0


def test_crossing_keys_off_same_participant_key_zero_allowed():
    """Crossing key check disabled → fills even with zero crossing keys, same participant."""
    rem = make_remainder(participantid=10, crossingkey=0, side=1)
    cp  = make_cp_contra(participantid=10, crossingkey=0, side=2)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 5. Session Filter
# ─────────────────────────────────────────────────────────────────────────────

def test_session_filter_blocks_non_open_contra():
    """Contra arrives during PRE_CSPA session → blocked when filter on."""
    session_df = make_session_df(open_start=T0, close_start=T1 + 5_000_000_000)
    cp = make_cp_contra(ts=T2, side=2, national_bid=NBBO_BID, national_offer=NBBO_OFFER)
    rem = make_remainder(side=1)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=True,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, session_df=session_df)

    assert len(result['resting_trades']) == 0


def test_session_filter_off_allows_non_open_contra():
    """Same setup but filter disabled → fills."""
    session_df = make_session_df(open_start=T0, close_start=T1 + 5_000_000_000)
    cp = make_cp_contra(ts=T2, side=2, national_bid=NBBO_BID, national_offer=NBBO_OFFER)
    rem = make_remainder(side=1)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, session_df=session_df)

    assert len(result['resting_trades']) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 6. MAQ
# ─────────────────────────────────────────────────────────────────────────────

def test_maq_blocks_small_fill():
    """MAQ=400, contra has 300 → potential_qty=300 < MAQ → no fill."""
    rem = make_remainder(side=1, remaining_qty=500, minimumquantity=400,
                         singlefillminimumquantity=0)
    cp  = make_cp_contra(side=2, qty=300)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=True,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 0


def test_maq_allows_sufficient_fill():
    """MAQ=200, contra has 300 → potential_qty=300 >= MAQ → fills 300."""
    rem = make_remainder(side=1, remaining_qty=500, minimumquantity=200,
                         singlefillminimumquantity=0)
    cp  = make_cp_contra(side=2, qty=300)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=True,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 2
    assert result['resting_summary'].iloc[0]['dark_filled_qty'] == 300


def test_maq_off_allows_small_fill():
    """MAQ=400 but flag disabled → fills 300."""
    rem = make_remainder(side=1, remaining_qty=500, minimumquantity=400)
    cp  = make_cp_contra(side=2, qty=300)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 7. Preferencing
# ─────────────────────────────────────────────────────────────────────────────

def test_preferencing_same_participant_fills_first():
    """
    Two resting sweeps: sweep A (participant 10) and sweep B (participant 20).
    One contra from participant 10 (same as A) with enough qty for only one sweep.
    Preferencing on → sweep A fills.
    """
    # Sweep A opts into preferencing (preferenceonly=1); B does not.
    rem_A = make_remainder(orderid=1, participantid=10, side=1, remaining_qty=300,
                           rest_entry_time=T1, preferenceonly=1)
    rem_B = make_remainder(orderid=2, participantid=20, side=1, remaining_qty=300,
                           rest_entry_time=T1 - 1_000_000_000)  # B entered earlier
    rem = pd.concat([rem_A, rem_B], ignore_index=True)
    cp = make_cp_contra(side=2, qty=300, participantid=10)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=True):
        result = run_resting(rem, cp)

    summary = result['resting_summary'].set_index('orderid')
    # Sweep A (participant 10, preferenceonly=1) should fill; sweep B should not
    assert summary.loc[1, 'dark_filled_qty'] == 300
    assert summary.loc[2, 'dark_filled_qty'] == 0


def test_preferencing_off_fifo_fills_earlier_order():
    """Preferencing off → strict FIFO. Sweep B entered earlier → fills first."""
    rem_A = make_remainder(orderid=1, participantid=10, side=1, remaining_qty=300,
                           rest_entry_time=T1)
    rem_B = make_remainder(orderid=2, participantid=20, side=1, remaining_qty=300,
                           rest_entry_time=T1 - 1_000_000_000)
    rem = pd.concat([rem_A, rem_B], ignore_index=True)
    cp = make_cp_contra(side=2, qty=300, participantid=10)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    summary = result['resting_summary'].set_index('orderid')
    # B entered resting queue earlier → fills first in FIFO
    assert summary.loc[2, 'dark_filled_qty'] == 300
    assert summary.loc[1, 'dark_filled_qty'] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 8. S3 Lit — Option B (scan)
# ─────────────────────────────────────────────────────────────────────────────

def test_s3_lit_scan_basic_fill():
    """BUY sweep resting in lit, contra sell at price <= limit → fills."""
    rem = make_remainder(side=1, limit_price=3000)
    # No CP contra (so dark leg gets nothing), but lit contra sells at 2900 <= 3000
    cp = pd.DataFrame(columns=['orderid', 'timestamp', 'effective_timestamp',
                                'sequence', 'side', 'quantity', 'orderbookid',
                                'national_bid', 'national_offer', 'bid', 'offer',
                                'participantid', 'crossingkey', 'midtick',
                                'minimumquantity', 'singlefillminimumquantity',
                                'ordertype', 'exchangeordertype', 'price',
                                'timevalidity', 'display_quantity'])
    lit = make_lit_contra(side=2, price=2900, qty=300)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=True,
                     RESTING_LIT_BOOK_MODE='scan', RESTING_LIT_USE_LIMIT=True,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, lit_orders=lit)

    summary = result['resting_summary']
    assert summary.iloc[0]['lit_filled_qty'] == 300
    trades = result['resting_trades']
    assert any(trades['venue'] == 'lit')


def test_s3_lit_scan_no_fill_price_too_high():
    """Contra sell price > limit → no fill."""
    rem = make_remainder(side=1, limit_price=3000)
    cp = pd.DataFrame(columns=make_lit_contra().columns)
    lit = make_lit_contra(side=2, price=3100, qty=300)  # price above limit

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=True,
                     RESTING_LIT_BOOK_MODE='scan', RESTING_LIT_USE_LIMIT=True,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, lit_orders=lit)

    assert result['resting_summary'].iloc[0]['lit_filled_qty'] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 9. S3 Lit — Option A (full book)
# ─────────────────────────────────────────────────────────────────────────────

def test_s3_lit_full_book_basic_fill():
    """BUY sweep at 3000; lit SELL at 2900 arrives → price crosses limit → fills."""
    rem = make_remainder(side=1, limit_price=3000)
    cp = pd.DataFrame(columns=make_cp_contra().columns)
    lit = make_lit_contra(side=2, price=2900, qty=300)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=True,
                     RESTING_LIT_BOOK_MODE='full', RESTING_LIT_USE_LIMIT=True,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, lit_orders=lit)

    assert result['resting_summary'].iloc[0]['lit_filled_qty'] == 300


def test_s3_lit_full_book_no_fill_price_too_high():
    """Lit SELL arrives at price 3100 > limit 3000 → no fill."""
    rem = make_remainder(side=1, limit_price=3000)
    cp = pd.DataFrame(columns=make_cp_contra().columns)
    lit = make_lit_contra(side=2, price=3100, qty=300)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=True,
                     RESTING_LIT_BOOK_MODE='full', RESTING_LIT_USE_LIMIT=True,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, lit_orders=lit)

    assert result['resting_summary'].iloc[0]['lit_filled_qty'] == 0


def test_s3_lit_full_book_vs_scan_same_result_simple_case():
    """In the absence of competing orders, Option A and Option B give same fill."""
    rem = make_remainder(side=1, limit_price=3000)
    cp = pd.DataFrame(columns=make_cp_contra().columns)
    lit = make_lit_contra(side=2, price=2900, qty=300)

    flags = dict(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=True,
                 RESTING_LIT_USE_LIMIT=True, RESTING_USE_MIDTICK=True,
                 RESTING_APPLY_SESSION_FILTER=False, RESTING_APPLY_CROSSING_KEYS=False,
                 RESTING_APPLY_MAQ=False, RESTING_MODEL_CANCELLATION=False,
                 RESTING_APPLY_PREFERENCING=False)

    with ConfigPatch(**flags, RESTING_LIT_BOOK_MODE='full'):
        res_a = run_resting(rem.copy(), cp.copy(), lit_orders=lit.copy())

    with ConfigPatch(**flags, RESTING_LIT_BOOK_MODE='scan'):
        res_b = run_resting(rem.copy(), cp.copy(), lit_orders=lit.copy())

    assert res_a['resting_summary'].iloc[0]['lit_filled_qty'] == \
           res_b['resting_summary'].iloc[0]['lit_filled_qty']


# ─────────────────────────────────────────────────────────────────────────────
# 10. No remainder
# ─────────────────────────────────────────────────────────────────────────────

def test_no_remainder_returns_empty():
    """Empty remainder_df → empty trade output."""
    rem = pd.DataFrame(columns=['orderid', 'rest_entry_time', 'remaining_qty',
                                 'limit_price', 'side', 'orderbookid', 'midtick',
                                 'timevaliditydecoded', 'participantid', 'crossingkey',
                                 'minimumquantity', 'singlefillminimumquantity'])
    cp = make_cp_contra(side=2)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=False,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp)

    assert len(result['resting_trades']) == 0
    assert len(result['resting_summary']) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 11. build_remainder_df
# ─────────────────────────────────────────────────────────────────────────────

def test_build_remainder_df_excludes_fully_filled():
    """Orders with phase1 fill = available qty should not appear in remainder."""
    sweep_orders = pd.DataFrame([{
        'orderid': 1, 'timestamp': T0, 'sequence': 1, 'side': 1,
        'leavesquantity': 300, 'matched_quantity': 300,
        'price': 3000, 'last_execution_time': T1, 'orderbookid': OB,
        'minimumquantity': 0, 'singlefillminimumquantity': 0,
        'crossingkey': 0, 'participantid': 10, 'midtick': 2,
    }])
    sweep_usage = {1: {'matched_quantity': 300, 'num_matches': 1}}
    rem = build_remainder_df(sweep_orders, sweep_usage)
    assert len(rem) == 0


def test_build_remainder_df_includes_partial_fill():
    """Order partially filled → appears in remainder with correct qty."""
    sweep_orders = pd.DataFrame([{
        'orderid': 2, 'timestamp': T0, 'sequence': 1, 'side': 1,
        'leavesquantity': 500, 'matched_quantity': 0,
        'price': 3000, 'last_execution_time': T1, 'orderbookid': OB,
        'minimumquantity': 0, 'singlefillminimumquantity': 0,
        'crossingkey': 0, 'participantid': 10, 'midtick': 2,
    }])
    sweep_usage = {2: {'matched_quantity': 200, 'num_matches': 1}}
    rem = build_remainder_df(sweep_orders, sweep_usage)
    assert len(rem) == 1
    assert rem.iloc[0]['remaining_qty'] == 300
    assert rem.iloc[0]['rest_entry_time'] == T1


# ─────────────────────────────────────────────────────────────────────────────
# 12. S3 dual venue — dark fills first, lit gets remainder
# ─────────────────────────────────────────────────────────────────────────────

def test_s3_dark_fills_partial_lit_fills_rest():
    """
    Sweep has 500 qty. Dark CP contra has 200 → dark fills 200.
    Lit contra has 400 → lit fills remaining 300.
    Total = 500 (fully filled).
    """
    rem = make_remainder(side=1, remaining_qty=500, limit_price=3000)
    cp  = make_cp_contra(side=2, qty=200)
    lit = make_lit_contra(side=2, price=2900, qty=400)

    with ConfigPatch(SIMULATE_RESTING_PHASE=True, SIMULATE_LIT_RESTING=True,
                     RESTING_LIT_BOOK_MODE='scan', RESTING_LIT_USE_LIMIT=True,
                     RESTING_USE_MIDTICK=True, RESTING_APPLY_SESSION_FILTER=False,
                     RESTING_APPLY_CROSSING_KEYS=False, RESTING_APPLY_MAQ=False,
                     RESTING_MODEL_CANCELLATION=False, RESTING_APPLY_PREFERENCING=False):
        result = run_resting(rem, cp, lit_orders=lit)

    s = result['resting_summary'].iloc[0]
    assert s['dark_filled_qty'] == 200
    assert s['lit_filled_qty'] == 300
    assert s['total_resting_filled_qty'] == 500


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import pytest as _pt
    _pt.main([__file__, '-v'])
