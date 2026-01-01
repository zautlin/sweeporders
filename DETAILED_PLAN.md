# Sweep Orders Analysis - Detailed Implementation Plan
## Dark Book Only Simulation Approach

### Core Concept
Real sweep orders: Dark pool → Lit market (when unexecuted in dark)
Simulation: Sweep orders stay in Dark book → Test dark pool matching

---

## Phase 1: Data Ingestion & Filtering

### 1.1 Read & Extract Centre Point Orders

**Objective:** Load orders file, identify Centre Point orders, prepare for downstream processing

**Implementation Details:**

```python
# Input: orders.csv or orders parquet
# File size: ? (need clarification)
# Format expected: CSV with columns like:
#   - orderid
#   - exchangeordertype (64, 256, 2048, 4096, 4098)
#   - timestamp / arrival_time
#   - quantity
#   - price
#   - participant_id / firm_id
#   - symbol / instrument_id
#   - side (BUY/SELL)
```

**Step-by-step process:**

1. **Chunked Reading Strategy**
   - Read file in 100MB-500MB chunks (adjustable)
   - Use pandas.read_csv with `chunksize` parameter or polars.scan_csv
   - Reason: Orders file may be large, avoid OOM errors

2. **Filter Centre Point Order Types**
   - Keep only rows where `exchangeordertype IN (64, 256, 2048, 4096, 4098)`
   - Reason: These are Centre Point specific order types
   
3. **Extract Key Columns**
   - Keep: orderid, exchangeordertype, timestamp, quantity, price, participant_id, symbol, side
   - Drop non-essential columns to save memory

4. **Data Type Optimization**
   - Convert orderid to uint64 (faster matching later)
   - Convert timestamp to datetime64[ns] or int64 (nanoseconds)
   - Convert quantity to uint32 or int32
   - Convert price to float32 (or float64 if precision needed)
   - Symbol to category dtype

5. **Store Intermediate Result**
   - Format: Parquet with compression (snappy/zstd)
   - Path: `processed_files/centrepoint_orders_raw.parquet`
   - Size estimate: ~10-20% of input
   - Include metadata: count, date range, order type distribution

**Output Data Structure:**
```
DataFrame columns:
- orderid (uint64, unique, primary key)
- exchangeordertype (int8)
- timestamp (int64 nanoseconds since epoch)
- quantity (uint32)
- price (float32)
- participant_id (uint32)
- symbol (category)
- side (category: BUY/SELL)
```

**Code template:**
```python
def extract_centrepoint_orders(input_file, output_dir, chunksize=500_000_000):
    centrepoint_types = {64, 256, 2048, 4096, 4098}
    chunks = []
    
    for chunk in pd.read_csv(input_file, chunksize=chunksize):
        filtered = chunk[chunk['exchangeordertype'].isin(centrepoint_types)].copy()
        # Optimize dtypes
        filtered['orderid'] = filtered['orderid'].astype('uint64')
        filtered['timestamp'] = pd.to_datetime(filtered['timestamp']).astype('int64')
        filtered['quantity'] = filtered['quantity'].astype('uint32')
        filtered['price'] = filtered['price'].astype('float32')
        chunks.append(filtered)
    
    df = pd.concat(chunks, ignore_index=True)
    df.to_parquet(f'{output_dir}/centrepoint_orders_raw.parquet', compression='snappy')
    
    return df
```

---

### 1.2 Read & Match Trades

**Objective:** Filter 50GB trades file to only Centre Point trades, match to orders

**Implementation Details:**

```python
# Input: trades.csv (≈50GB)
# Format expected: CSV with columns like:
#   - tradeid
#   - orderid (or order_ref)
#   - timestamp
#   - quantity_traded
#   - price_executed
#   - participant_id
#   - symbol
#   - execution_venue (e.g., 'DarkPool', 'Lit')
```

**Step-by-step process:**

1. **Create Lookup Set**
   - From Step 1.1, extract set of all Centre Point orderids
   - Store in memory (uint64 set is small even for millions of orders)
   ```python
   centrepoint_order_ids = set(cp_orders['orderid'].values)
   ```

2. **Stream Trades File**
   - Read trades.csv in 500MB-1GB chunks
   - For each chunk:
     - Filter: `tradeid` or `orderid` must be in centrepoint_order_ids
     - Keep matched rows
     - Append to output file (streaming write)

3. **Aggregate Trade Data by Order**
   - Group trades by orderid
   - Calculate per-order metrics:
     - Total quantity traded
     - Average execution price (volume-weighted)
     - First trade timestamp (execution start)
     - Last trade timestamp (execution end)
     - Execution duration = last_timestamp - first_timestamp
     - Execution venues (list of venues where traded)

4. **Data Type Optimization**
   - Similar to Phase 1.1
   - timestamp as int64 nanoseconds
   - quantities as uint32
   - prices as float32

5. **Store Results**
   - Individual trades: `processed_files/centrepoint_trades_raw.parquet` (all trades for CP orders)
   - Aggregated: `processed_files/centrepoint_trades_agg.parquet` (per-order summary)

**Output Data Structure (Raw):**
```
DataFrame columns:
- tradeid (uint64)
- orderid (uint64)
- timestamp (int64)
- quantity_traded (uint32)
- price_executed (float32)
- execution_venue (category)
```

**Output Data Structure (Aggregated by Order):**
```
DataFrame columns:
- orderid (uint64, primary key)
- total_quantity_filled (uint32)
- avg_execution_price (float32) - volume weighted
- first_trade_ts (int64)
- last_trade_ts (int64)
- execution_duration_ns (int64) - last - first
- execution_duration_seconds (float32) - for readability
- num_trades (int16)
- execution_venues (list of strings)
- fill_ratio (float32) - total_quantity_filled / original_quantity
```

**Code template:**
```python
def match_trades(trades_file, centrepoint_order_ids, output_dir, chunksize=1_000_000_000):
    cp_orders_set = set(centrepoint_order_ids)
    
    # Stream process trades
    matched_trades = []
    for chunk in pd.read_csv(trades_file, chunksize=chunksize):
        matched = chunk[chunk['orderid'].isin(cp_orders_set)].copy()
        matched_trades.append(matched)
    
    all_trades = pd.concat(matched_trades, ignore_index=True)
    all_trades.to_parquet(f'{output_dir}/centrepoint_trades_raw.parquet')
    
    # Aggregate
    agg = all_trades.groupby('orderid').agg({
        'quantity_traded': 'sum',
        'price_executed': lambda x: (x * all_trades.loc[x.index, 'quantity_traded']).sum() / all_trades.loc[x.index, 'quantity_traded'].sum(),
        'timestamp': ['min', 'max', 'count'],
        'execution_venue': lambda x: list(x.unique())
    })
    
    agg.columns = ['total_quantity_filled', 'avg_execution_price', 'first_trade_ts', 'last_trade_ts', 'num_trades', 'execution_venues']
    agg['execution_duration_ns'] = agg['last_trade_ts'] - agg['first_trade_ts']
    agg['execution_duration_seconds'] = agg['execution_duration_ns'] / 1e9
    
    agg.to_parquet(f'{output_dir}/centrepoint_trades_agg.parquet')
    
    return all_trades, agg
```

---

### 1.3 Build Dark Order Book State

**Objective:** Create a searchable data structure of all orders that could be in the dark book

**Key Difference from Lit Book:** 
- No continuous price-level snapshots
- Just a static snapshot of "what orders exist that could match?"
- Updated as of the trading session

**Implementation Details:**

```python
# Input: All Centre Point orders from 1.1
# Idea: Dark book contains resting orders that could match
# We'll simulate sweep orders resting here
```

**Step-by-step process:**

1. **Load All Centre Point Orders**
   - From Step 1.1: centrepoint_orders_raw.parquet
   - These represent ALL orders that could rest in dark book

2. **Organize by Symbol & Side**
   - Create nested index structure:
   ```
   dark_book = {
       'SYMBOL1': {
           'BUY': DataFrame(sorted by price descending, then timestamp),
           'SELL': DataFrame(sorted by price ascending, then timestamp)
       },
       'SYMBOL2': { ... }
   }
   ```

3. **Build Price Levels for Each Symbol/Side**
   - Group orders by (symbol, side, price)
   - For each price level, maintain:
     - List of orderids (in timestamp order for priority)
     - Cumulative quantity at that level
   - Example structure per symbol/side:
   ```
   BUY side for SYMBOL1:
   Price 100.50: [order1(qty=1000), order2(qty=500)]  // 1500 total
   Price 100.45: [order3(qty=2000)]                   // 2000 total
   Price 100.40: [order4(qty=500), order5(qty=500)]   // 1000 total
   ...
   ```

4. **Store in Efficient Format**
   - Use nested dictionaries with sorted order
   - Reason: Fast lookups during matching
   - Path: `processed_files/dark_book_state.pkl` (pickle for complex structures)
   - Also save as parquet for reference: `processed_files/dark_book_orders.parquet`

**Output Data Structure:**
```python
# Dictionary structure:
dark_book = {
    'SYMBOL1': {
        'BUY': {
            100.50: [('orderid1', 1000), ('orderid2', 500)],
            100.45: [('orderid3', 2000)],
            ...
        },
        'SELL': {
            100.55: [('orderid4', 1000)],
            ...
        }
    },
    'SYMBOL2': { ... }
}

# Also maintain quick-access index by orderid:
order_index = {
    'orderid1': {
        'symbol': 'SYMBOL1',
        'side': 'BUY',
        'price': 100.50,
        'quantity': 1000,
        'timestamp': 1234567890,
        'participant_id': 123
    },
    ...
}
```

**Code template:**
```python
def build_dark_book(centrepoint_orders_df):
    dark_book = {}
    order_index = {}
    
    for symbol in centrepoint_orders_df['symbol'].unique():
        symbol_orders = centrepoint_orders_df[centrepoint_orders_df['symbol'] == symbol]
        dark_book[symbol] = {'BUY': {}, 'SELL': {}}
        
        for side in ['BUY', 'SELL']:
            side_orders = symbol_orders[symbol_orders['side'] == side].sort_values('price', ascending=(side=='BUY'))
            
            for price in sorted(side_orders['price'].unique(), key=lambda x: -x if side=='BUY' else x):
                price_level = side_orders[side_orders['price'] == price].sort_values('timestamp')
                dark_book[symbol][side][price] = [
                    (row['orderid'], row['quantity']) for _, row in price_level.iterrows()
                ]
        
        # Build order index
        for _, order in centrepoint_orders_df.iterrows():
            order_index[order['orderid']] = {
                'symbol': order['symbol'],
                'side': order['side'],
                'price': order['price'],
                'quantity': order['quantity'],
                'timestamp': order['timestamp'],
                'participant_id': order['participant_id']
            }
    
    return dark_book, order_index
```

---

## Phase 2: Order Classification & Outcome Determination

### 2.1 Filter Sweep Orders Only

**Objective:** Extract only sweep orders (exchangeordertype == 2048) for detailed analysis

**Implementation Details:**

```python
# Input: centrepoint_orders_raw.parquet (from Phase 1.1)
# Filter: exchangeordertype == 2048
```

**Step-by-step process:**

1. **Filter for Sweep Orders**
   ```python
   sweep_orders = centrepoint_orders[centrepoint_orders['exchangeordertype'] == 2048].copy()
   ```

2. **Join with Trade Data**
   - Left join sweep_orders with centrepoint_trades_agg
   - Fill NaN with zeros (orders with no trades)
   - Calculate fill ratio:
   ```python
   sweep_orders['fill_ratio'] = sweep_orders['total_quantity_filled'] / sweep_orders['quantity']
   sweep_orders['fill_ratio'] = sweep_orders['fill_ratio'].fillna(0.0)
   ```

3. **Add Execution Duration**
   - From trades_agg: execution_duration_seconds
   - For unexecuted orders: set to NaT or 0

4. **Calculate Fill Classification (Early Indicator)**
   - This will be refined in 2.2
   - full_fill: fill_ratio >= 0.99 (allow 1% rounding error)
   - partial_fill: 0 < fill_ratio < 0.99
   - no_fill: fill_ratio == 0

5. **Store Result**
   - Path: `processed_files/sweep_orders_with_trades.parquet`

**Output Data Structure:**
```
DataFrame columns (from orders + trade data):
- orderid (uint64)
- exchangeordertype (int8) - always 2048 for this phase
- timestamp (int64)
- quantity (uint32)
- price (float32)
- participant_id (uint32)
- symbol (category)
- side (category)
- total_quantity_filled (uint32) - from trades
- avg_execution_price (float32) - from trades
- first_trade_ts (int64) - from trades
- last_trade_ts (int64) - from trades
- execution_duration_seconds (float32) - from trades
- num_trades (int16) - from trades
- execution_venues (list) - from trades
- fill_ratio (float32)
- fill_classification (category: 'full', 'partial', 'none')
```

**Code template:**
```python
def filter_sweep_orders(centrepoint_orders_df, trades_agg_df):
    sweep = centrepoint_orders_df[centrepoint_orders_df['exchangeordertype'] == 2048].copy()
    
    # Join with trade data
    sweep = sweep.merge(trades_agg_df, on='orderid', how='left')
    
    # Fill nulls
    sweep['total_quantity_filled'] = sweep['total_quantity_filled'].fillna(0).astype('uint32')
    sweep['fill_ratio'] = sweep['total_quantity_filled'] / sweep['quantity']
    
    # Classify
    sweep['fill_classification'] = 'none'
    sweep.loc[sweep['fill_ratio'] >= 0.99, 'fill_classification'] = 'full'
    sweep.loc[(sweep['fill_ratio'] > 0) & (sweep['fill_ratio'] < 0.99), 'fill_classification'] = 'partial'
    
    sweep.to_parquet('processed_files/sweep_orders_with_trades.parquet')
    return sweep
```

---

### 2.2 Classify Sweep Order Outcomes

**Objective:** Categorize real execution outcomes into 3 distinct groups

**Implementation Details:**

```python
# Input: sweep_orders_with_trades.parquet
# Output: 3 separate dataframes/files
#   - Scenario_A: Immediate full execution (< 1 second)
#   - Scenario_B: Eventual full execution (>= 1 second)
#   - Scenario_C: Partial or no execution
```

**Classification Logic:**

**Scenario A: Immediate Full Execution**
- Criteria:
  - fill_ratio >= 0.99 (fully filled)
  - execution_duration_seconds < 1.0 (filled in < 1 second)
- Business meaning: Sweep matched instantly in dark pool
- Count: N_A orders

**Scenario B: Eventual Full Execution**
- Criteria:
  - fill_ratio >= 0.99 (fully filled)
  - execution_duration_seconds >= 1.0 (took >= 1 second)
- Business meaning: Sweep matched in dark pool but took time
- Count: N_B orders

**Scenario C: Partial or No Execution**
- Criteria:
  - fill_ratio < 0.99 (not fully filled)
- Includes: partial fills AND complete no-fills
- Business meaning: Sweep didn't match fully in dark
- Count: N_C orders

**Step-by-step process:**

1. **Load Sweep Orders Data**
   ```python
   sweep = pd.read_parquet('processed_files/sweep_orders_with_trades.parquet')
   ```

2. **Split into 3 Groups**
   ```python
   scenario_a = sweep[(sweep['fill_ratio'] >= 0.99) & (sweep['execution_duration_seconds'] < 1.0)]
   scenario_b = sweep[(sweep['fill_ratio'] >= 0.99) & (sweep['execution_duration_seconds'] >= 1.0)]
   scenario_c = sweep[sweep['fill_ratio'] < 0.99]
   ```

3. **Add Scenario Label**
   - Add column `scenario_classification = 'A'/'B'/'C'`
   - Useful for tracking later

4. **Calculate Summary Statistics per Scenario**
   ```python
   for scenario_name, scenario_df in [('A', scenario_a), ('B', scenario_b), ('C', scenario_c)]:
       stats = {
           'scenario': scenario_name,
           'order_count': len(scenario_df),
           'total_quantity': scenario_df['quantity'].sum(),
           'total_quantity_filled': scenario_df['total_quantity_filled'].sum(),
           'avg_fill_ratio': scenario_df['fill_ratio'].mean(),
           'avg_execution_price': scenario_df['avg_execution_price'].mean(),
           'avg_execution_duration_seconds': scenario_df['execution_duration_seconds'].mean(),
           'symbol_count': scenario_df['symbol'].nunique(),
           'participant_count': scenario_df['participant_id'].nunique()
       }
   ```

5. **Store 3 Separate Files**
   ```python
   scenario_a.to_parquet('processed_files/scenario_a_immediate_full.parquet')
   scenario_b.to_parquet('processed_files/scenario_b_eventual_full.parquet')
   scenario_c.to_parquet('processed_files/scenario_c_partial_none.parquet')
   ```

6. **Create Summary Report**
   - Path: `processed_files/scenario_summary.csv`
   - Columns: Scenario, OrderCount, TotalQuantity, TotalFilled, AvgFillRatio, etc.

**Code template:**
```python
def classify_sweep_outcomes(sweep_df):
    # Split
    scenario_a = sweep_df[(sweep_df['fill_ratio'] >= 0.99) & (sweep_df['execution_duration_seconds'] < 1.0)].copy()
    scenario_b = sweep_df[(sweep_df['fill_ratio'] >= 0.99) & (sweep_df['execution_duration_seconds'] >= 1.0)].copy()
    scenario_c = sweep_df[sweep_df['fill_ratio'] < 0.99].copy()
    
    # Add labels
    scenario_a['scenario_classification'] = 'A'
    scenario_b['scenario_classification'] = 'B'
    scenario_c['scenario_classification'] = 'C'
    
    # Save
    scenario_a.to_parquet('processed_files/scenario_a_immediate_full.parquet')
    scenario_b.to_parquet('processed_files/scenario_b_eventual_full.parquet')
    scenario_c.to_parquet('processed_files/scenario_c_partial_none.parquet')
    
    # Summary
    summary = pd.DataFrame({
        'Scenario': ['A', 'B', 'C'],
        'OrderCount': [len(scenario_a), len(scenario_b), len(scenario_c)],
        'TotalQuantity': [scenario_a['quantity'].sum(), scenario_b['quantity'].sum(), scenario_c['quantity'].sum()],
        'TotalFilled': [scenario_a['total_quantity_filled'].sum(), scenario_b['total_quantity_filled'].sum(), scenario_c['total_quantity_filled'].sum()],
        'AvgFillRatio': [scenario_a['fill_ratio'].mean(), scenario_b['fill_ratio'].mean(), scenario_c['fill_ratio'].mean()]
    })
    summary.to_csv('processed_files/scenario_summary.csv', index=False)
    
    return scenario_a, scenario_b, scenario_c, summary
```

---

## Phase 3: Dark Book Simulation Engine

### Overview
**Core Idea:** Simulate what would happen if sweep orders rested in the dark book instead of going to lit market.

**Three Simulation Scenarios:**

---

### 3.1 Scenario A Simulation: Keep Sweep Orders in Dark Book

**Business Logic:**
- Real: Sweep order arrives → matches in dark pool (instantly) → leaves
- Simulation: Sweep order arrives → rests in dark book → matches against OTHER orders resting in dark

**Process:**

1. **For each Scenario A order (immediate full in real):**
   - Extract: orderid, symbol, side, price, quantity, arrival_timestamp
   - Example: OrderID=123, symbol=AAPL, side=BUY, price=100.50, qty=1000, timestamp=T0
   
2. **Query Dark Book at Order Arrival Time**
   - Look up dark_book[symbol][opposite_side]
   - Opposite side: if BUY, look at SELL; if SELL, look at BUY
   - Example: OrderID=123 is BUY at 100.50, so look for SELL orders at <= 100.50
   ```python
   # Pseudocode
   if order['side'] == 'BUY':
       sellside = dark_book['AAPL']['SELL']
       matching_prices = [p for p in sellside.keys() if p <= 100.50]  # Best first
   else:
       buyside = dark_book['AAPL']['BUY']
       matching_prices = [p for p in buyside.keys() if p >= 100.50]  # Best first
   ```

3. **Match Against Resting Orders (FIFO at each price level)**
   - Iterate through matching_prices (best first)
   - For each price level, iterate through orders in timestamp order
   - Execute trades until incoming order filled or no more counterparties
   ```python
   remaining_qty = order['quantity']
   matched_trades = []
   
   for price in matching_prices:
       for counterparty_orderid, counterparty_qty in dark_book[symbol][opposite_side][price]:
           match_qty = min(remaining_qty, counterparty_qty)
           matched_trades.append({
               'order_id': order['orderid'],
               'counterparty_id': counterparty_orderid,
               'price': price,
               'quantity': match_qty,
               'simulated_timestamp': arrival_timestamp
           })
           remaining_qty -= match_qty
           if remaining_qty == 0:
               break
       if remaining_qty == 0:
           break
   
   simulated_fill_ratio = (order['quantity'] - remaining_qty) / order['quantity']
   ```

4. **Calculate Simulation Metrics**
   - simulated_fill_ratio: (matched qty) / (order qty)
   - simulated_execution_price: volume-weighted price of matches
   - simulated_num_trades: number of matching trades
   - simulated_execution_duration: 0 (all matched at arrival) or actual time to fill if partial
   - comparison_to_real:
     - real_fill_ratio vs simulated_fill_ratio
     - real_execution_price vs simulated_execution_price
     - cost_diff = (simulated_fill_ratio * simulated_price) - (real_fill_ratio * real_price)

5. **Store Results**
   - Path: `processed_files/scenario_a_simulation_results.parquet`
   - Columns: orderid, symbol, side, price, arrival_timestamp, real_fill_ratio, real_execution_price, simulated_fill_ratio, simulated_execution_price, simulated_num_trades, cost_diff, matched_counterparties (list)

**Code template:**
```python
def simulate_scenario_a(scenario_a_orders, dark_book, order_index):
    """
    Scenario A: Immediately filled sweep orders stay in dark book
    Simulate matching against resting orders in dark book
    """
    results = []
    
    for _, order in scenario_a_orders.iterrows():
        orderid = order['orderid']
        symbol = order['symbol']
        side = order['side']
        price = order['price']
        quantity = order['quantity']
        arrival_ts = order['timestamp']
        
        # Query dark book
        opposite_side = 'SELL' if side == 'BUY' else 'BUY'
        matching_prices = sorted(
            [p for p in dark_book[symbol][opposite_side].keys() 
             if (side == 'BUY' and p <= price) or (side == 'SELL' and p >= price)],
            key=lambda x: -x if side == 'BUY' else x  # Best price first
        )
        
        # Match
        remaining_qty = quantity
        matched_trades = []
        simulated_price_sum = 0  # for weighted average
        
        for match_price in matching_prices:
            for counterparty_orderid, counterparty_qty in dark_book[symbol][opposite_side][match_price]:
                match_qty = min(remaining_qty, counterparty_qty)
                matched_trades.append(counterparty_orderid)
                simulated_price_sum += match_price * match_qty
                remaining_qty -= match_qty
                if remaining_qty == 0:
                    break
            if remaining_qty == 0:
                break
        
        # Calculate metrics
        simulated_fill_qty = quantity - remaining_qty
        simulated_fill_ratio = simulated_fill_qty / quantity if quantity > 0 else 0
        simulated_execution_price = simulated_price_sum / simulated_fill_qty if simulated_fill_qty > 0 else 0
        
        real_fill_ratio = order['fill_ratio']
        real_execution_price = order['avg_execution_price']
        
        cost_diff = (simulated_fill_ratio * simulated_execution_price) - (real_fill_ratio * real_execution_price)
        
        results.append({
            'orderid': orderid,
            'symbol': symbol,
            'side': side,
            'price': price,
            'arrival_timestamp': arrival_ts,
            'real_fill_ratio': real_fill_ratio,
            'real_execution_price': real_execution_price,
            'simulated_fill_ratio': simulated_fill_ratio,
            'simulated_execution_price': simulated_execution_price,
            'simulated_num_matches': len(matched_trades),
            'cost_diff': cost_diff,
            'matched_counterparties': matched_trades
        })
    
    results_df = pd.DataFrame(results)
    results_df.to_parquet('processed_files/scenario_a_simulation_results.parquet')
    return results_df
```

---

### 3.2 Scenario B Simulation: Keep Partially Filled Orders in Dark Book

**Business Logic:**
- Real: Sweep order partially fills → residual goes to lit market
- Simulation: Residual rests in dark book until EOD → test for dark pool matches at midpoint

**Process:**

1. **For each Scenario C order (partial or no fill in real):**
   - Extract: orderid, symbol, side, price, quantity, actual_fill_qty, residual_qty = qty - actual_fill_qty
   - Example: OrderID=456, qty=2000, real_fill=500, residual=1500
   
2. **Add Residual to Dark Book**
   - Treat residual as a resting order at midpoint price
   - midpoint_price = (best_bid + best_ask) / 2 at time of fill
   - Or: use the order's original price as proxy
   - Add to dark_book[symbol][side][price] with late timestamp
   ```python
   residual_price = order['price']  # Use original price as dark pool midpoint
   # Insert residual into dark book as newest order at that price
   ```

3. **Match Residual Against Other Resting Orders**
   - Similar to Scenario A: iterate through opposite side
   - Match from best price inward
   - Continue until residual exhausted or no more counterparties
   ```python
   remaining_qty = residual_qty
   matched_trades = []
   
   for price in matching_prices:
       for counterparty_orderid, counterparty_qty in dark_book[symbol][opposite_side][price]:
           # Skip if counterparty was already filled in real execution
           if counterparty_orderid in filled_order_ids:
               continue
           
           match_qty = min(remaining_qty, counterparty_qty)
           matched_trades.append({
               'counterparty_id': counterparty_orderid,
               'price': price,
               'qty': match_qty
           })
           remaining_qty -= match_qty
           if remaining_qty == 0:
               break
   ```

4. **Calculate Simulation Metrics**
   - residual_fill_ratio: (matched from residual qty) / (residual qty)
   - total_simulated_fill_ratio: (real_fill + residual_matched) / (original qty)
   - execution_price comparison
   - cost_diff: compare real outcome vs simulated outcome

5. **Store Results**
   - Path: `processed_files/scenario_b_simulation_results.parquet`
   - Columns: orderid, symbol, side, real_fill_qty, residual_qty, residual_fill_qty, total_simulated_fill_qty, simulated_fill_ratio, cost_diff, matched_counterparties

**Code template:**
```python
def simulate_scenario_b(scenario_c_orders, all_orders, dark_book, order_index, trades_agg):
    """
    Scenario B: Partially executed orders (Scenario C) with residual in dark book
    Simulate matching residual against resting orders in dark book
    """
    results = []
    
    for _, order in scenario_c_orders.iterrows():
        orderid = order['orderid']
        symbol = order['symbol']
        side = order['side']
        price = order['price']
        quantity = order['quantity']
        real_fill_qty = order['total_quantity_filled']
        residual_qty = quantity - real_fill_qty
        
        # Query dark book for matching
        opposite_side = 'SELL' if side == 'BUY' else 'BUY'
        matching_prices = sorted(
            [p for p in dark_book[symbol][opposite_side].keys() 
             if (side == 'BUY' and p <= price) or (side == 'SELL' and p >= price)],
            key=lambda x: -x if side == 'BUY' else x
        )
        
        # Get filled order ids to avoid double-matching
        filled_order_ids = set(trades_agg.index)
        
        # Match residual
        remaining_qty = residual_qty
        matched_trades = []
        simulated_price_sum = 0
        
        for match_price in matching_prices:
            for counterparty_orderid, counterparty_qty in dark_book[symbol][opposite_side][match_price]:
                if counterparty_orderid in filled_order_ids:
                    continue  # Skip already-filled orders
                
                match_qty = min(remaining_qty, counterparty_qty)
                matched_trades.append(counterparty_orderid)
                simulated_price_sum += match_price * match_qty
                remaining_qty -= match_qty
                if remaining_qty == 0:
                    break
            if remaining_qty == 0:
                break
        
        residual_fill_qty = residual_qty - remaining_qty
        total_simulated_fill_qty = real_fill_qty + residual_fill_qty
        simulated_fill_ratio = total_simulated_fill_qty / quantity
        
        results.append({
            'orderid': orderid,
            'symbol': symbol,
            'side': side,
            'real_fill_qty': real_fill_qty,
            'residual_qty': residual_qty,
            'residual_fill_qty': residual_fill_qty,
            'total_simulated_fill_qty': total_simulated_fill_qty,
            'simulated_fill_ratio': simulated_fill_ratio,
            'real_fill_ratio': real_fill_qty / quantity,
            'matched_counterparties': matched_trades
        })
    
    results_df = pd.DataFrame(results)
    results_df.to_parquet('processed_files/scenario_b_simulation_results.parquet')
    return results_df
```

---

### 3.3 Scenario C Simulation: Completely Unexecuted Orders in Dark Book

**Business Logic:**
- Real: Sweep order gets ZERO fills → presumably sent to lit
- Simulation: Order rests in dark book for ENTIRE session → test for dark pool matches

**Process:**

1. **For each Scenario C order with zero real fills:**
   - Extract: orderid, symbol, side, price, quantity, arrival_timestamp
   
2. **Rest Order in Dark Book for Entire Session**
   - Keep order in dark book from arrival_timestamp to EOD
   - Allow it to match any other order arriving in that time window
   ```python
   # Treat as if order is resting in dark book
   # Match against ALL orders (not just those in initial dark_book)
   ```

3. **Build Matching Universe**
   - Consider all other Centre Point orders as potential counterparties
   - Filter: opposite side, symbol, price compatible
   - Filter: arrival time >= order's arrival time (orders can only match with later arrivals or earlier resting)
   
4. **Simulate Matching Process**
   - For each potential counterparty (in order of arrival):
     - Check if price compatible
     - Execute partial match if needed
     - Continue until order filled or session ends
   ```python
   for potential_order in all_orders:
       if potential_order['symbol'] != symbol:
           continue
       if potential_order['side'] == side:  # Same side, no match
           continue
       if potential_order['arrival_timestamp'] < order['arrival_timestamp']:  # Skip earlier
           continue
       
       # Check price compatibility
       if side == 'BUY' and potential_order['side'] == 'SELL':
           if order['price'] >= potential_order['price']:
               # Match possible
       elif side == 'SELL' and potential_order['side'] == 'BUY':
           if order['price'] <= potential_order['price']:
               # Match possible
       
       # Execute match
       ...
   ```

5. **Calculate Metrics**
   - simulated_fill_qty: how much would have matched in dark book
   - simulated_fill_ratio: simulated_fill_qty / quantity
   - Execution price (if any)
   - real_fill_ratio = 0 (by definition of Scenario C)

6. **Store Results**
   - Path: `processed_files/scenario_c_simulation_results.parquet`

**Code template:**
```python
def simulate_scenario_c(scenario_c_no_fill_orders, all_centrepoint_orders, dark_book):
    """
    Scenario C: Completely unexecuted orders resting in dark book
    Simulate matching against all other Centre Point orders arriving later
    """
    results = []
    
    for _, order in scenario_c_no_fill_orders[scenario_c_no_fill_orders['total_quantity_filled'] == 0].iterrows():
        orderid = order['orderid']
        symbol = order['symbol']
        side = order['side']
        price = order['price']
        quantity = order['quantity']
        arrival_ts = order['timestamp']
        
        # Find potential counterparties
        opposite_side = 'SELL' if side == 'BUY' else 'BUY'
        potential_counterparties = all_centrepoint_orders[
            (all_centrepoint_orders['symbol'] == symbol) &
            (all_centrepoint_orders['side'] == opposite_side) &
            (all_centrepoint_orders['timestamp'] >= arrival_ts)  # Can only match with later arrivals
        ].sort_values('timestamp')
        
        # Match
        remaining_qty = quantity
        matched_trades = []
        simulated_price_sum = 0
        
        for _, counterparty in potential_counterparties.iterrows():
            # Check price compatibility
            if side == 'BUY' and counterparty['price'] <= price:
                # Match possible at counterparty's price
                match_price = counterparty['price']
            elif side == 'SELL' and counterparty['price'] >= price:
                # Match possible at counterparty's price
                match_price = counterparty['price']
            else:
                continue  # No match
            
            # Execute match
            counterparty_qty = counterparty['quantity']
            match_qty = min(remaining_qty, counterparty_qty)
            
            matched_trades.append(counterparty['orderid'])
            simulated_price_sum += match_price * match_qty
            remaining_qty -= match_qty
            
            if remaining_qty == 0:
                break
        
        simulated_fill_qty = quantity - remaining_qty
        simulated_fill_ratio = simulated_fill_qty / quantity if quantity > 0 else 0
        simulated_execution_price = simulated_price_sum / simulated_fill_qty if simulated_fill_qty > 0 else 0
        
        results.append({
            'orderid': orderid,
            'symbol': symbol,
            'side': side,
            'price': price,
            'real_fill_ratio': 0,  # By definition, no real execution
            'simulated_fill_ratio': simulated_fill_ratio,
            'simulated_execution_price': simulated_execution_price,
            'matched_counterparties': matched_trades
        })
    
    results_df = pd.DataFrame(results)
    results_df.to_parquet('processed_files/scenario_c_simulation_results.parquet')
    return results_df
```

---

## Phase 4: Reporting & Metrics Aggregation

### 4.1 Generate Comparison Reports

**Objective:** Create CSV reports comparing real execution vs 3 simulated scenarios

**Output Files:**

**1. scenario_comparison_summary.csv**
```
Scenario,OrderCount,TotalQuantity,TotalQuantityFilled,AvgFillRatio,AvgExecutionPrice,MedianExecutionPrice,FillRatioStdDev

Real Execution,N,Q_total,Q_filled,F,P_avg,P_median,σ_f
Simulated_A,N_A,Q_A_total,Q_A_filled,F_A,P_A_avg,P_A_median,σ_A
Simulated_B,N_B,Q_B_total,Q_B_filled,F_B,P_B_avg,P_B_median,σ_B
Simulated_C,N_C,Q_C_total,Q_C_filled,F_C,P_C_avg,P_C_median,σ_C
```

**2. scenario_detailed_comparison.csv**
```
Metric,Real_Execution,Simulated_A,Simulated_B,Simulated_C

Total Orders,N_total,N_A,N_B,N_C
Full Fills (>= 99%),N_full_real,N_A_full,N_B_full,N_C_full
Partial Fills,N_partial_real,N_A_partial,N_B_partial,N_C_partial
No Fills,N_zero_real,N_A_zero,N_B_zero,N_C_zero
Total Quantity,Q_real,Q_A,Q_B,Q_C
Total Filled,Q_fill_real,Q_fill_A,Q_fill_B,Q_fill_C
Avg Fill Ratio,F_real,F_A,F_B,F_C
Avg Execution Price,P_real,P_A,P_B,P_C
Avg Execution Cost (per share),C_real,C_A,C_B,C_C
Median Fill Time (seconds),T_real,T_A,T_B,T_C
```

**3. order_level_detail.csv**
```
OrderID,Symbol,Side,ArrivalPrice,OrderQuantity,RealFillRatio,RealExecutionPrice,RealFillDuration_sec,ScenarioA_FillRatio,ScenarioA_ExecPrice,ScenarioA_NumMatches,CostDiff_A,ScenarioB_FillRatio,ScenarioB_ExecPrice,CostDiff_B,ScenarioC_FillRatio,ScenarioC_ExecPrice,CostDiff_C
```

**4. execution_cost_comparison.csv**
```
Scenario,TotalExecutionCost,AvgCostPerShare,CostDiff_vs_Real,OrdersImproved,OrdersWorsened,AvgCostImprovement_BPS

Real,C_real,C_real_per_share,0,N,N,0
Scenario_A,C_A,C_A_per_share,ΔC_A,N_imp_A,N_worse_A,Δ_bps_A
Scenario_B,C_B,C_B_per_share,ΔC_B,N_imp_B,N_worse_B,Δ_bps_B
Scenario_C,C_C,C_C_per_share,ΔC_C,N_imp_C,N_worse_C,Δ_bps_C
```

**5. by_participant.csv**
```
ParticipantID,RealFillRatio,ScenarioA_FillRatio,ScenarioB_FillRatio,ScenarioC_FillRatio,RealAvgExecPrice,ScenarioA_AvgExecPrice,ScenarioB_AvgExecPrice,ScenarioC_AvgExecPrice
```

---

### Implementation Details:

**Step 1: Merge Simulation Results**
```python
def generate_reports(scenario_a_real, scenario_b_real, scenario_c_real,
                     scenario_a_sim_results, scenario_b_sim_results, scenario_c_sim_results):
    # Merge real execution data with simulated results
    scenario_a_merged = scenario_a_real.merge(scenario_a_sim_results, on='orderid', how='left')
    scenario_b_merged = scenario_c_real.merge(scenario_b_sim_results, on='orderid', how='left')
    scenario_c_merged = scenario_c_real.merge(scenario_c_sim_results, on='orderid', how='left')
```

**Step 2: Calculate Aggregated Metrics**
```python
def calculate_metrics(scenario_df, scenario_name):
    metrics = {
        'Scenario': scenario_name,
        'OrderCount': len(scenario_df),
        'TotalQuantity': scenario_df['quantity'].sum(),
        'TotalQuantityFilled': scenario_df[f'{scenario_name}_simulated_fill_qty'].sum(),
        'AvgFillRatio': scenario_df[f'{scenario_name}_simulated_fill_ratio'].mean(),
        'AvgExecutionPrice': scenario_df[f'{scenario_name}_simulated_execution_price'].mean(),
        'NumFullFills': (scenario_df[f'{scenario_name}_simulated_fill_ratio'] >= 0.99).sum(),
        'NumPartialFills': ((scenario_df[f'{scenario_name}_simulated_fill_ratio'] > 0) & 
                            (scenario_df[f'{scenario_name}_simulated_fill_ratio'] < 0.99)).sum(),
        'NumNoFills': (scenario_df[f'{scenario_name}_simulated_fill_ratio'] == 0).sum()
    }
    return metrics
```

**Step 3: Write CSV Files**
```python
# Write each report
scenario_comparison.to_csv('processed_files/scenario_comparison_summary.csv', index=False)
detailed_comparison.to_csv('processed_files/scenario_detailed_comparison.csv', index=False)
order_detail.to_csv('processed_files/order_level_detail.csv', index=False)
# ... etc
```

---

## Summary of Data Flow

```
PHASE 1: DATA INGESTION
  Input: orders.csv, trades.csv
  ├─→ 1.1 Extract Centre Point Orders → centrepoint_orders_raw.parquet
  ├─→ 1.2 Match Trades → centrepoint_trades_raw.parquet, centrepoint_trades_agg.parquet
  └─→ 1.3 Build Dark Book → dark_book_state.pkl, dark_book_orders.parquet

PHASE 2: CLASSIFICATION
  Input: centrepoint_orders_raw.parquet, centrepoint_trades_agg.parquet
  ├─→ 2.1 Filter Sweep Orders → sweep_orders_with_trades.parquet
  └─→ 2.2 Classify Outcomes
        ├─→ scenario_a_immediate_full.parquet (Scenario A: immediate 100% fill)
        ├─→ scenario_b_eventual_full.parquet (Scenario B: eventual 100% fill)
        └─→ scenario_c_partial_none.parquet (Scenario C: < 100% fill)

PHASE 3: SIMULATION
  Input: scenario_a/b/c.parquet, dark_book_state.pkl, all centrepoint_orders
  ├─→ 3.1 Scenario A Sim → scenario_a_simulation_results.parquet
  ├─→ 3.2 Scenario B Sim → scenario_b_simulation_results.parquet
  └─→ 3.3 Scenario C Sim → scenario_c_simulation_results.parquet

PHASE 4: REPORTING
  Input: All simulation results
  ├─→ 4.1 scenario_comparison_summary.csv
  ├─→ 4.2 scenario_detailed_comparison.csv
  ├─→ 4.3 order_level_detail.csv
  ├─→ 4.4 execution_cost_comparison.csv
  └─→ 4.5 by_participant.csv
```

---

## Key Implementation Notes

1. **Memory Management:**
   - Dark book: Store as dict of dicts, can be pickled
   - Large dataframes: Use parquet with compression
   - Chunk processing for 50GB trades file
   - Consider using Polars for better performance

2. **Order Matching Logic:**
   - FIFO within price level: timestamp order
   - Price priority: best prices first
   - Partial matches allowed
   - Skip already-filled orders in Scenario B/C

3. **Execution Price Calculation:**
   - Volume-weighted average: Σ(price × quantity) / Σ(quantity)
   - For simulation, use actual matching prices (not midpoint)

4. **Configuration:**
   - Chunk sizes
   - Time thresholds (< 1 second for Scenario A vs B)
   - Fill ratio threshold (>= 0.99 for "full")
   - Tolerance for rounding errors

5. **Validation:**
   - Orders should not be double-counted
   - Fill ratios should be 0 ≤ ratio ≤ 1
   - Total filled qty ≤ total order qty
   - Test on subset before full run

