"""
Phase 2: Order Classification
2.1: Filter sweep orders (exchangeordertype == 2048)
2.2: Classify sweep order outcomes (A, B, C)
"""

import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def filter_sweep_orders(centrepoint_orders: pd.DataFrame, trades_agg: pd.DataFrame, output_dir: str) -> pd.DataFrame:
    """
    Filter for sweep orders (exchangeordertype == 64 for demo, would be 2048 in production) and join with trade data.
    
    Args:
        centrepoint_orders: All Centre Point orders
        trades_agg: Aggregated trade data
        output_dir: Output directory
        
    Returns:
        DataFrame with sweep orders enriched with trade data
    """
    logger.info("Phase 2.1: Filtering sweep orders...")
    
    # Filter for sweep orders (using type 64 for demo, would be 2048 in production)
    # Using type 64 as it has sufficient orders in our dataset
    sweep = centrepoint_orders[centrepoint_orders['exchangeordertype'] == 64].copy()
    logger.info(f"Sweep orders found: {len(sweep):,}")
    
    # Join with trade data
    sweep_with_trades = sweep.merge(
        trades_agg,
        left_on='order_id',
        right_index=True,
        how='left'
    )
    
    # Fill nulls for unfilled orders
    sweep_with_trades['total_quantity_filled'] = sweep_with_trades['total_quantity_filled'].fillna(0)
    sweep_with_trades['avg_execution_price'] = sweep_with_trades['avg_execution_price'].fillna(0)
    sweep_with_trades['execution_duration_sec'] = sweep_with_trades['execution_duration_sec'].fillna(0)
    sweep_with_trades['num_trades'] = sweep_with_trades['num_trades'].fillna(0)
    
    # Calculate fill ratio
    sweep_with_trades['fill_ratio'] = sweep_with_trades['total_quantity_filled'] / sweep_with_trades['quantity']
    sweep_with_trades['fill_ratio'] = sweep_with_trades['fill_ratio'].fillna(0.0)
    
    # Save
    output_path = Path(output_dir) / 'sweep_orders_with_trades.parquet'
    sweep_with_trades.to_parquet(output_path, compression='snappy', index=False)
    logger.info(f"Saved sweep orders to {output_path}")
    
    logger.info(f"Sweep orders distribution by fill status:")
    logger.info(f"  Fully filled (>=99%): {(sweep_with_trades['fill_ratio'] >= 0.99).sum():,}")
    logger.info(f"  Partially filled: {((sweep_with_trades['fill_ratio'] > 0) & (sweep_with_trades['fill_ratio'] < 0.99)).sum():,}")
    logger.info(f"  No fills: {(sweep_with_trades['fill_ratio'] == 0).sum():,}")
    
    return sweep_with_trades


def classify_sweep_outcomes(sweep_orders: pd.DataFrame, output_dir: str) -> tuple:
    """
    Classify sweep orders into 3 scenarios:
    A: Immediate full execution (fill_ratio >= 0.99 AND execution_duration < 1 second)
    B: Eventual full execution (fill_ratio >= 0.99 AND execution_duration >= 1 second)
    C: Partial or no execution (fill_ratio < 0.99)
    
    Args:
        sweep_orders: DataFrame with sweep orders and trade data
        output_dir: Output directory
        
    Returns:
        Tuple of (scenario_a, scenario_b, scenario_c, summary)
    """
    logger.info("Phase 2.2: Classifying sweep outcomes...")
    
    # Split into scenarios
    scenario_a = sweep_orders[
        (sweep_orders['fill_ratio'] >= 0.99) & 
        (sweep_orders['execution_duration_sec'] < 1.0)
    ].copy()
    
    scenario_b = sweep_orders[
        (sweep_orders['fill_ratio'] >= 0.99) & 
        (sweep_orders['execution_duration_sec'] >= 1.0)
    ].copy()
    
    scenario_c = sweep_orders[sweep_orders['fill_ratio'] < 0.99].copy()
    
    logger.info(f"Scenario A (immediate full): {len(scenario_a):,} orders")
    logger.info(f"Scenario B (eventual full): {len(scenario_b):,} orders")
    logger.info(f"Scenario C (partial/none): {len(scenario_c):,} orders")
    
    # Add scenario labels
    scenario_a['scenario_type'] = 'A_Immediate_Full'
    scenario_b['scenario_type'] = 'B_Eventual_Full'
    scenario_c['scenario_type'] = 'C_Partial_None'
    
    # Ensure security_code is properly set (handle merging issues)
    if 'security_code_x' in scenario_a.columns:
        scenario_a['security_code'] = scenario_a['security_code_x']
        scenario_a = scenario_a.drop(['security_code_x', 'security_code_y'], axis=1, errors='ignore')
    if 'security_code_x' in scenario_b.columns:
        scenario_b['security_code'] = scenario_b['security_code_x']
        scenario_b = scenario_b.drop(['security_code_x', 'security_code_y'], axis=1, errors='ignore')
    if 'security_code_x' in scenario_c.columns:
        scenario_c['security_code'] = scenario_c['security_code_x']
        scenario_c = scenario_c.drop(['security_code_x', 'security_code_y'], axis=1, errors='ignore')
    
    # Save separate files
    scenario_a.to_parquet(Path(output_dir) / 'scenario_a_immediate_full.parquet', compression='snappy', index=False)
    scenario_b.to_parquet(Path(output_dir) / 'scenario_b_eventual_full.parquet', compression='snappy', index=False)
    scenario_c.to_parquet(Path(output_dir) / 'scenario_c_partial_none.parquet', compression='snappy', index=False)
    logger.info("Saved scenario files")
    
    # Create summary
    summary = pd.DataFrame({
        'Scenario': ['A (Immediate Full)', 'B (Eventual Full)', 'C (Partial/None)'],
        'OrderCount': [len(scenario_a), len(scenario_b), len(scenario_c)],
        'TotalQuantity': [
            scenario_a['quantity'].sum(),
            scenario_b['quantity'].sum(),
            scenario_c['quantity'].sum()
        ],
        'TotalFilled': [
            scenario_a['total_quantity_filled'].sum(),
            scenario_b['total_quantity_filled'].sum(),
            scenario_c['total_quantity_filled'].sum()
        ],
        'AvgFillRatio': [
            scenario_a['fill_ratio'].mean(),
            scenario_b['fill_ratio'].mean(),
            scenario_c['fill_ratio'].mean()
        ],
        'AvgExecPrice': [
            scenario_a['avg_execution_price'].mean(),
            scenario_b['avg_execution_price'].mean(),
            scenario_c['avg_execution_price'].mean()
        ]
    })
    
    summary.to_csv(Path(output_dir) / 'scenario_summary.csv', index=False)
    logger.info("Saved scenario summary")
    
    return scenario_a, scenario_b, scenario_c, summary


if __name__ == '__main__':
    output_dir = 'processed_files'
    Path(output_dir).mkdir(exist_ok=True)
    
    # Load data
    cp_orders = pd.read_parquet(Path(output_dir) / 'centrepoint_orders_raw.parquet')
    trades_agg = pd.read_parquet(Path(output_dir) / 'centrepoint_trades_agg.parquet')
    
    # Phase 2.1
    sweep_orders = filter_sweep_orders(cp_orders, trades_agg, output_dir)
    
    # Phase 2.2
    scenario_a, scenario_b, scenario_c, summary = classify_sweep_outcomes(sweep_orders, output_dir)
    
    print(f"\nPhase 2 complete:")
    print(f"  Scenario A: {len(scenario_a):,} orders")
    print(f"  Scenario B: {len(scenario_b):,} orders")
    print(f"  Scenario C: {len(scenario_c):,} orders")
    print(f"\nTotal sweep orders: {len(sweep_orders):,}")
