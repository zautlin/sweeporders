"""
Centre Point Sweep Order Matching Pipeline

Main entry point for the end-to-end pipeline that extracts Centre Point orders,
simulates sweep matching with time-priority algorithm, and compares real vs simulated execution.
"""

import pandas as pd
from pathlib import Path
import time

import data_processor as dp
import sweep_simulator as ss
import metrics_generator as mg
import config


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def simulate_sweep_matching(orders_by_partition, nbbo_by_partition, output_dir):
    """Step 8: Simulate sweep matching for all partitions."""
    print("\n[8/11] Simulating sweep matching...")
    
    simulation_results_by_partition = {}
    
    for partition_key in orders_by_partition.keys():
        # Load partition data
        partition_data = dp.load_partition_data(
            partition_key, 
            config.PROCESSED_DIR
        )
        
        if not partition_data or 'orders_before' not in partition_data:
            continue
        
        # Add NBBO data if available
        if partition_key not in nbbo_by_partition:
            partition_data['nbbo'] = None
        else:
            partition_data['nbbo'] = nbbo_by_partition[partition_key]
        
        # Run simulation
        sim_results = ss.simulate_partition(partition_key, partition_data)
        
        if not sim_results:
            continue
        
        order_summary = sim_results['order_summary']
        match_details = sim_results['match_details']
        
        # Save simulation results
        partition_output_dir = Path(output_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        
        order_summary.to_csv(partition_output_dir / 'simulation_order_summary.csv', index=False)
        match_details.to_csv(partition_output_dir / 'simulation_match_details.csv', index=False)
        
        simulation_results_by_partition[partition_key] = {
            'order_summary': order_summary,
            'match_details': match_details
        }
    
    print(f"   Completed sweep simulation for {len(simulation_results_by_partition)} partitions")
    return simulation_results_by_partition


def calculate_simulated_metrics_step(orders_by_partition, simulation_results_by_partition, output_dir):
    """Step 9: Calculate simulated metrics for all partitions."""
    print("\n[9/11] Calculating simulated metrics...")
    
    orders_with_sim_metrics_by_partition = {}
    
    for partition_key, sim_results in simulation_results_by_partition.items():
        # Load orders after matching (contains real execution results)
        orders_after_matching_file = Path(config.PROCESSED_DIR) / partition_key / 'orders_after_matching.csv'
        if not orders_after_matching_file.exists():
            continue
        
        orders_after_matching = pd.read_csv(orders_after_matching_file)
        
        # Calculate simulated metrics
        orders_with_metrics = mg.calculate_simulated_metrics(
            orders_after_matching,
            sim_results['order_summary'],
            sim_results['match_details']
        )
        
        # Save orders with simulated metrics
        partition_output_dir = Path(output_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        orders_with_metrics.to_csv(partition_output_dir / 'orders_with_simulated_metrics.csv', index=False)
        
        orders_with_sim_metrics_by_partition[partition_key] = orders_with_metrics
    
    print(f"   Calculated metrics for {len(orders_with_sim_metrics_by_partition)} partitions")
    return orders_with_sim_metrics_by_partition


def classify_order_groups(orders_by_partition):
    """Step 10: Classify sweep orders (type 2048 only) into groups based on real execution."""
    groups_by_partition = dp.classify_order_groups(
        orders_by_partition,
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    return groups_by_partition


def compare_real_vs_simulated(simulation_results_by_partition, groups_by_partition, orders_by_partition, output_dir):
    """Step 11: Compare real vs simulated execution for sweep orders with statistical analysis."""
    print("\n[11/11] Comparing real vs simulated execution (sweep orders)...")
    
    for partition_key, sim_results in simulation_results_by_partition.items():
        groups = groups_by_partition.get(partition_key)
        if not groups:
            continue
        
        # Load orders_after_matching for real execution data
        date, security_code = partition_key.split('/')
        orders_after_file = Path(config.PROCESSED_DIR) / date / security_code / 'orders_after_matching.csv'
        if not orders_after_file.exists():
            continue
        
        orders_after = pd.read_csv(orders_after_file)
        
        # Load aggregated trades for price comparison
        trades_agg_file = Path(config.PROCESSED_DIR) / date / security_code / 'cp_trades_aggregated.csv.gz'
        trades_agg = None
        if trades_agg_file.exists():
            trades_agg = pd.read_csv(trades_agg_file)
        
        # Run comprehensive sweep comparison
        comparison_results = mg.compare_sweep_execution(
            sweep_order_summary=sim_results['order_summary'],
            orders_after_matching=orders_after,
            trades_agg=trades_agg,
            groups=groups
        )
        
        # Generate reports
        partition_output_dir = Path(output_dir) / partition_key
        report_files = mg.generate_sweep_comparison_reports(
            partition_key,
            comparison_results,
            partition_output_dir
        )
    
    print(f"   Generated sweep comparison reports for {len(simulation_results_by_partition)} partitions")


def print_summary(orders_by_partition, trades_by_partition, execution_time):
    """Print execution summary."""
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUMMARY")
    print("="*80)
    
    # Count orders and trades
    total_orders = sum(len(df) for df in orders_by_partition.values())
    total_trades = sum(len(df) for df in trades_by_partition.values()) if trades_by_partition else 0
    num_partitions = len(orders_by_partition)
    
    print(f"Total Centre Point Orders: {total_orders:,}")
    print(f"Total Trades: {total_trades:,}")
    print(f"Number of Partitions: {num_partitions}")
    print(f"Execution Time: {execution_time:.2f} seconds")
    
    # Print partition breakdown
    print("\nPartition Breakdown:")
    for partition_key in sorted(orders_by_partition.keys()):
        num_orders = len(orders_by_partition[partition_key])
        num_trades = len(trades_by_partition.get(partition_key, [])) if trades_by_partition else 0
        print(f"  {partition_key}: {num_orders:,} orders, {num_trades:,} trades")
    
    print("\nOutput files:")
    print(f"  Processed data: {config.PROCESSED_DIR}/")
    print(f"  Final outputs:  {config.OUTPUTS_DIR}/")
    print("="*80)


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def main():
    """Main execution function."""
    start_time = time.time()
    
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    
    # Print system configuration
    print("\nSystem Configuration:")
    print(config.SYSTEM_CONFIG)
    
    print(f"\nProcessed directory: {config.PROCESSED_DIR}/")
    print(f"Outputs directory: {config.OUTPUTS_DIR}/")
    
    # Create output directories
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Step 1: Extract orders
    orders_by_partition = dp.extract_orders(
        config.INPUT_FILES['orders'], 
        config.PROCESSED_DIR, 
        config.CENTRE_POINT_ORDER_TYPES, 
        config.CHUNK_SIZE, 
        config.COLUMN_MAPPING
    )
    
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return
    
    # Get unique dates
    unique_dates = sorted(set(pk.split('/')[0] for pk in orders_by_partition.keys()))
    
    # Step 2: Extract trades
    trades_by_partition = dp.extract_trades(
        config.INPUT_FILES['trades'], 
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING,
        config.CHUNK_SIZE
    )
    
    # Step 3: Aggregate trades
    if trades_by_partition:
        dp.aggregate_trades(orders_by_partition, trades_by_partition, config.PROCESSED_DIR, config.COLUMN_MAPPING)
    else:
        print("\n[3/11] No trades to aggregate")
    
    # Step 4: Extract NBBO
    nbbo_by_partition = dp.extract_nbbo(
        config.INPUT_FILES['nbbo'], 
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Step 5: Extract reference data
    dp.extract_reference_data(
        config.INPUT_FILES, 
        unique_dates, 
        orders_by_partition,
        config.PROCESSED_DIR
    )
    
    # Step 6: Extract order states (before/after matching)
    dp.get_orders_state(
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Step 7: Extract last execution times
    dp.extract_last_execution_times(
        orders_by_partition, 
        trades_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Step 8: Simulate sweep matching (outputs to config.OUTPUTS_DIR)
    simulation_results_by_partition = simulate_sweep_matching(
        orders_by_partition, 
        nbbo_by_partition,
        config.OUTPUTS_DIR
    )
    
    # Step 9: Calculate simulated metrics (outputs to config.OUTPUTS_DIR)
    orders_with_sim_metrics_by_partition = calculate_simulated_metrics_step(
        orders_by_partition,
        simulation_results_by_partition,
        config.OUTPUTS_DIR
    )
    
    # Step 10: Classify order groups (sweep orders only, type 2048)
    groups_by_partition = classify_order_groups(orders_by_partition)
    
    # Step 11: Compare real vs simulated (outputs to config.OUTPUTS_DIR)
    compare_real_vs_simulated(
        simulation_results_by_partition,
        groups_by_partition,
        orders_by_partition,
        config.OUTPUTS_DIR
    )
    
    # Print summary
    execution_time = time.time() - start_time
    print_summary(orders_by_partition, trades_by_partition, execution_time)


if __name__ == '__main__':
    main()
