"""Pipeline configuration and CLI argument handling."""

import argparse
from pathlib import Path
import config.config as config
from discovery.security_discovery import SecurityDiscovery
from utils.statistics_layer import StatisticsEngine


def parse_arguments():
    """Parse CLI arguments with config.py fallback."""
    parser = argparse.ArgumentParser(
        description='Centre Point Sweep Order Matching Pipeline - 4 Stage Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Security selection (mutually exclusive)
    security_group = parser.add_mutually_exclusive_group()
    security_group.add_argument('--ticker', type=str, default=None,
                        help=f'Ticker symbol (legacy, default: {config.TICKER})')
    security_group.add_argument('--orderbookid', type=int, default=None,
                        help='OrderbookID to process')
    security_group.add_argument('--auto-discover', action='store_true',
                        help='Auto-discover and process all valid securities for the date')
    
    parser.add_argument('--date', type=str, default=None,
                        help=f'Date in YYYYMMDD format (default: {config.DATE})')
    
    # Discovery options
    parser.add_argument('--list-securities', action='store_true',
                        help='List all available securities for the date and exit')
    parser.add_argument('--list-dates', action='store_true',
                        help='List all available dates in raw data and exit')
    parser.add_argument('--min-orders', type=int, default=config.MIN_ORDERS_THRESHOLD,
                        help=f'Minimum orders threshold for valid security (default: {config.MIN_ORDERS_THRESHOLD})')
    parser.add_argument('--min-trades', type=int, default=config.MIN_TRADES_THRESHOLD,
                        help=f'Minimum trades threshold for valid security (default: {config.MIN_TRADES_THRESHOLD})')
    
    # Pipeline stages
    parser.add_argument('--stage', type=int, choices=[1, 2, 3, 4], action='append',
                        help='Pipeline stage(s) to run (can specify multiple): 1=extraction, 2=simulation, 3=analysis, 4=aggregation')
    
    # Processing options
    parser.add_argument('--parallel', action='store_true',
                        help='Enable parallel processing for Stage 2 (overrides config)')
    parser.add_argument('--sequential', action='store_true',
                        help='Force sequential processing for Stage 2 (overrides config)')
    
    # Statistical testing options
    stats_group = parser.add_mutually_exclusive_group()
    stats_group.add_argument('--enable-stats', action='store_true',
                        help='Enable statistical tests (t-tests, p-values, confidence intervals)')
    stats_group.add_argument('--disable-stats', action='store_true',
                        help='Disable statistical tests (only descriptive statistics)')
    
    return parser.parse_args()


def _handle_list_operations(args, discovery):
    """Handle --list-dates and --list-securities operations."""
    if args.list_dates:
        dates = discovery.get_available_dates()
        print("\nAvailable dates in raw data:")
        if dates:
            for date in dates:
                print(f"  - {date}")
        else:
            print("  No data files found")
        print()
        exit(0)
    
    date = args.date or config.DATE
    
    if args.list_securities:
        if not date:
            print("Error: --list-securities requires --date argument")
            exit(1)
        discovery.print_summary(date)
        exit(0)
    
    return date


def _resolve_stages_to_run(args):
    """Determine which stages to run from CLI args."""
    return args.stage if args.stage else None


def _determine_parallel_mode(args):
    """Determine parallel processing mode from CLI args and config."""
    if args.parallel:
        return True
    elif args.sequential:
        return False
    else:
        return config.ENABLE_PARALLEL_PROCESSING


def _create_stats_engine(args):
    """Create statistics engine based on CLI args and config."""
    if args.enable_stats:
        enable_stats = True
    elif args.disable_stats:
        enable_stats = False
    else:
        enable_stats = config.ENABLE_STATISTICAL_TESTS
    
    return StatisticsEngine(
        enable_stats=enable_stats,
        force_simple=config.FORCE_SIMPLE_STATS
    )


def _select_securities_from_args(args, discovery, date):
    """Select securities to process based on CLI arguments."""
    securities_to_process = []
    
    if args.auto_discover:
        if not date:
            raise ValueError("--auto-discover requires --date argument")
        
        valid_securities = discovery.get_valid_securities(date)
        if not valid_securities:
            raise ValueError(f"No valid securities found for date {date}")
        
        securities_to_process = valid_securities
        print(f"\nAuto-discovered {len(securities_to_process)} valid securities:")
        for sec in securities_to_process:
            print(f"  - {sec}")
    
    elif args.orderbookid:
        if not date:
            raise ValueError("--orderbookid requires --date argument")
        
        all_securities = discovery.discover_securities_for_date(date)
        security = next((s for s in all_securities if s.orderbookid == args.orderbookid), None)
        
        if not security:
            raise ValueError(f"OrderbookID {args.orderbookid} not found for date {date}")
        
        if not (security.in_orders and security.in_trades):
            raise ValueError(f"OrderbookID {args.orderbookid} missing order or trade data")
        
        securities_to_process = [security]
    
    elif args.ticker:
        if not date:
            raise ValueError("--ticker requires --date argument")
        
        orderbookid = discovery.get_orderbookid_from_ticker(args.ticker, date)
        if not orderbookid:
            raise ValueError(f"Ticker {args.ticker} not found for date {date}")
        
        all_securities = discovery.discover_securities_for_date(date)
        security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
        
        if not security:
            raise ValueError(f"Security not found for ticker {args.ticker}")
        
        securities_to_process = [security]
    
    else:
        ticker = config.TICKER
        if not date:
            raise ValueError("Date is required for processing")
        
        orderbookid = discovery.get_orderbookid_from_ticker(ticker, date)
        if orderbookid:
            all_securities = discovery.discover_securities_for_date(date)
            security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
            if security:
                securities_to_process = [security]
            else:
                raise ValueError(f"Security not found for config ticker {ticker}")
        else:
            raise ValueError(f"Config ticker {ticker} not found for date {date}. Use --orderbookid or --ticker")
    
    return securities_to_process


def _build_security_file_mappings(securities, date, stages):
    """Build input file mappings for each security."""
    securities_with_files = []
    
    if stages is None or any(s in [1, 2, 3] for s in stages):
        for security in securities:
            input_files = config.get_input_files(ticker=security.ticker, date=date)
            
            if stages is None or 1 in stages:
                config.validate_input_files(input_files)
            
            securities_with_files.append({
                'security': security,
                'input_files': input_files
            })
    
    return securities_with_files


def build_runtime_config(args):
    """Build runtime config from CLI args and config."""
    discovery = SecurityDiscovery(min_orders=args.min_orders, min_trades=args.min_trades)
    
    date = _handle_list_operations(args, discovery)
    stages = _resolve_stages_to_run(args)
    enable_parallel = _determine_parallel_mode(args)
    stats_engine = _create_stats_engine(args)
    
    securities_to_process = []
    if stages is None or any(s in [1, 2, 3] for s in stages):
        securities_to_process = _select_securities_from_args(args, discovery, date)
    
    if stages and any(s in [1, 2, 3] for s in stages):
        if not securities_to_process:
            raise ValueError("Stages 1-3 require security specification (--ticker, --orderbookid, or --auto-discover)")
    
    securities_with_files = _build_security_file_mappings(securities_to_process, date, stages)
    
    return {
        'date': date,
        'securities': securities_with_files,
        'enable_parallel': enable_parallel,
        'stages': stages,
        'stats_engine': stats_engine,
    }


def setup_directories():
    """Create output directories if they don't exist."""
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
