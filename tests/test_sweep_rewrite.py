"""Unit tests for src/pipeline/sweep_simulator/_rewrite.py.

Sprint 1 Foundations: Decision enum, SimFlags, SimContext, rule helpers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


# ─────────────────────────────────────────────────────────────────────────────
# Decision enum
# ─────────────────────────────────────────────────────────────────────────────

class TestDecision:
    def test_has_ok_skip_break_members(self):
        from pipeline.sweep_simulator._rewrite import Decision
        assert Decision.OK.name == 'OK'
        assert Decision.SKIP.name == 'SKIP'
        assert Decision.BREAK.name == 'BREAK'

    def test_ordering_ok_lt_skip_lt_break(self):
        from pipeline.sweep_simulator._rewrite import Decision
        assert Decision.OK < Decision.SKIP < Decision.BREAK

    def test_is_int_enum(self):
        from pipeline.sweep_simulator._rewrite import Decision
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
        from pipeline.sweep_simulator._rewrite import SimFlags
        f = SimFlags(**_flags_kwargs())
        assert f.nbbo_source == 'INTERNAL'
        assert f.min_block_size == 0

    def test_frozen_raises_on_mutation(self):
        import dataclasses
        from pipeline.sweep_simulator._rewrite import SimFlags
        f = SimFlags(**_flags_kwargs())
        try:
            f.nbbo_source = 'EXTERNAL'
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("SimFlags should be frozen")

    def test_slots_no_dict(self):
        from pipeline.sweep_simulator._rewrite import SimFlags
        f = SimFlags(**_flags_kwargs())
        assert not hasattr(f, '__dict__')

    def test_replace_roundtrip(self):
        import dataclasses
        from pipeline.sweep_simulator._rewrite import SimFlags
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
    from pipeline.sweep_simulator._rewrite import SimFlags
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
        from pipeline.sweep_simulator._rewrite import SimContext
        ctx = SimContext(**_context_kwargs())
        assert len(ctx.sweep_orderid) == 2
        assert len(ctx.contra_orderid) == 3
        assert ctx.tick_size == 1
        assert ctx.participants[10] == 'BROKER'

    def test_frozen_raises_on_mutation(self):
        import dataclasses
        from pipeline.sweep_simulator._rewrite import SimContext
        ctx = SimContext(**_context_kwargs())
        try:
            ctx.tick_size = 99
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("SimContext should be frozen")

    def test_slots_no_dict(self):
        from pipeline.sweep_simulator._rewrite import SimContext
        ctx = SimContext(**_context_kwargs())
        assert not hasattr(ctx, '__dict__')

    def test_replace_roundtrip(self):
        import dataclasses
        from pipeline.sweep_simulator._rewrite import SimContext
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
        from pipeline.sweep_simulator._rewrite import is_apb
        assert is_apb(contra_ordertype=4096, contra_midtick=5) is True

    def test_block_limit_with_apb_midtick_6(self):
        from pipeline.sweep_simulator._rewrite import is_apb
        assert is_apb(contra_ordertype=4096, contra_midtick=6) is True

    def test_block_limit_with_non_apb_midtick(self):
        from pipeline.sweep_simulator._rewrite import is_apb
        assert is_apb(contra_ordertype=4096, contra_midtick=0) is False
        assert is_apb(contra_ordertype=4096, contra_midtick=1) is False
        assert is_apb(contra_ordertype=4096, contra_midtick=2) is False

    def test_non_block_type_with_apb_midtick_still_false(self):
        from pipeline.sweep_simulator._rewrite import is_apb
        # Midtick 5 on a type-64 contra is NOT APB — the type must also match
        assert is_apb(contra_ordertype=64, contra_midtick=5) is False
        assert is_apb(contra_ordertype=2048, contra_midtick=5) is False


# ─────────────────────────────────────────────────────────────────────────────
# Rule: is_valid_session(state_enum: int) -> bool
# ─────────────────────────────────────────────────────────────────────────────
# Per bi.txt §8: matching allowed only in OPEN and CONTINUOUS.

class TestIsValidSession:
    def test_open_allowed(self):
        from pipeline.sweep_simulator._rewrite import is_valid_session, SESSION_OPEN
        assert is_valid_session(SESSION_OPEN) is True

    def test_continuous_allowed(self):
        from pipeline.sweep_simulator._rewrite import is_valid_session, SESSION_CONTINUOUS
        assert is_valid_session(SESSION_CONTINUOUS) is True

    def test_other_states_rejected(self):
        from pipeline.sweep_simulator._rewrite import is_valid_session, SESSION_OTHER
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
        from pipeline.sweep_simulator._rewrite import iceberg_available
        assert iceberg_available(display_qty=0, slice_consumed=0, remaining_qty=500) == 500

    def test_iceberg_fresh_slice(self):
        from pipeline.sweep_simulator._rewrite import iceberg_available
        # 100-unit display slice, none consumed, 500 underlying → 100 visible
        assert iceberg_available(display_qty=100, slice_consumed=0, remaining_qty=500) == 100

    def test_iceberg_partial_slice(self):
        from pipeline.sweep_simulator._rewrite import iceberg_available
        # 100-unit slice, 30 consumed → 70 left visible
        assert iceberg_available(display_qty=100, slice_consumed=30, remaining_qty=500) == 70

    def test_iceberg_exhausted_slice_returns_zero(self):
        from pipeline.sweep_simulator._rewrite import iceberg_available
        assert iceberg_available(display_qty=100, slice_consumed=100, remaining_qty=500) == 0

    def test_iceberg_overshot_clamped_to_zero(self):
        from pipeline.sweep_simulator._rewrite import iceberg_available
        # Defensive: consumed > display can happen during refresh bookkeeping
        assert iceberg_available(display_qty=100, slice_consumed=150, remaining_qty=500) == 0

    def test_slice_capped_by_remaining(self):
        from pipeline.sweep_simulator._rewrite import iceberg_available
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
        from pipeline.sweep_simulator._rewrite import check_crossing, Decision
        assert check_crossing(10, 20, 0, 0) is Decision.OK
        assert check_crossing(10, 20, 5, 5) is Decision.OK

    def test_same_participant_with_matching_keys_ok(self):
        from pipeline.sweep_simulator._rewrite import check_crossing, Decision
        assert check_crossing(10, 10, 7, 7) is Decision.OK

    def test_same_participant_with_zero_key_skip(self):
        from pipeline.sweep_simulator._rewrite import check_crossing, Decision
        assert check_crossing(10, 10, 0, 0) is Decision.SKIP
        assert check_crossing(10, 10, 0, 5) is Decision.SKIP
        assert check_crossing(10, 10, 5, 0) is Decision.SKIP

    def test_same_participant_with_mismatched_keys_skip(self):
        from pipeline.sweep_simulator._rewrite import check_crossing, Decision
        assert check_crossing(10, 10, 5, 6) is Decision.SKIP

    def test_zero_participant_treated_as_unknown_ok(self):
        from pipeline.sweep_simulator._rewrite import check_crossing, Decision
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
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_LIMIT
        # Buy sweep vs sell contra with limit 1000. Exec at 1000 → OK.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1000, 1000, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is True

    def test_limit_sell_contra_exec_below_limit_blocked(self):
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_LIMIT
        # Sell contra won't accept < 1000. Exec at 999 → blocked.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1000, 999, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is False

    def test_limit_buy_contra_exec_at_limit_ok(self):
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_LIMIT
        # Sell sweep vs buy contra with limit 1010. Exec at 1010 → OK.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1010, 1010, sweep_side=2,
                                    matched_qty=0, first_fill_price=0) is True

    def test_limit_buy_contra_exec_above_limit_blocked(self):
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_LIMIT
        # Buy contra won't pay > 1010. Exec at 1011 → blocked.
        assert validate_price_limit(ORDERTYPE_LIMIT, 1010, 1011, sweep_side=2,
                                    matched_qty=0, first_fill_price=0) is False

    def test_market_order_no_price_limit(self):
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_MARKET
        # Market order accepts any price.
        assert validate_price_limit(ORDERTYPE_MARKET, 0, 999, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is True
        assert validate_price_limit(ORDERTYPE_MARKET, 0, 1_000_000, sweep_side=2,
                                    matched_qty=0, first_fill_price=0) is True

    def test_mtl_first_fill_no_limit(self):
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_MTL
        # MTL before any fill: any price acceptable (like market).
        assert validate_price_limit(ORDERTYPE_MTL, 0, 999, sweep_side=1,
                                    matched_qty=0, first_fill_price=0) is True

    def test_mtl_after_fill_uses_first_fill_price(self):
        from pipeline.sweep_simulator._rewrite import validate_price_limit, ORDERTYPE_MTL
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
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_NO
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_NO, tick_size=10) == 1000

    def test_midtick_undefined_returns_midpoint_unchanged(self):
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_UNDEFINED
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_UNDEFINED, tick_size=10) == 1000

    def test_midtick_yes_buy_sweep_improves_lower(self):
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_YES
        # Buy sweep: improved price is LOWER by half a tick
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 995

    def test_midtick_yes_sell_sweep_improves_higher(self):
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_YES
        # Sell sweep: improved price is HIGHER by half a tick
        assert apply_midtick(1000, 995, 1005, side=2,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 1005

    def test_midtick_dark_with_midtick_also_triggers(self):
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_DARK_WITH_MIDTICK
        assert apply_midtick(1000, 995, 1005, side=1,
                             midtick_flag=MIDTICK_DARK_WITH_MIDTICK, tick_size=10) == 995

    def test_midtick_buy_clamped_at_nbbo_bid(self):
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_YES
        # If the half-tick would push below the NBBO bid, clamp at bid
        assert apply_midtick(1000, 999, 1001, side=1,
                             midtick_flag=MIDTICK_YES, tick_size=10) == 999

    def test_midtick_sell_clamped_at_nbbo_offer(self):
        from pipeline.sweep_simulator._rewrite import apply_midtick, MIDTICK_YES
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
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=500, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=500) is Decision.OK

    # ── Sweep MAQ, SFMQ=1 (single-fill) ──
    def test_sweep_sfmq_potential_below_maq_skip(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=1,
                         contra_avail=50, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP

    def test_sweep_sfmq_potential_at_or_above_maq_ok(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=1,
                         contra_avail=150, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=150) is Decision.OK

    # ── Sweep MAQ, SFMQ=0 (multi-fill) ──
    def test_sweep_maq_remaining_below_maq_after_partial_fill_BREAK(self):
        """The core regression hazard — partial fill + remaining<MAQ → BREAK."""
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=80, sweep_matched=200,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=500, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=80) is Decision.BREAK

    def test_sweep_maq_potential_below_maq_no_prior_fill_SKIP(self):
        """No prior fill + this contra can't satisfy MAQ → try another contra."""
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=50, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP

    def test_sweep_maq_potential_meets_maq_ok(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=200, contra_maq=0, contra_sfmq=0,
                         potential_match_qty=200) is Decision.OK

    # ── Contra MAQ ──
    def test_contra_sfmq_potential_below_contra_maq_skip(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=300, contra_maq=500, contra_sfmq=1,
                         potential_match_qty=300) is Decision.SKIP

    def test_contra_maq_avail_below_contra_maq_skip(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        # Contra has 50 left but requires 100 min — can't match
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=50, contra_maq=100, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP

    def test_contra_maq_avail_at_or_above_contra_maq_ok(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=0, sweep_sfmq=0,
                         contra_avail=200, contra_maq=100, contra_sfmq=0,
                         potential_match_qty=200) is Decision.OK

    def test_sweep_ok_but_contra_fails_overall_skip(self):
        from pipeline.sweep_simulator._rewrite import check_maq, Decision
        # Sweep MAQ satisfied but contra MAQ fails → overall SKIP
        assert check_maq(sweep_remaining=500, sweep_matched=0,
                         sweep_maq=100, sweep_sfmq=0,
                         contra_avail=50, contra_maq=100, contra_sfmq=0,
                         potential_match_qty=50) is Decision.SKIP
