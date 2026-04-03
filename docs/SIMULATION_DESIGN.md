# Sweep Order Simulation — Requirements & Design Document

**Project:** SweepOrders — ASX Centre Point Execution Quality Research  
**Module:** `pipeline/sweep_simulator.py`  
**Date:** 2026-04-01  
**Status:** Implemented

---

## Table of Contents

1. [Background & Purpose](#1-background--purpose)
2. [Market Structure Reference](#2-market-structure-reference)
3. [Simulation Scenarios](#3-simulation-scenarios)
4. [Data Inputs](#4-data-inputs)
5. [Phase 1 — Aggressive Dark Sweep (S1)](#5-phase-1--aggressive-dark-sweep-s1)
6. [Phase 2 — Passive Resting Simulation (S2 / S3)](#6-phase-2--passive-resting-simulation-s2--s3)
7. [Matching Rules Shared by Both Phases](#7-matching-rules-shared-by-both-phases)
8. [Configuration Flags](#8-configuration-flags)
9. [Output Schema](#9-output-schema)
10. [Key Design Decisions & Trade-offs](#10-key-design-decisions--trade-offs)
11. [Testing Requirements](#11-testing-requirements)

---

## 1. Background & Purpose

Centre Point (CP) is the ASX dark pool. A **sweep order** (exchange order type `2048`) is an aggressive order that immediately crosses the CP book at the midpoint price. If midtick=1 (ON), the order is additionally **dual-posted** in ASX TradeMatch (lit market) as a passive limit order at the same time — this is the regime defined in ASX Business Information §25.2.

**Observation from data:** All sweep orders in the dataset carry `midtick=2` (OFF). Therefore no order in the dataset was ever dual-posted. This simulation is a **fully counterfactual study**: it asks "what would have happened if these orders had been allowed to rest, either in CP alone (S2) or in both CP and TradeMatch (S3)?"

The pipeline has two simulation phases:

| Phase | Purpose | Scenario |
|-------|---------|----------|
| Phase 1 | Replay aggressive CP sweep matching as it actually occurred | S1 (baseline) |
| Phase 2 | Add passive resting of unfilled remainder in dark and/or lit venues | S2 (dark only) or S3 (dual venue) |

---

## 2. Market Structure Reference

### 2.1 ASX Centre Point (Dark Pool)

- Matching at the **midpoint** of the National Best Bid/Offer (NBBO).
- **Time priority** (§24.5): orders rest in strict arrival order.
- **Preferencing** (§24.10): an incoming order may be routed to a same-participant resting order before others in the queue, subject to price compatibility.
- **MAQ** (§25.3): Minimum Acceptable Quantity applies only in Centre Point, not in TradeMatch.
- **Crossing keys**: same-participant orders can only cross if both carry a non-zero, matching crossing key.
- **Cancellation**: CP orders are purged at end of session (`change_reason_c = 24`, `timevaliditydecoded = "Rest of Day"`).
- Eligible order types for matching: `{64, 256, 2048, 4096, 4098}`.

### 2.2 ASX TradeMatch (Lit Market)

- Continuous matching with **price-time priority**.
- No MAQ; midtick flag ignored (§25.2 pt 7).
- Resting limit price = the order's limit price (no half-tick adjustment).
- Eligible lit order types: `0` (regular limit) and `2` (market).

### 2.3 Sweep Order Dual-Posting (§25.2)

When `midtick = 1` (YES):
1. The sweep aggressively crosses CP at midpoint (Phase 1 — as normal).
2. Any unfilled remainder is passively posted in **both** CP and TradeMatch simultaneously.
3. The CP resting price is half a tick inside the limit (better price = easier to match).
4. The lit resting price equals the raw limit (midtick flag ignored in TradeMatch).
5. The order lives in both venues until it fills, is cancelled, or the session ends.

### 2.4 NBBO Sources

| Mode | Source | Description |
|------|--------|-------------|
| `INTERNAL` | `nationalbid` / `nationaloffer` fields on each order row | Snapshot carried in the order message |
| `EXTERNAL` | `nbbo.csv.gz` file | Separate timestamped NBBO feed |

---

## 3. Simulation Scenarios

```
S1  Aggressive dark only (baseline — what actually happened)
S2  S1 + passive resting in Centre Point
S3  S1 + passive resting in BOTH Centre Point AND ASX TradeMatch
```

The scenario is controlled by two master flags:

```
SIMULATE_RESTING_PHASE = False  →  S1 only
SIMULATE_RESTING_PHASE = True,  SIMULATE_LIT_RESTING = False  →  S2
SIMULATE_RESTING_PHASE = True,  SIMULATE_LIT_RESTING = True   →  S3
```

---

## 4. Data Inputs

### 4.1 Partition Key

All processing is partitioned by `{date}/{orderbookid}` (e.g. `20240905/100`).

### 4.2 Input DataFrames

| DataFrame | Source File | Contents |
|-----------|-------------|----------|
| `orders_before` | `orders_before_matching.csv` | Snapshot of all CP orders active at sweep entry time |
| `orders_after` | `orders_after_matching.csv` | Orders including `leavesquantity` after matching |
| `last_execution` | `last_execution_time.csv` | Per-sweep: first and last execution timestamps |
| `nbbo` | `nbbo.csv.gz` or order fields | NBBO bid/offer feed |
| `session_states` | `session.csv` | Session state changes with timestamps |
| `lit_orders_raw` | Full raw orders file | All order types including lit [0, 2] — Phase 2 S3 only |

### 4.3 Reference Data

| Data | Source | Use |
|------|--------|-----|
| Tick size table | `{date}_ob.csv` | Price-dependent tick size lookup |
| Price limits | `{date}_ob.csv` | Reject matches outside limits |
| Participants dict | `{date}_par.csv` | Participant type labelling |

---

## 5. Phase 1 — Aggressive Dark Sweep (S1)

### 5.1 Order Preparation

#### Sweep Orders (`_prepare_sweep_orders`)

1. Take `last_execution` (one row per sweep order with timing metadata).
2. Inner-join with `orders_after` filtered to `exchangeordertype == 2048`.
3. Required columns: `orderid`, `timestamp`, `sequence`, `side`, `leavesquantity`, `matched_quantity`, `price`, `first_execution_time`, `last_execution_time`, `orderbookid`, `minimumquantity`, `singlefillminimumquantity`, `crossingkey`, `participantid`, `midtick`.
4. Compute `effective_timestamp`:
   - If `orderbookposition > 0` or `changereason ∈ {7, 8, 39}` → use `timechanged` (priority lost due to amendment).
   - Otherwise → use `timestamp`.
5. Sort by `(effective_timestamp, sequence)`.

#### Contra Orders (`_prepare_all_orders_for_matching`)

1. Take `orders_before`, filter to eligible CP types `{64, 256, 2048, 4096, 4098}`.
2. Filter to active orders: `orderstatus == 1`.
3. Filter out passive-only orders: `ordertype != 4`.
4. Compute `effective_timestamp` using same amendment logic as sweeps.
5. Sort by `(effective_timestamp, sequence)`.

### 5.2 Main Matching Loop

For each sweep order (in `effective_timestamp` order):

```
sweep_window = [first_exec_time .. last_exec_time]

eligible_contras = all_orders WHERE:
    effective_timestamp IN sweep_window
    AND orderbookid == sweep.orderbookid
    AND side != sweep.side
    AND orderid != sweep.orderid

IF session_filter_enabled:
    eligible_contras = filter to OPEN/CONTINUOUS sessions

Sort eligible_contras by (effective_timestamp, sequence)  ← time priority

FOR each contra in eligible_contras:
    IF sweep.remaining_qty <= 0: BREAK

    contra_avail = order_remaining[contra.orderid]
    IF iceberg: contra_avail = min(contra_avail, display_quantity)
    IF contra_avail <= 0: SKIP

    # MAQ checks (sweep side)
    IF sweep.maq > 0:
        IF single_fill_maq: SKIP if potential < sweep.maq
        ELSE: BREAK if remaining < sweep.maq and already_filled
              SKIP  if potential < sweep.maq and not_yet_filled

    # MAQ checks (contra side)
    IF contra.maq > 0:
        IF single_fill_maq: SKIP if potential < contra.maq
        ELSE: SKIP if contra_avail < contra.maq

    # Crossing prevention
    IF same participant:
        IF either crossing_key == 0: SKIP
        IF crossing_keys differ: SKIP

    match_qty = min(sweep.remaining, contra_avail)
    match_ts  = contra.effective_timestamp

    # Session check at match time
    IF NOT valid_session(match_ts): SKIP

    # NBBO at match time
    IF NBBO_SOURCE == 'EXTERNAL': binary-search nbbo array at match_ts
    ELSE: use contra.nationalbid / contra.nationaloffer

    IF nbbo_bid == 0 OR nbbo_offer == 0: SKIP

    midpoint = (nbbo_bid + nbbo_offer) / 2

    IF price_limits set AND midpoint outside limits: SKIP
    IF contra.ordertype == LIMIT AND contra.price violates midpoint: SKIP

    # Execution price
    effective_midtick = max(sweep.midtick, contra.midtick)
    IF effective_midtick ∈ {MIDTICK_YES, MIDTICK_DARK_WITH_MIDTICK}:
        execution_price = midpoint ± 0.5*tick  (half-tick improvement for buyer)
    ELSE:
        execution_price = midpoint

    # Execution delay
    match_ts += lognormal(μ=5ms, σ=2ms)

    # Emit two trade rows (sweep leg + contra leg)
    match_type = 'SWEEP_TO_SWEEP' if contra.type==2048 else 'SWEEP_TO_REGULAR'

    UPDATE: sweep.remaining_qty, order_remaining[contra_id], sweep_usage[sweep_id]
```

### 5.3 Phase 1 Output

| Output | Description |
|--------|-------------|
| `simulated_trades` | DataFrame — two rows per match (sweep leg + contra leg) |
| `order_summary` | Per-sweep: matched_qty, remaining_qty, fill_ratio, num_matches |
| `sweep_utilization` | Per-sweep: utilization_ratio, num_matches |
| `sweep_usage` | Dict `{orderid: {matched_quantity, num_matches}}` — fed into Phase 2 |

---

## 6. Phase 2 — Passive Resting Simulation (S2 / S3)

Phase 2 simulates what would happen if each sweep order's unfilled remainder passively rested in CP (S2) and/or TradeMatch (S3) after the aggressive Phase 1 concluded.

### 6.1 Building the Remainder (`build_remainder_df`)

For each sweep order where `leavesquantity - phase1_filled > 0`:

```
remaining_qty   = leavesquantity - phase1_filled
rest_entry_time = last_execution_time  ← when aggressive phase ended
limit_price     = sweep.price
side            = sweep.side
```

Orders fully filled in Phase 1 are excluded.

### 6.2 Resting Price Calculation

#### Dark CP Resting Price (`_calc_resting_price`)

Per §25.2 pt 6, passive CP resting uses a half-tick inside the limit to determine whether the midpoint is eligible:

```
tick = tick_size(limit_price)

IF side == BUY:   dark_resting_price = limit_price + 0.5 * tick
IF side == SELL:  dark_resting_price = limit_price - 0.5 * tick
```

- BUY +0.5*tick: midpoint must be at or below this threshold for a buy to execute.
- SELL −0.5*tick: midpoint must be at or above this threshold for a sell to execute.
- If `RESTING_USE_MIDTICK = False` (non-spec): use `limit_price` directly.

#### Lit TradeMatch Resting Price (`_calc_lit_resting_price`)

Per §25.2 pt 7, the midtick flag is **ignored** in TradeMatch:

```
IF RESTING_LIT_USE_LIMIT = True (default):  lit_resting_price = limit_price
IF RESTING_LIT_USE_LIMIT = False (exp.):    lit_resting_price = limit ± 0.5*tick
```

### 6.3 Order Expiry (Cancellation)

Per exchange rules, CP resting orders are purged at session end:

```
IF RESTING_MODEL_CANCELLATION = True:
    expiry_time = first non-OPEN/CONTINUOUS session change after rest_entry_time
ELSE:
    expiry_time = ∞  (no cancellation)
```

### 6.4 Sweep State Dict

Before the dark leg loop, all remainder rows are loaded into a per-sweep state dict:

```python
sweep_state[sweep_id] = {
    rest_entry_time,     # when resting begins
    expiry_time,         # when order expires
    remaining_qty,       # decremented by dark then lit fills
    limit_price,
    side,
    orderbookid,
    participantid,
    crossingkey,
    minimumquantity,
    singlefillminimumquantity,
    dark_resting_price,
    lit_resting_price,
    dark_filled,         # cumulative dark fills
    dark_matches,
    lit_filled,          # cumulative lit fills
    lit_matches,
}
```

This dict persists across both the dark and lit legs, so lit fills correctly see the quantity already consumed by dark.

### 6.5 Dark Leg — Centre Point Resting

The loop is **contra-centric** (outer = incoming contras, inner = resting sweeps). This correctly implements §24.10 preferencing: an incoming order routes to same-participant resting orders first, then FIFO.

```
Build all_cp_contras = union of CP orders on contra side for all (orderbookid, side) pairs
Sort all_cp_contras by (effective_timestamp, sequence)

FOR each contra in all_cp_contras (time order):

    contra_avail = cp_order_remaining[contra_id]
    IF iceberg: contra_avail = min(contra_avail, display_quantity)
    IF contra_avail <= 0: SKIP
    IF session_filter AND NOT valid_session(contra_ts): SKIP

    Determine NBBO at contra_ts (EXTERNAL or INTERNAL)
    IF nbbo_bid == 0 OR nbbo_offer == 0: SKIP
    midpoint = (nbbo_bid + nbbo_offer) / 2

    eligible_sweeps = [sweeps where:
        orderbookid == contra.orderbookid
        AND side != contra.side
        AND rest_entry_time < contra_ts < expiry_time
        AND remaining_qty > 0
    ]

    # §24.10 Preferencing
    IF RESTING_APPLY_PREFERENCING AND contra.participantid > 0:
        preferred = same-participant sweeps sorted by rest_entry_time
        others    = other-participant sweeps sorted by rest_entry_time
        sorted_sweeps = preferred + others
    ELSE:
        sorted_sweeps = sorted by rest_entry_time  (FIFO)

    FOR each sweep in sorted_sweeps:
        IF contra_avail <= 0: BREAK

        # Midpoint check against dark resting price
        IF sweep.side == BUY:  IF midpoint > dark_resting_price: SKIP
        IF sweep.side == SELL: IF midpoint < dark_resting_price: SKIP

        # Crossing key validation
        IF RESTING_APPLY_CROSSING_KEYS AND same_participant:
            IF either key == 0 OR keys differ: SKIP

        potential_qty = min(sweep.remaining_qty, contra_avail)

        # MAQ
        IF RESTING_APPLY_MAQ AND sweep.maq > 0:
            IF single_fill: SKIP if potential < maq
            ELSE: SKIP/BREAK per standard MAQ rules

        match_qty = potential_qty
        execution_price = midtick_improvement(midpoint, ...)
        match_ts = contra_ts + lognormal_delay()

        Emit two trade rows (venue='dark', phase=2)

        UPDATE: sweep_state[sweep_id].remaining_qty -= match_qty
                sweep_state[sweep_id].dark_filled   += match_qty
                cp_order_remaining[contra_id]        -= match_qty
                contra_avail                         -= match_qty
```

### 6.6 Lit Leg — ASX TradeMatch Resting (S3 only)

The lit leg runs **after** the dark leg, per sweep. It reads `remaining_qty` from `sweep_state` (which already reflects dark fills), so a sweep that was fully filled in dark gets zero lit fills automatically.

Two modes are available:

#### Option A — Full Lit Order Book (`RESTING_LIT_BOOK_MODE = 'full'`)

Build a price-time priority order book from all raw orders of type `{0, 2}`:

```
buy_side  sorted by: price DESC, effective_timestamp ASC, sequence ASC
sell_side sorted by: price ASC,  effective_timestamp ASC, sequence ASC
```

For a resting BUY sweep, walk the **sell side** (contra side):

```
FOR each entry in contra_side_book (price-time order):
    IF remaining_qty <= 0: BREAK
    IF entry.effective_timestamp <= rest_entry_time: SKIP (entry was there before we arrived)
    IF entry.effective_timestamp >= expiry_time: BREAK

    entry_qty = lit_remaining.get(entry.orderid, entry.quantity)
    IF entry_qty <= 0: SKIP
    IF session_filter AND NOT valid_session(entry_ts): SKIP
    IF crossing_keys invalid: SKIP

    # Price crossing check
    IF sweep.side == BUY:  IF entry.price > lit_resting_price: SKIP  (sell too expensive)
    IF sweep.side == SELL: IF entry.price < lit_resting_price: SKIP  (buy too cheap)

    match_qty = min(remaining_qty, entry_qty)
    execution_price = lit_resting_price

    Emit two trade rows (venue='lit', phase=2)

    UPDATE: remaining_qty, lit_remaining[entry.orderid],
            sweep_state[sweep_id].lit_filled, sweep_state[sweep_id].lit_matches
```

Note: `lit_remaining` is a shared dict across sweeps. An entry consumed by sweep A is not available to sweep B.

#### Option B — Flat Scan (`RESTING_LIT_BOOK_MODE = 'scan'`)

```
contra_lit = lit_index[(orderbookid, contra_side)]  ← pre-indexed, sorted by time

FOR each contra in contra_lit (time order only — no price priority):
    IF remaining_qty <= 0: BREAK
    IF contra_ts <= rest_entry_time: SKIP
    IF contra_ts >= expiry_time: BREAK
    Apply session filter, crossing keys, iceberg
    Price check: same as Option A
    match_qty = min(remaining_qty, contra.quantity)
    Emit two trade rows (venue='lit', match_type='RESTING_LIT_SCAN', phase=2)
```

Option B is an **upper bound** on lit fills: it ignores competing resting orders at better prices and does not track contra consumption across sweeps.

### 6.7 Summary Generation

After the lit leg for each sweep:

```python
_make_resting_summary(
    orderid, rest_entry_time, orderbookid, side,
    dark_filled, dark_matches, lit_filled, lit_matches
)
```

Output columns: `orderid`, `rest_entry_time`, `orderbookid`, `side`, `dark_filled_qty`, `dark_num_matches`, `lit_filled_qty`, `lit_num_matches`, `total_resting_filled_qty`.

---

## 7. Matching Rules Shared by Both Phases

### 7.1 Time Priority

- Phase 1 (aggressive): contras sorted by `(effective_timestamp, sequence)` within the sweep's execution window.
- Phase 2 dark (passive): all contras globally sorted by `(effective_timestamp, sequence)`.
- Phase 2 lit Option A: sorted by `(price, effective_timestamp, sequence)` per side.
- Phase 2 lit Option B: sorted by `(effective_timestamp, sequence)` only.

### 7.2 Effective Timestamp & Priority Loss

An order loses its time priority if it was amended:

```
IF orderbookposition > 0
   OR changereason ∈ {MARKET_CONVERTED_AUCTION=7, MARKET_TO_LIMIT=8, UNDISCLOSED_TO_REGULAR=39}:
   effective_timestamp = timechanged
ELSE:
   effective_timestamp = timestamp
```

### 7.3 Tick Size

Priority of tick size resolution:
1. `tick_size_override` (explicit integer from reference data loader).
2. Price-dependent lookup in `tick_size_table` (from `{date}_ob.csv`).
3. Fallback: estimated from NBBO spread (minimum 1, scales with spread).

### 7.4 Mid-Tick Price Improvement

```
IF midtick ∈ {MIDTICK_YES=1, MIDTICK_DARK_WITH_MIDTICK=4}:
    half_tick = tick_size / 2
    IF side == BUY:   execution_price = max(midpoint - half_tick, nbbo_bid)
    IF side == SELL:  execution_price = min(midpoint + half_tick, nbbo_offer)
ELSE:
    execution_price = midpoint
```

In Phase 1, `effective_midtick = max(sweep.midtick, contra.midtick)`.  
In Phase 2, `effective_midtick = max(RESTING_USE_MIDTICK ? YES : NO, contra.midtick)`.

### 7.5 Session State Filtering

Only contras/entries with timestamps in `OPEN` or `CONTINUOUS` sessions are eligible. All other states — `PRE_OPEN`, `AUCTION`, `POST_CLOSE`, `CLOSED`, `PRE_CSPA`, `CSPA`, `ADJUST`, `PURGE_ORDERS`, `SYSTEM_MAINTENANCE` — are rejected.

### 7.6 MAQ (Minimum Acceptable Quantity)

MAQ applies only in Centre Point (not TradeMatch).

```
IF maq > 0:
    IF singlefillminimumquantity == 1:
        SKIP this fill if fill_qty < maq  (every fill must meet MAQ)
    ELSE:
        BREAK if remaining_qty < maq AND already_filled > 0
        SKIP  if fill_qty < maq AND already_filled == 0
```

### 7.7 Crossing Key Validation

```
IF same participant (participantid > 0 AND sweep.pid == contra.pid):
    IF either crossing_key == 0: SKIP  (self-match prevention)
    IF keys differ: SKIP               (intentional block)
    ELSE: allow                        (matching non-zero keys = explicit crossing intent)
```

### 7.8 Iceberg Order Support

```
IF display_quantity IS NOT NULL:
    available_qty = min(display_quantity, total_quantity)
ELSE:
    available_qty = total_quantity
```

Full iceberg refresh simulation (top-up after each fill) is not implemented; only the first shown slice is used.

### 7.9 Execution Delay

All match timestamps have a lognormal delay added to model exchange latency:

```
delay_ms ~ LogNormal(μ=ln(5ms), σ=0.5)
match_timestamp += delay_ms * 1_000_000 ns
```

---

## 8. Configuration Flags

### 8.1 Master Flags

| Flag | Default | Effect |
|------|---------|--------|
| `SIMULATE_RESTING_PHASE` | `False` | Enable Phase 2. False = S1 only, no behavioural change. |
| `SIMULATE_LIT_RESTING` | `False` | Include lit venue in Phase 2. False = S2, True = S3. |

### 8.2 Lit Book Mode

| Flag | Value | Effect |
|------|-------|--------|
| `RESTING_LIT_BOOK_MODE` | `'full'` | Option A: price-time priority order book (correct per spec) |
| | `'scan'` | Option B: flat time scan (upper bound, faster) |

### 8.3 Execution Price

| Flag | Default | Effect |
|------|---------|--------|
| `RESTING_USE_MIDTICK` | `True` | Dark CP: resting price = limit ± 0.5*tick (spec §25.2 pt 6) |
| `RESTING_USE_MIDTICK` | `False` | Dark CP: resting price = limit (non-spec, experimental) |
| `RESTING_LIT_USE_LIMIT` | `True` | Lit: resting price = limit (spec §25.2 pt 7) |
| `RESTING_LIT_USE_LIMIT` | `False` | Lit: resting price = limit ± 0.5*tick (experimental) |

### 8.4 Market Mechanics Flags

| Flag | Default | Description |
|------|---------|-------------|
| `RESTING_MODEL_CANCELLATION` | `True` | Orders expire at next non-trading session |
| `RESTING_APPLY_CROSSING_KEYS` | `True` | Enforce crossing key validation (§24.6) |
| `RESTING_APPLY_SESSION_FILTER` | `True` | Only match in OPEN/CONTINUOUS sessions |
| `RESTING_APPLY_MAQ` | `True` | Enforce minimum acceptable quantity (§25.3, CP only) |
| `RESTING_APPLY_PREFERENCING` | `True` | Same-participant preferencing (§24.10) |
| `RESTING_APPLY_ICEBERG` | `True` | Respect iceberg display quantity |

### 8.5 Other Flags

| Flag | Default | Description |
|------|---------|-------------|
| `NBBO_SOURCE` | `'INTERNAL'` | `'INTERNAL'` = order fields; `'EXTERNAL'` = nbbo.csv.gz |
| `USE_POLARS_TRANSFORMS` | `False` | Vectorised effective_timestamp via Polars |
| `USE_DUCKDB_IO` | `False` | DuckDB-backed CSV reads and glob queries |

---

## 9. Output Schema

### 9.1 Phase 1 Simulated Trades (`cp_trades_simulation.csv`)

| Column | Type | Description |
|--------|------|-------------|
| `EXCHANGE` | int | Always 3 (ASX) |
| `sequence` | int | Row number |
| `tradedate` | str | Trade date YYYY-MM-DD |
| `tradetime` | int | Execution timestamp (ns), includes latency delay |
| `securitycode` | int | orderbookid |
| `orderid` | int | Order ID (sweep leg or contra leg) |
| `dealsource` | int | 99 (synthetic) |
| `exchangeinfo` | str | Empty |
| `matchgroupid` | int | Unique match group ID linking the two legs |
| `nationalbidpricesnapshot` | int | NBBO bid at match time |
| `nationalofferpricesnapshot` | int | NBBO offer at match time |
| `tradeprice` | int | Execution price (midpoint ± half-tick) |
| `quantity` | int | Matched quantity |
| `side` | int | 1=BUY, 2=SELL |
| `participantid` | int | 0 (not tracked at trade level) |
| `passiveaggressive` | int | 1=aggressive (sweep), 0=passive (contra) |
| `row_num` | int | Row number |
| `match_type` | str | `SWEEP_TO_REGULAR` or `SWEEP_TO_SWEEP` |
| `contra_participant_type` | str | Participant type from reference data |

### 9.2 Phase 2 Resting Trades (`cp_trades_simulation_resting.csv`)

All Phase 1 columns plus:

| Column | Type | Description |
|--------|------|-------------|
| `phase` | int | Always 2 |
| `venue` | str | `'dark'` or `'lit'` |
| `rest_entry_time` | int | Timestamp when sweep entered resting queue (ns) |
| `resting_duration_sec` | float | `(tradetime - rest_entry_time) / 1e9` |
| `match_type` | str | `RESTING_DARK`, `RESTING_LIT`, or `RESTING_LIT_SCAN` |

### 9.3 Phase 1 Order Summary (`simulation_order_summary.csv`)

| Column | Description |
|--------|-------------|
| `orderid` | Sweep order ID |
| `timestamp` | Order entry timestamp |
| `side` | 1=BUY, 2=SELL |
| `quantity` | Available quantity (leavesquantity) |
| `matched_quantity` | Phase 1 filled quantity |
| `remaining_quantity` | Unfilled after Phase 1 |
| `fill_ratio` | matched / available |
| `num_matches` | Number of Phase 1 fills |
| `orderbookid` | Security |
| `lost_priority` | True if order had been amended |
| `changereason` | Change reason code |

### 9.4 Phase 2 Resting Summary (`resting_order_summary.csv`)

| Column | Description |
|--------|-------------|
| `orderid` | Sweep order ID |
| `rest_entry_time` | Resting start timestamp |
| `orderbookid` | Security |
| `side` | 1=BUY, 2=SELL |
| `dark_filled_qty` | Total dark CP fills in Phase 2 |
| `dark_num_matches` | Number of dark matches |
| `lit_filled_qty` | Total lit fills in Phase 2 |
| `lit_num_matches` | Number of lit matches |
| `total_resting_filled_qty` | dark + lit combined |

---

## 10. Key Design Decisions & Trade-offs

### 10.1 Contra-Centric Dark Loop (Preferencing)

**Decision:** Outer loop iterates contras in time order; inner loop selects the best eligible resting sweep.

**Why:** ASX §24.10 says the incoming order routes to same-participant resting orders first. If the outer loop were over resting sweeps, you would be asking "which contra does this sweep prefer?" — the opposite of what the rule says. The rule is about where the incoming contra is sent, so contras must be the outer loop.

**Trade-off:** This means a single contra can match against multiple sweeps (until its quantity is exhausted). Sweeps compete for each contra in participant-preference + FIFO order.

### 10.2 Dark Fills Before Lit Fills

**Decision:** Run the full dark leg to completion before starting the lit leg.

**Why:** In dual-posting (§25.2), both venues are active simultaneously. However, we model them sequentially to avoid complexity: dark is prioritised because CP is the primary venue for these orders. The remaining quantity after dark is then offered to the lit venue. This is a simplification that may slightly understate lit fills (some dark matches might not have occurred in a true simultaneous regime).

### 10.3 Option A vs Option B for Lit Fills

| | Option A (full) | Option B (scan) |
|-|-----------------|-----------------|
| Price priority | Yes — correct per ASX TradeMatch rules | No — ignores price priority |
| Cross-sweep depletion | Yes — `lit_remaining` shared across sweeps | No — treats each sweep independently |
| Fill estimate | Lower bound (correct) | Upper bound (overestimates) |
| Input required | Full raw orders file | Same |
| Computational cost | Higher (price-sorted book walk) | Lower (flat scan) |

Use Option A for publication-quality results. Option B for a quick sensitivity upper bound.

### 10.4 Tick Size Resolution

The half-tick dark resting price requires an accurate tick size. Resolution priority:
1. Explicit override from reference data (most accurate).
2. Price-band table from `{date}_ob.csv`.
3. Spread-based heuristic (least accurate, only when reference data missing).

### 10.5 NBBO Source

`INTERNAL` is the default because the NBBO is already embedded in each order row (nationalbid/nationaloffer), so no additional file is needed. `EXTERNAL` uses a separate timestamped NBBO file with binary-search lookup for accuracy at high-frequency timestamps.

### 10.6 Execution Delay

A lognormal delay (μ=5ms, σ=2ms) is added to simulate realistic exchange processing latency. This means simulated trade timestamps are slightly after the contra order's timestamp. In production analysis, this delay should be calibrated against observed first-fill latencies in the real trades data.

---

## 11. Testing Requirements

### 11.1 Test Coverage (32 tests — `tests/test_resting_phase.py`)

| Group | Tests | What is verified |
|-------|-------|-----------------|
| Helper unit tests | 7 | `_calc_resting_price`, `_calc_lit_resting_price`, `_get_session_end_time`, `_build_lit_order_book` |
| S2 dark basic | 3 | Fill at correct midpoint, no fill when midpoint outside resting price, midtick-off uses limit |
| Cancellation | 2 | Late contra blocked when cancellation ON; allowed when OFF |
| Crossing keys | 4 | Key=0 blocked; matching keys allowed; mismatched blocked; flag-off bypasses |
| Session filter | 2 | Non-OPEN contra blocked ON; allowed OFF |
| MAQ | 3 | Small fill blocked; sufficient fill allowed; flag-off bypasses |
| Preferencing | 2 | Same-participant fills first (§24.10); FIFO when flag OFF |
| S3 lit scan | 2 | Basic fill, no fill when price too high |
| S3 lit full book | 3 | Basic fill, no fill when price too high, full=scan in simple case |
| Edge cases | 2 | Empty remainder returns empty DataFrames; `build_remainder_df` excludes/includes correctly |
| S3 dual venue | 1 | Dark partial fill + lit fills remainder |

### 11.2 Key Invariants

- A contra order's quantity is never consumed more than once (tracked via `cp_order_remaining`).
- A lit order entry's quantity is never consumed more than once across sweeps (tracked via `lit_remaining`, Option A only).
- Phase 2 fills never exceed Phase 1 remaining quantity.
- Each match emits exactly two trade rows (one per leg) with the same `matchgroupid`.
- `dark_filled + lit_filled ≤ remaining_qty` (as measured at Phase 2 start).

---

*End of document.*
