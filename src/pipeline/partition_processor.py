"""Partition processing logic for pipeline steps 7-12."""

import pandas as pd
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import pipeline.data_processor as dp
import pipeline.sweep_simulator as ss
import pipeline.metrics_generator as mg
import utils.file_utils as fu
import utils.data_utils as du
from config.column_schema import col


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
            orders_with_metrics = mg.calculate_simulated_metrics(
                orders_after,
                sim_results['order_summary'],
                sim_results['match_details']
            )
            fu.save_orders_with_metrics(orders_with_metrics, outputs_dir, partition_key)
        
        # Steps 11-12: Trade-level comparison
        if enable_trade_comparison:
            compare_trades_for_partition(partition_key, sim_results, processed_dir, outputs_dir)
        
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
    fu.save_trade_comparison(comparison, accuracy_summary, output_dir, partition_key)


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


def simulate_partition_step(partition_key, partition_data, nbbo_data, output_dir):
    """Step 7: Simulate sweep matching for single partition."""
    # Add NBBO data
    partition_data['nbbo'] = nbbo_data
    
    # Run simulation
    sim_results = ss.simulate_partition(partition_key, partition_data)
    
    if not sim_results:
        return None
    
    # Save results
    fu.save_simulation_results(sim_results, output_dir, partition_key)
    
    return sim_results


def calculate_metrics_step(partition_key, sim_results, orders_after, output_dir):
    """Step 8: Calculate simulated metrics for single partition."""
    if orders_after is None or sim_results is None:
        return None
    
    orders_with_metrics = mg.calculate_simulated_metrics(
        orders_after,
        sim_results['order_summary'],
        sim_results['match_details']
    )
    
    fu.save_orders_with_metrics(orders_with_metrics, output_dir, partition_key)
    
    return orders_with_metrics


def simulate_sweep_matching_sequential(orders_by_partition, order_states_by_partition, 
                                       last_execution_by_partition, nbbo_by_partition, output_dir):
    """Step 7: Simulate sweep matching for all partitions (sequential processing)."""
    print("\n[7/11] Simulating sweep matching...")
    
    simulation_results_by_partition = {}
    
    for partition_key in orders_by_partition.keys():
        # Load partition data
        partition_data = dp.load_partition_data(
            partition_key, 
            Path(output_dir).parent / 'processed'
        )
        
        if not partition_data or 'orders_before' not in partition_data:
            continue
        
        # Override with in-memory datasets
        if partition_key in order_states_by_partition:
            partition_data['orders_before'] = order_states_by_partition[partition_key]['before']
            partition_data['orders_after'] = order_states_by_partition[partition_key]['after']
        
        if partition_key in last_execution_by_partition:
            partition_data['last_execution'] = last_execution_by_partition[partition_key]
        
        # Add NBBO data
        partition_data['nbbo'] = nbbo_by_partition.get(partition_key)
        
        # Run simulation
        sim_results = ss.simulate_partition(partition_key, partition_data)
        
        if not sim_results:
            continue
        
        # Save simulation results
        fu.save_simulation_results(sim_results, output_dir, partition_key)
        
        simulation_results_by_partition[partition_key] = {
            'order_summary': sim_results['order_summary'],
            'match_details': sim_results['match_details']
        }
    
    print(f"   Completed sweep simulation for {len(simulation_results_by_partition)} partitions")
    return simulation_results_by_partition


def calculate_simulated_metrics_sequential(orders_by_partition, simulation_results_by_partition, 
                                           processed_dir, output_dir):
    """Step 8: Calculate simulated metrics for all partitions (sequential processing)."""
    print("\n[8/11] Calculating simulated metrics...")
    
    orders_with_sim_metrics_by_partition = {}
    
    for partition_key, sim_results in simulation_results_by_partition.items():
        # Load orders after matching
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        orders_after = fu.load_orders_after(partition_dir)
        
        if orders_after is None:
            continue
        
        # Calculate simulated metrics
        orders_with_metrics = mg.calculate_simulated_metrics(
            orders_after,
            sim_results['order_summary'],
            sim_results['match_details']
        )
        
        # Save orders with simulated metrics
        fu.save_orders_with_metrics(orders_with_metrics, output_dir, partition_key)
        
        orders_with_sim_metrics_by_partition[partition_key] = orders_with_metrics
    
    print(f"   Calculated metrics for {len(orders_with_sim_metrics_by_partition)} partitions")
    return orders_with_sim_metrics_by_partition
