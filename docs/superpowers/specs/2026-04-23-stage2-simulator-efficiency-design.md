# Stage 2 Simulator Efficiency — Design

**Status:** Approved
**Date:** 2026-04-23
**Scope:** `src/pipeline/sweep_simulator.py` → `src/pipeline/sweep_simulator/` (package)
**Target hardware:** 32-core server, large memory

---

## 1. Goal & non-goals

### Goal

Rewrite `pipeline/sweep_simulator.py` so that Phase 1 (`simulate_sweep_matching`) and Phase 2 (`simulate_resting_phase`) run ~10× faster on DRR/20240905 and scale to 100 GB / full-ASX multi-day runs without OOMing — while remaining semantically equivalent to the current simulator (same matched orderid pairs, match quantities, execution prices; `match_group_id` / tie-break order may differ within the execution-delay noise).

### Non-goals

- **Functionality remains intact** — every sweep that matches today still matches after the rewrite; every output file Stages 3–6 consume is still produced; every config flag (`NBBO_SOURCE`, `SIMULATE_RESTING_PHASE`, `RESTING_*`, `USE_POLARS_TRANSFORMS`, `USE_DUCKDB_IO`, `PROCESSING_MODE`, etc.) still behaves as documented.
- **No new language runtimes** — Polars + numpy + DuckDB only. No numba, Cython, Rust, pyo3.
- **No change to ASX CP matching semantics** — bi.txt/dd.txt rules and the invariants listed in CLAUDE.md are preserved.
- **No feature additions** — efficiency only.
- **No behavioural change to Stages 3–6** — their logic is untouched. They consume new/renamed columns via the `ColumnAccessor` mapping, but no analysis rules change.

### In-scope changes outside Stage 2

- **Stage 1** may emit Stage-2-optimised intermediates (Parquet, pre-sorted, int-typed, normalised schema). No change to what it ingests from raw CSVs.
- **`config/column_schema.py`** gets the new simulator column names plus aliases back to the originals so Stages 3–6 keep compiling.

---

## 2. Architecture

Three clean layers with explicit boundaries. `sweep_simulator.py` (1861 lines) becomes a package.

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer A — Data Prep (Polars)                                    │
│ pipeline/sweep_simulator/prep.py                                │
│  ─ load Parquet (Stage 1 output) via pl.scan_parquet            │
│  ─ compute effective_timestamp, lost_priority, iceberg flags    │
│  ─ sort by (effective_ts, sequence), partition by side          │
│  ─ extract columns to primitive numpy arrays                    │
│  ─ return a SimContext struct of arrays                         │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer B — Kernel (pure-Python over numpy arrays)                │
│ pipeline/sweep_simulator/kernel.py                              │
│  ─ Phase 1: iterate sweeps, walk contra heap, drain inventory   │
│  ─ Phase 2: resting leg (dark + optional lit), contra-centric   │
│  ─ All state in pre-allocated numpy arrays & primitives         │
│  ─ No pandas, no Polars, no dict-of-dict, no .iloc / .apply     │
│  ─ Writes match events into pre-allocated output buffers        │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer C — Output Assembly (Polars)                              │
│ pipeline/sweep_simulator/emit.py                                │
│  ─ wrap output arrays as a Polars DataFrame (zero-copy)         │
│  ─ apply new→legacy column mapping for downstream consumers     │
│  ─ write Parquet (primary) + optional CSV (compat flag)         │
└─────────────────────────────────────────────────────────────────┘
```

### Package layout

Consolidated per the "≤5 Python scripts per sprint" constraint. The three-layer architecture (prep / kernel / emit) is preserved via **sections inside `_rewrite.py`**, not filesystem boundaries. Class/function boundaries inside the file still enforce layer isolation.

```
src/pipeline/sweep_simulator/
    __init__.py       # public surface: re-exports from _legacy (now) and _rewrite (as sections land)
    _legacy.py        # the current monolithic simulator (renamed, 0-byte move)
    _rewrite.py       # all new code — SimContext, rules, prep, kernel, emit — in one file
                      # with section headers (Sprint 1 → Sprint 5) as it grows
```

Public entry points (`simulate_partition`, `simulate_sweep_matching`, `simulate_resting_phase`) are re-exported from `__init__.py` so existing callers are unchanged.

**Sprint-by-sprint section layout inside `_rewrite.py`:**

| Section | Sprint | Content |
|---|---|---|
| Sprint 1 — Foundations | 1 | `SimFlags`, `SimContext`, `Decision` enum, rule helpers (`check_maq`, `check_crossing`, `validate_price_limit`, `is_valid_session`, `iceberg_available`, `apply_midtick`, `is_apb`) |
| Sprint 2 — Prep | 2 | `build_sim_context`, `build_sim_context_from_parquet`, `build_sim_context_from_csv` |
| Sprint 3 — Phase 1 kernel | 3 | `run_phase1`, `_run_sweep` |
| Sprint 4 — Phase 2 kernel | 4 | `run_phase2` |
| Sprint 5 — Emit + mapping | 5 | `emit_trades`, `emit_summary`, `apply_legacy_aliases` |

Expected file size at Sprint 5: ~800–1200 lines. Still smaller than today's `_legacy.py` (1861 lines). If the file becomes unwieldy after the rewrite lands, decomposition into `prep.py`/`kernel.py`/`emit.py` is a mechanical post-rewrite refactor — each section is already class/function-scoped.

### SimContext — the boundary contract

A frozen dataclass holding numpy arrays plus scalars. The kernel sees nothing else.

```python
@dataclass(frozen=True, slots=True)
class SimContext:
    # Sweep arrays (length N_sweeps, chronologically sorted)
    sweep_orderid:       np.ndarray  # int64
    sweep_eff_ts:        np.ndarray  # int64
    sweep_side:          np.ndarray  # int8
    sweep_qty:           np.ndarray  # int64
    sweep_first_exec:    np.ndarray  # int64
    sweep_last_exec:     np.ndarray  # int64
    sweep_price:         np.ndarray  # int64
    sweep_maq:           np.ndarray  # int64
    sweep_sfmq:          np.ndarray  # int8
    sweep_crossingkey:   np.ndarray  # int64
    sweep_participant:   np.ndarray  # int32
    sweep_midtick:       np.ndarray  # int8
    sweep_orderbookid:   np.ndarray  # int32
    sweep_lost_priority: np.ndarray  # bool
    sweep_changereason:  np.ndarray  # int8

    # Contra arrays — same column set plus: display_qty, ordertype,
    # nbbo_bid, nbbo_offer, bid, offer
    contra_orderid:      np.ndarray
    contra_eff_ts:       np.ndarray
    # ... (see context.py)

    # Session state (pre-sorted by timestamp)
    session_ts:          np.ndarray  # int64
    session_state:       np.ndarray  # int8 enum (OPEN=1, CONTINUOUS=2, others=0)

    # NBBO (if EXTERNAL)
    nbbo_ts:             np.ndarray | None
    nbbo_bid:            np.ndarray | None
    nbbo_offer:          np.ndarray | None

    # Reference data
    tick_size:           int
    tick_size_table:     np.ndarray | None
    price_lower:         int
    price_upper:         int
    participants:        dict[int, str]   # lookup is rare — dict is fine

    # Runtime config snapshot
    cfg_flags:           SimFlags
```

Kernel runtime state (also arrays, no dicts):
- `contra_remaining: np.ndarray[int64]` indexed by contra row position.
- `iceberg_consumed: np.ndarray[int64]` indexed by contra row position.
- Heap entries: `(eff_ts, seq, counter, contra_idx)` — primitive ints only.
- Output buffers: pre-allocated numpy arrays, grow-with-doubling.

### Design rationale

- **Kernel has no framework dependency** — pure Python + numpy; unit-testable with synthetic arrays.
- **SimContext is the contract** — upstream changes (Parquet vs CSV, Polars vs DuckDB) only have to produce a `SimContext`. No ripple into the kernel.
- **Column mapping lives at the emit boundary only** — kernel operates on arrays by position. Renames are cheap.
- **File sizes stay sane** — kernel ~400–500 lines; prep and emit ~200 each; `rules.py` independently testable.

---

## 3. Data-prep layer (Layer A, Polars)

### Stage 1 contract

```
data/processed/{date}/{orderbookid}/
    orders_before_matching.parquet   # contra pool
    orders_after_matching.parquet    # sweep-candidate state
    last_execution_time.parquet      # orderid → first_exec_time, last_exec_time
    nbbo.parquet                     # optional, EXTERNAL NBBO source
    session_states.parquet           # timestamp, session_state (int enum)
    reference.parquet                # tick size, price limits, participants
```

Stage 1 guarantees (moved left of the hot path):

- **Schema normalised** — int-like columns stored as int64 (no float64 with NaN); `session_state` stored as int enum (not string); column names per the updated `COLUMN_MAPPING`.
- **Pre-sorted** — `orders_before_matching` and `orders_after_matching` sorted by `(effective_timestamp, sequence)`; `session_states` sorted by `timestamp`; `nbbo` sorted by `timestamp`.
- **Pre-computed derived fields** — `effective_timestamp`, `lost_priority`, `qualifies_for_sweep` (the 3-filter funnel pre-applied). Biggest I/O win — eliminates row-by-row recomputation.
- **Partitioned by `(date, orderbookid)`** — matches existing layout; enables `pl.scan_parquet(...).filter(...)` predicate pushdown.

### Prep flow

```python
def build_sim_context(partition_key: str, processed_dir: Path,
                      cfg_flags: SimFlags) -> SimContext:
    date, ob_id = partition_key.split('/')
    p = processed_dir / date / ob_id

    sweeps = (
        pl.scan_parquet(p / 'orders_after_matching.parquet')
          .filter(pl.col('exchangeordertype') == 2048)
          .filter(pl.col('qualifies_for_sweep'))
          .join(
              pl.scan_parquet(p / 'last_execution_time.parquet'),
              on='orderid', how='inner',
          )
          .sort(['effective_timestamp', 'sequence'])
          .collect()
    )

    contras = (
        pl.scan_parquet(p / 'orders_before_matching.parquet')
          .filter(pl.col('exchangeordertype').is_in(ELIGIBLE_MATCHING_ORDER_TYPES))
          .sort(['effective_timestamp', 'sequence'])
          .collect()
    )

    session = pl.scan_parquet(p / 'session_states.parquet').collect()
    nbbo    = _load_nbbo(p, cfg_flags.nbbo_source)
    ref     = _load_reference(p, ob_id)

    return SimContext(
        **_arrays_from(sweeps, SWEEP_COLS),
        **_arrays_from(contras, CONTRA_COLS),
        session_ts=session['timestamp'].to_numpy(zero_copy_only=True),
        session_state=session['session_state'].to_numpy(zero_copy_only=True),
        nbbo_ts=nbbo['timestamp'].to_numpy() if nbbo is not None else None,
        cfg_flags=cfg_flags,
        # ... see prep.py
    )
```

### Key design points

1. **`_arrays_from` is zero-copy.** Polars → numpy via `to_numpy(zero_copy_only=True)`.
2. **Lazy → single `.collect()` per frame.** Kernel's shared-state semantics require the whole partition in memory. Chunked streaming is a future optimisation.

### CSV-fallback (transition period)

Until Stage 1 emits Parquet universally:

```python
if cfg.STAGE1_EMITS_PARQUET:        # default False during transition
    ctx = build_sim_context_from_parquet(...)
else:
    ctx = build_sim_context_from_csv(...)   # reads current intermediates
```

CSV path deleted when Stage 1 migration completes.

---

## 4. Inner-loop kernel (Layer B)

Pure Python + numpy. Framework-free. Every rule in `rules.py` is a small pure function.

### Top-level flow — Phase 1

```python
def run_phase1(ctx: SimContext) -> Phase1Result:
    N_sweeps  = len(ctx.sweep_orderid)
    N_contras = len(ctx.contra_orderid)

    # State — preallocated arrays
    contra_remaining = ctx.contra_qty.copy()
    iceberg_consumed = np.zeros(N_contras, dtype=np.int64)

    # Output buffers — grow by doubling
    out = OutputBuffers.alloc(estimated_matches=N_sweeps * 4)
    summaries = SweepSummaryBuffers.alloc(N_sweeps)
    sweep_matched_qty = np.zeros(N_sweeps, dtype=np.int64)
    sweep_num_matches = np.zeros(N_sweeps, dtype=np.int32)

    counters = Counters(row=1, match=0,
                        base_matchgroupid=7904794000999000001)

    for s in range(N_sweeps):
        _run_sweep(s, ctx, contra_remaining, iceberg_consumed,
                   out, summaries, sweep_matched_qty, sweep_num_matches,
                   counters)

    return Phase1Result(
        simulated_trades=out.as_arrays(),
        order_summary=summaries.as_arrays(),
        sweep_usage=(sweep_matched_qty, sweep_num_matches),
        final_contra_remaining=contra_remaining,
        final_iceberg_consumed=iceberg_consumed,
    )
```

### Per-sweep work

```python
def _run_sweep(s, ctx, contra_remaining, iceberg_consumed, out, summaries, ...):
    # === VECTORISED: candidate filter ===
    lo = ctx.sweep_first_exec[s]
    hi = ctx.sweep_last_exec[s]
    mask = (
        (ctx.contra_eff_ts      >= lo) &
        (ctx.contra_eff_ts      <= hi) &
        (ctx.contra_orderid     != ctx.sweep_orderid[s]) &
        (ctx.contra_orderbookid == ctx.sweep_orderbookid[s]) &
        (ctx.contra_side        != ctx.sweep_side[s]) &
        (contra_remaining        > 0)
    )
    cand_idx = np.flatnonzero(mask)

    # === VECTORISED: session-state filter ===
    if ctx.session_ts is not None:
        state = _asof_lookup(ctx.session_ts, ctx.session_state,
                             ctx.contra_eff_ts[cand_idx])
        cand_idx = cand_idx[(state == SESSION_OPEN) | (state == SESSION_CONTINUOUS)]

    # === SEQUENTIAL: heap walk ===
    heap = _build_heap(ctx, cand_idx)
    sweep_remaining = ctx.sweep_qty[s]
    sweep_matched = sweep_matches = 0

    while heap and sweep_remaining > 0:
        eff_ts, seq, counter, c = heapq.heappop(heap)

        avail = contra_remaining[c]
        if avail <= 0:
            continue
        avail = min(avail, _iceberg_available(ctx, c, iceberg_consumed[c]))
        if avail <= 0:
            continue

        decision = _check_match(s, c, ctx, sweep_remaining, sweep_matched, avail)
        if decision is Decision.SKIP:
            continue
        if decision is Decision.BREAK:
            break

        px, match_type = _price_match(s, c, ctx)
        if px is None:
            continue

        qty = min(sweep_remaining, avail)
        match_ts = _apply_execution_delay(ctx.contra_eff_ts[c])

        out.append_match(sweep_idx=s, contra_idx=c, qty=qty, price=px,
                         match_type=match_type, match_ts=match_ts,
                         matchgroupid=...)

        sweep_remaining     -= qty
        contra_remaining[c] -= qty
        sweep_matched       += qty
        sweep_matches       += 1

        # Iceberg refresh — re-push onto heap, back of queue
        dq = ctx.contra_display_qty[c]
        if dq > 0:
            iceberg_consumed[c] += qty
            if iceberg_consumed[c] >= dq:
                iceberg_consumed[c] -= dq
                if contra_remaining[c] > 0:
                    heapq.heappush(heap, (ctx.contra_eff_ts[c],
                                          counters.next(), counters.next(),
                                          c))

    summaries.append(s, sweep_matched, sweep_matches, ...)
```

### Key rewrites from today

| Today | Replacement | Approx. perf win |
|---|---|---|
| `all_orders[mask].copy()` per sweep | `np.flatnonzero(mask)` — indices, no copy | ~100× |
| `eligible_orders.apply(_is_valid_trading_session, axis=1)` | `_asof_lookup` on sorted numpy arrays | ~1000× |
| `order.get('midtick', MIDTICK_NO)` per match | `ctx.contra_midtick[c]` | ~50× |
| `heapq.heappush((..., _o_row.to_dict(), …))` | `heapq.heappush((..., c))` | ~20× + much less memory |
| `pd.DataFrame(simulated_trades)` at end | Pre-allocated numpy arrays; one `pl.from_numpy` at the end | ~10× |
| `sweep_orders.iloc[idx]` per sweep | `ctx.sweep_*[s]` | ~50× |
| `order_remaining = {int(orderid): qty for ...}` | `contra_remaining = ctx.contra_qty.copy()` | O(N)→O(N), ~20× smaller constant |

### Rule helpers (`rules.py`)

Pure functions of primitives, independently testable:

```python
def check_maq(sweep_remaining, sweep_matched, sweep_maq, sweep_sfmq,
              potential_match_qty) -> Decision:
    if sweep_maq == 0:
        return Decision.OK
    if sweep_sfmq == 1:
        return Decision.SKIP if potential_match_qty < sweep_maq else Decision.OK
    if sweep_remaining < sweep_maq and sweep_matched > 0:
        return Decision.BREAK
    if potential_match_qty < sweep_maq and sweep_matched == 0:
        return Decision.SKIP
    return Decision.OK
```

Rules extracted: `check_maq`, `check_crossing`, `validate_price_limit`, `is_valid_session`, `iceberg_available`, `apply_midtick`, `is_apb`.

### What is NOT vectorised (and why that's OK)

- **Outer sweep loop** — shared `contra_remaining` across sweeps.
- **Inner heap walk** — MAQ early-break and iceberg reposition both depend on prior iteration's state.
- **Execution-delay RNG** — sequential for reproducibility.

Python loop overhead on ~10k iterations is manageable; current bottleneck is pandas row access, not Python loops. If post-rewrite profiling shows the Python loop is the bottleneck, that's when the numba conversation reopens — not pre-committed.

### Phase 2

Contra-centric outer loop (per bi.txt §24.10 preferencing rule); rule-gauntlet inner match; shared `sweep_state` arrays. Structure identical to Phase 1, inverted loop direction. Phase 2 lit leg does **not** reposition on iceberg refresh — intentional, per CLAUDE.md.

---

## 5. Output schema evolution + compatibility mapping

### 5.1 Column renames — `simulated_trades`

| Legacy name | New name | Legacy dtype | New dtype | Notes |
|---|---|---|---|---|
| `EXCHANGE` | `exchange_id` | int64 | int8 | constant 3 |
| `sequence` | `sequence` | int64 | int64 | kept |
| `tradedate` | `trade_date` | string | string | |
| `tradetime` | `match_ts` | int64 | int64 | ns epoch |
| `securitycode` | `orderbookid` | int64 | int32 | normalised to canonical |
| `orderid` | `orderid` | int64 | int64 | kept |
| `dealsource` | `dealsource` | int64 | int8 | ∈ {1, 46, 47, 50, 51} |
| `exchangeinfo` | — | string | — | **dropped** (always empty) |
| `matchgroupid` | `match_group_id` | int64 | int64 | kept |
| `nationalbidpricesnapshot` | `nbbo_bid` | int64 | int64 | kept |
| `nationalofferpricesnapshot` | `nbbo_offer` | int64 | int64 | kept |
| `tradeprice` | `match_price` | int64 | int64 | kept |
| `quantity` | `quantity` | int64 | int64 | kept |
| `side` | `side` | int64 | int8 | ∈ {1, 2} |
| `participantid` | `participant_id` | int64 | int32 | always 0 today |
| `passiveaggressive` | `is_aggressor` | int64 | int8 | 1=sweep leg, 0=contra leg |
| `row_num` | — | int64 | — | **dropped** — equals `sequence` |
| `match_type` | `match_type` | string | int8 | **enum** — SWEEP_TO_REGULAR=1, SWEEP_TO_SWEEP=2, BLOCK=3, BLOCK_PREF=4 |
| `contra_participant_type` | `contra_participant_type` | string | string | kept |

### 5.2 Column renames — `order_summary`

| Legacy | New |
|---|---|
| `timestamp` | `submit_ts` |
| `quantity` | `original_qty` |
| `matched_quantity` | `matched_qty` |
| `remaining_quantity` | `remaining_qty` |
| `fill_ratio` | `fill_ratio` |
| `num_matches` | `num_matches` (int32) |
| `lost_priority` | `lost_priority` (int8) |
| others | unchanged |

`sweep_utilization` is unchanged.

### 5.3 Compatibility mapping

Single source of truth: `config/column_schema.py`.

```python
SIMULATED_TRADES_ALIASES = {
    'EXCHANGE':                   'exchange_id',
    'tradedate':                  'trade_date',
    'tradetime':                  'match_ts',
    'securitycode':               'orderbookid',
    'matchgroupid':               'match_group_id',
    'nationalbidpricesnapshot':   'nbbo_bid',
    'nationalofferpricesnapshot': 'nbbo_offer',
    'tradeprice':                 'match_price',
    'participantid':              'participant_id',
    'passiveaggressive':          'is_aggressor',
}

MATCH_TYPE_ENUM = {'SWEEP_TO_REGULAR': 1, 'SWEEP_TO_SWEEP': 2,
                   'BLOCK': 3, 'BLOCK_PREF': 4}
```

### 5.4 Rollout — Option Y (ColumnAccessor-driven migration)

1. Add new names + aliases to `COLUMN_MAPPING`. Old `col.trades.tradetime` keeps resolving.
2. `grep` and replace string literals with `col.sim_trades.*`.
3. New simulator writes Parquet with new names only. Transitional readers use a shim:

```python
def load_simulation_trades(path: Path, legacy_names: bool = True) -> pl.DataFrame:
    df = pl.read_parquet(path)
    if legacy_names and _is_new_schema(df):
        return df.rename(_invert(SIMULATED_TRADES_ALIASES))
    return df
```

During transition, `legacy_names=True` default. After all downstream readers are migrated, flip to `False` and drop the shim.

### 5.5 Discoverability

`config/column_schema.py` gains a top-of-file comment referencing this spec and the migration checklist (tracked in the implementation plan — produced by writing-plans next).

---

## 6. Deferred DuckDB-staging feature flag (Approach 2 promotion)

### 6.1 When to build

Only if post-Approach-1 benchmarks show:
- **Peak RSS > 75% of memory** on biggest target run with full parallelism, OR
- **Prep-layer time > 30% of total Stage 2 wall-clock**, OR
- **Explicit scale requirement** emerges (e.g. 200 tickers × 1 month in one process).

On the 32-core / large-memory target hardware, these triggers are unlikely to fire. Approach 2 is documented as an exit door, not a planned deliverable.

### 6.2 The flag

```python
# config/config.py
USE_DUCKDB_CONTRA_POOL = False     # default — Polars scan_parquet
```

When `True`, `prep.py` switches contra-pool loading to:

```python
def _load_contras_duckdb(p: Path, cfg_flags: SimFlags) -> pl.DataFrame:
    conn = io_backend.get_conn()
    parquet_path = p / 'orders_before_matching.parquet'
    return conn.execute(f"""
        SELECT {','.join(CONTRA_COLS)}
        FROM parquet_scan('{parquet_path}')
        WHERE exchangeordertype IN (64, 256, 2048, 4096, 4098)
        ORDER BY effective_timestamp, sequence
    """).pl()
```

Kernel untouched. `build_sim_context` has two producers and picks one:

```python
contras = (_load_contras_duckdb(p, cfg_flags)
           if cfg_flags.use_duckdb_contra_pool
           else _load_contras_polars(p, cfg_flags))
```

### 6.3 Not in this design

- Per-sweep DuckDB queries (10³–10⁴ per partition) — rejected risk.
- Arrow-batch streaming of per-sweep `cand_idx` — deferred to a future design if the simple flag proves insufficient.

### 6.4 Promotion checklist

If/when triggered:
- `prep.py` gains `_load_contras_duckdb`.
- `config.py` gains the flag (off by default).
- Benchmark harness re-runs with flag on/off to prove the trade.
- No other files change.

---

## 7. Correctness & regression testing

### 7.1 Dual-run parity harness

Lives in `tests/regression/test_simulator_parity.py`. For each baseline partition: runs legacy + new simulators, diffs outputs.

```python
def test_parity(partition_key):
    ctx = build_sim_context(partition_key)
    old_result = legacy_simulate_partition(partition_key)
    new_result = new_simulate_partition(ctx)
    diff = diff_simulations(old_result, new_result)
    assert diff.semantically_equivalent, diff.report()
```

### 7.2 Field-by-field diff rules

| Field | Rule | Rationale |
|---|---|---|
| `(sweep_orderid, contra_orderid, match_price, quantity)` tuple | **Bit-identical set** | Core invariant |
| `dealsource`, `match_type`, `side`, `is_aggressor` per match | **Bit-identical** | Same match semantics |
| `nbbo_bid`, `nbbo_offer` per match | **Bit-identical** | Same NBBO at match time |
| `match_ts` | **±execution_delay jitter** (std × 3 σ) | RNG, independent |
| `match_group_id` | **Structurally equivalent** — same groups, ID numbering may differ | Global counter |
| `sequence`, `row_num` | **Not compared** | Reordering within a match allowed |
| `order_summary.matched_qty`, `fill_ratio`, `num_matches` per orderid | **Bit-identical** | Aggregate invariants |
| `sweep_usage` | **Bit-identical** | Same |

Any diff outside these rules → test fails with structured report (first 20 mismatching orderids, category summary).

### 7.3 Baseline partitions

- `20240905/drr` — documented smoke-test target.
- 3 more from auto-discovery: one tiny (<100 orders), one medium (~1k sweeps), one heavy (>10k sweeps if available in dataset).
- Baselines captured once from current simulator, stored as Parquet fixtures under `tests/regression/baselines/`.

### 7.4 Unit tests (`rules.py`)

Table-driven tests for every rule helper. MAQ `BREAK` vs `SKIP` asymmetry gets dedicated cases (the CLAUDE.md-flagged regression hazard). Coverage targets: `rules.py` 100% branch; `kernel.py` 90%+ line.

### 7.5 Performance regression test

Benchmark on 4 baseline partitions must stay within **1.2× of the approved rewrite's measured time**. Catches silent regressions in future PRs.

---

## 8. Rollout & benchmarks

### 8.1 Phased merge

1. **Stage 1 Parquet contract** — land Stage 1 changes that emit pre-sorted, pre-typed, derived-column Parquet. Dual-write with legacy CSV for one PR cycle.
2. **New simulator package** behind `USE_NEW_SIMULATOR = False`. Opt-in for dev/testing.
3. **Dual-run enforcement** — CI runs both simulators on 4 baseline partitions; new must pass all parity + perf tests before default flip.
4. **Flip default** to `USE_NEW_SIMULATOR = True`. Legacy retained one release as fallback.
5. **Delete legacy** after one release with no parity failures. CSV-fallback prep path removed.
6. **Column-alias cleanup** — once all Stage 3–6 readers use `col.sim_trades.*`, drop shim in `load_simulation_trades` and `SIMULATED_TRADES_ALIASES`.

### 8.2 Benchmark matrix (32-core server)

| Run | Partitions | Workers | What we measure |
|---|---|---|---|
| B1 | 1 (DRR/20240905) | 1 | Single-partition wall-clock; baseline vs new |
| B2 | 20 tickers × 1 day | 20 | Per-partition throughput under light parallelism |
| B3 | 20 tickers × 1 day | 32 | Core utilisation at saturation |
| B4 | 50 tickers × 5 days | 32 | Realistic multi-day scaling |
| B5 | 1 ticker × 20 days | 32 | Cross-partition concurrency on one security |

### 8.3 Success targets

- **B1**: ≥ 8× wall-clock improvement.
- **B3 / B4**: ≥ 90% core utilisation; total wall-clock scales ~linearly with partition count until core saturation.
- **B4**: end-to-end pipeline runtime target set once B1 numbers are in.

### 8.4 Profiling artefacts

Each benchmark captures `py-spy` flame graph + peak RSS. Stored under `docs/superpowers/benchmarks/YYYY-MM-DD/`.

### 8.5 Parallelism defaults

- `ENABLE_PARALLEL_PROCESSING = True` for multi-security runs on target hardware.
- `MAX_PARALLEL_WORKERS` = `cpu_count()` (32 on dedicated server).
- CLI `--max-workers` retained for override.

---

## 9. Open questions

None at design approval. Next step: implementation plan via `superpowers:writing-plans`.
