"""
Phase 3: Dark Book Simulation Engine
Simulates three scenarios for sweep orders resting in dark book
"""

import pandas as pd
import pickle
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_dark_book(output_dir: str) -> dict:
    """Load pickled dark book."""
    with open(Path(output_dir) / 'dark_book_state.pkl', 'rb') as f:
        return pickle.load(f)


def simulate_scenario_a(scenario_a_orders: pd.DataFrame, dark_book: dict, all_orders: pd.DataFrame, output_dir: str) -> pd.DataFrame:
    """
    Scenario A: Immediately filled sweep orders stay in dark book.
    Simulate matching against resting orders in dark book.
    """
    logger.info("Scenario A: Simulating immediately filled orders in dark book...")
    
    results = []
    
    for _, order in scenario_a_orders.iterrows():
        order_id = order['order_id']
        symbol = order['security_code']
        side = order['side']  # 1=BUY, 2=SELL
        price = order['price']
        quantity = order['quantity']
        
        real_fill_ratio = order['fill_ratio']
        real_execution_price = order['avg_execution_price']
        
        # Find matching side (opposite of order side)
        opposite_side = 2 if side == 1 else 1
        
        try:
            if symbol not in dark_book or opposite_side not in dark_book[symbol]:
                results.append({
                    'order_id': order_id,
                    'symbol': symbol,
                    'side': side,
                    'real_fill_ratio': real_fill_ratio,
                    'real_execution_price': real_execution_price,
                    'simulated_fill_ratio': 0.0,
                    'simulated_execution_price': 0.0,
                    'simulated_num_matches': 0,
                    'cost_diff': 0.0
                })
                continue
            
            # Get matching prices from opposite side
            opposite_prices = dark_book[symbol][opposite_side]
            
            # Sort prices: if BUY, want lowest SELL prices; if SELL, want highest BUY prices
            if side == 1:  # BUY side, match against SELL
                matching_prices = sorted([p for p in opposite_prices.keys() if p <= price], reverse=True)
            else:  # SELL side, match against BUY
                matching_prices = sorted([p for p in opposite_prices.keys() if p >= price])
            
            # Match against orders
            remaining_qty = quantity
            matched_trades = []
            simulated_price_sum = 0
            
            for match_price in matching_prices:
                for order_data in opposite_prices[match_price]:
                    counterparty_qty = order_data['quantity']
                    match_qty = min(remaining_qty, counterparty_qty)
                    
                    matched_trades.append(order_data['order_id'])
                    simulated_price_sum += match_price * match_qty
                    remaining_qty -= match_qty
                    
                    if remaining_qty == 0:
                        break
                
                if remaining_qty == 0:
                    break
            
            # Calculate metrics
            simulated_fill_qty = quantity - remaining_qty
            simulated_fill_ratio = simulated_fill_qty / quantity if quantity > 0 else 0
            simulated_execution_price = simulated_price_sum / simulated_fill_qty if simulated_fill_qty > 0 else 0
            
            cost_diff = (simulated_fill_ratio * simulated_execution_price) - (real_fill_ratio * real_execution_price)
            
            results.append({
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'real_fill_ratio': real_fill_ratio,
                'real_execution_price': real_execution_price,
                'simulated_fill_ratio': simulated_fill_ratio,
                'simulated_execution_price': simulated_execution_price,
                'simulated_num_matches': len(matched_trades),
                'cost_diff': cost_diff
            })
        
        except Exception as e:
            logger.warning(f"Error processing order {order_id}: {e}")
            continue
    
    results_df = pd.DataFrame(results)
    output_path = Path(output_dir) / 'scenario_a_simulation_results.parquet'
    results_df.to_parquet(output_path, compression='snappy', index=False)
    logger.info(f"Saved Scenario A results: {len(results_df):,} orders")
    
    return results_df


def simulate_scenario_b(scenario_c_orders: pd.DataFrame, dark_book: dict, all_orders: pd.DataFrame, output_dir: str) -> pd.DataFrame:
    """Scenario B: Partially executed orders with residual in dark book."""
    logger.info("Scenario B: Simulating residual orders in dark book...")
    
    # Filter for only partially filled orders (not completely unfilled)
    partial_orders = scenario_c_orders[scenario_c_orders['total_quantity_filled'] > 0].copy()
    logger.info(f"Partially filled orders: {len(partial_orders):,}")
    
    results = []
    
    for _, order in partial_orders.iterrows():
        order_id = order['order_id']
        symbol = order['security_code']
        side = order['side']
        price = order['price']
        quantity = order['quantity']
        real_fill_qty = order['total_quantity_filled']
        residual_qty = quantity - real_fill_qty
        
        # Opposite side
        opposite_side = 2 if side == 1 else 1
        
        try:
            if symbol not in dark_book or opposite_side not in dark_book[symbol]:
                results.append({
                    'order_id': order_id,
                    'symbol': symbol,
                    'side': side,
                    'real_fill_qty': real_fill_qty,
                    'residual_qty': residual_qty,
                    'residual_fill_qty': 0,
                    'total_simulated_fill_qty': real_fill_qty,
                    'simulated_fill_ratio': real_fill_qty / quantity if quantity > 0 else 0,
                    'cost_diff': 0.0
                })
                continue
            
            opposite_prices = dark_book[symbol][opposite_side]
            
            if side == 1:  # BUY
                matching_prices = sorted([p for p in opposite_prices.keys() if p <= price], reverse=True)
            else:  # SELL
                matching_prices = sorted([p for p in opposite_prices.keys() if p >= price])
            
            # Match residual
            remaining_qty = residual_qty
            simulated_price_sum = 0
            
            for match_price in matching_prices:
                for order_data in opposite_prices[match_price]:
                    counterparty_qty = order_data['quantity']
                    match_qty = min(remaining_qty, counterparty_qty)
                    
                    simulated_price_sum += match_price * match_qty
                    remaining_qty -= match_qty
                    
                    if remaining_qty == 0:
                        break
                
                if remaining_qty == 0:
                    break
            
            residual_fill_qty = residual_qty - remaining_qty
            total_simulated_fill_qty = real_fill_qty + residual_fill_qty
            simulated_fill_ratio = total_simulated_fill_qty / quantity if quantity > 0 else 0
            
            cost_diff = (simulated_fill_ratio - (real_fill_qty / quantity)) * price
            
            results.append({
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'real_fill_qty': real_fill_qty,
                'residual_qty': residual_qty,
                'residual_fill_qty': residual_fill_qty,
                'total_simulated_fill_qty': total_simulated_fill_qty,
                'simulated_fill_ratio': simulated_fill_ratio,
                'cost_diff': cost_diff
            })
        
        except Exception as e:
            logger.warning(f"Error processing order {order_id}: {e}")
            continue
    
    results_df = pd.DataFrame(results)
    output_path = Path(output_dir) / 'scenario_b_simulation_results.parquet'
    results_df.to_parquet(output_path, compression='snappy', index=False)
    logger.info(f"Saved Scenario B results: {len(results_df):,} orders")
    
    return results_df


def simulate_scenario_c(scenario_c_orders: pd.DataFrame, dark_book: dict, all_orders: pd.DataFrame, output_dir: str) -> pd.DataFrame:
    """Scenario C: Completely unfilled orders resting in dark book."""
    logger.info("Scenario C: Simulating unfilled orders in dark book...")
    
    # Filter for completely unfilled orders only
    unfilled_orders = scenario_c_orders[scenario_c_orders['total_quantity_filled'] == 0].copy()
    logger.info(f"Unfilled orders: {len(unfilled_orders):,}")
    
    results = []
    
    for _, order in unfilled_orders.iterrows():
        order_id = order['order_id']
        symbol = order['security_code']
        side = order['side']
        price = order['price']
        quantity = order['quantity']
        
        # Opposite side
        opposite_side = 2 if side == 1 else 1
        
        try:
            if symbol not in dark_book or opposite_side not in dark_book[symbol]:
                results.append({
                    'order_id': order_id,
                    'symbol': symbol,
                    'side': side,
                    'real_fill_ratio': 0.0,
                    'simulated_fill_ratio': 0.0,
                    'simulated_execution_price': 0.0
                })
                continue
            
            opposite_prices = dark_book[symbol][opposite_side]
            
            if side == 1:  # BUY
                matching_prices = sorted([p for p in opposite_prices.keys() if p <= price], reverse=True)
            else:  # SELL
                matching_prices = sorted([p for p in opposite_prices.keys() if p >= price])
            
            # Match against all available orders
            remaining_qty = quantity
            simulated_price_sum = 0
            
            for match_price in matching_prices:
                for order_data in opposite_prices[match_price]:
                    counterparty_qty = order_data['quantity']
                    match_qty = min(remaining_qty, counterparty_qty)
                    
                    simulated_price_sum += match_price * match_qty
                    remaining_qty -= match_qty
                    
                    if remaining_qty == 0:
                        break
                
                if remaining_qty == 0:
                    break
            
            simulated_fill_qty = quantity - remaining_qty
            simulated_fill_ratio = simulated_fill_qty / quantity if quantity > 0 else 0
            simulated_execution_price = simulated_price_sum / simulated_fill_qty if simulated_fill_qty > 0 else 0
            
            results.append({
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'real_fill_ratio': 0.0,
                'simulated_fill_ratio': simulated_fill_ratio,
                'simulated_execution_price': simulated_execution_price
            })
        
        except Exception as e:
            logger.warning(f"Error processing order {order_id}: {e}")
            continue
    
    results_df = pd.DataFrame(results)
    output_path = Path(output_dir) / 'scenario_c_simulation_results.parquet'
    results_df.to_parquet(output_path, compression='snappy', index=False)
    logger.info(f"Saved Scenario C results: {len(results_df):,} orders")
    
    return results_df


if __name__ == '__main__':
    output_dir = 'processed_files'
    
    # Load data
    scenario_a = pd.read_parquet(Path(output_dir) / 'scenario_a_immediate_full.parquet')
    scenario_b_c = pd.read_parquet(Path(output_dir) / 'scenario_c_partial_none.parquet')
    all_orders = pd.read_parquet(Path(output_dir) / 'centrepoint_orders_raw.parquet')
    
    dark_book = load_dark_book(output_dir)
    
    # Run simulations
    sim_a = simulate_scenario_a(scenario_a, dark_book, all_orders, output_dir)
    sim_b = simulate_scenario_b(scenario_b_c, dark_book, all_orders, output_dir)
    sim_c = simulate_scenario_c(scenario_b_c, dark_book, all_orders, output_dir)
    
    print(f"\nPhase 3 Simulations complete:")
    print(f"  Scenario A results: {len(sim_a):,} orders")
    print(f"  Scenario B results: {len(sim_b):,} orders")
    print(f"  Scenario C results: {len(sim_c):,} orders")
