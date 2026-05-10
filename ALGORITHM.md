# SweepOrders — Algorithm Reference & Research-Question Evaluation

This document describes the algorithm running on the `_parq` branch as of
commit `100fb08`: how Centre Point sweep orders are selected, how a dark-pool
counterfactual is simulated against them, what metrics are computed, and how
real vs simulated execution is compared.

It also explicitly answers: **does the end-to-end pipeline answer the
research question?** That evaluation is in §6.

For the canonical exchange semantics, see `docs/bi.txt` and `docs/dd.txt`.

---

## 1. Research question

For ASX Centre Point sweep orders (`exchangeordertype = 2048`) that aggressed
the lit book at submission and parked some unfilled remainder on lit:

> **Would dark-resting in Centre Point have filled the lit-bound remainder
> instead — and at what price, what cost, what speed?**

Operational restatement:

- A sweep arrives at NEW_ORDER. It immediately tries to match in dark
  (Centre Point), then aggressively crosses lit. Whatever neither path
  consumed parks on lit as a passive limit order — call this the **rest_on_lit
  quantity**.
- The pipeline asks: if that rest_on_lit chunk had stayed in the dark book
  (instead of being routed to lit), would dark contras alive in the sweep's
  active window have filled it?
- The sweep is compared against this counterfactual on 36 execution-quality
  metrics (fill rate, exec cost, price improvement, time-to-fill, etc.).

The unit of analysis is the **survivor sweep** — sweeps that fully filled in
real life and had a non-zero rest_on_lit chunk to ask the counterfactual about.

---

## 2. End-to-end pipeline

```
data/raw/                                        Stage 0 (one-shot prep)
  orders/        {ticker}_{date}_orders.csv       │ optional convert_raw.py
  trades/        {ticker}_{date}_trades.csv       │ → *.parquet (zstd, ~13× smaller)
  nbbo/          ...                              ▼
  session/                                       Stage 1+2 = process.py
  reference/                                       │
  participants/                                    ▼
                                                 data/processed/{date}/{orderbookid}/
                                                   ├── cp_orders_filtered.parquet
                                                   ├── cp_trades_matched.parquet
                                                   ├── orders_before_matching.parquet
                                                   ├── orders_after_matching.parquet      ← post-dark/pre-lit synthesis
                                                   ├── last_execution_time.parquet        ← survivor list + window
                                                   ├── contra_rest_on_lit.parquet         ← dark-available cap per sweep-type contra
                                                   ├── contra_non_survivor_dark.parquet   ← phantom-liquidity adjustment
                                                   └── cp_trades_simulation.parquet       ← simulator output

                                                 Stage 3+4 = aggregate.py
                                                   │
                                                   ▼
                                                 data/outputs/{date}/{orderbookid}/
                                                   ├── real_trade_metrics.parquet         ← 36 metrics on real trades
                                                   ├── simulated_trade_metrics.parquet    ← 36 metrics on simulated trades
                                                   ├── simulation_order_summary.parquet
                                                   └── trade_level_comparison.parquet     ← per-order real-vs-sim row

                                                 Stage 5+6 = report.py
                                                   │
                                                   ▼
                                                 data/reports/
                                                   ├── per_security/{date}_{obid}_{ticker}.csv
                                                   ├── by_day.csv                     ← real-only rollups (legacy)
                                                   ├── by_ticker.csv
                                                   ├── by_volume_bucket.csv
                                                   ├── by_participant.csv             ← stub
                                                   ├── by_session_phase.csv           ← stub
                                                   ├── by_day_comparison.csv          ← real-vs-sim rollups (research-question outputs)
                                                   ├── by_ticker_comparison.csv
                                                   └── by_volume_bucket_comparison.csv
```

Final reports stay CSV (Excel-friendly). All intermediates are zstd-compressed
Parquet for columnar predicate pushdown and fast reload.

---

## 3. Algorithm — stage-by-stage

### 3.1 Sweep qualification funnel (process.py)

A sweep is **simulated** only if it passes all three filters:

1. **Order type:** `exchangeordertype == 2048` (Centre Point sweep order, per
   `dd.txt` p.24).
2. **Real-life completion:** the order's last event is `changereason == 3`
   (TRADED) with `leavesQuantity == 0` AND a `changereason == 6` (NEW_ORDER)
   event exists in its history. Code: `_filter_sweep_orders_by_execution`.
3. **Non-zero rest_on_lit:** at the end of the sweep's initial matching pass
   (last event sharing the NEW_ORDER timestamp), `leavesQuantity > 0`. A sweep
   that fully filled at submission has nothing to counterfactually re-route.
   Code: `_compute_rest_on_lit_qty`.

Sweeps passing all three are the **survivors**, written to
`last_execution_time.parquet` with their per-order
`(first_execution_time, last_execution_time)` window.

| Field | Meaning |
|---|---|
| `first_execution_time` | NEW_ORDER timestamp (sweep arrival). |
| `last_execution_time` | Max tradetime for the order's real trades — i.e., the moment `leavesQuantity` reached 0 at the terminal `cr=3`. |

The simulator iterates only survivors; everyone else (non-survivor sweeps,
non-sweep CP orders) becomes contra-pool population (§3.4).

### 3.2 `orders_before` / `orders_after` synthesis (process.py)

Both files have one row per CP orderid. Built from the full raw event stream
filtered to `exchangeordertype ∈ {64, 256, 2048, 4096, 4098}` (= all CP types,
per `config.ELIGIBLE_MATCHING_ORDER_TYPES`).

- **`orders_before_matching.parquet`** — captures each order's state at its
  NEW_ORDER (`cr=6`) event. Filters: `orders_sorted[changereason == 6]`,
  dedupe by orderid keeping the first row. Code: `process.py:1129`.
- **`orders_after_matching.parquet`** — same rows as `orders_before` but with
  `leavesquantity` overridden to `(quantity − dark_at_submission)`. Synthesizes
  the order's "post-dark, pre-lit" state. Code: `_synthesize_orders_after`.

`dark_at_submission` is the qty that filled in dark on the order's NEW_ORDER
timestamp:

```python
dark_at_submission(orderid) =
    SUM(trade.quantity)
    where trade.orderid == orderid
      AND trade.dealsource ∈ DARK_FILL_DEALSOURCES
      AND trade.tradetime  == NEW_ORDER timestamp for that orderid
```

`DARK_FILL_DEALSOURCES = {46, 47, 49, 50, 51, 52}` — every CP-mediated dark
fill mechanism (`dd.txt` §3.3.x):

| code | name |
|---|---|
| 46 | Preference Matched |
| 47 | CentrePoint |
| 49 | Preference Only Matched |
| 50 | Any Price Block (APB) |
| 51 | Preference APB |
| 52 | Preference Only APB |

`48` (BookTradeCentrePoint) is intentionally excluded — it's a book-trade
mechanism, not a continuous dark match.

### 3.3 Per-sweep window — `last_execution_time.parquet`

Built by `extract_last_execution_times`. For each survivor, persists:

```
orderid, first_execution_time, last_execution_time
```

This is the window the simulator scans contras within and the survivor list
that `aggregate.py` filters real metrics against.

### 3.4 Contra pool — phantom-liquidity guards

The simulator's contra pool = `orders_before_matching.parquet` filtered to
the five CP types. Two phantom-liquidity adjustments cap dark-available
inventory per contra:

#### 3.4.1 `contra_rest_on_lit.parquet` (sweep-variant cap)

Sweep-type contras (`exchangeordertype ∈ {2048, 4098}`) aggressed lit at
submission in real life. Their dark-available inventory is only the resting
portion left on lit — not their full original quantity. Per such contra:

```
rest_on_lit_quantity = leavesQuantity at the end of the contra's NEW_ORDER pass
                     = leavesQuantity at the last event sharing the NEW_ORDER timestamp
```

Pure-passive CP types (64, 256, 4096) keep their full submit qty as
dark-available. Code: `_build_contra_rest_on_lit`, `_compute_rest_on_lit_qty`.

#### 3.4.2 `contra_non_survivor_dark.parquet` (non-survivor consumption)

Non-survivor aggressors (sweeps that didn't pass the survivor gate) consumed
contra inventory in real life via dark fills. Without subtraction, the
simulator over-states dark-available qty. Per contra:

```python
non_survivor_dark_quantity(contra) =
    SUM(trade.quantity)
    where trade.matchgroupid pairs:
        passive leg: trade.orderid = contra AND trade.passiveaggressive = 0
                                          AND trade.dealsource ∈ DARK_FILL_DEALSOURCES
        aggressor leg: trade.orderid NOT IN survivor_orderids
                                     AND trade.passiveaggressive = 1
```

This is subtracted from the contra's dark-available qty at sim startup.
Code: `_build_contra_non_survivor_dark`.

### 3.5 Simulator (`simulator.py`) — per-sweep matching

`simulate_partition` runs once per `(date, orderbookid)` partition (parallel
across partitions via `ProcessPoolExecutor`).

#### 3.5.1 Inputs to the kernel (`SimContext`)

| Component | Source |
|---|---|
| Sweeps to walk | `last_execution_time.parquet` ⋈ `orders_after_matching.parquet[type=2048]` |
| `sweep_qty` per sweep | `orders_after.leavesquantity` (= original − dark_at_submission) |
| Sweep window | `[first_execution_time, last_execution_time]` |
| Contra pool | `orders_before_matching.parquet` filtered to CP types |
| Contra `dark_avail` | `quantity − rest_on_lit_subtraction − non_survivor_dark_subtraction` |
| Contra `effective_timestamp` | `timechanged` if priority lost; else `timestamp` (see below) |
| NBBO snapshots | per-order columns (INTERNAL source) |

**Effective timestamp** captures contra priority loss:

```
effective_timestamp = timechanged   if (orderbookposition > 0
                                        OR changereason ∈ {7, 8, 39})
                      timestamp     otherwise
```

Plain `cr=5` (user update) does NOT cost priority by itself. Only specific
amend types do.

#### 3.5.2 Per-sweep eligibility & match loop

For each survivor sweep `s`:

```python
# Binary-search the sorted contra event array
lo = searchsorted(c_eff_ts, first_exec_time(s), side='left')
hi = searchsorted(c_eff_ts, last_exec_time(s),  side='right')
candidates = contras[lo:hi] where:
    same orderbookid as sweep
    opposite side
    not the sweep itself
    passes session-state filter (matching only in OPEN/CONTINUOUS)

for contra in candidates sorted by (eff_ts, sequence):
    if sweep.remaining <= 0: break
    if contra.dark_avail <= 0: continue

    # MAQ rules — asymmetric continue vs break
    if sweep.maq_violated_for_match: continue or break  # see §3.6
    if contra.maq_violated:          continue

    # Crossing-prevention (same participant + crossing keys)
    if same_participant_with_mismatched_crossing_keys: continue

    # Price determination
    if contra.is_apb (type=4096 AND midtick ∈ {5,6}):
        execution_price = contra.limit_price
        match_type = 'BLOCK_PREF' if has_preference else 'BLOCK'
        dealsource = 51 or 50
    else:
        nbbo = get_nbbo_at_match_time
        midpoint = (nbbo.bid + nbbo.offer) / 2
        if midpoint outside price_limits: continue
        if not crosses(sweep.price, midpoint, side): continue
        execution_price = apply_midtick_improvement(midpoint, ...)
        match_type = 'SWEEP_TO_SWEEP' if contra.type==2048 else 'SWEEP_TO_REGULAR'
        dealsource = 47

    match_qty = min(sweep.remaining, contra.dark_avail)
    emit_trade(sweep,  qty=match_qty, side=aggressor, ...)
    emit_trade(contra, qty=match_qty, side=passive,    ...)
    sweep.remaining -= match_qty
    contra.dark_avail -= match_qty   # shared across all sweeps in the partition
```

**Shared inventory** (`order_remaining` dict) persists across the partition's
sweep loop. The order in which sweeps are processed (by
`(effective_timestamp, sequence)`) determines who gets each contra share —
"first sweep gets priority" emerges naturally.

### 3.6 MAQ rules (asymmetric — spec-mandated)

`minimumquantity` and `singlefillminimumquantity` are checked separately
because the spec mandates an asymmetry between "skip this contra" (continue)
and "stop scanning for this sweep" (break):

| Condition | Action |
|---|---|
| `singleFillMinimumQuantity == 1` AND `match_qty < sweep.maq` | continue — try next contra |
| `sweep.remaining < sweep.maq` AND already partially filled | **break** — stop scanning for this sweep |
| `sweep.remaining < sweep.maq` AND not yet filled | continue — keep looking for a contra big enough |
| Contra's MAQ violated | continue — try next contra |

The break trap prevents an MAQ-anchored sweep from giving up mid-fill and
matching small contras that violate its own MAQ.

### 3.7 APB branch (Any Price Block)

A contra qualifies as APB when `exchangeordertype == 4096` AND
`midtick ∈ {5, 6}`. APB matches:

- Bypass the NBBO check entirely (per `bi.txt` §24.1.3).
- Execute at the **contra's limit price**, not midpoint.
- Enforce `MIN_BLOCK_SIZE` if configured.
- Emit `dealsource = 50` (APB) or `51` (Preference APB).

### 3.8 Session-state filter

Continuous matching only happens in:
```
MATCHING_SESSION_STATES = {'OPEN', 'CONTINUOUS'}
```
All other states (`PRE_OPEN`, `AUCTION`, `POST_CLOSE`, `CLOSED`, `PRE_CSPA`,
`CSPA`, `ADJUST`, `ADJUST_ON`, `PURGE_ORDERS`, `SYSTEM_MAINTENANCE`) are
rejected. Implementation: searchsorted against the partition's session-state
array at the candidate contra's effective timestamp.

### 3.9 What the simulator emits

Per partition:
- **`cp_trades_simulation.parquet`** — per-leg trade rows (passive + aggressor
  per match), schema mirrors raw trades.
- **`simulation_order_summary.parquet`** — per-sweep summary:
  `orderid, side, quantity, matched_quantity, remaining_quantity, fill_ratio,
   num_matches, orderbookid, lost_priority, changereason`.

---

## 4. Metrics — Stage 3 (`aggregate.py`)

### 4.1 The 36 per-order metrics

`calculate_trade_metrics` is the unified per-order metric calculator,
run twice per partition: once over real trades, once over simulated trades.
The same 36 metric columns appear in both `real_trade_metrics.parquet` and
`simulated_trade_metrics.parquet`, which is what enables Stage 4's row-by-row
diff.

| Group | Metrics |
|---|---|
| **Fill** | `qty_filled`, `order_quantity`, `fill_ratio`, `fill_rate_pct`, `num_fills`, `avg_fill_size`, `fill_status` |
| **Price** | `vwap`, `arrival_midpoint`, `arrival_bid`, `arrival_offer`, `arrival_spread`, `arrival_spread_bps`, `limit_price`, `price_improvement`, `price_improvement_bps` |
| **Exec cost** | `exec_cost_arrival_bps`, `exec_cost_vw_bps`, `effective_spread_pct`, `slippage_bps`, `implementation_shortfall_bps`, `total_execution_value` |
| **Timing** | `time_to_first_fill_sec`, `execution_duration_sec`, `total_duration_sec`, `avg_time_between_fills`, `vw_exec_time_sec` |
| **Market** | `first_fill_midpoint`, `last_fill_midpoint`, `market_drift_bps`, `avg_execution_spread_bps`, `spread_volatility_bps`, `price_volatility_bps` |

### 4.2 Survivor-filtered real metrics

Per Task 7's fix: real metrics are computed only on survivor orderids
(intersection of `orders_before` sweep orderids with `last_execution_time.parquet`),
making the real-vs-sim comparison population symmetric. Code:
`_filter_to_survivors`, called at `_process_partition_calculate_metrics`.

### 4.3 Headline metrics (used for distribution stats)

Four metrics drive the routing-strategy answer directly:

```python
HEADLINE_METRICS = frozenset({
    "fill_rate_pct",
    "exec_cost_arrival_bps",
    "price_improvement_bps",
    "time_to_first_fill_sec",
})
```

In the comparison rollups (§5.2), these four get full distribution stats
(mean, median, p25, p75) on both the real side and both sim flavours.
Other metrics get mean only.

---

## 5. Comparison — Stage 4 + Stage 6

### 5.1 Per-order comparison — `trade_level_comparison.parquet`

Stage 4 joins `real_trade_metrics.parquet` and `simulated_trade_metrics.parquet`
on `orderid`. Each row has both real and `sim_*` versions of all 36 metrics,
plus:

- `match_status` ∈ `{EXACT_MATCH, CLOSE_MATCH, PARTIAL_MATCH, POOR_MATCH}` —
  per-order verdict comparing real vs sim.
- `accuracy_score` — numeric scalar combining fill, price, time deltas.
- `quantity_diff`, `price_diff`, `num_trades_diff`, `execution_time_diff_sec` —
  raw deltas.

This is the primary research-question artifact. One row per surviving sweep.
For sweeps where the simulator found no contras, the `sim_*` columns are
NULL and the row buckets into `POOR_MATCH`.

### 5.2 Comparison rollups — Stage 6 (`report.py`)

`_comparison_rollup` aggregates the per-order comparison rows by group_cols.
Per-metric output for each group:

```
real_avg_<metric>                   — mean across all rows
sim_avg_<metric>_overlap            — mean over rows where sim_total_matches > 0
sim_avg_<metric>_all_survivors      — mean with NULL sim_<metric> coalesced to 0
                                       (captures the "dark would have failed" signal)
```

For headline metrics: also emits `_median`, `_p25`, `_p75` in all three
flavours. Plus per-group counters:

```
n_total                  — survivors in this group
n_with_sim               — rows where simulator found at least one match
pct_with_sim_activity    — engagement rate (= 100 × n_with_sim / n_total)
n_exact_match
n_close_match
n_partial_match
n_poor_match
n_other_match            — catch-all for unknown match_status values
```

Three rollups emitted: `by_day_comparison.csv`, `by_ticker_comparison.csv`,
`by_volume_bucket_comparison.csv`.

The two-flavour sim mean is the key artifact. `_overlap` shows "when dark
engaged, how good was the fill?"; `_all_survivors` shows "averaged over all
sweeps including the ones where dark found nothing." The gap between them is
the engagement rate's effect on aggregate dark performance.

---

## 6. Does the pipeline answer the research question?

### 6.1 What the pipeline answers (yes)

Per ticker / per day / per volume bucket, the comparison rollups directly
give:

1. **Engagement rate** — `pct_with_sim_activity`: of the survivors in this
   group, what % had any contras to match against in dark?
2. **Conditional fill quality** — `sim_avg_fill_rate_pct_overlap`: when dark
   engaged, what fraction of the lit-bound chunk did it fill?
3. **Unconditional fill quality** — `sim_avg_fill_rate_pct_all_survivors`:
   averaged over all survivors (including no-engagement zeros), how well did
   dark do?
4. **Cost delta** — `real_avg_exec_cost_arrival_bps` vs the two sim flavours:
   was dark cheaper or more expensive?
5. **Speed delta** — `real_avg_time_to_first_fill_sec` vs sim: was dark
   faster?
6. **Price-improvement delta** — `real_avg_price_improvement_bps` vs sim.

The headline distribution stats (median / p25 / p75) for each of the four
headline metrics show whether the means are pulled by outliers and where
the bulk of the distribution lies.

### 6.2 Sample evidence from CBA / 2024-09-05 (current state)

| | Real | Sim (overlap only) | Sim (all survivors, NULL=0) |
|---|---:|---:|---:|
| Mean fill % | 104.0 | 94.4 | 33.7 |
| Mean exec cost (bps) | −0.03 | +0.72 | +0.26 |
| Mean PI (bps) | 1.36 | 2.31 | 0.84 |
| Mean time-to-first-fill (s) | 36.5 | 21.3 | 7.6 |

`n_total = 18,131` survivors, `n_with_sim = 6,477` (35.7% engagement),
`match_status`: EXACT 19.4%, CLOSE 5.5%, PARTIAL 2.7%, POOR 72.5%.

**Reading:** for CBA on 2024-09-05, dark routing would have engaged with
35.7% of survivor sweeps. When it engaged, dark was faster and gave better
price improvement, but real lit was cheaper and filled more. Across all
survivors (treating non-engagement as 0% fill), real dominates.

### 6.3 Known limitations / open issues

#### 6.3.1 Over-fill bug for amended sweeps (BLOCKING)

`fill_rate_pct > 100%` appears on ~5% of survivors for cba/bhp/wtc and ~94%
of survivors for drr (the latter due to a separate raw-data duplicate-row
issue). Root cause: `order_quantity` is captured at NEW_ORDER (e.g., qty=5)
but `qty_filled` sums real trades whose tradetime falls within the survivor's
window — including trades that occurred AFTER the participant amended the
order to a larger size (e.g., qty=5,154). The metric divides 5,154 by 5.

Open fix (proposed but not yet implemented): use the order's **terminal-event
quantity** (i.e., `quantity` at the cr=3 leaves=0 event, which reflects any
amendments) as `order_quantity`. Alternative discussed: cap qty_filled at
the original or freeze at first amendment. The chosen approach (per user) is
the terminal-event quantity — implementation pending.

#### 6.3.2 drr raw orders.csv has 50% duplicate rows

Every event row in `data/raw/orders/drr_20240905_orders.csv` appears twice
(consecutive sequence numbers, identical otherwise). This propagates into
`cp_trades_matched.parquet` (50% duplicate rate for drr, 0% for cba/bhp/wtc).
The duplicates double `qty_filled` independently of the amendment issue.

Defensive fix proposed: dedup on `(orderid, sequence)` for orders and
`(matchgroupid, passiveaggressive, orderid, tradetime, quantity)` for trades
in `process.py`'s ingestion. Print a warning when duplicates are detected.
Not yet implemented.

#### 6.3.3 Engagement asymmetry — 18,131 survivors vs 6,477 simulator-active

`last_execution_time.parquet` includes survivors that pass the three-level
gate (~18k for cba). The simulator only finds matchable contras for ~6.5k of
them (~36% engagement). The other ~64% bucket into POOR_MATCH. Two readings:

- **Real signal:** dark was genuinely thin for most survivors that day —
  this IS the answer to the research question for the long tail.
- **Possible bias:** contra pool construction may be over-filtering (e.g.,
  rest_on_lit cap is conservative; non-survivor consumption deduction may
  over-deduct). Worth spot-checking individual POOR_MATCH cases by hand.

The two-flavour sim metrics (overlap vs all_survivors) explicitly surface
this gap so the reader doesn't have to choose one denominator.

#### 6.3.4 `by_participant.csv` and `by_session_phase.csv` are stubs

Stage 6 emits these as empty CSVs with explanatory comments. They require
joins against raw orders / session data that aren't in the per-order metric
output today. To complete:

- `participantid` is in `orders_before` but not propagated through to
  `real_trade_metrics.parquet`. Add it as an extra column in
  `_aggregate_simulated_trades_per_order` and `calculate_trade_metrics`.
- `session_phase` requires joining each fill's `tradetime` against the
  partition's session-state array. Same join already used inside the simulator
  for the `MATCHING_SESSION_STATES` filter — can be lifted out.

#### 6.3.5 Sample-size concerns

The current dataset (laptop) has 4 tickers × 1 trade date. drr has only
1,646 survivors and 95% are POOR_MATCH (it's a thin stock). For statistical
weight on the "should we route dark?" question, multi-day × multi-ticker
runs on the server are required. The pipeline supports this (multi-date
filters in process.py / aggregate.py / report.py); just hasn't been done.

#### 6.3.6 Parity baseline regen blocked

`tests/parity_baseline/20240505_cba/` was captured against the legacy code
path. After the `_parq` changes (orders_after synthesis, broader dealsource
set, survivor filter, etc.) the baseline no longer matches. `test_parity.py`
fails on `processed_parity` and `outputs_parity` until the baseline is
regenerated against a date that's actually present in the raw data.

### 6.4 Summary verdict

**The pipeline answers the research question — partially.** The signal
exists per-order in `trade_level_comparison.parquet` and is now properly
aggregated in the new `*_comparison.csv` rollups. The engagement rate, fill
quality, cost delta, speed delta, and price-improvement delta are all
quantified.

But the over-fill bug (6.3.1) currently corrupts ~5% of CBA/BHP/WTC and ~94%
of DRR. Until that's fixed, headline `fill_rate_pct` numbers in the rollups
should be read with caution — particularly for DRR. Once 6.3.1 + 6.3.2 are
fixed and a multi-day server run is done, the comparison rollups directly
support the routing-strategy answer.

---

## 7. How to run

### 7.1 First-time setup

```bash
source activate.sh                     # activates swp_env/
pip install -r requirements.txt        # polars, duckdb, pandas, numpy, psutil
```

Drop raw CSVs into `data/raw/{orders,trades,nbbo,session,reference,participants}/`
following `{ticker}_{date}_orders.csv`-style naming (bulk-mode also accepts
generic names).

### 7.2 Five-step run

```bash
# 1+2. Stage 1 (extract+partition) + Stage 2 (simulation, parallel per partition)
python process.py

# 3+4. Stage 3 (metrics) + Stage 4 (comparison)
python aggregate.py

# 5+6. Per-security CSVs + cross-cut rollups (real-only + comparison)
python report.py
```

Outputs land in `data/processed/`, `data/outputs/`, and `data/reports/`.

### 7.3 Optional filters

| Flag | Where it filters | Notes |
|---|---|---|
| `--dates 20240505,20240905` | post-read, against partition trade date | accepts `YYYYMMDD` or `YYYY-MM-DD` |
| `--tickers cba,bhp` | filename substring on raw inputs | only useful when filenames carry the ticker |
| `--orderbookids 85603,70616` | parquet predicate-pushdown at read time | always works; cheaper than tickers when the dataset is large |
| `--workers N` | Stage 2/3 parallelism | default = `config.MAX_PARALLEL_WORKERS` |

Note: `aggregate.py --orderbookids` is the post-Task-7 idiom (the legacy
`--tickers` is accepted but warns; partitions are keyed by orderbookid).

### 7.4 Tests

```bash
python -m pytest tests/ -v
```

Currently passing:
- `test_config_smoke.py` — config import + key-knob assertions.
- `test_dark_deduction.py` (5) — `_synthesize_orders_after` + `_filter_to_survivors`.
- `test_comparison_rollup.py` (8) — `_comparison_rollup` aggregation logic.

Currently failing (pre-existing):
- `test_parity.py::test_processed_parity` — baseline stale (§6.3.6).
- `test_parity.py::test_outputs_parity` — same.
- `test_simulator.py::test_zero_qty_sweep_gets_zero_fill_summary` — pre-existing.

---

## 8. Key knobs (`config.py`)

| Knob | Default | What it controls |
|---|---|---|
| `SWEEP_ORDER_TYPE` | `2048` | The order type that defines a "sweep". |
| `ELIGIBLE_MATCHING_ORDER_TYPES` | `{64, 256, 2048, 4096, 4098}` | Contra-pool order types. |
| `CENTRE_POINT_ORDER_TYPES` | `[64, 256, 2048, 4096, 4098]` | Same set, different shape (legacy). |
| `DARK_FILL_DEALSOURCES` | `frozenset({46, 47, 49, 50, 51, 52})` | Trades classified as CP-mediated dark fills. |
| `NBBO_SOURCE` | `'INTERNAL'` | INTERNAL reads from order columns; EXTERNAL path was dropped. |
| `MIN_ORDERS_THRESHOLD` / `MIN_TRADES_THRESHOLD` | 100 / 10 | Auto-discovery thresholds. |
| `ENABLE_PARALLEL_PROCESSING` | `True` | Stage 2/3 fan out across partitions. |
| `USE_POLARS_TRANSFORMS` | `False` | Polars in-memory transforms (preserved at False for parity baseline). |
| `PROCESSING_MODE` | `'file'` | `'file'` or `'memory'`. |
| `VOLUME_BUCKET_METHOD` | `'quartile'` | `'quartile'`, `'quintile'`, or `'custom'`. |
| `MAX_PARALLEL_WORKERS` | auto | Worker pool size; override with `--workers N`. |

---

## 9. Pitfalls & invariants

- **Trade date ≠ filename date.** Pipeline partitions by trade date derived
  from each row's UTC ns timestamp converted to AEST. `cba_20240505_orders.csv`
  may contain trade dates 2024-09-04 and 2024-09-05. CLI `--dates 20240505`
  filters on the filename label; `--dates 20240905` filters on the trade-date
  string inside the data. Pick deliberately.
- **Shared contra inventory across sweeps.** The `order_remaining` dict
  persists for the whole partition's sweep loop. Sweep processing order
  (by `effective_timestamp, sequence`) determines who gets each share.
- **MAQ asymmetry.** `continue` (skip this contra) vs `break` (stop this
  sweep) are not interchangeable — spec mandates the asymmetry.
- **NBBO sentinel `-9223372036854775808`** means "unavailable"; simulator
  falls back to the order's own `bid`/`offer`. If those are also invalid
  (≤ 0), the match is skipped.
- **APB bypasses NBBO entirely** — don't add an NBBO check to the APB
  branch (`bi.txt` §24.1.3).
- **`changereason == 5` does NOT cost priority by itself.** Only
  `cr ∈ {7, 8, 39}` or `orderbookposition > 0` shifts `effective_timestamp`
  to `timechanged`. cr=5 also fires on system-driven iceberg refreshes for
  passive sweeps (`bi.txt` §25.6.1) — not just user amendments.
- **`aggregate.py` no longer re-runs Stage 1.** Post-Task-7, it reads
  `process.py`'s outputs directly. Runs Stages 3+4 only.
- **Phase 2 (resting leg) was removed.** Simulator is active-window-only;
  unfilled remainder is not modelled as resting on either dark or lit.
- **Iceberg display-slice modelling was removed.** Full contra quantity is
  treated as visible (per `swp_cleaned_phase_2`).
- **Survivor filter for real metrics** (Task 7) restricts real-side
  aggregation to the same orderids the simulator runs on. Without it, real
  metrics ran on ~19k orders while sim ran on ~17k — broke comparison
  symmetry.

---

## 10. Where things live (file map)

| Concern | File | Function(s) |
|---|---|---|
| Raw → parquet | `convert_raw.py` (optional) | `convert`, `main` |
| Format-aware I/O | `process.py`, `aggregate.py` | `safe_read_csv`, `safe_write_csv` |
| Stage 1 ingest | `process.py` | `extract_orders`, `extract_trades`, `process_reference_data` |
| Sweep funnel | `process.py` | `_filter_sweep_orders_by_execution`, `_compute_rest_on_lit_qty`, `extract_last_execution_times` |
| Order state synthesis | `process.py` | `_synthesize_orders_after`, `get_orders_state` |
| Phantom-liquidity guards | `process.py` | `_build_contra_rest_on_lit`, `_build_contra_non_survivor_dark`, `build_contra_non_survivor_dark_files` |
| Simulator | `simulator.py` | `simulate_partition`, `_run_kernel`, `is_apb`, `check_crossing`, `_deal_source_for` |
| Survivor filter | `aggregate.py` | `_filter_to_survivors`, `load_last_execution_times` |
| Metrics | `aggregate.py` | `calculate_trade_metrics`, `_aggregate_simulated_trades_per_order`, `_process_partition_calculate_metrics` |
| Comparison | `aggregate.py` | `compare_real_vs_simulated_trades`, `run_stage_4_comparison` |
| Reports — real-only | `report.py` | `_read_all_partitions`, `_rollup`, `_write_per_security` |
| Reports — comparison | `report.py` | `_read_comparison_partitions`, `_comparison_rollup`, `_real_metric_columns` |
| CLI plumbing | `process.py`, `aggregate.py`, `report.py` | `main`, `cli_*` |

---

## 11. Recent change log (relevant to this evaluation)

| Commit | What | Effect |
|---|---|---|
| `9dd5ebd` | orders_after synth + survivor-slim last_execution_time + simulator window fixes | Foundation for the post-dark/pre-lit counterfactual model. |
| `4d9d3aa` → `8d7b89a` → `afb96cf` → `c343eee` | DARK_AT_SUBMISSION_DEALSOURCES introduction, broader filter, doc cleanup, dead code removal | Closes the "DS=47-only" partial-coverage gap in dark-at-submission deduction. |
| `8f64a15` → `a326d93` | `_filter_to_survivors` helper + wiring into `_process_partition_calculate_metrics` | Real metrics now run on the same population as the simulator (survivors). |
| `8133d69` | Rename → `DARK_FILL_DEALSOURCES`; same broadening applied to `_build_contra_non_survivor_dark` | Closes the same partial-coverage gap on the second phantom-liquidity guard. |
| `52e8bb2` → `100fb08` | `_comparison_rollup` + 8 unit tests + `report.py` wiring for `*_comparison.csv` outputs | The research-question signal is now visible in aggregate reports, not just in per-partition parquets. |

---

*Last updated: 2026-05-10 against branch `_parq` at HEAD `100fb08`.*
