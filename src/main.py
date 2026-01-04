"""
Centre Point Sweep Order Matching Pipeline

Main entry point for the end-to-end pipeline that extracts Centre Point orders,
simulates sweep matching with time-priority algorithm, and compares real vs simulated execution.
"""

import pandas as pd
from pathlib import Path
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import data_processor as dp
import sweep_simulator as ss
import metrics_generator as mg
import config


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def simulate_sweep_matching(orders_by_partition, order_states_by_partition, 
                           last_execution_by_partition, nbbo_by_partition, output_dir):
    """Step 7: Simulate sweep matching for all partitions."""
    print("\n[7/11] Simulating sweep matching...")
    
    simulation_results_by_partition = {}
    
    for partition_key in orders_by_partition.keys():
        # Load partition data
        partition_data = dp.load_partition_data(
            partition_key, 
            config.PROCESSED_DIR
        )
        
        if not partition_data or 'orders_before' not in partition_data:
            continue
        
        # Override with in-memory datasets
        if partition_key in order_states_by_partition:
            partition_data['orders_before'] = order_states_by_partition[partition_key]['before']
            partition_data['orders_after'] = order_states_by_partition[partition_key]['after']
            # Note: orders_final not passed - used for debugging only, saved separately
        
        if partition_key in last_execution_by_partition:
            partition_data['last_execution'] = last_execution_by_partition[partition_key]
        
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
        simulated_trades = sim_results.get('simulated_trades')
        
        # Save simulation results
        partition_output_dir = Path(output_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        
        order_summary.to_csv(partition_output_dir / 'simulation_order_summary.csv', index=False)
        match_details.to_csv(partition_output_dir / 'simulation_match_details.csv', index=False)
        
        # Save simulated trades file
        if simulated_trades is not None and len(simulated_trades) > 0:
            date_part = partition_key.split('/')[0]  # Extract '2024-09-05'
            trades_filename = f's_t_{date_part}.csv.gz'
            simulated_trades.to_csv(
                partition_output_dir / trades_filename,
                index=False,
                compression='gzip'
            )
        
        simulation_results_by_partition[partition_key] = {
            'order_summary': order_summary,
            'match_details': match_details
        }
    
    print(f"   Completed sweep simulation for {len(simulation_results_by_partition)} partitions")
    return simulation_results_by_partition


def calculate_simulated_metrics_step(orders_by_partition, simulation_results_by_partition, output_dir):
    """Step 8: Calculate simulated metrics for all partitions."""
    print("\n[8/11] Calculating simulated metrics...")
    
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
    """Step 9: Classify sweep orders (type 2048 only) into groups based on real execution."""
    groups_by_partition = dp.classify_order_groups(
        orders_by_partition,
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    return groups_by_partition


def compare_real_vs_simulated(simulation_results_by_partition, groups_by_partition, orders_by_partition, output_dir):
    """Step 10: Compare real vs simulated execution for sweep orders with statistical analysis."""
    print("\n[10/11] Comparing real vs simulated execution (sweep orders)...")
    
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
# PARALLEL PARTITION PROCESSING
# ============================================================================

def process_single_partition(partition_key, processed_dir, outputs_dir, enable_trade_comparison=True):
    """
    Process a single partition through all analysis steps (Steps 7-12).
    
    This function is designed to be called in parallel for each partition.
    Each partition is completely independent and processes:
    - Sweep simulation
    - Simulated metrics calculation
    - Order group classification
    - Sweep order comparison
    - Trade-level metrics and comparison
    
    Args:
        partition_key: Partition identifier (e.g., "2024-09-05/85603")
        processed_dir: Directory with processed data
        outputs_dir: Directory for output files
        enable_trade_comparison: Whether to run trade-level comparison
    
    Returns:
        Dictionary with results and status for this partition
    """
    
    try:
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        
        # Load partition data
        partition_data = dp.load_partition_data(partition_key, processed_dir)
        
        if not partition_data or 'orders_before' not in partition_data:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No partition data found'
            }
        
        # Load NBBO if available
        nbbo_file = partition_dir / "nbbo.csv.gz"
        if nbbo_file.exists():
            partition_data['nbbo'] = pd.read_csv(nbbo_file)
        else:
            partition_data['nbbo'] = None
        
        # Step 7: Simulate sweep matching
        sim_results = ss.simulate_partition(partition_key, partition_data)
        
        if not sim_results:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No simulation results'
            }
        
        # Save simulation results
        partition_output_dir = Path(outputs_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        
        sim_results['order_summary'].to_csv(partition_output_dir / 'simulation_order_summary.csv', index=False)
        sim_results['match_details'].to_csv(partition_output_dir / 'simulation_match_details.csv', index=False)
        
        # Step 8: Calculate simulated metrics
        orders_after = partition_data.get('orders_after')
        if orders_after is not None:
            orders_with_metrics = mg.calculate_simulated_metrics(
                orders_after,
                sim_results['order_summary'],
                sim_results['match_details']
            )
            orders_with_metrics.to_csv(partition_output_dir / 'orders_with_simulated_metrics.csv', index=False)
        
        # Step 9: Classify order groups (sweep orders only)
        groups = _classify_order_group_for_partition(partition_key, processed_dir)
        
        # Step 10: Compare sweep orders
        if groups:
            _compare_sweep_for_partition(partition_key, sim_results, groups, processed_dir, partition_output_dir)
        
        # Steps 11-12: Trade-level comparison
        if enable_trade_comparison:
            _compare_trades_for_partition(partition_key, sim_results, processed_dir, partition_output_dir)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_sweep_orders': len(sim_results['order_summary']),
            'num_matches': len(sim_results['match_details'])
        }
        
    except Exception as e:
        return {
            'partition_key': partition_key,
            'status': 'error',
            'error': str(e)
        }


def _classify_order_group_for_partition(partition_key, processed_dir):
    """Classify orders into groups for a single partition."""
    
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    
    orders_after_file = partition_dir / "orders_after_matching.csv"
    if not orders_after_file.exists():
        return None
    
    orders_after = pd.read_csv(orders_after_file)
    
    # Filter for sweep orders only (type 2048)
    sweep_orders = orders_after[orders_after['exchangeordertype'] == 2048].copy()
    
    if len(sweep_orders) == 0:
        return None
    
    # Classify based on real execution
    group1 = sweep_orders[sweep_orders['leavesquantity'] == 0].copy()
    group2 = sweep_orders[
        (sweep_orders['leavesquantity'] > 0) & 
        (sweep_orders['totalmatchedquantity'] > 0)
    ].copy()
    group3 = sweep_orders[
        (sweep_orders['leavesquantity'] > 0) & 
        (sweep_orders['totalmatchedquantity'] == 0)
    ].copy()
    
    return {
        'Group 1 (Fully Filled)': group1,
        'Group 2 (Partially Filled)': group2,
        'Group 3 (Unfilled)': group3
    }


def _compare_sweep_for_partition(partition_key, sim_results, groups, processed_dir, output_dir):
    """Compare sweep orders for a single partition."""
    
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    
    # Load orders_after_matching for real execution data
    orders_after_file = partition_dir / 'orders_after_matching.csv'
    if not orders_after_file.exists():
        return
    
    orders_after = pd.read_csv(orders_after_file)
    
    # Load aggregated trades for price comparison
    trades_agg_file = partition_dir / 'cp_trades_aggregated.csv.gz'
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
    mg.generate_sweep_comparison_reports(
        partition_key,
        comparison_results,
        output_dir
    )


def _compare_trades_for_partition(partition_key, sim_results, processed_dir, output_dir):
    """Compare trades for a single partition."""
    
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    
    # Load trades data
    trades_file = partition_dir / "cp_trades_matched.csv.gz"
    if not trades_file.exists():
        return
    
    trades_df = pd.read_csv(trades_file)
    
    if len(trades_df) == 0:
        return
    
    # Load orders to identify sweep orders
    orders_before_file = partition_dir / "orders_before_matching.csv"
    if not orders_before_file.exists():
        return
    
    orders_before = pd.read_csv(orders_before_file)
    
    # Standardize column names
    if 'order_id' in orders_before.columns:
        orders_before = orders_before.rename(columns={'order_id': 'orderid'})
    if 'order_id' in trades_df.columns:
        trades_df = trades_df.rename(columns={'order_id': 'orderid'})
    
    # Get sweep order IDs
    sweep_orderids = orders_before[
        orders_before['exchangeordertype'] == 2048
    ]['orderid'].unique()
    
    if len(sweep_orderids) == 0:
        return
    
    # Filter trades to only those involving sweep orders
    sweep_trades = trades_df[trades_df['orderid'].isin(sweep_orderids)].copy()
    
    if len(sweep_trades) == 0:
        return
    
    # Calculate per-trade metrics
    trade_metrics = mg._calculate_per_trade_metrics(sweep_trades, orders_before)
    
    # Calculate aggregated metrics per order
    order_metrics = mg._aggregate_trade_metrics_per_order(sweep_trades)
    
    # Aggregate simulated trades
    sim_aggregated = mg._aggregate_simulated_trades_per_order(
        sim_results['match_details'],
        sim_results['order_summary']
    )
    
    # Compare
    comparison = mg._compare_order_level_trades(order_metrics, sim_aggregated)
    accuracy_summary = mg._calculate_trade_accuracy_summary(comparison)
    
    # Save reports
    comparison.to_csv(output_dir / 'trade_level_comparison.csv', index=False)
    accuracy_summary.to_csv(output_dir / 'trade_accuracy_summary.csv', index=False)


def process_partitions_parallel(partition_keys, processed_dir, outputs_dir, max_workers):
    """
    Process multiple partitions in parallel.
    
    Args:
        partition_keys: List of partition identifiers
        processed_dir: Directory with processed data
        outputs_dir: Directory for output files
        max_workers: Maximum number of parallel workers
    
    Returns:
        Dictionary mapping partition_key to results
    """
    
    print(f"\n{'='*80}")
    print(f"PARALLEL PARTITION PROCESSING")
    print(f"{'='*80}")
    print(f"Processing {len(partition_keys)} partitions with {max_workers} workers...")
    
    partition_results = {}
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all partition jobs
        futures = {
            executor.submit(
                process_single_partition,
                partition_key,
                processed_dir,
                outputs_dir,
                True  # enable_trade_comparison
            ): partition_key
            for partition_key in partition_keys
        }
        
        # Collect results as they complete
        completed = 0
        for future in as_completed(futures):
            partition_key = futures[future]
            completed += 1
            
            try:
                result = future.result()
                partition_results[partition_key] = result
                
                status = result.get('status')
                if status == 'success':
                    print(f"  [{completed}/{len(partition_keys)}] ✓ {partition_key}: "
                          f"{result.get('num_sweep_orders', 0):,} sweep orders, "
                          f"{result.get('num_matches', 0):,} matches")
                elif status == 'skipped':
                    print(f"  [{completed}/{len(partition_keys)}] ⊘ {partition_key}: "
                          f"Skipped - {result.get('reason', 'Unknown')}")
                elif status == 'error':
                    print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: "
                          f"ERROR - {result.get('error', 'Unknown')}")
                    
            except Exception as e:
                print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: EXCEPTION - {str(e)}")
                partition_results[partition_key] = {
                    'partition_key': partition_key,
                    'status': 'exception',
                    'error': str(e)
                }
    
    # Print summary
    successful = sum(1 for r in partition_results.values() if r.get('status') == 'success')
    failed = sum(1 for r in partition_results.values() if r.get('status') in ('error', 'exception'))
    skipped = sum(1 for r in partition_results.values() if r.get('status') == 'skipped')
    
    print(f"\n{'='*80}")
    print(f"PARALLEL PROCESSING COMPLETE")
    print(f"  ✓ Successful: {successful}/{len(partition_keys)}")
    print(f"  ✗ Failed: {failed}/{len(partition_keys)}")
    print(f"  ⊘ Skipped: {skipped}/{len(partition_keys)}")
    print(f"{'='*80}")
    
    return partition_results


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
    
    # Step 4: Process all reference data (session, reference, participants, nbbo)
    reference_results = dp.process_reference_data(
        config.RAW_FOLDERS,
        config.PROCESSED_DIR,
        orders_by_partition,
        config.COLUMN_MAPPING
    )
    
    # Extract NBBO results for simulation
    nbbo_by_partition = reference_results.get('nbbo', {})
    
    # Step 5: Extract order states (before/after matching)
    order_states_by_partition = dp.get_orders_state(
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Step 6: Extract last execution times (order book lifecycle tracking)
    last_execution_by_partition = dp.extract_last_execution_times(
        orders_by_partition, 
        trades_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Steps 7-12: Process partitions (parallel or sequential)
    partition_keys = list(orders_by_partition.keys())
    
    if config.ENABLE_PARALLEL_PROCESSING and len(partition_keys) > 1:
        print(f"\n{'='*80}")
        print(f"PARALLEL PROCESSING ENABLED")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(partition_keys)} partitions")
        print(f"{'='*80}")
        
        # Process all partitions in parallel
        partition_results = process_partitions_parallel(
            partition_keys,
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR,
            config.MAX_PARALLEL_WORKERS
        )
        
    else:
        # Sequential processing (original implementation)
        if not config.ENABLE_PARALLEL_PROCESSING:
            print(f"\n{'='*80}")
            print(f"SEQUENTIAL PROCESSING (Parallel disabled in config)")
            print(f"{'='*80}")
        else:
            print(f"\n{'='*80}")
            print(f"SEQUENTIAL PROCESSING (Only 1 partition)")
            print(f"{'='*80}")
        
        # Step 7: Simulate sweep matching (outputs to config.OUTPUTS_DIR)
        simulation_results_by_partition = simulate_sweep_matching(
            orders_by_partition,
            order_states_by_partition,
            last_execution_by_partition,
            nbbo_by_partition,
            config.OUTPUTS_DIR
        )
        
        # Step 8: Calculate simulated metrics (outputs to config.OUTPUTS_DIR)
        orders_with_sim_metrics_by_partition = calculate_simulated_metrics_step(
            orders_by_partition,
            simulation_results_by_partition,
            config.OUTPUTS_DIR
        )
        
        # Step 9: Classify order groups (sweep orders only, type 2048)
        groups_by_partition = classify_order_groups(orders_by_partition)
        
        # Step 10: Compare real vs simulated (outputs to config.OUTPUTS_DIR)
        compare_real_vs_simulated(
            simulation_results_by_partition,
            groups_by_partition,
            orders_by_partition,
            config.OUTPUTS_DIR
        )
        
        # Step 11: Calculate real trade metrics for sweep orders
        real_trade_metrics_by_partition = mg.calculate_real_trade_metrics(
            trades_by_partition,
            orders_by_partition,
            config.PROCESSED_DIR,
            config.COLUMN_MAPPING
        )
        
        # Step 12: Compare real vs simulated trades at trade level
        if real_trade_metrics_by_partition:
            trade_comparison_by_partition = mg.compare_real_vs_simulated_trades(
                real_trade_metrics_by_partition,
                simulation_results_by_partition,
                config.OUTPUTS_DIR
            )
            
            # Generate trade comparison reports
            mg.generate_trade_comparison_reports(
                trade_comparison_by_partition,
                config.OUTPUTS_DIR
            )
    
    # Print summary
    execution_time = time.time() - start_time
    print_summary(orders_by_partition, trades_by_partition, execution_time)


if __name__ == '__main__':
    main()
