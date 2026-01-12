"""File I/O utilities for pipeline operations."""

import pandas as pd
from pathlib import Path


def get_partition_dir(base_dir, partition_key):
    """Build partition directory path from partition key (date/security)."""
    date, security = partition_key.split('/')
    return Path(base_dir) / date / security


def safe_read_csv(filepath, required=True, compression='infer', **kwargs):
    """Read CSV with existence check and error handling."""
    filepath = Path(filepath)
    
    if not filepath.exists():
        if required:
            raise FileNotFoundError(f"Required file not found: {filepath}")
        return None
    
    try:
        return pd.read_csv(filepath, compression=compression, **kwargs)
    except Exception as e:
        raise IOError(f"Error reading {filepath}: {e}")


def safe_write_csv(df, filepath, compression=None, create_dirs=True, **kwargs):
    """Write CSV with directory creation and error handling."""
    filepath = Path(filepath)
    
    if create_dirs:
        filepath.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        df.to_csv(filepath, compression=compression, index=False, **kwargs)
    except Exception as e:
        raise IOError(f"Error writing {filepath}: {e}")


def load_orders_before(partition_dir):
    """Load orders_before_matching.csv from partition directory."""
    filepath = Path(partition_dir) / "orders_before_matching.csv"
    return safe_read_csv(filepath, required=False)


def load_orders_after(partition_dir):
    """Load orders_after_matching.csv from partition directory."""
    filepath = Path(partition_dir) / "orders_after_matching.csv"
    return safe_read_csv(filepath, required=False)


def load_trades_matched(partition_dir):
    """Load cp_trades_matched.csv.gz from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_matched.csv.gz"
    return safe_read_csv(filepath, required=False, compression='gzip')


def load_trades_aggregated(partition_dir):
    """Load cp_trades_aggregated.csv.gz from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_aggregated.csv.gz"
    return safe_read_csv(filepath, required=False, compression='gzip')


def load_last_execution(partition_dir):
    """Load last_execution_time.csv from partition directory."""
    filepath = Path(partition_dir) / "last_execution_time.csv"
    return safe_read_csv(filepath, required=False)


def load_nbbo(partition_dir):
    """Load nbbo.csv.gz from partition directory."""
    filepath = Path(partition_dir) / "nbbo.csv.gz"
    return safe_read_csv(filepath, required=False, compression='gzip')


def save_simulation_results(sim_results, output_dir, partition_key):
    """Save simulation outputs (order_summary and simulated_trades)."""
    partition_output_dir = Path(output_dir) / partition_key
    partition_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save order summary
    if 'order_summary' in sim_results and sim_results['order_summary'] is not None:
        safe_write_csv(
            sim_results['order_summary'],
            partition_output_dir / 'simulation_order_summary.csv',
            create_dirs=False
        )
    
    # Save simulated trades to processed directory
    if 'simulated_trades' in sim_results and sim_results['simulated_trades'] is not None:
        simulated_trades = sim_results['simulated_trades']
        if len(simulated_trades) > 0:
            # Save to processed directory instead of outputs
            processed_dir = Path(output_dir).parent / 'processed'
            partition_processed_dir = processed_dir / partition_key
            partition_processed_dir.mkdir(parents=True, exist_ok=True)
            
            trades_filename = 'cp_trades_simulation.csv'
            safe_write_csv(
                simulated_trades,
                partition_processed_dir / trades_filename,
                compression=None,  # Uncompressed for easier access
                create_dirs=False
            )


def save_orders_with_metrics(orders_with_metrics, output_dir, partition_key):
    """Save orders with simulated metrics."""
    partition_output_dir = Path(output_dir) / partition_key
    safe_write_csv(
        orders_with_metrics,
        partition_output_dir / 'orders_with_simulated_metrics.csv'
    )


def save_trade_comparison(comparison_df, accuracy_df, output_dir, partition_key):
    """Save trade-level comparison results."""
    partition_output_dir = Path(output_dir) / partition_key
    
    if comparison_df is not None and len(comparison_df) > 0:
        safe_write_csv(
            comparison_df,
            partition_output_dir / 'trade_level_comparison.csv'
        )
    
    if accuracy_df is not None and len(accuracy_df) > 0:
        safe_write_csv(
            accuracy_df,
            partition_output_dir / 'trade_accuracy_summary.csv'
        )


def save_trade_metrics(real_metrics_df, sim_metrics_df, output_dir, partition_key):
    """Save real and simulated trade metrics calculated in Stage 2 for Stage 3 reuse."""
    partition_output_dir = Path(output_dir) / partition_key
    
    if real_metrics_df is not None and len(real_metrics_df) > 0:
        safe_write_csv(
            real_metrics_df,
            partition_output_dir / 'real_trade_metrics.csv'
        )
    
    if sim_metrics_df is not None and len(sim_metrics_df) > 0:
        safe_write_csv(
            sim_metrics_df,
            partition_output_dir / 'simulated_trade_metrics.csv'
        )


def load_trade_metrics(output_dir, partition_key):
    """Load pre-calculated trade metrics from Stage 2 output directory."""
    partition_output_dir = Path(output_dir) / partition_key
    
    real_metrics_path = partition_output_dir / 'real_trade_metrics.csv'
    sim_metrics_path = partition_output_dir / 'simulated_trade_metrics.csv'
    
    real_metrics_df = safe_read_csv(real_metrics_path, required=False)
    sim_metrics_df = safe_read_csv(sim_metrics_path, required=False)
    
    return real_metrics_df, sim_metrics_df


def load_simulation_trades(partition_dir):
    """Load cp_trades_simulation.csv from partition processed directory."""
    filepath = Path(partition_dir) / "cp_trades_simulation.csv"
    return safe_read_csv(filepath, required=False)


def load_simulation_order_summary(partition_dir):
    """Load simulation_order_summary.csv from partition output directory."""
    filepath = Path(partition_dir) / "simulation_order_summary.csv"
    return safe_read_csv(filepath, required=False)
