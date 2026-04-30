"""Numpy simulator kernel for Centre Point sweep matching.

Sprint 1 foundations originally drafted on branch rewrite_wip_archive (commit
3f18fc6) and ported here on 2026-05-01 as the start of Tier 1 of the
perf roadmap. Rule helpers are framework-agnostic; the kernel built on top
will read pre-extracted flat numpy arrays via SimContext.

Layout:
  - Decision enum
  - SimFlags + SimContext dataclasses (boundary contract for the kernel)
  - 7 rule helpers — pure functions of int primitives:
      is_apb, is_valid_session, iceberg_available, check_crossing,
      validate_price_limit, apply_midtick, check_maq

Spec sources: docs/bi.txt and docs/dd.txt (ASX Centre Point behaviour spec).
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 1 — Foundations
# ─────────────────────────────────────────────────────────────────────────────


class Decision(IntEnum):
    """Outcome of a rule-gauntlet check in the matching kernel.

    OK    — the contra passes this rule; continue checking / match it.
    SKIP  — this contra fails; `continue` to the next contra.
    BREAK — stop scanning further contras for this sweep (MAQ partial-fill).
    """
    OK = 0
    SKIP = 1
    BREAK = 2


@dataclass(frozen=True, slots=True)
class SimFlags:
    """Frozen snapshot of every config flag the simulator reads.

    Captured at partition-entry time so the kernel never re-reads cfg mid-run.
    """
    nbbo_source: str                       # 'INTERNAL' | 'EXTERNAL'
    simulate_resting_phase: bool
    simulate_lit_resting: bool
    resting_lit_book_mode: str             # 'full' | 'scan'
    resting_use_midtick: bool
    resting_lit_use_limit: bool
    resting_model_cancellation: bool
    resting_apply_crossing_keys: bool
    resting_apply_session_filter: bool
    resting_apply_maq: bool
    resting_apply_preferencing: bool
    resting_apply_iceberg: bool
    use_polars_transforms: bool
    use_duckdb_io: bool
    min_block_size: int


@dataclass(frozen=True, slots=True)
class SimContext:
    """Boundary contract between the prep layer and the kernel.

    Every array is pre-sorted and typed by the prep layer; the kernel reads
    them by position only. See spec Section 2 for full rationale.
    """
    # ── Sweep arrays (length N_sweeps, chronologically sorted) ──────────────
    sweep_orderid:       np.ndarray   # int64
    sweep_eff_ts:        np.ndarray   # int64 — effective timestamp
    sweep_side:          np.ndarray   # int8  — 1 buy, 2 sell
    sweep_qty:           np.ndarray   # int64 — starting leaves quantity
    sweep_first_exec:    np.ndarray   # int64 — per-sweep window lower bound
    sweep_last_exec:     np.ndarray   # int64 — per-sweep window upper bound
    sweep_price:         np.ndarray   # int64 — limit price
    sweep_maq:           np.ndarray   # int64 — minimum quantity
    sweep_sfmq:          np.ndarray   # int8  — single-fill MAQ flag
    sweep_crossingkey:   np.ndarray   # int64
    sweep_participant:   np.ndarray   # int32
    sweep_midtick:       np.ndarray   # int8
    sweep_orderbookid:   np.ndarray   # int32
    sweep_lost_priority: np.ndarray   # bool
    sweep_changereason:  np.ndarray   # int8

    # ── Contra arrays (length N_contras, chronologically sorted) ────────────
    contra_orderid:      np.ndarray   # int64
    contra_eff_ts:       np.ndarray   # int64
    contra_sequence:     np.ndarray   # int64 — secondary priority key
    contra_side:         np.ndarray   # int8
    contra_qty:          np.ndarray   # int64
    contra_price:        np.ndarray   # int64
    contra_maq:          np.ndarray   # int64
    contra_sfmq:         np.ndarray   # int8
    contra_crossingkey:  np.ndarray   # int64
    contra_participant:  np.ndarray   # int32
    contra_midtick:      np.ndarray   # int8
    contra_orderbookid:  np.ndarray   # int32
    contra_display_qty:  np.ndarray   # int64 — 0 = non-iceberg
    contra_ordertype:    np.ndarray   # int8  — LIMIT=1, MARKET=2, MTL=3, PASSIVE=4
    contra_nbbo_bid:     np.ndarray   # int64 — per-order NBBO snapshot (INTERNAL)
    contra_nbbo_offer:   np.ndarray   # int64
    contra_bid:          np.ndarray   # int64 — fallback when NBBO sentinel
    contra_offer:        np.ndarray   # int64

    # ── Session state (pre-sorted by timestamp) ──────────────────────────────
    session_ts:          np.ndarray   # int64
    session_state:       np.ndarray   # int8 enum (OPEN=1, CONTINUOUS=2, others=0)

    # ── NBBO (only populated when cfg_flags.nbbo_source == 'EXTERNAL') ──────
    nbbo_ts:             Optional[np.ndarray]
    nbbo_bid:            Optional[np.ndarray]
    nbbo_offer:          Optional[np.ndarray]

    # ── Reference data ──────────────────────────────────────────────────────
    tick_size:           int
    tick_size_table:     Optional[np.ndarray]
    price_lower:         int
    price_upper:         int
    participants:        dict   # int participant_id → str type

    # ── Runtime config snapshot ─────────────────────────────────────────────
    cfg_flags:           SimFlags


# ─────────────────────────────────────────────────────────────────────────────
# Rule helpers — pure functions of primitives. Independently testable.
# ─────────────────────────────────────────────────────────────────────────────

# Order type constants (dd.txt p.924)
ORDERTYPE_LIMIT = 1
ORDERTYPE_MARKET = 2
ORDERTYPE_MTL = 3
ORDERTYPE_PASSIVE = 4

# Centre Point Block Limit type (used by APB)
ORDERTYPE_BLOCK_LIMIT = 4096

# Midtick field values (dd.txt p.1045)
MIDTICK_UNDEFINED = 0
MIDTICK_YES = 1
MIDTICK_NO = 2
MIDTICK_DARK_EXEC = 3
MIDTICK_DARK_WITH_MIDTICK = 4
MIDTICK_ANY_PRICE_BLOCK = 5
MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK = 6

# Session state enum (matches SimContext.session_state dtype)
SESSION_OTHER = 0
SESSION_OPEN = 1
SESSION_CONTINUOUS = 2


def is_apb(contra_ordertype: int, contra_midtick: int) -> bool:
    """Any Price Block match: Centre Point Block Limit contra with APB midtick."""
    return (
        contra_ordertype == ORDERTYPE_BLOCK_LIMIT
        and contra_midtick in (MIDTICK_ANY_PRICE_BLOCK,
                               MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK)
    )


def is_valid_session(state_enum: int) -> bool:
    """Matching allowed only in OPEN / CONTINUOUS (bi.txt §8)."""
    return state_enum == SESSION_OPEN or state_enum == SESSION_CONTINUOUS


def iceberg_available(display_qty: int, slice_consumed: int, remaining_qty: int) -> int:
    """Units the contra currently shows, capped by underlying remaining qty.

    `display_qty == 0` means non-iceberg — full remaining is visible.
    Returns max(0, ...) so overshoot during refresh bookkeeping is safe.
    """
    if display_qty <= 0:
        return max(0, remaining_qty)
    visible = max(0, display_qty - slice_consumed)
    return min(visible, remaining_qty)


def check_crossing(sweep_participant: int, contra_participant: int,
                   sweep_crossingkey: int, contra_crossingkey: int) -> 'Decision':
    """Self-match / crossing-key guard.

    Same participant with a matching non-zero crossing key → OK (allowed).
    Same participant with zero or mismatched keys → SKIP.
    Participant 0 means "unknown" — the guard doesn't fire.
    Different participants → OK.
    """
    if sweep_participant == 0 or contra_participant == 0:
        return Decision.OK
    if sweep_participant != contra_participant:
        return Decision.OK
    # Same non-zero participant — require matching non-zero crossing keys
    if sweep_crossingkey == 0 or contra_crossingkey == 0:
        return Decision.SKIP
    if sweep_crossingkey != contra_crossingkey:
        return Decision.SKIP
    return Decision.OK


def validate_price_limit(order_type: int, order_price: int, execution_price: int,
                         sweep_side: int, matched_qty: int,
                         first_fill_price: int) -> bool:
    """Contra's limit price (and MTL first-fill anchor) must accept execution_price.

    sweep_side = 1 → buy sweep → contra is a SELL; contra's limit is a minimum
    sweep_side = 2 → sell sweep → contra is a BUY; contra's limit is a maximum
    """
    if order_type == ORDERTYPE_MARKET:
        return True

    if order_type == ORDERTYPE_MTL:
        # Before the first fill, MTL behaves like market
        if matched_qty <= 0:
            return True
        # After first fill, first_fill_price acts as the limit
        if sweep_side == 1:
            return execution_price >= first_fill_price
        return execution_price <= first_fill_price

    # LIMIT (default)
    if sweep_side == 1:
        return execution_price >= order_price
    return execution_price <= order_price


def apply_midtick(midpoint: int, nbbo_bid: int, nbbo_offer: int, side: int,
                  midtick_flag: int, tick_size: int) -> int:
    """Shift the price half a tick toward the contra if the midtick flag says so.

    Triggers on MIDTICK_YES or MIDTICK_DARK_WITH_MIDTICK; all other flags
    return the midpoint unchanged. Result is bounded by the NBBO
    (never worse than the touch on the improving side).
    """
    if midtick_flag != MIDTICK_YES and midtick_flag != MIDTICK_DARK_WITH_MIDTICK:
        return midpoint

    half_tick = tick_size // 2
    if side == 1:                            # buy sweep — price improves downward
        return max(midpoint - half_tick, nbbo_bid)
    # sell sweep — price improves upward
    return min(midpoint + half_tick, nbbo_offer)


def check_maq(sweep_remaining: int, sweep_matched: int,
              sweep_maq: int, sweep_sfmq: int,
              contra_avail: int, contra_maq: int, contra_sfmq: int,
              potential_match_qty: int) -> 'Decision':
    """MAQ gauntlet. Returns OK / SKIP / BREAK per bi.txt § minimum-quantity.

    Sweep side:
      SFMQ=1 (single-fill): potential < MAQ → SKIP
      SFMQ=0 (multi-fill):  remaining < MAQ AND matched > 0 → BREAK
                            potential < MAQ AND matched == 0 → SKIP

    Contra side (never BREAK — one contra failing doesn't end the scan):
      SFMQ=1: potential < contra_maq → SKIP
      SFMQ=0: contra_avail < contra_maq → SKIP
    """
    # Sweep MAQ first — can BREAK out of the whole scan
    if sweep_maq > 0:
        if sweep_sfmq == 1:
            if potential_match_qty < sweep_maq:
                return Decision.SKIP
        else:
            if sweep_remaining < sweep_maq and sweep_matched > 0:
                return Decision.BREAK
            if potential_match_qty < sweep_maq and sweep_matched == 0:
                return Decision.SKIP

    # Contra MAQ — SKIP only
    if contra_maq > 0:
        if contra_sfmq == 1:
            if potential_match_qty < contra_maq:
                return Decision.SKIP
        else:
            if contra_avail < contra_maq:
                return Decision.SKIP

    return Decision.OK


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 2 — Prep layer: pandas DataFrames → SimContext (flat numpy arrays)
# ─────────────────────────────────────────────────────────────────────────────
#
# build_sim_context() is the boundary translator. It runs ONCE per partition
# (not per sweep) so its cost is amortised. The returned SimContext is the
# *only* state the kernel reads from — no DataFrame access in the inner loop.

# Session-state string → int8 enum used by the kernel.
SESSION_STATE_ENUM = {
    'OPEN':       SESSION_OPEN,
    'CONTINUOUS': SESSION_CONTINUOUS,
    # Everything else (PRE_OPEN, AUCTION, POST_CLOSE, CLOSED, PRE_CSPA, CSPA,
    # ADJUST, ADJUST_ON, PURGE_ORDERS, SYSTEM_MAINTENANCE) → SESSION_OTHER (0)
}

INT64_SENTINEL = -9223372036854775808
NULL_INT = 0


def _col_int64(df, col_name: str, default: int = NULL_INT) -> np.ndarray:
    """Pull a column as np.int64 with NaNs replaced by `default`."""
    if col_name not in df.columns:
        return np.full(len(df), default, dtype=np.int64)
    return df[col_name].fillna(default).astype(np.int64).to_numpy()


def _col_int8(df, col_name: str, default: int = 0) -> np.ndarray:
    if col_name not in df.columns:
        return np.full(len(df), default, dtype=np.int8)
    return df[col_name].fillna(default).astype(np.int8).to_numpy()


def _col_int32(df, col_name: str, default: int = 0) -> np.ndarray:
    if col_name not in df.columns:
        return np.full(len(df), default, dtype=np.int32)
    return df[col_name].fillna(default).astype(np.int32).to_numpy()


def _col_bool(df, col_name: str, default: bool = False) -> np.ndarray:
    if col_name not in df.columns:
        return np.full(len(df), default, dtype=bool)
    return df[col_name].fillna(default).astype(bool).to_numpy()


def _build_session_arrays(session_states_df) -> tuple[np.ndarray, np.ndarray]:
    """Sort session states by timestamp; encode state strings as int8 enum."""
    if session_states_df is None or len(session_states_df) == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int8)
    sorted_df = session_states_df.sort_values('timestamp').reset_index(drop=True)
    ts = sorted_df['timestamp'].to_numpy(dtype=np.int64)
    states = sorted_df['session_state'].map(
        lambda s: SESSION_STATE_ENUM.get(str(s).upper(), SESSION_OTHER)
    ).to_numpy(dtype=np.int8)
    return ts, states


def build_sim_context(
    sweep_orders,           # pandas DataFrame, sorted by (effective_timestamp, sequence)
    all_orders,             # pandas DataFrame of contra-eligible orders
    partition_data: dict,   # {'nbbo': df_or_None, 'session': df_or_None, ...}
    sim_flags: SimFlags,
    *,
    tick_size: int = 0,
    tick_size_table=None,
    price_lower: int = 0,
    price_upper: int = 0,
    participants: dict = None,
) -> SimContext:
    """Translate the partition's pandas state into a flat numpy SimContext.

    Costs O(N_orders + N_sweeps); runs once per partition. The kernel can then
    iterate sweeps without ever touching pandas.

    Sweep frame requirements: must contain `effective_timestamp`, `last_execution_time`,
    `lost_priority`, `changereason` (already present after _prepare_sweep_orders).
    All-orders frame requirements: must contain `effective_timestamp` and `sequence`.
    """
    nbbo_df = partition_data.get('nbbo') if partition_data else None
    session_df = partition_data.get('session') if partition_data else None

    # ── Sweep arrays ─────────────────────────────────────────────────────────
    sweep_orderid       = _col_int64(sweep_orders, 'orderid')
    sweep_eff_ts        = _col_int64(sweep_orders, 'effective_timestamp')
    sweep_side          = _col_int8 (sweep_orders, 'side')
    sweep_qty           = _col_int64(sweep_orders, 'leavesquantity')
    sweep_first_exec    = _col_int64(sweep_orders, 'effective_timestamp')
    sweep_last_exec     = _col_int64(sweep_orders, 'last_execution_time')
    sweep_price         = _col_int64(sweep_orders, 'price')
    sweep_maq           = _col_int64(sweep_orders, 'minimumquantity')
    sweep_sfmq          = _col_int8 (sweep_orders, 'singlefillminimumquantity')
    sweep_crossingkey   = _col_int64(sweep_orders, 'crossingkey')
    sweep_participant   = _col_int32(sweep_orders, 'participantid')
    sweep_midtick       = _col_int8 (sweep_orders, 'midtick', default=MIDTICK_NO)
    sweep_orderbookid   = _col_int32(sweep_orders, 'orderbookid')
    sweep_lost_priority = _col_bool (sweep_orders, 'lost_priority')
    sweep_changereason  = _col_int8 (sweep_orders, 'changereason')

    # ── Contra arrays ────────────────────────────────────────────────────────
    contra_orderid      = _col_int64(all_orders, 'orderid')
    contra_eff_ts       = _col_int64(all_orders, 'effective_timestamp')
    contra_sequence     = _col_int64(all_orders, 'sequence')
    contra_side         = _col_int8 (all_orders, 'side')
    contra_qty          = _col_int64(all_orders, 'quantity')
    contra_price        = _col_int64(all_orders, 'price')
    contra_maq          = _col_int64(all_orders, 'minimumquantity')
    contra_sfmq         = _col_int8 (all_orders, 'singlefillminimumquantity')
    contra_crossingkey  = _col_int64(all_orders, 'crossingkey')
    contra_participant  = _col_int32(all_orders, 'participantid')
    contra_midtick      = _col_int8 (all_orders, 'midtick', default=MIDTICK_NO)
    contra_orderbookid  = _col_int32(all_orders, 'orderbookid')
    contra_display_qty  = _col_int64(all_orders, 'display_quantity', default=0)
    contra_ordertype    = _col_int8 (all_orders, 'exchangeordertype')
    contra_nbbo_bid     = _col_int64(all_orders, 'national_bid', default=INT64_SENTINEL)
    contra_nbbo_offer   = _col_int64(all_orders, 'national_offer', default=INT64_SENTINEL)
    contra_bid          = _col_int64(all_orders, 'bid')
    contra_offer        = _col_int64(all_orders, 'offer')

    # ── Session ──────────────────────────────────────────────────────────────
    session_ts, session_state = _build_session_arrays(session_df)

    # ── NBBO (only when EXTERNAL) ────────────────────────────────────────────
    if sim_flags.nbbo_source == 'EXTERNAL' and nbbo_df is not None and len(nbbo_df) > 0:
        nbbo_sorted = nbbo_df.sort_values('timestamp').reset_index(drop=True)
        nbbo_ts    = nbbo_sorted['timestamp'].to_numpy(dtype=np.int64)
        nbbo_bid   = nbbo_sorted['bid'].to_numpy(dtype=np.int64)
        nbbo_offer = nbbo_sorted['offer'].to_numpy(dtype=np.int64)
    else:
        nbbo_ts = nbbo_bid = nbbo_offer = None

    return SimContext(
        sweep_orderid=sweep_orderid,
        sweep_eff_ts=sweep_eff_ts,
        sweep_side=sweep_side,
        sweep_qty=sweep_qty,
        sweep_first_exec=sweep_first_exec,
        sweep_last_exec=sweep_last_exec,
        sweep_price=sweep_price,
        sweep_maq=sweep_maq,
        sweep_sfmq=sweep_sfmq,
        sweep_crossingkey=sweep_crossingkey,
        sweep_participant=sweep_participant,
        sweep_midtick=sweep_midtick,
        sweep_orderbookid=sweep_orderbookid,
        sweep_lost_priority=sweep_lost_priority,
        sweep_changereason=sweep_changereason,
        contra_orderid=contra_orderid,
        contra_eff_ts=contra_eff_ts,
        contra_sequence=contra_sequence,
        contra_side=contra_side,
        contra_qty=contra_qty,
        contra_price=contra_price,
        contra_maq=contra_maq,
        contra_sfmq=contra_sfmq,
        contra_crossingkey=contra_crossingkey,
        contra_participant=contra_participant,
        contra_midtick=contra_midtick,
        contra_orderbookid=contra_orderbookid,
        contra_display_qty=contra_display_qty,
        contra_ordertype=contra_ordertype,
        contra_nbbo_bid=contra_nbbo_bid,
        contra_nbbo_offer=contra_nbbo_offer,
        contra_bid=contra_bid,
        contra_offer=contra_offer,
        session_ts=session_ts,
        session_state=session_state,
        nbbo_ts=nbbo_ts,
        nbbo_bid=nbbo_bid,
        nbbo_offer=nbbo_offer,
        tick_size=int(tick_size),
        tick_size_table=tick_size_table,
        price_lower=int(price_lower),
        price_upper=int(price_upper),
        participants=participants if participants is not None else {},
        cfg_flags=sim_flags,
    )
