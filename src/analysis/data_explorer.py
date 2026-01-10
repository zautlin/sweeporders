"""
Data Explorer for Raw Order and Trade Files

This module provides comprehensive exploratory analysis of raw order and trade data,
generating statistical summaries, groupings, and insights into market microstructure.

Usage:
    # Standalone
    python src/data_explorer.py --ticker drr --date 20240905
    python src/data_explorer.py --all
    
    # Integrated with main pipeline
    python src/main.py --ticker drr --date 20240905 --explore

Outputs:
    data/exploration/{date}/{orderbookid}/
    ├── 1_order_summary.csv
    ├── 2_order_by_type.csv
    ├── 3_order_by_exchange_type.csv
    ├── 4_order_by_midtick.csv
    ├── 5_order_by_crossing_keys.csv
    ├── 6_trade_summary.csv
    ├── 7_trade_by_deal_source.csv
    ├── 8_execution_analysis.csv
    ├── 9_hourly_patterns.csv
    └── exploration_report.txt
"""

import pandas as pd
from config.column_schema import col
import numpy as np
from pathlib import Path
import logging
from datetime import datetime
from typing import Dict, Tuple, Optional
import argparse
from security_discovery import SecurityDiscovery

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Legacy security mapping (for backward compatibility)
# DEPRECATED: Use auto-discovery instead
LEGACY_SECURITY_MAPPING = {
    '110621': 'DRR',
    '85603': 'WTC',
    '70616': 'BHP',
    '124055': 'CBA'
}

# Reverse mapping
LEGACY_TICKER_TO_ORDERBOOKID = {v: k for k, v in LEGACY_SECURITY_MAPPING.items()}

# Exchange order type mapping (based on ASX documentation)
EXCHANGE_ORDER_TYPE_MAP = {
    0: 'LIMIT',
    2: 'LIMIT_WITH_MIDTICK',
    64: 'CENTRE_POINT_LIMIT',
    2048: 'CENTRE_POINT_MIDPOINT_LIMIT',
    2050: 'CENTRE_POINT_MIDPOINT_LIMIT_WITH_MIDTICK',
    4096: 'CENTRE_POINT_SWEEP_LIMIT',
    4098: 'CENTRE_POINT_SWEEP_LIMIT_WITH_MIDTICK'
}

# Order type mapping
ORDER_TYPE_MAP = {
    0: 'MARKET',
    1: 'LIMIT'
}


def load_orders(ticker: Optional[str], date: str, orderbookid: Optional[int] = None) -> Tuple[pd.DataFrame, str]:
    """Load order data from raw files."""
    if orderbookid is None:
        # Use ticker to find orderbookid
        if ticker is None:
            raise ValueError("Either ticker or orderbookid must be provided")
        
        # Try legacy mapping first
        orderbookid_str = LEGACY_TICKER_TO_ORDERBOOKID.get(ticker.upper())
        
        if not orderbookid_str:
            # Use auto-discovery
            discovery = SecurityDiscovery()
            orderbookid = discovery.get_orderbookid_from_ticker(ticker, date)
            if not orderbookid:
                raise ValueError(f"Unknown ticker: {ticker}")
            orderbookid_str = str(orderbookid)
        
        ticker_lower = ticker.lower()
    else:
        # Use orderbookid to find ticker
        orderbookid_str = str(orderbookid)
        
        # Try legacy mapping first
        ticker_upper = LEGACY_SECURITY_MAPPING.get(orderbookid_str)
        if ticker_upper:
            ticker_lower = ticker_upper.lower()
        else:
            # Use auto-discovery
            discovery = SecurityDiscovery()
            securities = discovery.discover_securities_for_date(date)
            security = next((s for s in securities if s.orderbookid == orderbookid), None)
            if not security or not security.ticker:
                raise ValueError(f"Unknown orderbookid: {orderbookid}")
            ticker_lower = security.ticker.lower()
    
    orders_file = Path(f'data/raw/orders/{ticker_lower}_{date}_orders.csv')
    
    if not orders_file.exists():
        raise FileNotFoundError(f"Orders file not found: {orders_file}")
    
    logger.info(f"Loading orders from {orders_file}")
    df = pd.read_csv(orders_file)
    
    # Convert timestamp to datetime
    df['timestamp_dt'] = pd.to_datetime(df[col.common.timestamp], unit='ns')
    df['hour'] = df['timestamp_dt'].dt.hour
    
    logger.info(f"Loaded {len(df):,} order records")
    return df, orderbookid_str


def load_trades(ticker: Optional[str], date: str, orderbookid: Optional[int] = None) -> pd.DataFrame:
    """Load trade data from raw files."""
    if orderbookid is None and ticker is None:
        raise ValueError("Either ticker or orderbookid must be provided")
    
    if ticker is None:
        # Get ticker from orderbookid
        orderbookid_str = str(orderbookid)
        ticker_upper = LEGACY_SECURITY_MAPPING.get(orderbookid_str)
        if ticker_upper:
            ticker_lower = ticker_upper.lower()
        else:
            discovery = SecurityDiscovery()
            securities = discovery.discover_securities_for_date(date)
            security = next((s for s in securities if s.orderbookid == orderbookid), None)
            if not security or not security.ticker:
                raise ValueError(f"Unknown orderbookid: {orderbookid}")
            ticker_lower = security.ticker.lower()
    else:
        ticker_lower = ticker.lower()
    
    trades_file = Path(f'data/raw/trades/{ticker_lower}_{date}_trades.csv')
    
    if not trades_file.exists():
        raise FileNotFoundError(f"Trades file not found: {trades_file}")
    
    logger.info(f"Loading trades from {trades_file}")
    df = pd.read_csv(trades_file)
    
    # Convert tradetime to datetime
    df['tradetime_dt'] = pd.to_datetime(df['tradetime'], unit='ns')
    df['hour'] = df['tradetime_dt'].dt.hour
    
    logger.info(f"Loaded {len(df):,} trade records")
    return df


def analyze_order_summary(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Generate overall order statistics."""
    logger.info("Generating order summary...")
    
    stats = {
        'metric': [
            'Total Orders',
            'Unique Order IDs',
            'Date Range Start',
            'Date Range End',
            'Securities',
            'Total Order Value (price * quantity)',
            'Average Order Size',
            'Median Order Size',
            'Min Order Size',
            'Max Order Size',
            'Total Quantity',
            'Buy Orders',
            'Sell Orders',
            'Buy Order %',
            'Sell Order %'
        ],
        'value': [
            len(orders_df),
            orders_df[col.common.order_id].nunique(),
            orders_df['timestamp_dt'].min().strftime('%Y-%m-%d %H:%M:%S'),
            orders_df['timestamp_dt'].max().strftime('%Y-%m-%d %H:%M:%S'),
            orders_df['security_code'].nunique(),
            (orders_df[col.common.price] * orders_df[col.common.quantity]).sum(),
            orders_df[col.common.quantity].mean(),
            orders_df[col.common.quantity].median(),
            orders_df[col.common.quantity].min(),
            orders_df[col.common.quantity].max(),
            orders_df[col.common.quantity].sum(),
            len(orders_df[orders_df[col.common.side] == 1]),
            len(orders_df[orders_df[col.common.side] == 2]),
            f"{len(orders_df[orders_df[col.common.side] == 1]) / len(orders_df) * 100:.1f}%",
            f"{len(orders_df[orders_df[col.common.side] == 2]) / len(orders_df) * 100:.1f}%"
        ]
    }
    
    return pd.DataFrame(stats)


def analyze_order_by_type(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Group orders by order type."""
    logger.info("Analyzing orders by type...")
    
    grouped = orders_df.groupby('ordertype').agg({
        'order_id': 'count',
        'quantity': ['mean', 'median', 'sum']
    }).reset_index()
    
    grouped.columns = ['ordertype', 'count', 'avg_qty', 'median_qty', 'total_qty']
    grouped['type_name'] = grouped['ordertype'].map(ORDER_TYPE_MAP).fillna('UNKNOWN')
    grouped['pct'] = (grouped['count'] / grouped['count'].sum() * 100).round(2)
    
    # Reorder columns
    grouped = grouped[['ordertype', 'type_name', 'count', 'pct', 'avg_qty', 'median_qty', 'total_qty']]
    
    return grouped.sort_values('count', ascending=False)


def analyze_order_by_exchange_type(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Group orders by exchange order type."""
    logger.info("Analyzing orders by exchange order type...")
    
    grouped = orders_df.groupby('exchangeordertype').agg({
        'order_id': 'count',
        'quantity': ['mean', 'median', 'sum']
    }).reset_index()
    
    grouped.columns = ['exchangeordertype', 'count', 'avg_qty', 'median_qty', 'total_qty']
    grouped['type_name'] = grouped[col.orders.order_type].map(EXCHANGE_ORDER_TYPE_MAP).fillna('UNKNOWN')
    grouped['pct'] = (grouped['count'] / grouped['count'].sum() * 100).round(2)
    
    # Reorder columns
    grouped = grouped[['exchangeordertype', 'type_name', 'count', 'pct', 'avg_qty', 'median_qty', 'total_qty']]
    
    return grouped.sort_values('count', ascending=False)


def analyze_order_by_midtick(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Group orders by midtick flag."""
    logger.info("Analyzing orders by midtick flag...")
    
    grouped = orders_df.groupby('midtick').agg({
        'order_id': 'count',
        'quantity': ['mean', 'median', 'sum']
    }).reset_index()
    
    grouped.columns = ['midtick', 'count', 'avg_qty', 'median_qty', 'total_qty']
    grouped['midtick_allowed'] = grouped['midtick'].map({0: 'NO', 1: 'YES'})
    grouped['pct'] = (grouped['count'] / grouped['count'].sum() * 100).round(2)
    
    # Reorder columns
    grouped = grouped[['midtick', 'midtick_allowed', 'count', 'pct', 'avg_qty', 'median_qty', 'total_qty']]
    
    return grouped.sort_values('count', ascending=False)


def analyze_order_by_crossing_keys(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Group orders by crossing keys."""
    logger.info("Analyzing orders by crossing keys...")
    
    # Handle NaN crossing keys
    orders_df['crossingkey_clean'] = orders_df['crossingkey'].fillna('NONE')
    
    grouped = orders_df.groupby('crossingkey_clean').agg({
        'order_id': 'count',
        'quantity': ['mean', 'median', 'sum']
    }).reset_index()
    
    grouped.columns = ['crossing_key', 'count', 'avg_qty', 'median_qty', 'total_qty']
    grouped['pct'] = (grouped['count'] / grouped['count'].sum() * 100).round(2)
    
    return grouped.sort_values('count', ascending=False)


def analyze_trade_summary(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Generate overall trade statistics."""
    logger.info("Generating trade summary...")
    
    stats = {
        'metric': [
            'Total Trades',
            'Unique Trade IDs',
            'Date Range Start',
            'Date Range End',
            'Securities',
            'Total Trade Value (price * quantity)',
            'Total Volume Traded',
            'Average Trade Size',
            'Median Trade Size',
            'Min Trade Size',
            'Max Trade Size',
            'Buy Trades',
            'Sell Trades',
            'Buy Trade %',
            'Sell Trade %'
        ],
        'value': [
            len(trades_df),
            trades_df['matchgroupid'].nunique() if 'matchgroupid' in trades_df.columns else 'N/A',
            trades_df['tradetime_dt'].min().strftime('%Y-%m-%d %H:%M:%S'),
            trades_df['tradetime_dt'].max().strftime('%Y-%m-%d %H:%M:%S'),
            trades_df['securitycode'].nunique(),
            (trades_df['tradeprice'] * trades_df[col.common.quantity]).sum(),
            trades_df[col.common.quantity].sum(),
            trades_df[col.common.quantity].mean(),
            trades_df[col.common.quantity].median(),
            trades_df[col.common.quantity].min(),
            trades_df[col.common.quantity].max(),
            len(trades_df[trades_df[col.common.side] == 1]),
            len(trades_df[trades_df[col.common.side] == 2]),
            f"{len(trades_df[trades_df[col.common.side] == 1]) / len(trades_df) * 100:.1f}%",
            f"{len(trades_df[trades_df[col.common.side] == 2]) / len(trades_df) * 100:.1f}%"
        ]
    }
    
    return pd.DataFrame(stats)


def analyze_trade_by_deal_source(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Group trades by deal source."""
    logger.info("Analyzing trades by deal source...")
    
    grouped = trades_df.groupby(['dealsource', 'dealsourcedecoded']).agg({
        'matchgroupid': 'count',
        'quantity': ['sum', 'mean', 'median']
    }).reset_index()
    
    grouped.columns = ['deal_source', 'source_name', 'count', 'total_volume', 'avg_size', 'median_size']
    grouped['pct'] = (grouped['count'] / grouped['count'].sum() * 100).round(2)
    
    # Reorder columns
    grouped = grouped[['deal_source', 'source_name', 'count', 'pct', 'total_volume', 'avg_size', 'median_size']]
    
    return grouped.sort_values('count', ascending=False)


def analyze_execution(orders_df: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    """Analyze order execution rates."""
    logger.info("Analyzing order execution rates...")
    
    # Link orders to trades
    orders_with_trades = orders_df[col.common.order_id].isin(trades_df[col.common.orderid])
    
    # Calculate fill statistics
    order_trade_counts = trades_df.groupby('orderid').size()
    orders_df['trade_count'] = orders_df[col.common.order_id].map(order_trade_counts).fillna(0)
    orders_df['has_trades'] = orders_with_trades
    
    # Overall statistics
    overall_stats = []
    overall_stats.append(['Total Orders', len(orders_df), '100.0%'])
    overall_stats.append(['Orders with Trades', orders_with_trades.sum(), 
                         f"{orders_with_trades.sum() / len(orders_df) * 100:.1f}%"])
    overall_stats.append(['Orders without Trades', (~orders_with_trades).sum(),
                         f"{(~orders_with_trades).sum() / len(orders_df) * 100:.1f}%"])
    
    # Calculate fully vs partially filled
    orders_with_exec = orders_df[orders_df['has_trades']]
    if len(orders_with_exec) > 0:
        fully_filled = (orders_with_exec[col.orders.matched_quantity] >= orders_with_exec[col.common.quantity]).sum()
        partially_filled = len(orders_with_exec) - fully_filled
        
        overall_stats.append(['Fully Filled Orders', fully_filled,
                             f"{fully_filled / len(orders_with_exec) * 100:.1f}%"])
        overall_stats.append(['Partially Filled Orders', partially_filled,
                             f"{partially_filled / len(orders_with_exec) * 100:.1f}%"])
        overall_stats.append(['Avg Trades per Order (executed)', 
                             orders_with_exec['trade_count'].mean(), '-'])
    
    overall_df = pd.DataFrame(overall_stats, columns=['metric', 'value', 'percentage'])
    
    # By exchange order type
    by_exchange_type = orders_df.groupby('exchangeordertype').agg({
        'order_id': 'count',
        'has_trades': 'sum',
        'trade_count': 'mean'
    }).reset_index()
    
    by_exchange_type.columns = ['exchangeordertype', 'total_orders', 'executed_orders', 'avg_trades_per_order']
    by_exchange_type['type_name'] = by_exchange_type[col.orders.order_type].map(EXCHANGE_ORDER_TYPE_MAP).fillna('UNKNOWN')
    by_exchange_type['execution_rate'] = (by_exchange_type['executed_orders'] / by_exchange_type['total_orders'] * 100).round(2)
    
    by_exchange_type = by_exchange_type[['exchangeordertype', 'type_name', 'total_orders', 
                                         'executed_orders', 'execution_rate', 'avg_trades_per_order']]
    by_exchange_type = by_exchange_type.sort_values('execution_rate', ascending=False)
    
    # Combine with section headers
    result = pd.DataFrame()
    result = pd.concat([
        pd.DataFrame([['=== OVERALL STATISTICS ===', '', '']], columns=['metric', 'value', 'percentage']),
        overall_df,
        pd.DataFrame([['', '', '']], columns=['metric', 'value', 'percentage']),
        pd.DataFrame([['=== BY EXCHANGE ORDER TYPE ===', '', '']], columns=['metric', 'value', 'percentage']),
        pd.DataFrame([['exchangeordertype', 'type_name', 'total_orders']], 
                    columns=['metric', 'value', 'percentage']),
        by_exchange_type.rename(columns={'exchangeordertype': 'metric', 'type_name': 'value', 
                                         'total_orders': 'percentage'})
    ], ignore_index=True)
    
    return result


def analyze_hourly_patterns(orders_df: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    """Analyze time-of-day patterns."""
    logger.info("Analyzing hourly patterns...")
    
    # Orders by hour
    orders_by_hour = orders_df.groupby('hour').agg({
        'order_id': 'count',
        'quantity': 'mean'
    }).reset_index()
    orders_by_hour.columns = ['hour', 'order_count', 'avg_order_size']
    
    # Trades by hour
    trades_by_hour = trades_df.groupby('hour').agg({
        'matchgroupid': 'count',
        'quantity': 'mean'
    }).reset_index()
    trades_by_hour.columns = ['hour', 'trade_count', 'avg_trade_size']
    
    # Merge
    hourly = pd.merge(orders_by_hour, trades_by_hour, on='hour', how='outer').fillna(0)
    
    # Calculate execution rate per hour
    hourly['execution_rate'] = (hourly['trade_count'] / hourly['order_count'] * 100).round(2)
    hourly['execution_rate'] = hourly['execution_rate'].fillna(0)
    
    return hourly.sort_values('hour')


def generate_exploration_report(
    ticker: str,
    date: str,
    orderbookid: str,
    order_summary: pd.DataFrame,
    order_by_type: pd.DataFrame,
    order_by_exchange_type: pd.DataFrame,
    trade_summary: pd.DataFrame,
    trade_by_deal_source: pd.DataFrame,
    execution_analysis: pd.DataFrame,
    hourly_patterns: pd.DataFrame,
    output_file: str
) -> None:
    """Generate human-readable exploration report."""
    logger.info(f"Generating exploration report: {output_file}")
    
    with open(output_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("DATA EXPLORATION REPORT\n")
        f.write("=" * 80 + "\n\n")
        
        # Overview
        f.write("OVERVIEW\n")
        f.write("-" * 80 + "\n")
        f.write(f"Ticker: {ticker.upper()}\n")
        f.write(f"Date: {date[:4]}-{date[4:6]}-{date[6:]}\n")
        f.write(f"Orderbook ID: {orderbookid}\n")
        f.write(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        # Order Statistics
        f.write("ORDER STATISTICS\n")
        f.write("-" * 80 + "\n")
        for _, row in order_summary.iterrows():
            f.write(f"{row['metric']}: {row['value']}\n")
        f.write("\n")
        
        # Most common order types
        f.write("Order Type Distribution:\n")
        for _, row in order_by_type.head(5).iterrows():
            f.write(f"  {row['type_name']}: {row['count']:,} orders ({row['pct']}%)\n")
        f.write("\n")
        
        # Exchange order types
        f.write("Exchange Order Type Distribution:\n")
        for _, row in order_by_exchange_type.head(5).iterrows():
            f.write(f"  {row['type_name']}: {row['count']:,} orders ({row['pct']}%)\n")
        f.write("\n")
        
        # Centre Point usage
        cp_types = order_by_exchange_type[order_by_exchange_type['type_name'].str.contains('CENTRE_POINT', na=False)]
        if len(cp_types) > 0:
            cp_total = cp_types['count'].sum()
            cp_pct = cp_types['pct'].sum()
            f.write(f"Centre Point Orders: {cp_total:,} ({cp_pct:.1f}%)\n")
            for _, row in cp_types.iterrows():
                f.write(f"  - {row['type_name']}: {row['count']:,} ({row['pct']}%)\n")
            f.write("\n")
        
        # Trade Statistics
        f.write("TRADE STATISTICS\n")
        f.write("-" * 80 + "\n")
        for _, row in trade_summary.iterrows():
            f.write(f"{row['metric']}: {row['value']}\n")
        f.write("\n")
        
        # Deal source distribution
        f.write("Deal Source Distribution:\n")
        for _, row in trade_by_deal_source.head(10).iterrows():
            f.write(f"  {row['source_name']}: {row['count']:,} trades ({row['pct']}%)\n")
        f.write("\n")
        
        # Execution Analysis
        f.write("EXECUTION ANALYSIS\n")
        f.write("-" * 80 + "\n")
        
        # Extract overall stats
        overall_section = execution_analysis[execution_analysis['metric'].str.contains('===', na=False) == False]
        overall_section = overall_section[overall_section['metric'].str.len() > 0]
        
        for _, row in overall_section.head(10).iterrows():
            if row['percentage'] and str(row['percentage']) != 'nan':
                f.write(f"{row['metric']}: {row['value']} ({row['percentage']})\n")
            else:
                f.write(f"{row['metric']}: {row['value']}\n")
        f.write("\n")
        
        # Temporal Patterns
        f.write("TEMPORAL PATTERNS\n")
        f.write("-" * 80 + "\n")
        
        peak_order_hour = hourly_patterns.loc[hourly_patterns['order_count'].idxmax()]
        peak_trade_hour = hourly_patterns.loc[hourly_patterns['trade_count'].idxmax()]
        peak_exec_hour = hourly_patterns.loc[hourly_patterns['execution_rate'].idxmax()]
        
        f.write(f"Peak Order Hour: {int(peak_order_hour['hour']):02d}:00 ({int(peak_order_hour['order_count']):,} orders)\n")
        f.write(f"Peak Trade Hour: {int(peak_trade_hour['hour']):02d}:00 ({int(peak_trade_hour['trade_count']):,} trades)\n")
        f.write(f"Highest Execution Rate: {int(peak_exec_hour['hour']):02d}:00 ({peak_exec_hour['execution_rate']:.1f}%)\n")
        f.write("\n")
        
        # Market times
        first_hour = hourly_patterns[hourly_patterns['order_count'] > 0]['hour'].min()
        last_hour = hourly_patterns[hourly_patterns['order_count'] > 0]['hour'].max()
        f.write(f"Market Active: {int(first_hour):02d}:00 - {int(last_hour):02d}:00\n")
        f.write(f"Trading Duration: {int(last_hour - first_hour + 1)} hours\n")
        f.write("\n")
        
        # Key Findings
        f.write("KEY FINDINGS\n")
        f.write("-" * 80 + "\n")
        
        findings = []
        
        # Execution rate comparison
        cp_exec = order_by_exchange_type[order_by_exchange_type['type_name'].str.contains('CENTRE_POINT', na=False)]
        if len(cp_exec) > 0:
            # Note: execution rate would need to be calculated properly from execution_analysis
            findings.append("Centre Point order types are being actively used")
        
        # Deal source insights
        top_deal_source = trade_by_deal_source.iloc[0]
        findings.append(f"Most trades execute via {top_deal_source['source_name']} ({top_deal_source['pct']}%)")
        
        # Temporal insights
        findings.append(f"Peak trading activity occurs at {int(peak_order_hour['hour']):02d}:00")
        
        for i, finding in enumerate(findings, 1):
            f.write(f"{i}. {finding}\n")
        
        f.write("\n")
        f.write("=" * 80 + "\n")
        f.write("END OF REPORT\n")
        f.write("=" * 80 + "\n")
    
    logger.info(f"Report written to {output_file}")


def explore_data(ticker: Optional[str], date: str, orderbookid: Optional[int] = None, 
                output_base_dir: str = 'data/exploration'):
    """Main exploration function for a single security. Requires ticker or orderbookid with date."""
    if ticker:
        ticker_display = ticker.upper()
    else:
        ticker_display = f"OrderbookID {orderbookid}"
    
    logger.info("=" * 80)
    logger.info(f"EXPLORING DATA: {ticker_display} - {date}")
    logger.info("=" * 80)
    
    try:
        # Load data
        orders_df, orderbookid_str = load_orders(ticker, date, orderbookid)
        trades_df = load_trades(ticker, date, orderbookid)
        
        # Create output directory
        date_formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        output_dir = Path(output_base_dir) / date_formatted / orderbookid_str
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Output directory: {output_dir}")
        
        # Run analyses
        logger.info("Running analyses...")
        
        order_summary = analyze_order_summary(orders_df)
        order_summary.to_csv(output_dir / '1_order_summary.csv', index=False)
        
        order_by_type = analyze_order_by_type(orders_df)
        order_by_type.to_csv(output_dir / '2_order_by_type.csv', index=False)
        
        order_by_exchange_type = analyze_order_by_exchange_type(orders_df)
        order_by_exchange_type.to_csv(output_dir / '3_order_by_exchange_type.csv', index=False)
        
        order_by_midtick = analyze_order_by_midtick(orders_df)
        order_by_midtick.to_csv(output_dir / '4_order_by_midtick.csv', index=False)
        
        order_by_crossing_keys = analyze_order_by_crossing_keys(orders_df)
        order_by_crossing_keys.to_csv(output_dir / '5_order_by_crossing_keys.csv', index=False)
        
        trade_summary = analyze_trade_summary(trades_df)
        trade_summary.to_csv(output_dir / '6_trade_summary.csv', index=False)
        
        trade_by_deal_source = analyze_trade_by_deal_source(trades_df)
        trade_by_deal_source.to_csv(output_dir / '7_trade_by_deal_source.csv', index=False)
        
        execution_analysis = analyze_execution(orders_df, trades_df)
        execution_analysis.to_csv(output_dir / '8_execution_analysis.csv', index=False)
        
        hourly_patterns = analyze_hourly_patterns(orders_df, trades_df)
        hourly_patterns.to_csv(output_dir / '9_hourly_patterns.csv', index=False)
        
        # Generate report
        generate_exploration_report(
            ticker if ticker else f"OB{orderbookid}", 
            date, 
            orderbookid_str,
            order_summary, order_by_type, order_by_exchange_type,
            trade_summary, trade_by_deal_source, execution_analysis,
            hourly_patterns,
            str(output_dir / 'exploration_report.txt')
        )
        
        logger.info("=" * 80)
        logger.info("EXPLORATION COMPLETE")
        logger.info("=" * 80)
        logger.info(f"✓ Generated 10 output files in {output_dir}")
        
        return True
        
    except Exception as e:
        logger.error(f"Exploration failed: {str(e)}", exc_info=True)
        return False


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Explore raw order and trade data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available dates
  python src/data_explorer.py --list-dates
  
  # List securities for a date
  python src/data_explorer.py --list-securities --date 20240905
  
  # Explore specific ticker/date (legacy)
  python src/data_explorer.py --ticker drr --date 20240905
  
  # Explore specific orderbookid/date
  python src/data_explorer.py --orderbookid 110621 --date 20240905
  
  # Auto-discover and explore all valid securities
  python src/data_explorer.py --auto-discover --date 20240905
        """
    )
    
    # Security selection
    security_group = parser.add_mutually_exclusive_group()
    security_group.add_argument('--ticker', type=str, help='Security ticker (drr, bhp, cba, wtc) - legacy')
    security_group.add_argument('--orderbookid', type=int, help='OrderbookID to explore')
    security_group.add_argument('--auto-discover', action='store_true', 
                               help='Auto-discover and explore all valid securities')
    
    parser.add_argument('--date', type=str, help='Date in YYYYMMDD format')
    parser.add_argument('--output-dir', type=str, default='data/exploration',
                       help='Base output directory (default: data/exploration)')
    
    # Discovery options
    parser.add_argument('--list-dates', action='store_true',
                       help='List all available dates in raw data')
    parser.add_argument('--list-securities', action='store_true',
                       help='List all available securities for the date')
    parser.add_argument('--min-orders', type=int, default=100,
                       help='Minimum orders threshold (default: 100)')
    parser.add_argument('--min-trades', type=int, default=10,
                       help='Minimum trades threshold (default: 10)')
    
    args = parser.parse_args()
    
    # Initialize discovery
    discovery = SecurityDiscovery(min_orders=args.min_orders, min_trades=args.min_trades)
    
    # Handle list operations
    if args.list_dates:
        dates = discovery.get_available_dates()
        print("\nAvailable dates in raw data:")
        if dates:
            for date in dates:
                print(f"  - {date}")
        else:
            print("  No data files found")
        print()
        return
    
    if args.list_securities:
        if not args.date:
            parser.error("--list-securities requires --date")
        discovery.print_summary(args.date)
        return
    
    # Require date for exploration
    if not args.date:
        parser.error("--date is required for exploration")
    
    # Determine which securities to explore
    if args.auto_discover:
        # Auto-discover all valid securities
        securities = discovery.get_valid_securities(args.date)
        if not securities:
            logger.error(f"No valid securities found for date {args.date}")
            return
        
        print(f"\n{'='*80}")
        print(f"AUTO-DISCOVERY: Found {len(securities)} valid securities for {args.date}")
        print(f"{'='*80}\n")
        
        for security in securities:
            print(f"Exploring {security}...")
            try:
                explore_data(
                    ticker=security.ticker,
                    date=args.date,
                    orderbookid=security.orderbookid,
                    output_base_dir=args.output_dir
                )
                print()
            except Exception as e:
                logger.error(f"Failed to explore OrderbookID {security.orderbookid}: {e}")
                continue
    
    elif args.orderbookid:
        # Explore specific orderbookid
        explore_data(
            ticker=None,
            date=args.date,
            orderbookid=args.orderbookid,
            output_base_dir=args.output_dir
        )
    
    elif args.ticker:
        # Legacy ticker-based exploration
        explore_data(
            ticker=args.ticker,
            date=args.date,
            orderbookid=None,
            output_base_dir=args.output_dir
        )
    
    else:
        parser.error("Must specify --ticker, --orderbookid, or --auto-discover")


if __name__ == '__main__':
    main()
