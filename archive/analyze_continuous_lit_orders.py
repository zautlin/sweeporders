"""
Analyze orders with Continuous Lit entry and generate stats by deal source.

This script:
1. Reads orders from processed files
2. Filters for orders with exchangeordertype == 2048 (Continuous Lit, type 2048 sweep orders)
3. Gets order IDs from these filtered orders
4. Finds trades matching these order IDs
5. Filters to only include orders where ALL trades have dealsource == 1 (Continuous Lit only)
6. Generates statistics grouped by deal source
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
    
    # Filter for orders with ONLY Continuous Lit deal source (dealsource == 1)
    print("\n" + "="*80)
    print("FILTERING FOR CONTINUOUS LIT ONLY ORDERS")
    print("="*80)
    
    if len(all_trade_data) == 0:
        print("\nNo trades found for Continuous Lit orders")
        return
    
    # Check deal sources per order
    deal_sources_per_order = all_trade_data.groupby('orderid')['dealsource'].apply(lambda x: x.unique())
    
    # Find orders with multiple deal sources
    orders_with_multiple_sources = deal_sources_per_order[deal_sources_per_order.apply(len) > 1]
    
    # Find orders with only Continuous Lit (dealsource == 1)
    orders_only_continuous_lit = deal_sources_per_order[
        (deal_sources_per_order.apply(len) == 1) & 
        (deal_sources_per_order.apply(lambda x: x[0] == 1))
    ]
    
    print(f"\nOrders with multiple deal sources: {len(orders_with_multiple_sources):,}")
    if len(orders_with_multiple_sources) > 0:
        print("\nSample orders with multiple deal sources:")
        for order_id in list(orders_with_multiple_sources.index)[:5]:
            sources = all_trade_data[all_trade_data['orderid'] == order_id]['dealsourcedecoded'].unique()
            print(f"  Order {order_id}: {', '.join(sources)}")
    
    print(f"\nOrders with ONLY Continuous Lit (dealsource=1): {len(orders_only_continuous_lit):,}")
    
    # Filter trades and orders to only those with Continuous Lit only
    continuous_lit_only_order_ids = orders_only_continuous_lit.index.tolist()
    
    filtered_trades = all_trade_data[all_trade_data['orderid'].isin(continuous_lit_only_order_ids)].copy()
    filtered_orders = all_orders[all_orders['order_id'].isin(continuous_lit_only_order_ids)].copy()
    
    print(f"\nFiltered orders (Continuous Lit only): {len(filtered_orders):,}")
    print(f"Filtered trades (Continuous Lit only): {len(filtered_trades):,}")
    
    # Generate statistics by deal source (should only be dealsource=1 now)
    print("\n" + "="*80)
    print("STATISTICS BY DEAL SOURCE (CONTINUOUS LIT ONLY)")
    print("="*80)
    
    # Basic stats by deal source
    stats_by_dealsource = filtered_trades.groupby(['dealsource', 'dealsourcedecoded']).agg({
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
    
    stats_by_side = filtered_trades.groupby(['dealsource', 'dealsourcedecoded', 'sidedecoded']).agg({
        'orderid': 'count',
        'quantity': 'sum',
        'tradeprice': 'mean',
    }).round(2)
    
    stats_by_side.columns = ['num_trades', 'total_quantity', 'avg_price']
    print(stats_by_side.to_string())
    
    # Order statistics
    print("\n" + "="*80)
    print("ORDER STATISTICS (CONTINUOUS LIT ONLY)")
    print("="*80)
    
    print(f"\nTotal Continuous Lit Orders (with Continuous Lit trades only): {len(filtered_orders):,}")
    print(f"\nOrders by Side:")
    print(filtered_orders['side'].value_counts().to_string())
    
    # Additional statistics
    print("\n" + "="*80)
    print("TRADE QUANTITY STATISTICS")
    print("="*80)
    
    trades_per_order = filtered_trades.groupby('orderid').size()
    print(f"\nTrades per order - Mean: {trades_per_order.mean():.2f}, Median: {trades_per_order.median():.2f}")
    print(f"Trades per order - Min: {trades_per_order.min()}, Max: {trades_per_order.max()}")
    
    quantity_per_order = filtered_trades.groupby('orderid')['quantity'].sum()
    print(f"\nQuantity per order - Mean: {quantity_per_order.mean():.2f}, Median: {quantity_per_order.median():.2f}")
    print(f"Quantity per order - Min: {quantity_per_order.min()}, Max: {quantity_per_order.max()}")
    
    # Save results
    output_dir = Path("data/outputs/continuous_lit_analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save filtered orders (Continuous Lit only)
    filtered_orders.to_csv(output_dir / "continuous_lit_only_orders.csv", index=False)
    print(f"\nSaved filtered orders to: {output_dir / 'continuous_lit_only_orders.csv'}")
    
    # Save filtered trades (Continuous Lit only)
    filtered_trades.to_csv(output_dir / "continuous_lit_only_trades.csv", index=False)
    print(f"Saved filtered trades to: {output_dir / 'continuous_lit_only_trades.csv'}")
    
    # Save statistics
    stats_by_dealsource.to_csv(output_dir / "stats_by_dealsource_continuous_lit_only.csv")
    print(f"Saved deal source stats to: {output_dir / 'stats_by_dealsource_continuous_lit_only.csv'}")
    
    stats_by_side.to_csv(output_dir / "stats_by_side_and_dealsource_continuous_lit_only.csv")
    print(f"Saved side & deal source stats to: {output_dir / 'stats_by_side_and_dealsource_continuous_lit_only.csv'}")
    
    # Save orders with multiple deal sources for reference
    if len(orders_with_multiple_sources) > 0:
        multi_source_order_ids = orders_with_multiple_sources.index.tolist()
        multi_source_orders = all_orders[all_orders['order_id'].isin(multi_source_order_ids)].copy()
        multi_source_trades = all_trade_data[all_trade_data['orderid'].isin(multi_source_order_ids)].copy()
        
        multi_source_orders.to_csv(output_dir / "orders_with_multiple_deal_sources.csv", index=False)
        multi_source_trades.to_csv(output_dir / "trades_with_multiple_deal_sources.csv", index=False)
        print(f"\nSaved orders with multiple deal sources to: {output_dir / 'orders_with_multiple_deal_sources.csv'}")
        print(f"Saved trades with multiple deal sources to: {output_dir / 'trades_with_multiple_deal_sources.csv'}")
    
    print("\n" + "="*80)
    print("ANALYSIS COMPLETE")
    print("="*80)
    
    return {
        'orders': filtered_orders,
        'trades': filtered_trades,
        'stats_by_dealsource': stats_by_dealsource,
        'stats_by_side': stats_by_side
    }


if __name__ == "__main__":
    results = analyze_continuous_lit_orders()
