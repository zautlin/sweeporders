#!/usr/bin/env python3
"""
Comprehensive script to refactor all remaining files to use column schema.
"""

import re
from pathlib import Path

# Common column reference patterns to replace
COLUMN_REPLACEMENTS = [
    (r"\['orderid'\]", "[col.common.orderid]"),
    (r"\['order_id'\]", "[col.common.order_id]"),
    (r"\['timestamp'\]", "[col.common.timestamp]"),
    (r"\['side'\]", "[col.common.side]"),
    (r"\['price'\]", "[col.common.price]"),
    (r"\['quantity'\]", "[col.common.quantity]"),
    (r"\['date'\]", "[col.common.date]"),
    (r"\['ticker'\]", "[col.common.ticker]"),
    (r"\['orderbookid'\]", "[col.common.orderbookid]"),
    
    # Orders columns
    (r"\['bid'\]", "[col.orders.bid]"),
    (r"\['offer'\]", "[col.orders.offer]"),
    (r"\['sequence'\]", "[col.orders.sequence]"),
    (r"\['leavesquantity'\]", "[col.orders.leaves_quantity]"),
    (r"\['totalmatchedquantity'\]", "[col.orders.matched_quantity]"),
    (r"\['exchangeordertype'\]", "[col.orders.order_type]"),
    
    # Metrics columns
    (r"\['vwap'\]", "[col.metrics.vwap]"),
    (r"\['fill_rate'\]", "[col.metrics.fill_rate]"),
    (r"\['qty_filled'\]", "[col.metrics.qty_filled]"),
    (r"\['num_fills'\]", "[col.metrics.num_fills]"),
    (r"\['exec_time_sec'\]", "[col.metrics.exec_time_sec]"),
    
    # Comparison columns
    (r"\['exec_cost_diff'\]", "[col.comparison.exec_cost_diff]"),
    (r"\['vwap_diff'\]", "[col.comparison.vwap_diff]"),
    (r"\['fill_rate_diff'\]", "[col.comparison.fill_rate_diff]"),
    
    # Volume columns
    (r"\['volume_bucket'\]", "[col.volume.volume_bucket]"),
    (r"\['n_orders'\]", "[col.volume.n_orders]"),
    
    # Stats columns  
    (r"\['p_value'\]", "[col.stats.p_value]"),
    (r"\['t_statistic'\]", "[col.stats.t_statistic]"),
    (r"\['effect_size'\]", "[col.stats.effect_size]"),
]

def add_import(content):
    """Add column_schema import if not present."""
    if 'from column_schema import col' in content:
        return content
    
    # Find the last import line
    import_patterns = [
        (r'(import pandas as pd)', r'\1\nfrom column_schema import col'),
        (r'(from pathlib import Path)', r'\1\nfrom column_schema import col'),
        (r'(import numpy as np)', r'\1\nfrom column_schema import col'),
    ]
    
    for pattern, replacement in import_patterns:
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content, count=1)
            return content
    
    # Fallback: add after first import
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('import ') or line.startswith('from '):
            lines.insert(i+1, 'from column_schema import col')
            return '\n'.join(lines)
    
    return content

def refactor_file(file_path):
    """Refactor a single file."""
    if not file_path.exists():
        print(f"  ⊘ {file_path.name}: File not found")
        return False
    
    content = file_path.read_text()
    original_content = content
    
    # Step 1: Add import
    content = add_import(content)
    
    # Step 2: Replace column references
    for old_pattern, new_value in COLUMN_REPLACEMENTS:
        content = re.sub(old_pattern, new_value, content)
    
    # Step 3: Save if changed
    if content != original_content:
        file_path.write_text(content)
        print(f"  ✓ {file_path.name}: Refactored")
        return True
    else:
        print(f"  ○ {file_path.name}: No changes needed")
        return False

def main():
    repo_root = Path(__file__).parent.parent
    src_dir = repo_root / "src"
    
    # List of files to refactor
    files_to_refactor = [
        "metrics_generator.py",
        "sweep_execution_analyzer.py",
        "unmatched_analyzer.py",
        "volume_analyzer.py",
        "aggregate_sweep_results.py",
        "analyze_aggregated_results.py",
        "aggregate_volume_analysis.py",
        "data_explorer.py",
        "security_discovery.py",
    ]
    
    print("Refactoring files...")
    print("=" * 80)
    
    refactored_count = 0
    for filename in files_to_refactor:
        file_path = src_dir / filename
        if refactor_file(file_path):
            refactored_count += 1
    
    print("=" * 80)
    print(f"✓ Refactored {refactored_count} files")

if __name__ == "__main__":
    main()
