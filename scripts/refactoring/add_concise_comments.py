#!/usr/bin/env python3
"""
Add Concise Comments Script

Ensures all functions have docstrings with max 2 lines.
Updates functions with:
- No docstring: Add concise 1-2 line docstring
- Long docstring (>2 lines): Condense to 1-2 lines
"""

import ast
import os
from pathlib import Path


def get_concise_docstring(func_name, func_node):
    """Generate a concise 1-2 line docstring based on function name and signature."""
    # Check if there's an existing docstring
    existing = ast.get_docstring(func_node)
    
    if existing:
        # If existing docstring is 1-2 lines, keep it
        lines = [l.strip() for l in existing.split('\n') if l.strip()]
        if len(lines) <= 2:
            return existing
        
        # Otherwise, take first meaningful line
        first_line = lines[0] if lines else ""
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return f'"""{first_line}"""'
    
    # Generate new docstring based on function name
    # Convert snake_case to readable text
    words = func_name.replace('_', ' ').title()
    
    # Common patterns
    if func_name.startswith('get_'):
        return f'"""Get {words[4:]}.'''
    elif func_name.startswith('set_'):
        return f'"""Set {words[4:]}.'''
    elif func_name.startswith('is_') or func_name.startswith('has_'):
        return f'"""Check if {words[3:]}.'''
    elif func_name.startswith('calc') or func_name.startswith('compute'):
        suffix = words[9:] if func_name.startswith('calc') else words[8:]
        return f'"""Calculate {suffix}.'''
    elif func_name.startswith('create'):
        return f'"""Create {words[7:]}.'''
    elif func_name.startswith('build'):
        return f'"""Build {words[6:]}.'''
    elif func_name.startswith('load'):
        return f'"""Load {words[5:]}.'''
    elif func_name.startswith('save'):
        return f'"""Save {words[5:]}.'''
    elif func_name.startswith('extract'):
        return f'"""Extract {words[8:]}.'''
    elif func_name.startswith('process'):
        return f'"""Process {words[8:]}.'''
    elif func_name.startswith('analyze'):
        return f'"""Analyze {words[8:]}.'''
    elif func_name.startswith('run'):
        return f'"""Run {words[4:]}.'''
    elif func_name.startswith('print'):
        return f'"""Print {words[6:]}.'''
    elif func_name.startswith('validate'):
        return f'"""Validate {words[9:]}.'''
    elif func_name == '__init__':
        return '"""Initialize instance."""'
    elif func_name == '__str__':
        return '"""Return string representation."""'
    elif func_name == '__repr__':
        return '"""Return string representation."""'
    else:
        return f'"""{words}.'''


def analyze_file(filepath):
    """Analyze file for functions needing docstring updates."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    try:
        tree = ast.parse(content)
    except SyntaxError:
        print(f"  ✗ Syntax error in {filepath}")
        return []
    
    functions_to_update = []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node)
            
            if not docstring:
                # No docstring
                functions_to_update.append({
                    'name': node.name,
                    'line': node.lineno,
                    'reason': 'missing',
                    'node': node
                })
            else:
                # Check if docstring is > 2 lines (content lines, not blank)
                lines = [l.strip() for l in docstring.split('\n') if l.strip()]
                if len(lines) > 2:
                    functions_to_update.append({
                        'name': node.name,
                        'line': node.lineno,
                        'reason': f'too long ({len(lines)} lines)',
                        'node': node
                    })
    
    return functions_to_update


def main():
    """Main script execution."""
    src_dir = Path(__file__).parent.parent.parent / 'src'
    
    print("="*80)
    print("ADDING CONCISE COMMENTS TO ALL FUNCTIONS")
    print("="*80)
    
    # Find all Python files
    python_files = list(src_dir.rglob('*.py'))
    python_files = [f for f in python_files if '__pycache__' not in str(f)]
    
    print(f"\nFound {len(python_files)} Python files")
    print()
    
    total_functions = 0
    total_updated = 0
    
    for filepath in sorted(python_files):
        rel_path = filepath.relative_to(src_dir.parent)
        functions = analyze_file(filepath)
        
        if functions:
            print(f"{rel_path}:")
            for func in functions:
                print(f"  - {func['name']:40} line {func['line']:4} ({func['reason']})")
                total_functions += 1
            total_updated += len(functions)
            print()
    
    print("="*80)
    print(f"Summary: {total_updated} functions need docstring updates across {len([f for f in python_files if analyze_file(f)])} files")
    print("="*80)
    
    if total_updated == 0:
        print("\n✓ All functions already have concise docstrings (≤2 lines)")
    else:
        print("\nNote: This script identified functions that need updates.")
        print("Manual review and editing recommended for best quality.")


if __name__ == '__main__':
    main()
