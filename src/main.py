"""Centre Point Sweep Order Matching Pipeline - Main Orchestrator"""

import argparse
import time
from pathlib import Path

import data_processor as dp
import partition_processor as pp
import metrics_generator as mg
import sweep_execution_analyzer as sea
import unmatched_analyzer as uma
import config


def parse_arguments():
    """Parse CLI arguments with config.py fallback. CLI args override config defaults."""
    parser = argparse.ArgumentParser(
        description='Centre Point Sweep Order Matching Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use config defaults (config.TICKER, config.DATE)
  python main.py
  
  # Override ticker only
  python main.py --ticker cba
  
  # Override both ticker and date
  python main.py --ticker cba --date 20240905
  
  # Override execution mode
  python main.py --ticker cba --date 20240905 --parallel
        """
    )
    
    parser.add_argument('--ticker', type=str, default=None,
                        help=f'Ticker symbol (default: {config.TICKER})')
    parser.add_argument('--date', type=str, default=None,
                        help=f'Date in YYYYMMDD format (default: {config.DATE})')
    parser.add_argument('--parallel', action='store_true',
                        help='Enable parallel processing (overrides config)')
    parser.add_argument('--sequential', action='store_true',
                        help='Force sequential processing (overrides config)')
    
    return parser.parse_args()


def get_runtime_config(args):
    """Build runtime configuration from CLI args and config defaults. CLI takes priority."""
    # Determine input files (CLI overrides config)
    input_files = config.get_input_files(ticker=args.ticker, date=args.date)
    
    # Validate required files exist
    config.validate_input_files(input_files)
    
    # Determine parallel processing mode (CLI overrides config)
    if args.parallel:
        enable_parallel = True
    elif args.sequential:
        enable_parallel = False
    else:
        enable_parallel = config.ENABLE_PARALLEL_PROCESSING
    
    return {
        'ticker': args.ticker or config.TICKER,
        'date': args.date or config.DATE,
        'input_files': input_files,
        'enable_parallel': enable_parallel,
    }


def setup_directories():
    """Create output directories if they don't exist."""
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)


def extract_and_prepare_data(input_files):
    """Extract orders, trades, reference data, order states, and execution times (steps 1-6)."""
    print("\n" + "="*80)
    print("DATA EXTRACTION AND PREPARATION (Steps 1-6)")
    print("="*80)
    
    # Step 1: Extract orders
    orders_by_partition = dp.extract_orders(
        input_files['orders'], 
        config.PROCESSED_DIR, 
        config.CENTRE_POINT_ORDER_TYPES, 
        config.CHUNK_SIZE, 
        config.COLUMN_MAPPING
    )
    
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return None
    
    # Step 2: Extract trades
    trades_by_partition = dp.extract_trades(
        input_files['trades'], 
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.COLUMN_MAPPING,
        config.CHUNK_SIZE
    )
    
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
    
    return {
        'orders': orders_by_partition,
        'trades': trades_by_partition,
        'order_states': order_states_by_partition,
        'last_execution': last_execution_by_partition,
        'nbbo': nbbo_by_partition,
        'partition_keys': list(orders_by_partition.keys()),
    }


def process_partitions_parallel_mode(partition_keys):
    """Process partitions in parallel mode (steps 7-12)."""
    print(f"\n{'='*80}")
    print(f"PARALLEL PROCESSING MODE")
    print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(partition_keys)} partitions")
    print(f"{'='*80}")
    
    partition_results = pp.process_partitions_parallel(
        partition_keys,
        config.PROCESSED_DIR,
        config.OUTPUTS_DIR,
        config.MAX_PARALLEL_WORKERS
    )
    
    return partition_results


def process_partitions_sequential_mode(data):
    """Process partitions in sequential mode (steps 7-12)."""
    print(f"\n{'='*80}")
    print(f"SEQUENTIAL PROCESSING MODE")
    print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")
    print(f"{'='*80}")
    
    # Step 7: Simulate sweep matching
    simulation_results_by_partition = pp.simulate_sweep_matching_sequential(
        data['orders'],
        data['order_states'],
        data['last_execution'],
        data['nbbo'],
        config.OUTPUTS_DIR
    )
    
    # Step 8: Calculate simulated metrics
    orders_with_sim_metrics_by_partition = pp.calculate_simulated_metrics_sequential(
        data['orders'],
        simulation_results_by_partition,
        config.PROCESSED_DIR,
        config.OUTPUTS_DIR
    )
    
    # Step 11: Calculate real trade metrics
    real_trade_metrics_by_partition = mg.calculate_real_trade_metrics(
        data['trades'],
        data['orders'],
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
        
        mg.generate_trade_comparison_reports(
            trade_comparison_by_partition,
            config.OUTPUTS_DIR,
            include_accuracy_summary=False
        )
    
    return simulation_results_by_partition


def run_final_analysis(processed_dir, outputs_dir, partition_keys):
    """Run sweep execution and unmatched orders analysis (steps 13-14 FINAL)."""
    print(f"\n{'='*80}")
    print(f"FINAL ANALYSIS (Steps 13-14)")
    print(f"{'='*80}")
    
    # Step 13: Sweep order execution analysis
    print("\n[Step 13] Analyzing sweep order execution...")
    sea.analyze_sweep_execution(
        processed_dir,
        outputs_dir,
        partition_keys
    )
    
    # Step 14: Unmatched orders root cause analysis (FINAL STEP)
    print("\n[Step 14] Analyzing unmatched orders (FINAL STEP)...")
    uma.analyze_unmatched_orders(
        processed_dir,
        outputs_dir,
        partition_keys
    )
    
    print("\n✓ Final analysis complete")


def print_summary(data, runtime_config, execution_time):
    """Print pipeline execution summary with configuration and results."""
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUMMARY")
    print("="*80)
    print(f"Pipeline completed 14 steps successfully")
    print(f"")
    
    print("Configuration:")
    print(f"  Ticker:  {runtime_config['ticker']}")
    print(f"  Date:    {runtime_config['date']}")
    print(f"  Mode:    {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
    print(f"")
    
    total_orders = sum(len(df) for df in data['orders'].values())
    total_trades = sum(len(df) for df in data['trades'].values()) if data['trades'] else 0
    num_partitions = len(data['orders'])
    
    print(f"Total Centre Point Orders: {total_orders:,}")
    print(f"Total Trades: {total_trades:,}")
    print(f"Number of Partitions: {num_partitions}")
    print(f"Execution Time: {execution_time:.2f} seconds")
    
    print("\nPartition Breakdown:")
    for partition_key in sorted(data['orders'].keys()):
        num_orders = len(data['orders'][partition_key])
        num_trades = len(data['trades'].get(partition_key, [])) if data['trades'] else 0
        print(f"  {partition_key}: {num_orders:,} orders, {num_trades:,} trades")
    
    print("\nOutput files:")
    print(f"  Processed data: {config.PROCESSED_DIR}/")
    print(f"  Final outputs:  {config.OUTPUTS_DIR}/")
    print("="*80)


def main():
    """Main pipeline: parse args -> load config -> extract -> process -> analyze."""
    start_time = time.time()
    
    # Parse CLI arguments
    args = parse_arguments()
    
    # Build runtime configuration (CLI overrides config)
    runtime_config = get_runtime_config(args)
    
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    print("\nRuntime Configuration:")
    print(f"  Ticker:     {runtime_config['ticker']}")
    print(f"  Date:       {runtime_config['date']}")
    print(f"  Mode:       {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
    print(f"\nSystem Configuration:")
    print(config.SYSTEM_CONFIG)
    print(f"\nDirectories:")
    print(f"  Processed:  {config.PROCESSED_DIR}/")
    print(f"  Outputs:    {config.OUTPUTS_DIR}/")
    
    # Setup directories
    setup_directories()
    
    # Extract and prepare data (steps 1-6)
    data = extract_and_prepare_data(runtime_config['input_files'])
    if data is None:
        return
    
    # Process partitions (steps 7-12)
    if runtime_config['enable_parallel'] and len(data['partition_keys']) > 1:
        process_partitions_parallel_mode(data['partition_keys'])
    else:
        process_partitions_sequential_mode(data)
    
    # Run final analysis (steps 13-14)
    run_final_analysis(config.PROCESSED_DIR, config.OUTPUTS_DIR, data['partition_keys'])
    
    # Print summary
    execution_time = time.time() - start_time
    print_summary(data, runtime_config, execution_time)


if __name__ == '__main__':
    main()
