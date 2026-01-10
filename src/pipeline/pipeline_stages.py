"""Pipeline stage execution functions."""

from pathlib import Path
import config.config as config
import pipeline.data_processor as dp
import pipeline.partition_processor as pp
import pipeline.metrics_generator as mg
import analysis.sweep_execution_analyzer as sea
import analysis.unmatched_analyzer as uma
import analysis.volume_analyzer as va
import aggregation.aggregate_sweep_results as agg
import aggregation.analyze_aggregated_results as analyze
import aggregation.aggregate_volume_analysis as vol_agg


def extract_and_prepare_data(input_files):
    """Extract orders, trades, reference data, order states, and execution times."""
    print("\n" + "="*80)
    print("STAGE 1: DATA EXTRACTION & PREPARATION (Steps 1-6)")
    print("="*80)
    
    orders_by_partition = dp.extract_orders(
        input_files['orders'], 
        config.PROCESSED_DIR, 
        config.CENTRE_POINT_ORDER_TYPES, 
        config.CHUNK_SIZE
    )
    
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return None
    
    trades_by_partition = dp.extract_trades(
        input_files['trades'], 
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.CHUNK_SIZE
    )
    
    reference_results = dp.process_reference_data(
        config.RAW_FOLDERS,
        config.PROCESSED_DIR,
        orders_by_partition
    )
    
    nbbo_by_partition = reference_results.get('nbbo', {})
    
    order_states_by_partition = dp.get_orders_state(
        orders_by_partition, 
        config.PROCESSED_DIR
    )
    
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
    """Run simulations and create LOB states."""
    print(f"\n{'='*80}")
    print(f"STAGE 2: SIMULATION & LOB STATES (Steps 7-12)")
    print(f"{'='*80}")
    
    if enable_parallel and len(data['partition_keys']) > 1:
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
        print(f"SEQUENTIAL PROCESSING MODE")
        print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")
        
        simulation_results_by_partition = pp.simulate_sweep_matching_sequential(
            data['orders'],
            data['order_states'],
            data['last_execution'],
            data['nbbo'],
            config.OUTPUTS_DIR
        )
        
        orders_with_sim_metrics_by_partition = pp.calculate_simulated_metrics_sequential(
            data['orders'],
            simulation_results_by_partition,
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR
        )
        
        real_trade_metrics_by_partition = mg.calculate_real_trade_metrics(
            data['trades'],
            data['orders'],
            config.PROCESSED_DIR
        )
        
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


def run_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine):
    """Run sweep execution and unmatched order analysis plus volume analysis."""
    print(f"\n{'='*80}")
    print(f"STAGE 3: PER-SECURITY ANALYSIS (Steps 13-14 + Volume Analysis)")
    print(f"{'='*80}")
    
    print("\n[Step 13] Analyzing sweep order execution...")
    sea.analyze_sweep_execution(
        processed_dir,
        outputs_dir,
        partition_keys,
        stats_engine=stats_engine
    )
    
    print("\n[Step 14] Analyzing unmatched orders...")
    uma.analyze_unmatched_orders(
        processed_dir,
        outputs_dir,
        partition_keys
    )
    
    print("\n[Volume Analysis] Analyzing execution by order volume...")
    volume_summary = va.analyze_by_volume(
        outputs_dir,
        partition_keys,
        method='quartile',
        stats_engine=stats_engine
    )
    
    print(f"\n✓ Stage 3 complete (per-security analysis + volume analysis)")
    return volume_summary


def run_cross_security_aggregation(runtime_config):
    """Aggregate sweep and volume results across all securities."""
    print(f"\n{'='*80}")
    print(f"STAGE 4: CROSS-SECURITY AGGREGATION")
    print(f"{'='*80}")
    
    print("\n[Stage 4] Aggregating results across all securities...")
    
    stats_engine = runtime_config.get('stats_engine')
    
    print("  Step 1: Merging sweep_order_comparison_detailed.csv files...")
    aggregated_df = agg.aggregate_results(config.OUTPUTS_DIR)
    
    if aggregated_df is None:
        print("  ✗ No results found to aggregate")
        return False
    
    print("  Step 2: Saving aggregated dataset...")
    output_path = config.AGGREGATED_DIR + '/aggregated_sweep_comparison.csv'
    agg.save_aggregated_results(aggregated_df, output_path)
    
    print("  Step 3: Running statistical analysis...")
    analyze.main(stats_engine)
    
    print("  Step 4: Aggregating volume analysis across securities...")
    try:
        vol_agg.main(stats_engine)
        print("  ✓ Volume analysis aggregation complete")
    except Exception as e:
        print(f"  ⚠ Volume analysis aggregation skipped: {str(e)}")
    
    print("\n✓ Stage 4 complete (cross-security aggregation + volume analysis)")
    return True


def _load_partition_keys_from_disk(security, processed_dir):
    """Load partition keys from processed directory for a security."""
    processed_path = Path(processed_dir)
    partition_keys = []
    
    for date_dir in processed_path.iterdir():
        if date_dir.is_dir() and not date_dir.name.startswith('.'):
            for orderbook_dir in date_dir.iterdir():
                if orderbook_dir.is_dir() and not orderbook_dir.name.startswith('.'):
                    if int(orderbook_dir.name) == security.orderbookid:
                        partition_key = f"{date_dir.name}/{orderbook_dir.name}"
                        partition_keys.append(partition_key)
    
    return partition_keys


def _execute_stage_1(sec_info, stages):
    """Execute Stage 1 for a security if needed."""
    if stages is None or 1 in stages:
        data = extract_and_prepare_data(sec_info['input_files'])
        if data is None:
            return None, []
        return data, data['partition_keys']
    return None, []


def _execute_stage_2(data, runtime_config, stages):
    """Execute Stage 2 for a security if needed."""
    if stages is None or 2 in stages:
        if data is None:
            print(f"\n✗ Stage 2 requires Stage 1 data. Please run Stage 1 first or run both together.")
            return
        
        run_simulations_and_lob(data, runtime_config['enable_parallel'])


def _execute_stage_3(security, data, runtime_config, stages):
    """Execute Stage 3 for a security if needed."""
    if stages is None or 3 in stages:
        if data is None:
            partition_keys = _load_partition_keys_from_disk(security, config.PROCESSED_DIR)
            if not partition_keys:
                print(f"✗ Stage 3 requires processed data from Stages 1-2 for OrderbookID {security.orderbookid}")
                return []
        else:
            partition_keys = data['partition_keys']
        
        run_per_security_analysis(config.PROCESSED_DIR, config.OUTPUTS_DIR, partition_keys, runtime_config['stats_engine'])
        return partition_keys
    
    return []


def _process_single_security(sec_info, runtime_config):
    """Process all stages for a single security."""
    security = sec_info['security']
    stages = runtime_config['stages']
    
    ticker_str = security.ticker.upper() if security.ticker else "Unknown"
    print(f"\n{'='*80}")
    print(f"Processing OrderbookID {security.orderbookid} ({ticker_str})")
    print(f"{'='*80}")
    
    data, partition_keys_s1 = _execute_stage_1(sec_info, stages)
    
    if data is None and (stages is None or 1 in stages):
        print(f"\n✗ Stage 1 failed for OrderbookID {security.orderbookid} - no Centre Point orders found")
        return None, []
    
    if stages is None or 1 in stages:
        print(f"\n✓ Stage 1 complete for OrderbookID {security.orderbookid}")
    
    _execute_stage_2(data, runtime_config, stages)
    
    if stages is None or 2 in stages:
        print(f"\n✓ Stage 2 complete for OrderbookID {security.orderbookid}")
    
    partition_keys_s3 = _execute_stage_3(security, data, runtime_config, stages)
    
    if stages is None or 3 in stages:
        print(f"\n✓ Stage 3 complete for OrderbookID {security.orderbookid}")
    
    # Combine partition keys from stages 1 and 3
    all_partition_keys = []
    if partition_keys_s1:
        all_partition_keys.extend(partition_keys_s1)
    if partition_keys_s3:
        all_partition_keys.extend(partition_keys_s3)
    
    return data, all_partition_keys


def execute_pipeline_stages(runtime_config):
    """Execute all pipeline stages based on runtime config."""
    stages = runtime_config['stages']
    securities = runtime_config.get('securities', [])
    
    data = None
    all_partition_keys = []
    
    for sec_info in securities:
        sec_data, sec_partition_keys = _process_single_security(sec_info, runtime_config)
        if sec_data:
            data = sec_data
        all_partition_keys.extend(sec_partition_keys)
    
    if stages is None or 4 in stages:
        run_cross_security_aggregation(runtime_config)
        print(f"\n✓ Stage 4 complete")
    
    return data, all_partition_keys
