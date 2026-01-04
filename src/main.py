"""Centre Point Sweep Order Matching Pipeline - Main Orchestrator"""

import time
from pathlib import Path

import data_processor as dp
import partition_processor as pp
import metrics_generator as mg
import config


def print_summary(orders_by_partition, trades_by_partition, execution_time):
    """Print pipeline execution summary."""
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUMMARY")
    print("="*80)
    
    total_orders = sum(len(df) for df in orders_by_partition.values())
    total_trades = sum(len(df) for df in trades_by_partition.values()) if trades_by_partition else 0
    num_partitions = len(orders_by_partition)
    
    print(f"Total Centre Point Orders: {total_orders:,}")
    print(f"Total Trades: {total_trades:,}")
    print(f"Number of Partitions: {num_partitions}")
    print(f"Execution Time: {execution_time:.2f} seconds")
    
    print("\nPartition Breakdown:")
    for partition_key in sorted(orders_by_partition.keys()):
        num_orders = len(orders_by_partition[partition_key])
        num_trades = len(trades_by_partition.get(partition_key, [])) if trades_by_partition else 0
        print(f"  {partition_key}: {num_orders:,} orders, {num_trades:,} trades")
    
    print("\nOutput files:")
    print(f"  Processed data: {config.PROCESSED_DIR}/")
    print(f"  Final outputs:  {config.OUTPUTS_DIR}/")
    print("="*80)


def main():
    """Main pipeline orchestrator."""
    start_time = time.time()
    
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    
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
    
    # Step 4: Process reference data
    reference_results = dp.process_reference_data(
        config.RAW_FOLDERS,
        config.PROCESSED_DIR,
        orders_by_partition,
        config.COLUMN_MAPPING
    )
    
    nbbo_by_partition = reference_results.get('nbbo', {})
    
    # Step 5: Extract order states
    order_states_by_partition = dp.get_orders_state(
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Step 6: Extract execution times
    last_execution_by_partition = dp.extract_last_execution_times(
        orders_by_partition, 
        trades_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING
    )
    
    # Steps 7-12: Process partitions
    partition_keys = list(orders_by_partition.keys())
    
    if config.ENABLE_PARALLEL_PROCESSING and len(partition_keys) > 1:
        print(f"\n{'='*80}")
        print(f"PARALLEL PROCESSING ENABLED")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(partition_keys)} partitions")
        print(f"{'='*80}")
        
        partition_results = pp.process_partitions_parallel(
            partition_keys,
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR,
            config.MAX_PARALLEL_WORKERS
        )
        
    else:
        # Sequential processing
        if not config.ENABLE_PARALLEL_PROCESSING:
            print(f"\n{'='*80}")
            print(f"SEQUENTIAL PROCESSING (Parallel disabled in config)")
            print(f"{'='*80}")
        else:
            print(f"\n{'='*80}")
            print(f"SEQUENTIAL PROCESSING (Only 1 partition)")
            print(f"{'='*80}")
        
        # Step 7: Simulate sweep matching
        simulation_results_by_partition = pp.simulate_sweep_matching_sequential(
            orders_by_partition,
            order_states_by_partition,
            last_execution_by_partition,
            nbbo_by_partition,
            config.OUTPUTS_DIR
        )
        
        # Step 8: Calculate simulated metrics
        orders_with_sim_metrics_by_partition = pp.calculate_simulated_metrics_sequential(
            orders_by_partition,
            simulation_results_by_partition,
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR
        )
        
        # Step 11: Calculate real trade metrics
        real_trade_metrics_by_partition = mg.calculate_real_trade_metrics(
            trades_by_partition,
            orders_by_partition,
            config.PROCESSED_DIR,
            config.COLUMN_MAPPING
        )
        
        # Step 12: Compare real vs simulated trades
        if real_trade_metrics_by_partition:
            trade_comparison_by_partition = mg.compare_real_vs_simulated_trades(
                real_trade_metrics_by_partition,
                simulation_results_by_partition,
                config.OUTPUTS_DIR
            )
            
            # Generate only trade_level_comparison.csv (not trade_accuracy_summary.csv)
            mg.generate_trade_comparison_reports(
                trade_comparison_by_partition,
                config.OUTPUTS_DIR,
                include_accuracy_summary=False
            )
    
    # Print summary
    execution_time = time.time() - start_time
    print_summary(orders_by_partition, trades_by_partition, execution_time)


if __name__ == '__main__':
    main()
