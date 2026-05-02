"""Numpy simulator kernel for Centre Point sweep matching.

Sprint 1 foundations originally drafted on branch rewrite_wip_archive (commit
3f18fc6) and ported here on 2026-05-01 as the start of Tier 1 of the
perf roadmap. Rule helpers are framework-agnostic; the kernel built on top
will read pre-extracted flat numpy arrays via SimContext.

Layout:
  - Decision enum
  - SimFlags + SimContext dataclasses (boundary contract for the kernel)
  - 7 rule helpers — pure functions of int primitives:
      is_apb, is_valid_session, check_crossing,
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
    use_polars_transforms: bool
    use_duckdb_io: bool
    min_block_size: int                    # APB minimum traded value (0 = disabled)


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
    contra_ordertype:    np.ndarray   # int32 — values include 2048 (Sweep) and 4096 (Block Limit), so int8 overflows
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
    contra_ordertype    = _col_int32(all_orders, 'exchangeordertype')
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


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 3 — Kernel Phase 1 (full gauntlet + match emission)
# ─────────────────────────────────────────────────────────────────────────────
#
# Walks every sweep, builds its eligibility window via np.searchsorted, and
# applies the full match gauntlet (rule helpers + APB / NBBO / midtick paths)
# in priority order. Mutates inventory in place via flat int64 arrays.
# Iceberg refresh uses heapq-of-indices so contras can be repositioned at
# the back of the queue when their display slice exhausts (bi.txt §27).
#
# Deal-source codes (dd.txt §1144-1187):
#   1 = lit continuous, 46 = preference, 47 = Centre Point,
#   50 = Any Price Block, 51 = preference Any Price Block
DEALSOURCE_CONTINUOUS       = 1
DEALSOURCE_PREFERENCE       = 46
DEALSOURCE_CENTREPOINT      = 47
DEALSOURCE_BLOCK            = 50
DEALSOURCE_PREFERENCE_BLOCK = 51

_BASE_MATCHGROUPID = 7904794000999000001


def _deal_source_for(match_type: str) -> int:
    if match_type == 'BLOCK_PREF': return DEALSOURCE_PREFERENCE_BLOCK
    if match_type == 'BLOCK':      return DEALSOURCE_BLOCK
    return DEALSOURCE_CENTREPOINT


def _execution_delay_ns(rng: np.random.Generator, mean_ms: float = 5.0) -> int:
    """Log-normal execution delay (mirrors process.py:_add_execution_delay).

    Takes a Generator instance so the kernel is reproducible / parity-testable
    when the caller seeds it. Legacy uses np.random.lognormal — pass
    np.random.default_rng() with no seed to match its semantics.
    """
    delay_ms = rng.lognormal(mean=np.log(mean_ms), sigma=0.5)
    return int(delay_ms * 1_000_000)


def _internal_nbbo(ctx: SimContext, ci: int) -> tuple[int, int]:
    """INTERNAL NBBO source: per-order snapshot, sentinel falls back to bid/offer."""
    nbbo_bid   = int(ctx.contra_nbbo_bid[ci])
    nbbo_offer = int(ctx.contra_nbbo_offer[ci])
    if nbbo_bid == INT64_SENTINEL or nbbo_offer == INT64_SENTINEL:
        nbbo_bid   = int(ctx.contra_bid[ci])
        nbbo_offer = int(ctx.contra_offer[ci])
    return nbbo_bid, nbbo_offer


def _external_nbbo(ctx: SimContext, ts: int) -> tuple[int, int]:
    """EXTERNAL NBBO source: most-recent snapshot at or before ts."""
    if ctx.nbbo_ts is None or len(ctx.nbbo_ts) == 0:
        return 0, 0
    idx = int(np.searchsorted(ctx.nbbo_ts, ts, side='right')) - 1
    if idx < 0:
        return 0, 0
    return int(ctx.nbbo_bid[idx]), int(ctx.nbbo_offer[idx])


def _session_state_at(ts: int, session_ts: np.ndarray, session_state: np.ndarray) -> int:
    """Most-recent session state at or before `ts`. Returns SESSION_OTHER if no entry."""
    if len(session_ts) == 0:
        # No session info available → permissive (legacy behaviour treats this as OPEN)
        return SESSION_OPEN
    idx = int(np.searchsorted(session_ts, ts, side='right')) - 1
    if idx < 0:
        return SESSION_OTHER
    return int(session_state[idx])


def _sort_lexicographic(eff_ts: np.ndarray, sequence: np.ndarray) -> np.ndarray:
    """Return the permutation that puts arrays into (eff_ts, sequence) priority order.

    Mirrors pandas df.sort_values(['effective_timestamp', 'sequence']).
    """
    # np.lexsort sorts by the LAST key first → put eff_ts last for primary key.
    return np.lexsort([sequence, eff_ts])


def run_phase1(ctx: SimContext, *, rng: np.random.Generator = None,
               tradedate: str = None) -> tuple[list, list]:
    """Phase-1 dark continuous matching kernel.

    Walks every sweep, builds the eligibility window, runs the full match
    gauntlet (MAQ → crossing key → APB-or-NBBO branch → price validation),
    emits two trade rows per match (sweep side + contra side with shared
    matchgroupid), and mutates flat int64 inventory arrays in place.

    Args:
      ctx: prepared SimContext from build_sim_context()
      rng: numpy Generator for execution-delay log-normal draws.
           Defaults to np.random.default_rng() (matches legacy unseeded behaviour).
      tradedate: 'YYYY-MM-DD' string for trade rows. If None, derived from
           the first sweep's timestamp.

    Returns:
      simulated_trades: list[dict] — sweep + contra row per match
      sweep_summaries:  list[dict] — one row per sweep
    """
    if rng is None:
        rng = np.random.default_rng()
    flags = ctx.cfg_flags

    # ── Per-partition one-time prep ─────────────────────────────────────────
    contra_perm = _sort_lexicographic(ctx.contra_eff_ts, ctx.contra_sequence)
    c_eff_ts        = ctx.contra_eff_ts[contra_perm]
    c_seq           = ctx.contra_sequence[contra_perm]
    c_orderid       = ctx.contra_orderid[contra_perm]
    c_orderbookid   = ctx.contra_orderbookid[contra_perm]
    c_side          = ctx.contra_side[contra_perm]
    c_qty           = ctx.contra_qty[contra_perm]
    c_price         = ctx.contra_price[contra_perm]
    c_maq           = ctx.contra_maq[contra_perm]
    c_sfmq          = ctx.contra_sfmq[contra_perm]
    c_xkey          = ctx.contra_crossingkey[contra_perm]
    c_part          = ctx.contra_participant[contra_perm]
    c_midtick       = ctx.contra_midtick[contra_perm]
    c_ordertype     = ctx.contra_ordertype[contra_perm]
    c_nbbo_bid      = ctx.contra_nbbo_bid[contra_perm]
    c_nbbo_offer    = ctx.contra_nbbo_offer[contra_perm]
    c_bid           = ctx.contra_bid[contra_perm]
    c_offer         = ctx.contra_offer[contra_perm]

    # Build a permuted-context shim for the NBBO helpers, since they index by
    # the permuted position. Cheaper than allocating a new SimContext.
    class _PermutedNbboCtx:
        contra_nbbo_bid   = c_nbbo_bid
        contra_nbbo_offer = c_nbbo_offer
        contra_bid        = c_bid
        contra_offer      = c_offer
        nbbo_ts           = ctx.nbbo_ts
        nbbo_bid          = ctx.nbbo_bid
        nbbo_offer        = ctx.nbbo_offer
    pctx = _PermutedNbboCtx()

    # Inventory state — mutable across sweeps. One int64 per contra position.
    order_remaining = c_qty.copy()

    # tradedate derived from earliest sweep timestamp (matches legacy)
    if tradedate is None and len(ctx.sweep_eff_ts) > 0:
        from datetime import datetime, timezone
        first_ts_ns = int(ctx.sweep_eff_ts.min())
        tradedate = datetime.fromtimestamp(first_ts_ns / 1e9, tz=timezone.utc).strftime('%Y-%m-%d')

    simulated_trades: list = []
    sweep_summaries:  list = []

    row_counter = 1
    match_counter = 0

    n_sweeps = len(ctx.sweep_orderid)
    for s in range(n_sweeps):
        sweep_id        = int(ctx.sweep_orderid[s])
        sweep_side      = int(ctx.sweep_side[s])
        sweep_qty_avail = int(ctx.sweep_qty[s])
        sweep_obid      = int(ctx.sweep_orderbookid[s])
        first_exec_time = int(ctx.sweep_first_exec[s])
        last_exec_time  = int(ctx.sweep_last_exec[s])
        sweep_lost_pri  = bool(ctx.sweep_lost_priority[s])
        sweep_changeres = int(ctx.sweep_changereason[s])
        sweep_price     = int(ctx.sweep_price[s])
        sweep_maq       = int(ctx.sweep_maq[s])
        sweep_sfmq      = int(ctx.sweep_sfmq[s])
        sweep_xkey      = int(ctx.sweep_crossingkey[s])
        sweep_part      = int(ctx.sweep_participant[s])
        sweep_midtick   = int(ctx.sweep_midtick[s])

        if sweep_qty_avail <= 0:
            sweep_summaries.append({
                'orderid': sweep_id, 'timestamp': int(ctx.sweep_eff_ts[s]),
                'side': sweep_side, 'quantity': sweep_qty_avail,
                'matched_quantity': 0, 'remaining_quantity': 0,
                'fill_ratio': 0, 'num_matches': 0,
                'orderbookid': sweep_obid,
                'lost_priority': sweep_lost_pri,
                'changereason': sweep_changeres,
            })
            continue

        # Eligibility window via searchsorted (O(log N) per sweep)
        lo = int(np.searchsorted(c_eff_ts, first_exec_time, side='left'))
        hi = int(np.searchsorted(c_eff_ts, last_exec_time,  side='right'))
        idx_window = np.arange(lo, hi)
        mask = (
            (c_orderbookid[idx_window] == sweep_obid)
            & (c_side[idx_window] != sweep_side)
            & (c_orderid[idx_window] != sweep_id)
        )
        candidate_idx = idx_window[mask]

        sweep_remaining = sweep_qty_avail
        sweep_matched   = 0
        sweep_n_matches = 0
        first_fill_price = 0   # MTL anchor (set on first fill)

        # Walk candidates in (eff_ts, sequence) priority order. No iceberg
        # refresh — full contra quantity is always treated as visible.
        for cand_pos in range(len(candidate_idx)):
            if sweep_remaining <= 0:
                break
            ci = int(candidate_idx[cand_pos])

            order_avail = int(order_remaining[ci])
            if order_avail <= 0:
                continue

            potential = min(sweep_remaining, order_avail)

            # MAQ gauntlet — may BREAK
            d = check_maq(
                sweep_remaining, sweep_matched, sweep_maq, sweep_sfmq,
                order_avail, int(c_maq[ci]), int(c_sfmq[ci]), potential,
            )
            if d == Decision.BREAK:
                break
            if d == Decision.SKIP:
                continue

            # Crossing key
            if check_crossing(
                sweep_part, int(c_part[ci]), sweep_xkey, int(c_xkey[ci])
            ) == Decision.SKIP:
                continue

            # Match-time session-state recheck
            ts = int(c_eff_ts[ci])
            if not is_valid_session(
                _session_state_at(ts, ctx.session_ts, ctx.session_state)
            ):
                continue

            # APB vs non-APB branch
            ot = int(c_ordertype[ci]); mt = int(c_midtick[ci])
            if is_apb(ot, mt):
                contra_limit = int(c_price[ci])
                # Sweep limit must cross contra limit
                if sweep_side == 1 and sweep_price < contra_limit: continue
                if sweep_side == 2 and sweep_price > contra_limit: continue
                execution_price = float(contra_limit)
                # MIN_BLOCK_SIZE check (qty * price ≥ threshold)
                if flags.min_block_size > 0 and \
                   potential * execution_price < flags.min_block_size:
                    continue
                # APB same-participant preferencing (spec rule, always on)
                is_pref = (
                    sweep_part > 0 and int(c_part[ci]) > 0
                    and sweep_part == int(c_part[ci])
                )
                match_type = 'BLOCK_PREF' if is_pref else 'BLOCK'
                nbbo_bid_ev = nbbo_offer_ev = 0
            else:
                # NBBO at match time (per-source)
                if flags.nbbo_source == 'EXTERNAL':
                    nbbo_bid_ev, nbbo_offer_ev = _external_nbbo(pctx, ts)
                else:
                    nbbo_bid_ev, nbbo_offer_ev = _internal_nbbo(pctx, ci)
                if nbbo_bid_ev <= 0 or nbbo_offer_ev <= 0:
                    continue

                midpoint = (nbbo_bid_ev + nbbo_offer_ev) / 2

                # Price-limit envelope (lower/upper from reference data)
                if ctx.price_lower and midpoint < ctx.price_lower:  continue
                if ctx.price_upper and midpoint > ctx.price_upper:  continue

                # Order limit-price validation (LIMIT/MARKET/MTL)
                if not validate_price_limit(
                    ot, int(c_price[ci]), int(midpoint), sweep_side,
                    sweep_matched, first_fill_price,
                ):
                    continue

                # Mid-tick improvement (effective_midtick = max(sweep, contra))
                effective_midtick = max(sweep_midtick, mt)
                execution_price = apply_midtick(
                    int(midpoint), nbbo_bid_ev, nbbo_offer_ev,
                    sweep_side, effective_midtick,
                    ctx.tick_size if ctx.tick_size > 0 else 1,
                )

                match_type = 'SWEEP_TO_SWEEP' if ot == 2048 else 'SWEEP_TO_REGULAR'

            # Match accepted — emit + mutate
            match_qty = potential
            match_ts = ts + _execution_delay_ns(rng)
            matchgroupid = _BASE_MATCHGROUPID + match_counter
            match_counter += 1

            contra_part_id = int(c_part[ci])
            contra_part_type = ctx.participants.get(contra_part_id, 'UNKNOWN')
            dealsource = _deal_source_for(match_type)
            order_id = int(c_orderid[ci])
            order_side = int(c_side[ci])

            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_ts, 'securitycode': sweep_obid,
                'orderid': sweep_id, 'dealsource': dealsource, 'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid_ev,
                'nationalofferpricesnapshot': nbbo_offer_ev,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': sweep_side, 'participantid': 0,
                'passiveaggressive': 1, 'row_num': row_counter,
                'match_type': match_type,
                'contra_participant_type': contra_part_type,
            })
            row_counter += 1
            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_ts, 'securitycode': sweep_obid,
                'orderid': order_id, 'dealsource': dealsource, 'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid_ev,
                'nationalofferpricesnapshot': nbbo_offer_ev,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': order_side, 'participantid': 0,
                'passiveaggressive': 0, 'row_num': row_counter,
                'match_type': match_type,
                'contra_participant_type': contra_part_type,
            })
            row_counter += 1

            sweep_remaining -= match_qty
            order_remaining[ci] -= match_qty
            sweep_matched += match_qty
            sweep_n_matches += 1
            if first_fill_price == 0:
                first_fill_price = int(execution_price)

        sweep_summaries.append({
            'orderid': sweep_id, 'timestamp': int(ctx.sweep_eff_ts[s]),
            'side': sweep_side, 'quantity': sweep_qty_avail,
            'matched_quantity': sweep_matched,
            'remaining_quantity': sweep_remaining,
            'fill_ratio': sweep_matched / sweep_qty_avail if sweep_qty_avail else 0,
            'num_matches': sweep_n_matches,
            'orderbookid': sweep_obid,
            'lost_priority': sweep_lost_pri,
            'changereason': sweep_changeres,
        })

    return simulated_trades, sweep_summaries


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 5 — legacy-shape adapter for drop-in replacement
# ─────────────────────────────────────────────────────────────────────────────
#
# simulate_sweep_matching_numpy() mirrors the signature/return-shape of
# process.py:simulate_sweep_matching(), so process.py can pick either backend
# with a single conditional. Adapter responsibilities:
#   1. Build a SimContext from the pandas inputs
#   2. Run the kernel
#   3. Convert kernel outputs (list[dict], list[dict]) to the dict-of-DataFrames
#      shape the rest of the pipeline expects.

def simulate_sweep_matching_numpy(
    sweep_orders,          # pandas DataFrame, sorted by (effective_timestamp, sequence)
    all_orders,            # pandas DataFrame
    nbbo_data,             # pandas DataFrame or None
    nbbo_source: str = 'INTERNAL',
    tick_size_override: int = 0,
    tick_size_table=None,
    price_limits: dict = None,
    participants_dict: dict = None,
    session_states_df=None,
    *,
    rng: np.random.Generator = None,
):
    """Drop-in numpy-kernel replacement for process.py:simulate_sweep_matching.

    Returns the same dict shape:
      {'order_summary': DataFrame, 'sweep_utilization': DataFrame,
       'simulated_trades': DataFrame, 'sweep_usage': dict}
    """
    import pandas as pd

    flags = SimFlags(
        nbbo_source=nbbo_source,
        use_polars_transforms=False,
        use_duckdb_io=False,
        min_block_size=0,
    )

    partition_data = {'nbbo': nbbo_data, 'session': session_states_df}

    price_lower = int(price_limits.get('lower_limit', 0)) if price_limits else 0
    price_upper = int(price_limits.get('upper_limit', 0)) if price_limits else 0

    ctx = build_sim_context(
        sweep_orders, all_orders, partition_data, flags,
        tick_size=int(tick_size_override) if tick_size_override else 0,
        tick_size_table=tick_size_table,
        price_lower=price_lower,
        price_upper=price_upper,
        participants=participants_dict,
    )

    trades, summaries = run_phase1(ctx, rng=rng)

    # ── Adapt to legacy dict-of-DataFrames shape ─────────────────────────────
    sim_trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(columns=[
        'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
        'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
        'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
        'tradeprice', 'quantity', 'side', 'participantid',
        'passiveaggressive', 'row_num', 'match_type', 'contra_participant_type',
    ])
    if not sim_trades_df.empty:
        for c in ('EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
                  'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
                  'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
                  'participantid', 'passiveaggressive', 'row_num'):
            if c in sim_trades_df.columns:
                sim_trades_df[c] = sim_trades_df[c].astype('int64')

    order_summary_df = pd.DataFrame(summaries) if summaries else pd.DataFrame(columns=[
        'orderid', 'timestamp', 'side', 'quantity', 'matched_quantity',
        'remaining_quantity', 'fill_ratio', 'num_matches', 'orderbookid',
        'lost_priority', 'changereason',
    ])

    # sweep_usage: map orderid → {matched_quantity, num_matches}
    sweep_usage = {
        int(s['orderid']): {
            'matched_quantity': int(s['matched_quantity']),
            'num_matches':      int(s['num_matches']),
        }
        for s in summaries
    }

    return {
        'order_summary':    order_summary_df,
        'simulated_trades': sim_trades_df,
        'sweep_usage':      sweep_usage,
        # sweep_utilization is built downstream by _generate_sweep_utilization;
        # we leave a placeholder here to keep the shape consistent.
        'sweep_utilization': pd.DataFrame(),
    }
