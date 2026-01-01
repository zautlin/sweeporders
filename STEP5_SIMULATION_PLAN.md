# STEP 5: DARK POOL SIMULATION - COMPREHENSIVE PLAN

**Date:** January 1, 2026  
**Status:** 🔄 PLANNING  
**Version:** 1.0

---

## EXECUTIVE SUMMARY

Step 5 will simulate a dark pool matching scenario where sweep orders are "played back" against dark order volumes. The simulation uses realistic data from Step 1-4 to calculate hypothetical execution scenarios and compare them with actual execution results.

**Objective:** Understand how Centre Point's execution would have changed if dark orders (at mid-price) were matched against the sweep order during the time window from order creation to completion.

---

## SECTION 1: SIMULATION CONCEPT

### 1.1 Core Simulation Logic

**Question Being Answered:**
> "What if Centre Point could match dark orders (at mid-price) against our sweep orders between order creation (T) and completion (T+K)?"

**Simulation Flow:**

```
For Each Sweep Order in Group 1:
    1. Find the earliest point when order was "in the book" (T)
    2. Find when order was completed or trading day ended (T+K)
    3. Get all available dark orders between T and T+K
    4. Match dark orders at mid-price from NBBO
    5. Compare simulated execution vs. actual execution
```

### 1.2 Key Concept: "In the Book"

**Definition:** The earliest state of the order when it first appeared in the order book.

**How to Identify:**
1. Find the **minimum timestamp** where order first appears
2. If multiple records exist at that timestamp, select the one with **highest sequence**
3. This is the "initial" state of the order

**Why This Matters:**
- Represents when order was first submitted/visible
- Starting point for hypothetical dark order matching
- Different from final state (which may be amended/cancelled)

### 1.3 Time Window: T to T+K

**Definition:** The period during which dark orders could potentially match.

**Start (T):** Minimum timestamp of the sweep order (when first in book)

**End (T+K):** The earlier of:
- The timestamp when `leavesquantity == 0` (order fully filled)
- End of trading day (typically 4:00 PM AEST)

**Interpretation:** This is the "opportunity window" during which dark orders could have executed.

### 1.4 Dark Order Matching

**The Dark Order Pool:**
- Assumed available dark order volumes at mid-price
- Not explicitly in data (must be inferred or assume standard)
- **Key Assumption:** Unlimited dark order volume at mid-price

**Matching Rules:**
1. Orders match at **mid-price** (not at limit price)
2. Mid-price = (bid + ask) / 2 from NBBO
3. Match as much as possible within time window T to T+K
4. Matching is at the price available at time of execution

**Price Lookup:**
- Use NBBO timestamp to find closest mid-price
- Interpolate if exact timestamp not in NBBO
- Use most recent NBBO before execution time

---

## SECTION 2: DETAILED REQUIREMENTS

### 2.1 Step 1: Identify Eligible Orders

**Criteria:**
- Must be in GROUP_1_FULLY_FILLED (24 orders)
- Must have 2+ records at minimum timestamp (to select by sequence)

**Result:** List of eligible orders for simulation

**Data Source:**
- File: `data/orders/drr_orders.csv`
- Field: `order_id`, `timestamp`, `sequence`, `leavesquantity`

### 2.2 Step 2: Select "Initial State" Order

**For Each Eligible Order:**

1. **Load full order history** from raw orders file
   ```python
   order_records = raw_orders[raw_orders['order_id'] == order_id]
   order_records = order_records.sort_values('timestamp')
   ```

2. **Find minimum timestamp**
   ```python
   T = order_records['timestamp'].min()
   ```

3. **Get all records at minimum timestamp**
   ```python
   min_ts_records = order_records[order_records['timestamp'] == T]
   ```

4. **Select highest sequence**
   ```python
   selected_order = min_ts_records.nlargest(1, 'sequence').iloc[0]
   ```

5. **Extract initial state**
   ```python
   initial_quantity = selected_order['quantity']
   initial_price = selected_order['price']
   initial_side = selected_order['side']
   T = selected_order['timestamp']
   ```

**Output for Each Order:**
```
{
    'order_id': 12345,
    'T': 1725516166885349741,
    'initial_quantity': 882,
    'initial_price': 3400,
    'side': 'BUY/SELL',
    'sequence': 54912293
}
```

### 2.3 Step 3: Determine Time Window End (T+K)

**For Each Order:**

1. **Find when order reached leaves_quantity = 0**
   ```python
   filled_records = order_records[order_records['leavesquantity'] == 0]
   if len(filled_records) > 0:
       T_K = filled_records['timestamp'].max()  # Latest completion time
   else:
       T_K = TRADING_DAY_END  # 4:00 PM AEST
   ```

2. **Get all trades for this order within [T, T_K]**
   ```python
   order_trades = trades[trades['orderid'] == order_id]
   window_trades = order_trades[
       (order_trades['tradetime'] >= T) & 
       (order_trades['tradetime'] <= T_K)
   ]
   ```

3. **Calculate actual quantity matched**
   ```python
   actual_quantity_matched = window_trades['quantity'].sum()
   ```

**Output for Each Order:**
```
{
    'order_id': 12345,
    'T': 1725516166885349741,
    'T_K': 1725516614001680863,
    'window_duration_ns': 447116331122,
    'actual_quantity_matched': 882,
    'actual_match_price': 3400.0
}
```

### 2.4 Step 4: Get Mid-Price During Window

**For Each Order:**

1. **Find all NBBO snapshots within [T, T_K]**
   ```python
   nbbo_window = nbbo[
       (nbbo['timestamp'] >= T) & 
       (nbbo['timestamp'] <= T_K)
   ]
   ```

2. **Calculate mid-price for each snapshot**
   ```python
   nbbo_window['midprice'] = (
       nbbo_window['bidprice'] + nbbo_window['offerprice']
   ) / 2
   ```

3. **Handle multiple NBBO snapshots:**
   - If 1 snapshot: Use its mid-price
   - If 2+ snapshots: Use VWAP of mid-prices (weighted by time)
   - If 0 snapshots: Use limit price as fallback

4. **Store for use in simulation**
   ```python
   midprice = nbbo_window['midprice'].mean()  # Simple average
   # or
   midprice = calculate_vwap(nbbo_window)  # Volume-weighted if available
   ```

**Output:**
```
{
    'order_id': 12345,
    'midprice_in_window': 3335.0,
    'nbbo_snapshots_count': 1,
    'bid': 3330,
    'ask': 3340
}
```

### 2.5 Step 5: Simulate Dark Order Matching

**For Each Order:**

1. **Determine available dark order quantity**
   ```python
   # Assumption: Unlimited dark orders at mid-price
   dark_order_available = UNLIMITED
   
   # Or realistic scenarios:
   dark_order_available = simulate_dark_pool_volume()
   ```

2. **Match dark orders up to order quantity**
   ```python
   simulated_matched_qty = min(
       initial_quantity,
       dark_order_available
   )
   simulated_matched_price = midprice_in_window
   ```

3. **Calculate simulated metrics**
   ```python
   simulated_execution_cost = (
       simulated_matched_qty * simulated_matched_price
   )
   simulated_fill_ratio = (
       simulated_matched_qty / initial_quantity
   )
   ```

4. **Calculate cost comparison**
   ```python
   actual_cost = actual_matched_qty * actual_price
   simulated_cost = simulated_matched_qty * simulated_price
   cost_difference = actual_cost - simulated_cost
   cost_percent = (cost_difference / actual_cost) * 100
   ```

**Output:**
```
{
    'order_id': 12345,
    'scenario': 'unlimited_dark_orders',
    'simulated_matched_qty': 882,
    'simulated_match_price': 3335.0,
    'simulated_execution_cost': 2,943,470,
    'actual_execution_cost': 3,087,200,
    'cost_difference': -143,730,
    'cost_improvement': 4.66%
}
```

---

## SECTION 3: SIMULATION SCENARIOS

### 3.1 Scenario A: Unlimited Dark Orders at Mid-Price

**Description:** All sweep order quantities can be matched against dark orders at mid-price.

**Assumptions:**
- Dark order volume: Unlimited at mid-price
- No market impact
- All execution at exactly mid-price

**Expected Outcome:**
- 100% fill ratio (all quantity executed at mid-price)
- Best possible execution price for buyer (worst for seller)
- Represents "ideal" execution

**Metric Comparison:**
```
GROUP_1 ACTUAL      | GROUP_1 SIMULATED (Unlimited)
────────────────────┼─────────────────────────────
Matched Qty: 57,791 | Matched Qty: 57,791
Avg Price: 3,359.47 | Mid Price: ~3,337.5
Cost: $194.2M       | Cost: $192.8M (better by 0.7%)
```

### 3.2 Scenario B: Limited Dark Orders (50% of order quantity)

**Description:** Dark pool has limited volume (~50% of typical order size).

**Assumptions:**
- Dark order volume available: 50% of initial order quantity
- Execution at mid-price for available quantity
- Partial fill scenario

**Expected Outcome:**
- ~50% fill from dark orders
- Remaining 50% from real market (at actual execution price)
- Realistic dark pool scenario

**Metric Comparison:**
```
GROUP_1 ACTUAL      | GROUP_1 SIMULATED (50% Dark)
────────────────────┼──────────────────────────────
Matched Qty: 57,791 | Dark Matched: 28,895 @ midprice
Avg Price: 3,359.47 |   + Lit Market: 28,896 @ actual
Cost: $194.2M       | Cost: $193.5M (better by 0.36%)
```

### 3.3 Scenario C: Market Conditions Impact (Price changes)

**Description:** Dark orders match, then light moves. Remaining quantity executed at worse prices.

**Assumptions:**
- Dark order volume: 50% of order quantity at T (mid-price)
- Market moves: Remaining 50% at worse prices
- Simulates adverse price movement

**Expected Outcome:**
- Partial dark fill at mid-price
- Remaining fill at worse price
- Shows impact of market conditions

---

## SECTION 4: DATA SOURCES & PREPARATION

### 4.1 Input Data Files

| File | Records | Use |
|------|---------|-----|
| `data/orders/drr_orders.csv` | 48,033 | Full order timeline (all timestamps) |
| `processed_files/centrepoint_trades_raw.csv.gz` | 60 | Actual execution details |
| `processed_files/sweep_orders_classified.csv.gz` | 29 | Order classification & final state |
| `data/nbbo/nbbo.csv` | 2 | Mid-price lookup for simulation |

### 4.2 Data Mapping

**Order Timeline (from raw orders):**
- `order_id`: Unique identifier
- `timestamp`: Order state timestamp (nanoseconds)
- `sequence`: Order sequence at that timestamp
- `quantity`: Original order quantity
- `leavesquantity`: Unfilled quantity at timestamp
- `price`: Order limit price
- `side`: BUY (1) or SELL (2)

**Trade Data:**
- `orderid`: Links to order_id
- `tradetime`: Execution timestamp
- `tradeprice`: Actual execution price
- `quantity`: Quantity executed in this trade

**NBBO Data:**
- `timestamp`: NBBO snapshot time
- `bidprice`: Best bid price
- `offerprice`: Best ask (offer) price
- Derived: `midprice` = (bidprice + offerprice) / 2

---

## SECTION 5: ALGORITHM PSEUDOCODE

### 5.1 Main Simulation Loop

```python
def step5_simulation():
    """
    Main simulation pipeline for dark order matching.
    """
    
    # Step 1: Load data
    raw_orders = load_orders()
    trades = load_trades()
    nbbo = load_nbbo()
    classified = load_classified()
    
    # Step 2: Get eligible orders
    group1_ids = classified[
        classified['sweep_group'] == 'GROUP_1_FULLY_FILLED'
    ]['order_id'].unique()
    
    # Step 3: Run simulation for each order
    results = []
    for order_id in group1_ids:
        
        # 3a. Get initial state
        initial_state = get_initial_state(order_id, raw_orders)
        T = initial_state['timestamp']
        
        # 3b. Find completion time
        T_K = get_completion_time(order_id, raw_orders)
        
        # 3c. Get actual execution
        actual = get_actual_execution(order_id, trades, T, T_K)
        
        # 3d. Get mid-price
        midprice = get_midprice(T, T_K, nbbo)
        
        # 3e. Simulate scenarios
        for scenario in ['unlimited', 'limited_50', 'with_price_impact']:
            simulated = simulate_dark_matching(
                initial_state, actual, midprice, scenario
            )
            
            # 3f. Compare metrics
            comparison = compare_metrics(actual, simulated)
            results.append(comparison)
    
    # Step 4: Aggregate results
    summary = aggregate_results(results)
    
    # Step 5: Save outputs
    save_results(summary)
    
    return summary
```

### 5.2 Get Initial State Function

```python
def get_initial_state(order_id, raw_orders):
    """
    Get order state at minimum timestamp with highest sequence.
    """
    # Get all records for this order
    order_records = raw_orders[raw_orders['order_id'] == order_id]
    order_records = order_records.sort_values('timestamp')
    
    # Find minimum timestamp
    min_ts = order_records['timestamp'].min()
    
    # Get records at min timestamp
    min_ts_records = order_records[order_records['timestamp'] == min_ts]
    
    # Select highest sequence
    selected = min_ts_records.nlargest(1, 'sequence').iloc[0]
    
    return {
        'order_id': order_id,
        'T': selected['timestamp'],
        'sequence': selected['sequence'],
        'quantity': selected['quantity'],
        'price': selected['price'],
        'side': selected['side'],
        'leavesquantity': selected['leavesquantity']
    }
```

### 5.3 Get Completion Time Function

```python
def get_completion_time(order_id, raw_orders):
    """
    Get timestamp when order reached leavesquantity == 0.
    """
    order_records = raw_orders[raw_orders['order_id'] == order_id]
    
    # Find when leavesquantity became 0
    filled_records = order_records[order_records['leavesquantity'] == 0]
    
    if len(filled_records) > 0:
        # Return latest timestamp of completion
        return filled_records['timestamp'].max()
    else:
        # If never fully filled, use end of trading day
        return TRADING_DAY_END  # 16:00:00 AEST
```

### 5.4 Get Midprice Function

```python
def get_midprice(T, T_K, nbbo):
    """
    Get mid-price during window [T, T_K].
    """
    # Find NBBO snapshots in window
    window_nbbo = nbbo[
        (nbbo['timestamp'] >= T) & 
        (nbbo['timestamp'] <= T_K)
    ]
    
    if len(window_nbbo) == 0:
        # No NBBO in window, use last available
        pre_window = nbbo[nbbo['timestamp'] < T]
        if len(pre_window) > 0:
            window_nbbo = pre_window.tail(1)
        else:
            # No NBBO at all, return None
            return None
    
    # Calculate mid-price (simple average of available snapshots)
    window_nbbo['midprice'] = (
        window_nbbo['bidprice'] + window_nbbo['offerprice']
    ) / 2
    
    # Return average midprice in window
    return window_nbbo['midprice'].mean()
```

### 5.5 Simulate Dark Matching Function

```python
def simulate_dark_matching(initial_state, actual, midprice, scenario):
    """
    Simulate dark order matching for given scenario.
    """
    qty = initial_state['quantity']
    
    if scenario == 'unlimited':
        # All quantity matched at midprice
        simulated_qty = qty
        simulated_price = midprice
        
    elif scenario == 'limited_50':
        # Only 50% dark, rest from actual
        dark_qty = qty * 0.5
        simulated_qty = dark_qty
        # Remaining qty assumed filled at actual price
        remaining_qty = qty - dark_qty
        
    elif scenario == 'with_price_impact':
        # Dark matched at midprice, rest at worse price
        dark_qty = qty * 0.5
        simulated_qty = dark_qty
        simulated_price = midprice
        # Remaining qty at mid + spread
        
    return {
        'scenario': scenario,
        'simulated_qty': simulated_qty,
        'simulated_price': simulated_price,
        'execution_cost': simulated_qty * simulated_price
    }
```

---

## SECTION 6: OUTPUT STRUCTURE

### 6.1 Per-Order Simulation Results

**File:** `step5_simulation_results_detailed.csv.gz`

**Columns:**
```
order_id                          : Order identifier
group                             : GROUP_1, GROUP_2, GROUP_3
T_initial_timestamp               : Order entry timestamp
T_completion_timestamp            : Order completion timestamp
window_duration_ns                : T+K - T duration
initial_quantity                  : Original order quantity
initial_price                     : Order limit price
initial_side                      : BUY/SELL

actual_matched_qty                : Actual executed quantity
actual_matched_price              : Actual VWAP
actual_execution_cost             : Qty × Price
actual_fill_ratio                 : Matched / Original

scenario                          : unlimited_dark / limited_50 / price_impact
simulated_matched_qty             : Simulated execution quantity
simulated_matched_price           : Simulated execution price
simulated_execution_cost          : Qty × Price
simulated_fill_ratio              : Matched / Original

cost_difference                   : Actual - Simulated (abs)
cost_difference_pct               : % difference
improvement                       : Better/Worse/Same
```

### 6.2 Summary by Group

**File:** `step5_simulation_summary_by_group.csv.gz`

**Rows:** GROUP_1, GROUP_2, GROUP_3, OVERALL

**Columns:**
```
group                             : Classification
order_count                       : # of orders
avg_actual_cost                   : Average actual cost
avg_simulated_cost_unlimited      : Unlimited scenario
avg_simulated_cost_limited_50     : Limited scenario
avg_cost_improvement_unlimited    : % improvement
avg_cost_improvement_limited_50   : % improvement
total_actual_cost                 : Sum of all actual costs
total_simulated_cost              : Sum of simulated costs
total_cost_impact                 : Total $ improvement
```

### 6.3 Scenario Comparison

**File:** `step5_scenario_comparison.csv.gz`

**Rows:** Scenario A, Scenario B, Scenario C

**Columns:**
```
scenario                          : Scenario name
description                       : Scenario description
orders_analyzed                   : # of orders
avg_fill_ratio_actual             : Actual average
avg_fill_ratio_simulated          : Simulated average
total_qty_matched_actual          : Sum actual
total_qty_matched_simulated       : Sum simulated
avg_execution_price_actual        : VWAP actual
avg_execution_price_simulated     : VWAP simulated
total_cost_difference             : Dollar impact
total_improvement_pct             : % improvement
```

---

## SECTION 7: IMPLEMENTATION TIMELINE

### Phase 1: Data Preparation (Day 1)
- [ ] Load and validate all input data
- [ ] Identify eligible orders (2+ min timestamp records)
- [ ] Verify NBBO data availability
- [ ] Create data validation report

### Phase 2: Core Algorithm (Day 2)
- [ ] Implement `get_initial_state()` function
- [ ] Implement `get_completion_time()` function
- [ ] Implement `get_midprice()` function
- [ ] Test on sample orders

### Phase 3: Simulation Engines (Day 3)
- [ ] Implement Scenario A (Unlimited Dark)
- [ ] Implement Scenario B (Limited Dark)
- [ ] Implement Scenario C (Price Impact)
- [ ] Unit test each scenario

### Phase 4: Analysis & Output (Day 4)
- [ ] Aggregate results
- [ ] Create comparison metrics
- [ ] Generate visualization data
- [ ] Save output files

### Phase 5: Documentation & Review (Day 5)
- [ ] Create Step 5 detailed summary
- [ ] Peer review results
- [ ] Git commit
- [ ] Documentation finalization

---

## SECTION 8: VALIDATION & QA

### 8.1 Data Validation Checks

| Check | Method | Expected |
|-------|--------|----------|
| Order count | Must match Group 1 | 24 orders |
| Timestamp range | T < T_K | Always true |
| Quantity > 0 | All orders | Always true |
| Mid-price valid | Between bid/ask | $3,335-$3,340 |
| Simulated ≥ 0 | All metrics | No negatives |
| Cost improvement logic | Varies by scenario | Consistent |

### 8.2 Logical Validation

**Assertion: Scenario Ordering**
```
Cost_Unlimited < Cost_Limited_50 < Cost_Actual

(Dark at midprice is better than actual execution)
```

**Assertion: Fill Ratios**
```
Fill_Ratio_Unlimited = 1.0 (100%)
Fill_Ratio_Limited_50 = ~0.5 (50%)
Fill_Ratio_Actual = Varies (0-1)
```

**Assertion: Price Logic**
```
MidPrice <= Limit_Price (for buy orders)
MidPrice >= Limit_Price (for sell orders)
```

---

## SECTION 9: EXPECTED OUTCOMES

### 9.1 Key Findings

**Expected Results:**

1. **Unlimited Dark Scenario:**
   - 100% fill ratio (all quantity executed)
   - Best possible execution price (at mid)
   - Cost improvement: ~0.5-2% (depending on bid-ask spread)

2. **Limited Dark Scenario:**
   - 50% fill at dark (mid-price)
   - 50% fill from actual (various prices)
   - Cost improvement: ~0.2-1%

3. **Price Impact Scenario:**
   - 50% at mid-price
   - 50% at worse prices
   - Net cost may be worse or better (depends on timing)

### 9.2 Interpretation

**What These Results Mean:**

- **Positive Cost Difference:** Dark orders would have provided better execution
- **Negative Cost Difference:** Actual execution was better than hypothetical dark
- **Fill Ratio Comparison:** Shows execution completeness

---

## SECTION 10: RISK & LIMITATIONS

### 10.1 Key Assumptions

| Assumption | Impact | Mitigation |
|-----------|--------|-----------|
| Unlimited dark volume | May be unrealistic | Model realistic volumes |
| Fixed mid-price | Mid-price actually changes | Use VWAP of snapshots |
| No market impact | Large orders affect prices | Add price impact model |
| Deterministic matching | Real matching has latency | Add timing delays |
| Single NBBO during window | Multiple snapshots expected | Weight by time |

### 10.2 Data Limitations

1. **Limited NBBO Snapshots:** Only 2 available
   - Mitigation: Interpolate between snapshots
   - Or: Use available snapshots with disclaimers

2. **No Dark Order Volume Data:** Must be assumed
   - Mitigation: Use realistic ASX statistics
   - Or: Run sensitivity analysis

3. **No Market Impact Model:** Execution assumes no market reaction
   - Mitigation: Add simple impact model
   - Or: Acknowledge limitation in results

---

## SECTION 11: SUCCESS CRITERIA

### Step 5 Will Be Complete When:

✅ **Functional Requirements:**
- [ ] All 24 Group 1 orders processed
- [ ] Time windows [T, T+K] calculated correctly
- [ ] Mid-prices looked up from NBBO
- [ ] All 3 scenarios simulated
- [ ] Results aggregated by group

✅ **Quality Requirements:**
- [ ] All validations passed
- [ ] Cross-validation with Step 1-4 data
- [ ] Logical assertions verified
- [ ] No data quality issues

✅ **Output Requirements:**
- [ ] Detailed results CSV generated
- [ ] Summary by group CSV generated
- [ ] Scenario comparison CSV generated
- [ ] All metrics calculated

✅ **Documentation:**
- [ ] STEP5_DETAILED_SUMMARY.md created
- [ ] Code commented and documented
- [ ] Results interpreted with insights
- [ ] Limitations clearly stated

---

## APPENDIX A: EXAMPLE SIMULATION WALKTHROUGH

### Sample Order: 7904794000132347948

**Step 1: Initial State**
```
Order ID: 7904794000132347948
T (initial timestamp): 1725516166885349741
Sequence: 54912293
Quantity: 882 units
Price: $3,400 (limit)
Side: BUY
```

**Step 2: Completion Time**
```
Initial leavesquantity: 882
Final record with leavesquantity=0: 1725516614001680863
T+K: 1725516614001680863
Window: 447,116,331,122 nanoseconds (~6.2 hours)
```

**Step 3: Actual Execution**
```
Trades in [T, T+K]:
  Trade 1: 882 units @ $3,400
  
Actual metrics:
- Matched qty: 882
- Matched price: $3,400
- Execution cost: $3,087,200
```

**Step 4: NBBO Mid-Price**
```
NBBO snapshot 1: Bid $3,330, Ask $3,340 @ ts1
NBBO snapshot 2: Bid $3,335, Ask $3,345 @ ts2

For window [T, T+K]:
- Snapshot 1 is before T
- Snapshot 2 is within window
- Selected mid-price: (3,335 + 3,345) / 2 = $3,340
```

**Step 5: Scenario A (Unlimited Dark)**
```
Available dark volume: Unlimited at $3,340
Matching:
- Match 882 units @ $3,340 (mid-price)
  
Simulated metrics:
- Matched qty: 882
- Matched price: $3,340
- Execution cost: $2,945,680

Comparison:
- Actual cost: $3,087,200
- Simulated cost: $2,945,680
- Difference: -$141,520 (BETTER with dark)
- % improvement: 4.58%
```

---

## APPENDIX B: FILE REFERENCES

### Input Files
- `data/orders/drr_orders.csv` - Full order history
- `processed_files/sweep_orders_classified.csv.gz` - Step 2 output
- `processed_files/centrepoint_trades_raw.csv.gz` - Step 1 trades
- `data/nbbo/nbbo.csv` - Mid-price data

### Output Files
- `step5_simulation_results_detailed.csv.gz` - Per-order results
- `step5_scenario_comparison.csv.gz` - Scenario summary
- `step5_simulation_summary_by_group.csv.gz` - Group aggregates
- `STEP5_DETAILED_SUMMARY.md` - Documentation

### Code Files
- `step5_pipeline.py` - Main simulation pipeline
- `src/simulation.py` - Simulation engine (optional)
- `src/analysis.py` - Result analysis (optional)

---

## APPENDIX C: TRADING CONSTANTS

```python
# Trading Schedule
TRADING_DAY_START = 10:00:00 AEST
TRADING_DAY_END = 16:00:00 AEST

# Time Zone
AEST = UTC+10

# Security
SECURITY_CODE = 110621
SECURITY_NAME = "ASX Index"

# Participants
CENTRE_POINT_ID = 69

# Time Units
1 nanosecond = 10^-9 seconds
1 microsecond = 10^-6 seconds = 1,000 nanoseconds
1 second = 10^9 nanoseconds
```

---

**Plan Version:** 1.0  
**Status:** ✅ READY FOR IMPLEMENTATION  
**Last Updated:** January 1, 2026

---

