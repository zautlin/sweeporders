# Sweep Order Execution Analysis Algorithm
## Detailed Implementation Plan

---

## 🔍 DATA STRUCTURE ANALYSIS

### Files Examined

#### **1. Real Trades: `cp_trades_matched.csv.gz`**
```
Columns (20 total):
1. EXCHANGE           - Exchange ID (3)
2. sequence           - Sequence number
3. tradedate          - Trade date
4. tradetime          - Trade timestamp (nanoseconds)
5. securitycode       - Security code (110621)
6. orderid            - Order ID
7. dealsource         - Deal source code (20=Auction, etc.)
8. dealsourcedecoded  - Deal source description
9. exchangeinfo       - Exchange info (ON Market, etc.)
10. matchgroupid      - Match group ID (links opposite sides)
11. nationalbidpricesnapshot   - NBBO bid at trade time
12. nationalofferpricesnapshot - NBBO offer at trade time
13. tradeprice        - Execution price
14. quantity          - Trade quantity
15. side              - Side (1=Buy, 2=Sell)
16. sidedecoded       - Side description
17. participantid     - Participant ID
18. passiveaggressive - 0=passive, 1=aggressive
19. row_num           - Row number
20. date              - Trade date string

Total rows: 4,201 (including header)
```

**Key Observations:**
- Each trade has a unique `matchgroupid`
- `matchgroupid` does NOT directly pair opposite sides (it's a unique trade identifier)
- Real trades are mostly auction executions (dealsource=20)
- NBBO snapshots available for each trade (columns 11, 12)

#### **2. Simulated Trades: `cp_trades_simulation.csv`**
```
Columns (19 total):
1. EXCHANGE           - Exchange ID (3)
2. sequence           - Sequence number
3. tradedate          - Trade date
4. tradetime          - Trade timestamp (nanoseconds)
5. securitycode       - Security code (110621)
6. orderid            - Order ID
7. dealsource         - Deal source code (99=Simulated)
8. dealsourcedecoded  - "Simulated"
9. exchangeinfo       - Empty
10. matchgroupid      - Match group ID (format: 7904794000999XXXXXX)
11. nationalbidpricesnapshot   - NBBO bid at match time
12. nationalofferpricesnapshot - NBBO offer at match time
13. tradeprice        - Execution price
14. quantity          - Trade quantity
15. side              - Side (1=Buy, 2=Sell)
16. sidedecoded       - Side description
17. participantid     - 0
18. passiveaggressive - 0=passive, 1=aggressive
19. row_num           - Row number

Total rows: 3,097 (including header)
```

**Key Observations:**
- Simulated trades come in PAIRS with same `matchgroupid`
- Each matchgroupid has exactly 2 rows: one Buy, one Sell
- Format: 7904794000999XXXXXX (sequential counter)
- Example: matchgroupid=7904794000999000001 has:
  - Row 1: OrderID=7904794000124134400, Side=2 (Sell), Qty=2, Passive/Aggr=1
  - Row 2: OrderID=7904794000124135424, Side=1 (Buy), Qty=2, Passive/Aggr=0
- NBBO snapshots are identical for both sides of the match (same snapshot time)

#### **3. Sweep Orders: `last_execution_time.csv`**
```
Columns (3 total):
1. orderid                - Sweep order ID
2. first_execution_time   - First trade timestamp (nanoseconds)
3. last_execution_time    - Last trade timestamp (nanoseconds)

Total rows: 1,274 (including header) = 1,273 sweep orders
```

#### **4. Orders: `cp_orders_filtered.csv.gz`**
```
Relevant columns:
1. order_id           - Order ID
2. timestamp          - Order arrival timestamp (nanoseconds)
3. security_code      - Security code
4. price              - Limit price
5. side               - Side (1=Buy, 2=Sell)
6. quantity           - Total order quantity
7. exchangeordertype  - Order type (2048=Sweep)
8. national_bid       - NBBO bid at order arrival
9. national_offer     - NBBO offer at order arrival
```

---

## 📊 ALGORITHM OVERVIEW

### Phase 1: Data Loading & Filtering
### Phase 2: Order Set Identification
### Phase 3: Per-Order Metric Calculation
### Phase 4: Statistical Analysis
### Phase 5: Output Generation

---

## 🔧 DETAILED ALGORITHM

### **PHASE 1: DATA LOADING & FILTERING**

#### Step 1.1: Load Sweep Order Universe
```python
INPUT: 
  - last_execution_time.csv

PROCESS:
  1. Load CSV file
  2. Extract column: orderid
  3. Store as set: sweep_orderids
  
OUTPUT:
  - sweep_orderids: Set[int] (1,273 orders)

VALIDATION:
  - Assert len(sweep_orderids) == 1273
  - Assert all orderids are integers
  - Assert no duplicates
```

#### Step 1.2: Load Order Metadata (Arrival Context)
```python
INPUT:
  - cp_orders_filtered.csv.gz
  - sweep_orderids

PROCESS:
  1. Load compressed CSV
  2. Filter: WHERE order_id IN sweep_orderids
  3. Filter: WHERE exchangeordertype == 2048  (sweep orders only)
  4. Select columns:
     - order_id (rename to orderid)
     - timestamp (rename to arrival_time)
     - side
     - quantity (rename to order_quantity)
     - price (rename to limit_price)
     - national_bid (rename to arrival_bid)
     - national_offer (rename to arrival_offer)
  5. Calculate: arrival_midpoint = (arrival_bid + arrival_offer) / 2
  6. Calculate: arrival_spread = arrival_offer - arrival_bid
  7. Set index: orderid
  
OUTPUT:
  - orders_df: DataFrame with columns:
    [orderid, arrival_time, side, order_quantity, limit_price, 
     arrival_bid, arrival_offer, arrival_midpoint, arrival_spread]

VALIDATION:
  - Assert all sweep_orderids found in orders_df
  - Assert no NULL values in arrival_bid, arrival_offer
  - Assert arrival_spread >= 0 for all rows
  - Assert side in {1, 2} for all rows
```

#### Step 1.3: Load Real Trades
```python
INPUT:
  - cp_trades_matched.csv.gz
  - sweep_orderids

PROCESS:
  1. Load compressed CSV
  2. Filter: WHERE orderid IN sweep_orderids
  3. Select columns:
     - orderid
     - tradetime
     - tradeprice
     - quantity
     - side
     - nationalbidpricesnapshot
     - nationalofferpricesnapshot
     - matchgroupid
  4. Calculate: trade_midpoint = (nationalbidpricesnapshot + nationalofferpricesnapshot) / 2
  5. Sort by: orderid, tradetime
  
OUTPUT:
  - real_trades_df: DataFrame with columns:
    [orderid, tradetime, tradeprice, quantity, side, 
     nationalbidpricesnapshot, nationalofferpricesnapshot, 
     trade_midpoint, matchgroupid]

VALIDATION:
  - Assert all orderids exist in sweep_orderids
  - Assert tradetime > 0 for all rows
  - Assert quantity > 0 for all rows
  - Assert tradeprice > 0 for all rows
```

#### Step 1.4: Load Simulated Trades
```python
INPUT:
  - cp_trades_simulation.csv
  - sweep_orderids

PROCESS:
  1. Load CSV
  2. Filter: WHERE orderid IN sweep_orderids
  3. Select columns:
     - orderid
     - tradetime
     - tradeprice
     - quantity
     - side
     - nationalbidpricesnapshot
     - nationalofferpricesnapshot
     - matchgroupid
     - passiveaggressive
  4. Calculate: trade_midpoint = (nationalbidpricesnapshot + nationalofferpricesnapshot) / 2
  5. Sort by: orderid, tradetime
  
OUTPUT:
  - sim_trades_df: DataFrame with columns:
    [orderid, tradetime, tradeprice, quantity, side, 
     nationalbidpricesnapshot, nationalofferpricesnapshot, 
     trade_midpoint, matchgroupid, passiveaggressive]

VALIDATION:
  - Assert all orderids exist in sweep_orderids
  - Assert tradetime > 0 for all rows
  - Assert quantity > 0 for all rows
  - Assert tradeprice > 0 for all rows
  - Assert passiveaggressive in {0, 1} for all rows
```

---

### **PHASE 2: ORDER SET IDENTIFICATION**

#### Step 2.1: Identify Order Sets
```python
INPUT:
  - sweep_orderids
  - real_trades_df
  - sim_trades_df

PROCESS:
  1. Get unique orderids from real_trades_df → real_orderids
  2. Get unique orderids from sim_trades_df → sim_orderids
  3. Calculate intersection:
     set_a_orderids = real_orderids ∩ sim_orderids
  4. Calculate set B:
     set_b_orderids = real_orderids - sim_orderids
  5. Calculate orphan simulations (for diagnostics):
     orphan_sim_orderids = sim_orderids - real_orderids

OUTPUT:
  - set_a_orderids: Set[int] (orders with BOTH real and simulated)
  - set_b_orderids: Set[int] (orders with real but NO simulated)
  - orphan_sim_orderids: Set[int] (orders with sim but NO real - should be empty)

VALIDATION:
  - Assert len(set_a_orderids) > 0
  - Assert set_a_orderids ⊆ sweep_orderids
  - Assert set_b_orderids ⊆ sweep_orderids
  - Assert set_a_orderids ∩ set_b_orderids = ∅
  - Log: f"Set A (paired): {len(set_a_orderids)} orders"
  - Log: f"Set B (real only): {len(set_b_orderids)} orders"
  - Log: f"Orphan simulations: {len(orphan_sim_orderids)} orders"
  - Warn if orphan_sim_orderids not empty

EXPECTED RESULTS:
  - Set A: ~800-1,100 orders (to be determined)
  - Set B: ~200-400 orders (to be determined)
  - Orphan: 0 orders (ideally)
```

---

### **PHASE 3: PER-ORDER METRIC CALCULATION**

#### Step 3.1: Calculate Real Execution Metrics

```python
FOR EACH orderid IN (set_a_orderids ∪ set_b_orderids):
    
    # Get order context
    arrival_context = orders_df.loc[orderid]
    arrival_time = arrival_context['arrival_time']
    arrival_mid = arrival_context['arrival_midpoint']
    arrival_spread = arrival_context['arrival_spread']
    order_qty = arrival_context['order_quantity']
    side = arrival_context['side']
    side_multiplier = 1 if side == 1 else -1  # Buy=+1, Sell=-1
    
    # Get all real trades for this order
    order_trades = real_trades_df[real_trades_df.orderid == orderid]
    
    # Skip if no trades (shouldn't happen for orders in last_execution_time)
    IF len(order_trades) == 0:
        Log warning and continue
    
    # === GROUP A: FILL METRICS ===
    
    # Metric 1: Quantity Traded
    real_qty_filled = order_trades['quantity'].sum()
    
    # Metric 2: Fill Rate
    real_fill_rate_pct = (real_qty_filled / order_qty) * 100
    
    # Metric 3: Number of Fills
    real_num_fills = len(order_trades)
    
    # Metric 4: Average Fill Size
    real_avg_fill_size = real_qty_filled / real_num_fills
    
    # === GROUP B: PRICE/COST METRICS ===
    
    # Metric 5: VWAP (Volume-Weighted Average Price)
    real_vwap = (order_trades['tradeprice'] * order_trades['quantity']).sum() / real_qty_filled
    
    # Metric 6: Execution Cost - Arrival Based
    real_exec_cost_arrival_bps = side_multiplier * ((real_vwap - arrival_mid) / arrival_mid) * 10000
    
    # Metric 7: Execution Cost - Volume Weighted (using trade-by-trade NBBO)
    weighted_costs = []
    FOR each_trade IN order_trades:
        trade_mid = each_trade['trade_midpoint']
        trade_cost = side_multiplier * ((each_trade['tradeprice'] - trade_mid) / trade_mid) * 10000
        weighted_cost = trade_cost * each_trade['quantity']
        weighted_costs.append(weighted_cost)
    real_exec_cost_vw_bps = sum(weighted_costs) / real_qty_filled
    
    # Metric 8: Effective Spread Captured
    effective_spread = 2 * abs(real_vwap - arrival_mid)
    real_effective_spread_pct = (effective_spread / arrival_spread) * 100 IF arrival_spread > 0 ELSE 0
    
    # === GROUP C: TIMING METRICS ===
    
    # Metric 9: Total Execution Time
    first_trade_time = order_trades['tradetime'].min()
    last_trade_time = order_trades['tradetime'].max()
    real_exec_time_sec = (last_trade_time - first_trade_time) / 1e9
    
    # Metric 10: Time to First Fill
    real_time_to_first_fill_sec = (first_trade_time - arrival_time) / 1e9
    
    # === GROUP D: CONTEXT METRICS ===
    
    # Metric 11: Market Drift During Execution
    first_trade_mid = order_trades.iloc[0]['trade_midpoint']
    last_trade_mid = order_trades.iloc[-1]['trade_midpoint']
    real_market_drift_bps = ((last_trade_mid - first_trade_mid) / first_trade_mid) * 10000
    
    # Store all metrics in dictionary
    real_metrics[orderid] = {
        'real_qty_filled': real_qty_filled,
        'real_fill_rate_pct': real_fill_rate_pct,
        'real_num_fills': real_num_fills,
        'real_avg_fill_size': real_avg_fill_size,
        'real_vwap': real_vwap,
        'real_exec_cost_arrival_bps': real_exec_cost_arrival_bps,
        'real_exec_cost_vw_bps': real_exec_cost_vw_bps,
        'real_effective_spread_pct': real_effective_spread_pct,
        'real_exec_time_sec': real_exec_time_sec,
        'real_time_to_first_fill_sec': real_time_to_first_fill_sec,
        'real_market_drift_bps': real_market_drift_bps,
        'real_first_trade_time': first_trade_time,
        'real_last_trade_time': last_trade_time,
        'real_midpoint_at_last_fill': last_trade_mid
    }

OUTPUT:
  - real_metrics: Dict[orderid → metrics_dict]
```

#### Step 3.2: Calculate Simulated Execution Metrics

```python
FOR EACH orderid IN set_a_orderids:  # Only orders with simulated trades
    
    # Get order context (same as real)
    arrival_context = orders_df.loc[orderid]
    arrival_time = arrival_context['arrival_time']
    arrival_mid = arrival_context['arrival_midpoint']
    arrival_spread = arrival_context['arrival_spread']
    order_qty = arrival_context['order_quantity']
    side = arrival_context['side']
    side_multiplier = 1 if side == 1 else -1
    
    # Get all simulated trades for this order
    order_trades = sim_trades_df[sim_trades_df.orderid == orderid]
    
    # Skip if no trades (shouldn't happen for set_a)
    IF len(order_trades) == 0:
        Log warning and continue
    
    # === SAME CALCULATIONS AS REAL ===
    # (repeat all metric calculations from Step 3.1)
    
    sim_qty_filled = order_trades['quantity'].sum()
    sim_fill_rate_pct = (sim_qty_filled / order_qty) * 100
    sim_num_fills = len(order_trades)
    sim_avg_fill_size = sim_qty_filled / sim_num_fills
    sim_vwap = (order_trades['tradeprice'] * order_trades['quantity']).sum() / sim_qty_filled
    sim_exec_cost_arrival_bps = side_multiplier * ((sim_vwap - arrival_mid) / arrival_mid) * 10000
    
    # Volume-weighted cost
    weighted_costs = []
    FOR each_trade IN order_trades:
        trade_mid = each_trade['trade_midpoint']
        trade_cost = side_multiplier * ((each_trade['tradeprice'] - trade_mid) / trade_mid) * 10000
        weighted_cost = trade_cost * each_trade['quantity']
        weighted_costs.append(weighted_cost)
    sim_exec_cost_vw_bps = sum(weighted_costs) / sim_qty_filled
    
    effective_spread = 2 * abs(sim_vwap - arrival_mid)
    sim_effective_spread_pct = (effective_spread / arrival_spread) * 100 IF arrival_spread > 0 ELSE 0
    
    first_trade_time = order_trades['tradetime'].min()
    last_trade_time = order_trades['tradetime'].max()
    sim_exec_time_sec = (last_trade_time - first_trade_time) / 1e9
    sim_time_to_first_fill_sec = (first_trade_time - arrival_time) / 1e9
    
    first_trade_mid = order_trades.iloc[0]['trade_midpoint']
    last_trade_mid = order_trades.iloc[-1]['trade_midpoint']
    sim_market_drift_bps = ((last_trade_mid - first_trade_mid) / first_trade_mid) * 10000
    
    # Store all metrics
    sim_metrics[orderid] = {
        'sim_qty_filled': sim_qty_filled,
        'sim_fill_rate_pct': sim_fill_rate_pct,
        'sim_num_fills': sim_num_fills,
        'sim_avg_fill_size': sim_avg_fill_size,
        'sim_vwap': sim_vwap,
        'sim_exec_cost_arrival_bps': sim_exec_cost_arrival_bps,
        'sim_exec_cost_vw_bps': sim_exec_cost_vw_bps,
        'sim_effective_spread_pct': sim_effective_spread_pct,
        'sim_exec_time_sec': sim_exec_time_sec,
        'sim_time_to_first_fill_sec': sim_time_to_first_fill_sec,
        'sim_market_drift_bps': sim_market_drift_bps,
        'sim_first_trade_time': first_trade_time,
        'sim_last_trade_time': last_trade_time,
        'sim_midpoint_at_last_fill': last_trade_mid
    }

OUTPUT:
  - sim_metrics: Dict[orderid → metrics_dict]
```

#### Step 3.3: Merge and Calculate Differences (Set A Only)

```python
INPUT:
  - set_a_orderids
  - real_metrics
  - sim_metrics
  - orders_df

PROCESS:
  comparison_rows = []
  
  FOR EACH orderid IN set_a_orderids:
      # Get arrival context
      arrival_ctx = orders_df.loc[orderid]
      
      # Get real and sim metrics
      real = real_metrics[orderid]
      sim = sim_metrics[orderid]
      
      # Calculate differences (Real - Simulated)
      # Positive difference = Real was worse (higher cost, longer time, etc.)
      differences = {
          'qty_diff': real['real_qty_filled'] - sim['sim_qty_filled'],
          'fill_rate_diff_pct': real['real_fill_rate_pct'] - sim['sim_fill_rate_pct'],
          'num_fills_diff': real['real_num_fills'] - sim['sim_num_fills'],
          'avg_fill_size_diff': real['real_avg_fill_size'] - sim['sim_avg_fill_size'],
          'vwap_diff': real['real_vwap'] - sim['sim_vwap'],
          'exec_cost_arrival_diff_bps': real['real_exec_cost_arrival_bps'] - sim['sim_exec_cost_arrival_bps'],
          'exec_cost_vw_diff_bps': real['real_exec_cost_vw_bps'] - sim['sim_exec_cost_vw_bps'],
          'effective_spread_diff_pct': real['real_effective_spread_pct'] - sim['sim_effective_spread_pct'],
          'exec_time_diff_sec': real['real_exec_time_sec'] - sim['sim_exec_time_sec'],
          'time_to_first_fill_diff_sec': real['real_time_to_first_fill_sec'] - sim['sim_time_to_first_fill_sec'],
      }
      
      # Determine if dark pool was better (lower cost = better)
      dark_pool_better = sim['sim_exec_cost_arrival_bps'] < real['real_exec_cost_arrival_bps']
      
      # Combine all data
      row = {
          # Identifiers
          'orderid': orderid,
          'order_timestamp': arrival_ctx['arrival_time'],
          'side': arrival_ctx['side'],
          'order_quantity': arrival_ctx['order_quantity'],
          'arrival_bid': arrival_ctx['arrival_bid'],
          'arrival_offer': arrival_ctx['arrival_offer'],
          'arrival_midpoint': arrival_ctx['arrival_midpoint'],
          'arrival_spread': arrival_ctx['arrival_spread'],
          
          # Real metrics
          **real,
          
          # Simulated metrics
          **sim,
          
          # Differences
          **differences,
          
          # Summary flag
          'dark_pool_better': dark_pool_better
      }
      
      comparison_rows.append(row)
  
  # Create DataFrame
  comparison_df = pd.DataFrame(comparison_rows)

OUTPUT:
  - comparison_df: DataFrame with all metrics for Set A orders
  
VALIDATION:
  - Assert len(comparison_df) == len(set_a_orderids)
  - Assert no NULL values in key metrics
  - Log summary statistics
```

#### Step 3.4: Create Unexecuted-in-Dark DataFrame (Set B)

```python
INPUT:
  - set_b_orderids
  - real_metrics
  - orders_df

PROCESS:
  unexecuted_rows = []
  
  FOR EACH orderid IN set_b_orderids:
      # Get arrival context
      arrival_ctx = orders_df.loc[orderid]
      
      # Get real metrics
      real = real_metrics[orderid]
      
      # Determine reason for no dark match (heuristic)
      reason = "No contra orders available in dark pool"
      # Could be enhanced with more sophisticated logic
      
      # Create row
      row = {
          # Identifiers
          'orderid': orderid,
          'order_timestamp': arrival_ctx['arrival_time'],
          'side': arrival_ctx['side'],
          'order_quantity': arrival_ctx['order_quantity'],
          'arrival_bid': arrival_ctx['arrival_bid'],
          'arrival_offer': arrival_ctx['arrival_offer'],
          'arrival_midpoint': arrival_ctx['arrival_midpoint'],
          'arrival_spread': arrival_ctx['arrival_spread'],
          
          # Real execution metrics
          **real,
          
          # Dark pool status
          'sim_qty_filled': 0,
          'sim_fill_rate_pct': 0.0,
          'reason_no_dark_match': reason,
          
          # Opportunity cost (lit market cost vs ideal dark pool)
          'lit_market_cost_bps': real['real_exec_cost_arrival_bps'],
          'estimated_dark_savings_bps': None  # Can't estimate without counter-factual
      }
      
      unexecuted_rows.append(row)
  
  # Create DataFrame
  unexecuted_df = pd.DataFrame(unexecuted_rows)

OUTPUT:
  - unexecuted_df: DataFrame with Set B orders
  
VALIDATION:
  - Assert len(unexecuted_df) == len(set_b_orderids)
  - Assert sim_qty_filled == 0 for all rows
```

---

### **PHASE 4: STATISTICAL ANALYSIS**

#### Step 4.1: Calculate Summary Statistics

```python
INPUT:
  - comparison_df (Set A only)

METRICS_TO_ANALYZE = [
    'qty_filled',
    'fill_rate_pct', 
    'num_fills',
    'avg_fill_size',
    'vwap',
    'exec_cost_arrival_bps',
    'exec_cost_vw_bps',
    'effective_spread_pct',
    'exec_time_sec',
    'time_to_first_fill_sec'
]

PROCESS:
  summary_rows = []
  
  FOR EACH metric IN METRICS_TO_ANALYZE:
      real_col = f'real_{metric}'
      sim_col = f'sim_{metric}'
      diff_col = f'{metric}_diff' IF exists ELSE f'{metric.replace("_", "_")}_diff'
      
      # Extract data
      real_values = comparison_df[real_col].dropna()
      sim_values = comparison_df[sim_col].dropna()
      diff_values = comparison_df[diff_col].dropna() IF diff_col IN comparison_df ELSE (real_values - sim_values)
      
      # Calculate descriptive statistics
      summary = {
          'metric_group': get_metric_group(metric),  # Fill, Cost, Timing, Context
          'metric_name': metric,
          
          # Real statistics
          'real_mean': real_values.mean(),
          'real_median': real_values.median(),
          'real_std': real_values.std(),
          'real_min': real_values.min(),
          'real_max': real_values.max(),
          'real_q25': real_values.quantile(0.25),
          'real_q75': real_values.quantile(0.75),
          
          # Simulated statistics
          'sim_mean': sim_values.mean(),
          'sim_median': sim_values.median(),
          'sim_std': sim_values.std(),
          'sim_min': sim_values.min(),
          'sim_max': sim_values.max(),
          'sim_q25': sim_values.quantile(0.25),
          'sim_q75': sim_values.quantile(0.75),
          
          # Difference statistics
          'diff_mean': diff_values.mean(),
          'diff_median': diff_values.median(),
          'diff_std': diff_values.std(),
          
          # Sample size
          'n_orders': len(real_values)
      }
      
      summary_rows.append(summary)
  
  summary_df = pd.DataFrame(summary_rows)

OUTPUT:
  - summary_df: DataFrame with summary statistics
```

#### Step 4.2: Perform Statistical Tests

```python
INPUT:
  - comparison_df
  - METRICS_TO_ANALYZE

PROCESS:
  from scipy import stats
  import numpy as np
  
  test_rows = []
  
  FOR EACH metric IN METRICS_TO_ANALYZE:
      real_col = f'real_{metric}'
      sim_col = f'sim_{metric}'
      
      # Extract paired data
      real_values = comparison_df[real_col].dropna()
      sim_values = comparison_df[sim_col].dropna()
      
      # Ensure same length (paired)
      common_idx = real_values.index.intersection(sim_values.index)
      real_values = real_values.loc[common_idx]
      sim_values = sim_values.loc[common_idx]
      differences = real_values - sim_values
      
      n_pairs = len(real_values)
      
      # === PAIRED T-TEST ===
      IF n_pairs >= 2:
          t_stat, t_pvalue = stats.ttest_rel(real_values, sim_values)
      ELSE:
          t_stat, t_pvalue = np.nan, np.nan
      
      # === WILCOXON SIGNED-RANK TEST ===
      IF n_pairs >= 10:  # Need sufficient sample
          TRY:
              w_stat, w_pvalue = stats.wilcoxon(real_values, sim_values)
          EXCEPT:
              w_stat, w_pvalue = np.nan, np.nan
      ELSE:
          w_stat, w_pvalue = np.nan, np.nan
      
      # === CORRELATION ===
      IF n_pairs >= 3:
          pearson_r, pearson_p = stats.pearsonr(real_values, sim_values)
          spearman_r, spearman_p = stats.spearmanr(real_values, sim_values)
      ELSE:
          pearson_r, pearson_p = np.nan, np.nan
          spearman_r, spearman_p = np.nan, np.nan
      
      # === EFFECT SIZE (COHEN'S D) ===
      IF differences.std() > 0:
          cohens_d = differences.mean() / differences.std()
      ELSE:
          cohens_d = np.nan
      
      # === CONFIDENCE INTERVAL ===
      IF n_pairs >= 2:
          ci = stats.t.interval(
              confidence=0.95,
              df=n_pairs-1,
              loc=differences.mean(),
              scale=differences.std() / np.sqrt(n_pairs)
          )
          ci_lower, ci_upper = ci
      ELSE:
          ci_lower, ci_upper = np.nan, np.nan
      
      # === INTERPRETATION ===
      IF not np.isnan(t_pvalue):
          IF t_pvalue < 0.001:
              significance = "***"
          ELIF t_pvalue < 0.01:
              significance = "**"
          ELIF t_pvalue < 0.05:
              significance = "*"
          ELSE:
              significance = "ns"
          
          IF differences.mean() > 0:
              direction = "Real execution worse than simulated"
          ELIF differences.mean() < 0:
              direction = "Real execution better than simulated"
          ELSE:
              direction = "No difference"
          
          interpretation = f"{direction} (p{significance})"
      ELSE:
          significance = "N/A"
          interpretation = "Insufficient data"
      
      # Store results
      test_result = {
          'metric_name': metric,
          'n_pairs': n_pairs,
          
          # t-test
          'paired_t_statistic': t_stat,
          'paired_t_pvalue': t_pvalue,
          
          # Wilcoxon
          'wilcoxon_statistic': w_stat,
          'wilcoxon_pvalue': w_pvalue,
          
          # Correlation
          'pearson_correlation': pearson_r,
          'pearson_pvalue': pearson_p,
          'spearman_correlation': spearman_r,
          'spearman_pvalue': spearman_p,
          
          # Effect size
          'cohens_d_effect_size': cohens_d,
          
          # Confidence interval
          'mean_diff_ci_lower': ci_lower,
          'mean_diff_ci_upper': ci_upper,
          
          # Interpretation
          'significant_at_05': (t_pvalue < 0.05) IF not np.isnan(t_pvalue) ELSE False,
          'significant_at_01': (t_pvalue < 0.01) IF not np.isnan(t_pvalue) ELSE False,
          'significance_level': significance,
          'interpretation': interpretation
      }
      
      test_rows.append(test_result)
  
  tests_df = pd.DataFrame(test_rows)

OUTPUT:
  - tests_df: DataFrame with statistical test results
```

#### Step 4.3: Quantile Analysis

```python
INPUT:
  - comparison_df
  - METRICS_TO_ANALYZE

QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]

PROCESS:
  quantile_rows = []
  
  FOR EACH metric IN METRICS_TO_ANALYZE:
      real_col = f'real_{metric}'
      sim_col = f'sim_{metric}'
      
      real_values = comparison_df[real_col].dropna()
      sim_values = comparison_df[sim_col].dropna()
      
      FOR EACH q IN QUANTILES:
          real_q = real_values.quantile(q)
          sim_q = sim_values.quantile(q)
          diff_q = real_q - sim_q
          
          quantile_rows.append({
              'metric_name': metric,
              'percentile': int(q * 100),
              'real_value': real_q,
              'sim_value': sim_q,
              'difference': diff_q
          })
  
  quantiles_df = pd.DataFrame(quantile_rows)

OUTPUT:
  - quantiles_df: DataFrame with quantile comparisons
```

---

### **PHASE 5: OUTPUT GENERATION**

#### Step 5.1: Create Output Directory Structure

```python
INPUT:
  - partition_key (e.g., "2024-09-05/110621")
  - base_dir (e.g., "data/processed")

PROCESS:
  output_dir = f"{base_dir}/{partition_key}/stats"
  
  # Create directory if not exists
  os.makedirs(output_dir, exist_ok=True)
  
  Log: f"Created output directory: {output_dir}"

OUTPUT:
  - output_dir: str (path to stats directory)
```

#### Step 5.2: Write Output Files

```python
INPUT:
  - comparison_df
  - summary_df
  - tests_df
  - quantiles_df
  - unexecuted_df
  - output_dir

OUTPUT FILES:

1. sweep_order_comparison_detailed.csv
   - Data: comparison_df
   - Rows: Set A orders
   - Format: CSV with header
   - Compression: None
   
2. sweep_order_comparison_summary.csv
   - Data: summary_df
   - Rows: One per metric
   - Format: CSV with header
   - Compression: None
   
3. sweep_order_statistical_tests.csv
   - Data: tests_df
   - Rows: One per metric
   - Format: CSV with header
   - Compression: None
   
4. sweep_order_quantile_comparison.csv
   - Data: quantiles_df
   - Rows: Metrics × Quantiles
   - Format: CSV with header
   - Compression: None
   
5. sweep_order_unexecuted_in_dark.csv
   - Data: unexecuted_df
   - Rows: Set B orders
   - Format: CSV with header
   - Compression: None

PROCESS:
  FOR EACH (filename, dataframe) IN output_files:
      filepath = os.path.join(output_dir, filename)
      dataframe.to_csv(filepath, index=False)
      
      # Log
      Log: f"Wrote {len(dataframe)} rows to {filepath}"

VALIDATION:
  - Assert all files exist
  - Assert file sizes > 0
  - Assert row counts match expected
```

#### Step 5.3: Generate Validation Report

```python
INPUT:
  - All output files

PROCESS:
  validation_report = {
      'timestamp': datetime.now().isoformat(),
      'partition_key': partition_key,
      'sweep_order_universe': len(sweep_orderids),
      'set_a_count': len(set_a_orderids),
      'set_b_count': len(set_b_orderids),
      'orphan_sim_count': len(orphan_sim_orderids),
      'total_real_trades': len(real_trades_df),
      'total_sim_trades': len(sim_trades_df),
      'output_files': {
          'sweep_order_comparison_detailed.csv': len(comparison_df),
          'sweep_order_comparison_summary.csv': len(summary_df),
          'sweep_order_statistical_tests.csv': len(tests_df),
          'sweep_order_quantile_comparison.csv': len(quantiles_df),
          'sweep_order_unexecuted_in_dark.csv': len(unexecuted_df)
      },
      'key_findings': {
          'avg_real_exec_cost_bps': summary_df[summary_df.metric_name == 'exec_cost_arrival_bps']['real_mean'].values[0],
          'avg_sim_exec_cost_bps': summary_df[summary_df.metric_name == 'exec_cost_arrival_bps']['sim_mean'].values[0],
          'avg_cost_savings_bps': summary_df[summary_df.metric_name == 'exec_cost_arrival_bps']['diff_mean'].values[0],
          'cost_diff_pvalue': tests_df[tests_df.metric_name == 'exec_cost_arrival_bps']['paired_t_pvalue'].values[0]
      }
  }
  
  # Write validation report
  validation_path = os.path.join(output_dir, 'analysis_validation_report.json')
  with open(validation_path, 'w') as f:
      json.dump(validation_report, f, indent=2)
  
  Log: "Validation report written"
  Log: f"Key Finding: Dark pool saves avg {validation_report['key_findings']['avg_cost_savings_bps']:.2f} bps (p={validation_report['key_findings']['cost_diff_pvalue']:.4f})"

OUTPUT:
  - analysis_validation_report.json
```

---

## 📁 OUTPUT FILE SPECIFICATIONS

### File 1: `sweep_order_comparison_detailed.csv`

**Purpose**: Order-level paired comparison (Real vs Simulated)

**Columns (50+ total)**:
```
# Identifiers & Context (8 columns)
orderid, order_timestamp, side, order_quantity, 
arrival_bid, arrival_offer, arrival_midpoint, arrival_spread

# Real Execution Metrics (14 columns)
real_qty_filled, real_fill_rate_pct, real_num_fills, real_avg_fill_size,
real_vwap, real_exec_cost_arrival_bps, real_exec_cost_vw_bps, real_effective_spread_pct,
real_exec_time_sec, real_time_to_first_fill_sec, real_market_drift_bps,
real_first_trade_time, real_last_trade_time, real_midpoint_at_last_fill

# Simulated Execution Metrics (14 columns)
sim_qty_filled, sim_fill_rate_pct, sim_num_fills, sim_avg_fill_size,
sim_vwap, sim_exec_cost_arrival_bps, sim_exec_cost_vw_bps, sim_effective_spread_pct,
sim_exec_time_sec, sim_time_to_first_fill_sec, sim_market_drift_bps,
sim_first_trade_time, sim_last_trade_time, sim_midpoint_at_last_fill

# Differences (10 columns)
qty_diff, fill_rate_diff_pct, num_fills_diff, avg_fill_size_diff,
vwap_diff, exec_cost_arrival_diff_bps, exec_cost_vw_diff_bps, effective_spread_diff_pct,
exec_time_diff_sec, time_to_first_fill_diff_sec

# Summary Flag (1 column)
dark_pool_better
```

**Estimated Rows**: ~800-1,100 (Set A)

---

### File 2: `sweep_order_comparison_summary.csv`

**Purpose**: Aggregated statistics per metric

**Columns**:
```
metric_group, metric_name,
real_mean, real_median, real_std, real_min, real_max, real_q25, real_q75,
sim_mean, sim_median, sim_std, sim_min, sim_max, sim_q25, sim_q75,
diff_mean, diff_median, diff_std,
n_orders
```

**Estimated Rows**: 10 metrics

---

### File 3: `sweep_order_statistical_tests.csv`

**Purpose**: Hypothesis test results

**Columns**:
```
metric_name, n_pairs,
paired_t_statistic, paired_t_pvalue,
wilcoxon_statistic, wilcoxon_pvalue,
pearson_correlation, pearson_pvalue,
spearman_correlation, spearman_pvalue,
cohens_d_effect_size,
mean_diff_ci_lower, mean_diff_ci_upper,
significant_at_05, significant_at_01,
significance_level, interpretation
```

**Estimated Rows**: 10 metrics

---

### File 4: `sweep_order_quantile_comparison.csv`

**Purpose**: Distribution comparison at percentiles

**Columns**:
```
metric_name, percentile, real_value, sim_value, difference
```

**Estimated Rows**: 10 metrics × 5 percentiles = 50 rows

---

### File 5: `sweep_order_unexecuted_in_dark.csv`

**Purpose**: Orders with real trades but no simulated trades

**Columns**:
```
# Identifiers & Context (8 columns)
orderid, order_timestamp, side, order_quantity,
arrival_bid, arrival_offer, arrival_midpoint, arrival_spread

# Real Execution Metrics (14 columns)
real_qty_filled, real_fill_rate_pct, real_num_fills, real_avg_fill_size,
real_vwap, real_exec_cost_arrival_bps, real_exec_cost_vw_bps, real_effective_spread_pct,
real_exec_time_sec, real_time_to_first_fill_sec, real_market_drift_bps,
real_first_trade_time, real_last_trade_time, real_midpoint_at_last_fill

# Dark Pool Status (4 columns)
sim_qty_filled, sim_fill_rate_pct, reason_no_dark_match,
lit_market_cost_bps, estimated_dark_savings_bps
```

**Estimated Rows**: ~200-400 (Set B)

---

## 🔍 VALIDATION CHECKLIST

### Data Integrity
- [ ] All sweep orders from last_execution_time.csv loaded (1,273 orders)
- [ ] No NULL values in arrival NBBO (bid, offer)
- [ ] All trades have positive quantities
- [ ] All trades have positive prices
- [ ] Set A + Set B = Orders with real trades
- [ ] Set A ∩ Set B = Empty set
- [ ] All simulated matchgroups have exactly 2 rows (buy + sell)

### Metric Calculations
- [ ] VWAP calculations match manual spot checks
- [ ] Execution costs have correct sign (positive = worse for buyer/seller)
- [ ] Fill rates are between 0-100%
- [ ] Execution times are non-negative
- [ ] Volume-weighted costs match trade-by-trade calculations

### Statistical Tests
- [ ] Paired t-tests only on orders in Set A
- [ ] Sample sizes match across all tests for same metric
- [ ] p-values are between 0 and 1
- [ ] Confidence intervals contain the mean difference
- [ ] Effect sizes are reasonable (not extreme outliers)

### Output Files
- [ ] All 5 CSV files created in stats/ directory
- [ ] File row counts match expectations
- [ ] No duplicate orderids in detailed comparison
- [ ] Summary statistics file has 10 rows (one per metric)
- [ ] Statistical tests file has 10 rows (one per metric)
- [ ] Quantile file has 50 rows (10 metrics × 5 percentiles)

### Reproducibility
- [ ] Re-running analysis produces identical results
- [ ] Validation report generated
- [ ] Key findings logged to console
- [ ] All intermediate DataFrames preserved for debugging

---

## 📊 EXPECTED OUTPUT STRUCTURE

```
data/processed/2024-09-05/110621/stats/
├── sweep_order_comparison_detailed.csv        (~800-1,100 rows)
├── sweep_order_comparison_summary.csv         (10 rows)
├── sweep_order_statistical_tests.csv          (10 rows)
├── sweep_order_quantile_comparison.csv        (50 rows)
├── sweep_order_unexecuted_in_dark.csv         (~200-400 rows)
└── analysis_validation_report.json            (metadata)
```

---

## 🚀 IMPLEMENTATION NOTES

### Performance Considerations
- Use vectorized pandas operations instead of loops where possible
- Load compressed files with `compression='gzip'` parameter
- Filter early to reduce memory footprint
- Use `pd.merge()` instead of repeated `loc[]` lookups

### Error Handling
- Wrap file I/O in try-except blocks
- Log warnings for missing data (don't fail silently)
- Validate inputs before processing
- Provide clear error messages with context

### Extensibility
- Design functions to accept arbitrary metric definitions
- Allow configuration of significance levels
- Support multiple partitions in single run
- Enable easy addition of new statistical tests

---

## ⏱️ ESTIMATED RUNTIME

- Phase 1 (Data Loading): ~5-10 seconds
- Phase 2 (Set Identification): <1 second
- Phase 3 (Metric Calculation): ~15-30 seconds
- Phase 4 (Statistical Analysis): ~2-5 seconds
- Phase 5 (Output Generation): ~2-5 seconds

**Total: ~30-60 seconds per partition**

---

## 🎯 SUCCESS CRITERIA

1. ✅ All 1,273 sweep orders accounted for
2. ✅ Set A (paired comparison) has >700 orders
3. ✅ All 5 output files generated successfully
4. ✅ Statistical tests yield significant results (p < 0.05) for cost metrics
5. ✅ Validation report shows no data integrity issues
6. ✅ Manual spot-check of 5 random orders confirms correct calculations

---

## END OF ALGORITHM SPECIFICATION
