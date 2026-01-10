"""Pipeline output printing and formatting."""

import config.config as config


def _print_statistics_tier(stats_engine):
    """Print statistics tier information."""
    print(f"\nStatistics Tier: {stats_engine.get_tier_name()}")
    if stats_engine.tier == 2:
        print(f"  ⚠ Using approximate statistics (scipy not available)")
    elif stats_engine.tier == 1:
        print(f"  ℹ Statistical tests disabled (use --enable-stats to enable)")


def _print_security_info(securities):
    """Print security configuration details."""
    if len(securities) == 1:
        sec = securities[0]['security']
        ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
        print(f"  Security:   OrderbookID {sec.orderbookid} ({ticker_str})")
        print(f"              {sec.order_count:,} orders, {sec.trade_count:,} trades")
    else:
        print(f"  Securities: {len(securities)} securities to process")
        for sec_info in securities:
            sec = sec_info['security']
            ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
            print(f"              - OrderbookID {sec.orderbookid} ({ticker_str}): {sec.order_count:,} orders, {sec.trade_count:,} trades")


def _print_system_config():
    """Print system configuration."""
    print(f"\nSystem Configuration:")
    print(config.SYSTEM_CONFIG)


def _print_stage_plan(stages):
    """Print which stages will be executed."""
    if stages:
        print(f"\nStages to run: {', '.join(map(str, stages))}")
    else:
        print(f"\nRunning all stages (1-4)")


def print_pipeline_header(runtime_config):
    """Print pipeline header with configuration details."""
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    
    stats_engine = runtime_config['stats_engine']
    _print_statistics_tier(stats_engine)
    
    stages = runtime_config['stages']
    if stages is None or any(s in [1, 2, 3] for s in stages):
        print("\nRuntime Configuration:")
        print(f"  Date:       {runtime_config['date']}")
        
        securities = runtime_config['securities']
        _print_security_info(securities)
        
        print(f"  Mode:       {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
        _print_system_config()
        
        print(f"\nDirectories:")
        print(f"  Processed:  {config.PROCESSED_DIR}/")
        print(f"  Outputs:    {config.OUTPUTS_DIR}/")
    
    _print_stage_plan(stages)


def _format_partition_breakdown(data):
    """Format partition breakdown for summary."""
    lines = []
    for partition_key in sorted(data['orders'].keys()):
        num_orders = len(data['orders'][partition_key])
        num_trades = len(data['trades'].get(partition_key, [])) if data['trades'] else 0
        lines.append(f"  {partition_key}: {num_orders:,} orders, {num_trades:,} trades")
    return '\n'.join(lines)


def _format_output_directories():
    """Format output directory paths."""
    return (
        f"  Processed data: {config.PROCESSED_DIR}/\n"
        f"  Final outputs:  {config.OUTPUTS_DIR}/\n"
        f"  Aggregated:     {config.AGGREGATED_DIR}/"
    )


def print_execution_summary(data, runtime_config, execution_time):
    """Print pipeline execution summary."""
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUMMARY")
    print("="*80)
    print(f"Pipeline completed successfully")
    print(f"")
    
    print("Configuration:")
    print(f"  Date:    {runtime_config['date']}")
    
    securities = runtime_config.get('securities', [])
    if securities:
        if len(securities) == 1:
            sec = securities[0]['security']
            ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
            print(f"  Security: OrderbookID {sec.orderbookid} ({ticker_str})")
        else:
            print(f"  Securities: {len(securities)} processed")
    
    print(f"  Mode:    {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
    
    stats_engine = runtime_config.get('stats_engine')
    if stats_engine:
        print(f"  Statistics: {stats_engine.get_tier_name()}")
    
    print(f"")
    
    if data:
        total_orders = sum(len(df) for df in data['orders'].values())
        total_trades = sum(len(df) for df in data['trades'].values()) if data['trades'] else 0
        num_partitions = len(data['orders'])
        
        print(f"Total Centre Point Orders: {total_orders:,}")
        print(f"Total Trades: {total_trades:,}")
        print(f"Number of Partitions: {num_partitions}")
    
    print(f"Execution Time: {execution_time:.2f} seconds")
    
    if data:
        print("\nPartition Breakdown:")
        print(_format_partition_breakdown(data))
    
    print("\nOutput files:")
    print(_format_output_directories())
    print("="*80)
