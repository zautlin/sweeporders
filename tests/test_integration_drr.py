"""
Integration test for TradeMetricsCalculator with real DRR 2024-09-05 data

Tests the calculator with actual simulated trades and orders.
"""

import pandas as pd
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline.trade_metrics_calculator import TradeMetricsCalculator


def test_with_drr_data():
    """Test with DRR 2024-09-05 simulated trades."""
    print("\n=== Integration Test: DRR 2024-09-05 ===")
    
    data_dir = Path("data/processed/2024-09-05/110621")
    
    # Check if data exists
    orders_file = data_dir / "orders_before_matching.csv"
    sim_trades_file = data_dir / "cp_trades_simulation.csv"
    
    if not orders_file.exists():
        print(f"❌ Orders file not found: {orders_file}")
        return False
    
    if not sim_trades_file.exists():
        print(f"❌ Simulated trades file not found: {sim_trades_file}")
        return False
    
    print(f"✓ Loading data from {data_dir}")
    
    # Load data
    orders = pd.read_csv(orders_file)
    sim_trades = pd.read_csv(sim_trades_file)
    
    print(f"  Orders loaded: {len(orders):,} rows")
    print(f"  Simulated trades loaded: {len(sim_trades):,} rows")
    
    # Get sweep orders (order_type == 2048)
    SWEEP_ORDER_TYPE = 2048
    if 'order_type' in orders.columns:
        sweep_orders = orders[orders['order_type'] == SWEEP_ORDER_TYPE].copy()
        print(f"  Sweep orders: {len(sweep_orders):,}")
    else:
        print("  Warning: order_type column not found, using all orders")
        sweep_orders = orders.copy()
    
    if len(sweep_orders) == 0:
        print("❌ No sweep orders found")
        return False
    
    # Filter for aggressor rows in simulated trades
    aggressor_trades = sim_trades[sim_trades['passiveaggressive'] == 1].copy()
    print(f"  Aggressor trades: {len(aggressor_trades):,}")
    
    # Initialize calculator
    calculator = TradeMetricsCalculator()
    
    # Calculate metrics (sim_ prefix for simulated trades)
    print("\n  Calculating comprehensive metrics...")
    result = calculator.calculate_metrics(
        trades_df=sim_trades,
        orders_df=sweep_orders,
        nbbo_df=None,
        filter_orderids=None,
        role_filter='aggressor',  # Only aggressor rows
        prefix='sim_'
    )
    
    per_order_metrics = result['per_order_metrics']
    per_trade_metrics = result['per_trade_metrics']
    
    print(f"✓ Metrics calculated successfully")
    print(f"  Orders with metrics: {len(per_order_metrics):,}")
    print(f"  Enriched trades: {len(per_trade_metrics):,}")
    
    # Display summary statistics
    print("\n=== Metrics Summary ===")
    
    # Fill metrics
    print("\nFill Metrics:")
    print(f"  Average fill ratio: {per_order_metrics['sim_fill_ratio'].mean():.2%}")
    print(f"  Average num fills: {per_order_metrics['sim_num_fills'].mean():.1f}")
    print(f"  Fill status distribution:")
    for status, count in per_order_metrics['sim_fill_status'].value_counts().items():
        print(f"    {status}: {count:,} ({count/len(per_order_metrics)*100:.1f}%)")
    
    # Price metrics
    print("\nPrice Metrics:")
    print(f"  Average VWAP: {per_order_metrics['sim_vwap'].mean():.2f}")
    print(f"  Average arrival spread (bps): {per_order_metrics['sim_arrival_spread_bps'].mean():.2f}")
    print(f"  Average price improvement (bps): {per_order_metrics['sim_price_improvement_bps'].mean():.2f}")
    
    # Execution cost metrics
    print("\nExecution Cost Metrics:")
    print(f"  Average exec cost (arrival, bps): {per_order_metrics['sim_exec_cost_arrival_bps'].mean():.2f}")
    print(f"  Average exec cost (VW, bps): {per_order_metrics['sim_exec_cost_vw_bps'].mean():.2f}")
    print(f"  Average effective spread (%): {per_order_metrics['sim_effective_spread_pct'].mean():.2f}")
    
    # Timing metrics
    print("\nTiming Metrics:")
    print(f"  Average time to first fill (sec): {per_order_metrics['sim_time_to_first_fill_sec'].mean():.2f}")
    print(f"  Average execution duration (sec): {per_order_metrics['sim_execution_duration_sec'].mean():.2f}")
    print(f"  Average total duration (sec): {per_order_metrics['sim_total_duration_sec'].mean():.2f}")
    
    # Market context metrics
    print("\nMarket Context Metrics:")
    print(f"  Average market drift (bps): {per_order_metrics['sim_market_drift_bps'].mean():.2f}")
    print(f"  Average execution spread (bps): {per_order_metrics['sim_avg_execution_spread_bps'].mean():.2f}")
    print(f"  Average price volatility (bps): {per_order_metrics['sim_price_volatility_bps'].mean():.2f}")
    
    # Check data quality
    print("\n=== Data Quality Checks ===")
    
    # Check for NaN values
    nan_counts = per_order_metrics.isna().sum()
    cols_with_nans = nan_counts[nan_counts > 0]
    
    if len(cols_with_nans) > 0:
        print("  Columns with NaN values:")
        for col, count in cols_with_nans.items():
            pct = (count / len(per_order_metrics)) * 100
            print(f"    {col}: {count:,} ({pct:.1f}%)")
    else:
        print("  ✓ No NaN values found")
    
    # Check value ranges
    print("\n  Value range checks:")
    assert (per_order_metrics['sim_fill_ratio'] >= 0).all(), "Fill ratio should be >= 0"
    assert (per_order_metrics['sim_fill_ratio'] <= 1).all(), "Fill ratio should be <= 1"
    print("    ✓ Fill ratios in valid range [0, 1]")
    
    assert (per_order_metrics['sim_num_fills'] >= 0).all(), "Num fills should be >= 0"
    print("    ✓ Num fills non-negative")
    
    assert (per_order_metrics['sim_vwap'] > 0).all(), "VWAP should be positive"
    print("    ✓ VWAP values positive")
    
    assert (per_order_metrics['sim_execution_duration_sec'] >= 0).all(), "Execution duration should be >= 0"
    print("    ✓ Execution durations non-negative")
    
    # Sample a few orders to display
    print("\n=== Sample Order Metrics ===")
    sample_orders = per_order_metrics.head(3)
    
    for idx, order in sample_orders.iterrows():
        print(f"\nOrder {order['orderid']}:")
        print(f"  Fill: {order['sim_qty_filled']}/{order['sim_order_quantity']} ({order['sim_fill_ratio']:.1%})")
        print(f"  VWAP: {order['sim_vwap']:.2f}, Arrival mid: {order['sim_arrival_midpoint']:.2f}")
        print(f"  Exec cost: {order['sim_exec_cost_arrival_bps']:.2f} bps")
        print(f"  Duration: {order['sim_total_duration_sec']:.2f} sec")
    
    print("\n✓ Integration test passed!")
    return True


def main():
    """Run integration test."""
    print("=" * 70)
    print("TradeMetricsCalculator Integration Test")
    print("=" * 70)
    
    try:
        success = test_with_drr_data()
        if success:
            print("\n🎉 Integration test completed successfully!")
            return 0
        else:
            print("\n❌ Integration test failed")
            return 1
    except Exception as e:
        print(f"\n✗ Integration test error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
