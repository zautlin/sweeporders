#!/usr/bin/env python3
"""
Script to refactor data_processor.py to use the new column schema system.

Replaces:
- col('orders', 'order_type', column_mapping) → col.orders.order_type
- col('trades', 'order_id', column_mapping) → col.trades.order_id
- Hard coded strings like 'date', 'security_code', 'orderid' → col.common.*
"""

import re
from pathlib import Path

# Mapping of old col() calls to new col.* syntax
COL_MAPPINGS = [
    # Orders columns
    (r"col\('orders', 'order_type', column_mapping\)", "col.orders.order_type"),
    (r"col\('orders', 'timestamp', column_mapping\)", "col.orders.timestamp"),
    (r"col\('orders', 'security_code', column_mapping\)", "col.orders.security_code"),
    (r"col\('orders', 'order_id', column_mapping\)", "col.orders.order_id"),
    (r"col\('orders', 'sequence', column_mapping\)", "col.orders.sequence"),
    (r"col\('orders', 'change_reason', column_mapping\)", "col.orders.change_reason"),
    (r"col\('orders', 'leaves_quantity', column_mapping\)", "col.orders.leaves_quantity"),
    (r"col\('orders', 'matched_quantity', column_mapping\)", "col.orders.matched_quantity"),
    
    # Trades columns
    (r"col\('trades', 'order_id', column_mapping\)", "col.trades.order_id"),
    (r"col\('trades', 'trade_time', column_mapping\)", "col.trades.trade_time"),
    (r"col\('trades', 'trade_price', column_mapping\)", "col.trades.trade_price"),
    (r"col\('trades', 'quantity', column_mapping\)", "col.trades.quantity"),
    
    # NBBO columns
    (r"col\('nbbo', 'timestamp', column_mapping\)", "col.nbbo.timestamp"),
    (r"col\('nbbo', 'security_code', column_mapping\)", "col.nbbo.security_code"),
    
    # Reference data columns
    (r"col\('session', 'timestamp', column_mapping\)", "col.session.timestamp"),
    (r"col\('reference', 'timestamp', column_mapping\)", "col.reference.timestamp"),
    (r"col\('participants', 'timestamp', column_mapping\)", "col.participants.timestamp"),
]

# Hardcoded string columns to replace (use sparingly, only where it's clearly a column name)
STRING_MAPPINGS = [
    # Be careful with these - only replace when they're DataFrame column references
    (r"\['date'\]", f"[col.common.date]"),
    (r'\"date\"', "col.common.date"),
    # Note: 'security_code', 'orderid', etc. are trickier as they appear in many contexts
]

def refactor_file(file_path):
    """Refactor a Python file to use new column schema."""
    content = file_path.read_text()
    original_content = content
    
    # Step 1: Add import statement after existing imports
    if 'from column_schema import col' not in content:
        # Find the last import line
        import_pattern = r'(from config import SWEEP_ORDER_TYPE)'
        content = re.sub(
            import_pattern,
            r'\1\nfrom column_schema import col',
            content
        )
    
    # Step 2: Remove old col() helper function (lines 30-34)
    old_col_function = r'\n\ndef col\(file_type, logical_name, column_mapping\):\n    """Get actual column name from logical name using mapping\."""\n    mapped = column_mapping\.get\(file_type, \{\}\)\.get\(logical_name\)\n    return mapped if mapped is not None else logical_name\n'
    content = re.sub(old_col_function, '\n', content)
    
    # Step 3: Replace all col() function calls
    for old_pattern, new_value in COL_MAPPINGS:
        content = re.sub(old_pattern, new_value, content)
    
    # Step 4: Replace hardcoded string references (carefully!)
    # Only do this for df['date'] patterns
    content = re.sub(r"df\['date'\]", "df[col.common.date]", content)
    content = re.sub(r"'date' not in .+?\.columns", "'date' not in df.columns", content)  # Keep string in conditions
    
    if content != original_content:
        print(f"✓ Refactored {file_path.name}")
        return content
    else:
        print(f"  No changes needed for {file_path.name}")
        return None

def main():
    repo_root = Path(__file__).parent.parent
    file_path = repo_root / "src" / "data_processor.py"
    
    if not file_path.exists():
        print(f"Error: {file_path} not found")
        return
    
    print(f"Refactoring {file_path}...")
    refactored_content = refactor_file(file_path)
    
    if refactored_content:
        # Save refactored content
        file_path.write_text(refactored_content)
        print(f"\n✓ Successfully refactored {file_path.name}")
    else:
        print(f"\n  No changes needed")

if __name__ == "__main__":
    main()
