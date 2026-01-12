"""
Comprehensive Trade Metrics Calculator for Sweep Orders

This module calculates 36 comprehensive metrics for sweep orders covering:
- Fill metrics (quantity, ratios, fill counts)
- Price metrics (VWAP, arrival prices, price improvement)
- Execution cost metrics (arrival-based, volume-weighted)
- Timing metrics (durations, time to first fill)
- Market context metrics (market drift, spread volatility)

Works with both real and simulated trades using unified logic.
All functions are pure functions - no class wrapper needed.
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict, List


def calculate_trade_metrics(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    nbbo_df: Optional[pd.DataFrame] = None,
    filter_orderids: Optional[List[int]] = None,
    role_filter: Optional[str] = None,
    prefix: str = '',
    is_simulated: bool = False
) -> Dict[str, pd.DataFrame]:
    """Calculate 36 comprehensive metrics for sweep orders across 5 groups (fill, price, cost, timing, market)."""
    # 1. Normalize schemas
    # Note: Only needed for simulated trades (securitycode → orderbookid)
    # Real trades are already normalized by Stage 1
    trades = _normalize_trade_schema(trades_df.copy())
    orders = orders_df.copy()  # No normalization needed - Stage 1 guarantees normalized schema
    
    # 2. Filter trades
    # Note: Necessary because real trades may contain non-sweep orders
    # and simulated trades have both aggressor + passive rows per match
    trades = _filter_trades(trades, filter_orderids, role_filter)
    
    if len(trades) == 0:
        return {
            'per_trade_metrics': pd.DataFrame(),
            'per_order_metrics': pd.DataFrame()
        }
    
    # 3. Enrich trades with order context
    trades_enriched = _enrich_trades_with_context(trades, orders, nbbo_df)
    
    # 4. Calculate per-trade metrics
    per_trade = _calculate_per_trade_metrics(trades_enriched)
    
    # 5. Aggregate to order level
    per_order = _aggregate_to_order_level(per_trade, orders, is_simulated)
    
    # 6. Apply prefix
    per_order = _apply_prefix(per_order, prefix)
    
    return {
        'per_trade_metrics': per_trade,
        'per_order_metrics': per_order
    }


def _normalize_trade_schema(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize simulated trade schema to match real trades (securitycode → orderbookid)."""
    if 'securitycode' in trades_df.columns and 'orderbookid' not in trades_df.columns:
        trades_df['orderbookid'] = trades_df['securitycode']
    
    return trades_df



def _filter_trades(
    trades_df: pd.DataFrame,
    filter_orderids: Optional[List[int]],
    role_filter: Optional[str]
) -> pd.DataFrame:
    """Filter trades by orderids and role (aggressor if role_filter='aggressor')."""
    filtered = trades_df
    
    # Filter by orderids
    if filter_orderids is not None:
        filtered = filtered[filtered['orderid'].isin(filter_orderids)]
    
    # Filter by role
    if role_filter == 'aggressor' and 'passiveaggressive' in filtered.columns:
        filtered = filtered[filtered['passiveaggressive'] == 1]
    
    return filtered


def _enrich_trades_with_context(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    nbbo_df: Optional[pd.DataFrame]
) -> pd.DataFrame:
    """Enrich trades with order context (arrival time, side, quantity, price) and arrival NBBO."""
    # Handle indexed DataFrame (reset if orderid is index)
    if orders_df.index.name == 'orderid':
        orders_df = orders_df.reset_index()
    
    # Prepare order context
    order_context = orders_df[['orderid', 'timestamp', 'side', 'quantity', 'price']].copy()
    order_context = order_context.rename(columns={
        'timestamp': 'order_timestamp',
        'side': 'order_side',
        'quantity': 'order_quantity',
        'price': 'order_price'
    })
    
    # Get arrival NBBO from orders
    # Both real (orders_before) and simulated (orders_after) have national_bid/national_offer
    if 'national_bid' in orders_df.columns and 'national_offer' in orders_df.columns:
        order_context['arrival_bid'] = orders_df['national_bid']
        order_context['arrival_offer'] = orders_df['national_offer']
    else:
        # Fallback: No arrival NBBO available (shouldn't happen with current pipeline)
        order_context['arrival_bid'] = np.nan
        order_context['arrival_offer'] = np.nan
    
    # Calculate arrival midpoint and spread (use existing if available)
    if 'arrival_midpoint' in orders_df.columns:
        order_context['arrival_midpoint'] = orders_df['arrival_midpoint']
    else:
        order_context['arrival_midpoint'] = (
            order_context['arrival_bid'] + order_context['arrival_offer']
        ) / 2.0
    
    if 'arrival_spread' in orders_df.columns:
        order_context['arrival_spread'] = orders_df['arrival_spread']
    else:
        order_context['arrival_spread'] = (
            order_context['arrival_offer'] - order_context['arrival_bid']
        )
    
    # Merge with trades
    enriched = trades_df.merge(order_context, on='orderid', how='left')
    
    # Calculate trade midpoint from NBBO snapshots (if available)
    if 'nationalbidpricesnapshot' in enriched.columns and 'nationalofferpricesnapshot' in enriched.columns:
        enriched['trade_midpoint'] = (
            enriched['nationalbidpricesnapshot'] + enriched['nationalofferpricesnapshot']
        ) / 2.0
        enriched['trade_spread'] = (
            enriched['nationalofferpricesnapshot'] - enriched['nationalbidpricesnapshot']
        )
    else:
        enriched['trade_midpoint'] = np.nan
        enriched['trade_spread'] = np.nan
    
    return enriched


def _calculate_per_trade_metrics(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Calculate per-trade enrichment metrics (cumulative fill, first/last fill flags, price vs limit)."""
    enriched = trades_df.sort_values(['orderid', 'tradetime']).copy()
    
    # Calculate cumulative fill
    enriched['cumulative_fill'] = enriched.groupby('orderid')['quantity'].cumsum()
    
    # Identify first and last fills
    enriched['is_first_fill'] = ~enriched.duplicated(subset=['orderid'], keep='first')
    enriched['is_last_fill'] = ~enriched.duplicated(subset=['orderid'], keep='last')
    
    # Price vs order price
    enriched['price_vs_order_price'] = enriched['tradeprice'] - enriched['order_price']
    
    return enriched


def _aggregate_to_order_level(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    is_simulated: bool = False
) -> pd.DataFrame:
    """Aggregate per-trade metrics to order level with all 36 metrics across 5 groups."""
    metrics_list = []
    
    for orderid, order_trades in trades_df.groupby('orderid'):
        # Get order context (take first row since all trades have same order context)
        order_context = order_trades.iloc[0]
        
        # Calculate all metric groups
        fill_metrics = _calculate_fill_metrics(order_trades, order_context)
        price_metrics = _calculate_price_metrics(order_trades, order_context)
        exec_cost_metrics = _calculate_execution_cost_metrics(order_trades, order_context, is_simulated)
        timing_metrics = _calculate_timing_metrics(order_trades, order_context)
        market_context_metrics = _calculate_market_context_metrics(order_trades, order_context)
        
        # Combine all metrics
        order_metrics = {
            'orderid': orderid,
            'orderbookid': order_context.get('orderbookid', np.nan),
            **fill_metrics,
            **price_metrics,
            **exec_cost_metrics,
            **timing_metrics,
            **market_context_metrics
        }
        
        metrics_list.append(order_metrics)
    
    return pd.DataFrame(metrics_list)


def _calculate_fill_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group A fill metrics (qty filled, fill ratio, num fills, avg fill size, fill status)."""
    qty_filled = int(trades['quantity'].sum())
    order_quantity = int(order_context['order_quantity'])
    num_fills = len(trades)
    
    # Calculate ratios
    fill_ratio = qty_filled / order_quantity if order_quantity > 0 else 0.0
    fill_rate_pct = fill_ratio * 100.0
    avg_fill_size = qty_filled / num_fills if num_fills > 0 else 0.0
    
    # Determine fill status
    if qty_filled == 0:
        fill_status = 'Unfilled'
    elif qty_filled >= order_quantity:
        fill_status = 'Fully Filled'
    else:
        fill_status = 'Partially Filled'
    
    return {
        'qty_filled': qty_filled,
        'order_quantity': order_quantity,
        'fill_ratio': fill_ratio,
        'fill_rate_pct': fill_rate_pct,
        'num_fills': num_fills,
        'avg_fill_size': avg_fill_size,
        'fill_status': fill_status
    }


def _calculate_price_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group B price metrics (VWAP, arrival prices, spread, limit price, price improvement)."""
    qty_filled = trades['quantity'].sum()
    
    # Calculate VWAP
    if qty_filled > 0:
        vwap = (trades['tradeprice'] * trades['quantity']).sum() / qty_filled
    else:
        vwap = 0.0
    
    # Arrival prices
    arrival_bid = float(order_context['arrival_bid']) if pd.notna(order_context['arrival_bid']) else np.nan
    arrival_offer = float(order_context['arrival_offer']) if pd.notna(order_context['arrival_offer']) else np.nan
    arrival_midpoint = float(order_context['arrival_midpoint']) if pd.notna(order_context['arrival_midpoint']) else np.nan
    
    # Arrival spread
    if pd.notna(arrival_bid) and pd.notna(arrival_offer):
        arrival_spread = arrival_offer - arrival_bid
        arrival_spread_bps = (arrival_spread / arrival_midpoint) * 10000 if arrival_midpoint > 0 else 0.0
    else:
        arrival_spread = np.nan
        arrival_spread_bps = np.nan
    
    # Limit price
    limit_price = int(order_context['order_price'])
    
    # Price improvement (positive = better)
    side = int(order_context['order_side'])
    if side == 1:  # Buy: saved money if vwap < limit
        price_improvement = limit_price - vwap
    else:  # Sell: made more if vwap > limit
        price_improvement = vwap - limit_price
    
    price_improvement_bps = (price_improvement / limit_price) * 10000 if limit_price > 0 else 0.0
    
    return {
        'vwap': vwap,
        'arrival_midpoint': arrival_midpoint,
        'arrival_bid': arrival_bid,
        'arrival_offer': arrival_offer,
        'arrival_spread': arrival_spread,
        'arrival_spread_bps': arrival_spread_bps,
        'limit_price': limit_price,
        'price_improvement': price_improvement,
        'price_improvement_bps': price_improvement_bps
    }


def _calculate_execution_cost_metrics(trades: pd.DataFrame, order_context: pd.Series, is_simulated: bool = False) -> Dict:
    """Calculate Group C execution cost metrics (arrival-based, volume-weighted, effective spread, slippage, shortfall)."""
    qty_filled = trades['quantity'].sum()
    side = int(order_context['order_side'])
    side_multiplier = 1 if side == 1 else -1  # Buy=+1, Sell=-1
    
    # Calculate VWAP
    if qty_filled > 0:
        vwap = (trades['tradeprice'] * trades['quantity']).sum() / qty_filled
    else:
        vwap = 0.0
    
    arrival_midpoint = float(order_context['arrival_midpoint']) if pd.notna(order_context['arrival_midpoint']) else np.nan
    arrival_spread = float(order_context['arrival_spread']) if pd.notna(order_context['arrival_spread']) else np.nan
    
    # Execution cost - arrival based
    # Negative = better (bought below / sold above midpoint)
    if pd.notna(arrival_midpoint) and arrival_midpoint > 0:
        exec_cost_arrival_bps = side_multiplier * ((vwap - arrival_midpoint) / arrival_midpoint) * 10000
    else:
        exec_cost_arrival_bps = np.nan
    
    # Execution cost - volume weighted (using trade-by-trade NBBO)
    # Note: For simulated trades, tradeprice = midpoint by design (see sweep_simulator.py line 444)
    # For real trades, we compare actual execution price vs trade-time NBBO midpoint
    # For simulated trades, we compare midpoint execution vs arrival midpoint (market drift)
    if is_simulated:
        # Simulated trades execute at midpoint, so compare vs arrival midpoint to show market drift
        # This shows how much the market moved from arrival to execution time
        if pd.notna(arrival_midpoint) and arrival_midpoint > 0:
            weighted_costs = []
            for _, trade in trades.iterrows():
                # trade_midpoint is the NBBO midpoint at execution time (matches tradeprice for simulated)
                trade_mid = trade.get('trade_midpoint', np.nan)
                if pd.notna(trade_mid) and trade_mid > 0:
                    # Cost = how much market moved from arrival to execution
                    trade_cost = side_multiplier * ((trade_mid - arrival_midpoint) / arrival_midpoint) * 10000
                    weighted_cost = trade_cost * trade['quantity']
                    weighted_costs.append(weighted_cost)
            
            if len(weighted_costs) > 0 and qty_filled > 0:
                exec_cost_vw_bps = sum(weighted_costs) / qty_filled
            else:
                exec_cost_vw_bps = 0.0  # No market movement
        else:
            exec_cost_vw_bps = np.nan
    else:
        # Real trades: compare actual execution price vs trade-time NBBO midpoint
        weighted_costs = []
        for _, trade in trades.iterrows():
            trade_mid = trade.get('trade_midpoint', np.nan)
            if pd.notna(trade_mid) and trade_mid > 0:
                trade_cost = side_multiplier * ((trade['tradeprice'] - trade_mid) / trade_mid) * 10000
                weighted_cost = trade_cost * trade['quantity']
                weighted_costs.append(weighted_cost)
        
        if len(weighted_costs) > 0 and qty_filled > 0:
            exec_cost_vw_bps = sum(weighted_costs) / qty_filled
        else:
            exec_cost_vw_bps = np.nan
    
    # Effective spread
    if pd.notna(arrival_midpoint) and pd.notna(arrival_spread) and arrival_spread > 0:
        effective_spread_cents = 2 * abs(vwap - arrival_midpoint)
        effective_spread_pct = (effective_spread_cents / arrival_spread) * 100
    else:
        effective_spread_pct = np.nan
    
    # Slippage (same as exec_cost_arrival_bps)
    slippage_bps = exec_cost_arrival_bps
    
    # Implementation shortfall
    if pd.notna(arrival_midpoint) and qty_filled > 0:
        actual_cost = qty_filled * vwap
        ideal_cost = qty_filled * arrival_midpoint
        implementation_shortfall_bps = ((actual_cost - ideal_cost) / ideal_cost) * 10000 if ideal_cost > 0 else 0.0
    else:
        implementation_shortfall_bps = np.nan
    
    # Total execution value
    total_execution_value = (trades['tradeprice'] * trades['quantity']).sum()
    
    return {
        'exec_cost_arrival_bps': exec_cost_arrival_bps,
        'exec_cost_vw_bps': exec_cost_vw_bps,
        'effective_spread_pct': effective_spread_pct,
        'slippage_bps': slippage_bps,
        'implementation_shortfall_bps': implementation_shortfall_bps,
        'total_execution_value': total_execution_value
    }


def _calculate_timing_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group D timing metrics (order timestamp, fill times, durations, time to first fill, avg interval)."""
    order_timestamp = int(order_context['order_timestamp']) if pd.notna(order_context['order_timestamp']) else np.nan
    first_fill_time = int(trades['tradetime'].min())
    last_fill_time = int(trades['tradetime'].max())
    num_fills = len(trades)
    qty_filled = trades['quantity'].sum()
    
    # Time to first fill (from sweep order arrival)
    if pd.notna(order_timestamp):
        time_to_first_fill_sec = (first_fill_time - order_timestamp) / 1e9
        # Handle negative times (timestamp ordering issues)
        time_to_first_fill_sec = max(0.0, time_to_first_fill_sec)
    else:
        time_to_first_fill_sec = np.nan
    
    # Execution duration (first fill to last fill)
    execution_duration_sec = (last_fill_time - first_fill_time) / 1e9
    execution_duration_sec = max(0.0, execution_duration_sec)
    
    # Total duration (sweep order to last fill)
    if pd.notna(order_timestamp):
        total_duration_sec = (last_fill_time - order_timestamp) / 1e9
        total_duration_sec = max(0.0, total_duration_sec)
    else:
        total_duration_sec = np.nan
    
    # Average time between fills
    if num_fills > 1:
        avg_time_between_fills = execution_duration_sec / (num_fills - 1)
    else:
        avg_time_between_fills = 0.0
    
    # Volume-weighted execution time
    if pd.notna(order_timestamp) and qty_filled > 0:
        weighted_time = 0.0
        for _, trade in trades.iterrows():
            time_from_arrival = (trade['tradetime'] - order_timestamp) / 1e9
            time_from_arrival = max(0.0, time_from_arrival)  # Handle negative times
            weighted_time += trade['quantity'] * time_from_arrival
        vw_exec_time_sec = weighted_time / qty_filled
    else:
        vw_exec_time_sec = np.nan
    
    return {
        'order_timestamp': order_timestamp,
        'first_fill_time': first_fill_time,
        'last_fill_time': last_fill_time,
        'time_to_first_fill_sec': time_to_first_fill_sec,
        'execution_duration_sec': execution_duration_sec,
        'total_duration_sec': total_duration_sec,
        'avg_time_between_fills': avg_time_between_fills,
        'vw_exec_time_sec': vw_exec_time_sec
    }


def _calculate_market_context_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group E market context metrics (fill midpoints, market drift, spread volatility, price volatility)."""
    # Midpoints at first and last fill
    if 'trade_midpoint' in trades.columns and trades['trade_midpoint'].notna().any():
        first_fill_midpoint = float(trades.iloc[0]['trade_midpoint']) if pd.notna(trades.iloc[0]['trade_midpoint']) else np.nan
        last_fill_midpoint = float(trades.iloc[-1]['trade_midpoint']) if pd.notna(trades.iloc[-1]['trade_midpoint']) else np.nan
        
        # Market drift
        if pd.notna(first_fill_midpoint) and pd.notna(last_fill_midpoint) and first_fill_midpoint > 0:
            market_drift_bps = ((last_fill_midpoint - first_fill_midpoint) / first_fill_midpoint) * 10000
        else:
            market_drift_bps = np.nan
    else:
        first_fill_midpoint = np.nan
        last_fill_midpoint = np.nan
        market_drift_bps = np.nan
    
    # Spread metrics
    if 'trade_spread' in trades.columns and 'trade_midpoint' in trades.columns:
        # Calculate spread in bps for each trade
        spread_bps_list = []
        for _, trade in trades.iterrows():
            if pd.notna(trade['trade_spread']) and pd.notna(trade['trade_midpoint']) and trade['trade_midpoint'] > 0:
                spread_bps = (trade['trade_spread'] / trade['trade_midpoint']) * 10000
                spread_bps_list.append(spread_bps)
        
        if len(spread_bps_list) > 0:
            avg_execution_spread_bps = float(np.mean(spread_bps_list))
            spread_volatility_bps = float(np.std(spread_bps_list)) if len(spread_bps_list) > 1 else 0.0
        else:
            avg_execution_spread_bps = np.nan
            spread_volatility_bps = np.nan
    else:
        avg_execution_spread_bps = np.nan
        spread_volatility_bps = np.nan
    
    # Price volatility
    if len(trades) > 1:
        qty_filled = trades['quantity'].sum()
        vwap = (trades['tradeprice'] * trades['quantity']).sum() / qty_filled if qty_filled > 0 else 0.0
        price_std = float(trades['tradeprice'].std())
        price_volatility_bps = (price_std / vwap) * 10000 if vwap > 0 else 0.0
    else:
        price_volatility_bps = 0.0
    
    return {
        'first_fill_midpoint': first_fill_midpoint,
        'last_fill_midpoint': last_fill_midpoint,
        'market_drift_bps': market_drift_bps,
        'avg_execution_spread_bps': avg_execution_spread_bps,
        'spread_volatility_bps': spread_volatility_bps,
        'price_volatility_bps': price_volatility_bps
    }


def _apply_prefix(metrics_df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Apply prefix to all metric columns except identifiers (orderid, orderbookid)."""
    if not prefix or len(metrics_df) == 0:
        return metrics_df
    
    # Columns that should NOT be prefixed
    identifier_columns = ['orderid', 'orderbookid']
    
    # Rename all columns except identifiers
    rename_map = {}
    for col in metrics_df.columns:
        if col not in identifier_columns:
            rename_map[col] = f"{prefix}{col}"
    
    return metrics_df.rename(columns=rename_map)
