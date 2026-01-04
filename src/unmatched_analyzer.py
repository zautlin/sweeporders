"""Unmatched Orders Root Cause Analysis Module

Analyzes why sweep orders didn't match in dark pool simulation.
Investigates liquidity availability, timing issues, and order characteristics.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from scipy import stats
import json
from datetime import datetime

import file_utils as fu


# ===== PHASE 1: DATA LOADING =====

def load_unmatched_orders(partition_dir):
    """Load unmatched orders from Step 13 output."""
    filepath = Path(partition_dir) / 'stats' / 'unmatched' / 'sweep_order_unexecuted_in_dark.csv'
    df = fu.safe_read_csv(filepath, required=False)
    
    if df is None or len(df) == 0:
        print(f"  No unmatched orders found")
        return pd.DataFrame()
    
    print(f"  Loaded {len(df)} unmatched orders")
    return df


def load_all_centre_point_orders(partition_dir):
    """Load all Centre Point orders for liquidity analysis."""
    filepath = Path(partition_dir) / 'cp_orders_filtered.csv.gz'
    df = fu.safe_read_csv(filepath, required=True, compression='gzip')
    
    # Filter to Centre Point orders only
    df = df[df['exchangeordertype'] == 2048].copy()
    
    # Sort by timestamp for temporal queries
    df = df.sort_values('timestamp')
    
    print(f"  Loaded {len(df)} Centre Point orders ({df['order_id'].nunique()} unique)")
    return df


# ===== PHASE 2: LIQUIDITY AT ARRIVAL ANALYSIS =====

def analyze_liquidity_at_arrival(unmatched_order, all_orders_df):
    """
    Analyze contra-side liquidity at the moment the unmatched order arrived.
    
    Returns metrics:
    - contra_depth_at_arrival: Total quantity available on contra side
    - contra_orders_count_at_arrival: Number of contra orders
    - best_contra_price_at_arrival: Best available contra price
    - price_overlap: Whether limit prices are compatible
    - potential_fill_qty_at_arrival: Max quantity that could match immediately
    """
    orderid = unmatched_order['orderid']
    arrival_time = int(unmatched_order['order_timestamp'])
    side = int(unmatched_order['side'])
    limit_price = unmatched_order['arrival_bid'] if side == 1 else unmatched_order['arrival_offer']
    order_qty = unmatched_order['order_quantity']
    
    # Determine contra side
    contra_side = 2 if side == 1 else 1
    
    # Find all contra-side orders that were active at arrival time
    # Active = entered before arrival_time and not yet filled
    # For simplicity, we'll use orders that have timestamp <= arrival_time
    contra_orders = all_orders_df[
        (all_orders_df['side'] == contra_side) &
        (all_orders_df['timestamp'] <= arrival_time)
    ].copy()
    
    # Get unique orderids and their latest state before arrival
    contra_orders = contra_orders.sort_values('timestamp')
    contra_orders = contra_orders.drop_duplicates(subset='order_id', keep='last')
    
    # Calculate metrics
    contra_depth = contra_orders['quantity'].sum() if len(contra_orders) > 0 else 0
    contra_count = len(contra_orders)
    
    # Best contra price
    if len(contra_orders) > 0:
        # For buys (side=1), we want the lowest sell price
        # For sells (side=2), we want the highest buy price
        if contra_side == 2:  # Looking for sellers
            best_contra_price = contra_orders['price'].min()
        else:  # Looking for buyers
            best_contra_price = contra_orders['price'].max()
    else:
        best_contra_price = None
    
    # Price overlap check
    price_overlap = False
    potential_fill_qty = 0
    
    if best_contra_price is not None:
        if side == 1:  # Buy order
            # Can match if buy limit >= sell price
            price_overlap = limit_price >= best_contra_price
            if price_overlap:
                # Calculate potential fill quantity
                compatible_orders = contra_orders[contra_orders['price'] <= limit_price]
                potential_fill_qty = min(compatible_orders['quantity'].sum(), order_qty)
        else:  # Sell order
            # Can match if sell limit <= buy price
            price_overlap = limit_price <= best_contra_price
            if price_overlap:
                # Calculate potential fill quantity
                compatible_orders = contra_orders[contra_orders['price'] >= limit_price]
                potential_fill_qty = min(compatible_orders['quantity'].sum(), order_qty)
    
    return {
        'orderid': orderid,
        'contra_depth_at_arrival': contra_depth,
        'contra_orders_count_at_arrival': contra_count,
        'best_contra_price_at_arrival': best_contra_price,
        'price_overlap': price_overlap,
        'potential_fill_qty_at_arrival': potential_fill_qty
    }


# ===== PHASE 3: TEMPORAL LIQUIDITY EVOLUTION =====

def analyze_liquidity_evolution(unmatched_order, all_orders_df):
    """
    Track contra-side liquidity arrivals during order lifetime.
    
    Returns metrics:
    - time_to_lit_execution: How long order waited before lit execution
    - contra_arrival_during_lifetime: Whether contra orders arrived while live
    - contra_qty_arrived_during_lifetime: Total contra quantity that arrived
    - earliest_possible_dark_match_time: When could order have matched
    - dark_vs_lit_timing_advantage: Time difference
    """
    orderid = unmatched_order['orderid']
    arrival_time = int(unmatched_order['order_timestamp'])
    first_lit_fill_time = int(unmatched_order['real_first_trade_time'])
    side = int(unmatched_order['side'])
    limit_price = unmatched_order['arrival_bid'] if side == 1 else unmatched_order['arrival_offer']
    order_qty = unmatched_order['order_quantity']
    
    # Time to lit execution
    time_to_lit_execution = (first_lit_fill_time - arrival_time) / 1e9  # Convert to seconds
    
    # Determine contra side
    contra_side = 2 if side == 1 else 1
    
    # Find contra orders that arrived during order lifetime
    contra_arrivals = all_orders_df[
        (all_orders_df['side'] == contra_side) &
        (all_orders_df['timestamp'] > arrival_time) &
        (all_orders_df['timestamp'] <= first_lit_fill_time)
    ].copy()
    
    # Get first occurrence of each order (when it entered)
    contra_arrivals = contra_arrivals.sort_values('timestamp')
    contra_arrivals = contra_arrivals.drop_duplicates(subset='order_id', keep='first')
    
    contra_arrival_during_lifetime = len(contra_arrivals) > 0
    contra_qty_arrived_during_lifetime = contra_arrivals['quantity'].sum() if len(contra_arrivals) > 0 else 0
    
    # Find earliest possible dark match time
    earliest_possible_dark_match_time = None
    dark_vs_lit_timing_advantage = None
    
    if len(contra_arrivals) > 0:
        # Check each contra arrival for price compatibility
        for _, contra_order in contra_arrivals.iterrows():
            contra_price = contra_order['price']
            contra_time = contra_order['timestamp']
            
            # Check price compatibility
            can_match = False
            if side == 1:  # Buy order
                can_match = limit_price >= contra_price
            else:  # Sell order
                can_match = limit_price <= contra_price
            
            if can_match:
                earliest_possible_dark_match_time = int(contra_time)
                dark_vs_lit_timing_advantage = (first_lit_fill_time - contra_time) / 1e9
                break
    
    return {
        'orderid': orderid,
        'time_to_lit_execution': time_to_lit_execution,
        'contra_arrival_during_lifetime': contra_arrival_during_lifetime,
        'contra_qty_arrived_during_lifetime': contra_qty_arrived_during_lifetime,
        'earliest_possible_dark_match_time': earliest_possible_dark_match_time,
        'dark_vs_lit_timing_advantage': dark_vs_lit_timing_advantage
    }


# ===== PHASE 4: ROOT CAUSE CLASSIFICATION =====

def classify_root_cause(liquidity_at_arrival, liquidity_evolution):
    """
    Classify why the order didn't match in dark pool.
    
    Categories:
    1. NO_LIQUIDITY_AT_ALL: No contra orders at arrival or during lifetime
    2. TIMING_MISMATCH: Contra arrived after lit execution
    3. PRICE_INCOMPATIBLE: Contra existed but prices didn't overlap
    4. INSUFFICIENT_QUANTITY: Some liquidity but not enough
    5. INSTANT_LIT_EXECUTION: Order filled in lit market instantly (< 0.1s)
    """
    orderid = liquidity_at_arrival['orderid']
    
    # Check metrics
    contra_at_arrival = liquidity_at_arrival['contra_depth_at_arrival'] > 0
    contra_during_lifetime = liquidity_evolution['contra_arrival_during_lifetime']
    price_overlap = liquidity_at_arrival['price_overlap']
    potential_fill_qty = liquidity_at_arrival['potential_fill_qty_at_arrival']
    time_to_lit = liquidity_evolution['time_to_lit_execution']
    
    # Classification logic
    root_cause = None
    root_cause_detail = None
    
    if time_to_lit < 0.1:
        # Order executed instantly in lit market, no time for dark pool
        root_cause = 'INSTANT_LIT_EXECUTION'
        root_cause_detail = f'Order filled in lit market in {time_to_lit:.4f}s, no time for dark matching'
    
    elif not contra_at_arrival and not contra_during_lifetime:
        # No contra liquidity at any time
        root_cause = 'NO_LIQUIDITY_AT_ALL'
        root_cause_detail = 'No contra-side orders in dark pool at arrival or during lifetime'
    
    elif not contra_at_arrival and contra_during_lifetime:
        # Contra arrived later
        if liquidity_evolution['earliest_possible_dark_match_time'] is not None:
            timing_advantage = liquidity_evolution['dark_vs_lit_timing_advantage']
            root_cause = 'TIMING_MISMATCH'
            root_cause_detail = f'Contra orders arrived {timing_advantage:.2f}s before lit execution, but order already committed to lit'
        else:
            root_cause = 'TIMING_MISMATCH_NO_PRICE_OVERLAP'
            root_cause_detail = 'Contra orders arrived during lifetime but prices incompatible'
    
    elif contra_at_arrival and not price_overlap:
        # Contra existed but price incompatible
        root_cause = 'PRICE_INCOMPATIBLE'
        root_cause_detail = f'Contra liquidity available ({liquidity_at_arrival["contra_depth_at_arrival"]:.0f} units) but limit prices did not overlap'
    
    elif contra_at_arrival and price_overlap and potential_fill_qty == 0:
        # This shouldn't happen, but handle edge case
        root_cause = 'INSUFFICIENT_QUANTITY'
        root_cause_detail = 'Price overlap but calculated potential fill quantity is 0'
    
    elif contra_at_arrival and price_overlap and potential_fill_qty > 0:
        # Liquidity existed and could have matched - investigate why simulation didn't match
        root_cause = 'SIMULATION_MISS'
        root_cause_detail = f'Potential fill qty: {potential_fill_qty:.0f} units available at arrival, possible simulation logic gap'
    
    else:
        # Unknown case
        root_cause = 'UNKNOWN'
        root_cause_detail = 'Unable to classify root cause'
    
    return {
        'orderid': orderid,
        'root_cause': root_cause,
        'root_cause_detail': root_cause_detail
    }


# ===== PHASE 5: OUTPUT GENERATION =====

def create_output_directory(partition_dir):
    """Create stats/unmatched_analysis/ directory."""
    analysis_dir = Path(partition_dir) / 'stats' / 'unmatched_analysis'
    analysis_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n  Created output directory:")
    print(f"    {analysis_dir}")
    
    return analysis_dir


def print_root_cause_summary(root_causes_df, liquidity_analysis_df):
    """Print formatted root cause analysis summary."""
    print(f"\n{'='*90}")
    print(f"UNMATCHED ORDERS ROOT CAUSE ANALYSIS")
    print(f"{'='*90}")
    
    total = len(root_causes_df)
    print(f"Total Unmatched: {total} orders")
    print(f"")
    
    # Root cause distribution
    print(f"ROOT CAUSE DISTRIBUTION:")
    cause_counts = root_causes_df['root_cause'].value_counts()
    for cause, count in cause_counts.items():
        pct = (count / total) * 100
        print(f"  {cause:<30} {count:>4} orders ({pct:>5.1f}%)")
    
    print(f"")
    
    # Liquidity timing summary
    print(f"LIQUIDITY TIMING:")
    print(f"  Avg time to lit execution:      {liquidity_analysis_df['time_to_lit_execution'].mean():>8.2f} seconds")
    print(f"  Orders with contra during life:  {liquidity_analysis_df['contra_arrival_during_lifetime'].sum():>8} orders")
    print(f"  Avg contra qty arrived:          {liquidity_analysis_df['contra_qty_arrived_during_lifetime'].mean():>8.0f} units")
    
    print(f"{'='*90}")


def write_output_files(unmatched_df, liquidity_analysis_df, root_causes_df, analysis_dir):
    """Write all output files."""
    print(f"\n  Writing analysis files...")
    
    # 1. Liquidity analysis
    liquidity_path = analysis_dir / 'unmatched_liquidity_analysis.csv'
    liquidity_analysis_df.to_csv(liquidity_path, index=False)
    print(f"    ✓ unmatched_liquidity_analysis.csv: {len(liquidity_analysis_df)} rows")
    
    # 2. Root causes summary
    root_causes_path = analysis_dir / 'unmatched_root_causes.csv'
    root_causes_df.to_csv(root_causes_path, index=False)
    print(f"    ✓ unmatched_root_causes.csv: {len(root_causes_df)} rows")
    
    # 3. JSON summary report
    report = {
        'timestamp': datetime.now().isoformat(),
        'total_unmatched': len(unmatched_df),
        'root_cause_distribution': root_causes_df['root_cause'].value_counts().to_dict(),
        'key_findings': {
            'avg_time_to_lit_execution': float(liquidity_analysis_df['time_to_lit_execution'].mean()),
            'orders_with_no_liquidity': int((root_causes_df['root_cause'] == 'NO_LIQUIDITY_AT_ALL').sum()),
            'orders_with_timing_mismatch': int((root_causes_df['root_cause'] == 'TIMING_MISMATCH').sum()),
            'orders_with_price_incompatible': int((root_causes_df['root_cause'] == 'PRICE_INCOMPATIBLE').sum()),
            'orders_with_instant_execution': int((root_causes_df['root_cause'] == 'INSTANT_LIT_EXECUTION').sum()),
            'orders_with_simulation_miss': int((root_causes_df['root_cause'] == 'SIMULATION_MISS').sum()),
        }
    }
    
    report_path = analysis_dir / 'unmatched_analysis_report.json'
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"    ✓ unmatched_analysis_report.json")


# ===== MAIN ANALYSIS FUNCTION =====

def analyze_unmatched_orders(processed_dir, partition_keys):
    """Main function to analyze unmatched orders."""
    print("\n" + "="*80)
    print("[14/14] UNMATCHED ORDERS ROOT CAUSE ANALYSIS")
    print("="*80)
    
    for partition_key in partition_keys:
        print(f"\nAnalyzing partition: {partition_key}")
        partition_dir = Path(processed_dir) / partition_key
        
        # Phase 1: Load data
        print(f"\nPhase 1: Loading data...")
        unmatched_df = load_unmatched_orders(partition_dir)
        
        if len(unmatched_df) == 0:
            print(f"  ⏭️  Skipping partition (no unmatched orders)")
            continue
        
        all_orders_df = load_all_centre_point_orders(partition_dir)
        
        # Phase 2: Analyze liquidity at arrival
        print(f"\nPhase 2: Analyzing liquidity at arrival...")
        liquidity_at_arrival_results = []
        for _, order in unmatched_df.iterrows():
            result = analyze_liquidity_at_arrival(order, all_orders_df)
            liquidity_at_arrival_results.append(result)
        
        liquidity_at_arrival_df = pd.DataFrame(liquidity_at_arrival_results)
        print(f"  Analyzed liquidity at arrival for {len(liquidity_at_arrival_df)} orders")
        
        # Phase 3: Analyze temporal liquidity evolution
        print(f"\nPhase 3: Analyzing temporal liquidity evolution...")
        liquidity_evolution_results = []
        for _, order in unmatched_df.iterrows():
            result = analyze_liquidity_evolution(order, all_orders_df)
            liquidity_evolution_results.append(result)
        
        liquidity_evolution_df = pd.DataFrame(liquidity_evolution_results)
        print(f"  Analyzed temporal evolution for {len(liquidity_evolution_df)} orders")
        
        # Merge liquidity analysis results
        liquidity_analysis_df = pd.merge(
            liquidity_at_arrival_df,
            liquidity_evolution_df,
            on='orderid',
            how='inner'
        )
        
        # Phase 4: Classify root causes
        print(f"\nPhase 4: Classifying root causes...")
        root_cause_results = []
        for i, row_arrival in liquidity_at_arrival_df.iterrows():
            row_evolution = liquidity_evolution_df.iloc[i]
            result = classify_root_cause(row_arrival.to_dict(), row_evolution.to_dict())
            root_cause_results.append(result)
        
        root_causes_df = pd.DataFrame(root_cause_results)
        
        # Merge root causes into liquidity analysis
        liquidity_analysis_df = pd.merge(
            liquidity_analysis_df,
            root_causes_df,
            on='orderid',
            how='left'
        )
        
        print(f"  Classified root causes for {len(root_causes_df)} orders")
        
        # Phase 5: Output generation
        print(f"\nPhase 5: Generating outputs...")
        analysis_dir = create_output_directory(partition_dir)
        write_output_files(unmatched_df, liquidity_analysis_df, root_causes_df, analysis_dir)
        
        # Print summary
        print_root_cause_summary(root_causes_df, liquidity_analysis_df)
        
        print(f"\n✅ Unmatched orders analysis complete for {partition_key}")
    
    print("\n" + "="*80)
