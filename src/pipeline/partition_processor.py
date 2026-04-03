"""Partition processing logic for pipeline steps 7-12."""

import pandas as pd
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import pipeline.data_processor as dp
import pipeline.sweep_simulator as ss
import pipeline.execution_comparison as ec
import utils.file_utils as fu
import utils.data_utils as du
from config.column_schema import col
from .trade_metrics_calculator import calculate_trade_metrics
import config.config as cfg


def process_single_partition(partition_key, processed_dir, outputs_dir, enable_trade_comparison=True):
    """Process single partition through steps 7-12 (simulation, metrics, comparison)."""
    try:
        date, security_code = partition_key.split('/')
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        
        # Load partition data
        partition_data = dp.load_partition_data(partition_key, processed_dir)
        
        if not partition_data or 'orders_before' not in partition_data:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No partition data found'
            }
        
        # Load NBBO if available
        nbbo_data = fu.load_nbbo(partition_dir)
        partition_data['nbbo'] = nbbo_data
        
        # Step 7: Simulate sweep matching
        sim_results = ss.simulate_partition(partition_key, partition_data)
        
        if not sim_results:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No simulation results'
            }
        
        # Save simulation results
        fu.save_simulation_results(sim_results, outputs_dir, partition_key)
        
        # Step 8: Calculate simulated metrics
        orders_after = partition_data.get('orders_after')
        if orders_after is not None:
            orders_with_metrics = ec.calculate_simulated_metrics(
                orders_after,
                sim_results['order_summary'],
                sim_results['simulated_trades']
            )
            fu.save_orders_with_metrics(orders_with_metrics, outputs_dir, partition_key)
        
        # Steps 11-12: Trade-level comparison
        if enable_trade_comparison:
            compare_trades_for_partition(partition_key, sim_results, processed_dir, outputs_dir)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_sweep_orders': len(sim_results['order_summary']),
            'num_matches': len(sim_results['simulated_trades']) // 2  # 2 rows per match
        }
        
    except Exception as e:
        return {
            'partition_key': partition_key,
            'status': 'error',
            'error': str(e)
        }


def compare_trades_for_partition(partition_key, sim_results, processed_dir, output_dir):
    """Compare trades for single partition (Steps 11-12)."""
    partition_dir = fu.get_partition_dir(processed_dir, partition_key)
    
    # Load trades data
    trades_df = fu.load_trades_matched(partition_dir)
    if trades_df is None or len(trades_df) == 0:
        return
    
    # Load orders to identify sweep orders
    orders_before = fu.load_orders_before(partition_dir)
    if orders_before is None:
        return
    
    # No normalization needed - Stage 1 already normalized column names
    
    # Get sweep order IDs
    sweep_orderids = du.get_sweep_orderids(orders_before)
    
    if len(sweep_orderids) == 0:
        return
    
    # Filter trades to only those involving sweep orders
    sweep_trades = trades_df[trades_df[col.common.orderid].isin(sweep_orderids)].copy()
    
    if len(sweep_trades) == 0:
        return
    
    # Aggregate simulated trades (pass orders_before for arrival NBBO)
    sim_aggregated = ec._aggregate_simulated_trades_per_order(
        sim_results['simulated_trades'],
        sim_results['order_summary'],
        orders_before  # Include arrival NBBO for simulated metrics
    )
    
    # Calculate real trade metrics
    from .trade_metrics_calculator import calculate_trade_metrics
    real_metrics_result = calculate_trade_metrics(
        trades_df=sweep_trades,
        orders_df=orders_before,
        filter_orderids=list(sweep_orderids),
        role_filter=None,
        prefix=''
    )
    real_order_metrics = real_metrics_result['per_order_metrics']
    
    # Compare
    comparison = ec._compare_order_level_trades(real_order_metrics, sim_aggregated)
    accuracy_summary = ec._calculate_trade_accuracy_summary(comparison)
    
    # Save comparison reports
    fu.save_trade_comparison(comparison, accuracy_summary, output_dir, partition_key)
    
    # Save full metrics (36 metrics per order) for Stage 3 to load
    # This avoids recalculating the same metrics in Stage 3
    fu.save_trade_metrics(real_order_metrics, sim_aggregated, output_dir, partition_key)


def process_partitions_parallel(partition_keys, processed_dir, outputs_dir, max_workers):
    """Process multiple partitions in parallel."""
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


def _inject_lit_orders(partition_data, partition_key):
    """
    If resting phase + lit resting are both enabled, load the raw orders file
    for the security (all order types including 0 and 2) and attach it to
    partition_data['lit_orders_raw'].

    The orderbookid for this partition is derived from partition_key
    (format: 'date/orderbookid').  Raw files are discovered via cfg.RAW_FOLDERS.
    Does nothing if SIMULATE_RESTING_PHASE or SIMULATE_LIT_RESTING are False.
    """
    if not (cfg.SIMULATE_RESTING_PHASE and cfg.SIMULATE_LIT_RESTING):
        return

    if partition_data.get('lit_orders_raw') is not None:
        return  # already loaded

    try:
        _, orderbookid_str = partition_key.split('/')
        orderbookid = int(orderbookid_str)

        # Find the raw orders file that contains this orderbookid
        raw_orders_dir = Path(cfg.RAW_FOLDERS['orders'])
        raw_files = list(raw_orders_dir.glob('*_orders.csv'))
        if not raw_files:
            return

        # Prefer the most recently modified file (usually the active dataset)
        raw_file = sorted(raw_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        lit_df = pd.read_csv(raw_file, usecols=[
            'order_id', 'timestamp', 'security_code', 'exchangeordertype',
            'side', 'price', 'quantity', 'leavesquantity', 'orderstatus',
            'participantid', 'crossingkey', 'changereason', 'ordertype',
            'sequence', 'national_bid', 'national_offer',
        ], dtype={'security_code': int})

        # Normalise column names to match processed schema
        lit_df = lit_df.rename(columns={
            'order_id': 'orderid',
            'security_code': 'orderbookid',
            'leavesquantity': 'leavesquantity',
        })

        # Filter to this security
        lit_df = lit_df[lit_df['orderbookid'] == orderbookid].copy()

        # Keep only lit order types [0, 2]
        lit_df = lit_df[lit_df['exchangeordertype'].isin([0, 2])].copy()

        if len(lit_df) > 0:
            partition_data['lit_orders_raw'] = lit_df
            print(f"    Loaded {len(lit_df)} lit orders for resting phase (orderbookid={orderbookid})")
    except Exception as e:
        print(f"    Warning: could not load lit orders for resting phase: {e}")


def simulate_partition_step(partition_key, partition_data, nbbo_data, output_dir):
    """Step 7: Simulate sweep matching for single partition."""
    partition_data['nbbo'] = nbbo_data
    _inject_lit_orders(partition_data, partition_key)

    sim_results = ss.simulate_partition(partition_key, partition_data)

    if not sim_results:
        return None

    fu.save_simulation_results(sim_results, output_dir, partition_key)

    if cfg.SIMULATE_RESTING_PHASE and 'resting_trades' in sim_results:
        fu.save_resting_simulation_results(sim_results, output_dir, partition_key)

    return sim_results


def calculate_metrics_step(partition_key, sim_results, orders_after, output_dir):
    """Step 8: Calculate simulated metrics for single partition."""
    if orders_after is None or sim_results is None:
        return None
    
    orders_with_metrics = ec.calculate_simulated_metrics(
        orders_after,
        sim_results['order_summary'],
        sim_results['simulated_trades']
    )
    
    fu.save_orders_with_metrics(orders_with_metrics, output_dir, partition_key)
    
    return orders_with_metrics


def simulate_sweep_matching_sequential(orders_by_partition, order_states_by_partition,
                                       last_execution_by_partition, nbbo_by_partition, output_dir,
                                       reference_results=None):
    """Step 7: Simulate sweep matching for all partitions (sequential processing)."""
    print("\n[7/11] Simulating sweep matching...")

    simulation_results_by_partition = {}

    for partition_key in orders_by_partition.keys():
        if cfg.PROCESSING_MODE == 'memory':
            # Build partition_data entirely from in-memory dicts — no disk reads.
            states = order_states_by_partition.get(partition_key, {})
            if not states or 'before' not in states:
                continue
            date = partition_key.split('/')[0]
            ref = reference_results or {}
            partition_data = {
                'orders_before':  states['before'],
                'orders_after':   states['after'],
                'last_execution': last_execution_by_partition.get(partition_key, pd.DataFrame(
                    columns=['orderid', 'first_execution_time', 'last_execution_time'])),
                'nbbo':           nbbo_by_partition.get(partition_key),
                'session':        ref.get('session', {}).get(date, pd.DataFrame()),
                'reference':      ref.get('reference', {}).get(date, pd.DataFrame()),
                'participants':   ref.get('participants', {}).get(date, pd.DataFrame()),
            }
        else:
            # Original file-based path: load from data/processed/
            partition_data = dp.load_partition_data(
                partition_key,
                Path(output_dir).parent / 'processed'
            )

            if not partition_data or 'orders_before' not in partition_data:
                continue

            # Override with in-memory datasets (they are fresher than what's on disk)
            if partition_key in order_states_by_partition:
                partition_data['orders_before'] = order_states_by_partition[partition_key]['before']
                partition_data['orders_after'] = order_states_by_partition[partition_key]['after']

            if partition_key in last_execution_by_partition:
                partition_data['last_execution'] = last_execution_by_partition[partition_key]

            partition_data['nbbo'] = nbbo_by_partition.get(partition_key)

        # Load raw lit orders for resting phase if needed
        _inject_lit_orders(partition_data, partition_key)

        # Run simulation
        sim_results = ss.simulate_partition(partition_key, partition_data)

        if not sim_results:
            continue

        # Save simulation results
        fu.save_simulation_results(sim_results, output_dir, partition_key)

        if cfg.SIMULATE_RESTING_PHASE and 'resting_trades' in sim_results:
            fu.save_resting_simulation_results(sim_results, output_dir, partition_key)

        simulation_results_by_partition[partition_key] = {
            'order_summary': sim_results['order_summary'],
            'simulated_trades': sim_results['simulated_trades'],
        }
    
    print(f"   Completed sweep simulation for {len(simulation_results_by_partition)} partitions")
    return simulation_results_by_partition


def calculate_simulated_metrics_sequential(orders_by_partition, simulation_results_by_partition,
                                           processed_dir, output_dir, order_states=None):
    """Step 8: Calculate simulated metrics for all partitions (sequential processing)."""
    print("\n[8/11] Calculating simulated metrics...")

    orders_with_sim_metrics_by_partition = {}

    for partition_key, sim_results in simulation_results_by_partition.items():
        if cfg.PROCESSING_MODE == 'memory' and order_states and partition_key in order_states:
            orders_after = order_states[partition_key]['after']
        else:
            partition_dir = fu.get_partition_dir(processed_dir, partition_key)
            orders_after = fu.load_orders_after(partition_dir)

        if orders_after is None:
            continue
        
        # Calculate simulated metrics
        orders_with_metrics = ec.calculate_simulated_metrics(
            orders_after,
            sim_results['order_summary'],
            sim_results['simulated_trades']
        )
        
        # Save orders with simulated metrics
        fu.save_orders_with_metrics(orders_with_metrics, output_dir, partition_key)
        
        orders_with_sim_metrics_by_partition[partition_key] = orders_with_metrics
    
    print(f"   Calculated metrics for {len(orders_with_sim_metrics_by_partition)} partitions")
    return orders_with_sim_metrics_by_partition


def process_partitions_parallel_stage_2(partition_keys, processed_dir, outputs_dir, max_workers):
    """Process partitions in parallel for Stage 2 (simulation only)."""
    print(f"\nProcessing {len(partition_keys)} partitions with {max_workers} workers...")
    
    partition_results = {}
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit simulation jobs
        futures = {
            executor.submit(
                _process_partition_simulation_only,
                partition_key,
                processed_dir,
                outputs_dir
            ): partition_key
            for partition_key in partition_keys
        }
        
        # Collect results
        completed = 0
        for future in as_completed(futures):
            partition_key = futures[future]
            completed += 1
            
            try:
                result = future.result()
                partition_results[partition_key] = result
                
                if result.get('status') == 'success':
                    print(f"  [{completed}/{len(partition_keys)}] ✓ {partition_key}: "
                          f"{result.get('num_sweep_orders', 0):,} sweep orders, "
                          f"{result.get('num_matches', 0):,} matches")
                elif result.get('status') == 'skipped':
                    print(f"  [{completed}/{len(partition_keys)}] ⊘ {partition_key}: "
                          f"Skipped - {result.get('reason', 'Unknown')}")
                else:
                    print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: "
                          f"ERROR - {result.get('error', 'Unknown')}")
            except Exception as e:
                print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: EXCEPTION - {str(e)}")
                partition_results[partition_key] = {
                    'partition_key': partition_key,
                    'status': 'exception',
                    'error': str(e)
                }
    
    _print_processing_summary(partition_results, partition_keys)
    return partition_results


def _process_partition_simulation_only(partition_key, processed_dir, outputs_dir):
    """Process single partition for Stage 2: simulation only."""
    try:
        partition_data = dp.load_partition_data(partition_key, processed_dir)
        
        if not partition_data or 'orders_before' not in partition_data:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No partition data found'
            }
        
        # Load NBBO
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        nbbo_data = fu.load_nbbo(partition_dir)
        partition_data['nbbo'] = nbbo_data
        
        # Step 7: Simulate sweep matching
        sim_results = ss.simulate_partition(partition_key, partition_data)
        
        if not sim_results:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No simulation results'
            }
        
        # Save simulation results
        fu.save_simulation_results(sim_results, outputs_dir, partition_key)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_sweep_orders': len(sim_results['order_summary']),
            'num_matches': len(sim_results['simulated_trades']) // 2
        }
        
    except Exception as e:
        return {
            'partition_key': partition_key,
            'status': 'error',
            'error': str(e)
        }


def process_partitions_parallel_stage_3(partition_keys, processed_dir, outputs_dir, max_workers):
    """Process partitions in parallel for Stage 3 (calculate both real and simulated metrics)."""
    print(f"\nProcessing {len(partition_keys)} partitions with {max_workers} workers...")
    
    partition_results = {}
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit metrics calculation jobs
        futures = {
            executor.submit(
                _process_partition_calculate_metrics,
                partition_key,
                processed_dir,
                outputs_dir
            ): partition_key
            for partition_key in partition_keys
        }
        
        # Collect results
        completed = 0
        for future in as_completed(futures):
            partition_key = futures[future]
            completed += 1
            
            try:
                result = future.result()
                partition_results[partition_key] = result
                
                if result.get('status') == 'success':
                    print(f"  [{completed}/{len(partition_keys)}] ✓ {partition_key}: "
                          f"Calculated metrics for {result.get('num_orders', 0):,} orders")
                elif result.get('status') == 'skipped':
                    print(f"  [{completed}/{len(partition_keys)}] ⊘ {partition_key}: "
                          f"Skipped - {result.get('reason', 'Unknown')}")
                else:
                    print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: "
                          f"ERROR - {result.get('error', 'Unknown')}")
            except Exception as e:
                print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: EXCEPTION - {str(e)}")
                partition_results[partition_key] = {
                    'partition_key': partition_key,
                    'status': 'exception',
                    'error': str(e)
                }
    
    _print_processing_summary(partition_results, partition_keys)
    return partition_results


def _process_partition_calculate_metrics(partition_key, processed_dir, outputs_dir):
    """Process single partition for Stage 3: calculate real and simulated metrics."""
    try:
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        
        # Load partition data
        orders_before = fu.load_orders_before(partition_dir)
        trades_df = fu.load_trades_matched(partition_dir)
        
        if orders_before is None or trades_df is None:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'Missing orders or trades data'
            }
        
        # Get sweep order IDs
        sweep_orderids = du.get_sweep_orderids(orders_before)
        
        if len(sweep_orderids) == 0:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No sweep orders'
            }
        
        # Filter trades to sweep orders only
        sweep_trades = trades_df[trades_df[col.common.orderid].isin(sweep_orderids)].copy()
        
        # Step 8: Calculate REAL trade metrics
        real_metrics_result = calculate_trade_metrics(
            trades_df=sweep_trades,
            orders_df=orders_before,
            filter_orderids=list(sweep_orderids),
            role_filter=None,
            prefix='',
            is_simulated=False
        )
        real_order_metrics = real_metrics_result['per_order_metrics']
        
        # Step 9: Calculate SIMULATED trade metrics
        # Load simulation results
        simulated_trades = fu.load_simulation_trades(partition_dir)
        output_partition_dir = fu.get_partition_dir(outputs_dir, partition_key)
        order_summary = fu.load_simulation_order_summary(output_partition_dir)
        
        if simulated_trades is not None and order_summary is not None:
            sim_aggregated = ec._aggregate_simulated_trades_per_order(
                simulated_trades,
                order_summary,
                orders_before
            )
        else:
            sim_aggregated = None
        
        # Save metrics
        fu.save_trade_metrics(real_order_metrics, sim_aggregated, outputs_dir, partition_key)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_orders': len(real_order_metrics)
        }
        
    except Exception as e:
        return {
            'partition_key': partition_key,
            'status': 'error',
            'error': str(e)
        }


def _print_processing_summary(partition_results, partition_keys):
    """Print summary of parallel processing results."""
    successful = sum(1 for r in partition_results.values() if r.get('status') == 'success')
    failed = sum(1 for r in partition_results.values() if r.get('status') in ('error', 'exception'))
    skipped = sum(1 for r in partition_results.values() if r.get('status') == 'skipped')
    
    print(f"\nPARALLEL PROCESSING COMPLETE")
    print(f"  ✓ Successful: {successful}/{len(partition_keys)}")
    print(f"  ✗ Failed: {failed}/{len(partition_keys)}")
    print(f"  ⊘ Skipped: {skipped}/{len(partition_keys)}")
