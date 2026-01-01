"""
Phase 2: Sweep Order Classification

Classifies sweep orders (orders with trades) into 3 categories based on
their final state (at max timestamp/sequence):
1. Fully filled: leavesquantity == 0
2. Partially filled: leavesquantity > 0 AND totalmatchedquantity > 0
3. Not executed: totalmatchedquantity == 0
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import sys
from datetime import timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from config.columns import INPUT_FILES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_filtered_data(output_dir='processed_files'):
    """
    Load Step 1 filtered outputs and enrich with raw order data (for sequence).
    
    Returns:
        Tuple of (orders_df, trades_df)
    """
    logger.info("Loading Step 1 filtered data...")
    
    # Load filtered orders
    filtered_orders = pd.read_csv(f'{output_dir}/centrepoint_orders_raw.csv.gz')
    
    # Load raw orders to get sequence field
    raw_orders = pd.read_csv(INPUT_FILES['orders'])
    
    # Filter raw orders to match filtered orders (time and participant)
    aest_tz = timezone(timedelta(hours=10))
    raw_orders['timestamp_dt'] = pd.to_datetime(raw_orders['timestamp'], unit='ns', utc=True).dt.tz_convert(aest_tz)
    raw_orders['hour'] = raw_orders['timestamp_dt'].dt.hour
    
    time_filtered = raw_orders[(raw_orders['hour'] >= 10) & (raw_orders['hour'] <= 16)]
    cp_filtered = time_filtered[time_filtered['participantid'] == 69]
    
    # Merge to get sequence and other fields
    orders = filtered_orders.merge(
        cp_filtered[['order_id', 'sequence']],
        on='order_id',
        how='left'
    )
    
    logger.info(f"Enriched filtered orders with sequence field")
    
    trades = pd.read_csv(f'{output_dir}/centrepoint_trades_raw.csv.gz')
    
    return orders, trades


def get_sweep_orders(orders_df, trades_df):
    """
    Identify sweep orders (orders with at least one trade).
    
    A sweep order is an order that has been executed against (has trades).
    
    Args:
        orders_df: Filtered orders from Step 1 (enriched with sequence)
        trades_df: Filtered trades from Step 1
        
    Returns:
        DataFrame with sweep orders (all timestamps)
    """
    logger.info("Identifying sweep orders...")
    
    # Get unique order IDs that have trades
    order_ids_with_trades = list(trades_df['orderid'].unique())
    logger.info(f"  Orders with trades: {len(order_ids_with_trades)}")
    
    # Filter to sweep orders only
    sweep_orders = orders_df[orders_df['order_id'].isin(order_ids_with_trades)].copy()
    logger.info(f"  Total sweep order records (all timestamps): {len(sweep_orders)}")
    
    return sweep_orders


def get_final_order_state(sweep_orders_df):
    """
    Get the final state of each order (at max timestamp/sequence).
    
    For each order_id, keep only the record with the maximum timestamp.
    If multiple records at same max timestamp, keep the one with max sequence.
    
    Args:
        sweep_orders_df: DataFrame with multiple records per order
        
    Returns:
        DataFrame with one record per order (final state)
    """
    logger.info("Extracting final order states (max timestamp/sequence)...")
    
    # Sort by timestamp descending, then sequence descending
    sweep_orders_df = sweep_orders_df.sort_values(['timestamp', 'sequence'], ascending=[False, False])
    
    # Keep only the first (most recent) record per order_id
    final_state = sweep_orders_df.drop_duplicates(subset=['order_id'], keep='first').copy()
    
    logger.info(f"  Extracted {len(final_state)} final order states")
    
    return final_state.reset_index(drop=True)


def classify_sweep_orders(final_state_df):
    """
    Classify sweep orders into 3 groups based on final state.
    
    Group 1: Fully filled - leavesquantity == 0
    Group 2: Partially filled - leavesquantity > 0 AND totalmatchedquantity > 0
    Group 3: Not executed - totalmatchedquantity == 0
    
    Args:
        final_state_df: Orders at final state (max timestamp/sequence)
        
    Returns:
        Dictionary with classification results
    """
    logger.info("Classifying sweep orders into 3 groups...")
    
    # Group 1: Fully filled (leavesquantity == 0)
    group1 = final_state_df[final_state_df['leavesquantity'] == 0].copy()
    logger.info(f"  Group 1 (Fully Filled): {len(group1)} orders")
    
    # Group 2: Partially filled (leavesquantity > 0 AND totalmatchedquantity > 0)
    group2 = final_state_df[
        (final_state_df['leavesquantity'] > 0) & 
        (final_state_df['totalmatchedquantity'] > 0)
    ].copy()
    logger.info(f"  Group 2 (Partially Filled): {len(group2)} orders")
    
    # Group 3: Not executed (totalmatchedquantity == 0)
    group3 = final_state_df[final_state_df['totalmatchedquantity'] == 0].copy()
    logger.info(f"  Group 3 (Not Executed): {len(group3)} orders")
    
    # Add group label
    group1['sweep_group'] = 'GROUP_1_FULLY_FILLED'
    group2['sweep_group'] = 'GROUP_2_PARTIALLY_FILLED'
    group3['sweep_group'] = 'GROUP_3_NOT_EXECUTED'
    
    return {
        'group1': group1,
        'group2': group2,
        'group3': group3,
        'all_classified': pd.concat([group1, group2, group3], ignore_index=True)
    }


def validate_classification(groups_dict, final_state_df):
    """
    Validate that all orders are classified and no duplicates.
    
    Args:
        groups_dict: Classification results
        final_state_df: Original final state data
        
    Returns:
        True if validation passes
    """
    logger.info("Validating classification...")
    
    all_classified = groups_dict['all_classified']
    
    # Check total count
    if len(all_classified) != len(final_state_df):
        logger.error(f"Classification mismatch: {len(all_classified)} != {len(final_state_df)}")
        return False
    
    # Check no duplicates
    if len(all_classified['order_id'].unique()) != len(all_classified):
        logger.error("Duplicate order_ids in classification")
        return False
    
    # Check no overlap
    g1_ids = set(groups_dict['group1']['order_id'])
    g2_ids = set(groups_dict['group2']['order_id'])
    g3_ids = set(groups_dict['group3']['order_id'])
    
    if len(g1_ids & g2_ids) > 0:
        logger.error(f"Overlap between Group 1 and Group 2: {len(g1_ids & g2_ids)}")
        return False
    
    if len(g1_ids & g3_ids) > 0:
        logger.error(f"Overlap between Group 1 and Group 3: {len(g1_ids & g3_ids)}")
        return False
    
    if len(g2_ids & g3_ids) > 0:
        logger.error(f"Overlap between Group 2 and Group 3: {len(g2_ids & g3_ids)}")
        return False
    
    logger.info("✓ Classification validation PASSED")
    return True


def save_results(groups_dict, output_dir='processed_files'):
    """
    Save classification results to files.
    
    Args:
        groups_dict: Classification results
        output_dir: Output directory
    """
    logger.info("Saving classification results...")
    
    # Save each group
    for group_name, group_df in [
        ('sweep_orders_group1_fully_filled', groups_dict['group1']),
        ('sweep_orders_group2_partially_filled', groups_dict['group2']),
        ('sweep_orders_group3_not_executed', groups_dict['group3']),
    ]:
        output_path = Path(output_dir) / f'{group_name}.csv.gz'
        group_df.to_csv(output_path, compression='gzip', index=False)
        logger.info(f"  Saved {group_name}: {len(group_df)} orders")
    
    # Save all classified
    output_path = Path(output_dir) / 'sweep_orders_classified.csv.gz'
    groups_dict['all_classified'].to_csv(output_path, compression='gzip', index=False)
    logger.info(f"  Saved sweep_orders_classified: {len(groups_dict['all_classified'])} orders")


def run_step2_pipeline(output_dir='processed_files'):
    """
    Execute Step 2: Sweep order classification.
    
    Returns:
        Dictionary with classification results
    """
    logger.info("=" * 80)
    logger.info("STEP 2: SWEEP ORDER CLASSIFICATION")
    logger.info("=" * 80)
    
    Path(output_dir).mkdir(exist_ok=True)
    
    # Load filtered data from Step 1
    logger.info("\n[2.1] Loading Step 1 filtered data...")
    orders, trades = load_filtered_data(output_dir)
    logger.info(f"✓ Loaded {len(orders)} enriched orders and {len(trades)} filtered trades")
    
    # Identify sweep orders
    logger.info("\n[2.2] Identifying sweep orders...")
    sweep_orders = get_sweep_orders(orders, trades)
    logger.info(f"✓ Found {len(sweep_orders)} sweep order records")
    
    # Get final state (max timestamp/sequence per order)
    logger.info("\n[2.3] Extracting final order states...")
    final_state = get_final_order_state(sweep_orders)
    logger.info(f"✓ Extracted {len(final_state)} final order states")
    
    # Classify into 3 groups
    logger.info("\n[2.4] Classifying sweep orders...")
    groups = classify_sweep_orders(final_state)
    logger.info(f"✓ Classification complete")
    
    # Validate
    logger.info("\n[2.5] Validating classification...")
    if not validate_classification(groups, final_state):
        logger.error("Validation FAILED!")
        return None
    
    # Save results
    logger.info("\n[2.6] Saving results...")
    save_results(groups, output_dir)
    logger.info(f"✓ Results saved")
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2 SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total sweep orders: {len(final_state)}")
    logger.info(f"  Group 1 (Fully Filled): {len(groups['group1'])}")
    logger.info(f"  Group 2 (Partially Filled): {len(groups['group2'])}")
    logger.info(f"  Group 3 (Not Executed): {len(groups['group3'])}")
    logger.info("=" * 80)
    
    return groups


if __name__ == '__main__':
    results = run_step2_pipeline()
    
    if results:
        print("\n" + "=" * 80)
        print("STEP 2 OUTPUT SUMMARY")
        print("=" * 80)
        print(f"Group 1 (Fully Filled): {len(results['group1'])} orders")
        print(f"  - leavesquantity == 0")
        print(f"  - All quantity matched")
        print(f"\nGroup 2 (Partially Filled): {len(results['group2'])} orders")
        print(f"  - leavesquantity > 0")
        print(f"  - Some quantity matched")
        print(f"\nGroup 3 (Not Executed): {len(results['group3'])} orders")
        print(f"  - totalmatchedquantity == 0")
        print(f"  - No trades executed")
        print("=" * 80)
