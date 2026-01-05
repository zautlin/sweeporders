"""Sweep Order Execution Analysis Module

Compares real vs simulated execution for sweep orders.
Generates comprehensive statistics and outputs to stats/matched/ and stats/unmatched/ directories.
"""

import pandas as pd
from config.column_schema import col
import numpy as np
from pathlib import Path
from utils.statistics_layer import StatisticsEngine
import json
from datetime import datetime

# Try to import scipy for backward compatibility
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_stats = None
    SCIPY_AVAILABLE = False

import utils.file_utils as fu


# ===== PHASE 1: DATA LOADING =====

def load_sweep_order_universe(partition_dir):
    """Load sweep order IDs from last_execution_time.csv (orders with real executions)."""
    filepath = Path(partition_dir) / 'last_execution_time.csv'
    df = fu.safe_read_csv(filepath, required=False)
    if df is None or len(df) == 0:
        print(f"  Loaded 0 sweep orders with executions")
        return set(), pd.DataFrame()
    
    sweep_orderids = set(df[col.common.orderid].unique())
    print(f"  Loaded {len(sweep_orderids)} sweep orders with executions")
    return sweep_orderids, df


def load_order_metadata(partition_dir, sweep_orderids):
    """Load order metadata with arrival NBBO for sweep orders."""
    if len(sweep_orderids) == 0:
        print(f"  Loaded metadata for 0 sweep orders")
        return pd.DataFrame()
    
    filepath = Path(partition_dir) / 'cp_orders_filtered.csv.gz'
    df = fu.safe_read_csv(filepath, required=True, compression='gzip')
    
    # Filter to sweep orders only
    df = df[df[col.common.order_id].isin(sweep_orderids) & (df[col.orders.order_type] == 2048)].copy()
    
    # Rename and select columns
    df = df.rename(columns={'order_id': 'orderid'})
    
    # De-duplicate: keep FIRST row per orderid (arrival state, not final state)
    # Sort by timestamp to ensure we get the earliest state for arrival time
    df = df.sort_values(['orderid', 'timestamp'])
    df = df.drop_duplicates(subset='orderid', keep='first')
    
    # Calculate arrival metrics
    df['arrival_midpoint'] = (df['national_bid'] + df['national_offer']) / 2
    df['arrival_spread'] = df['national_offer'] - df['national_bid']
    
    # Select final columns
    orders_df = df[['orderid', 'timestamp', 'side', 'quantity', 'price', 
                     'national_bid', 'national_offer', 'arrival_midpoint', 'arrival_spread']].copy()
    orders_df.columns = ['orderid', 'arrival_time', 'side', 'order_quantity', 'limit_price',
                         'arrival_bid', 'arrival_offer', 'arrival_midpoint', 'arrival_spread']
    
    orders_df = orders_df.set_index('orderid')
    
    print(f"  Loaded metadata for {len(orders_df)} sweep orders")
    return orders_df


def load_real_trades(partition_dir, sweep_orderids):
    """Load real trades for sweep orders."""
    if len(sweep_orderids) == 0:
        print(f"  Loaded 0 real trades for sweep orders")
        return pd.DataFrame()
    
    filepath = Path(partition_dir) / 'cp_trades_matched.csv.gz'
    df = fu.safe_read_csv(filepath, required=True, compression='gzip')
    
    # Filter to sweep orders
    df = df[df[col.common.orderid].isin(sweep_orderids)].copy()
    
    # Calculate trade midpoint
    df['trade_midpoint'] = (df['nationalbidpricesnapshot'] + df['nationalofferpricesnapshot']) / 2
    
    # Select columns
    trades_df = df[['orderid', 'tradetime', 'tradeprice', 'quantity', 'side',
                     'nationalbidpricesnapshot', 'nationalofferpricesnapshot', 
                     'trade_midpoint', 'matchgroupid']].copy()
    
    # Sort by orderid and time
    trades_df = trades_df.sort_values(['orderid', 'tradetime'])
    
    print(f"  Loaded {len(trades_df)} real trades for sweep orders")
    return trades_df


def load_simulated_trades(partition_dir, sweep_orderids):
    """Load simulated trades for sweep orders (PASSIVE side only)."""
    if len(sweep_orderids) == 0:
        print(f"  Loaded 0 simulated trades for sweep orders")
        return pd.DataFrame()
    
    filepath = Path(partition_dir) / 'cp_trades_simulation.csv'
    if not filepath.exists():
        print(f"  Loaded 0 simulated trades for sweep orders (file not found)")
        return pd.DataFrame()
    
    df = fu.safe_read_csv(filepath, required=True)
    
    # CRITICAL: Filter to sweep orders AND passive side only (passiveaggressive = 1)
    # The simulation creates paired trades: passive=sweep order, aggressive=counterparty
    df = df[(df[col.common.orderid].isin(sweep_orderids)) & (df['passiveaggressive'] == 1)].copy()
    
    # Calculate trade midpoint
    df['trade_midpoint'] = (df['nationalbidpricesnapshot'] + df['nationalofferpricesnapshot']) / 2
    
    # Select columns
    trades_df = df[['orderid', 'tradetime', 'tradeprice', 'quantity', 'side',
                     'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
                     'trade_midpoint', 'matchgroupid', 'passiveaggressive']].copy()
    
    # Sort by orderid and time
    trades_df = trades_df.sort_values(['orderid', 'tradetime'])
    
    print(f"  Loaded {len(trades_df)} simulated trades for sweep orders (passive side only)")
    return trades_df


# ===== PHASE 2: ORDER SET IDENTIFICATION =====

def identify_order_sets(sweep_orderids, real_trades_df, sim_trades_df):
    """Identify Set A (paired) and Set B (unmatched) orders."""
    real_orderids = set(real_trades_df[col.common.orderid].unique())
    sim_orderids = set(sim_trades_df[col.common.orderid].unique())
    
    # Set A: Orders with BOTH real and simulated
    set_a_orderids = real_orderids & sim_orderids
    
    # Set B: Orders with real but NO simulated
    set_b_orderids = real_orderids - sim_orderids
    
    # Orphan simulations (should be empty)
    orphan_sim_orderids = sim_orderids - real_orderids
    
    print(f"\n  Order Set Analysis:")
    print(f"    Set A (matched - both real & sim): {len(set_a_orderids)} orders")
    print(f"    Set B (unmatched - real only):     {len(set_b_orderids)} orders")
    print(f"    Orphan simulations:                {len(orphan_sim_orderids)} orders")
    
    if len(orphan_sim_orderids) > 0:
        print(f"    ⚠️  Warning: {len(orphan_sim_orderids)} simulated trades without real trades")
    
    return set_a_orderids, set_b_orderids, orphan_sim_orderids


# ===== PHASE 3: METRIC CALCULATION =====

def calculate_execution_metrics(trades_df, orderid, arrival_context):
    """Calculate all execution metrics for a single order."""
    order_trades = trades_df[trades_df[col.common.orderid] == orderid].copy()
    
    if len(order_trades) == 0:
        return None
    
    # Get arrival context (convert Series values to scalars)
    arrival_time = int(arrival_context['arrival_time'])
    arrival_mid = float(arrival_context['arrival_midpoint'])
    arrival_spread = float(arrival_context['arrival_spread'])
    order_qty = float(arrival_context['order_quantity'])
    side = int(arrival_context[col.common.side])
    side_multiplier = 1 if side == 1 else -1  # Buy=+1, Sell=-1
    
    # === GROUP A: FILL METRICS ===
    qty_filled = order_trades[col.common.quantity].sum()
    fill_rate_pct = (qty_filled / order_qty) * 100 if order_qty > 0 else 0
    num_fills = len(order_trades)
    avg_fill_size = qty_filled / num_fills if num_fills > 0 else 0
    
    # === GROUP B: PRICE/COST METRICS ===
    vwap = (order_trades['tradeprice'] * order_trades[col.common.quantity]).sum() / qty_filled if qty_filled > 0 else 0
    
    # Execution cost - arrival based
    exec_cost_arrival_bps = side_multiplier * ((vwap - arrival_mid) / arrival_mid) * 10000 if arrival_mid > 0 else 0
    
    # Execution cost - volume weighted (using trade-by-trade NBBO)
    weighted_costs = []
    for _, trade in order_trades.iterrows():
        trade_mid = trade['trade_midpoint']
        if trade_mid > 0:
            trade_cost = side_multiplier * ((trade['tradeprice'] - trade_mid) / trade_mid) * 10000
            weighted_cost = trade_cost * trade[col.common.quantity]
            weighted_costs.append(weighted_cost)
    exec_cost_vw_bps = sum(weighted_costs) / qty_filled if qty_filled > 0 else 0
    
    # Effective spread captured
    effective_spread = 2 * abs(vwap - arrival_mid)
    effective_spread_pct = (effective_spread / arrival_spread) * 100 if arrival_spread > 0 else 0
    
    # === GROUP C: TIMING METRICS ===
    first_trade_time = order_trades['tradetime'].min()
    last_trade_time = order_trades['tradetime'].max()
    
    # Calculate execution time (always non-negative)
    exec_time_sec = max(0, (last_trade_time - first_trade_time) / 1e9)
    
    # Calculate time to first fill (can be negative if timestamp ordering is wrong)
    time_to_first_fill_sec = (first_trade_time - arrival_time) / 1e9
    
    # If time to first fill is negative, it means the order timestamp is after the trade
    # This can happen if we're using the wrong timestamp. Set to 0 in this case.
    if time_to_first_fill_sec < 0:
        time_to_first_fill_sec = 0.0
    
    # Calculate volume-weighted time of execution (VWTE)
    # Weights each fill time by its quantity - shows when bulk volume executed
    weighted_time = 0.0
    for _, trade in order_trades.iterrows():
        time_from_arrival = (trade['tradetime'] - arrival_time) / 1e9
        # Ensure non-negative (handle timestamp issues)
        time_from_arrival = max(0.0, time_from_arrival)
        weighted_time += trade[col.common.quantity] * time_from_arrival
    
    vw_exec_time_sec = weighted_time / qty_filled if qty_filled > 0 else 0.0
    
    # === GROUP D: CONTEXT METRICS ===
    first_trade_mid = order_trades.iloc[0]['trade_midpoint']
    last_trade_mid = order_trades.iloc[-1]['trade_midpoint']
    market_drift_bps = ((last_trade_mid - first_trade_mid) / first_trade_mid) * 10000 if first_trade_mid > 0 else 0
    
    return {
        'qty_filled': qty_filled,
        'fill_rate_pct': fill_rate_pct,
        'num_fills': num_fills,
        'avg_fill_size': avg_fill_size,
        'vwap': vwap,
        'exec_cost_arrival_bps': exec_cost_arrival_bps,
        'exec_cost_vw_bps': exec_cost_vw_bps,
        'effective_spread_pct': effective_spread_pct,
        'exec_time_sec': exec_time_sec,
        'time_to_first_fill_sec': time_to_first_fill_sec,
        'vw_exec_time_sec': vw_exec_time_sec,
        'market_drift_bps': market_drift_bps,
        'first_trade_time': first_trade_time,
        'last_trade_time': last_trade_time,
        'midpoint_at_last_fill': last_trade_mid
    }


def calculate_all_metrics(orderids, trades_df, orders_df, prefix='real'):
    """Calculate metrics for all orders in the set."""
    metrics = {}
    
    for orderid in orderids:
        arrival_context = orders_df.loc[orderid]
        order_metrics = calculate_execution_metrics(trades_df, orderid, arrival_context)
        
        if order_metrics is not None:
            # Add prefix to all keys
            prefixed_metrics = {f'{prefix}_{k}': v for k, v in order_metrics.items()}
            metrics[orderid] = prefixed_metrics
    
    print(f"  Calculated {prefix} metrics for {len(metrics)} orders")
    return metrics


def merge_and_calculate_differences(set_a_orderids, real_metrics, sim_metrics, orders_df):
    """Merge real and sim metrics, calculate differences for Set A orders."""
    comparison_rows = []
    
    for orderid in set_a_orderids:
        # Get arrival context
        arrival_ctx = orders_df.loc[orderid]
        
        # Get metrics
        real = real_metrics.get(orderid)
        sim = sim_metrics.get(orderid)
        
        if real is None or sim is None:
            continue
        
        # Calculate differences (Real - Simulated)
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
            'vw_exec_time_diff_sec': real['real_vw_exec_time_sec'] - sim['sim_vw_exec_time_sec'],
        }
        
        # Dark pool better if lower cost
        dark_pool_better = sim['sim_exec_cost_arrival_bps'] < real['real_exec_cost_arrival_bps']
        
        # Combine all data
        row = {
            'orderid': orderid,
            'order_timestamp': arrival_ctx['arrival_time'],
            'side': arrival_ctx[col.common.side],
            'order_quantity': arrival_ctx['order_quantity'],
            'arrival_bid': arrival_ctx['arrival_bid'],
            'arrival_offer': arrival_ctx['arrival_offer'],
            'arrival_midpoint': arrival_ctx['arrival_midpoint'],
            'arrival_spread': arrival_ctx['arrival_spread'],
            **real,
            **sim,
            **differences,
            'dark_pool_better': dark_pool_better
        }
        
        comparison_rows.append(row)
    
    comparison_df = pd.DataFrame(comparison_rows)
    print(f"  Created comparison DataFrame with {len(comparison_df)} matched orders")
    return comparison_df


def create_unmatched_dataframe(set_b_orderids, real_metrics, orders_df):
    """Create DataFrame for orders with real trades but no simulated trades."""
    unmatched_rows = []
    
    for orderid in set_b_orderids:
        # Get arrival context
        arrival_ctx = orders_df.loc[orderid]
        
        # Get real metrics
        real = real_metrics.get(orderid)
        
        if real is None:
            continue
        
        # Create row
        row = {
            'orderid': orderid,
            'order_timestamp': arrival_ctx['arrival_time'],
            'side': arrival_ctx[col.common.side],
            'order_quantity': arrival_ctx['order_quantity'],
            'arrival_bid': arrival_ctx['arrival_bid'],
            'arrival_offer': arrival_ctx['arrival_offer'],
            'arrival_midpoint': arrival_ctx['arrival_midpoint'],
            'arrival_spread': arrival_ctx['arrival_spread'],
            **real,
            'sim_qty_filled': 0,
            'sim_fill_rate_pct': 0.0,
            'reason_no_dark_match': 'No contra orders available in dark pool',
            'lit_market_cost_bps': real['real_exec_cost_arrival_bps'],
            'estimated_dark_savings_bps': None
        }
        
        unmatched_rows.append(row)
    
    unmatched_df = pd.DataFrame(unmatched_rows)
    print(f"  Created unmatched DataFrame with {len(unmatched_df)} orders")
    return unmatched_df


# ===== PHASE 4: STATISTICAL ANALYSIS =====

def calculate_summary_statistics(comparison_df):
    """Calculate summary statistics for each metric."""
    metrics_to_analyze = [
        'qty_filled',
        'fill_rate_pct',
        'num_fills',
        'avg_fill_size',
        'vwap',
        'exec_cost_arrival_bps',
        'exec_cost_vw_bps',
        'effective_spread_pct',
        'exec_time_sec',
        'time_to_first_fill_sec',
        'vw_exec_time_sec'
    ]
    
    metric_groups = {
        'qty_filled': 'Fill',
        'fill_rate_pct': 'Fill',
        'num_fills': 'Efficiency',
        'avg_fill_size': 'Efficiency',
        'vwap': 'Price',
        'exec_cost_arrival_bps': 'Cost',
        'exec_cost_vw_bps': 'Cost',
        'effective_spread_pct': 'Cost',
        'exec_time_sec': 'Timing',
        'time_to_first_fill_sec': 'Timing',
        'vw_exec_time_sec': 'Timing'
    }
    
    summary_rows = []
    
    for metric in metrics_to_analyze:
        real_col = f'real_{metric}'
        sim_col = f'sim_{metric}'
        diff_col = f'{metric.replace("_", "_")}_diff' if f'{metric}_diff' not in comparison_df.columns else f'{metric}_diff'
        
        # Handle different diff column naming
        if diff_col not in comparison_df.columns:
            if 'exec_cost_arrival' in metric:
                diff_col = 'exec_cost_arrival_diff_bps'
            elif 'exec_cost_vw' in metric:
                diff_col = 'exec_cost_vw_diff_bps'
            elif 'effective_spread' in metric:
                diff_col = 'effective_spread_diff_pct'
            elif metric == 'exec_time_sec':
                diff_col = 'exec_time_diff_sec'
            elif metric == 'time_to_first_fill_sec':
                diff_col = 'time_to_first_fill_diff_sec'
            elif metric == 'vw_exec_time_sec':
                diff_col = 'vw_exec_time_diff_sec'
            elif 'fill_rate' in metric:
                diff_col = 'fill_rate_diff_pct'
            else:
                diff_col = f'{metric}_diff'
        
        real_values = comparison_df[real_col].dropna()
        sim_values = comparison_df[sim_col].dropna()
        
        if diff_col in comparison_df.columns:
            diff_values = comparison_df[diff_col].dropna()
        else:
            diff_values = real_values - sim_values
        
        summary = {
            'metric_group': metric_groups.get(metric, 'Other'),
            'metric_name': metric,
            'real_mean': real_values.mean(),
            'real_median': real_values.median(),
            'real_std': real_values.std(),
            'real_min': real_values.min(),
            'real_max': real_values.max(),
            'real_q25': real_values.quantile(0.25),
            'real_q75': real_values.quantile(0.75),
            'sim_mean': sim_values.mean(),
            'sim_median': sim_values.median(),
            'sim_std': sim_values.std(),
            'sim_min': sim_values.min(),
            'sim_max': sim_values.max(),
            'sim_q25': sim_values.quantile(0.25),
            'sim_q75': sim_values.quantile(0.75),
            'diff_mean': diff_values.mean(),
            'diff_median': diff_values.median(),
            'diff_std': diff_values.std(),
            'n_orders': len(real_values)
        }
        
        summary_rows.append(summary)
    
    summary_df = pd.DataFrame(summary_rows)
    print(f"  Generated summary statistics for {len(summary_df)} metrics")
    return summary_df


def perform_statistical_tests(comparison_df, stats_engine=None):
    """Perform statistical tests on paired data."""
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    metrics_to_analyze = [
        'qty_filled',
        'fill_rate_pct',
        'num_fills',
        'avg_fill_size',
        'vwap',
        'exec_cost_arrival_bps',
        'exec_cost_vw_bps',
        'effective_spread_pct',
        'exec_time_sec',
        'time_to_first_fill_sec',
        'vw_exec_time_sec'
    ]
    
    test_rows = []
    
    for metric in metrics_to_analyze:
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
        
        # Effect size (Cohen's d) - always calculate (descriptive)
        if n_pairs >= 2 and differences.std() > 0:
            cohens_d = differences.mean() / differences.std()
        else:
            cohens_d = np.nan
        
        # Initialize variables
        t_stat = t_pvalue = np.nan
        w_stat = w_pvalue = np.nan
        pearson_r = pearson_p = np.nan
        spearman_r = spearman_p = np.nan
        ci_lower = ci_upper = np.nan
        
        if stats_engine.is_enabled():
            # Paired t-test
            if n_pairs >= 2:
                ttest_result = stats_engine.ttest_rel(real_values, sim_values)
                if ttest_result:
                    t_stat = ttest_result.statistic
                    t_pvalue = ttest_result.pvalue
            
            # Wilcoxon signed-rank test
            if n_pairs >= 10:
                try:
                    wilcoxon_result = stats_engine.wilcoxon(real_values, sim_values)
                    if wilcoxon_result:
                        w_stat = wilcoxon_result.statistic
                        w_pvalue = wilcoxon_result.pvalue
                except:
                    pass  # Wilcoxon can fail for various reasons
            
            # Correlation
            if n_pairs >= 3:
                pearson_result = stats_engine.pearsonr(real_values, sim_values)
                if pearson_result:
                    pearson_r = pearson_result.statistic
                    pearson_p = pearson_result.pvalue
                
                spearman_result = stats_engine.spearmanr(real_values, sim_values)
                if spearman_result:
                    spearman_r = spearman_result.statistic
                    spearman_p = spearman_result.pvalue
            
            # Confidence interval
            if n_pairs >= 2:
                ci_result = stats_engine.confidence_interval(differences, confidence=0.95)
                if ci_result:
                    ci_lower, ci_upper = ci_result
        
        # Interpretation
        if not np.isnan(t_pvalue):
            if t_pvalue < 0.001:
                significance = "***"
            elif t_pvalue < 0.01:
                significance = "**"
            elif t_pvalue < 0.05:
                significance = "*"
            else:
                significance = "ns"
            
            if differences.mean() > 0:
                direction = "Real execution worse than simulated"
            elif differences.mean() < 0:
                direction = "Real execution better than simulated"
            else:
                direction = "No difference"
            
            interpretation = f"{direction} (p{significance})"
        else:
            significance = "N/A"
            interpretation = "Insufficient data" if n_pairs > 0 else "Stats disabled"
        
        test_result = {
            'metric_name': metric,
            'n_pairs': n_pairs,
            'paired_t_statistic': t_stat,
            'paired_t_pvalue': t_pvalue,
            'wilcoxon_statistic': w_stat,
            'wilcoxon_pvalue': w_pvalue,
            'pearson_correlation': pearson_r,
            'pearson_pvalue': pearson_p,
            'spearman_correlation': spearman_r,
            'spearman_pvalue': spearman_p,
            'cohens_d_effect_size': cohens_d,
            'mean_diff_ci_lower': ci_lower,
            'mean_diff_ci_upper': ci_upper,
            'significant_at_05': (t_pvalue < 0.05) if not np.isnan(t_pvalue) else False,
            'significant_at_01': (t_pvalue < 0.01) if not np.isnan(t_pvalue) else False,
            'significance_level': significance,
            'interpretation': interpretation
        }
        
        test_rows.append(test_result)
    
    tests_df = pd.DataFrame(test_rows)
    print(f"  Performed statistical tests for {len(tests_df)} metrics")
    return tests_df


def calculate_quantile_comparison(comparison_df):
    """Calculate quantile comparison for distribution analysis."""
    metrics_to_analyze = [
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
    
    quantiles = [0.10, 0.25, 0.50, 0.75, 0.90]
    quantile_rows = []
    
    for metric in metrics_to_analyze:
        real_col = f'real_{metric}'
        sim_col = f'sim_{metric}'
        
        real_values = comparison_df[real_col].dropna()
        sim_values = comparison_df[sim_col].dropna()
        
        for q in quantiles:
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
    print(f"  Generated quantile analysis with {len(quantiles_df)} rows")
    return quantiles_df


# ===== PHASE 5: OUTPUT GENERATION =====

def print_statistical_summary(summary_df, tests_df, n_matched, n_unmatched):
    """Print formatted statistical summary table to console."""
    print(f"\n{'='*90}")
    print(f"STATISTICAL TEST SUMMARY")
    print(f"{'='*90}")
    print(f"Matched Orders (Set A):   {n_matched:>6}")
    print(f"Unmatched Orders (Set B): {n_unmatched:>6}")
    print(f"{'='*90}")
    
    # Key metrics to display
    display_metrics = [
        ('exec_cost_arrival_bps', 'Execution Cost (Arrival)', 'bps'),
        ('exec_cost_vw_bps', 'Execution Cost (VW)', 'bps'),
        ('effective_spread_pct', 'Effective Spread', '%'),
        ('vwap', 'VWAP', 'price'),
        ('qty_filled', 'Quantity Filled', 'units'),
        ('fill_rate_pct', 'Fill Rate', '%'),
        ('num_fills', 'Number of Fills', 'count'),
        ('exec_time_sec', 'Execution Time', 'sec'),
        ('vw_exec_time_sec', 'VW Execution Time', 'sec')
    ]
    
    print(f"\n{'Metric':<30} {'Real':>12} {'Sim':>12} {'Diff':>12} {'p-value':>12} {'Sig':>5}")
    print(f"{'-'*90}")
    
    for metric_key, metric_label, unit in display_metrics:
        # Get summary stats
        summary_row = summary_df[summary_df['metric_name'] == metric_key]
        test_row = tests_df[tests_df['metric_name'] == metric_key]
        
        if len(summary_row) == 0 or len(test_row) == 0:
            continue
        
        real_mean = summary_row['real_mean'].values[0]
        sim_mean = summary_row['sim_mean'].values[0]
        diff_mean = summary_row['diff_mean'].values[0]
        p_value = test_row['paired_t_pvalue'].values[0]
        
        # Determine significance level
        if not np.isnan(p_value):
            if p_value < 0.001:
                sig = '***'
            elif p_value < 0.01:
                sig = '**'
            elif p_value < 0.05:
                sig = '*'
            else:
                sig = 'ns'
        else:
            sig = 'N/A'
        
        # Format based on unit
        if unit == 'bps':
            print(f"{metric_label:<30} {real_mean:>12.2f} {sim_mean:>12.2f} {diff_mean:>12.2f} {p_value:>12.2e} {sig:>5}")
        elif unit == '%':
            print(f"{metric_label:<30} {real_mean:>12.2f} {sim_mean:>12.2f} {diff_mean:>12.2f} {p_value:>12.2e} {sig:>5}")
        elif unit == 'price':
            print(f"{metric_label:<30} {real_mean:>12.2f} {sim_mean:>12.2f} {diff_mean:>12.2f} {p_value:>12.2e} {sig:>5}")
        elif unit == 'units':
            print(f"{metric_label:<30} {real_mean:>12.1f} {sim_mean:>12.1f} {diff_mean:>12.1f} {p_value:>12.2e} {sig:>5}")
        elif unit == 'count':
            print(f"{metric_label:<30} {real_mean:>12.1f} {sim_mean:>12.1f} {diff_mean:>12.1f} {p_value:>12.2e} {sig:>5}")
        else:
            print(f"{metric_label:<30} {real_mean:>12.2f} {sim_mean:>12.2f} {diff_mean:>12.2f} {p_value:>12.2e} {sig:>5}")
    
    print(f"{'-'*90}")
    print(f"Significance: *** p<0.001, ** p<0.01, * p<0.05, ns = not significant")
    
    # Key finding
    exec_cost_summary = summary_df[summary_df['metric_name'] == 'exec_cost_arrival_bps']
    exec_cost_test = tests_df[tests_df['metric_name'] == 'exec_cost_arrival_bps']
    
    if len(exec_cost_summary) > 0 and len(exec_cost_test) > 0:
        diff_mean = exec_cost_summary['diff_mean'].values[0]  # Real - Sim
        real_mean = exec_cost_summary['real_mean'].values[0]
        sim_mean = exec_cost_summary['sim_mean'].values[0]
        p_val = exec_cost_test['paired_t_pvalue'].values[0]
        cohens_d = exec_cost_test['cohens_d_effect_size'].values[0]
        
        # For execution cost: LOWER (more negative) = BETTER
        # If diff_mean > 0, it means Real > Sim, so Sim is BETTER (more negative)
        # If diff_mean < 0, it means Real < Sim, so Real is BETTER (more negative)
        
        print(f"\n{'='*90}")
        print(f"KEY FINDING:")
        print(f"  Real execution cost:      {real_mean:>8.2f} bps")
        print(f"  Simulated execution cost: {sim_mean:>8.2f} bps")
        print(f"  Difference (Real - Sim):  {diff_mean:>8.2f} bps")
        
        if diff_mean > 0:
            # Real is higher (worse), Sim is lower (better)
            print(f"  --> Dark pool provides {diff_mean:.2f} bps BETTER execution cost")
        elif diff_mean < 0:
            # Real is lower (better), Sim is higher (worse)
            print(f"  --> Dark pool provides {abs(diff_mean):.2f} bps WORSE execution cost")
        else:
            print(f"  --> No difference in execution cost")
        
        print(f"  Statistical significance: p = {p_val:.2e}")
        if not np.isnan(cohens_d):
            print(f"  Effect size (Cohen's d): {cohens_d:.3f}")
            if abs(cohens_d) < 0.2:
                effect_interp = "small"
            elif abs(cohens_d) < 0.5:
                effect_interp = "small to medium"
            elif abs(cohens_d) < 0.8:
                effect_interp = "medium to large"
            else:
                effect_interp = "large"
            print(f"  Practical significance: {effect_interp} effect")
        print(f"{'='*90}")


def create_output_directories(outputs_dir, partition_key):
    """Create matched/ and unmatched/ directories in outputs/{date}/{orderbookid}/."""
    output_partition_dir = Path(outputs_dir) / partition_key
    matched_dir = output_partition_dir / 'matched'
    unmatched_dir = output_partition_dir / 'unmatched'
    
    matched_dir.mkdir(parents=True, exist_ok=True)
    unmatched_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n  Created output directories:")
    print(f"    {matched_dir}")
    print(f"    {unmatched_dir}")
    
    return matched_dir, unmatched_dir


def write_output_files(comparison_df, summary_df, tests_df, quantiles_df, unmatched_df, 
                        matched_dir, unmatched_dir, partition_key):
    """Write all output files to respective directories."""
    
    # Write matched files
    print(f"\n  Writing matched analysis files...")
    
    detailed_path = matched_dir / 'sweep_order_comparison_detailed.csv'
    comparison_df.to_csv(detailed_path, index=False)
    print(f"    ✓ {detailed_path.name}: {len(comparison_df)} rows")
    
    summary_path = matched_dir / 'sweep_order_comparison_summary.csv'
    summary_df.to_csv(summary_path, index=False)
    print(f"    ✓ {summary_path.name}: {len(summary_df)} rows")
    
    tests_path = matched_dir / 'sweep_order_statistical_tests.csv'
    tests_df.to_csv(tests_path, index=False)
    print(f"    ✓ {tests_path.name}: {len(tests_df)} rows")
    
    quantiles_path = matched_dir / 'sweep_order_quantile_comparison.csv'
    quantiles_df.to_csv(quantiles_path, index=False)
    print(f"    ✓ {quantiles_path.name}: {len(quantiles_df)} rows")
    
    # Write unmatched files
    print(f"\n  Writing unmatched analysis files...")
    
    unmatched_path = unmatched_dir / 'sweep_order_unexecuted_in_dark.csv'
    unmatched_df.to_csv(unmatched_path, index=False)
    print(f"    ✓ {unmatched_path.name}: {len(unmatched_df)} rows")
    
    # Generate validation report
    validation_report = {
        'timestamp': datetime.now().isoformat(),
        'partition_key': partition_key,
        'matched_orders_count': len(comparison_df),
        'unmatched_orders_count': len(unmatched_df),
        'metrics_analyzed': len(summary_df),
        'key_findings': {
            'avg_real_exec_cost_bps': float(summary_df[summary_df.metric_name == 'exec_cost_arrival_bps']['real_mean'].values[0]),
            'avg_sim_exec_cost_bps': float(summary_df[summary_df.metric_name == 'exec_cost_arrival_bps']['sim_mean'].values[0]),
            'avg_cost_savings_bps': float(summary_df[summary_df.metric_name == 'exec_cost_arrival_bps']['diff_mean'].values[0]),
            'cost_diff_pvalue': float(tests_df[tests_df.metric_name == 'exec_cost_arrival_bps']['paired_t_pvalue'].values[0])
        }
    }
    
    validation_path = matched_dir / 'analysis_validation_report.json'
    with open(validation_path, 'w') as f:
        json.dump(validation_report, f, indent=2)
    print(f"    ✓ {validation_path.name}")
    
    # Print formatted statistical summary
    print_statistical_summary(summary_df, tests_df, len(comparison_df), len(unmatched_df))


# ===== MAIN ANALYSIS FUNCTION =====

def analyze_sweep_execution(processed_dir, outputs_dir, partition_keys, stats_engine=None):
    """Main function to analyze sweep order execution."""
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    print("\n" + "="*80)
    print("[13/13] SWEEP ORDER EXECUTION ANALYSIS")
    print("="*80)
    print(f"Statistics: {'ENABLED' if stats_engine.is_enabled() else 'DISABLED'}")
    if stats_engine.is_enabled():
        print(f"Statistics Tier: {stats_engine.get_tier_name()}")
    print("="*80)
    
    for partition_key in partition_keys:
        print(f"\nAnalyzing partition: {partition_key}")
        partition_dir = Path(processed_dir) / partition_key
        
        # Phase 1: Load data
        print(f"\nPhase 1: Loading data...")
        sweep_orderids, exec_time_df = load_sweep_order_universe(partition_dir)
        
        # Skip partition if no sweep orders
        if len(sweep_orderids) == 0:
            print(f"  ⏭️  Skipping partition (no sweep orders with executions)")
            continue
        
        # Check if simulation file exists before proceeding
        sim_file = partition_dir / 'cp_trades_simulation.csv'
        if not sim_file.exists():
            print(f"  ⏭️  Skipping partition (no simulation results found)")
            continue
        
        orders_df = load_order_metadata(partition_dir, sweep_orderids)
        real_trades_df = load_real_trades(partition_dir, sweep_orderids)
        sim_trades_df = load_simulated_trades(partition_dir, sweep_orderids)
        
        # Phase 2: Identify order sets
        print(f"\nPhase 2: Identifying order sets...")
        set_a_orderids, set_b_orderids, orphan_sim = identify_order_sets(
            sweep_orderids, real_trades_df, sim_trades_df
        )
        
        # Phase 3: Calculate metrics
        print(f"\nPhase 3: Calculating metrics...")
        real_metrics = calculate_all_metrics(
            set_a_orderids | set_b_orderids, real_trades_df, orders_df, prefix='real'
        )
        sim_metrics = calculate_all_metrics(
            set_a_orderids, sim_trades_df, orders_df, prefix='sim'
        )
        
        # Create comparison DataFrames
        comparison_df = merge_and_calculate_differences(
            set_a_orderids, real_metrics, sim_metrics, orders_df
        )
        unmatched_df = create_unmatched_dataframe(
            set_b_orderids, real_metrics, orders_df
        )
        
        # Phase 4: Statistical analysis
        print(f"\nPhase 4: Performing statistical analysis...")
        summary_df = calculate_summary_statistics(comparison_df)
        tests_df = perform_statistical_tests(comparison_df, stats_engine)
        quantiles_df = calculate_quantile_comparison(comparison_df)
        
        # Phase 5: Output generation
        print(f"\nPhase 5: Generating outputs...")
        matched_dir, unmatched_dir = create_output_directories(outputs_dir, partition_key)
        write_output_files(
            comparison_df, summary_df, tests_df, quantiles_df, unmatched_df,
            matched_dir, unmatched_dir, partition_key
        )
        
        print(f"\n✅ Sweep execution analysis complete for {partition_key}")
    
    print("\n" + "="*80)
