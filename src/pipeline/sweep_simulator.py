"""
Sweep Simulator Module - Complete Implementation

Handles all sweep order matching simulation logic:
- Load and prepare sweep orders and incoming orders
- Simulate time-priority sweep matching algorithm
- Calculate midpoint prices from NBBO or fallback to bid/offer
- Apply mid-tick price improvement when configured
- Track sweep order utilization
- Generate match details and order summaries
- Handle order amendments and priority loss
- Use reference data for tick sizes and participant info
- Filter by order type (Limit/Market/Passive)
- Validate price limits
- Model execution delays
- Track sweep-to-sweep matches
- Session state filtering (IMPROVEMENT 1)
- Order book position tracking (IMPROVEMENT 3)
- Iceberg order support (IMPROVEMENT 4)
- Participant type analysis (IMPROVEMENT 6)
"""

import heapq
import pandas as pd
import numpy as np
from pathlib import Path
from config.column_schema import col
import config.config as cfg
from pipeline.reference_data import ReferenceDataLoader

# Constants
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}  # ALL CP types, including sweep-to-sweep
ORDER_TYPE_COLUMN = 'exchangeordertype'

# Order type constants (dd.txt p.924)
ORDERTYPE_LIMIT = 1
ORDERTYPE_MARKET = 2
ORDERTYPE_MTL = 3  # Market-to-Limit
ORDERTYPE_PASSIVE = 4

# Midtick field values (per dd.txt spec p.1045)
MIDTICK_UNDEFINED = 0
MIDTICK_YES = 1
MIDTICK_NO = 2
MIDTICK_DARK_EXEC = 3
MIDTICK_DARK_WITH_MIDTICK = 4
MIDTICK_ANY_PRICE_BLOCK = 5
MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK = 6

# ASX deal source codes (per dd.txt §1144-1187)
DEALSOURCE_CONTINUOUS        = 1   # Two orders matched in continuous matching (lit)
DEALSOURCE_CENTREPOINT       = 47  # Centre Point dark pool match
DEALSOURCE_PREFERENCE        = 46  # Centre Point preference-matched
DEALSOURCE_BLOCK             = 50  # Centre Point Any Price Block trade
DEALSOURCE_PREFERENCE_BLOCK  = 51  # Preference Any Price Block trade

ORDERTYPE_BLOCK_LIMIT = 4096  # Centre Point Block Limit order type

# Sentinel value used by ASX for unavailable int64 fields
INT64_SENTINEL = -9223372036854775808

# Change reason codes (per dd.txt spec p.941-954)
CHANGEREASON_UNDEFINED = 0
CHANGEREASON_CANCELED_BY_TRADER = 1
CHANGEREASON_TRADED = 3
CHANGEREASON_INACTIVATED_CONNECTION = 4
CHANGEREASON_UPDATED_BY_USER = 5
CHANGEREASON_NEW_ORDER = 6
CHANGEREASON_MARKET_CONVERTED_AUCTION = 7
CHANGEREASON_MARKET_TO_LIMIT = 8
CHANGEREASON_CANCELED_BY_SYSTEM = 9
CHANGEREASON_UNDISCLOSED_TO_REGULAR = 39

# Session states (bi.txt Section 8)
MATCHING_SESSION_STATES = {'OPEN', 'CONTINUOUS'}
NON_MATCHING_SESSION_STATES = {'PRE_OPEN', 'AUCTION', 'POST_CLOSE', 'CLOSED', 'PRE_CSPA', 'CSPA', 'ADJUST', 'ADJUST_ON', 'PURGE_ORDERS', 'SYSTEM_MAINTENANCE'}


def get_midpoint(nbbo_data, timestamp, orderbookid, fallback_bid, fallback_offer):
    """Get midpoint price at given timestamp."""
    if nbbo_data is not None and len(nbbo_data) > 0:
        midpoint = _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid)
        if midpoint is not None:
            return midpoint
    if fallback_bid is not None and fallback_offer is not None:
        if pd.notna(fallback_bid) and pd.notna(fallback_offer):
            return (fallback_bid + fallback_offer) / 2.0
    return None


def _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid):
    """Get midpoint from NBBO data (most recent quote before timestamp)."""
    if nbbo_data is None:
        return None
    valid_quotes = nbbo_data[
        (nbbo_data[col.common.orderbookid] == orderbookid) &
        (nbbo_data[col.common.timestamp] <= timestamp)
    ]
    if len(valid_quotes) == 0:
        return None
    latest = valid_quotes.iloc[-1]
    midpoint = (latest[col.common.bid] + latest[col.common.offer]) / 2.0
    return midpoint


def _get_nbbo_at_timestamp(nbbo_data, timestamp, orderbookid):
    """Get NBBO bid/offer/timestamp at or before given timestamp."""
    if nbbo_data is None or len(nbbo_data) == 0:
        return 0, 0, 0
    relevant_nbbo = nbbo_data[
        (nbbo_data[col.common.orderbookid] == orderbookid) &
        (nbbo_data[col.common.timestamp] <= timestamp)
    ]
    if len(relevant_nbbo) == 0:
        return 0, 0, 0
    latest = relevant_nbbo.iloc[-1]
    bid = latest.get('bid', 0)
    offer = latest.get('offer', 0)
    nbbo_ts = latest[col.common.timestamp]
    return int(bid), int(offer), int(nbbo_ts)


def _calculate_tick_size(nbbo_bid, nbbo_offer, tick_size_override=None, tick_size_table=None, price=None):
    """
    Calculate tick size from NBBO spread or reference data.
    
    Priority:
    1. Use override if provided
    2. Use tick size table from reference data (price-dependent)
    3. Estimate from spread
    """
    # Use override if provided
    if tick_size_override is not None and tick_size_override > 0:
        return tick_size_override
    
    # Use tick size table if available (price-dependent)
    if tick_size_table is not None and price is not None:
        for ts in tick_size_table:
            if ts['lower_limit'] <= price <= ts['upper_limit']:
                return ts['tick_size']
    
    # Fallback: estimate from spread
    spread = nbbo_offer - nbbo_bid
    if spread <= 0:
        return 10
    if spread <= 20:
        return max(1, spread // 2)
    elif spread <= 100:
        return max(5, spread // 10)
    else:
        return max(10, spread // 20)


def _apply_midtick_improvement(midpoint, nbbo_bid, nbbo_offer, side, midtick_value, tick_size_override=None, tick_size_table=None, price=None):
    """Apply mid-tick price improvement if configured."""
    if midtick_value not in [MIDTICK_YES, MIDTICK_DARK_WITH_MIDTICK]:
        return midpoint
    
    tick_size = _calculate_tick_size(nbbo_bid, nbbo_offer, tick_size_override, tick_size_table, price)
    half_tick = tick_size / 2.0
    
    if side == 1:  # BUY - better price is LOWER
        improved_price = midpoint - half_tick
        improved_price = max(improved_price, nbbo_bid)
    else:  # SELL - better price is HIGHER
        improved_price = midpoint + half_tick
        improved_price = min(improved_price, nbbo_offer)
    
    return improved_price


def _add_execution_delay(timestamp_ns, delay_mean_ms=5.0, delay_std_ms=2.0):
    """
    Add realistic execution delay to timestamp.
    
    Models latency as log-normal distribution:
    - Mean: ~5ms (order entry + matching + broadcast)
    - Std: ~2ms
    """
    delay_ms = np.random.lognormal(mean=np.log(delay_mean_ms), sigma=0.5)
    delay_ns = int(delay_ms * 1_000_000)
    return timestamp_ns + delay_ns


def _has_lost_priority(order_row):
    """
    Check if an order has lost priority due to amendment.
    
    IMPROVEMENT 3: Order Book Position Tracking
    
    Per dd.txt p.868:
    - orderBookPosition > 0 indicates priority loss
    - changereason=5 (updated by user) with certain changes loses priority
    - changereason=7,8,39 also indicate priority loss
    """
    order_book_position = order_row.get('orderbookposition', 0)
    if pd.isna(order_book_position):
        order_book_position = 0
    if order_book_position > 0:
        return True
    changereason = order_row.get('changereason', 0)
    if pd.isna(changereason):
        changereason = 0
    priority_loss_reasons = {
        CHANGEREASON_MARKET_CONVERTED_AUCTION,
        CHANGEREASON_MARKET_TO_LIMIT,
        CHANGEREASON_UNDISCLOSED_TO_REGULAR,
    }
    if changereason in priority_loss_reasons:
        return True
    return False


def _get_effective_timestamp(order_row):
    """Get effective timestamp for order matching."""
    if _has_lost_priority(order_row):
        time_changed = order_row.get('timechanged', order_row.get('timestamp', 0))
        if pd.isna(time_changed):
            time_changed = order_row.get('timestamp', 0)
        return int(time_changed)
    else:
        return int(order_row.get('timestamp', 0))


def _is_price_within_limits(price, price_limits):
    """Check if price is within acceptable limits."""
    if price_limits is None:
        return True
    lower = price_limits.get('lower_limit', 0)
    upper = price_limits.get('upper_limit', float('inf'))
    return lower <= price <= upper


def _validate_order_price_limit(order, execution_price, sweep_side):
    """
    Validate execution price against order's price limit.
    
    Returns True if execution is allowed, False if price violates limit.
    """
    order_type = int(order.get('ordertype', ORDERTYPE_LIMIT))
    order_price = order.get('price', 0)
    
    if order_type == ORDERTYPE_LIMIT:
        # Limit order: must execute at limit or better
        if sweep_side == 1:  # BUY sweep against SELL limit
            # Contra is selling, their limit is minimum acceptable
            if execution_price < order_price:
                return False
        else:  # SELL sweep against BUY limit
            # Contra is buying, their limit is maximum acceptable
            if execution_price > order_price:
                return False
    elif order_type == ORDERTYPE_MARKET:
        # Market order: no price limit
        pass
    elif order_type == ORDERTYPE_MTL:
        # Market-to-Limit: if already filled, use first fill price as limit
        if order.get('matched_quantity', 0) > 0:
            first_fill_price = order.get('first_fill_price', order_price)
            if sweep_side == 1:  # BUY sweep
                if execution_price < first_fill_price:
                    return False
            else:  # SELL sweep
                if execution_price > first_fill_price:
                    return False
    
    return True


def _is_valid_trading_session(timestamp_ns, session_states_df):
    """
    IMPROVEMENT 1: Session State Filtering
    
    Check if timestamp falls within a valid trading session.
    
    Per bi.txt Section 8:
    - OPEN/CONTINUOUS: Matching allowed
    - PRE_OPEN, AUCTION, POST_CLOSE, CLOSED: No continuous matching
    
    Args:
        timestamp_ns: Timestamp in nanoseconds
        session_states_df: DataFrame with session state changes
    
    Returns:
        bool: True if in valid trading session
    """
    if session_states_df is None or len(session_states_df) == 0:
        return True  # No session data, assume valid
    
    # Find the session state at this timestamp
    session_before = session_states_df[
        session_states_df['timestamp'] <= timestamp_ns
    ]
    
    if len(session_before) == 0:
        return True  # Before any session data, assume valid
    
    current_session = session_before.iloc[-1]
    session_state = current_session.get('session_state', 'OPEN')
    
    # Check if session allows continuous matching
    return session_state in MATCHING_SESSION_STATES


def _get_iceberg_available_qty(order, slice_consumed=0):
    """
    IMPROVEMENT 4: Iceberg Order Support

    Get available quantity from the current display slice of an iceberg order.

    Per bi.txt §27: undisclosed-quantity orders show only `display_quantity` in
    the book.  Each time the displayed slice is fully consumed the order is
    repositioned to the back of the queue and a fresh slice of `display_quantity`
    (or remaining total, whichever is smaller) becomes visible.

    Args:
        order:          Order DataFrame row.
        slice_consumed: How many units of the current display slice have already
                        been matched (tracked externally per order_id).

    Returns:
        int: Units available from the current display slice.
             Returns the full remaining quantity for non-iceberg orders.
    """
    display_qty = order.get('display_quantity', None)
    if display_qty is None or pd.isna(display_qty):
        # Not an iceberg — no slice cap.
        return int(order.get('quantity', order.get('leaves_quantity', 0)))
    return max(0, int(display_qty) - int(slice_consumed))


def _get_participant_type(participant_id, participants_dict):
    """
    IMPROVEMENT 6: Participant Type Analysis
    
    Get participant type (Broker, Market Maker, etc.)
    
    Args:
        participant_id: Participant ID
        participants_dict: Dictionary of participant info
    
    Returns:
        str: Participant type
    """
    if participants_dict is None:
        return 'Unknown'
    
    participant_info = participants_dict.get(participant_id, {})
    return participant_info.get('ParticipantType', 'Unknown')


def load_and_prepare_orders(partition_data):
    """Load and prepare sweep orders and all matching-eligible orders for simulation."""
    sweep_orders = _prepare_sweep_orders(partition_data)
    all_orders = _prepare_all_orders_for_matching(partition_data)
    return sweep_orders, all_orders


def _prepare_sweep_orders(partition_data):
    """Prepare sweep orders (type 2048) from ONLY qualifying orders in last_execution."""
    orders_after = partition_data['orders_after']
    last_execution = partition_data['last_execution']
    sweep_orders = last_execution.copy()
    sweep_orders_after = orders_after[orders_after[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE].copy()
    sweep_orders = sweep_orders.merge(sweep_orders_after, on='orderid', how='inner')
    
    required_columns = [
        col.common.orderid, col.common.timestamp, col.common.sequence,
        col.common.side, col.orders.leaves_quantity, 'matched_quantity',
        col.common.price, 'first_execution_time', 'last_execution_time',
        col.common.orderbookid, 'minimumquantity', 'singlefillminimumquantity',
        'crossingkey', col.orders.participant_id, 'midtick',
    ]
    
    # Optional columns with defaults
    optional_columns = {
        'changereason': CHANGEREASON_NEW_ORDER,
        'orderbookposition': 0,
        'timechanged': None,  # Will be filled with timestamp
        'display_quantity': None,  # Iceberg display quantity
        'preferenceonly': 0,
    }
    
    missing = set(required_columns) - set(sweep_orders.columns)
    if missing:
        raise ValueError(f"Missing required columns for sweep order simulation: {missing}")
    
    sweep_orders = sweep_orders[required_columns].copy()
    
    # Add optional columns with defaults
    for col_name, default_val in optional_columns.items():
        if col_name not in sweep_orders.columns:
            sweep_orders[col_name] = default_val
    
    sweep_orders[col.common.orderid] = sweep_orders[col.common.orderid].astype('int64')
    sweep_orders['minimumquantity'] = sweep_orders['minimumquantity'].fillna(0).astype('int64')
    sweep_orders['singlefillminimumquantity'] = sweep_orders['singlefillminimumquantity'].fillna(0).astype('int64')
    sweep_orders['crossingkey'] = sweep_orders['crossingkey'].fillna(0).astype('int64')
    sweep_orders[col.orders.participant_id] = sweep_orders[col.orders.participant_id].fillna(0).astype('int64')
    sweep_orders['midtick'] = sweep_orders['midtick'].fillna(MIDTICK_NO).astype('int64')
    sweep_orders['changereason'] = sweep_orders['changereason'].fillna(CHANGEREASON_NEW_ORDER).astype('int64')
    sweep_orders['orderbookposition'] = sweep_orders['orderbookposition'].fillna(0).astype('int64')
    sweep_orders['preferenceonly'] = sweep_orders['preferenceonly'].fillna(0).astype('int64')
    sweep_orders['timechanged'] = sweep_orders['timechanged'].fillna(sweep_orders['timestamp']).astype('int64')
    if cfg.USE_POLARS_TRANSFORMS:
        import polars as pl
        so_pl = pl.from_pandas(sweep_orders)
        so_pl = so_pl.with_columns(
            pl.when(
                (pl.col('orderbookposition') > 0) |
                pl.col('changereason').is_in([7, 8, 39])
            )
            .then(pl.col('timechanged').fill_null(pl.col('timestamp')))
            .otherwise(pl.col('timestamp'))
            .cast(pl.Int64)
            .alias('effective_timestamp')
        )
        sweep_orders = so_pl.to_pandas()
    else:
        result = sweep_orders.apply(_get_effective_timestamp, axis=1)
        if isinstance(result, pd.DataFrame):
            result = result.squeeze(axis=1) if result.shape[1] == 1 else pd.Series(dtype='int64')
        sweep_orders['effective_timestamp'] = result.astype('int64')
    sweep_orders['lost_priority'] = sweep_orders.apply(_has_lost_priority, axis=1)
    sweep_orders = sweep_orders.sort_values(['effective_timestamp', 'sequence']).reset_index(drop=True)

    return sweep_orders


def _prepare_all_orders_for_matching(partition_data):
    """Prepare ALL Centre Point orders for matching (including sweeps)."""
    orders_before = partition_data['orders_before']
    all_orders = orders_before[orders_before[ORDER_TYPE_COLUMN].isin(ELIGIBLE_MATCHING_ORDER_TYPES)].copy()
    
    # orderstatus is NOT used to filter the contra pool here.
    # Per the ASX DD spec, orderstatus=1 means "on book" and orderstatus=2 means
    # "not yet on book" — but new order submissions always arrive as orderstatus=2
    # (incoming, not yet handled). The orders_before_matching.csv now captures each
    # order at its changereason=1 (submission) event, so orderstatus is always 2
    # at that snapshot. Chronological filtering is done by the simulator via
    # timestamp comparison (contra must arrive before the sweep).

    # Passive orders (ordertype=4) cannot be the aggressive side, but sweeps are
    # always the aggressive side in this simulation — passive orders remain valid
    # as the resting/contra side, so they are kept in the matching pool.

    required_columns = [
        col.common.orderid, col.common.timestamp, col.common.sequence,
        col.common.side, col.common.quantity, col.common.orderbookid,
        col.orders.bid, col.orders.offer,
        col.orders.national_bid, col.orders.national_offer,
        'minimumquantity', 'singlefillminimumquantity', 'crossingkey',
        'participantid', 'midtick', 'timevalidity', 'price',
    ]
    
    # Optional columns with defaults
    optional_columns = {
        'changereason': CHANGEREASON_NEW_ORDER,
        'orderbookposition': 0,
        'timechanged': None,
        'ordertype': ORDERTYPE_LIMIT,
        'display_quantity': None,  # Iceberg display quantity
    }
    
    missing = set(required_columns) - set(all_orders.columns)
    if missing:
        raise ValueError(f"Missing required columns for matching orders: {missing}")
    
    all_orders = all_orders[required_columns].copy()
    
    # Add optional columns with defaults
    for col_name, default_val in optional_columns.items():
        if col_name not in all_orders.columns:
            all_orders[col_name] = default_val
    
    all_orders[col.common.orderid] = all_orders[col.common.orderid].astype('int64')
    all_orders['minimumquantity'] = all_orders['minimumquantity'].fillna(0).astype('int64')
    all_orders['singlefillminimumquantity'] = all_orders['singlefillminimumquantity'].fillna(0).astype('int64')
    all_orders['crossingkey'] = all_orders['crossingkey'].fillna(0).astype('int64')
    all_orders['participantid'] = all_orders['participantid'].fillna(0).astype('int64')
    all_orders['midtick'] = all_orders['midtick'].fillna(MIDTICK_NO).astype('int64')
    all_orders['changereason'] = all_orders['changereason'].fillna(CHANGEREASON_NEW_ORDER).astype('int64')
    all_orders['orderbookposition'] = all_orders['orderbookposition'].fillna(0).astype('int64')
    all_orders['timechanged'] = all_orders['timechanged'].fillna(all_orders['timestamp']).astype('int64')
    all_orders['timevalidity'] = all_orders['timevalidity'].fillna(1536).astype('int64')
    all_orders['ordertype'] = all_orders['ordertype'].fillna(ORDERTYPE_LIMIT).astype('int64')
    all_orders['price'] = all_orders['price'].fillna(0).astype('int64')
    if cfg.USE_POLARS_TRANSFORMS:
        import polars as pl
        ao_pl = pl.from_pandas(all_orders)
        ao_pl = ao_pl.with_columns(
            pl.when(
                (pl.col('orderbookposition') > 0) |
                pl.col('changereason').is_in([7, 8, 39])
            )
            .then(pl.col('timechanged').fill_null(pl.col('timestamp')))
            .otherwise(pl.col('timestamp'))
            .cast(pl.Int64)
            .alias('effective_timestamp')
        )
        all_orders = ao_pl.to_pandas()
    else:
        result = all_orders.apply(_get_effective_timestamp, axis=1)
        if isinstance(result, pd.DataFrame):
            result = result.squeeze(axis=1) if result.shape[1] == 1 else pd.Series(dtype='int64')
        all_orders['effective_timestamp'] = result.astype('int64')
    all_orders = all_orders.sort_values(['effective_timestamp', 'sequence']).reset_index(drop=True)

    return all_orders


def simulate_partition(partition_key, partition_data, reference_loader=None):
    """Simulate sweep matching for a partition."""
    try:
        sweep_orders, all_orders = load_and_prepare_orders(partition_data)
        
        if len(sweep_orders) == 0:
            print(f"  {partition_key}: No sweep orders, skipping")
            return None
        if len(all_orders) == 0:
            print(f"  {partition_key}: No matching orders, skipping")
            return None
        
        nbbo_data = partition_data.get('nbbo')
        if cfg.NBBO_SOURCE == 'EXTERNAL':
            if nbbo_data is None or len(nbbo_data) == 0:
                raise ValueError(f"NBBO_SOURCE is 'EXTERNAL' but no NBBO data found for partition {partition_key}")
        
        # Get reference data (tick sizes, price limits, participants)
        if reference_loader is None:
            processed_dir = Path(partition_data.get('processed_dir', 'data/processed'))
            reference_loader = ReferenceDataLoader(processed_dir)
        
        partition_dir = Path(partition_data.get('partition_dir', f'data/processed/{partition_key}'))
        orderbookid = sweep_orders[col.common.orderbookid].iloc[0] if len(sweep_orders) > 0 else None
        
        # IMPROVEMENT 2: Get tick size table from reference data
        tick_size = reference_loader.get_tick_size(str(partition_dir), orderbookid)
        tick_size_table = reference_loader.get_tick_size_table(orderbookid)
        
        # IMPROVEMENT 3: Get price limits
        price_limits = reference_loader.get_price_limits(orderbookid)
        
        # IMPROVEMENT 6: Get participant info
        reference_loader.load_participants()
        participants_dict = reference_loader.participants
        
        # IMPROVEMENT 1: Get session states
        session_states_df = partition_data.get('session_states')
        
        print(f"    Using tick size: {tick_size} (from reference data)")
        
        results = simulate_sweep_matching(
            sweep_orders, all_orders, nbbo_data,
            tick_size_override=tick_size,
            tick_size_table=tick_size_table,
            price_limits=price_limits,
            participants_dict=participants_dict,
            session_states_df=session_states_df
        )
        
        num_matches = len(results['simulated_trades']) // 2 if len(results['simulated_trades']) > 0 else 0
        print(f"  {partition_key}: {num_matches:,} matches, {len(sweep_orders):,} sweep orders")
        
        amended_count = (sweep_orders['lost_priority'] == True).sum()
        print(f"  {partition_key}: {amended_count} sweep orders lost priority due to amendment")
        
        # Print order type distribution
        if 'ordertype' in all_orders.columns:
            ot_counts = all_orders['ordertype'].value_counts()
            print(f"    Order types: {dict(ot_counts)}")
        
        # Print match type breakdown
        if 'match_type' in results['simulated_trades'].columns:
            match_types = results['simulated_trades']['match_type'].value_counts()
            print(f"    Match types: {dict(match_types)}")

        # IMPROVEMENT 6: Print participant type breakdown
        if 'contra_participant_type' in results['simulated_trades'].columns:
            participant_types = results['simulated_trades']['contra_participant_type'].value_counts()
            print(f"    Participant types: {dict(participant_types)}")

        # ── Phase 2: Resting simulation (controlled by config flags) ─────────
        if cfg.SIMULATE_RESTING_PHASE:
            sweep_orders_prepared, all_orders_prepared = load_and_prepare_orders(partition_data)
            remainder_df = build_remainder_df(
                sweep_orders_prepared, results['sweep_usage']
            )
            if len(remainder_df) > 0:
                print(f"  {partition_key}: Phase 2 — {len(remainder_df)} orders with remaining qty")
                lit_orders_raw = partition_data.get('lit_orders_raw')
                resting_results = simulate_resting_phase(
                    sweep_orders=sweep_orders_prepared,
                    remainder_df=remainder_df,
                    all_cp_orders=all_orders_prepared,
                    lit_orders_raw=lit_orders_raw,
                    nbbo_data=nbbo_data,
                    session_states_df=session_states_df,
                    tick_size_override=tick_size,
                    tick_size_table=tick_size_table,
                    participants_dict=participants_dict,
                )
                results['resting_trades']  = resting_results['resting_trades']
                results['resting_summary'] = resting_results['resting_summary']
                n_resting = len(resting_results['resting_trades']) // 2
                print(f"  {partition_key}: Phase 2 — {n_resting} resting matches")
            else:
                print(f"  {partition_key}: Phase 2 — no remaining qty after Phase 1, skipping")
                results['resting_trades']  = pd.DataFrame()
                results['resting_summary'] = pd.DataFrame()

        return results
    except ValueError as e:
        print(f"\n{'='*80}\nERROR: NBBO Configuration Issue for {partition_key}\n{'='*80}")
        print(f"{str(e)}\n{'='*80}\n")
        raise


def simulate_sweep_matching(sweep_orders, all_orders, nbbo_data, nbbo_source=None, 
                           tick_size_override=None, tick_size_table=None, price_limits=None,
                           participants_dict=None, session_states_df=None):
    """
    Simulate sweep matching with all improvements:
    - Order type filtering
    - Tick size from reference data
    - Price limit validation
    - NBBO timing at match time
    - Execution delay modeling
    - Sweep-to-sweep tracking
    - Session state filtering (IMPROVEMENT 1)
    - Order book position tracking (IMPROVEMENT 3)
    - Iceberg order support (IMPROVEMENT 4)
    - Participant type analysis (IMPROVEMENT 6)
    """
    if nbbo_source is None:
        nbbo_source = cfg.NBBO_SOURCE
    if nbbo_source not in ['INTERNAL', 'EXTERNAL']:
        raise ValueError(f"Invalid NBBO_SOURCE: '{nbbo_source}'")
    
    simulated_trades = []
    sweep_summaries = []
    sweep_usage = {int(orderid): {'matched_quantity': 0, 'num_matches': 0} 
                   for orderid in sweep_orders[col.common.orderid].values}
    order_remaining = {int(orderid): qty for orderid, qty in
                      zip(all_orders[col.common.orderid].values, all_orders[col.common.quantity].values)}
    # Per-order count of units consumed from the current display slice (iceberg tracking).
    iceberg_slice_consumed: dict = {}
    all_orders_indexed = all_orders.set_index('orderid')
    
    row_counter = 1
    match_counter = 0
    base_matchgroupid = 7904794000999000001
    
    if len(sweep_orders) > 0:
        first_timestamp = sweep_orders[col.common.timestamp].iloc[0]
        tradedate = pd.to_datetime(first_timestamp, unit='ns').strftime('%Y-%m-%d')
    else:
        tradedate = None
    
    if nbbo_source == 'EXTERNAL':
        if nbbo_data is None or len(nbbo_data) == 0:
            raise ValueError("NBBO_SOURCE is 'EXTERNAL' but no NBBO data available")
        nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
        print(f"    Using NBBO source: EXTERNAL ({len(nbbo_data):,} snapshots)")
        # Pre-extract numpy arrays for fast NBBO lookup inside inner loop
        _nbbo_ts  = nbbo_sorted['timestamp'].to_numpy()
        _nbbo_bid = nbbo_sorted[col.orders.bid].to_numpy()
        _nbbo_ask = nbbo_sorted[col.orders.offer].to_numpy()
    else:
        nbbo_sorted = None
        _nbbo_ts = _nbbo_bid = _nbbo_ask = None
        print(f"    Using NBBO source: INTERNAL")

    # Pre-sort session states once for Polars join_asof (used when USE_POLARS_TRANSFORMS=True)
    if cfg.USE_POLARS_TRANSFORMS and session_states_df is not None and len(session_states_df) > 0:
        import polars as pl
        _session_pl = pl.from_pandas(session_states_df).sort('timestamp')
    else:
        _session_pl = None

    for idx in range(len(sweep_orders)):
        sweep = sweep_orders.iloc[idx]
        sweep_id = int(sweep_orders[col.common.orderid].iloc[idx])
        sweep_side = int(sweep[col.common.side])
        sweep_qty_available = sweep[col.orders.leaves_quantity]
        sweep_orderbookid = sweep[col.common.orderbookid]
        
        first_exec_time = int(sweep['effective_timestamp'])
        last_exec_time = int(sweep['last_execution_time'])
        sweep_lost_priority = bool(sweep.get('lost_priority', False))
        sweep_changereason = int(sweep.get('changereason', CHANGEREASON_NEW_ORDER))
        
        if sweep_qty_available <= 0:
            sweep_summaries.append({
                'orderid': sweep_id, 'timestamp': sweep[col.common.timestamp],
                'side': sweep_side, 'quantity': sweep_qty_available,
                'matched_quantity': 0, 'remaining_quantity': 0,
                'fill_ratio': 0, 'num_matches': 0,
                'orderbookid': sweep_orderbookid,
                'lost_priority': sweep_lost_priority,
                'changereason': sweep_changereason,
            })
            continue
        
        eligible_orders = all_orders[
            (all_orders['effective_timestamp'] >= first_exec_time) &
            (all_orders['effective_timestamp'] <= last_exec_time) &
            (all_orders[col.common.orderid] != sweep_id) &
            (all_orders[col.common.orderbookid] == sweep_orderbookid) &
            (all_orders[col.common.side] != sweep_side)
        ].copy()
        
        # IMPROVEMENT 1: Session State Filtering
        if session_states_df is not None and len(session_states_df) > 0:
            if cfg.USE_POLARS_TRANSFORMS and _session_pl is not None:
                import polars as pl
                eo_pl = pl.from_pandas(eligible_orders).sort('effective_timestamp')
                eo_pl = eo_pl.join_asof(
                    _session_pl.select(['timestamp', 'session_state']),
                    left_on='effective_timestamp',
                    right_on='timestamp',
                    strategy='backward'
                ).filter(pl.col('session_state').is_in(['OPEN', 'CONTINUOUS'])).drop('session_state')
                eligible_orders = eo_pl.to_pandas()
            else:
                eligible_orders = eligible_orders[
                    eligible_orders['effective_timestamp'].apply(
                        lambda ts: _is_valid_trading_session(ts, session_states_df)
                    )
                ]
        
        eligible_orders = eligible_orders.sort_values(['effective_timestamp', 'sequence'])

        # Build a min-heap so iceberg contras can be repositioned to the back of
        # the queue when their display slice is exhausted (bi.txt §27).
        # Elements: (effective_timestamp, sequence, counter, order_dict)
        _p1_heap: list = []
        _p1_counter = 0
        for _, _o_row in eligible_orders.iterrows():
            heapq.heappush(_p1_heap, (
                int(_o_row['effective_timestamp']),
                int(_o_row['sequence']),
                _p1_counter,
                _o_row.to_dict(),
            ))
            _p1_counter += 1

        sweep_remaining_qty = sweep_qty_available
        sweep_matched_qty = 0
        sweep_num_matches = 0

        while _p1_heap and sweep_remaining_qty > 0:
            _, _, _, order = heapq.heappop(_p1_heap)

            order_id = int(order[col.common.orderid])
            order_available = order_remaining.get(order_id, 0)
            if order_available <= 0:
                continue
            
            # IMPROVEMENT 4: Iceberg Order Support — cap to current display slice
            order_available = min(
                order_available,
                _get_iceberg_available_qty(order, iceberg_slice_consumed.get(order_id, 0)),
            )
            
            # MAQ Validation
            sweep_maq = int(sweep.get('minimumquantity', 0)) if 'minimumquantity' in sweep else 0
            sweep_single_fill = int(sweep.get('singlefillminimumquantity', 0)) if 'singlefillminimumquantity' in sweep else 0
            contra_maq = int(order.get('minimumquantity', 0))
            contra_single_fill = int(order.get('singlefillminimumquantity', 0))
            potential_match_qty = min(sweep_remaining_qty, order_available)
            
            if sweep_maq > 0:
                if sweep_single_fill == 1:
                    if potential_match_qty < sweep_maq:
                        continue
                else:
                    if sweep_remaining_qty < sweep_maq and sweep_matched_qty > 0:
                        break
                    elif potential_match_qty < sweep_maq and sweep_matched_qty == 0:
                        continue
            
            if contra_maq > 0:
                if contra_single_fill == 1:
                    if potential_match_qty < contra_maq:
                        continue
                else:
                    if order_available < contra_maq:
                        continue
            
            # Crossing Prevention
            sweep_participant = int(sweep.get('participantid', 0))
            contra_participant = int(order.get('participantid', 0))
            sweep_crossing_key = int(sweep.get('crossingkey', 0))
            contra_crossing_key = int(order.get('crossingkey', 0))
            
            if sweep_participant > 0 and sweep_participant == contra_participant:
                if sweep_crossing_key == 0 or contra_crossing_key == 0:
                    continue
                elif sweep_crossing_key != contra_crossing_key:
                    continue
            
            match_qty = min(sweep_remaining_qty, order_available)
            match_timestamp = int(order['effective_timestamp'])
            
            # IMPROVEMENT 1: Check session state at match time
            if not _is_valid_trading_session(match_timestamp, session_states_df):
                continue
            
            # Detect Any Price Block contra before NBBO check (APB doesn't need NBBO)
            contra_midtick = int(order.get('midtick', MIDTICK_NO))
            is_apb = (
                int(order.get('exchangeordertype', 0)) == ORDERTYPE_BLOCK_LIMIT
                and contra_midtick in (MIDTICK_ANY_PRICE_BLOCK, MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK)
            )

            if is_apb:
                # §24.1.3 Any Price Block — execution at contra's limit price, no NBBO constraint.
                # Both sides must have crossing prices; minimum block size must be met.
                contra_limit = int(order.get('price', 0))
                sweep_limit  = int(sweep.get('price', 0))
                if sweep_side == 1:      # buy sweep: must be willing to pay at least contra ask
                    if sweep_limit < contra_limit:
                        continue
                else:                    # sell sweep: must be willing to sell at most contra bid
                    if sweep_limit > contra_limit:
                        continue
                execution_price = float(contra_limit)
                # Minimum block size: traded value (qty * price in price units) must meet threshold.
                if cfg.MIN_BLOCK_SIZE > 0 and match_qty * execution_price < cfg.MIN_BLOCK_SIZE:
                    continue
                is_pref = (
                    cfg.RESTING_APPLY_PREFERENCING
                    and sweep_participant > 0
                    and contra_participant > 0
                    and sweep_participant == contra_participant
                )
                match_type = 'BLOCK_PREF' if is_pref else 'BLOCK'
                nbbo_bid = nbbo_offer = 0  # no NBBO constraint for APB
            else:
                # GAP 4: Get NBBO at match time (not order entry time)
                if nbbo_source == 'EXTERNAL':
                    if _nbbo_ts is not None:
                        # Fast numpy searchsorted — eliminates one DataFrame slice per match
                        _idx = int(np.searchsorted(_nbbo_ts, match_timestamp, side='right')) - 1
                        nbbo_bid  = int(_nbbo_bid[_idx])  if _idx >= 0 else 0
                        nbbo_offer = int(_nbbo_ask[_idx]) if _idx >= 0 else 0
                    else:
                        nbbo_bid, nbbo_offer, _ = _get_nbbo_at_timestamp(
                            nbbo_sorted, match_timestamp, sweep_orderbookid
                        )
                    if nbbo_bid == 0 or nbbo_offer == 0:
                        continue
                else:
                    nbbo_bid = int(order[col.orders.national_bid])
                    nbbo_offer = int(order[col.orders.national_offer])
                    # Sentinel value (-9223372036854775808) means field is unavailable;
                    # fall back to order book bid/offer snapshot columns.
                    if nbbo_bid == INT64_SENTINEL or nbbo_offer == INT64_SENTINEL:
                        nbbo_bid = int(order[col.orders.bid])
                        nbbo_offer = int(order[col.orders.offer])
                    # If still invalid (both 0 or sentinel), skip this match.
                    if nbbo_bid <= 0 or nbbo_offer <= 0:
                        continue

                # GAP 3: Check price limits
                midpoint = (nbbo_bid + nbbo_offer) / 2
                if not _is_price_within_limits(midpoint, price_limits):
                    continue

                # GAP 1: Validate order price limits
                if not _validate_order_price_limit(order, midpoint, sweep_side):
                    continue

                # GAP 2 & Mid-tick improvement
                sweep_midtick = int(sweep.get('midtick', MIDTICK_NO))
                effective_midtick = max(sweep_midtick, contra_midtick)
                execution_price = _apply_midtick_improvement(
                    midpoint, nbbo_bid, nbbo_offer, sweep_side, effective_midtick,
                    tick_size_override=tick_size_override,
                    tick_size_table=tick_size_table,
                    price=midpoint
                )

                # GAP 6: Track match type (sweep-to-sweep vs sweep-to-regular)
                match_type = 'SWEEP_TO_SWEEP' if order.get('exchangeordertype') == 2048 else 'SWEEP_TO_REGULAR'

            # GAP 5: Add execution delay
            match_timestamp = _add_execution_delay(match_timestamp)

            matchgroupid = base_matchgroupid + match_counter
            match_counter += 1

            sweep_side_val = int(sweep[col.common.side])
            order_side = int(order[col.common.side])
            
            # IMPROVEMENT 6: Get participant type
            contra_participant_type = _get_participant_type(contra_participant, participants_dict)
            
            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_timestamp, 'securitycode': sweep_orderbookid,
                'orderid': sweep_id, 'dealsource': _get_deal_source(match_type), 'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': sweep_side_val, 'participantid': 0,
                'passiveaggressive': 1, 'row_num': row_counter,
                'match_type': match_type,
                'contra_participant_type': contra_participant_type,
            })
            row_counter += 1

            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_timestamp, 'securitycode': sweep_orderbookid,
                'orderid': order_id, 'dealsource': _get_deal_source(match_type), 'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': order_side, 'participantid': 0,
                'passiveaggressive': 0, 'row_num': row_counter,
                'match_type': match_type,
                'contra_participant_type': contra_participant_type,
            })
            row_counter += 1
            
            sweep_remaining_qty -= match_qty
            order_remaining[order_id] -= match_qty
            sweep_matched_qty += match_qty
            sweep_num_matches += 1
            # Update iceberg slice consumed; on exhaustion, reposition to back of queue.
            _iceberg_dq = order.get('display_quantity', None)
            if _iceberg_dq is not None and not pd.isna(_iceberg_dq):
                _iceberg_dq = int(_iceberg_dq)
                _new_consumed = iceberg_slice_consumed.get(order_id, 0) + match_qty
                if _new_consumed >= _iceberg_dq:
                    # Slice exhausted — reset consumed counter and reposition the order to
                    # the back of the time-priority queue at its current timestamp.
                    iceberg_slice_consumed[order_id] = _new_consumed - _iceberg_dq
                    if order_remaining.get(order_id, 0) > 0:
                        heapq.heappush(_p1_heap, (
                            int(order['effective_timestamp']),
                            _p1_counter,
                            _p1_counter,
                            order,
                        ))
                        _p1_counter += 1
                else:
                    iceberg_slice_consumed[order_id] = _new_consumed
        
        if sweep_id not in sweep_usage:
            sweep_usage[sweep_id] = {'matched_quantity': 0, 'num_matches': 0}
        sweep_usage[sweep_id]['matched_quantity'] = sweep_matched_qty
        sweep_usage[sweep_id]['num_matches'] = sweep_num_matches
        
        sweep_summaries.append({
            'orderid': sweep_id, 'timestamp': sweep[col.common.timestamp],
            'side': sweep_side, 'quantity': sweep_qty_available,
            'matched_quantity': sweep_matched_qty,
            'remaining_quantity': sweep_remaining_qty,
            'fill_ratio': sweep_matched_qty / sweep_qty_available if sweep_qty_available > 0 else 0,
            'num_matches': sweep_num_matches, 'orderbookid': sweep_orderbookid,
            'lost_priority': sweep_lost_priority, 'changereason': sweep_changereason,
        })
    
    if simulated_trades:
        simulated_trades_df = pd.DataFrame(simulated_trades)
        int_columns = [
            'EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
            'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
            'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
            'participantid', 'passiveaggressive', 'row_num'
        ]
        for col_name in int_columns:
            if col_name in simulated_trades_df.columns:
                simulated_trades_df[col_name] = simulated_trades_df[col_name].astype('int64')
    else:
        simulated_trades_df = pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
            'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
            'tradeprice', 'quantity', 'side', 'participantid',
            'passiveaggressive', 'row_num', 'match_type', 'contra_participant_type'
        ])
    
    order_summary_df = pd.DataFrame(sweep_summaries)
    sweep_utilization_df = _generate_sweep_utilization(sweep_orders, sweep_usage)

    return {
        'order_summary': order_summary_df,
        'sweep_utilization': sweep_utilization_df,
        'simulated_trades': simulated_trades_df,
        'sweep_usage': sweep_usage,          # exposed for build_remainder_df
    }


def _generate_sweep_utilization_from_list(sweep_dicts, sweep_usage):
    """Streaming variant of _generate_sweep_utilization that works from a list of dicts."""
    utilization = []
    for sweep in sweep_dicts:
        sweep_id = int(sweep[col.common.orderid])
        available_qty = sweep[col.orders.leaves_quantity]
        if sweep_id not in sweep_usage:
            continue
        usage = sweep_usage[sweep_id]
        matched_qty = usage['matched_quantity']
        utilization.append({
            'orderid': sweep_id,
            'leavesquantity': available_qty,
            'matched_quantity': matched_qty,
            'remaining_quantity': available_qty - matched_qty,
            'utilization_ratio': matched_qty / available_qty if available_qty > 0 else 0,
            'num_matches': usage['num_matches']
        })
    return pd.DataFrame(utilization)


def simulate_sweep_matching_streaming(sweep_orders_iter, all_orders_iter, nbbo_data,
                                      nbbo_source=None, tick_size_override=None,
                                      tick_size_table=None, price_limits=None,
                                      participants_dict=None, session_states_df=None):
    """
    Streaming variant of simulate_sweep_matching.

    Accepts iterators of order dicts instead of DataFrames.  all_orders_iter is
    consumed eagerly into a list (required for the per-sweep eligible-window
    filter); sweep_orders_iter is consumed one sweep at a time.

    Key difference from simulate_sweep_matching:
    - No per-sweep DataFrame slice / .sort_values() / .iterrows()
    - Eligible contras are found via a single list comprehension directly into
      the heapq — one pass, no intermediate copy.
    - Session pre-filtering (Polars join_asof) is skipped; the per-match
      _is_valid_trading_session check inside the while loop still runs.
    """
    if nbbo_source is None:
        nbbo_source = cfg.NBBO_SOURCE
    if nbbo_source not in ['INTERNAL', 'EXTERNAL']:
        raise ValueError(f"Invalid NBBO_SOURCE: '{nbbo_source}'")

    # Consume contra iterator eagerly — must arrive pre-sorted by
    # (effective_timestamp, sequence), which stream_orders_for_partition guarantees.
    all_orders_list: list = list(all_orders_iter)

    order_remaining: dict = {int(o[col.common.orderid]): int(o[col.common.quantity])
                             for o in all_orders_list}
    iceberg_slice_consumed: dict = {}
    sweep_usage: dict = {}

    simulated_trades: list = []
    sweep_summaries: list = []
    sweep_dicts: list = []  # for utilization report

    row_counter = 1
    match_counter = 0
    base_matchgroupid = 7904794000999000001
    tradedate = None

    if nbbo_source == 'EXTERNAL':
        if nbbo_data is None or len(nbbo_data) == 0:
            raise ValueError("NBBO_SOURCE is 'EXTERNAL' but no NBBO data available")
        nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
        print(f"    Using NBBO source: EXTERNAL ({len(nbbo_data):,} snapshots)")
        _nbbo_ts  = nbbo_sorted['timestamp'].to_numpy()
        _nbbo_bid = nbbo_sorted[col.orders.bid].to_numpy()
        _nbbo_ask = nbbo_sorted[col.orders.offer].to_numpy()
    else:
        _nbbo_ts = _nbbo_bid = _nbbo_ask = None
        print(f"    Using NBBO source: INTERNAL")

    for sweep in sweep_orders_iter:
        sweep_id          = int(sweep[col.common.orderid])
        sweep_side        = int(sweep[col.common.side])
        sweep_qty_available = sweep[col.orders.leaves_quantity]
        sweep_orderbookid = sweep[col.common.orderbookid]

        first_exec_time   = int(sweep['effective_timestamp'])
        last_exec_time    = int(sweep['last_execution_time'])
        sweep_lost_priority  = bool(sweep.get('lost_priority', False))
        sweep_changereason   = int(sweep.get('changereason', CHANGEREASON_NEW_ORDER))

        if tradedate is None:
            tradedate = pd.to_datetime(first_exec_time, unit='ns').strftime('%Y-%m-%d')

        sweep_dicts.append(sweep)
        sweep_usage.setdefault(sweep_id, {'matched_quantity': 0, 'num_matches': 0})

        if sweep_qty_available <= 0:
            sweep_summaries.append({
                'orderid': sweep_id, 'timestamp': sweep[col.common.timestamp],
                'side': sweep_side, 'quantity': sweep_qty_available,
                'matched_quantity': 0, 'remaining_quantity': 0,
                'fill_ratio': 0, 'num_matches': 0,
                'orderbookid': sweep_orderbookid,
                'lost_priority': sweep_lost_priority,
                'changereason': sweep_changereason,
            })
            continue

        # Build heap directly from all_orders_list — no DataFrame copy or iterrows.
        # The list is already sorted by (effective_timestamp, sequence).
        _p1_heap: list = []
        _p1_counter = 0
        for o in all_orders_list:
            if (first_exec_time <= o['effective_timestamp'] <= last_exec_time
                    and int(o[col.common.orderid]) != sweep_id
                    and int(o[col.common.orderbookid]) == sweep_orderbookid
                    and int(o[col.common.side]) != sweep_side):
                heapq.heappush(_p1_heap, (
                    o['effective_timestamp'], o['sequence'], _p1_counter, o
                ))
                _p1_counter += 1

        sweep_remaining_qty = sweep_qty_available
        sweep_matched_qty   = 0
        sweep_num_matches   = 0

        while _p1_heap and sweep_remaining_qty > 0:
            _, _, _, order = heapq.heappop(_p1_heap)

            order_id = int(order[col.common.orderid])
            order_available = order_remaining.get(order_id, 0)
            if order_available <= 0:
                continue

            order_available = min(
                order_available,
                _get_iceberg_available_qty(order, iceberg_slice_consumed.get(order_id, 0)),
            )

            sweep_maq        = int(sweep.get('minimumquantity', 0)) if 'minimumquantity' in sweep else 0
            sweep_single_fill = int(sweep.get('singlefillminimumquantity', 0)) if 'singlefillminimumquantity' in sweep else 0
            contra_maq        = int(order.get('minimumquantity', 0))
            contra_single_fill = int(order.get('singlefillminimumquantity', 0))
            potential_match_qty = min(sweep_remaining_qty, order_available)

            if sweep_maq > 0:
                if sweep_single_fill == 1:
                    if potential_match_qty < sweep_maq:
                        continue
                else:
                    if sweep_remaining_qty < sweep_maq and sweep_matched_qty > 0:
                        break
                    elif potential_match_qty < sweep_maq and sweep_matched_qty == 0:
                        continue

            if contra_maq > 0:
                if contra_single_fill == 1:
                    if potential_match_qty < contra_maq:
                        continue
                else:
                    if order_available < contra_maq:
                        continue

            sweep_participant  = int(sweep.get('participantid', 0))
            contra_participant = int(order.get('participantid', 0))
            sweep_crossing_key  = int(sweep.get('crossingkey', 0))
            contra_crossing_key = int(order.get('crossingkey', 0))

            if sweep_participant > 0 and sweep_participant == contra_participant:
                if sweep_crossing_key == 0 or contra_crossing_key == 0:
                    continue
                elif sweep_crossing_key != contra_crossing_key:
                    continue

            match_qty       = min(sweep_remaining_qty, order_available)
            match_timestamp = int(order['effective_timestamp'])

            if not _is_valid_trading_session(match_timestamp, session_states_df):
                continue

            contra_midtick = int(order.get('midtick', MIDTICK_NO))
            is_apb = (
                int(order.get('exchangeordertype', 0)) == ORDERTYPE_BLOCK_LIMIT
                and contra_midtick in (MIDTICK_ANY_PRICE_BLOCK, MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK)
            )

            if is_apb:
                contra_limit = int(order.get('price', 0))
                sweep_limit  = int(sweep.get('price', 0))
                if sweep_side == 1:
                    if sweep_limit < contra_limit:
                        continue
                else:
                    if sweep_limit > contra_limit:
                        continue
                execution_price = float(contra_limit)
                if cfg.MIN_BLOCK_SIZE > 0 and match_qty * execution_price < cfg.MIN_BLOCK_SIZE:
                    continue
                is_pref = (
                    cfg.RESTING_APPLY_PREFERENCING
                    and sweep_participant > 0
                    and contra_participant > 0
                    and sweep_participant == contra_participant
                )
                match_type = 'BLOCK_PREF' if is_pref else 'BLOCK'
                nbbo_bid = nbbo_offer = 0
            else:
                if nbbo_source == 'EXTERNAL':
                    if _nbbo_ts is not None:
                        _idx = int(np.searchsorted(_nbbo_ts, match_timestamp, side='right')) - 1
                        nbbo_bid   = int(_nbbo_bid[_idx]) if _idx >= 0 else 0
                        nbbo_offer = int(_nbbo_ask[_idx]) if _idx >= 0 else 0
                    else:
                        nbbo_bid, nbbo_offer, _ = _get_nbbo_at_timestamp(
                            None, match_timestamp, sweep_orderbookid)
                    if nbbo_bid == 0 or nbbo_offer == 0:
                        continue
                else:
                    nbbo_bid   = int(order[col.orders.national_bid])
                    nbbo_offer = int(order[col.orders.national_offer])
                    if nbbo_bid == INT64_SENTINEL or nbbo_offer == INT64_SENTINEL:
                        nbbo_bid   = int(order[col.orders.bid])
                        nbbo_offer = int(order[col.orders.offer])
                    if nbbo_bid <= 0 or nbbo_offer <= 0:
                        continue

                midpoint = (nbbo_bid + nbbo_offer) / 2
                if not _is_price_within_limits(midpoint, price_limits):
                    continue
                if not _validate_order_price_limit(order, midpoint, sweep_side):
                    continue

                sweep_midtick     = int(sweep.get('midtick', MIDTICK_NO))
                effective_midtick = max(sweep_midtick, contra_midtick)
                execution_price   = _apply_midtick_improvement(
                    midpoint, nbbo_bid, nbbo_offer, sweep_side, effective_midtick,
                    tick_size_override=tick_size_override,
                    tick_size_table=tick_size_table,
                    price=midpoint
                )
                match_type = 'SWEEP_TO_SWEEP' if order.get('exchangeordertype') == 2048 else 'SWEEP_TO_REGULAR'

            match_timestamp = _add_execution_delay(match_timestamp)
            matchgroupid    = base_matchgroupid + match_counter
            match_counter  += 1

            sweep_side_val = int(sweep[col.common.side])
            order_side     = int(order[col.common.side])
            contra_participant_type = _get_participant_type(contra_participant, participants_dict)

            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_timestamp, 'securitycode': sweep_orderbookid,
                'orderid': sweep_id, 'dealsource': _get_deal_source(match_type),
                'exchangeinfo': '', 'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': sweep_side_val, 'participantid': 0,
                'passiveaggressive': 1, 'row_num': row_counter,
                'match_type': match_type, 'contra_participant_type': contra_participant_type,
            })
            row_counter += 1
            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_timestamp, 'securitycode': sweep_orderbookid,
                'orderid': order_id, 'dealsource': _get_deal_source(match_type),
                'exchangeinfo': '', 'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': order_side, 'participantid': 0,
                'passiveaggressive': 0, 'row_num': row_counter,
                'match_type': match_type, 'contra_participant_type': contra_participant_type,
            })
            row_counter += 1

            sweep_remaining_qty -= match_qty
            order_remaining[order_id] -= match_qty
            sweep_matched_qty   += match_qty
            sweep_num_matches   += 1

            _iceberg_dq = order.get('display_quantity', None)
            if _iceberg_dq is not None and not pd.isna(_iceberg_dq):
                _iceberg_dq = int(_iceberg_dq)
                _new_consumed = iceberg_slice_consumed.get(order_id, 0) + match_qty
                if _new_consumed >= _iceberg_dq:
                    iceberg_slice_consumed[order_id] = _new_consumed - _iceberg_dq
                    if order_remaining.get(order_id, 0) > 0:
                        heapq.heappush(_p1_heap, (
                            int(order['effective_timestamp']),
                            _p1_counter, _p1_counter, order,
                        ))
                        _p1_counter += 1
                else:
                    iceberg_slice_consumed[order_id] = _new_consumed

        sweep_usage[sweep_id]['matched_quantity'] = sweep_matched_qty
        sweep_usage[sweep_id]['num_matches']      = sweep_num_matches

        sweep_summaries.append({
            'orderid': sweep_id, 'timestamp': sweep[col.common.timestamp],
            'side': sweep_side, 'quantity': sweep_qty_available,
            'matched_quantity': sweep_matched_qty,
            'remaining_quantity': sweep_remaining_qty,
            'fill_ratio': sweep_matched_qty / sweep_qty_available if sweep_qty_available > 0 else 0,
            'num_matches': sweep_num_matches, 'orderbookid': sweep_orderbookid,
            'lost_priority': sweep_lost_priority, 'changereason': sweep_changereason,
        })

    if simulated_trades:
        simulated_trades_df = pd.DataFrame(simulated_trades)
        int_columns = [
            'EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
            'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
            'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
            'participantid', 'passiveaggressive', 'row_num'
        ]
        for col_name in int_columns:
            if col_name in simulated_trades_df.columns:
                simulated_trades_df[col_name] = simulated_trades_df[col_name].astype('int64')
    else:
        simulated_trades_df = pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
            'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
            'tradeprice', 'quantity', 'side', 'participantid',
            'passiveaggressive', 'row_num', 'match_type', 'contra_participant_type'
        ])

    order_summary_df      = pd.DataFrame(sweep_summaries)
    sweep_utilization_df  = _generate_sweep_utilization_from_list(sweep_dicts, sweep_usage)

    return {
        'order_summary':      order_summary_df,
        'sweep_utilization':  sweep_utilization_df,
        'simulated_trades':   simulated_trades_df,
        'sweep_usage':        sweep_usage,
    }


def simulate_partition_streaming(partition_key, partition_data, reference_loader=None):
    """
    Streaming variant of simulate_partition.

    Phase 1 matching is fed via dict iterators from data_processor streaming
    generators — no per-sweep DataFrame copies or .iterrows() calls.
    Phase 2 (resting simulation) still uses DataFrames via load_and_prepare_orders.
    """
    import pipeline.data_processor as _dp
    try:
        # Prepare DataFrames once for metadata (orderbookid, counts) and Phase 2.
        # Phase 1 matching will use the streaming generators below.
        sweep_orders_check, all_orders_check = load_and_prepare_orders(partition_data)
        if len(sweep_orders_check) == 0:
            print(f"  {partition_key}: No sweep orders, skipping")
            return None
        if len(all_orders_check) == 0:
            print(f"  {partition_key}: No matching orders, skipping")
            return None

        sweep_orders_iter = _dp.stream_sweep_orders_for_partition(partition_data)
        all_orders_iter   = _dp.stream_orders_for_partition(partition_data)

        nbbo_data = partition_data.get('nbbo')
        if cfg.NBBO_SOURCE == 'EXTERNAL':
            if nbbo_data is None or len(nbbo_data) == 0:
                raise ValueError(
                    f"NBBO_SOURCE is 'EXTERNAL' but no NBBO data found for partition {partition_key}")

        if reference_loader is None:
            processed_dir    = Path(partition_data.get('processed_dir', 'data/processed'))
            reference_loader = ReferenceDataLoader(processed_dir)

        partition_dir = Path(partition_data.get('partition_dir', f'data/processed/{partition_key}'))
        orderbookid   = sweep_orders_check[col.common.orderbookid].iloc[0] if len(sweep_orders_check) > 0 else None

        tick_size       = reference_loader.get_tick_size(str(partition_dir), orderbookid)
        tick_size_table = reference_loader.get_tick_size_table(orderbookid)
        price_limits    = reference_loader.get_price_limits(orderbookid)
        reference_loader.load_participants()
        participants_dict = reference_loader.participants
        session_states_df = partition_data.get('session_states')

        print(f"    Using tick size: {tick_size} (from reference data) [streaming]")

        results = simulate_sweep_matching_streaming(
            sweep_orders_iter, all_orders_iter, nbbo_data,
            tick_size_override=tick_size,
            tick_size_table=tick_size_table,
            price_limits=price_limits,
            participants_dict=participants_dict,
            session_states_df=session_states_df,
        )

        num_matches = len(results['simulated_trades']) // 2 if len(results['simulated_trades']) > 0 else 0
        print(f"  {partition_key}: {num_matches:,} matches, {len(sweep_orders_check):,} sweep orders [streaming]")

        # Phase 2: resting simulation still uses DataFrames (reuse the already-prepared ones)
        if cfg.SIMULATE_RESTING_PHASE:
            sweep_orders_prepared, all_orders_prepared = sweep_orders_check, all_orders_check
            remainder_df = build_remainder_df(sweep_orders_prepared, results['sweep_usage'])
            if len(remainder_df) > 0:
                print(f"  {partition_key}: Phase 2 — {len(remainder_df)} orders with remaining qty")
                lit_orders_raw = partition_data.get('lit_orders_raw')
                resting_results = simulate_resting_phase(
                    sweep_orders=sweep_orders_prepared,
                    remainder_df=remainder_df,
                    all_cp_orders=all_orders_prepared,
                    lit_orders_raw=lit_orders_raw,
                    nbbo_data=nbbo_data,
                    session_states_df=session_states_df,
                    tick_size_override=tick_size,
                    tick_size_table=tick_size_table,
                    participants_dict=participants_dict,
                )
                results['resting_trades']  = resting_results['resting_trades']
                results['resting_summary'] = resting_results['resting_summary']
                n_resting = len(resting_results['resting_trades']) // 2
                print(f"  {partition_key}: Phase 2 — {n_resting} resting matches")
            else:
                print(f"  {partition_key}: Phase 2 — no remaining qty after Phase 1, skipping")
                results['resting_trades']  = pd.DataFrame()
                results['resting_summary'] = pd.DataFrame()

        return results
    except ValueError as e:
        print(f"\n{'='*80}\nERROR: NBBO Configuration Issue for {partition_key}\n{'='*80}")
        print(f"{str(e)}\n{'='*80}\n")
        raise


def _calc_resting_price(limit_price, tick_size, side):
    """
    Calculate effective passive resting price for Centre Point.

    Per bi.txt §25.2 pt 6: passive CP execution is at a half-tick INSIDE
    the order's limit price (i.e. the midpoint must be at least this good).

    If RESTING_USE_MIDTICK is False, the raw limit price is returned unchanged
    (experimental — not per ASX spec).

    Args:
        limit_price: Order limit price (integer ticks)
        tick_size:   Tick size for the instrument
        side:        1 = BUY, 2 = SELL

    Returns:
        int: Effective resting price threshold
    """
    if not cfg.RESTING_USE_MIDTICK:
        return int(limit_price)
    half_tick = tick_size / 2.0
    if side == 1:   # BUY  — half-tick ABOVE limit makes it easier to match
        return int(limit_price + half_tick)
    else:           # SELL — half-tick BELOW limit
        return int(limit_price - half_tick)


def _calc_lit_resting_price(limit_price, tick_size, side):
    """
    Effective resting price in ASX TradeMatch.

    Per bi.txt §25.2 pt 7: mid-tick flag is IGNORED in TradeMatch.
    When RESTING_LIT_USE_LIMIT=True (default / spec-compliant) the raw
    limit price is used.  Set False for experimental half-tick variant.
    """
    if cfg.RESTING_LIT_USE_LIMIT:
        return int(limit_price)
    half_tick = tick_size / 2.0
    if side == 1:
        return int(limit_price + half_tick)
    else:
        return int(limit_price - half_tick)


def _get_session_end_time(session_states_df, after_timestamp):
    """
    Return the timestamp of the first non-OPEN/CONTINUOUS session change
    after `after_timestamp`.  Falls back to int max if no session data.
    """
    if session_states_df is None or len(session_states_df) == 0:
        return int(2**62)
    future = session_states_df[
        (session_states_df['timestamp'] > after_timestamp) &
        (~session_states_df['session_state'].isin(MATCHING_SESSION_STATES))
    ]
    if len(future) == 0:
        return int(2**62)
    return int(future['timestamp'].iloc[0])


def _build_lit_order_book(lit_orders_raw, orderbookid):
    """
    Build a price-time priority lit order book from raw orders for a security.

    Only includes orders with exchangeordertype in [0, 2] (regular lit + market).
    The book is returned as a sorted structure suitable for walking in price
    priority when an aggressive order arrives.

    Returns:
        dict with keys 'buy' and 'sell', each a list of dicts sorted by
        (price desc for buy / price asc for sell, then effective_timestamp asc).
        Each entry: {orderid, price, effective_timestamp, sequence, quantity,
                     participantid, crossingkey, ordertype, remaining_qty}
    """
    LIT_ORDER_TYPES = {0, 2}
    if lit_orders_raw is None or len(lit_orders_raw) == 0:
        return {'buy': [], 'sell': []}

    book_df = lit_orders_raw[
        (lit_orders_raw[ORDER_TYPE_COLUMN].isin(LIT_ORDER_TYPES)) &
        (lit_orders_raw['orderbookid'] == orderbookid) &
        (lit_orders_raw['orderstatus'] == 1)
    ].copy()

    if len(book_df) == 0:
        return {'buy': [], 'sell': []}

    # Compute effective timestamp
    book_df['effective_timestamp'] = book_df.apply(
        _get_effective_timestamp, axis=1
    ).astype('int64')
    book_df['quantity'] = book_df['quantity'].fillna(0).astype('int64')
    book_df['price'] = book_df['price'].fillna(0).astype('int64')
    book_df['participantid'] = book_df['participantid'].fillna(0).astype('int64')
    book_df['crossingkey'] = book_df['crossingkey'].fillna(0).astype('int64')
    book_df['ordertype'] = book_df.get('ordertype', 1).fillna(1).astype('int64')

    buy_side = book_df[book_df['side'] == 1].sort_values(
        ['price', 'effective_timestamp', 'sequence'],
        ascending=[False, True, True]
    )
    sell_side = book_df[book_df['side'] == 2].sort_values(
        ['price', 'effective_timestamp', 'sequence'],
        ascending=[True, True, True]
    )

    def _to_records(df):
        return [
            {
                'orderid': int(r['orderid']),
                'price': int(r['price']),
                'effective_timestamp': int(r['effective_timestamp']),
                'sequence': int(r.get('sequence', 0)),
                'quantity': int(r['quantity']),
                'participantid': int(r['participantid']),
                'crossingkey': int(r['crossingkey']),
                'ordertype': int(r['ordertype']),
                'remaining_qty': int(r['quantity']),
            }
            for _, r in df.iterrows()
        ]

    return {'buy': _to_records(buy_side), 'sell': _to_records(sell_side)}


def simulate_resting_phase(
    sweep_orders,
    remainder_df,
    all_cp_orders,
    lit_orders_raw,
    nbbo_data,
    session_states_df,
    tick_size_override=None,
    tick_size_table=None,
    participants_dict=None,
):
    """
    Phase 2: Simulate passive resting of sweep orders in Centre Point (dark)
    and/or ASX TradeMatch (lit) after the aggressive Phase 1.

    Controlled entirely by config flags:
        cfg.SIMULATE_RESTING_PHASE      — master switch (caller should check)
        cfg.SIMULATE_LIT_RESTING        — include lit venue
        cfg.RESTING_LIT_BOOK_MODE       — 'full' (Option A) or 'scan' (Option B)
        cfg.RESTING_USE_MIDTICK         — half-tick dark resting price
        cfg.RESTING_LIT_USE_LIMIT       — lit resting price = limit (spec)
        cfg.RESTING_MODEL_CANCELLATION  — expire at session end
        cfg.RESTING_APPLY_CROSSING_KEYS — crossing key validation
        cfg.RESTING_APPLY_SESSION_FILTER— session state filter
        cfg.RESTING_APPLY_MAQ           — MAQ validation
        cfg.RESTING_APPLY_PREFERENCING  — same-participant preferencing
        cfg.RESTING_APPLY_ICEBERG       — iceberg shown qty

    Args:
        sweep_orders:    Prepared sweep orders DataFrame (from _prepare_sweep_orders)
        remainder_df:    DataFrame with columns [orderid, rest_entry_time,
                         remaining_qty, limit_price, side, orderbookid, midtick,
                         timevaliditydecoded] — one row per sweep order with
                         remaining quantity after Phase 1
        all_cp_orders:   All CP orders (same as Phase 1 all_orders) — contra pool
                         for dark leg
        lit_orders_raw:  Raw orders DataFrame including types [0, 2] — contra pool
                         for lit leg (may be None if SIMULATE_LIT_RESTING=False)
        nbbo_data:       NBBO DataFrame or None
        session_states_df: Session state DataFrame or None
        tick_size_override: Tick size override (int) or None
        tick_size_table: Tick size table from reference data or None
        participants_dict: Participant info dict or None

    Returns:
        dict with keys:
            'resting_trades'  — DataFrame of all Phase 2 simulated trades
            'resting_summary' — DataFrame with per-order fill summary
    """
    nbbo_source = cfg.NBBO_SOURCE

    resting_trades = []
    resting_summaries = []
    row_counter = 1
    match_counter = 0
    base_matchgroupid = 7904794001999000001

    # Pre-build NBBO arrays for fast lookup
    if nbbo_source == 'EXTERNAL' and nbbo_data is not None and len(nbbo_data) > 0:
        _nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
        _nbbo_ts  = _nbbo_sorted['timestamp'].to_numpy()
        _nbbo_bid = _nbbo_sorted[col.orders.bid].to_numpy()
        _nbbo_ask = _nbbo_sorted[col.orders.offer].to_numpy()
    else:
        _nbbo_ts = _nbbo_bid = _nbbo_ask = None

    # Pre-index CP contra orders by (orderbookid, side) for fast lookup
    cp_index = {}
    for side_val in [1, 2]:
        for ob_id in all_cp_orders[col.common.orderbookid].unique():
            mask = (
                (all_cp_orders[col.common.orderbookid] == ob_id) &
                (all_cp_orders[col.common.side] == side_val)
            )
            cp_index[(int(ob_id), int(side_val))] = all_cp_orders[mask].copy()

    # Build lit order book per security (Option A) or index (Option B)
    lit_books = {}   # orderbookid → {'buy': [...], 'sell': [...]}  (Option A)
    lit_index = {}   # (orderbookid, side) → DataFrame              (Option B)
    if cfg.SIMULATE_LIT_RESTING and lit_orders_raw is not None:
        ob_ids = remainder_df['orderbookid'].unique()
        for ob_id in ob_ids:
            if cfg.RESTING_LIT_BOOK_MODE == 'full':
                lit_books[int(ob_id)] = _build_lit_order_book(lit_orders_raw, ob_id)
            else:
                LIT_TYPES = {0, 2}
                for side_val in [1, 2]:
                    mask = (
                        (lit_orders_raw[ORDER_TYPE_COLUMN].isin(LIT_TYPES)) &
                        (lit_orders_raw['orderbookid'] == ob_id) &
                        (lit_orders_raw['side'] == side_val) &
                        (lit_orders_raw['orderstatus'] == 1)
                    )
                    sub = lit_orders_raw[mask].copy()
                    if 'effective_timestamp' not in sub.columns:
                        _r = sub.apply(_get_effective_timestamp, axis=1)
                        if isinstance(_r, pd.DataFrame):
                            _r = _r.squeeze(axis=1) if _r.shape[1] == 1 else pd.Series(dtype='int64')
                        sub['effective_timestamp'] = _r.astype('int64')
                    lit_index[(int(ob_id), int(side_val))] = sub.sort_values(
                        ['effective_timestamp', 'sequence']
                    )

    # Track remaining qty per CP contra order across all resting sweeps
    cp_order_remaining = {
        int(oid): int(qty)
        for oid, qty in zip(
            all_cp_orders[col.common.orderid].values,
            all_cp_orders[col.common.quantity].values,
        )
    }
    # Iceberg slice tracking for Phase 2 (CP dark and lit-scan legs, keyed by order_id)
    p2_iceberg_slice_consumed: dict = {}
    # Track remaining qty for lit book entries (Option A)
    lit_remaining = {}   # orderid → remaining qty

    if len(sweep_orders) > 0:
        first_ts = sweep_orders[col.common.timestamp].iloc[0]
        tradedate = pd.to_datetime(first_ts, unit='ns').strftime('%Y-%m-%d')
    else:
        tradedate = None

    # ── DARK LEG — inverted loop: contra-centric with preferencing ───────────
    # Per §24.10: an incoming order routes to same-participant resting orders
    # first, then FIFO.  We must iterate over incoming contras in time order
    # and, for each, select the best eligible resting sweep.
    #
    # Sweep state is tracked in a dict so the lit leg (below) can read final
    # dark fill counts.

    # Build per-sweep state from remainder_df
    sweep_state = {}
    for _, rem in remainder_df.iterrows():
        sid = int(rem['orderid'])
        ts_rem = int(rem['rest_entry_time'])
        if cfg.RESTING_MODEL_CANCELLATION:
            exp = _get_session_end_time(session_states_df, ts_rem)
        else:
            exp = int(2**62)
        lp  = int(rem['limit_price'])
        sv  = int(rem['side'])
        tck = _calculate_tick_size(0, 0, tick_size_override, tick_size_table, lp)
        sweep_state[sid] = {
            'rest_entry_time':   ts_rem,
            'expiry_time':       exp,
            'remaining_qty':     int(rem['remaining_qty']),
            'limit_price':       lp,
            'side':              sv,
            'orderbookid':       int(rem['orderbookid']),
            'participantid':     int(rem.get('participantid', 0)),
            'crossingkey':       int(rem.get('crossingkey', 0)),
            'minimumquantity':   int(rem.get('minimumquantity', 0)),
            'singlefillminimumquantity': int(rem.get('singlefillminimumquantity', 0)),
            'preferenceonly':    int(rem.get('preferenceonly', 0)),
            'dark_resting_price': _calc_resting_price(lp, tck, sv),
            'lit_resting_price':  _calc_lit_resting_price(lp, tck, sv),
            'dark_filled':   0,
            'dark_matches':  0,
            'lit_filled':    0,
            'lit_matches':   0,
        }

    # Collect all CP contra orders across all relevant (orderbookid, side) pairs,
    # sort globally by time.
    all_ob_sides = {(st['orderbookid'], 2 if st['side'] == 1 else 1)
                    for st in sweep_state.values()}
    all_contra_frames = [
        cp_index.get(key, pd.DataFrame()) for key in all_ob_sides
        if len(cp_index.get(key, pd.DataFrame())) > 0
    ]
    if all_contra_frames:
        all_cp_contras = pd.concat(all_contra_frames).sort_values(
            ['effective_timestamp', 'sequence']
        ).reset_index(drop=True)
    else:
        all_cp_contras = pd.DataFrame()

    # Build a min-heap for Phase 2 dark-leg contra iteration so that iceberg
    # contras can be repositioned after slice exhaustion (bi.txt §27).
    # Elements: (effective_timestamp, sequence, counter, contra_dict)
    _p2_heap: list = []
    _p2_counter = 0
    for _, _c_row in all_cp_contras.iterrows():
        heapq.heappush(_p2_heap, (
            int(_c_row['effective_timestamp']),
            int(_c_row['sequence']),
            _p2_counter,
            _c_row.to_dict(),
        ))
        _p2_counter += 1

    while _p2_heap:
        _, _, _, contra = heapq.heappop(_p2_heap)
        contra_ts          = int(contra['effective_timestamp'])
        contra_id          = int(contra[col.common.orderid])
        contra_ob          = int(contra[col.common.orderbookid])
        contra_side_val    = int(contra[col.common.side])
        contra_participant = int(contra.get('participantid', 0))
        contra_crossing_key = int(contra.get('crossingkey', 0))

        contra_avail = cp_order_remaining.get(contra_id, 0)
        if contra_avail <= 0:
            continue

        if cfg.RESTING_APPLY_ICEBERG:
            contra_avail = min(
                contra_avail,
                _get_iceberg_available_qty(contra, p2_iceberg_slice_consumed.get(contra_id, 0)),
            )
        if contra_avail <= 0:
            continue

        if cfg.RESTING_APPLY_SESSION_FILTER:
            if not _is_valid_trading_session(contra_ts, session_states_df):
                continue

        # NBBO at match time (same as Phase 1)
        if nbbo_source == 'EXTERNAL' and _nbbo_ts is not None:
            _idx = int(np.searchsorted(_nbbo_ts, contra_ts, side='right')) - 1
            nbbo_bid   = int(_nbbo_bid[_idx]) if _idx >= 0 else 0
            nbbo_offer = int(_nbbo_ask[_idx]) if _idx >= 0 else 0
            # If external NBBO unavailable, fall back to order snapshot fields.
            if nbbo_bid <= 0 or nbbo_offer <= 0:
                nb = int(contra.get(col.orders.national_bid, INT64_SENTINEL))
                no = int(contra.get(col.orders.national_offer, INT64_SENTINEL))
                if nb != INT64_SENTINEL and no != INT64_SENTINEL and nb > 0 and no > 0:
                    nbbo_bid, nbbo_offer = nb, no
                else:
                    nbbo_bid = int(contra.get(col.orders.bid, 0))
                    nbbo_offer = int(contra.get(col.orders.offer, 0))
        else:
            nbbo_bid   = int(contra.get(col.orders.national_bid, INT64_SENTINEL))
            nbbo_offer = int(contra.get(col.orders.national_offer, INT64_SENTINEL))
            # Sentinel means unavailable — fall back to order book bid/offer snapshot.
            if nbbo_bid == INT64_SENTINEL or nbbo_offer == INT64_SENTINEL:
                nbbo_bid   = int(contra.get(col.orders.bid, 0))
                nbbo_offer = int(contra.get(col.orders.offer, 0))

        if nbbo_bid <= 0 or nbbo_offer <= 0:
            continue

        midpoint = (nbbo_bid + nbbo_offer) / 2.0

        # Find eligible resting sweeps for this contra
        eligible = [
            (sid, st) for sid, st in sweep_state.items()
            if st['orderbookid'] == contra_ob
            and st['side'] != contra_side_val        # opposite sides
            and st['rest_entry_time'] < contra_ts
            and contra_ts < st['expiry_time']
            and st['remaining_qty'] > 0
        ]
        if not eligible:
            continue

        # §24.10 Preferencing: same-participant resting sweeps first (only if that
        # sweep has preferenceonly=1, i.e. the participant opted in), then FIFO.
        if cfg.RESTING_APPLY_PREFERENCING and contra_participant > 0:
            preferred = sorted(
                [
                    (sid, st) for sid, st in eligible
                    if st['participantid'] == contra_participant
                    and st['preferenceonly'] == 1
                ],
                key=lambda x: x[1]['rest_entry_time']
            )
            others = sorted(
                [
                    (sid, st) for sid, st in eligible
                    if not (st['participantid'] == contra_participant
                            and st['preferenceonly'] == 1)
                ],
                key=lambda x: x[1]['rest_entry_time']
            )
            sorted_sweeps = preferred + others
        else:
            sorted_sweeps = sorted(eligible, key=lambda x: x[1]['rest_entry_time'])

        contra_midtick = int(contra.get('midtick', MIDTICK_NO))

        for sweep_id, st in sorted_sweeps:
            if contra_avail <= 0:
                break

            sweep_side    = st['side']
            dark_rp       = st['dark_resting_price']
            sweep_maq     = st['minimumquantity']
            sweep_sfmaq   = st['singlefillminimumquantity']
            dark_filled_so_far = st['dark_filled']

            # Midpoint check against this sweep's resting price
            if sweep_side == 1:
                if midpoint > dark_rp:
                    continue
            else:
                if midpoint < dark_rp:
                    continue

            if cfg.RESTING_APPLY_CROSSING_KEYS:
                sweep_participant  = st['participantid']
                sweep_crossing_key = st['crossingkey']
                if sweep_participant > 0 and sweep_participant == contra_participant:
                    if sweep_crossing_key == 0 or contra_crossing_key == 0:
                        continue
                    if sweep_crossing_key != contra_crossing_key:
                        continue

            potential_qty = min(st['remaining_qty'], contra_avail)

            if cfg.RESTING_APPLY_MAQ and sweep_maq > 0:
                if sweep_sfmaq == 1:
                    if potential_qty < sweep_maq:
                        continue
                else:
                    if st['remaining_qty'] < sweep_maq and dark_filled_so_far > 0:
                        continue
                    if potential_qty < sweep_maq and dark_filled_so_far == 0:
                        continue

            match_qty = potential_qty
            execution_price = _apply_midtick_improvement(
                midpoint, nbbo_bid, nbbo_offer, sweep_side,
                max(MIDTICK_YES if cfg.RESTING_USE_MIDTICK else MIDTICK_NO, contra_midtick),
                tick_size_override=tick_size_override,
                tick_size_table=tick_size_table,
                price=midpoint,
            )

            match_timestamp = _add_execution_delay(contra_ts)
            matchgroupid = base_matchgroupid + match_counter
            match_counter += 1

            contra_participant_type = _get_participant_type(contra_participant, participants_dict)

            # Use preference match type when the sweep opted into preferencing
            # and the contra order is from the same participant (bi.txt §24.10).
            dark_match_type = (
                'RESTING_DARK_PREF'
                if st['preferenceonly'] == 1
                   and contra_participant > 0
                   and st['participantid'] == contra_participant
                else 'RESTING_DARK'
            )

            _append_resting_trade(
                resting_trades, row_counter, tradedate, match_timestamp,
                contra_ob, sweep_id, matchgroupid, nbbo_bid, nbbo_offer,
                execution_price, match_qty, sweep_side, 0,
                dark_match_type, contra_participant_type, st['rest_entry_time'],
            )
            row_counter += 1
            _append_resting_trade(
                resting_trades, row_counter, tradedate, match_timestamp,
                contra_ob, contra_id, matchgroupid, nbbo_bid, nbbo_offer,
                execution_price, match_qty, contra_side_val, 1,
                dark_match_type, contra_participant_type, st['rest_entry_time'],
            )
            row_counter += 1

            st['remaining_qty'] -= match_qty
            st['dark_filled']   += match_qty
            st['dark_matches']  += 1
            contra_avail        -= match_qty
            cp_order_remaining[contra_id] = cp_order_remaining.get(contra_id, 0) - match_qty
            # Update iceberg slice tracking; on exhaustion, reposition contra to back of queue.
            _p2_dq = contra.get('display_quantity', None)
            if _p2_dq is not None and not pd.isna(_p2_dq):
                _p2_dq = int(_p2_dq)
                _p2_new = p2_iceberg_slice_consumed.get(contra_id, 0) + match_qty
                if _p2_new >= _p2_dq:
                    # Slice exhausted — reset and reposition contra to back of time-priority queue.
                    p2_iceberg_slice_consumed[contra_id] = _p2_new - _p2_dq
                    if cp_order_remaining.get(contra_id, 0) > 0:
                        heapq.heappush(_p2_heap, (
                            contra_ts,
                            _p2_counter,
                            _p2_counter,
                            contra,
                        ))
                        _p2_counter += 1
                    break  # exit inner sorted_sweeps loop; contra re-enters heap at updated priority
                else:
                    p2_iceberg_slice_consumed[contra_id] = _p2_new

    # ── LIT LEG — per-sweep loop (lit contras are not shared across sweeps) ──
    for _, rem in remainder_df.sort_values('rest_entry_time').iterrows():
        sweep_id = int(rem['orderid'])
        st = sweep_state[sweep_id]

        remaining_qty = st['remaining_qty']
        sweep_side    = st['side']
        ob_id         = st['orderbookid']
        rest_entry_time = st['rest_entry_time']
        expiry_time     = st['expiry_time']
        lit_resting_price = st['lit_resting_price']
        sweep_participant  = st['participantid']
        sweep_crossing_key = st['crossingkey']
        contra_side = 2 if sweep_side == 1 else 1

        # ── LIT LEG (ASX TradeMatch passive) ─────────────────────────────────
        if cfg.SIMULATE_LIT_RESTING and remaining_qty > 0:

            if cfg.RESTING_LIT_BOOK_MODE == 'full':
                # Option A: price-time priority lit book.
                # Our sweep is RESTING — contra orders are on the opposite side.
                # BUY sweep resting → contra SELL orders must have price <= our limit.
                # SELL sweep resting → contra BUY orders must have price >= our limit.
                # Sell side sorted price ASC (cheapest first); buy side price DESC (highest first).
                # NOTE: this models contra-order price crossings correctly but does not
                # yet deduct competing same-side orders at better prices — a future enhancement.
                book = lit_books.get(ob_id, {})
                contra_book_key = 'sell' if sweep_side == 1 else 'buy'
                contra_side_book = book.get(contra_book_key, [])

                for entry in contra_side_book:
                    if remaining_qty <= 0:
                        break

                    entry_ts = entry['effective_timestamp']
                    if entry_ts <= rest_entry_time:
                        continue
                    if entry_ts >= expiry_time:
                        break

                    entry_qty = lit_remaining.get(entry['orderid'], entry['remaining_qty'])
                    if entry_qty <= 0:
                        continue

                    if cfg.RESTING_APPLY_SESSION_FILTER:
                        if not _is_valid_trading_session(entry_ts, session_states_df):
                            continue

                    if cfg.RESTING_APPLY_CROSSING_KEYS:
                        contra_participant = entry['participantid']
                        contra_crossing_key = entry['crossingkey']
                        if sweep_participant > 0 and sweep_participant == contra_participant:
                            if sweep_crossing_key == 0 or contra_crossing_key == 0:
                                continue
                            if sweep_crossing_key != contra_crossing_key:
                                continue

                    # Price check (lit): contra price must cross our resting limit
                    entry_price = entry['price']
                    if sweep_side == 1:   # BUY resting — contra sell price <= our limit
                        if entry_price > lit_resting_price:
                            continue
                    else:                  # SELL resting — contra buy price >= our limit
                        if entry_price < lit_resting_price:
                            continue

                    match_qty = min(remaining_qty, entry_qty)
                    execution_price = lit_resting_price
                    match_timestamp = _add_execution_delay(entry_ts)
                    matchgroupid = base_matchgroupid + match_counter
                    match_counter += 1

                    contra_participant_type = _get_participant_type(
                        entry['participantid'], participants_dict
                    )

                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, sweep_id, matchgroupid, 0, 0,
                        execution_price, match_qty, sweep_side, 0,
                        'RESTING_LIT', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1
                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, entry['orderid'], matchgroupid, 0, 0,
                        execution_price, match_qty, contra_side, 1,
                        'RESTING_LIT', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1

                    remaining_qty -= match_qty
                    lit_remaining[entry['orderid']] = entry_qty - match_qty
                    st['lit_filled']  += match_qty
                    st['lit_matches'] += 1

            else:
                # Option B: flat scan — same structure as dark leg but with lit orders
                contra_lit = lit_index.get((ob_id, contra_side), pd.DataFrame())

                for _, contra in contra_lit.iterrows():
                    if remaining_qty <= 0:
                        break

                    contra_ts = int(contra.get('effective_timestamp',
                                               contra.get('timestamp', 0)))
                    if contra_ts <= rest_entry_time:
                        continue
                    if contra_ts >= expiry_time:
                        break

                    if cfg.RESTING_APPLY_SESSION_FILTER:
                        if not _is_valid_trading_session(contra_ts, session_states_df):
                            continue

                    if cfg.RESTING_APPLY_CROSSING_KEYS:
                        contra_participant = int(contra.get('participantid', 0))
                        contra_crossing_key = int(contra.get('crossingkey', 0))
                        if sweep_participant > 0 and sweep_participant == contra_participant:
                            if sweep_crossing_key == 0 or contra_crossing_key == 0:
                                continue
                            if sweep_crossing_key != contra_crossing_key:
                                continue

                    contra_id_lit = int(contra.get('orderid', 0))
                    contra_avail = int(contra.get('quantity', 0))
                    if cfg.RESTING_APPLY_ICEBERG:
                        contra_avail = min(
                            contra_avail,
                            _get_iceberg_available_qty(
                                contra, p2_iceberg_slice_consumed.get(contra_id_lit, 0)
                            ),
                        )
                    if contra_avail <= 0:
                        continue

                    contra_price = int(contra.get('price', 0))
                    if sweep_side == 1:
                        if contra_price > lit_resting_price:
                            continue
                    else:
                        if contra_price < lit_resting_price:
                            continue

                    match_qty = min(remaining_qty, contra_avail)
                    execution_price = lit_resting_price
                    match_timestamp = _add_execution_delay(contra_ts)
                    matchgroupid = base_matchgroupid + match_counter
                    match_counter += 1

                    contra_participant_type = _get_participant_type(
                        int(contra.get('participantid', 0)), participants_dict
                    )

                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, sweep_id, matchgroupid, 0, 0,
                        execution_price, match_qty, sweep_side, 0,
                        'RESTING_LIT_SCAN', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1
                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, contra_id_lit, matchgroupid, 0, 0,
                        execution_price, match_qty, contra_side, 1,
                        'RESTING_LIT_SCAN', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1

                    remaining_qty -= match_qty
                    st['lit_filled']  += match_qty
                    st['lit_matches'] += 1
                    # Update iceberg slice tracking for this lit contra.
                    _lit_dq = contra.get('display_quantity', None)
                    if _lit_dq is not None and not pd.isna(_lit_dq):
                        _lit_dq = int(_lit_dq)
                        _lit_new = p2_iceberg_slice_consumed.get(contra_id_lit, 0) + match_qty
                        p2_iceberg_slice_consumed[contra_id_lit] = (
                            _lit_new - _lit_dq if _lit_new >= _lit_dq else _lit_new
                        )

        resting_summaries.append(_make_resting_summary(
            sweep_id, rest_entry_time, ob_id, sweep_side,
            st['dark_filled'], st['dark_matches'],
            st['lit_filled'],  st['lit_matches'],
        ))

    # Build output DataFrames
    if resting_trades:
        resting_trades_df = pd.DataFrame(resting_trades)
        int_cols = [
            'EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
            'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
            'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
            'participantid', 'passiveaggressive', 'row_num', 'rest_entry_time',
        ]
        for c in int_cols:
            if c in resting_trades_df.columns:
                resting_trades_df[c] = resting_trades_df[c].astype('int64')
    else:
        resting_trades_df = pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
            'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
            'tradeprice', 'quantity', 'side', 'participantid',
            'passiveaggressive', 'row_num', 'match_type',
            'contra_participant_type', 'rest_entry_time',
            'resting_duration_sec', 'phase', 'venue',
        ])

    resting_summary_df = pd.DataFrame(resting_summaries)
    return {
        'resting_trades': resting_trades_df,
        'resting_summary': resting_summary_df,
    }


def _get_deal_source(match_type):
    """Map internal match_type to ASX deal source code (dd.txt §1144-1187)."""
    if match_type in ('RESTING_LIT', 'RESTING_LIT_SCAN'):
        return DEALSOURCE_CONTINUOUS        # 1 — lit continuous matching
    if match_type == 'RESTING_DARK_PREF':
        return DEALSOURCE_PREFERENCE        # 46 — Centre Point preference matched
    if match_type == 'BLOCK_PREF':
        return DEALSOURCE_PREFERENCE_BLOCK  # 51 — preference Any Price Block
    if match_type == 'BLOCK':
        return DEALSOURCE_BLOCK             # 50 — Any Price Block
    return DEALSOURCE_CENTREPOINT           # 47 — Centre Point dark pool


def _append_resting_trade(
    trades_list, row_num, tradedate, tradetime, securitycode, orderid,
    matchgroupid, nbbo_bid, nbbo_offer, execution_price, quantity,
    side, passiveaggressive, match_type, contra_participant_type,
    rest_entry_time,
):
    """Append a single resting-phase trade row."""
    phase = 2
    venue = 'dark' if 'DARK' in match_type else 'lit'
    resting_duration_sec = (tradetime - rest_entry_time) / 1e9 if rest_entry_time > 0 else 0.0
    trades_list.append({
        'EXCHANGE': 3,
        'sequence': row_num,
        'tradedate': tradedate,
        'tradetime': tradetime,
        'securitycode': securitycode,
        'orderid': orderid,
        'dealsource': _get_deal_source(match_type),
        'exchangeinfo': '',
        'matchgroupid': matchgroupid,
        'nationalbidpricesnapshot': nbbo_bid,
        'nationalofferpricesnapshot': nbbo_offer,
        'tradeprice': int(execution_price),
        'quantity': int(quantity),
        'side': side,
        'participantid': 0,
        'passiveaggressive': passiveaggressive,
        'row_num': row_num,
        'match_type': match_type,
        'contra_participant_type': contra_participant_type,
        'rest_entry_time': rest_entry_time,
        'resting_duration_sec': resting_duration_sec,
        'phase': phase,
        'venue': venue,
    })


def _make_resting_summary(
    orderid, rest_entry_time, orderbookid, side,
    dark_filled, dark_matches, lit_filled, lit_matches,
):
    """Build a resting phase summary row for one sweep order."""
    total_filled = dark_filled + lit_filled
    return {
        'orderid': orderid,
        'rest_entry_time': rest_entry_time,
        'orderbookid': orderbookid,
        'side': side,
        'dark_filled_qty': dark_filled,
        'dark_num_matches': dark_matches,
        'lit_filled_qty': lit_filled,
        'lit_num_matches': lit_matches,
        'total_resting_filled_qty': total_filled,
    }


def build_remainder_df(sweep_orders, sweep_usage):
    """
    Build remainder_df from Phase 1 results — one row per sweep order with
    unfilled quantity and the timestamp at which it enters the resting queue.

    rest_entry_time = last_execution_time of the sweep order (i.e. when the
    aggressive phase ended and the order began resting).

    Args:
        sweep_orders: Prepared sweep orders DataFrame
        sweep_usage:  Dict {orderid: {'matched_quantity': ..., 'num_matches': ...}}

    Returns:
        DataFrame with columns needed by simulate_resting_phase
    """
    rows = []
    for _, sweep in sweep_orders.iterrows():
        sweep_id = int(sweep[col.common.orderid])
        available_qty = int(sweep[col.orders.leaves_quantity])
        usage = sweep_usage.get(sweep_id, {'matched_quantity': 0})
        phase1_filled = int(usage['matched_quantity'])
        remaining = available_qty - phase1_filled
        if remaining <= 0:
            continue
        rows.append({
            'orderid': sweep_id,
            'rest_entry_time': int(sweep.get('last_execution_time', sweep[col.common.timestamp])),
            'remaining_qty': remaining,
            'limit_price': int(sweep[col.common.price]),
            'side': int(sweep[col.common.side]),
            'orderbookid': int(sweep[col.common.orderbookid]),
            'midtick': int(sweep.get('midtick', MIDTICK_NO)),
            'timevaliditydecoded': sweep.get('timevaliditydecoded', 'Rest of Day'),
            'participantid': int(sweep.get('participantid', 0)),
            'crossingkey': int(sweep.get('crossingkey', 0)),
            'minimumquantity': int(sweep.get('minimumquantity', 0)),
            'singlefillminimumquantity': int(sweep.get('singlefillminimumquantity', 0)),
            'preferenceonly': int(sweep.get('preferenceonly', 0)),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'orderid', 'rest_entry_time', 'remaining_qty', 'limit_price',
        'side', 'orderbookid', 'midtick', 'timevaliditydecoded',
        'participantid', 'crossingkey', 'minimumquantity',
        'singlefillminimumquantity',
    ])


def _generate_sweep_utilization(sweep_orders, sweep_usage):
    """Generate utilization report for sweep orders."""
    utilization = []
    for _, sweep in sweep_orders.iterrows():
        sweep_id = int(sweep[col.common.orderid])
        available_qty = sweep[col.orders.leaves_quantity]
        if sweep_id not in sweep_usage:
            continue
        usage = sweep_usage[sweep_id]
        matched_qty = usage['matched_quantity']
        utilization.append({
            'orderid': sweep_id,
            'leavesquantity': available_qty,
            'matched_quantity': matched_qty,
            'remaining_quantity': available_qty - matched_qty,
            'utilization_ratio': matched_qty / available_qty if available_qty > 0 else 0,
            'num_matches': usage['num_matches']
        })
    return pd.DataFrame(utilization)
