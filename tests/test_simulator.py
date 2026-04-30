"""Unit tests for simulator.py.

Sprint 1 Foundations: Decision enum, SimFlags, SimContext, rule helpers.
"""



# ─────────────────────────────────────────────────────────────────────────────
# Decision enum
# ─────────────────────────────────────────────────────────────────────────────

class TestDecision:
    def test_has_ok_skip_break_members(self):
        from simulator import Decision
        assert Decision.OK.name == 'OK'
        assert Decision.SKIP.name == 'SKIP'
        assert Decision.BREAK.name == 'BREAK'

    def test_ordering_ok_lt_skip_lt_break(self):
        from simulator import Decision
        assert Decision.OK < Decision.SKIP < Decision.BREAK

    def test_is_int_enum(self):
        from simulator import Decision
        assert int(Decision.OK) == 0
        assert int(Decision.SKIP) == 1
        assert int(Decision.BREAK) == 2


# ─────────────────────────────────────────────────────────────────────────────
# SimFlags
# ─────────────────────────────────────────────────────────────────────────────

def _flags_kwargs():
    """Canonical kwargs matching every field the current simulator reads from cfg."""
    return dict(
        nbbo_source='INTERNAL',
        simulate_resting_phase=False,
        simulate_lit_resting=False,
        resting_lit_book_mode='scan',
        resting_use_midtick=False,
        resting_lit_use_limit=True,
        resting_model_cancellation=True,
        resting_apply_crossing_keys=True,
        resting_apply_session_filter=True,
        resting_apply_maq=True,
        resting_apply_preferencing=True,
        resting_apply_iceberg=True,
        use_polars_transforms=False,
        use_duckdb_io=False,
        min_block_size=0,
    )


class TestSimFlags:
    def test_construct_with_all_fields(self):
        from simulator import SimFlags
        f = SimFlags(**_flags_kwargs())
        assert f.nbbo_source == 'INTERNAL'
        assert f.min_block_size == 0

    def test_frozen_raises_on_mutation(self):
        import dataclasses
        from simulator import SimFlags
        f = SimFlags(**_flags_kwargs())
        try:
            f.nbbo_source = 'EXTERNAL'
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("SimFlags should be frozen")

    def test_slots_no_dict(self):
        from simulator import SimFlags
        f = SimFlags(**_flags_kwargs())
        assert not hasattr(f, '__dict__')

    def test_replace_roundtrip(self):
        import dataclasses
        from simulator import SimFlags
        f = SimFlags(**_flags_kwargs())
        g = dataclasses.replace(f, nbbo_source='EXTERNAL')
        assert f.nbbo_source == 'INTERNAL'
        assert g.nbbo_source == 'EXTERNAL'
        # All other fields preserved
        assert g.min_block_size == f.min_block_size
        assert g.simulate_resting_phase == f.simulate_resting_phase


# ─────────────────────────────────────────────────────────────────────────────
# SimContext
# ─────────────────────────────────────────────────────────────────────────────

def _context_kwargs():
    """Construct a minimal SimContext with 2-sweep / 3-contra synthetic arrays."""
    import numpy as np
    from simulator import SimFlags
    N_s, N_c = 2, 3
    return dict(
        # Sweep arrays
        sweep_orderid       = np.array([1001, 1002], dtype=np.int64),
        sweep_eff_ts        = np.array([100, 200], dtype=np.int64),
        sweep_side          = np.array([1, 2], dtype=np.int8),
        sweep_qty           = np.array([500, 300], dtype=np.int64),
        sweep_first_exec    = np.array([100, 200], dtype=np.int64),
        sweep_last_exec    = np.array([150, 250], dtype=np.int64),
        sweep_price         = np.array([1000, 1010], dtype=np.int64),
        sweep_maq           = np.zeros(N_s, dtype=np.int64),
        sweep_sfmq          = np.zeros(N_s, dtype=np.int8),
        sweep_crossingkey   = np.zeros(N_s, dtype=np.int64),
        sweep_participant   = np.array([10, 20], dtype=np.int32),
        sweep_midtick       = np.zeros(N_s, dtype=np.int8),
        sweep_orderbookid   = np.array([100, 100], dtype=np.int32),
        sweep_lost_priority = np.zeros(N_s, dtype=bool),
        sweep_changereason  = np.zeros(N_s, dtype=np.int8),
        # Contra arrays
        contra_orderid      = np.array([2001, 2002, 2003], dtype=np.int64),
        contra_eff_ts       = np.array([50, 120, 220], dtype=np.int64),
        contra_sequence     = np.array([1, 2, 3], dtype=np.int64),
        contra_side         = np.array([2, 1, 2], dtype=np.int8),
        contra_qty          = np.array([100, 200, 300], dtype=np.int64),
        contra_price        = np.array([1000, 1005, 1010], dtype=np.int64),
        contra_maq          = np.zeros(N_c, dtype=np.int64),
        contra_sfmq         = np.zeros(N_c, dtype=np.int8),
        contra_crossingkey  = np.zeros(N_c, dtype=np.int64),
        contra_participant  = np.array([10, 30, 10], dtype=np.int32),
        contra_midtick      = np.zeros(N_c, dtype=np.int8),
        contra_orderbookid  = np.array([100, 100, 100], dtype=np.int32),
        contra_display_qty  = np.zeros(N_c, dtype=np.int64),
        contra_ordertype    = np.ones(N_c, dtype=np.int8),
        contra_nbbo_bid     = np.array([995, 1000, 1005], dtype=np.int64),
        contra_nbbo_offer   = np.array([1005, 1010, 1015], dtype=np.int64),
        contra_bid          = np.array([995, 1000, 1005], dtype=np.int64),
        contra_offer        = np.array([1005, 1010, 1015], dtype=np.int64),
        # Session state
        session_ts          = np.array([0], dtype=np.int64),
        session_state       = np.array([1], dtype=np.int8),
        # NBBO (None when using INTERNAL)
        nbbo_ts             = None,
        nbbo_bid            = None,
        nbbo_offer          = None,
        # Reference scalars
        tick_size           = 1,
        tick_size_table     = None,
        price_lower         = 0,
        price_upper         = 100_000_000,
        participants        = {10: 'BROKER', 20: 'MM', 30: 'BROKER'},
        # Flags
        cfg_flags           = SimFlags(**_flags_kwargs()),
    )


class TestSimContext:
    def test_construct_with_synthetic_arrays(self):
        from simulator import SimContext
        ctx = SimContext(**_context_kwargs())
        assert len(ctx.sweep_orderid) == 2
        assert len(ctx.contra_orderid) == 3
        assert ctx.tick_size == 1
        assert ctx.participants[10] == 'BROKER'

    def test_frozen_raises_on_mutation(self):
        import dataclasses
        from simulator import SimContext
        ctx = SimContext(**_context_kwargs())
        try:
            ctx.tick_size = 99
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("SimContext should be frozen")

    def test_slots_no_dict(self):
        from simulator import SimContext
        ctx = SimContext(**_context_kwargs())
        assert not hasattr(ctx, '__dict__')

    def test_replace_roundtrip(self):
        import dataclasses
        from simulator import SimContext
        ctx = SimContext(**_context_kwargs())
        ctx2 = dataclasses.replace(ctx, tick_size=5)
        assert ctx.tick_size == 1
        assert ctx2.tick_size == 5
        # Arrays preserved (same object — dataclass doesn't deep-copy)
        assert ctx2.sweep_orderid is ctx.sweep_orderid


# ─────────────────────────────────────────────────────────────────────────────
# Rule: is_apb(contra_ordertype, contra_midtick) -> bool
# ─────────────────────────────────────────────────────────────────────────────
# Per dd.txt §1045 + _legacy.py:812: APB = exchangeordertype 4096 (Centre Point
# Block Limit) AND midtick ∈ {5, 6} (ANY_PRICE_BLOCK or ..._WITH_MIDTICK).

class TestIsApb:
    def test_block_limit_with_apb_midtick_5(self):
        from simulator import is_apb
        assert is_apb(contra_ordertype=4096, contra_midtick=5) is True

    def test_block_limit_with_apb_midtick_6(self):
        from simulator import is_apb
        assert is_apb(contra_ordertype=4096, contra_midtick=6) is True

    def test_block_limit_with_non_apb_midtick(self):
        from simulator import is_apb
        assert is_apb(contra_ordertype=4096, contra_midtick=0) is False
        assert is_apb(contra_ordertype=4096, contra_midtick=1) is False
        assert is_apb(contra_ordertype=4096, contra_midtick=2) is False

    def test_non_block_type_with_apb_midtick_still_false(self):
        from simulator import is_apb
        # Midtick 5 on a type-64 contra is NOT APB — the type must also match
        assert is_apb(contra_ordertype=64, contra_midtick=5) is False
        assert is_apb(contra_ordertype=2048, contra_midtick=5) is False


# ─────────────────────────────────────────────────────────────────────────────
# Rule: is_valid_session(state_enum: int) -> bool
# ─────────────────────────────────────────────────────────────────────────────
# Per bi.txt §8: matching allowed only in OPEN and CONTINUOUS.

class TestIsValidSession:
    def test_open_allowed(self):
        from simulator import is_valid_session, SESSION_OPEN
        assert is_valid_session(SESSION_OPEN) is True

    def test_continuous_allowed(self):
        from simulator import is_valid_session, SESSION_CONTINUOUS
        assert is_valid_session(SESSION_CONTINUOUS) is True

    def test_other_states_rejected(self):
        from simulator import is_valid_session, SESSION_OTHER
        assert is_valid_session(SESSION_OTHER) is False
        # Belt-and-braces: any unknown enum value also rejected
        assert is_valid_session(99) is False
        assert is_valid_session(-1) is False


# ─────────────────────────────────────────────────────────────────────────────
# Rule: iceberg_available(display_qty, slice_consumed, remaining_qty) -> int
# ─────────────────────────────────────────────────────────────────────────────
# Per bi.txt §27: icebergs show only `display_qty`; when exhausted, the next
# slice appears at the back of the queue. display_qty == 0 marks a non-iceberg
# order (show everything). Caller still caps by remaining_qty.

class TestIcebergAvailable:
    def test_non_iceberg_returns_remaining(self):
        from simulator import iceberg_available
        assert iceberg_available(display_qty=0, slice_consumed=0, remaining_qty=500) == 500

    def test_iceberg_fresh_slice(self):
        from simulator import iceberg_available
        # 100-unit display slice, none consumed, 500 underlying → 100 visible
        assert iceberg_available(display_qty=100, slice_consumed=0, remaining_qty=500) == 100

    def test_iceberg_partial_slice(self):
        from simulator import iceberg_available
        # 100-unit slice, 30 consumed → 70 left visible
        assert iceberg_available(display_qty=100, slice_consumed=30, remaining_qty=500) == 70

    def test_iceberg_exhausted_slice_returns_zero(self):
        from simulator import iceberg_available
        assert iceberg_available(display_qty=100, slice_consumed=100, remaining_qty=500) == 0

    def test_iceberg_overshot_clamped_to_zero(self):
        from simulator import iceberg_available
        # Defensive: consumed > display can happen during refresh bookkeeping
        assert iceberg_available(display_qty=100, slice_consumed=150, remaining_qty=500) == 0

    def test_slice_capped_by_remaining(self):
        from simulator import iceberg_available
        # Display qty 100 but only 30 total left
        assert iceberg_available(display_qty=100, slice_consumed=0, remaining_qty=30) == 30


# ─────────────────────────────────────────────────────────────────────────────
# Rule: check_crossing(sweep_participant, contra_participant,
#                      sweep_crossingkey, contra_crossingkey) -> Decision
# ─────────────────────────────────────────────────────────────────────────────
# Per _legacy.py:793-802: same participant requires matching non-zero
# crossing keys. Participant 0 is "unknown" and is ignored.

class TestCheckCrossing:
    def test_different_participants_ok(self):
        from simulator import check_crossing, Decision
        assert check_crossing(10, 20, 0, 0) is Decision.OK
        assert check_crossing(10, 20, 5, 5) is Decision.OK

    def test_same_participant_with_matching_keys_ok(self):
        from simulator import check_crossing, Decision
        assert check_crossing(10, 10, 7, 7) is Decision.OK

    def test_same_participant_with_zero_key_skip(self):
        from simulator import check_crossing, Decision
        assert check_crossing(10, 10, 0, 0) is Decision.SKIP
        assert check_crossing(10, 10, 0, 5) is Decision.SKIP
        assert check_crossing(10, 10, 5, 0) is Decision.SKIP

    def test_same_participant_with_mismatched_keys_skip(self):
        from simulator import check_crossing, Decision
        assert check_crossing(10, 10, 5, 6) is Decision.SKIP

    def test_zero_participant_treated_as_unknown_ok(self):
        from simulator import check_crossing, Decision
        # Unknown participant on either side → no self-match guard fires
        assert check_crossing(0, 10, 0, 0) is Decision.OK
        assert check_crossing(10, 0, 0, 0) is Decision.OK


# ─────────────────────────────────────────────────────────────────────────────
# Rule: validate_price_limit(order_type, order_price, execution_price,
#                            sweep_side, matched_qty, first_fill_price) -> bool
# ─────────────────────────────────────────────────────────────────────────────
# Per _legacy.py:235-268. The contra (resting) order's limit must accept the
# proposed execution price. sweep_side tells us whether the contra is selling
# (sweep_side=1, buy) or buying (sweep_side=2, sell). Order types:
#   LIMIT=1   — exec price must not violate contra limit
#   MARKET=2  — no price limit
#   MTL=3     — Market-to-Limit; once partially filled, treat first fill as limit

class TestValidatePriceLimit:
    def test_limit_sell_contra_exec_at_limit_ok(self):
        from simulator import validate_price_limit, ORDERTYPE_LIMIT
        # Buy sweep vs sell contra with limit 1000. Exec at 1000 → OK.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1000, 1000, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is True

    def test_limit_sell_contra_exec_below_limit_blocked(self):
        from simulator import validate_price_limit, ORDERTYPE_LIMIT
        # Sell contra won't accept < 1000. Exec at 999 → blocked.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1000, 999, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is False

    def test_limit_buy_contra_exec_at_limit_ok(self):
        from simulator import validate_price_limit, ORDERTYPE_LIMIT
        # Sell sweep vs buy contra with limit 1010. Exec at 1010 → OK.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1010, 1010, sweep_side=2,
                                    matched_qty=0, first_fill_price=0) is True

    def test_limit_buy_contra_exec_above_limit_blocked(self):
        from simulator import validate_price_limit, ORDERTYPE_LIMIT
        # Buy contra won't pay > 1010. Exec at 1011 → blocked.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1010, 1011, sweep_side=2,
                                    matched_qty=0, first_fill_price=0) is False

    def test_market_order_no_price_limit(self):
        from simulator import validate_price_limit, ORDERTYPE_MARKET
        # Market order accepts any price.
        assert validate_price_limit(ORDERTYPE_MARKET, 0, 999, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is True
        assert validate_price_limit(ORDERTYPE_MARKET, 0, 1_000_000, sweep_side=2,
                                    matched_qty=0, first_fill_price=0) is True

    def test_mtl_first_fill_no_limit(self):
        from simulator import validate_price_limit, ORDERTYPE_MTL
        # MTL before any fill: any price acceptable (like market).
        assert validate_price_limit(ORDERTYPE_MTL, 0, 999, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is True

    def test_mtl_after_fill_uses_first_fill_price(self):
        from simulator import validate_price_limit, ORDERTYPE_MTL
        # MTL already filled at 1000 acting as sell contra. Next fill at 999 → blocked.
        assert validate_price_limit(ORDERTYPE_MTL, 0, 999, sweep_side=1,
                                    matched_qty=100, first_fill_price=1000) is False
        # Same setup, exec at 1000 → OK.
        assert validate_price_limit(ORDERTYPE_MTL, 0, 1000, sweep_side=1,
                                    matched_qty=100, first_fill_price=1000) is True


# ─────────────────────────────────────────────────────────────────────────────
# Rule: apply_midtick(midpoint, nbbo_bid, nbbo_offer, side, midtick_flag,
#                     tick_size) -> int
# ─────────────────────────────────────────────────────────────────────────────
# Per _legacy.py:155. Midtick improvement shifts the price half a tick toward
# the contra's side, bounded by NBBO. Only triggers when the midtick flag is
# MIDTICK_YES or MIDTICK_DARK_WITH_MIDTICK.

class TestApplyMidtick:
    def test_midtick_no_returns_midpoint_unchanged(self):
        from simulator import apply_midtick, MIDTICK_NO
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_NO, tick_size=10) == 1000

    def test_midtick_undefined_returns_midpoint_unchanged(self):
        from simulator import apply_midtick, MIDTICK_UNDEFINED
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_UNDEFINED, tick_size=10) == 1000

    def test_midtick_yes_buy_sweep_improves_lower(self):
        from simulator import apply_midtick, MIDTICK_YES
        # Buy sweep: improved price is LOWER by half a tick
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 995

    def test_midtick_yes_sell_sweep_improves_higher(self):
        from simulator import apply_midtick, MIDTICK_YES
        # Sell sweep: improved price is HIGHER by half a tick
        assert apply_midtick(1000, 995, 1005, side=2,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 1005

    def test_midtick_dark_with_midtick_also_triggers(self):
        from simulator import apply_midtick, MIDTICK_DARK_WITH_MIDTICK
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_DARK_WITH_MIDTICK, tick_size=10) == 995

    def test_midtick_buy_clamped_at_nbbo_bid(self):
        from simulator import apply_midtick, MIDTICK_YES
        # If the half-tick would push below the NBBO bid, clamp at bid
        assert apply_midtick(1000, 999, 1001, side=1,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 999

    def test_midtick_sell_clamped_at_nbbo_offer(self):
        from simulator import apply_midtick, MIDTICK_YES
        # Clamp at offer on the upper side
        assert apply_midtick(1000, 999, 1001, side=2,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 1001


# ─────────────────────────────────────────────────────────────────────────────
# Rule: check_maq(sweep_remaining, sweep_matched, sweep_maq, sweep_sfmq,
#                 contra_avail, contra_maq, contra_sfmq,
#                 potential_match_qty) -> Decision
# ─────────────────────────────────────────────────────────────────────────────
# The BREAK vs SKIP asymmetry is the CLAUDE.md-flagged regression hazard.
# Per _legacy.py:774-790:
#   sweep MAQ, SFMQ=1: potential < MAQ → SKIP
#   sweep MAQ, SFMQ=0: remaining<MAQ AND matched>0 → BREAK
#                      potential<MAQ AND matched==0 → SKIP
#   contra MAQ: always SKIP (never BREAK)

class TestCheckMAQ:
    def test_zero_maq_both_sides_ok(self):
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=500, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=500) is Decision.OK

    # ── Sweep MAQ, SFMQ=1 (single-fill) ──
    def test_sweep_sfmq_potential_below_maq_skip(self):
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=1,
                         contra_avail=50, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP

    def test_sweep_sfmq_potential_at_or_above_maq_ok(self):
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=1,
                         contra_avail=150, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=150) is Decision.OK

    # ── Sweep MAQ, SFMQ=0 (multi-fill) ──
    def test_sweep_maq_remaining_below_maq_after_partial_fill_BREAK(self):
        """The core regression hazard — partial fill + remaining<MAQ → BREAK."""
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=80, sweep_matched=200,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=500, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=80) is Decision.BREAK

    def test_sweep_maq_potential_below_maq_no_prior_fill_SKIP(self):
        """No prior fill + this contra can't satisfy MAQ → try another contra."""
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=50, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP

    def test_sweep_maq_potential_meets_maq_ok(self):
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=200, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=200) is Decision.OK

    # ── Contra MAQ ──
    def test_contra_sfmq_potential_below_contra_maq_skip(self):
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=300, contra_maq=500, contra_sfmq=1,
                         potential_match_qty=300) is Decision.SKIP

    def test_contra_maq_avail_below_contra_maq_skip(self):
        from simulator import check_maq, Decision
        # Contra has 50 left but requires 100 min — can't match
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=50, contra_maq=100, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP

    def test_contra_maq_avail_at_or_above_contra_maq_ok(self):
        from simulator import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=200, contra_maq=100, contra_sfmq=0,
                         potential_match_qty=200) is Decision.OK

    def test_sweep_ok_but_contra_fails_overall_skip(self):
        from simulator import check_maq, Decision
        # Sweep MAQ satisfied but contra MAQ fails → overall SKIP
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=50, contra_maq=100, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP


# ─────────────────────────────────────────────────────────────────────────────
# build_sim_context — pandas → SimContext translator
# ─────────────────────────────────────────────────────────────────────────────

import numpy as np
import pandas as pd
import pytest


def _flags_kwargs():
    return dict(
        nbbo_source='INTERNAL',
        simulate_resting_phase=False,
        simulate_lit_resting=False,
        resting_lit_book_mode='scan',
        resting_use_midtick=False,
        resting_lit_use_limit=True,
        resting_model_cancellation=True,
        resting_apply_crossing_keys=True,
        resting_apply_session_filter=True,
        resting_apply_maq=True,
        resting_apply_preferencing=True,
        resting_apply_iceberg=True,
        use_polars_transforms=False,
        use_duckdb_io=False,
        min_block_size=0,
    )


def _toy_sweep_orders():
    return pd.DataFrame({
        'orderid':                   [1001, 1002],
        'effective_timestamp':       [1_000_000_000, 2_000_000_000],
        'last_execution_time':       [1_500_000_000, 2_500_000_000],
        'side':                      [1, 2],
        'leavesquantity':            [500, 300],
        'price':                     [100, 200],
        'minimumquantity':           [0, 50],
        'singlefillminimumquantity': [0, 0],
        'crossingkey':               [0, 7],
        'participantid':             [10, 11],
        'midtick':                   [2, 1],
        'orderbookid':               [85603, 85603],
        'lost_priority':             [False, True],
        'changereason':              [6, 6],
    })


def _toy_all_orders():
    return pd.DataFrame({
        'orderid':                   [2001, 2002, 2003],
        'effective_timestamp':       [1_100_000_000, 1_200_000_000, 2_100_000_000],
        'sequence':                  [1, 2, 3],
        'side':                      [2, 2, 1],
        'quantity':                  [100, 200, 150],
        'price':                     [99, 101, 200],
        'minimumquantity':           [0, 0, 0],
        'singlefillminimumquantity': [0, 0, 0],
        'crossingkey':               [0, 0, 0],
        'participantid':             [20, 21, 22],
        'midtick':                   [2, 2, 2],
        'orderbookid':               [85603, 85603, 85603],
        'display_quantity':          [0, 0, 50],
        'exchangeordertype':         [1, 1, 1],
        'national_bid':              [99, 99, 200],
        'national_offer':            [101, 101, 201],
        'bid':                       [98, 98, 199],
        'offer':                     [102, 102, 202],
    })


class TestBuildSimContext:
    def test_returns_sim_context(self):
        from simulator import build_sim_context, SimContext, SimFlags
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={}, sim_flags=SimFlags(**_flags_kwargs()),
        )
        assert isinstance(ctx, SimContext)

    def test_sweep_arrays_have_correct_dtypes_and_lengths(self):
        from simulator import build_sim_context, SimFlags
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={}, sim_flags=SimFlags(**_flags_kwargs()),
        )
        assert ctx.sweep_orderid.dtype == np.int64
        assert ctx.sweep_side.dtype == np.int8
        assert ctx.sweep_qty.dtype == np.int64
        assert ctx.sweep_lost_priority.dtype == bool
        assert ctx.sweep_orderbookid.dtype == np.int32
        assert len(ctx.sweep_orderid) == 2
        assert len(ctx.sweep_qty) == 2

    def test_contra_arrays_have_correct_dtypes_and_lengths(self):
        from simulator import build_sim_context, SimFlags
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={}, sim_flags=SimFlags(**_flags_kwargs()),
        )
        assert ctx.contra_orderid.dtype == np.int64
        assert ctx.contra_side.dtype == np.int8
        assert ctx.contra_qty.dtype == np.int64
        assert ctx.contra_display_qty.dtype == np.int64
        assert len(ctx.contra_orderid) == 3
        assert len(ctx.contra_qty) == 3

    def test_sweep_values_round_trip(self):
        from simulator import build_sim_context, SimFlags
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={}, sim_flags=SimFlags(**_flags_kwargs()),
        )
        np.testing.assert_array_equal(ctx.sweep_orderid, [1001, 1002])
        np.testing.assert_array_equal(ctx.sweep_side, [1, 2])
        np.testing.assert_array_equal(ctx.sweep_qty, [500, 300])
        np.testing.assert_array_equal(ctx.sweep_first_exec, [1_000_000_000, 2_000_000_000])
        np.testing.assert_array_equal(ctx.sweep_last_exec,  [1_500_000_000, 2_500_000_000])

    def test_contra_values_round_trip(self):
        from simulator import build_sim_context, SimFlags
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={}, sim_flags=SimFlags(**_flags_kwargs()),
        )
        np.testing.assert_array_equal(ctx.contra_orderid, [2001, 2002, 2003])
        np.testing.assert_array_equal(ctx.contra_qty,     [100, 200, 150])
        np.testing.assert_array_equal(ctx.contra_display_qty, [0, 0, 50])
        np.testing.assert_array_equal(ctx.contra_sequence, [1, 2, 3])

    def test_missing_columns_get_default_zero(self):
        """Columns not present in the source DF should produce zero-filled arrays of correct length."""
        from simulator import build_sim_context, SimFlags
        sweep = _toy_sweep_orders().drop(columns=['minimumquantity'])
        ctx = build_sim_context(
            sweep, _toy_all_orders(),
            partition_data={}, sim_flags=SimFlags(**_flags_kwargs()),
        )
        np.testing.assert_array_equal(ctx.sweep_maq, [0, 0])

    def test_session_arrays_sorted_by_timestamp(self):
        from simulator import build_sim_context, SimFlags, SESSION_OPEN, SESSION_CONTINUOUS, SESSION_OTHER
        # Deliberately unsorted input
        session_df = pd.DataFrame({
            'timestamp':     [3_000, 1_000, 2_000],
            'session_state': ['CONTINUOUS', 'OPEN', 'PRE_OPEN'],
        })
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={'session': session_df},
            sim_flags=SimFlags(**_flags_kwargs()),
        )
        np.testing.assert_array_equal(ctx.session_ts, [1_000, 2_000, 3_000])
        np.testing.assert_array_equal(
            ctx.session_state, [SESSION_OPEN, SESSION_OTHER, SESSION_CONTINUOUS]
        )

    def test_nbbo_external_populates_arrays(self):
        from simulator import build_sim_context, SimFlags
        flags = _flags_kwargs(); flags['nbbo_source'] = 'EXTERNAL'
        nbbo_df = pd.DataFrame({
            'timestamp': [2_000, 1_000],
            'bid':       [199, 99],
            'offer':     [201, 101],
        })
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={'nbbo': nbbo_df},
            sim_flags=SimFlags(**flags),
        )
        np.testing.assert_array_equal(ctx.nbbo_ts,    [1_000, 2_000])
        np.testing.assert_array_equal(ctx.nbbo_bid,   [99, 199])
        np.testing.assert_array_equal(ctx.nbbo_offer, [101, 201])

    def test_nbbo_internal_leaves_nbbo_arrays_none(self):
        from simulator import build_sim_context, SimFlags
        ctx = build_sim_context(
            _toy_sweep_orders(), _toy_all_orders(),
            partition_data={'nbbo': pd.DataFrame({'timestamp': [1], 'bid': [1], 'offer': [1]})},
            sim_flags=SimFlags(**_flags_kwargs()),
        )
        assert ctx.nbbo_ts is None and ctx.nbbo_bid is None and ctx.nbbo_offer is None

    def test_real_partition_smoke(self):
        """Integration: build context from the on-disk CBA fixture if present."""
        from pathlib import Path
        import duckdb
        from simulator import build_sim_context, SimFlags
        partition = Path('data/processed/2024-09-05/85603')
        if not partition.exists():
            pytest.skip('CBA fixture not on disk; run process.py first')
        before = duckdb.sql(f"SELECT * FROM '{partition}/orders_before_matching.parquet'").df()
        # Stripped-down run — just verify it doesn't crash on real schema
        # (sweep_orders here is just a placeholder; effective_timestamp etc.
        #  may not be in orders_before_matching, so this is purely a column-coverage test.)
        sweep = before.head(5).copy()
        if 'effective_timestamp' not in sweep.columns:
            sweep['effective_timestamp'] = sweep['timestamp']
        if 'last_execution_time' not in sweep.columns:
            sweep['last_execution_time'] = sweep['timestamp']
        if 'lost_priority' not in sweep.columns:
            sweep['lost_priority'] = False
        if 'leavesquantity' not in sweep.columns:
            sweep['leavesquantity'] = sweep.get('quantity', 0)
        ctx = build_sim_context(sweep, before, {}, SimFlags(**_flags_kwargs()))
        assert len(ctx.sweep_orderid) == 5
        assert len(ctx.contra_orderid) == len(before)


# ─────────────────────────────────────────────────────────────────────────────
# run_phase1 — kernel skeleton (commit B). Emits zero matches; loop structure only.
# ─────────────────────────────────────────────────────────────────────────────


class TestRunPhase1Skeleton:
    def test_empty_inputs_produce_empty_outputs(self):
        from simulator import build_sim_context, run_phase1, SimFlags
        empty_sweep = pd.DataFrame({
            'orderid':[], 'effective_timestamp':[], 'last_execution_time':[],
            'side':[], 'leavesquantity':[], 'price':[], 'minimumquantity':[],
            'singlefillminimumquantity':[], 'crossingkey':[], 'participantid':[],
            'midtick':[], 'orderbookid':[], 'lost_priority':[], 'changereason':[],
        })
        empty_orders = pd.DataFrame({
            'orderid':[], 'effective_timestamp':[], 'sequence':[], 'side':[],
            'quantity':[], 'price':[], 'minimumquantity':[],
            'singlefillminimumquantity':[], 'crossingkey':[], 'participantid':[],
            'midtick':[], 'orderbookid':[], 'display_quantity':[],
            'exchangeordertype':[], 'national_bid':[], 'national_offer':[],
            'bid':[], 'offer':[],
        })
        ctx = build_sim_context(empty_sweep, empty_orders, {}, SimFlags(**_flags_kwargs()))
        trades, summaries = run_phase1(ctx)
        assert trades == [] and summaries == []

    def test_zero_qty_sweep_gets_zero_fill_summary(self):
        from simulator import build_sim_context, run_phase1, SimFlags
        sweep = _toy_sweep_orders().copy()
        sweep.loc[0, 'leavesquantity'] = 0       # first sweep has nothing to fill
        ctx = build_sim_context(sweep, _toy_all_orders(), {}, SimFlags(**_flags_kwargs()))
        trades, summaries = run_phase1(ctx)
        assert len(summaries) == 2
        assert summaries[0]['matched_quantity'] == 0
        assert summaries[0]['fill_ratio'] == 0
        assert summaries[0]['num_matches'] == 0
        assert summaries[0]['quantity'] == 0
        assert trades == []                        # skeleton — no matches

    def test_one_summary_per_sweep_skeleton_emits_zero_trades(self):
        from simulator import build_sim_context, run_phase1, SimFlags
        ctx = build_sim_context(_toy_sweep_orders(), _toy_all_orders(),
                                {}, SimFlags(**_flags_kwargs()))
        trades, summaries = run_phase1(ctx)
        assert len(summaries) == 2                 # one per sweep
        assert trades == []                        # skeleton — gauntlet always SKIPs
        for s in summaries:
            assert s['matched_quantity'] == 0
            assert s['num_matches'] == 0

    def test_summary_carries_through_lost_priority_and_changereason(self):
        from simulator import build_sim_context, run_phase1, SimFlags
        ctx = build_sim_context(_toy_sweep_orders(), _toy_all_orders(),
                                {}, SimFlags(**_flags_kwargs()))
        _, summaries = run_phase1(ctx)
        # Toy fixture: sweep[0] lost_priority=False, sweep[1] lost_priority=True
        assert summaries[0]['lost_priority'] is False
        assert summaries[1]['lost_priority'] is True

    def test_eligibility_window_excludes_self_match(self):
        """Sweep's own orderid in all_orders must not appear as a candidate."""
        from simulator import build_sim_context, run_phase1, SimFlags
        sweep = _toy_sweep_orders()
        # Inject the sweep's own id into all_orders — should be filtered out.
        ao = _toy_all_orders()
        ao.loc[len(ao)] = ao.iloc[0].copy()
        ao.iloc[-1, ao.columns.get_loc('orderid')] = 1001  # sweep[0]'s id
        ctx = build_sim_context(sweep, ao, {}, SimFlags(**_flags_kwargs()))
        # Skeleton emits no trades regardless; this just verifies no crash + correct summary count
        trades, summaries = run_phase1(ctx)
        assert len(summaries) == 2
        assert trades == []

    def test_session_state_filter_blocks_pre_open(self):
        """When session is PRE_OPEN at the contra's timestamp, kernel should still
        run cleanly (skeleton emits nothing anyway, but we want to ensure the gate is wired)."""
        from simulator import build_sim_context, run_phase1, SimFlags
        # Session that's PRE_OPEN at all timestamps in the test → no matches even
        # when commit C lands its match logic.
        session_df = pd.DataFrame({
            'timestamp':     [0],
            'session_state': ['PRE_OPEN'],
        })
        ctx = build_sim_context(_toy_sweep_orders(), _toy_all_orders(),
                                {'session': session_df},
                                SimFlags(**_flags_kwargs()))
        trades, summaries = run_phase1(ctx)
        assert trades == []
        assert len(summaries) == 2

    def test_real_partition_smoke(self):
        """Run skeleton against the real CBA fixture if present — expect no crash."""
        from pathlib import Path
        import duckdb
        from simulator import build_sim_context, run_phase1, SimFlags
        partition = Path('data/processed/2024-09-05/85603')
        if not partition.exists():
            pytest.skip('CBA fixture not on disk; run process.py first')
        all_orders = duckdb.sql(f"SELECT * FROM '{partition}/orders_before_matching.parquet'").df()
        # Synthesize a few sweeps to exercise the loop without running the full pipeline.
        sweep = all_orders.head(3).copy()
        sweep['effective_timestamp'] = sweep['timestamp']
        sweep['last_execution_time'] = sweep['timestamp'] + 10**9
        sweep['lost_priority'] = False
        sweep['leavesquantity'] = sweep.get('quantity', 0)
        ctx = build_sim_context(sweep, all_orders, {}, SimFlags(**_flags_kwargs()))
        trades, summaries = run_phase1(ctx)
        assert len(summaries) == 3
        assert all(s['matched_quantity'] == 0 for s in summaries)   # skeleton
