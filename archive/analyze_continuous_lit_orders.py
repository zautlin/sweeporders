"""
Analyze orders with Continuous Lit entry and generate stats by deal source.

This script:
1. Reads orders from processed files
2. Filters for orders with exchangeordertype == 2048 (Continuous Lit, type 2048 sweep orders)
3. Gets order IDs from these filtered orders
4. Finds trades matching these order IDs
5. Generates statistics grouped by deal source
"""

import pandas as pd
import glob
from pathlib import Path


def analyze_continuous_lit_orders():
    """Main analysis function."""
    
    print("="*80)
    print("CONTINUOUS LIT ORDER ANALYSIS BY DEAL SOURCE")
    print("="*80)
    
    # Find all processed order files
    order_files = glob.glob("data/processed/*/*/orders_before_matching.csv")
    
    if not order_files:
        print("\nNo order files found in data/processed/")
        return
    
    print(f"\nFound {len(order_files)} order file(s)")
    
    all_continuous_lit_orders = []
    all_trades = []
    
    # Process each partition
    for order_file in order_files:
        print(f"\nProcessing: {order_file}")
        
        # Extract partition info
        parts = Path(order_file).parts
        date = parts[-3]
        security_code = parts[-2]
        
        # Read orders
        orders_df = pd.read_csv(order_file)
        print(f"  Total orders: {len(orders_df):,}")
        
        # Filter for Continuous Lit orders (exchangeordertype == 2048)
        continuous_lit_orders = orders_df[orders_df['exchangeordertype'] == 2048].copy()
        print(f"  Continuous Lit orders (type 2048): {len(continuous_lit_orders):,}")
        
        if len(continuous_lit_orders) == 0:
            print("  Skipping - no Continuous Lit orders")
            continue
        
        # Get order IDs
        order_ids = continuous_lit_orders['order_id'].unique()
        print(f"  Unique order IDs: {len(order_ids):,}")
        
        # Read trades file
        trades_file = Path(order_file).parent / "cp_trades_matched.csv.gz"
        
        if not trades_file.exists():
            print(f"  Warning: Trades file not found: {trades_file}")
            continue
        
        trades_df = pd.read_csv(trades_file)
        print(f"  Total trades: {len(trades_df):,}")
        
        # Filter trades for these order IDs
        matching_trades = trades_df[trades_df['orderid'].isin(order_ids)].copy()
        print(f"  Matching trades: {len(matching_trades):,}")
        
        # Add partition info
        continuous_lit_orders['date'] = date
        continuous_lit_orders['security_code'] = security_code
        matching_trades['date'] = date
        matching_trades['security_code'] = security_code
        
        # Collect
        all_continuous_lit_orders.append(continuous_lit_orders)
        all_trades.append(matching_trades)
    
    if not all_continuous_lit_orders:
        print("\nNo Continuous Lit orders found across all files")
        return
    
    # Combine all data
    print("\n" + "="*80)
    print("COMBINING DATA")
    print("="*80)
    
    all_orders = pd.concat(all_continuous_lit_orders, ignore_index=True)
    all_trade_data = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    
    print(f"\nTotal Continuous Lit orders: {len(all_orders):,}")
    print(f"Total matching trades: {len(all_trade_data):,}")
    
    # Generate statistics by deal source
    print("\n" + "="*80)
    print("STATISTICS BY DEAL SOURCE")
    print("="*80)
    
    if len(all_trade_data) == 0:
        print("\nNo trades found for Continuous Lit orders")
        return
    
    # Basic stats by deal source
    stats_by_dealsource = all_trade_data.groupby(['dealsource', 'dealsourcedecoded']).agg({
        'orderid': 'count',
        'quantity': 'sum',
        'tradeprice': ['mean', 'min', 'max'],
    }).round(2)
    
    stats_by_dealsource.columns = ['_'.join(col).strip('_') for col in stats_by_dealsource.columns]
    stats_by_dealsource = stats_by_dealsource.rename(columns={
        'orderid_count': 'num_trades',
        'quantity_sum': 'total_quantity',
        'tradeprice_mean': 'avg_price',
        'tradeprice_min': 'min_price',
        'tradeprice_max': 'max_price'
    })
    
    print("\nTrade Statistics by Deal Source:")
    print(stats_by_dealsource.to_string())
    
    # Stats by side and deal source
    print("\n" + "="*80)
    print("STATISTICS BY SIDE AND DEAL SOURCE")
    print("="*80)
    
    stats_by_side = all_trade_data.groupby(['dealsource', 'dealsourcedecoded', 'sidedecoded']).agg({
        'orderid': 'count',
        'quantity': 'sum',
        'tradeprice': 'mean',
    }).round(2)
    
    stats_by_side.columns = ['num_trades', 'total_quantity', 'avg_price']
    print(stats_by_side.to_string())
    
    # Order statistics
    print("\n" + "="*80)
    print("ORDER STATISTICS")
    print("="*80)
    
    print(f"\nTotal Continuous Lit Orders: {len(all_orders):,}")
    print(f"\nOrders by Side:")
    print(all_orders['side'].value_counts().to_string())
    
    print(f"\nOrders with trades: {all_orders['order_id'].isin(all_trade_data['orderid']).sum():,}")
    print(f"Orders without trades: {(~all_orders['order_id'].isin(all_trade_data['orderid'])).sum():,}")
    
    # Save results
    output_dir = Path("data/outputs/continuous_lit_analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save orders
    all_orders.to_csv(output_dir / "continuous_lit_orders.csv", index=False)
    print(f"\nSaved orders to: {output_dir / 'continuous_lit_orders.csv'}")
    
    # Save trades
    all_trade_data.to_csv(output_dir / "continuous_lit_trades.csv", index=False)
    print(f"Saved trades to: {output_dir / 'continuous_lit_trades.csv'}")
    
    # Save statistics
    stats_by_dealsource.to_csv(output_dir / "stats_by_dealsource.csv")
    print(f"Saved deal source stats to: {output_dir / 'stats_by_dealsource.csv'}")
    
    stats_by_side.to_csv(output_dir / "stats_by_side_and_dealsource.csv")
    print(f"Saved side & deal source stats to: {output_dir / 'stats_by_side_and_dealsource.csv'}")
    
    print("\n" + "="*80)
    print("ANALYSIS COMPLETE")
    print("="*80)
    
    return {
        'orders': all_orders,
        'trades': all_trade_data,
        'stats_by_dealsource': stats_by_dealsource,
        'stats_by_side': stats_by_side
    }


if __name__ == "__main__":
    results = analyze_continuous_lit_orders()
