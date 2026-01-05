#!/usr/bin/env python3
"""Refactor sweep_simulator.py to use column schema."""

import re
from pathlib import Path

def refactor_file():
    file_path = Path(__file__).parent.parent / "src" / "sweep_simulator.py"
    content = file_path.read_text()
    
    # Step 1: Add import
    if 'from column_schema import col' not in content:
        content = content.replace(
            'import pandas as pd\nimport numpy as np',
            'import pandas as pd\nimport numpy as np\nfrom column_schema import col'
        )
    
    # Step 2: Replace hardcoded string column references
    # Be careful to only replace actual column references, not string comparisons
    
    # Replace df['columnname'] patterns
    replacements = [
        (r"\['orderid'\]", f"[col.common.orderid]"),
        (r"\['timestamp'\]", f"[col.common.timestamp]"),
        (r"\['side'\]", f"[col.common.side]"),
        (r"\['price'\]", f"[col.common.price]"),
        (r"\['quantity'\]", f"[col.common.quantity]"),
        (r"\['bid'\]", f"[col.orders.bid]"),
        (r"\['offer'\]", f"[col.orders.offer]"),
        (r"\['sequence'\]", f"[col.orders.sequence]"),
        (r"\['leavesquantity'\]", f"[col.orders.leaves_quantity]"),
        (r"\['totalmatchedquantity'\]", f"[col.orders.matched_quantity]"),
        (r"\['orderbookid'\]", f"[col.common.orderbookid]"),
    ]
    
    for old, new in replacements:
        content = re.sub(old, new, content)
    
    # Step 3: Replace string literals in lists (column selections)
    # e.g., ['orderid', 'timestamp', ...] 
    # These are trickier - keep them as strings for now since they're in lists
    # Actually, for DataFrame column selections, we need the actual string values
    # So we should NOT replace these - they need to resolve to strings
    
    # Step 4: Save
    file_path.write_text(content)
    print(f"✓ Refactored {file_path.name}")

if __name__ == "__main__":
    refactor_file()
