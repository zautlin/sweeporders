"""
Unit tests for TradeMetricsCalculator

Tests all 5 metric groups:
- Group A: Fill metrics
- Group B: Price metrics (VWAP, arrival midpoint)
- Group C: Execution cost metrics
- Group D: Timing metrics
- Group E: Market context metrics
"""

import pytest
import pandas as pd
import numpy as np
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline.trade_metrics_calculator import TradeMetricsCalculator


@pytest.fixture
def calculator():
    """Create calculator instance."""
    return TradeMetricsCalculator()


@pytest.fixture
def sample_trades_buy():
    """Sample trades for a buy order."""
    return pd.DataFrame({
        'orderid': [1, 1, 1],
        'orderbookid': [110621, 110621, 110621],
        'tradetime': [1725494538064829640, 1725494538076428033, 1725494540000000000],
        'tradeprice': [3335, 3335, 3338],
        'quantity': [2, 268, 150],
        'passiveaggressive': [1, 1, 1],
        'nationalbidpricesnapshot': [3330, 3330, 3335],
        'nationalofferpricesnapshot': [3340, 3340, 3345]
    })


@pytest.fixture
def sample_trades_sell():
    """Sample trades for a sell order."""
    return pd.DataFrame({
        'orderid': [2, 2, 2],
        'orderbookid': [110621, 110621, 110621],
        'tradetime': [1725494538064829640, 1725494538076428033, 1725494540000000000],
        'tradeprice': [3335, 3335, 3338],
        'quantity': [2, 268, 150],
        'passiveaggressive': [1, 1, 1],
        'nationalbidpricesnapshot': [3330, 3330, 3335],
        'nationalofferpricesnapshot': [3340, 3340, 3345]
    })


@pytest.fixture
def sample_orders_buy():
    """Sample order for buy."""
    return pd.DataFrame({
        'orderid': [1],
        'timestamp': [1725494480000000000],  # 58 seconds before first trade
        'side': [1],  # Buy
        'quantity': [500],
        'price': [3340],
        'national_bid': [3330],
        'national_offer': [3340]
    })


@pytest.fixture
def sample_orders_sell():
    """Sample order for sell."""
    return pd.DataFrame({
        'orderid': [2],
        'timestamp': [1725494480000000000],
        'side': [2],  # Sell
        'quantity': [500],
        'price': [3330],
        'national_bid': [3330],
        'national_offer': [3340]
    })


class TestFillMetrics:
    """Test Group A: Fill metrics."""
    
    def test_full_fill(self, calculator, sample_orders_buy):
        """Test metrics for fully filled order."""
        trades = pd.DataFrame({
            'orderid': [1, 1],
            'tradetime': [1000, 2000],
            'tradeprice': [100, 100],
            'quantity': [300, 200]
        })
        orders = sample_orders_buy.copy()
        orders['quantity'] = 500
        
        result = calculator.calculate_metrics(trades, orders)
        metrics = result['per_order_metrics'].iloc[0]
        
        assert metrics['qty_filled'] == 500
        assert metrics['order_quantity'] == 500
        assert metrics['fill_ratio'] == 1.0
        assert metrics['fill_rate_pct'] == 100.0
        assert metrics['num_fills'] == 2
        assert metrics['avg_fill_size'] == 250.0
        assert metrics['fill_status'] == 'Fully Filled'
    
    def test_partial_fill(self, calculator, sample_orders_buy):
        """Test metrics for partially filled order."""
        trades = pd.DataFrame({
            'orderid': [1, 1],
            'tradetime': [1000, 2000],
            'tradeprice': [100, 100],
            'quantity': [100, 200]
        })
        orders = sample_orders_buy.copy()
        orders['quantity'] = 500
        
        result = calculator.calculate_metrics(trades, orders)
        metrics = result['per_order_metrics'].iloc[0]
        
        assert metrics['qty_filled'] == 300
        assert metrics['order_quantity'] == 500
        assert metrics['fill_ratio'] == 0.6
        assert metrics['fill_rate_pct'] == 60.0
        assert metrics['fill_status'] == 'Partially Filled'
    
    def test_single_fill(self, calculator, sample_orders_buy):
        """Test metrics for single fill."""
        trades = pd.DataFrame({
            'orderid': [1],
            'tradetime': [1000],
            'tradeprice': [100],
            'quantity': [500]
        })
        orders = sample_orders_buy.copy()
        orders['quantity'] = 500
        
        result = calculator.calculate_metrics(trades, orders)
        metrics = result['per_order_metrics'].iloc[0]
        
        assert metrics['num_fills'] == 1
        assert metrics['avg_fill_size'] == 500.0
        assert metrics['fill_status'] == 'Fully Filled'


class TestPriceMetrics:
    """Test Group B: Price metrics."""
    
    def test_vwap_calculation(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test VWAP calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Manual calculation: (3335*2 + 3335*268 + 3338*150) / (2+268+150)
        expected_vwap = (3335*2 + 3335*268 + 3338*150) / 420
        assert abs(metrics['vwap'] - expected_vwap) < 0.01
    
    def test_arrival_midpoint(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test arrival midpoint calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # (3330 + 3340) / 2 = 3335
        assert metrics['arrival_midpoint'] == 3335.0
        assert metrics['arrival_bid'] == 3330
        assert metrics['arrival_offer'] == 3340
        assert metrics['arrival_spread'] == 10
    
    def test_arrival_spread_bps(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test arrival spread in basis points."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # (10 / 3335) * 10000 ≈ 30 bps
        expected_bps = (10 / 3335) * 10000
        assert abs(metrics['arrival_spread_bps'] - expected_bps) < 0.1
    
    def test_price_improvement_buy(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test price improvement for buy order (positive = good)."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Buy limit = 3340, VWAP ≈ 3336 → improvement = 3340 - 3336 = 4 (saved money)
        vwap = metrics['vwap']
        limit = 3340
        expected_improvement = limit - vwap
        
        assert abs(metrics['price_improvement'] - expected_improvement) < 0.01
        assert metrics['price_improvement'] > 0  # Positive = good for buy
    
    def test_price_improvement_sell(self, calculator, sample_trades_sell, sample_orders_sell):
        """Test price improvement for sell order (positive = good)."""
        result = calculator.calculate_metrics(sample_trades_sell, sample_orders_sell)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Sell limit = 3330, VWAP ≈ 3336 → improvement = 3336 - 3330 = 6 (made more)
        vwap = metrics['vwap']
        limit = 3330
        expected_improvement = vwap - limit
        
        assert abs(metrics['price_improvement'] - expected_improvement) < 0.01
        assert metrics['price_improvement'] > 0  # Positive = good for sell


class TestExecutionCostMetrics:
    """Test Group C: Execution cost metrics."""
    
    def test_exec_cost_arrival_buy(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test execution cost vs arrival for buy order."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Buy: vwap ≈ 3336, arrival_mid = 3335
        # Cost = +1 * ((3336 - 3335) / 3335) * 10000 ≈ 3 bps (positive = paid above mid)
        vwap = metrics['vwap']
        arrival_mid = 3335.0
        expected_cost = ((vwap - arrival_mid) / arrival_mid) * 10000
        
        assert abs(metrics['exec_cost_arrival_bps'] - expected_cost) < 0.1
        assert metrics['exec_cost_arrival_bps'] > 0  # Paid above midpoint
    
    def test_exec_cost_arrival_sell(self, calculator, sample_trades_sell, sample_orders_sell):
        """Test execution cost vs arrival for sell order."""
        result = calculator.calculate_metrics(sample_trades_sell, sample_orders_sell)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Sell: vwap ≈ 3336, arrival_mid = 3335
        # Cost = -1 * ((3336 - 3335) / 3335) * 10000 ≈ -3 bps (negative = sold above mid = good)
        vwap = metrics['vwap']
        arrival_mid = 3335.0
        expected_cost = -1 * ((vwap - arrival_mid) / arrival_mid) * 10000
        
        assert abs(metrics['exec_cost_arrival_bps'] - expected_cost) < 0.1
        assert metrics['exec_cost_arrival_bps'] < 0  # Sold above midpoint (good)
    
    def test_effective_spread(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test effective spread calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # effective_spread = 2 * |vwap - arrival_mid| / arrival_spread * 100
        vwap = metrics['vwap']
        arrival_mid = 3335.0
        arrival_spread = 10.0
        expected_pct = (2 * abs(vwap - arrival_mid) / arrival_spread) * 100
        
        assert abs(metrics['effective_spread_pct'] - expected_pct) < 0.1
    
    def test_slippage_equals_exec_cost(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test that slippage equals exec_cost_arrival_bps."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        assert metrics['slippage_bps'] == metrics['exec_cost_arrival_bps']
    
    def test_total_execution_value(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test total execution value calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Sum of price * quantity for all trades
        expected_value = 3335*2 + 3335*268 + 3338*150
        assert metrics['total_execution_value'] == expected_value


class TestTimingMetrics:
    """Test Group D: Timing metrics."""
    
    def test_time_to_first_fill(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test time to first fill calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Order at 1725494480000000000, first trade at 1725494538064829640
        # Difference ≈ 58.06 seconds
        order_time = 1725494480000000000
        first_trade_time = 1725494538064829640
        expected_sec = (first_trade_time - order_time) / 1e9
        
        assert abs(metrics['time_to_first_fill_sec'] - expected_sec) < 0.01
    
    def test_execution_duration(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test execution duration (first fill to last fill)."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # First trade at 1725494538064829640, last at 1725494540000000000
        # Difference ≈ 1.94 seconds
        first_trade_time = 1725494538064829640
        last_trade_time = 1725494540000000000
        expected_sec = (last_trade_time - first_trade_time) / 1e9
        
        assert abs(metrics['execution_duration_sec'] - expected_sec) < 0.01
    
    def test_total_duration(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test total duration (order to last fill)."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Order at 1725494480000000000, last trade at 1725494540000000000
        # Difference = 60 seconds
        order_time = 1725494480000000000
        last_trade_time = 1725494540000000000
        expected_sec = (last_trade_time - order_time) / 1e9
        
        assert abs(metrics['total_duration_sec'] - expected_sec) < 0.01
    
    def test_avg_time_between_fills(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test average time between fills."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # 3 fills → 2 intervals
        # execution_duration / (num_fills - 1)
        exec_duration = metrics['execution_duration_sec']
        num_fills = 3
        expected_avg = exec_duration / (num_fills - 1)
        
        assert abs(metrics['avg_time_between_fills'] - expected_avg) < 0.01
    
    def test_single_fill_timing(self, calculator, sample_orders_buy):
        """Test timing metrics for single fill."""
        trades = pd.DataFrame({
            'orderid': [1],
            'tradetime': [1725494540000000000],
            'tradeprice': [3335],
            'quantity': [500]
        })
        
        result = calculator.calculate_metrics(trades, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        assert metrics['execution_duration_sec'] == 0.0
        assert metrics['avg_time_between_fills'] == 0.0


class TestMarketContextMetrics:
    """Test Group E: Market context metrics."""
    
    def test_market_drift(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test market drift calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # First fill mid: (3330 + 3340) / 2 = 3335
        # Last fill mid: (3335 + 3345) / 2 = 3340
        # Drift: ((3340 - 3335) / 3335) * 10000 ≈ 15 bps
        first_mid = (3330 + 3340) / 2
        last_mid = (3335 + 3345) / 2
        expected_drift = ((last_mid - first_mid) / first_mid) * 10000
        
        assert abs(metrics['market_drift_bps'] - expected_drift) < 0.1
    
    def test_first_last_fill_midpoints(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test first and last fill midpoint extraction."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        assert metrics['first_fill_midpoint'] == 3335.0
        assert metrics['last_fill_midpoint'] == 3340.0
    
    def test_avg_execution_spread(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test average execution spread calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Spreads: 10, 10, 10 (all same)
        # In bps: (10/3335)*10000, (10/3335)*10000, (10/3340)*10000
        assert metrics['avg_execution_spread_bps'] > 0
    
    def test_price_volatility(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test price volatility calculation."""
        result = calculator.calculate_metrics(sample_trades_buy, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Prices: 3335, 3335, 3338
        # Some volatility should be present
        assert metrics['price_volatility_bps'] >= 0


class TestPrefixApplication:
    """Test prefix application for simulated metrics."""
    
    def test_sim_prefix(self, calculator, sample_trades_buy, sample_orders_buy):
        """Test that sim_ prefix is applied correctly."""
        result = calculator.calculate_metrics(
            sample_trades_buy, 
            sample_orders_buy,
            prefix='sim_'
        )
        metrics = result['per_order_metrics']
        
        # Identifiers should NOT have prefix
        assert 'orderid' in metrics.columns
        assert 'orderbookid' in metrics.columns
        
        # Metrics should have prefix
        assert 'sim_vwap' in metrics.columns
        assert 'sim_exec_cost_arrival_bps' in metrics.columns
        assert 'sim_fill_ratio' in metrics.columns
        assert 'sim_time_to_first_fill_sec' in metrics.columns
        
        # Should NOT have unprefixed versions
        assert 'vwap' not in metrics.columns
        assert 'exec_cost_arrival_bps' not in metrics.columns


class TestRoleFiltering:
    """Test role filtering (aggressor/passive)."""
    
    def test_aggressor_filter(self, calculator, sample_orders_buy):
        """Test filtering for aggressor rows only."""
        trades = pd.DataFrame({
            'orderid': [1, 1, 1, 1],
            'tradetime': [1000, 1000, 2000, 2000],
            'tradeprice': [100, 100, 100, 100],
            'quantity': [50, 50, 50, 50],
            'passiveaggressive': [1, 0, 1, 0]  # 2 aggressor, 2 passive
        })
        
        result = calculator.calculate_metrics(
            trades, 
            sample_orders_buy,
            role_filter='aggressor'
        )
        
        # Should only include aggressor rows
        assert len(result['per_trade_metrics']) == 2
        assert result['per_order_metrics'].iloc[0]['qty_filled'] == 100  # 50 + 50


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_trades(self, calculator, sample_orders_buy):
        """Test with no trades."""
        trades = pd.DataFrame()
        result = calculator.calculate_metrics(trades, sample_orders_buy)
        
        assert len(result['per_order_metrics']) == 0
        assert len(result['per_trade_metrics']) == 0
    
    def test_missing_nbbo(self, calculator, sample_orders_buy):
        """Test when NBBO snapshots are missing."""
        trades = pd.DataFrame({
            'orderid': [1, 1],
            'tradetime': [1000, 2000],
            'tradeprice': [100, 100],
            'quantity': [100, 200]
        })
        
        result = calculator.calculate_metrics(trades, sample_orders_buy)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Should still calculate basic metrics
        assert metrics['qty_filled'] == 300
        assert metrics['vwap'] == 100.0
        
        # Market context metrics should be NaN
        assert pd.isna(metrics['market_drift_bps'])
    
    def test_zero_quantity_order(self, calculator, sample_orders_buy):
        """Test with zero quantity order."""
        trades = pd.DataFrame({
            'orderid': [1],
            'tradetime': [1000],
            'tradeprice': [100],
            'quantity': [100]
        })
        orders = sample_orders_buy.copy()
        orders['quantity'] = 0
        
        result = calculator.calculate_metrics(trades, orders)
        metrics = result['per_order_metrics'].iloc[0]
        
        # Should handle division by zero gracefully
        assert metrics['fill_ratio'] == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
