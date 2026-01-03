"""
Centre Point Orders Dual Posting Simulation with Sweep Order Partitioning
Partitions sweep orders into three categories for separate analysis:
1. Immediately Executed Orders
2. Eventually Fully Executed Orders  
3. Partially Executed Orders
"""

import pandas as pd
import numpy as np
from bisect import bisect_right
from typing import Dict, List, Tuple, Optional, Set
import warnings
warnings.filterwarnings('ignore')


# =============================================================================
# CENTRE POINT ORDER TYPES
# =============================================================================

CENTRE_POINT_ORDER_TYPES = {
    64: "Centre Point Order",
    256: "Centre Point Crossing",
    2048: "Centre Point Sweep Order",
    4096: "Centre Point Block Order",
    4098: "Centre Point Sweep and Block"
}

DARK_ONLY_TYPES = {64, 256, 4096}
SWEEP_TYPES = {2048, 4098}


# =============================================================================
# SWEEP ORDER EXECUTION CATEGORIES
# =============================================================================

class ExecutionCategory:
    """Enum for sweep order execution categories"""
    IMMEDIATE_FULL = "Immediate Full Execution"
    EVENTUAL_FULL = "Eventual Full Execution"
    PARTIAL = "Partial Execution"
    NO_EXECUTION = "No Execution"


def categorize_sweep_order(order_events: pd.DataFrame, trades: pd.DataFrame) -> str:
    """
    Categorize a sweep order based on its execution pattern.

    Args:
        order_events: All events for this order
        trades: All trades for this order

    Returns:
        Category string
    """
    if len(trades) == 0:
        return ExecutionCategory.NO_EXECUTION

    # Get initial submission
    initial_event = order_events[order_events['changereason'] == 6].iloc[0]
    initial_quantity = initial_event['quantity']

    # Check if fully executed immediately (within 1 second of submission)
    first_trade_time = trades.iloc[0]['tradetime']
    submission_time = initial_event['timestamp']
    time_to_first_trade = first_trade_time - submission_time

    total_traded = trades['quantity'].sum()

    # Immediate full execution (within 1 second)
    if total_traded == initial_quantity and time_to_first_trade <= 1_000_000_000:  # 1 second in nanoseconds
        return ExecutionCategory.IMMEDIATE_FULL

    # Eventual full execution
    elif total_traded == initial_quantity:
        return ExecutionCategory.EVENTUAL_FULL

    # Partial execution
    elif total_traded < initial_quantity:
        return ExecutionCategory.PARTIAL

    else:
        return ExecutionCategory.NO_EXECUTION


# =============================================================================
# PART 1: MEMORY-EFFICIENT DATA LOADING (Same as before)
# =============================================================================

def filter_centre_point_orders(orders_file: str, chunk_size: int = 100000) -> pd.DataFrame:
    """Filter orders file for ALL Centre Point orders."""
    print(f"\nFiltering Centre Point orders from {orders_file}...")
    print(f"Looking for order types: {CENTRE_POINT_ORDER_TYPES}")
    print("Reading in chunks to save memory...")

    cp_orders_list = []
    total_rows = 0
    cp_count_by_type = {ot: 0 for ot in CENTRE_POINT_ORDER_TYPES.keys()}

    for chunk_num, chunk in enumerate(pd.read_csv(orders_file, chunksize=chunk_size), 1):
        total_rows += len(chunk)

        cp_chunk = chunk[chunk['exchangeordertype'].isin(CENTRE_POINT_ORDER_TYPES.keys())].copy()

        if len(cp_chunk) > 0:
            cp_orders_list.append(cp_chunk)

            for order_type in CENTRE_POINT_ORDER_TYPES.keys():
                cp_count_by_type[order_type] += len(cp_chunk[cp_chunk['exchangeordertype'] == order_type])

        if chunk_num % 10 == 0:
            total_cp = sum(cp_count_by_type.values())
            print(f"  Processed {total_rows:,} rows | Found {total_cp:,} Centre Point orders")

    total_cp = sum(cp_count_by_type.values())
    print(f"\nCompleted: Processed {total_rows:,} total rows")
    print(f"Found {total_cp:,} Centre Point orders ({total_cp/total_rows*100:.2f}%)")
    print("\nBreakdown by type:")
    for order_type, count in cp_count_by_type.items():
        print(f"  {CENTRE_POINT_ORDER_TYPES[order_type]:<35} ({order_type:>4}): {count:>10,} orders")

    if len(cp_orders_list) == 0:
        print("WARNING: No Centre Point orders found!")
        return pd.DataFrame()

    cp_orders = pd.concat(cp_orders_list, ignore_index=True)
    del cp_orders_list

    return cp_orders


def filter_trades_for_orders(trades_file: str, orderids: Set[int], chunk_size: int = 100000) -> pd.DataFrame:
    """Filter trades file for specific orders only."""
    print(f"\nFiltering trades from {trades_file}...")
    print(f"Looking for {len(orderids):,} order IDs")
    print("Reading in chunks to save memory...")

    trades_list = []
    total_rows = 0
    trade_count = 0

    for chunk_num, chunk in enumerate(pd.read_csv(trades_file, chunksize=chunk_size), 1):
        total_rows += len(chunk)

        trade_chunk = chunk[chunk['orderid'].isin(orderids)].copy()

        if len(trade_chunk) > 0:
            trades_list.append(trade_chunk)
            trade_count += len(trade_chunk)

        if chunk_num % 10 == 0:
            print(f"  Processed {total_rows:,} rows | Found {trade_count:,} matching trades")

    print(f"\nCompleted: Processed {total_rows:,} total rows")
    print(f"Found {trade_count:,} trades for Centre Point orders")

    if len(trades_list) == 0:
        print("WARNING: No trades found for Centre Point orders!")
        return pd.DataFrame()

    trades_df = pd.concat(trades_list, ignore_index=True)
    del trades_list

    return trades_df


def filter_nbbo_for_securities(nbbo_file: str, security_ids: Set[int], chunk_size: int = 100000) -> pd.DataFrame:
    """Filter NBBO file for relevant securities only."""
    print(f"\nFiltering NBBO data from {nbbo_file}...")
    print(f"Looking for {len(security_ids):,} securities")
    print("Reading in chunks to save memory...")

    nbbo_list = []
    total_rows = 0
    nbbo_count = 0

    for chunk_num, chunk in enumerate(pd.read_csv(nbbo_file, chunksize=chunk_size), 1):
        total_rows += len(chunk)

        nbbo_chunk = chunk[chunk['orderbookid'].isin(security_ids)].copy()

        if len(nbbo_chunk) > 0:
            nbbo_list.append(nbbo_chunk)
            nbbo_count += len(nbbo_chunk)

        if chunk_num % 10 == 0:
            print(f"  Processed {total_rows:,} rows | Found {nbbo_count:,} relevant NBBO records")

    print(f"\nCompleted: Processed {total_rows:,} total rows")
    print(f"Found {nbbo_count:,} NBBO records for {len(security_ids)} securities")

    if len(nbbo_list) == 0:
        print("WARNING: No NBBO data found for securities!")
        return pd.DataFrame()

    nbbo_df = pd.concat(nbbo_list, ignore_index=True)
    del nbbo_list

    return nbbo_df


def load_centre_point_data_only(orders_file: str, trades_file: str, nbbo_file: str,
                                chunk_size: int = 100000) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Memory-efficient loading: Filter for Centre Point orders first."""
    print("="*80)
    print("MEMORY-EFFICIENT DATA LOADING")
    print("="*80)

    cp_orders = filter_centre_point_orders(orders_file, chunk_size)

    if len(cp_orders) == 0:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    print(f"\nMemory usage of Centre Point orders: {cp_orders.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    cp_orderids = set(cp_orders['orderid'].unique())
    security_col = 'orderbookid' if 'orderbookid' in cp_orders.columns else 'securitycode'
    security_ids = set(cp_orders[security_col].unique())

    print(f"\nFound {len(cp_orderids):,} unique Centre Point orders")
    print(f"Found {len(security_ids):,} unique securities")

    cp_trades = filter_trades_for_orders(trades_file, cp_orderids, chunk_size)

    if len(cp_trades) > 0:
        print(f"Memory usage of Centre Point trades: {cp_trades.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    nbbo_df = filter_nbbo_for_securities(nbbo_file, security_ids, chunk_size)

    if len(nbbo_df) > 0:
        print(f"Memory usage of NBBO data: {nbbo_df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    total_memory = (
        cp_orders.memory_usage(deep=True).sum() + 
        cp_trades.memory_usage(deep=True).sum() + 
        nbbo_df.memory_usage(deep=True).sum()
    ) / 1024**2

    print(f"\nTotal memory usage: {total_memory:.2f} MB")
    print("="*80)

    return cp_orders, cp_trades, nbbo_df


# =============================================================================
# PART 2: CANDIDATE IDENTIFICATION WITH PARTITIONING
# =============================================================================

def identify_and_partition_candidates(cp_orders: pd.DataFrame, 
                                      cp_trades: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Identify candidates and partition sweep orders into execution categories.

    Returns:
        Dict with keys: 'all', 'immediate_full', 'eventual_full', 'partial'
    """
    print("\n" + "="*80)
    print("IDENTIFYING AND PARTITIONING CANDIDATE ORDERS")
    print("="*80)

    # Get initial submission events
    initial_submissions = cp_orders[cp_orders['changereason'] == 6].copy()
    print(f"Initial submissions: {len(initial_submissions):,}")

    # Filter for sweep orders only
    sweep_submissions = initial_submissions[initial_submissions['exchangeordertype'].isin(SWEEP_TYPES)].copy()
    print(f"Sweep order submissions: {len(sweep_submissions):,}")

    # Filter for not fully executed on submission
    partially_filled = sweep_submissions[sweep_submissions['leavesquantity'] > 0].copy()
    print(f"Not fully executed on submission: {len(partially_filled):,}")

    # Get unique order IDs
    candidate_orderids = set(partially_filled['orderid'].unique())
    print(f"\nPartitioning {len(candidate_orderids):,} sweep orders by execution pattern...")

    # Partition by execution category
    partitions = {
        ExecutionCategory.IMMEDIATE_FULL: [],
        ExecutionCategory.EVENTUAL_FULL: [],
        ExecutionCategory.PARTIAL: []
    }

    for i, orderid in enumerate(candidate_orderids):
        if (i + 1) % 1000 == 0:
            print(f"  Categorized {i+1}/{len(candidate_orderids)} orders")

        order_events = cp_orders[cp_orders['orderid'] == orderid]
        order_trades = cp_trades[cp_trades['orderid'] == orderid]

        category = categorize_sweep_order(order_events, order_trades)

        if category in partitions:
            partitions[category].append(orderid)

    # Create DataFrames for each partition
    result = {}

    print("\n" + "="*80)
    print("PARTITIONING RESULTS")
    print("="*80)

    for category, orderids in partitions.items():
        if len(orderids) > 0:
            partition_df = partially_filled[partially_filled['orderid'].isin(orderids)].copy()
            partition_df['execution_category'] = category
            result[category] = partition_df
            print(f"{category:<35}: {len(orderids):>10,} orders ({len(partition_df):,} records)")
        else:
            result[category] = pd.DataFrame()
            print(f"{category:<35}: {0:>10,} orders")

    # Create combined DataFrame
    all_partitions = []
    for category, df in result.items():
        if len(df) > 0:
            all_partitions.append(df)

    if len(all_partitions) > 0:
        result['all'] = pd.concat(all_partitions, ignore_index=True)
        print(f"\n{'Total partitioned orders':<35}: {len(result['all']['orderid'].unique()):>10,} orders")
    else:
        result['all'] = pd.DataFrame()

    return result


# =============================================================================
# PART 3: NBBO LOOKUP (Same as before)
# =============================================================================

class NBBOLookup:
    """Fast lookup structure for NBBO data using binary search."""

    def __init__(self, nbbo_df: pd.DataFrame):
        self.nbbo_by_security = {}

        print("Building NBBO lookup index...")
        securities = nbbo_df['orderbookid'].unique()

        for i, orderbookid in enumerate(securities):
            if (i + 1) % 100 == 0:
                print(f"  Indexed {i+1}/{len(securities)} securities")

            security_nbbo = nbbo_df[nbbo_df['orderbookid'] == orderbookid].copy()
            security_nbbo = security_nbbo.sort_values('timestamp')
            security_nbbo['midpoint'] = (security_nbbo['bid'] + security_nbbo['ask']) / 2

            self.nbbo_by_security[orderbookid] = {
                'timestamps': security_nbbo['timestamp'].values,
                'bid': security_nbbo['bid'].values,
                'ask': security_nbbo['ask'].values,
                'midpoint': security_nbbo['midpoint'].values
            }

        print(f"NBBO index complete for {len(securities)} securities")

    def get_nbbo_at_time(self, orderbookid: int, timestamp: int) -> Optional[Dict]:
        if orderbookid not in self.nbbo_by_security:
            return None

        nbbo_data = self.nbbo_by_security[orderbookid]
        timestamps = nbbo_data['timestamps']

        idx = bisect_right(timestamps, timestamp) - 1

        if idx < 0:
            return None

        return {
            'bid': nbbo_data['bid'][idx],
            'ask': nbbo_data['ask'][idx],
            'midpoint': nbbo_data['midpoint'][idx],
            'timestamp': timestamps[idx]
        }


# =============================================================================
# PART 4: SIMULATION ENGINE (Same as before)
# =============================================================================

def calculate_tick_size(price: float) -> float:
    if price <= 0.01:
        return 0.001
    elif price <= 2.00:
        return 0.005
    else:
        return 0.01


def calculate_dark_price(midtick: int, nbbo: Dict, side: int) -> Optional[float]:
    midpoint = nbbo['midpoint']

    if midtick == 0:
        return None
    elif midtick in [1, 4]:
        return midpoint
    elif midtick == 3:
        tick_size = calculate_tick_size(midpoint)
        half_tick = tick_size / 2
        if side == 1:
            return midpoint + half_tick
        else:
            return midpoint - half_tick
    elif midtick == 2:
        return midpoint
    elif midtick in [5, 6]:
        return midpoint
    else:
        return midpoint


def simulate_dark_match(order_state: Dict, trade: Dict, nbbo: Dict) -> Dict:
    result = {'matched': False, 'quantity': 0, 'price': 0}

    dark_price = calculate_dark_price(order_state['midtick'], nbbo, order_state['side'])

    if dark_price is None:
        return result

    if order_state['side'] == 1:
        if dark_price > order_state['limit_price']:
            return result
    else:
        if dark_price < order_state['limit_price']:
            return result

    match_quantity = min(order_state['sim_remaining_quantity'], trade['quantity'])

    if order_state.get('singlefillminimumquantity', 2) == 1:
        if match_quantity < order_state.get('minimumquantity', 0):
            return result

    result['matched'] = True
    result['quantity'] = match_quantity
    result['price'] = dark_price

    return result


def run_simulation_for_partition(candidate_orders: pd.DataFrame, 
                                 cp_orders: pd.DataFrame,
                                 cp_trades: pd.DataFrame, 
                                 nbbo_lookup: NBBOLookup,
                                 partition_name: str) -> List[Dict]:
    """Run simulation for a specific partition of orders."""
    print(f"\nRunning simulation for: {partition_name}")

    simulation_results = []
    unique_orderids = candidate_orders['orderid'].unique()

    for i, orderid in enumerate(unique_orderids):
        if (i + 1) % 100 == 0:
            print(f"  Processing order {i+1}/{len(unique_orderids)}")

        order = candidate_orders[candidate_orders['orderid'] == orderid].iloc[0]

        state = {
            'orderid': orderid,
            'orderbookid': order.get('orderbookid', order.get('securitycode', 0)),
            'side': order['side'],
            'submission_time': order['timestamp'],
            'limit_price': order['price'],
            'initial_quantity': order['quantity'],
            'midtick': order.get('midtick', 0),
            'minimumquantity': order.get('minimumquantity', 0),
            'singlefillminimumquantity': order.get('singlefillminimumquantity', 2),
            'exchangeordertype': order['exchangeordertype'],
            'execution_category': order.get('execution_category', 'Unknown'),

            'real_lit_executions': [],
            'real_lit_quantity': 0,
            'real_lit_value': 0,
            'real_dark_executions': [],
            'real_dark_quantity': 0,
            'real_dark_value': 0,

            'sim_dark_executions': [],
            'sim_dark_quantity': 0,
            'sim_dark_value': 0,

            'sim_remaining_quantity': order['quantity'],
            'sim_lit_executions': [],
            'sim_lit_quantity': 0,
            'sim_lit_value': 0
        }

        order_trades = cp_trades[cp_trades['orderid'] == orderid].copy()
        order_trades = order_trades.sort_values('tradetime')

        # Store real executions
        for _, trade in order_trades.iterrows():
            execution = {
                'timestamp': trade['tradetime'],
                'quantity': trade['quantity'],
                'price': trade['tradeprice']
            }

            if trade.get('dealsource', 1) == 47:
                state['real_dark_executions'].append(execution)
                state['real_dark_quantity'] += trade['quantity']
                state['real_dark_value'] += trade['quantity'] * trade['tradeprice']
            else:
                state['real_lit_executions'].append(execution)
                state['real_lit_quantity'] += trade['quantity']
                state['real_lit_value'] += trade['quantity'] * trade['tradeprice']

        # Simulate dark pool matching
        for _, trade in order_trades.iterrows():
            if state['sim_remaining_quantity'] > 0:
                nbbo = nbbo_lookup.get_nbbo_at_time(state['orderbookid'], trade['tradetime'])

                if nbbo is not None:
                    trade_dict = {
                        'timestamp': trade['tradetime'],
                        'quantity': trade['quantity'],
                        'price': trade['tradeprice']
                    }

                    dark_match = simulate_dark_match(state, trade_dict, nbbo)

                    if dark_match['matched']:
                        execution = {
                            'timestamp': trade['tradetime'],
                            'quantity': dark_match['quantity'],
                            'price': dark_match['price'],
                            'venue': 'Centre Point'
                        }

                        state['sim_dark_executions'].append(execution)
                        state['sim_dark_quantity'] += dark_match['quantity']
                        state['sim_dark_value'] += dark_match['quantity'] * dark_match['price']
                        state['sim_remaining_quantity'] -= dark_match['quantity']

        # Simulate remaining lit execution
        for _, trade in order_trades.iterrows():
            if state['sim_remaining_quantity'] > 0:
                lit_quantity = min(state['sim_remaining_quantity'], trade['quantity'])

                execution = {
                    'timestamp': trade['tradetime'],
                    'quantity': lit_quantity,
                    'price': trade['tradeprice'],
                    'venue': 'ASX CLOB'
                }

                state['sim_lit_executions'].append(execution)
                state['sim_lit_quantity'] += lit_quantity
                state['sim_lit_value'] += lit_quantity * trade['tradeprice']
                state['sim_remaining_quantity'] -= lit_quantity

        simulation_results.append(state)

    print(f"  Simulation complete for {len(simulation_results)} orders")
    return simulation_results


# =============================================================================
# PART 5: METRICS CALCULATION
# =============================================================================

def calculate_execution_cost(executions: List[Dict], side: int, decision_price: float) -> float:
    if len(executions) == 0 or decision_price == 0:
        return 0.0

    total_cost = 0
    total_quantity = 0

    for execution in executions:
        if side == 1:
            slippage = execution['price'] - decision_price
        else:
            slippage = decision_price - execution['price']

        cost_bps = (slippage / decision_price) * 10000
        total_cost += cost_bps * execution['quantity']
        total_quantity += execution['quantity']

    if total_quantity > 0:
        return total_cost / total_quantity
    else:
        return 0.0


def calculate_metrics_for_partition(simulation_results: List[Dict], 
                                    nbbo_lookup: NBBOLookup,
                                    partition_name: str) -> Dict:
    """Calculate metrics for a specific partition."""
    print(f"\nCalculating metrics for: {partition_name}")

    metrics = {
        'partition_name': partition_name,
        'real_cp_full_fills': 0, 'real_cp_partial_fills': 0, 'real_cp_quantity': 0,
        'real_cp_total_qty': 0, 'real_cp_costs': [],
        'real_clob_full_fills': 0, 'real_clob_partial_fills': 0, 'real_clob_quantity': 0,
        'real_clob_total_qty': 0, 'real_clob_costs': [],
        'sim_cp_full_fills': 0, 'sim_cp_partial_fills': 0, 'sim_cp_quantity': 0,
        'sim_cp_total_qty': 0, 'sim_cp_costs': [],
        'sim_clob_full_fills': 0, 'sim_clob_partial_fills': 0, 'sim_clob_quantity': 0,
        'sim_clob_total_qty': 0, 'sim_clob_costs': []
    }

    for state in simulation_results:
        nbbo_at_submission = nbbo_lookup.get_nbbo_at_time(
            state['orderbookid'], state['submission_time']
        )

        if nbbo_at_submission is None:
            continue

        decision_price = nbbo_at_submission['midpoint']

        # Real Centre Point metrics
        if state['real_dark_quantity'] > 0:
            metrics['real_cp_quantity'] += state['real_dark_quantity']
            metrics['real_cp_total_qty'] += state['initial_quantity']

            total_real_qty = state['real_dark_quantity'] + state['real_lit_quantity']
            if total_real_qty == state['initial_quantity']:
                metrics['real_cp_full_fills'] += 1
            else:
                metrics['real_cp_partial_fills'] += 1

            cost = calculate_execution_cost(state['real_dark_executions'], state['side'], decision_price)
            metrics['real_cp_costs'].append(cost)

        # Real CLOB metrics
        if state['real_lit_quantity'] > 0:
            metrics['real_clob_quantity'] += state['real_lit_quantity']
            metrics['real_clob_total_qty'] += state['initial_quantity']

            total_real_qty = state['real_dark_quantity'] + state['real_lit_quantity']
            if total_real_qty == state['initial_quantity']:
                metrics['real_clob_full_fills'] += 1
            else:
                metrics['real_clob_partial_fills'] += 1

            cost = calculate_execution_cost(state['real_lit_executions'], state['side'], decision_price)
            metrics['real_clob_costs'].append(cost)

        # Simulated Centre Point metrics
        if state['sim_dark_quantity'] > 0:
            metrics['sim_cp_quantity'] += state['sim_dark_quantity']
            metrics['sim_cp_total_qty'] += state['initial_quantity']

            if state['sim_dark_quantity'] == state['initial_quantity']:
                metrics['sim_cp_full_fills'] += 1
            else:
                metrics['sim_cp_partial_fills'] += 1

            cost = calculate_execution_cost(state['sim_dark_executions'], state['side'], decision_price)
            metrics['sim_cp_costs'].append(cost)

        # Simulated CLOB metrics
        if state['sim_lit_quantity'] > 0:
            metrics['sim_clob_quantity'] += state['sim_lit_quantity']
            metrics['sim_clob_total_qty'] += state['initial_quantity'] - state['sim_dark_quantity']

            if state['sim_remaining_quantity'] == 0:
                metrics['sim_clob_full_fills'] += 1
            else:
                metrics['sim_clob_partial_fills'] += 1

            cost = calculate_execution_cost(state['sim_lit_executions'], state['side'], decision_price)
            metrics['sim_clob_costs'].append(cost)

    # Calculate averages
    metrics['real_cp_avg_cost'] = np.mean(metrics['real_cp_costs']) if metrics['real_cp_costs'] else 0
    metrics['real_clob_avg_cost'] = np.mean(metrics['real_clob_costs']) if metrics['real_clob_costs'] else 0
    metrics['sim_cp_avg_cost'] = np.mean(metrics['sim_cp_costs']) if metrics['sim_cp_costs'] else 0
    metrics['sim_clob_avg_cost'] = np.mean(metrics['sim_clob_costs']) if metrics['sim_clob_costs'] else 0

    # Calculate fill ratios
    metrics['real_cp_fill_ratio'] = metrics['real_cp_quantity'] / metrics['real_cp_total_qty'] if metrics['real_cp_total_qty'] > 0 else 0
    metrics['real_clob_fill_ratio'] = metrics['real_clob_quantity'] / metrics['real_clob_total_qty'] if metrics['real_clob_total_qty'] > 0 else 0
    metrics['sim_cp_fill_ratio'] = metrics['sim_cp_quantity'] / metrics['sim_cp_total_qty'] if metrics['sim_cp_total_qty'] > 0 else 0
    metrics['sim_clob_fill_ratio'] = metrics['sim_clob_quantity'] / metrics['sim_clob_total_qty'] if metrics['sim_clob_total_qty'] > 0 else 0

    return metrics


# =============================================================================
# PART 6: OUTPUT GENERATION
# =============================================================================

def generate_comparison_table(metrics: Dict, partition_name: str = "All Orders") -> pd.DataFrame:
    """Generate comparison table for a specific partition."""
    comparison_data = {
        'Metric': [
            'No. of Full Fills', 'No. of Partial Fills', 'Quantity Traded',
            'Total Order Quantity', 'Execution Cost (bps)', 'Order Fill Ratio'
        ],
        'Lit Posted (Real) - Centre Point': [
            metrics['real_cp_full_fills'], metrics['real_cp_partial_fills'],
            f"{metrics['real_cp_quantity']:,.0f}", f"{metrics['real_cp_total_qty']:,.0f}",
            f"{metrics['real_cp_avg_cost']:.4f}", f"{metrics['real_cp_fill_ratio']:.4f}"
        ],
        'Lit Posted (Real) - ASX CLOB': [
            metrics['real_clob_full_fills'], metrics['real_clob_partial_fills'],
            f"{metrics['real_clob_quantity']:,.0f}", f"{metrics['real_clob_total_qty']:,.0f}",
            f"{metrics['real_clob_avg_cost']:.4f}", f"{metrics['real_clob_fill_ratio']:.4f}"
        ],
        'Dual Post (Simulated) - Centre Point': [
            metrics['sim_cp_full_fills'], metrics['sim_cp_partial_fills'],
            f"{metrics['sim_cp_quantity']:,.0f}", f"{metrics['sim_cp_total_qty']:,.0f}",
            f"{metrics['sim_cp_avg_cost']:.4f}", f"{metrics['sim_cp_fill_ratio']:.4f}"
        ],
        'Dual Post (Simulated) - ASX CLOB': [
            metrics['sim_clob_full_fills'], metrics['sim_clob_partial_fills'],
            f"{metrics['sim_clob_quantity']:,.0f}", f"{metrics['sim_clob_total_qty']:,.0f}",
            f"{metrics['sim_clob_avg_cost']:.4f}", f"{metrics['sim_clob_fill_ratio']:.4f}"
        ]
    }

    df = pd.DataFrame(comparison_data)
    df.insert(0, 'Partition', partition_name)
    return df


def generate_order_detail_report(simulation_results: List[Dict], 
                                 nbbo_lookup: NBBOLookup) -> pd.DataFrame:
    """Generate detailed order-level report."""
    details = []

    for state in simulation_results:
        nbbo_at_submission = nbbo_lookup.get_nbbo_at_time(
            state['orderbookid'], state['submission_time']
        )

        if nbbo_at_submission is None:
            continue

        decision_price = nbbo_at_submission['midpoint']

        # Real metrics
        total_real_qty = state['real_dark_quantity'] + state['real_lit_quantity']
        total_real_value = state['real_dark_value'] + state['real_lit_value']
        real_vwap = total_real_value / total_real_qty if total_real_qty > 0 else 0

        all_real_executions = state['real_dark_executions'] + state['real_lit_executions']
        real_cost = calculate_execution_cost(all_real_executions, state['side'], decision_price)

        # Simulated metrics
        total_sim_quantity = state['sim_dark_quantity'] + state['sim_lit_quantity']
        total_sim_value = state['sim_dark_value'] + state['sim_lit_value']
        sim_vwap = total_sim_value / total_sim_quantity if total_sim_quantity > 0 else 0

        all_sim_executions = state['sim_dark_executions'] + state['sim_lit_executions']
        sim_cost = calculate_execution_cost(all_sim_executions, state['side'], decision_price)

        # Comparison
        cost_savings = real_cost - sim_cost
        dark_capture_rate = state['sim_dark_quantity'] / state['initial_quantity'] if state['initial_quantity'] > 0 else 0
        lit_reduction_rate = (state['real_lit_quantity'] - state['sim_lit_quantity']) / state['real_lit_quantity'] if state['real_lit_quantity'] > 0 else 0

        details.append({
            'orderid': state['orderid'],
            'orderbookid': state['orderbookid'],
            'execution_category': state.get('execution_category', 'Unknown'),
            'exchangeordertype': state['exchangeordertype'],
            'submission_time': state['submission_time'],
            'side': 'BUY' if state['side'] == 1 else 'SELL',
            'initial_quantity': state['initial_quantity'],
            'limit_price': state['limit_price'],
            'decision_price': decision_price,
            'real_dark_qty': state['real_dark_quantity'],
            'real_lit_qty': state['real_lit_quantity'],
            'real_vwap': real_vwap,
            'real_execution_cost_bps': real_cost,
            'sim_dark_qty': state['sim_dark_quantity'],
            'sim_lit_qty': state['sim_lit_quantity'],
            'sim_vwap': sim_vwap,
            'sim_execution_cost_bps': sim_cost,
            'cost_savings_bps': cost_savings,
            'dark_capture_rate': dark_capture_rate,
            'lit_reduction_rate': lit_reduction_rate
        })

    return pd.DataFrame(details)


# =============================================================================
# MAIN EXECUTION WITH PARTITIONING
# =============================================================================

def main(orders_file: str, 
         trades_file: str, 
         nbbo_file: str, 
         output_dir: str = './',
         chunk_size: int = 100000):
    """
    Main execution function with sweep order partitioning.
    """
    print("="*80)
    print("CENTRE POINT DUAL POSTING SIMULATION WITH PARTITIONING")
    print("="*80)

    # Step 1: Load data
    cp_orders, cp_trades, nbbo_df = load_centre_point_data_only(
        orders_file, trades_file, nbbo_file, chunk_size
    )

    if len(cp_orders) == 0:
        print("\nNo Centre Point orders found. Exiting.")
        return

    # Step 2: Identify and partition candidates
    partitions = identify_and_partition_candidates(cp_orders, cp_trades)

    if len(partitions['all']) == 0:
        print("\nNo candidate orders found. Exiting.")
        return

    # Step 3: Build NBBO lookup
    print("\n" + "="*80)
    print("BUILDING NBBO LOOKUP")
    print("="*80)
    nbbo_lookup = NBBOLookup(nbbo_df)
    del nbbo_df

    # Step 4: Run simulation for each partition
    print("\n" + "="*80)
    print("RUNNING SIMULATIONS")
    print("="*80)

    all_results = {}
    all_metrics = {}
    all_comparison_tables = []
    all_detail_reports = []

    # Simulate for each partition
    partition_order = [
        ('all', 'All Sweep Orders'),
        (ExecutionCategory.IMMEDIATE_FULL, ExecutionCategory.IMMEDIATE_FULL),
        (ExecutionCategory.EVENTUAL_FULL, ExecutionCategory.EVENTUAL_FULL),
        (ExecutionCategory.PARTIAL, ExecutionCategory.PARTIAL)
    ]

    for partition_key, partition_name in partition_order:
        if partition_key not in partitions or len(partitions[partition_key]) == 0:
            continue

        print(f"\n{'='*80}")
        print(f"PARTITION: {partition_name}")
        print(f"{'='*80}")

        # Run simulation
        results = run_simulation_for_partition(
            partitions[partition_key],
            cp_orders,
            cp_trades,
            nbbo_lookup,
            partition_name
        )

        all_results[partition_key] = results

        # Calculate metrics
        metrics = calculate_metrics_for_partition(results, nbbo_lookup, partition_name)
        all_metrics[partition_key] = metrics

        # Generate comparison table
        comparison_table = generate_comparison_table(metrics, partition_name)
        all_comparison_tables.append(comparison_table)

        # Generate detail report
        detail_report = generate_order_detail_report(results, nbbo_lookup)
        all_detail_reports.append(detail_report)

    # Free memory
    del cp_orders
    del cp_trades

    # Step 5: Combine and save outputs
    print("\n" + "="*80)
    print("SAVING RESULTS")
    print("="*80)

    # Combined comparison table
    combined_comparison = pd.concat(all_comparison_tables, ignore_index=True)
    comparison_file = f"{output_dir}/comparison_results_partitioned.csv"
    combined_comparison.to_csv(comparison_file, index=False)
    print(f"Saved: {comparison_file}")

    # Combined detail report
    combined_details = pd.concat(all_detail_reports, ignore_index=True)
    detail_file = f"{output_dir}/order_detail_report_partitioned.csv"
    combined_details.to_csv(detail_file, index=False)
    print(f"Saved: {detail_file}")

    # Save individual partition reports
    for partition_key, partition_name in partition_order:
        if partition_key not in all_results:
            continue

        safe_name = partition_name.replace(' ', '_').lower()
        partition_detail_file = f"{output_dir}/detail_{safe_name}.csv"

        partition_details = [d for d in all_detail_reports 
                           if len(all_results[partition_key]) > 0 
                           and d[d['execution_category'] == partition_name] if 'execution_category' in d.columns else d]

        if partition_key < len(all_detail_reports):
            all_detail_reports[list(all_results.keys()).index(partition_key)].to_csv(partition_detail_file, index=False)
            print(f"Saved: {partition_detail_file}")

    # Step 6: Display results
    print("\n" + "="*80)
    print("COMPARISON RESULTS BY PARTITION")
    print("="*80)

    for partition_key, partition_name in partition_order:
        if partition_key not in all_metrics:
            continue

        print(f"\n{'='*80}")
        print(f"{partition_name.upper()}")
        print(f"{'='*80}")

        comparison = combined_comparison[combined_comparison['Partition'] == partition_name]
        print(comparison.to_string(index=False))

    print("\n" + "="*80)
    print("SIMULATION COMPLETE")
    print("="*80)
    print(f"\nTotal orders analyzed: {len(combined_details):,}")
    print(f"\nPartitions:")
    for partition_key, partition_name in partition_order:
        if partition_key in all_results:
            count = len(all_results[partition_key])
            print(f"  {partition_name:<35}: {count:>10,} orders")


# =============================================================================
# USAGE
# =============================================================================

if __name__ == "__main__":
    ORDERS_FILE = "orders.csv"
    TRADES_FILE = "trades.csv"
    NBBO_FILE = "externalprice.csv"
    OUTPUT_DIR = "./"
    CHUNK_SIZE = 100000

    main(ORDERS_FILE, TRADES_FILE, NBBO_FILE, OUTPUT_DIR, CHUNK_SIZE)
