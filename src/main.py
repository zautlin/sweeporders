"""Centre Point Sweep Order Matching Pipeline - Main Orchestrator"""

import argparse
import time
from pathlib import Path

import pipeline.data_processor as dp
import pipeline.partition_processor as pp
import pipeline.metrics_generator as mg
import analysis.sweep_execution_analyzer as sea
import analysis.unmatched_analyzer as uma
import config.config as config
from discovery.security_discovery import SecurityDiscovery
from utils.statistics_layer import StatisticsEngine, SCIPY_AVAILABLE


def parse_arguments():
    """Parse CLI arguments with config.py fallback. CLI args override config defaults."""
    parser = argparse.ArgumentParser(
        description='Centre Point Sweep Order Matching Pipeline - 4 Stage Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Security selection (mutually exclusive)
    security_group = parser.add_mutually_exclusive_group()
    security_group.add_argument('--ticker', type=str, default=None,
                        help=f'Ticker symbol (legacy, default: {config.TICKER})')
    security_group.add_argument('--orderbookid', type=int, default=None,
                        help='OrderbookID to process')
    security_group.add_argument('--auto-discover', action='store_true',
                        help='Auto-discover and process all valid securities for the date')
    
    parser.add_argument('--date', type=str, default=None,
                        help=f'Date in YYYYMMDD format (default: {config.DATE})')
    
    # Discovery options
    parser.add_argument('--list-securities', action='store_true',
                        help='List all available securities for the date and exit')
    parser.add_argument('--list-dates', action='store_true',
                        help='List all available dates in raw data and exit')
    parser.add_argument('--min-orders', type=int, default=config.MIN_ORDERS_THRESHOLD,
                        help=f'Minimum orders threshold for valid security (default: {config.MIN_ORDERS_THRESHOLD})')
    parser.add_argument('--min-trades', type=int, default=config.MIN_TRADES_THRESHOLD,
                        help=f'Minimum trades threshold for valid security (default: {config.MIN_TRADES_THRESHOLD})')
    
    # Pipeline stages
    parser.add_argument('--stage', type=int, choices=[1, 2, 3, 4], action='append',
                        help='Pipeline stage(s) to run (can specify multiple): 1=extraction, 2=simulation, 3=analysis, 4=aggregation')
    
    # Processing options
    parser.add_argument('--parallel', action='store_true',
                        help='Enable parallel processing for Stage 2 (overrides config)')
    parser.add_argument('--sequential', action='store_true',
                        help='Force sequential processing for Stage 2 (overrides config)')
    
    # Statistical testing options
    stats_group = parser.add_mutually_exclusive_group()
    stats_group.add_argument('--enable-stats', action='store_true',
                        help='Enable statistical tests (t-tests, p-values, confidence intervals)')
    stats_group.add_argument('--disable-stats', action='store_true',
                        help='Disable statistical tests (only descriptive statistics)')
    
    return parser.parse_args()


def get_runtime_config(args):
    """Build runtime config from CLI args and config. CLI takes priority."""
    # Handle list operations first (they exit early)
    discovery = SecurityDiscovery(min_orders=args.min_orders, min_trades=args.min_trades)
    
    if args.list_dates:
        dates = discovery.get_available_dates()
        print("\nAvailable dates in raw data:")
        if dates:
            for date in dates:
                print(f"  - {date}")
        else:
            print("  No data files found")
        print()
        exit(0)
    
    date = args.date or config.DATE
    
    if args.list_securities:
        if not date:
            print("Error: --list-securities requires --date argument")
            exit(1)
        discovery.print_summary(date)
        exit(0)
    
    # Determine which stages to run
    stages = args.stage if args.stage else None  # None means run all stages
    
    # Determine securities to process
    securities_to_process = []
    
    if args.auto_discover:
        # Auto-discover all valid securities
        if not date:
            raise ValueError("--auto-discover requires --date argument")
        
        valid_securities = discovery.get_valid_securities(date)
        if not valid_securities:
            raise ValueError(f"No valid securities found for date {date}")
        
        securities_to_process = valid_securities
        print(f"\nAuto-discovered {len(securities_to_process)} valid securities:")
        for sec in securities_to_process:
            print(f"  - {sec}")
    
    elif args.orderbookid:
        # Process specific orderbookid
        if not date:
            raise ValueError("--orderbookid requires --date argument")
        
        # Validate orderbookid exists
        all_securities = discovery.discover_securities_for_date(date)
        security = next((s for s in all_securities if s.orderbookid == args.orderbookid), None)
        
        if not security:
            raise ValueError(f"OrderbookID {args.orderbookid} not found for date {date}")
        
        if not (security.in_orders and security.in_trades):
            raise ValueError(f"OrderbookID {args.orderbookid} missing order or trade data")
        
        securities_to_process = [security]
    
    elif args.ticker:
        # Legacy ticker-based lookup
        if not date:
            raise ValueError("--ticker requires --date argument")
        
        orderbookid = discovery.get_orderbookid_from_ticker(args.ticker, date)
        if not orderbookid:
            raise ValueError(f"Ticker {args.ticker} not found for date {date}")
        
        # Get security info
        all_securities = discovery.discover_securities_for_date(date)
        security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
        
        if not security:
            raise ValueError(f"Security not found for ticker {args.ticker}")
        
        securities_to_process = [security]
    
    else:
        # Default: use config ticker
        if stages is None or any(s in [1, 2, 3] for s in stages):
            # Try to use config ticker
            ticker = config.TICKER
            if not date:
                raise ValueError("Date is required for processing")
            
            orderbookid = discovery.get_orderbookid_from_ticker(ticker, date)
            if orderbookid:
                all_securities = discovery.discover_securities_for_date(date)
                security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
                if security:
                    securities_to_process = [security]
                else:
                    raise ValueError(f"Security not found for config ticker {ticker}")
            else:
                raise ValueError(f"Config ticker {ticker} not found for date {date}. Use --orderbookid or --ticker")
    
    # Validate: Stages 1-3 require securities
    if stages and any(s in [1, 2, 3] for s in stages):
        if not securities_to_process:
            raise ValueError("Stages 1-3 require security specification (--ticker, --orderbookid, or --auto-discover)")
    
    # Build input files for each security (only for stages 1-3)
    securities_with_files = []
    if stages is None or any(s in [1, 2, 3] for s in stages):
        for security in securities_to_process:
            # For now, we process one security at a time
            # TODO: Support multiple securities in one run
            input_files = config.get_input_files(ticker=security.ticker, date=date)
            
            # Validate required files exist (only for Stage 1)
            if stages is None or 1 in stages:
                config.validate_input_files(input_files)
            
            securities_with_files.append({
                'security': security,
                'input_files': input_files
            })
    
    # Determine parallel processing mode (CLI overrides config) - only for Stage 2
    if args.parallel:
        enable_parallel = True
    elif args.sequential:
        enable_parallel = False
    else:
        enable_parallel = config.ENABLE_PARALLEL_PROCESSING
    
    # Determine statistics mode
    if args.enable_stats:
        enable_stats = True
    elif args.disable_stats:
        enable_stats = False
    else:
        enable_stats = config.ENABLE_STATISTICAL_TESTS
    
    # Create statistics engine
    stats_engine = StatisticsEngine(
        enable_stats=enable_stats,
        force_simple=config.FORCE_SIMPLE_STATS
    )
    
    return {
        'date': date,
        'securities': securities_with_files,
        'enable_parallel': enable_parallel,
        'stages': stages,  # None means all stages, or list of stage numbers
        'stats_engine': stats_engine,
    }


def setup_directories():
    """Create output directories if they don't exist."""
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)


def extract_and_prepare_data(input_files):
    """Stage 1: Extract orders, trades, reference data, order states, and execution times (steps 1-6)."""
    print("\n" + "="*80)
    print("STAGE 1: DATA EXTRACTION & PREPARATION (Steps 1-6)")
    print("="*80)
    
    # Step 1: Extract orders
    orders_by_partition = dp.extract_orders(
        input_files['orders'], 
        config.PROCESSED_DIR, 
        config.CENTRE_POINT_ORDER_TYPES, 
        config.CHUNK_SIZE
    )
    
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return None
    
    # Step 2: Extract trades
    trades_by_partition = dp.extract_trades(
        input_files['trades'], 
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.CHUNK_SIZE
    )
    
    # Step 4: Process reference data
    reference_results = dp.process_reference_data(
        config.RAW_FOLDERS,
        config.PROCESSED_DIR,
        orders_by_partition
    )
    
    nbbo_by_partition = reference_results.get('nbbo', {})
    
    # Step 5: Extract order states
    order_states_by_partition = dp.get_orders_state(
        orders_by_partition, 
        config.PROCESSED_DIR
    )
    
    # Step 6: Extract execution times
    last_execution_by_partition = dp.extract_last_execution_times(
        orders_by_partition, 
        trades_by_partition, 
        config.PROCESSED_DIR
    )
    
    return {
        'orders': orders_by_partition,
        'trades': trades_by_partition,
        'order_states': order_states_by_partition,
        'last_execution': last_execution_by_partition,
        'nbbo': nbbo_by_partition,
        'partition_keys': list(orders_by_partition.keys()),
    }


def run_simulations_and_lob(data, enable_parallel):
    """Run simulations and create LOB states for all partitions (steps 7-12).
    Uses parallel or sequential processing based on enable_parallel flag."""
    print(f"\n{'='*80}")
    print(f"STAGE 2: SIMULATION & LOB STATES (Steps 7-12)")
    print(f"{'='*80}")
    
    if enable_parallel and len(data['partition_keys']) > 1:
        # Parallel mode
        print(f"PARALLEL PROCESSING MODE")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(data['partition_keys'])} partitions")
        
        partition_results = pp.process_partitions_parallel(
            data['partition_keys'],
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR,
            config.MAX_PARALLEL_WORKERS
        )
        
        return partition_results
    else:
        # Sequential mode
        print(f"SEQUENTIAL PROCESSING MODE")
        print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")
        
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
            config.PROCESSED_DIR
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


def print_summary(data, runtime_config, execution_time):
    """Print pipeline execution summary with configuration and results."""
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUMMARY")
    print("="*80)
    print(f"Pipeline completed successfully")
    print(f"")
    
    print("Configuration:")
    print(f"  Date:    {runtime_config['date']}")
    
    securities = runtime_config.get('securities', [])
    if securities:
        if len(securities) == 1:
            sec = securities[0]['security']
            ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
            print(f"  Security: OrderbookID {sec.orderbookid} ({ticker_str})")
        else:
            print(f"  Securities: {len(securities)} processed")
    
    print(f"  Mode:    {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
    
    stats_engine = runtime_config.get('stats_engine')
    if stats_engine:
        print(f"  Statistics: {stats_engine.get_tier_name()}")
    
    print(f"")
    
    if data:
        total_orders = sum(len(df) for df in data['orders'].values())
        total_trades = sum(len(df) for df in data['trades'].values()) if data['trades'] else 0
        num_partitions = len(data['orders'])
        
        print(f"Total Centre Point Orders: {total_orders:,}")
        print(f"Total Trades: {total_trades:,}")
        print(f"Number of Partitions: {num_partitions}")
    
    print(f"Execution Time: {execution_time:.2f} seconds")
    
    if data:
        print("\nPartition Breakdown:")
        for partition_key in sorted(data['orders'].keys()):
            num_orders = len(data['orders'][partition_key])
            num_trades = len(data['trades'].get(partition_key, [])) if data['trades'] else 0
            print(f"  {partition_key}: {num_orders:,} orders, {num_trades:,} trades")
    
    print("\nOutput files:")
    print(f"  Processed data: {config.PROCESSED_DIR}/")
    print(f"  Final outputs:  {config.OUTPUTS_DIR}/")
    print(f"  Aggregated:     {config.AGGREGATED_DIR}/")
    print("="*80)


def run_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine=None):
    """Run sweep execution and unmatched order analysis (steps 13-14) plus volume analysis for single security."""
    print(f"\n{'='*80}")
    print(f"STAGE 3: PER-SECURITY ANALYSIS (Steps 13-14 + Volume Analysis)")
    print(f"{'='*80}")
    
    # Step 13: Sweep order execution analysis
    print("\n[Step 13] Analyzing sweep order execution...")
    sea.analyze_sweep_execution(
        processed_dir,
        outputs_dir,
        partition_keys,
        stats_engine=stats_engine
    )
    
    # Step 14: Unmatched orders root cause analysis
    print("\n[Step 14] Analyzing unmatched orders...")
    uma.analyze_unmatched_orders(
        processed_dir,
        outputs_dir,
        partition_keys
    )
    
    # Volume-based analysis (NEW)
    print("\n[Volume Analysis] Analyzing execution by order volume...")
    import analysis.volume_analyzer as va
    volume_summary = va.analyze_by_volume(
        outputs_dir,
        partition_keys,
        method='quartile',
        stats_engine=stats_engine
    )
    
    print(f"\n✓ Stage 3 complete (per-security analysis + volume analysis)")
    return volume_summary


def run_cross_security_aggregation(runtime_config):
    """Aggregate sweep and volume results across all securities with statistical analysis."""
    print(f"\n{'='*80}")
    print(f"STAGE 4: CROSS-SECURITY AGGREGATION")
    print(f"{'='*80}")
    
    print("\n[Stage 4] Aggregating results across all securities...")
    
    # Import aggregation modules
    import aggregation.aggregate_sweep_results as agg
    import aggregation.analyze_aggregated_results as analyze
    import aggregation.aggregate_volume_analysis as vol_agg
    
    # Get stats engine from runtime config
    stats_engine = runtime_config.get('stats_engine')
    
    # Step 1: Aggregate all sweep comparison results
    print("  Step 1: Merging sweep_order_comparison_detailed.csv files...")
    aggregated_df = agg.aggregate_results(config.OUTPUTS_DIR)
    
    if aggregated_df is None:
        print("  ✗ No results found to aggregate")
        return False
    
    # Step 2: Save aggregated dataset
    print("  Step 2: Saving aggregated dataset...")
    output_path = config.AGGREGATED_DIR + '/aggregated_sweep_comparison.csv'
    agg.save_aggregated_results(aggregated_df, output_path)
    
    # Step 3: Run statistical analysis
    print("  Step 3: Running statistical analysis...")
    analyze.main(stats_engine)
    
    # Step 4: Aggregate volume analysis (NEW)
    print("  Step 4: Aggregating volume analysis across securities...")
    try:
        vol_agg.main(stats_engine)
        print("  ✓ Volume analysis aggregation complete")
    except Exception as e:
        print(f"  ⚠ Volume analysis aggregation skipped: {str(e)}")
    
    print("\n✓ Stage 4 complete (cross-security aggregation + volume analysis)")
    return True


def main():
    """Main pipeline: 4-stage architecture with --stage argument support."""
    start_time = time.time()
    
    # Parse CLI arguments
    args = parse_arguments()
    
    # Build runtime configuration (CLI overrides config)
    runtime_config = get_runtime_config(args)
    
    stages = runtime_config['stages']
    
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    
    # Print statistics tier information
    stats_engine = runtime_config['stats_engine']
    print(f"\nStatistics Tier: {stats_engine.get_tier_name()}")
    if stats_engine.tier == 2:
        print(f"  ⚠ Using approximate statistics (scipy not available)")
    elif stats_engine.tier == 1:
        print(f"  ℹ Statistical tests disabled (use --enable-stats to enable)")
    
    # Only print security/date config if running stages 1-3
    if stages is None or any(s in [1, 2, 3] for s in stages):
        print("\nRuntime Configuration:")
        print(f"  Date:       {runtime_config['date']}")
        
        securities = runtime_config['securities']
        if len(securities) == 1:
            sec = securities[0]['security']
            ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
            print(f"  Security:   OrderbookID {sec.orderbookid} ({ticker_str})")
            print(f"              {sec.order_count:,} orders, {sec.trade_count:,} trades")
        else:
            print(f"  Securities: {len(securities)} securities to process")
            for sec_info in securities:
                sec = sec_info['security']
                ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
                print(f"              - OrderbookID {sec.orderbookid} ({ticker_str}): {sec.order_count:,} orders, {sec.trade_count:,} trades")
        
        print(f"  Mode:       {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
        print(f"\nSystem Configuration:")
        print(config.SYSTEM_CONFIG)
        print(f"\nDirectories:")
        print(f"  Processed:  {config.PROCESSED_DIR}/")
        print(f"  Outputs:    {config.OUTPUTS_DIR}/")
    
    # Determine which stages to run
    if stages:
        print(f"\nStages to run: {', '.join(map(str, stages))}")
    else:
        print(f"\nRunning all stages (1-4)")
    
    # Setup directories
    setup_directories()
    
    # Initialize data variable for stages that need it
    data = None
    all_partition_keys = []
    
    # Process each security (for stages 1-3)
    securities = runtime_config.get('securities', [])
    
    for sec_info in securities:
        security = sec_info['security']
        input_files = sec_info['input_files']
        
        ticker_str = security.ticker.upper() if security.ticker else "Unknown"
        print(f"\n{'='*80}")
        print(f"Processing OrderbookID {security.orderbookid} ({ticker_str})")
        print(f"{'='*80}")
        
        # STAGE 1: Data Extraction & Preparation
        if stages is None or 1 in stages:
            data = extract_and_prepare_data(input_files)
            if data is None:
                print(f"\n✗ Stage 1 failed for OrderbookID {security.orderbookid} - no Centre Point orders found")
                continue
            print(f"\n✓ Stage 1 complete for OrderbookID {security.orderbookid}")
            all_partition_keys.extend(data['partition_keys'])
        
        # STAGE 2: Simulation & LOB States
        if stages is None or 2 in stages:
            # If Stage 2 is run without Stage 1, we need to load existing data
            if data is None:
                print(f"\n[Stage 2] Loading existing data from Stage 1 for OrderbookID {security.orderbookid}...")
                # For now, we'll require Stage 1 to be run first
                # TODO: Implement loading from processed directory
                print(f"✗ Stage 2 requires Stage 1 data. Please run Stage 1 first or run both together.")
                continue
            
            run_simulations_and_lob(data, runtime_config['enable_parallel'])
            print(f"\n✓ Stage 2 complete for OrderbookID {security.orderbookid}")
        
        # STAGE 3: Per-Security Analysis
        if stages is None or 3 in stages:
            # Stage 3 can run independently if processed files exist
            if data is None:
                # Get partition keys from processed directory (format: date/orderbookid)
                from pathlib import Path
                processed_path = Path(config.PROCESSED_DIR)
                partition_keys = []
                
                for date_dir in processed_path.iterdir():
                    if date_dir.is_dir() and not date_dir.name.startswith('.'):
                        for orderbook_dir in date_dir.iterdir():
                            if orderbook_dir.is_dir() and not orderbook_dir.name.startswith('.'):
                                # Filter by current orderbookid
                                if int(orderbook_dir.name) == security.orderbookid:
                                    partition_key = f"{date_dir.name}/{orderbook_dir.name}"
                                    partition_keys.append(partition_key)
                
                if not partition_keys:
                    print(f"✗ Stage 3 requires processed data from Stages 1-2 for OrderbookID {security.orderbookid}")
                    continue
            else:
                partition_keys = data['partition_keys']
            
            run_per_security_analysis(config.PROCESSED_DIR, config.OUTPUTS_DIR, partition_keys, runtime_config['stats_engine'])
            all_partition_keys.extend(partition_keys)
            print(f"\n✓ Stage 3 complete for OrderbookID {security.orderbookid}")
    
    # STAGE 4: Cross-Security Aggregation
    if stages is None or 4 in stages:
        run_cross_security_aggregation(runtime_config)
        print(f"\n✓ Stage 4 complete")
    
    # Print summary (only if we have data from Stage 1)
    execution_time = time.time() - start_time
    
    if data:
        print_summary(data, runtime_config, execution_time)
    else:
        print("\n" + "="*80)
        print("PIPELINE EXECUTION SUMMARY")
        print("="*80)
        print(f"Execution Time: {execution_time:.2f} seconds")
        print("="*80)


if __name__ == '__main__':
    main()
