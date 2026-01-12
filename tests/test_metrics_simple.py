"""
Simple test script for TradeMetricsCalculator

Run directly without pytest: python tests/test_metrics_simple.py
"""

import pandas as pd
import numpy as np
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline.trade_metrics_calculator import TradeMetricsCalculator


def test_basic_functionality():
    """Test basic calculator functionality."""
    print("\n=== Test 1: Basic Functionality ===")
    
    calculator = TradeMetricsCalculator()
    
    # Create sample data
    trades = pd.DataFrame({
        'orderid': [1, 1, 1],
        'orderbookid': [110621, 110621, 110621],
        'tradetime': [1725494538064829640, 1725494538076428033, 1725494540000000000],
        'tradeprice': [3335, 3335, 3338],
        'quantity': [2, 268, 150],
        'passiveaggressive': [1, 1, 1],
        'nationalbidpricesnapshot': [3330, 3330, 3335],
        'nationalofferpricesnapshot': [3340, 3340, 3345]
    })
    
    orders = pd.DataFrame({
        'orderid': [1],
        'timestamp': [1725494480000000000],
        'side': [1],  # Buy
        'quantity': [500],
        'price': [3340],
        'national_bid': [3330],
        'national_offer': [3340]
    })
    
    # Calculate metrics
    result = calculator.calculate_metrics(trades, orders)
    
    print(f"✓ Calculator executed successfully")
    print(f"✓ Per-trade metrics: {len(result['per_trade_metrics'])} rows")
    print(f"✓ Per-order metrics: {len(result['per_order_metrics'])} rows")
    
    # Check per-order metrics
    metrics = result['per_order_metrics'].iloc[0]
    
    print(f"\n=== Fill Metrics ===")
    print(f"  qty_filled: {metrics['qty_filled']}")
    print(f"  order_quantity: {metrics['order_quantity']}")
    print(f"  fill_ratio: {metrics['fill_ratio']:.2%}")
    print(f"  num_fills: {metrics['num_fills']}")
    print(f"  fill_status: {metrics['fill_status']}")
    
    assert metrics['qty_filled'] == 420, "qty_filled should be 420"
    assert metrics['order_quantity'] == 500, "order_quantity should be 500"
    assert abs(metrics['fill_ratio'] - 0.84) < 0.01, "fill_ratio should be 0.84"
    assert metrics['num_fills'] == 3, "num_fills should be 3"
    assert metrics['fill_status'] == 'Partially Filled', "Should be partially filled"
    
    print("\n=== Price Metrics ===")
    print(f"  vwap: {metrics['vwap']:.2f}")
    print(f"  arrival_midpoint: {metrics['arrival_midpoint']:.2f}")
    print(f"  arrival_spread: {metrics['arrival_spread']}")
    print(f"  limit_price: {metrics['limit_price']}")
    print(f"  price_improvement: {metrics['price_improvement']:.2f}")
    
    # VWAP = (3335*2 + 3335*268 + 3338*150) / 420 ≈ 3336.0
    expected_vwap = (3335*2 + 3335*268 + 3338*150) / 420
    assert abs(metrics['vwap'] - expected_vwap) < 0.01, f"VWAP should be ~{expected_vwap}"
    assert metrics['arrival_midpoint'] == 3335.0, "arrival_midpoint should be 3335"
    
    print("\n=== Execution Cost Metrics ===")
    print(f"  exec_cost_arrival_bps: {metrics['exec_cost_arrival_bps']:.2f}")
    print(f"  exec_cost_vw_bps: {metrics['exec_cost_vw_bps']:.2f}")
    print(f"  effective_spread_pct: {metrics['effective_spread_pct']:.2f}%")
    print(f"  slippage_bps: {metrics['slippage_bps']:.2f}")
    
    # Buy order: positive cost = paid above midpoint
    assert metrics['exec_cost_arrival_bps'] > 0, "Buy should have positive exec cost"
    
    print("\n=== Timing Metrics ===")
    print(f"  time_to_first_fill_sec: {metrics['time_to_first_fill_sec']:.2f}")
    print(f"  execution_duration_sec: {metrics['execution_duration_sec']:.2f}")
    print(f"  total_duration_sec: {metrics['total_duration_sec']:.2f}")
    print(f"  avg_time_between_fills: {metrics['avg_time_between_fills']:.2f}")
    
    assert metrics['time_to_first_fill_sec'] > 50, "Time to first fill should be ~58 sec"
    assert metrics['execution_duration_sec'] > 1, "Execution duration should be ~2 sec"
    
    print("\n=== Market Context Metrics ===")
    print(f"  first_fill_midpoint: {metrics['first_fill_midpoint']:.2f}")
    print(f"  last_fill_midpoint: {metrics['last_fill_midpoint']:.2f}")
    print(f"  market_drift_bps: {metrics['market_drift_bps']:.2f}")
    print(f"  price_volatility_bps: {metrics['price_volatility_bps']:.2f}")
    
    assert metrics['first_fill_midpoint'] == 3335.0, "First fill midpoint should be 3335"
    assert metrics['last_fill_midpoint'] == 3340.0, "Last fill midpoint should be 3340"
    
    print("\n✓ All assertions passed!")
    return True


def test_sell_order():
    """Test sell order metrics."""
    print("\n=== Test 2: Sell Order ===")
    
    calculator = TradeMetricsCalculator()
    
    trades = pd.DataFrame({
        'orderid': [2],
        'tradetime': [1725494540000000000],
        'tradeprice': [3335],
        'quantity': [500],
        'nationalbidpricesnapshot': [3330],
        'nationalofferpricesnapshot': [3340]
    })
    
    orders = pd.DataFrame({
        'orderid': [2],
        'timestamp': [1725494480000000000],
        'side': [2],  # Sell
        'quantity': [500],
        'price': [3330],  # Limit price
        'national_bid': [3330],
        'national_offer': [3340]
    })
    
    result = calculator.calculate_metrics(trades, orders)
    metrics = result['per_order_metrics'].iloc[0]
    
    print(f"  vwap: {metrics['vwap']:.2f}")
    print(f"  limit_price: {metrics['limit_price']}")
    print(f"  price_improvement: {metrics['price_improvement']:.2f}")
    print(f"  exec_cost_arrival_bps: {metrics['exec_cost_arrival_bps']:.2f}")
    
    # Sell at 3335, limit 3330 → improvement = 3335 - 3330 = 5 (positive = good)
    assert metrics['price_improvement'] > 0, "Sell above limit should have positive improvement"
    
    # Sell at 3335, arrival mid 3335 → exec_cost ≈ 0
    assert abs(metrics['exec_cost_arrival_bps']) < 1, "Exec cost should be ~0"
    
    print("✓ Sell order test passed!")
    return True


def test_prefix_application():
    """Test sim_ prefix application."""
    print("\n=== Test 3: Prefix Application ===")
    
    calculator = TradeMetricsCalculator()
    
    trades = pd.DataFrame({
        'orderid': [1],
        'tradetime': [1000],
        'tradeprice': [100],
        'quantity': [100],
        'passiveaggressive': [1]
    })
    
    orders = pd.DataFrame({
        'orderid': [1],
        'timestamp': [500],
        'side': [1],
        'quantity': [100],
        'price': [100],
        'national_bid': [99],
        'national_offer': [101]
    })
    
    # Calculate with sim_ prefix
    result = calculator.calculate_metrics(trades, orders, prefix='sim_')
    metrics = result['per_order_metrics']
    
    print(f"  Columns: {list(metrics.columns[:10])}...")
    
    # Check identifiers have no prefix
    assert 'orderid' in metrics.columns, "orderid should not be prefixed"
    
    # Check metrics have prefix
    assert 'sim_vwap' in metrics.columns, "vwap should have sim_ prefix"
    assert 'sim_fill_ratio' in metrics.columns, "fill_ratio should have sim_ prefix"
    assert 'sim_exec_cost_arrival_bps' in metrics.columns, "exec_cost should have sim_ prefix"
    
    # Check no unprefixed versions
    assert 'vwap' not in metrics.columns, "vwap should not exist without prefix"
    
    print("✓ Prefix application test passed!")
    return True


def test_role_filtering():
    """Test aggressor role filtering."""
    print("\n=== Test 4: Role Filtering ===")
    
    calculator = TradeMetricsCalculator()
    
    # Trades with mixed aggressor/passive rows
    trades = pd.DataFrame({
        'orderid': [1, 1, 1, 1],
        'tradetime': [1000, 1000, 2000, 2000],
        'tradeprice': [100, 100, 100, 100],
        'quantity': [50, 50, 50, 50],
        'passiveaggressive': [1, 0, 1, 0]  # 2 aggressor, 2 passive
    })
    
    orders = pd.DataFrame({
        'orderid': [1],
        'timestamp': [500],
        'side': [1],
        'quantity': [200],
        'price': [100],
        'national_bid': [99],
        'national_offer': [101]
    })
    
    # Filter for aggressor only
    result = calculator.calculate_metrics(trades, orders, role_filter='aggressor')
    
    print(f"  Total trades: 4")
    print(f"  Aggressor trades: {len(result['per_trade_metrics'])}")
    print(f"  Total quantity: {result['per_order_metrics'].iloc[0]['qty_filled']}")
    
    assert len(result['per_trade_metrics']) == 2, "Should filter to 2 aggressor trades"
    assert result['per_order_metrics'].iloc[0]['qty_filled'] == 100, "Should sum aggressor quantity"
    
    print("✓ Role filtering test passed!")
    return True


def test_edge_cases():
    """Test edge cases."""
    print("\n=== Test 5: Edge Cases ===")
    
    calculator = TradeMetricsCalculator()
    
    # Empty trades
    result = calculator.calculate_metrics(pd.DataFrame(), pd.DataFrame())
    assert len(result['per_order_metrics']) == 0, "Empty trades should return empty result"
    print("  ✓ Empty trades handled")
    
    # Single fill
    trades = pd.DataFrame({
        'orderid': [1],
        'tradetime': [1000],
        'tradeprice': [100],
        'quantity': [100]
    })
    orders = pd.DataFrame({
        'orderid': [1],
        'timestamp': [500],
        'side': [1],
        'quantity': [100],
        'price': [100],
        'national_bid': [99],
        'national_offer': [101]
    })
    
    result = calculator.calculate_metrics(trades, orders)
    metrics = result['per_order_metrics'].iloc[0]
    
    assert metrics['execution_duration_sec'] == 0.0, "Single fill should have 0 execution duration"
    assert metrics['avg_time_between_fills'] == 0.0, "Single fill should have 0 avg time between"
    print("  ✓ Single fill handled")
    
    print("✓ Edge cases test passed!")
    return True


def main():
    """Run all tests."""
    print("=" * 70)
    print("TradeMetricsCalculator Test Suite")
    print("=" * 70)
    
    tests = [
        ("Basic Functionality", test_basic_functionality),
        ("Sell Order", test_sell_order),
        ("Prefix Application", test_prefix_application),
        ("Role Filtering", test_role_filtering),
        ("Edge Cases", test_edge_cases)
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"\n✗ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 70)
    
    if failed == 0:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n❌ {failed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
