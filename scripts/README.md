# Scripts Directory

This directory contains utility scripts for development, maintenance, and refactoring tasks.

## Directory Structure

```
scripts/
├── refactoring/          # Code refactoring scripts
│   ├── refactor_all_files.py           # Batch refactor all analysis modules
│   ├── refactor_data_processor.py      # Refactor data_processor.py
│   └── refactor_sweep_simulator.py     # Refactor sweep_simulator.py
│
├── archive/              # Archived/deprecated scripts
│   └── (empty - for future use)
│
└── README.md             # This file
```

## Script Categories

### 🔧 Refactoring Scripts (`refactoring/`)

Scripts used to migrate codebase to use the column schema system.

**Purpose:** Convert hardcoded column name strings to centralized column schema accessors.

**Usage:**
```bash
# Refactor all files at once
python scripts/refactoring/refactor_all_files.py

# Refactor specific file
python scripts/refactoring/refactor_data_processor.py
```

**Status:** ✅ Complete - All modules refactored (see feature/column-mapping-refactor branch)

---

### 📦 Archive (`archive/`)

Deprecated or one-time-use scripts kept for reference.

---

## Adding New Scripts

When adding new scripts, follow this organization:

1. **Development Scripts** → `scripts/dev/`
   - Testing helpers
   - Debug utilities
   - Performance profiling

2. **Deployment Scripts** → `scripts/deploy/`
   - Build scripts
   - Deployment automation
   - Environment setup

3. **Maintenance Scripts** → `scripts/maintenance/`
   - Data cleanup
   - Database migrations
   - Log rotation

4. **Analysis Scripts** → `scripts/analysis/`
   - Data exploration
   - Report generation
   - Statistical analysis

5. **Refactoring Scripts** → `scripts/refactoring/`
   - Code modernization
   - Architecture changes
   - Migration utilities

## Best Practices

1. **Naming Convention:** Use descriptive names with underscores
   - ✅ `refactor_column_schema.py`
   - ❌ `script1.py`

2. **Documentation:** Add a docstring at the top of each script
   ```python
   """
   Script Name
   
   Description of what this script does.
   
   Usage:
       python scripts/category/script_name.py [arguments]
   
   Author: Your Name
   Date: YYYY-MM-DD
   """
   ```

3. **Shebang:** Include for executable scripts
   ```python
   #!/usr/bin/env python3
   ```

4. **Make Executable:** For frequently used scripts
   ```bash
   chmod +x scripts/category/script_name.py
   ```

5. **Archive When Done:** Move completed one-time scripts to `archive/`

## Current Status

- ✅ Refactoring scripts organized
- 📁 Structure ready for future categories
- 📝 Documentation in place
