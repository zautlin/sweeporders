"""
Multi-Dataset Pipeline Orchestrator

High-level script to process large datasets with multiple dates and securities.
Coordinates streaming extraction, parallel processing, and aggregation.

Usage:
    python run_multidataset_pipeline.py --orders orders.csv --trades trades.csv --output data/processed
"""

import argparse
from pathlib import Path
import sys
import time

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.partition_registry import PartitionRegistry, PartitionStatus
from src.streaming_extractor import StreamingExtractor
from src.parallel_engine import ParallelProcessingEngine
from src.system_config import detect_system_config, print_system_info


def main():
    parser = argparse.ArgumentParser(description='Multi-dataset sweep order analysis pipeline')
    parser.add_argument('--orders', type=str, required=True, help='Path to orders CSV file')
    parser.add_argument('--trades', type=str, required=True, help='Path to trades CSV file')
    parser.add_argument('--output', type=str, required=True, help='Output directory')
    parser.add_argument('--workers', type=int, help='Number of parallel workers (auto-detect if not specified)')
    parser.add_argument('--chunk-size', type=int, help='Chunk size for streaming (auto-detect if not specified)')
    parser.add_argument('--resume', action='store_true', help='Resume from existing registry')
    parser.add_argument('--retry', action='store_true', default=True, help='Retry failed partitions')
    parser.add_argument('--max-retries', type=int, default=3, help='Maximum retry attempts')
    parser.add_argument('--show-system-info', action='store_true', help='Show system information and exit')
    
    args = parser.parse_args()
    
    # Show system info if requested
    if args.show_system_info:
        print_system_info()
        return
    
    # Detect system configuration
    print("\n" + "="*70)
    print("MULTI-DATASET PIPELINE - SYSTEM CONFIGURATION")
    print("="*70)
    
    sys_config = detect_system_config(
        override_workers=args.workers,
        override_chunk_size=args.chunk_size
    )
    print(sys_config)
    
    # Setup paths
    orders_file = Path(args.orders)
    trades_file = Path(args.trades)
    output_dir = Path(args.output)
    partitions_dir = output_dir / 'partitions'
    registry_path = output_dir / 'partition_registry.json'
    
    # Validate inputs
    if not orders_file.exists():
        print(f"ERROR: Orders file not found: {orders_file}")
        return 1
    
    if not trades_file.exists():
        print(f"ERROR: Trades file not found: {trades_file}")
        return 1
    
    # Initialize registry
    registry = PartitionRegistry(registry_path)
    
    if args.resume and registry_path.exists():
        print(f"\n✓ Resuming from existing registry: {registry_path}")
        registry.load()
        registry.print_summary()
    else:
        print(f"\n✓ Creating new registry: {registry_path}")
    
    # PHASE 1: Streaming Extraction
    print("\n" + "="*70)
    print("PHASE 1: STREAMING EXTRACTION")
    print("="*70)
    
    if not args.resume or len(registry.partitions) == 0:
        extractor = StreamingExtractor(
            chunk_size=sys_config.chunk_size,
            progress_callback=None
        )
        
        partition_counts = extractor.extract_partitions(
            orders_file=orders_file,
            trades_file=trades_file,
            output_dir=partitions_dir,
            date_col='date',
            security_col='security_code'
        )
        
        # Register all partitions
        for partition_key, (order_count, trade_count) in partition_counts.items():
            date, security = partition_key.split('/')
            registry.add_partition(
                partition_key=partition_key,
                date=date,
                security_code=security,
                order_count=order_count,
                trade_count=trade_count
            )
        
        # Save registry
        registry.save()
        print(f"\n✓ Registered {len(partition_counts)} partitions")
    else:
        print("\n✓ Skipping extraction (resuming from existing partitions)")
    
    registry.print_summary()
    
    # PHASE 2: Parallel Processing
    print("\n" + "="*70)
    print("PHASE 2: PARALLEL PROCESSING")
    print("="*70)
    
    engine = ParallelProcessingEngine(
        registry=registry,
        num_workers=sys_config.num_workers,
        progress_callback=None
    )
    
    successful, failed = engine.process_all_pending(
        base_dir=partitions_dir,
        retry_failed=args.retry,
        max_retries=args.max_retries
    )
    
    # Final summary
    print("\n" + "="*70)
    print("PIPELINE COMPLETE")
    print("="*70)
    registry.print_summary()
    
    print(f"\nResults saved to: {output_dir}")
    print(f"Registry saved to: {registry_path}")
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    start_time = time.time()
    exit_code = main()
    elapsed = time.time() - start_time
    
    print(f"\nTotal pipeline duration: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
    sys.exit(exit_code)
