#!/usr/bin/env python3
"""Fix imports after src/ directory reorganization."""

import re
from pathlib import Path

def fix_imports_in_file(file_path, import_mappings):
    """Update imports in a single file."""
    content = file_path.read_text()
    original_content = content
    
    for old_import, new_import in import_mappings.items():
        # Handle various import patterns
        patterns = [
            (f"import {old_import}", f"import {new_import}"),
            (f"from {old_import} import", f"from {new_import} import"),
            (f"import {old_import} as", f"import {new_import} as"),
        ]
        
        for old_pattern, new_pattern in patterns:
            content = content.replace(old_pattern, new_pattern)
    
    if content != original_content:
        file_path.write_text(content)
        return True
    return False

def main():
    repo_root = Path(__file__).parent.parent.parent
    src_dir = repo_root / "src"
    
    # Mapping of old imports to new imports
    import_mappings = {
        # Pipeline modules
        'data_processor': 'pipeline.data_processor',
        'partition_processor': 'pipeline.partition_processor',
        'sweep_simulator': 'pipeline.sweep_simulator',
        'metrics_generator': 'pipeline.metrics_generator',
        # Analysis modules
        'sweep_execution_analyzer': 'analysis.sweep_execution_analyzer',
        'unmatched_analyzer': 'analysis.unmatched_analyzer',
        'volume_analyzer': 'analysis.volume_analyzer',
        'data_explorer': 'analysis.data_explorer',
        # Aggregation modules
        'aggregate_sweep_results': 'aggregation.aggregate_sweep_results',
        'aggregate_volume_analysis': 'aggregation.aggregate_volume_analysis',
        'analyze_aggregated_results': 'aggregation.analyze_aggregated_results',
        # Utility modules
        'data_utils': 'utils.data_utils',
        'file_utils': 'utils.file_utils',
        'statistics_layer': 'utils.statistics_layer',
        # Discovery modules
        'security_discovery': 'discovery.security_discovery',
    }
    
    # Files to update
    files_to_update = [
        src_dir / "main.py",
        src_dir / "pipeline" / "partition_processor.py",
        src_dir / "pipeline" / "sweep_simulator.py",
        src_dir / "pipeline" / "metrics_generator.py",
        src_dir / "analysis" / "sweep_execution_analyzer.py",
        src_dir / "analysis" / "unmatched_analyzer.py",
        src_dir / "analysis" / "volume_analyzer.py",
        src_dir / "aggregation" / "aggregate_sweep_results.py",
        src_dir / "aggregation" / "aggregate_volume_analysis.py",
        src_dir / "aggregation" / "analyze_aggregated_results.py",
    ]
    
    print("Fixing imports after src/ reorganization...")
    print("=" * 80)
    
    updated_count = 0
    for file_path in files_to_update:
        if file_path.exists():
            if fix_imports_in_file(file_path, import_mappings):
                print(f"  ✓ Updated: {file_path.relative_to(repo_root)}")
                updated_count += 1
            else:
                print(f"  ○ No changes: {file_path.relative_to(repo_root)}")
        else:
            print(f"  ⊘ Not found: {file_path.relative_to(repo_root)}")
    
    print("=" * 80)
    print(f"✓ Updated {updated_count} files")

if __name__ == "__main__":
    main()
