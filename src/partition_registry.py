"""
Partition Registry Module

Tracks all partitions in a dataset, their status, metadata, and processing results.
Enables resumable processing, error tracking, and cross-partition aggregation.
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from enum import Enum


class PartitionStatus(Enum):
    """Status of a partition in the processing pipeline."""
    PENDING = "pending"           # Not yet processed
    EXTRACTING = "extracting"     # Currently being extracted
    EXTRACTED = "extracted"       # Extraction complete, ready to process
    PROCESSING = "processing"     # Currently being processed
    COMPLETED = "completed"       # Successfully processed
    FAILED = "failed"             # Failed processing
    SKIPPED = "skipped"           # Skipped (filtered out or excluded)


@dataclass
class PartitionMetadata:
    """Metadata for a single partition."""
    partition_key: str                    # e.g., "2024-09-05/110621"
    date: str                             # e.g., "2024-09-05"
    security_code: str                    # e.g., "110621"
    status: str                           # PartitionStatus value
    
    # Row counts
    order_count: int = 0
    trade_count: int = 0
    
    # Processing info
    created_at: Optional[str] = None      # ISO timestamp
    started_at: Optional[str] = None      # ISO timestamp
    completed_at: Optional[str] = None    # ISO timestamp
    duration_sec: Optional[float] = None
    
    # Error info
    error_message: Optional[str] = None
    retry_count: int = 0
    
    # Output info
    output_dir: Optional[str] = None
    matched_orders: int = 0
    unmatched_orders: int = 0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PartitionMetadata':
        """Create from dictionary loaded from JSON."""
        return cls(**data)


class PartitionRegistry:
    """
    Registry for tracking all partitions in a processing run.
    
    Provides:
    - Partition status tracking
    - Resumable processing (save/load state)
    - Error tracking and retry management
    - Query interface for filtering partitions
    """
    
    def __init__(self, registry_path: Path):
        """
        Initialize partition registry.
        
        Args:
            registry_path: Path to registry JSON file
        """
        self.registry_path = Path(registry_path)
        self.partitions: Dict[str, PartitionMetadata] = {}
        
        # Load existing registry if it exists
        if self.registry_path.exists():
            self.load()
    
    def add_partition(
        self,
        partition_key: str,
        date: str,
        security_code: str,
        order_count: int = 0,
        trade_count: int = 0
    ):
        """
        Add a new partition to the registry.
        
        Args:
            partition_key: Unique key for partition (e.g., "2024-09-05/110621")
            date: Date string (YYYY-MM-DD)
            security_code: Security code
            order_count: Number of orders in partition
            trade_count: Number of trades in partition
        """
        if partition_key in self.partitions:
            # Update counts if partition already exists
            self.partitions[partition_key].order_count = order_count
            self.partitions[partition_key].trade_count = trade_count
        else:
            self.partitions[partition_key] = PartitionMetadata(
                partition_key=partition_key,
                date=date,
                security_code=security_code,
                status=PartitionStatus.PENDING.value,
                order_count=order_count,
                trade_count=trade_count,
                created_at=datetime.utcnow().isoformat()
            )
    
    def update_status(
        self,
        partition_key: str,
        status: PartitionStatus,
        error_message: Optional[str] = None
    ):
        """
        Update the status of a partition.
        
        Args:
            partition_key: Partition key
            status: New status
            error_message: Error message if failed
        """
        if partition_key not in self.partitions:
            raise KeyError(f"Partition {partition_key} not found in registry")
        
        partition = self.partitions[partition_key]
        old_status = partition.status
        partition.status = status.value
        
        # Update timestamps
        now = datetime.utcnow().isoformat()
        
        if status == PartitionStatus.PROCESSING and old_status != PartitionStatus.PROCESSING.value:
            partition.started_at = now
        
        if status == PartitionStatus.COMPLETED:
            partition.completed_at = now
            if partition.started_at:
                start = datetime.fromisoformat(partition.started_at)
                end = datetime.fromisoformat(now)
                partition.duration_sec = (end - start).total_seconds()
        
        if status == PartitionStatus.FAILED:
            partition.completed_at = now
            partition.error_message = error_message
            partition.retry_count += 1
    
    def update_results(
        self,
        partition_key: str,
        output_dir: str,
        matched_orders: int,
        unmatched_orders: int
    ):
        """
        Update partition results after successful processing.
        
        Args:
            partition_key: Partition key
            output_dir: Output directory path
            matched_orders: Number of matched orders
            unmatched_orders: Number of unmatched orders
        """
        if partition_key not in self.partitions:
            raise KeyError(f"Partition {partition_key} not found in registry")
        
        partition = self.partitions[partition_key]
        partition.output_dir = output_dir
        partition.matched_orders = matched_orders
        partition.unmatched_orders = unmatched_orders
    
    def get_by_status(self, status: PartitionStatus) -> List[PartitionMetadata]:
        """Get all partitions with given status."""
        return [p for p in self.partitions.values() if p.status == status.value]
    
    def get_by_date(self, date: str) -> List[PartitionMetadata]:
        """Get all partitions for a given date."""
        return [p for p in self.partitions.values() if p.date == date]
    
    def get_by_security(self, security_code: str) -> List[PartitionMetadata]:
        """Get all partitions for a given security."""
        return [p for p in self.partitions.values() if p.security_code == security_code]
    
    def get_failed(self) -> List[PartitionMetadata]:
        """Get all failed partitions."""
        return self.get_by_status(PartitionStatus.FAILED)
    
    def get_pending(self) -> List[PartitionMetadata]:
        """Get all pending partitions."""
        return self.get_by_status(PartitionStatus.PENDING)
    
    def get_completed(self) -> List[PartitionMetadata]:
        """Get all completed partitions."""
        return self.get_by_status(PartitionStatus.COMPLETED)
    
    def get_all_dates(self) -> Set[str]:
        """Get set of all dates in registry."""
        return {p.date for p in self.partitions.values()}
    
    def get_all_securities(self) -> Set[str]:
        """Get set of all security codes in registry."""
        return {p.security_code for p in self.partitions.values()}
    
    def save(self):
        """Save registry to JSON file."""
        # Ensure directory exists
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to JSON-serializable format
        data = {
            'created_at': datetime.utcnow().isoformat(),
            'total_partitions': len(self.partitions),
            'status_counts': self._count_by_status(),
            'partitions': {
                key: partition.to_dict()
                for key, partition in self.partitions.items()
            }
        }
        
        # Write to file
        with open(self.registry_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def load(self):
        """Load registry from JSON file."""
        if not self.registry_path.exists():
            return
        
        with open(self.registry_path, 'r') as f:
            data = json.load(f)
        
        # Load partitions
        self.partitions = {
            key: PartitionMetadata.from_dict(partition_data)
            for key, partition_data in data['partitions'].items()
        }
    
    def _count_by_status(self) -> Dict[str, int]:
        """Count partitions by status."""
        counts = {}
        for partition in self.partitions.values():
            status = partition.status
            counts[status] = counts.get(status, 0) + 1
        return counts
    
    def get_summary(self) -> Dict:
        """Get summary statistics for the registry."""
        status_counts = self._count_by_status()
        
        completed = self.get_completed()
        failed = self.get_failed()
        
        # Calculate average duration for completed partitions
        durations = [p.duration_sec for p in completed if p.duration_sec is not None]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        # Calculate total matched/unmatched
        total_matched = sum(p.matched_orders for p in completed)
        total_unmatched = sum(p.unmatched_orders for p in completed)
        
        return {
            'total_partitions': len(self.partitions),
            'dates': sorted(list(self.get_all_dates())),
            'securities': sorted(list(self.get_all_securities())),
            'status_counts': status_counts,
            'completed_count': len(completed),
            'failed_count': len(failed),
            'pending_count': len(self.get_pending()),
            'avg_duration_sec': avg_duration,
            'total_matched_orders': total_matched,
            'total_unmatched_orders': total_unmatched,
            'failed_partitions': [p.partition_key for p in failed]
        }
    
    def print_summary(self):
        """Print formatted summary of registry."""
        summary = self.get_summary()
        
        print(f"\n{'='*70}")
        print(f"PARTITION REGISTRY SUMMARY")
        print(f"{'='*70}")
        print(f"Total Partitions:     {summary['total_partitions']:>6}")
        print(f"  Dates:              {len(summary['dates']):>6} ({summary['dates'][0]} to {summary['dates'][-1]})" if summary['dates'] else "  No dates")
        print(f"  Securities:         {len(summary['securities']):>6}")
        print(f"\nStatus Breakdown:")
        for status, count in sorted(summary['status_counts'].items()):
            print(f"  {status.title():<15} {count:>6}")
        
        if summary['completed_count'] > 0:
            print(f"\nProcessing Stats:")
            print(f"  Avg Duration:       {summary['avg_duration_sec']:>6.1f} sec")
            print(f"  Total Matched:      {summary['total_matched_orders']:>6}")
            print(f"  Total Unmatched:    {summary['total_unmatched_orders']:>6}")
        
        if summary['failed_count'] > 0:
            print(f"\nFailed Partitions ({summary['failed_count']}):")
            for partition_key in summary['failed_partitions'][:10]:  # Show first 10
                partition = self.partitions[partition_key]
                print(f"  {partition_key}: {partition.error_message}")
            if summary['failed_count'] > 10:
                print(f"  ... and {summary['failed_count'] - 10} more")
        
        print(f"{'='*70}\n")
    
    def reset_failed(self):
        """Reset all failed partitions to pending status for retry."""
        failed = self.get_failed()
        for partition in failed:
            partition.status = PartitionStatus.PENDING.value
            partition.error_message = None
        return len(failed)


if __name__ == '__main__':
    # Test the registry
    print("Testing Partition Registry")
    print("="*70)
    
    # Create test registry
    test_path = Path("test_registry.json")
    registry = PartitionRegistry(test_path)
    
    # Add some test partitions
    print("\nAdding test partitions...")
    for date in ['2024-09-04', '2024-09-05']:
        for security in ['110621', '85603']:
            key = f"{date}/{security}"
            registry.add_partition(
                partition_key=key,
                date=date,
                security_code=security,
                order_count=1000,
                trade_count=500
            )
    
    print(f"Added {len(registry.partitions)} partitions")
    
    # Simulate processing
    print("\nSimulating processing...")
    for i, (key, partition) in enumerate(registry.partitions.items()):
        if i == 0:
            # First partition: success
            registry.update_status(key, PartitionStatus.PROCESSING)
            registry.update_status(key, PartitionStatus.COMPLETED)
            registry.update_results(key, f"/output/{key}", 800, 200)
        elif i == 1:
            # Second partition: failed
            registry.update_status(key, PartitionStatus.PROCESSING)
            registry.update_status(key, PartitionStatus.FAILED, "Test error")
        # Others: pending
    
    # Save registry
    print(f"\nSaving registry to {test_path}...")
    registry.save()
    
    # Print summary
    registry.print_summary()
    
    # Test query methods
    print("\nQuery Tests:")
    print(f"Completed partitions: {len(registry.get_completed())}")
    print(f"Failed partitions: {len(registry.get_failed())}")
    print(f"Pending partitions: {len(registry.get_pending())}")
    print(f"Partitions for 2024-09-05: {len(registry.get_by_date('2024-09-05'))}")
    print(f"Partitions for 110621: {len(registry.get_by_security('110621'))}")
    
    # Test reset
    print(f"\nResetting {registry.reset_failed()} failed partitions...")
    print(f"Failed partitions after reset: {len(registry.get_failed())}")
    
    # Clean up
    test_path.unlink()
    print(f"\nTest complete! Cleaned up {test_path}")
