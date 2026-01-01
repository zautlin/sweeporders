"""
Phase 6: Dark Pool Simulation - Generate Simulated Metrics

Simulates dark pool matching scenarios for sweep orders and calculates
simulated execution metrics for comparison with real execution.

Scenarios:
1. Scenario A: Unlimited dark orders at mid-price
2. Scenario B: Limited dark orders (50% of volume)
3. Scenario C: Market conditions impact (price moves)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import sys
from typing import Dict, Tuple, List

sys.path.insert(0, str(Path(__file__).parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from config.columns import INPUT_FILES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_simulation_data(output_dir='processed_files'):
    """
    Load all data needed for simulation.
    
    Returns:
        Tuple of (raw_orders, classified, trades, nbbo, real_metrics)
    """
    logger.info("Loading simulation data...")
    
    raw_orders = pd.read_csv(INPUT_FILES['orders'])
    classified = pd.read_csv(f'{output_dir}/sweep_orders_classified.csv.gz')
    trades = pd.read_csv(f'{output_dir}/centrepoint_trades_raw.csv.gz')
    nbbo = pd.read_csv('data/nbbo/nbbo.csv')
    real_metrics = pd.read_csv(f'{output_dir}/real_execution_metrics.csv', index_col=0)
    
    logger.info(f"✓ Loaded {len(raw_orders)} orders, {len(classified)} classified, "
                f"{len(trades)} trades, {len(nbbo)} NBBO snapshots")
    
    return raw_orders, classified, trades, nbbo, real_metrics


def get_initial_order_state(order_id, raw_orders):
    """
    Get order's initial state (at minimum timestamp with highest sequence).
    
    Args:
        order_id: Order identifier
        raw_orders: Full order history
        
    Returns:
        Dictionary with initial state
    """
    order_records = raw_orders[raw_orders['order_id'] == order_id].sort_values('timestamp')
    
    # Find minimum timestamp
    min_ts = order_records['timestamp'].min()
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


def get_completion_time(order_id, raw_orders):
    """
    Get order completion timestamp (when leavesquantity == 0).
    
    Args:
        order_id: Order identifier
        raw_orders: Full order history
        
    Returns:
        Timestamp of completion or max timestamp
    """
    order_records = raw_orders[raw_orders['order_id'] == order_id]
    
    filled_records = order_records[order_records['leavesquantity'] == 0]
    if len(filled_records) > 0:
        return filled_records['timestamp'].max()
    else:
        return order_records['timestamp'].max()


def get_midprice(T, T_K, nbbo):
    """
    Get mid-price during window [T, T+K].
    
    Args:
        T: Start timestamp
        T_K: End timestamp
        nbbo: NBBO data
        
    Returns:
        Mid-price (float)
    """
    # Find NBBO snapshots in window
    window_nbbo = nbbo[(nbbo['timestamp'] >= T) & (nbbo['timestamp'] <= T_K)]
    
    if len(window_nbbo) > 0:
        # Calculate mid-prices for available snapshots
        midprices = (window_nbbo['bidprice'] + window_nbbo['offerprice']) / 2
        return midprices.mean()
    else:
        # Use last NBBO before T+K
        pre_window = nbbo[nbbo['timestamp'] <= T_K].tail(1)
        if len(pre_window) > 0:
            pre = pre_window.iloc[0]
            return (pre['bidprice'] + pre['offerprice']) / 2
        else:
            return None


def get_actual_execution(order_id, trades, T, T_K):
    """
    Get actual execution details for order during window [T, T+K].
    
    Args:
        order_id: Order identifier
        trades: Trades data
        T: Start timestamp
        T_K: End timestamp
        
    Returns:
        Dictionary with actual execution details
    """
    order_trades = trades[trades['orderid'] == order_id]
    window_trades = order_trades[
        (order_trades['tradetime'] >= T) & 
        (order_trades['tradetime'] <= T_K)
    ]
    
    if len(window_trades) > 0:
        total_qty = window_trades['quantity'].sum()
        total_value = (window_trades['quantity'] * window_trades['tradeprice']).sum()
        avg_price = total_value / total_qty if total_qty > 0 else 0
        
        return {
            'qty': total_qty,
            'price': avg_price,
            'cost': total_value,
            'fill_ratio': 1.0 if total_qty > 0 else 0.0
        }
    else:
        return {
            'qty': 0,
            'price': 0.0,
            'cost': 0.0,
            'fill_ratio': 0.0
        }


def simulate_order(order_id, initial_state, actual, midprice, scenario):
    """
    Simulate dark pool matching for given scenario.
    
    Args:
        order_id: Order identifier
        initial_state: Initial order state
        actual: Actual execution details
        midprice: Mid-price during window
        scenario: Scenario name ('unlimited', 'limited_50', 'price_impact')
        
    Returns:
        Dictionary with simulated metrics
    """
    qty = initial_state['quantity']
    
    if scenario == 'unlimited':
        # All quantity matched at midprice
        sim_qty = qty
        sim_price = midprice if midprice else initial_state['price']
        sim_cost = sim_qty * sim_price
        sim_fill = 1.0
        
    elif scenario == 'limited_50':
        # 50% dark at midprice, 50% lit at actual price
        dark_qty = qty * 0.5
        lit_qty = qty * 0.5
        dark_price = midprice if midprice else initial_state['price']
        lit_price = actual['price'] if actual['qty'] > 0 else initial_state['price']
        
        sim_qty = qty
        sim_cost = (dark_qty * dark_price) + (lit_qty * lit_price)
        sim_price = sim_cost / sim_qty if sim_qty > 0 else 0.0
        sim_fill = 1.0
        
    elif scenario == 'price_impact':
        # 50% dark at midprice, 50% at worse price (mid + 5)
        dark_qty = qty * 0.5
        lit_qty = qty * 0.5
        dark_price = midprice if midprice else initial_state['price']
        lit_price = (midprice if midprice else initial_state['price']) + 5
        
        sim_qty = qty
        sim_cost = (dark_qty * dark_price) + (lit_qty * lit_price)
        sim_price = sim_cost / sim_qty if sim_qty > 0 else 0.0
        sim_fill = 1.0
    else:
        sim_qty = 0
        sim_price = 0.0
        sim_cost = 0.0
        sim_fill = 0.0
    
    return {
        'order_id': order_id,
        'scenario': scenario,
        'simulated_qty': int(sim_qty),
        'simulated_price': round(sim_price, 2),
        'simulated_cost': round(sim_cost, 2),
        'simulated_fill_ratio': round(sim_fill, 4),
        'cost_difference': round(actual['cost'] - sim_cost, 2),
        'cost_improvement_pct': round(
            ((actual['cost'] - sim_cost) / actual['cost'] * 100) if actual['cost'] > 0 else 0.0,
            2
        )
    }


def run_simulations(group1_ids, raw_orders, classified, trades, nbbo):
    """
    Run simulations for all Group 1 orders across all scenarios.
    
    Args:
        group1_ids: List of Group 1 order IDs
        raw_orders: Full order history
        classified: Classified orders
        trades: Trade data
        nbbo: NBBO data
        
    Returns:
        DataFrame with all simulation results
    """
    logger.info("Running simulations for all Group 1 orders...")
    
    all_results = []
    
    for i, order_id in enumerate(group1_ids):
        logger.info(f"[{i+1}/{len(group1_ids)}] Simulating order {order_id}...")
        
        # Get initial state
        initial_state = get_initial_order_state(order_id, raw_orders)
        T = initial_state['T']
        
        # Get completion time
        T_K = get_completion_time(order_id, raw_orders)
        
        # Get actual execution
        actual = get_actual_execution(order_id, trades, T, T_K)
        
        # Get mid-price
        midprice = get_midprice(T, T_K, nbbo)
        
        # Run all 3 scenarios
        for scenario in ['unlimited', 'limited_50', 'price_impact']:
            sim_result = simulate_order(order_id, initial_state, actual, midprice, scenario)
            
            # Add metadata
            sim_result['T'] = T
            sim_result['T_K'] = T_K
            sim_result['initial_qty'] = initial_state['quantity']
            sim_result['initial_price'] = initial_state['price']
            sim_result['actual_qty'] = actual['qty']
            sim_result['actual_price'] = round(actual['price'], 2)
            sim_result['actual_cost'] = round(actual['cost'], 2)
            sim_result['midprice'] = round(midprice, 2) if midprice else None
            
            all_results.append(sim_result)
    
    results_df = pd.DataFrame(all_results)
    logger.info(f"✓ Simulated {len(group1_ids)} orders × 3 scenarios = {len(results_df)} simulation results")
    
    return results_df


def calculate_simulated_metrics(results_df, real_metrics):
    """
    Calculate aggregated simulated metrics by scenario.
    
    Args:
        results_df: Simulation results
        real_metrics: Real execution metrics
        
    Returns:
        DataFrame with metrics by scenario
    """
    logger.info("Calculating simulated metrics by scenario...")
    
    scenarios = ['unlimited', 'limited_50', 'price_impact']
    metrics_list = []
    
    for scenario in scenarios:
        scenario_data = results_df[results_df['scenario'] == scenario]
        
        # Get real metrics for Group 1
        real_g1 = real_metrics.loc['GROUP_1_FULLY_FILLED']
        
        # Calculate aggregates
        total_sim_cost = scenario_data['simulated_cost'].sum()
        total_actual_cost = scenario_data['actual_cost'].sum()
        total_cost_diff = total_actual_cost - total_sim_cost
        cost_improvement_pct = (total_cost_diff / total_actual_cost * 100) if total_actual_cost > 0 else 0.0
        
        avg_sim_price = scenario_data['simulated_price'].mean()
        avg_actual_price = scenario_data['actual_price'].mean()
        
        metrics_list.append({
            'scenario': scenario,
            'orders_simulated': len(scenario_data),
            'total_qty_ordered': scenario_data['initial_qty'].sum(),
            'total_actual_cost': round(total_actual_cost, 2),
            'total_simulated_cost': round(total_sim_cost, 2),
            'total_cost_difference': round(total_cost_diff, 2),
            'cost_improvement_pct': round(cost_improvement_pct, 2),
            'avg_actual_price': round(avg_actual_price, 2),
            'avg_simulated_price': round(avg_sim_price, 2),
            'avg_midprice': round(scenario_data['midprice'].mean(), 2)
        })
    
    metrics_df = pd.DataFrame(metrics_list)
    logger.info(f"✓ Calculated metrics for {len(metrics_df)} scenarios")
    
    return metrics_df


def save_simulation_results(results_df, metrics_df, output_dir='processed_files'):
    """
    Save simulation results to CSV files.
    
    Args:
        results_df: Detailed simulation results
        metrics_df: Aggregated metrics by scenario
        output_dir: Output directory
    """
    logger.info("Saving simulation results...")
    
    # Save detailed results
    results_path = Path(output_dir) / 'simulated_metrics_detailed.csv.gz'
    results_df.to_csv(results_path, compression='gzip', index=False)
    logger.info(f"  ✓ Saved detailed results: {results_path}")
    
    # Save summary metrics
    metrics_path = Path(output_dir) / 'simulated_metrics_summary.csv.gz'
    metrics_df.to_csv(metrics_path, compression='gzip', index=False)
    logger.info(f"  ✓ Saved summary metrics: {metrics_path}")
    
    return results_path, metrics_path


def print_simulation_summary(results_df, metrics_df):
    """
    Print formatted simulation summary.
    
    Args:
        results_df: Detailed results
        metrics_df: Aggregated metrics
    """
    print("\n" + "=" * 120)
    print("STEP 6: SIMULATED METRICS - DARK POOL SIMULATION SUMMARY")
    print("=" * 120)
    
    print("\n" + "-" * 120)
    print("SCENARIO COMPARISON - AGGREGATED METRICS")
    print("-" * 120)
    
    display_df = metrics_df[[
        'scenario', 'orders_simulated', 'total_qty_ordered', 
        'total_actual_cost', 'total_simulated_cost', 'total_cost_difference', 'cost_improvement_pct'
    ]].copy()
    
    display_df.columns = ['Scenario', 'Orders', 'Total Qty', 'Actual Cost', 
                          'Simulated Cost', 'Cost Difference', 'Improvement %']
    
    print("\n" + display_df.to_string(index=False))
    
    print("\n" + "-" * 120)
    print("PRICE COMPARISON")
    print("-" * 120)
    
    price_df = metrics_df[[
        'scenario', 'avg_actual_price', 'avg_simulated_price', 'avg_midprice'
    ]].copy()
    
    price_df.columns = ['Scenario', 'Avg Actual Price', 'Avg Simulated Price', 'Avg Mid-Price']
    
    print("\n" + price_df.to_string(index=False))
    
    print("\n" + "=" * 120)
    print("KEY INSIGHTS")
    print("=" * 120)
    
    # Find best scenario
    best_scenario = metrics_df.loc[metrics_df['cost_improvement_pct'].idxmax()]
    print(f"\nBest Scenario: {best_scenario['scenario'].upper()}")
    print(f"  Cost Improvement: {best_scenario['cost_improvement_pct']}%")
    print(f"  Savings: ${best_scenario['total_cost_difference']:,.2f}")
    
    print("\n" + "=" * 120 + "\n")


def run_step6_simulation(output_dir='processed_files'):
    """
    Execute Step 6: Dark Pool Simulation - Generate Simulated Metrics.
    
    Returns:
        Tuple of (results_df, metrics_df)
    """
    logger.info("=" * 80)
    logger.info("STEP 6: DARK POOL SIMULATION - GENERATE SIMULATED METRICS")
    logger.info("=" * 80)
    
    Path(output_dir).mkdir(exist_ok=True)
    
    # Load data
    logger.info("\n[6.1] Loading simulation data...")
    raw_orders, classified, trades, nbbo, real_metrics = load_simulation_data(output_dir)
    logger.info(f"✓ Data loaded")
    
    # Get Group 1 orders
    logger.info("\n[6.2] Identifying Group 1 orders for simulation...")
    group1_ids = classified[classified['sweep_group'] == 'GROUP_1_FULLY_FILLED']['order_id'].unique()
    logger.info(f"✓ Found {len(group1_ids)} Group 1 orders")
    
    # Run simulations
    logger.info("\n[6.3] Running simulations across 3 scenarios...")
    results_df = run_simulations(group1_ids, raw_orders, classified, trades, nbbo)
    logger.info(f"✓ Simulations complete: {len(results_df)} results")
    
    # Calculate metrics
    logger.info("\n[6.4] Calculating simulated metrics...")
    metrics_df = calculate_simulated_metrics(results_df, real_metrics)
    logger.info(f"✓ Metrics calculated")
    
    # Save results
    logger.info("\n[6.5] Saving results...")
    save_simulation_results(results_df, metrics_df, output_dir)
    logger.info(f"✓ Results saved")
    
    # Print summary
    print_simulation_summary(results_df, metrics_df)
    
    logger.info("=" * 80)
    logger.info("STEP 6 COMPLETE")
    logger.info("=" * 80)
    
    return results_df, metrics_df


if __name__ == '__main__':
    results, metrics = run_step6_simulation()
    
    if results is not None and metrics is not None:
        print("\n✓ STEP 6 SIMULATION COMPLETED SUCCESSFULLY")
    else:
        print("\n✗ STEP 6 SIMULATION FAILED")
