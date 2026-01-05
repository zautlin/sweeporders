#!/usr/bin/env python3
"""Fix imports after moving config files to config/ package."""

import re
from pathlib import Path

def fix_imports_in_file(file_path):
    """Update imports in a single file."""
    content = file_path.read_text()
    original_content = content
    
    # Replace imports
    replacements = [
        # config imports
        (r'^from config import', 'from config.config import'),
        (r'^import config$', 'import config.config as config'),
        (r'^import config as cfg', 'import config.config as cfg'),
        
        # column_schema imports
        (r'^from column_schema import', 'from config.column_schema import'),
        (r'^import column_schema', 'import config.column_schema'),
        
        # system_config imports
        (r'^from system_config import', 'from config.system_config import'),
        (r'^import system_config', 'import config.system_config'),
    ]
    
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        new_line = line
        for old_pattern, new_pattern in replacements:
            new_line = re.sub(old_pattern, new_pattern, new_line)
        new_lines.append(new_line)
    
    content = '\n'.join(new_lines)
    
    if content != original_content:
        file_path.write_text(content)
        return True
    return False

def main():
    repo_root = Path(__file__).parent.parent.parent
    src_dir = repo_root / "src"
    
    # Find all Python files
    python_files = []
    for pattern in ['**/*.py']:
        python_files.extend(src_dir.glob(pattern))
    
    # Exclude __pycache__ and the config package itself
    python_files = [
        f for f in python_files 
        if '__pycache__' not in str(f) and '/config/' not in str(f)
    ]
    
    print("Fixing imports after moving to config/ package...")
    print("=" * 80)
    
    updated_count = 0
    for file_path in sorted(python_files):
        if fix_imports_in_file(file_path):
            print(f"  ✓ Updated: {file_path.relative_to(repo_root)}")
            updated_count += 1
        else:
            print(f"  ○ No changes: {file_path.relative_to(repo_root)}")
    
    print("=" * 80)
    print(f"✓ Updated {updated_count} files")

if __name__ == "__main__":
    main()
