# Complete CLI Parameters Analysis

## Executive Summary

This document analyzes **ALL CLI parameters** in the sweeporders codebase:
- Where they're defined
- How they're used
- Why they exist
- Whether they're necessary
- Redundancy analysis

---

## Table of Contents

1. [Security Selection Parameters](#security-selection-parameters)
2. [Date Parameter](#date-parameter)
3. [Discovery Parameters](#discovery-parameters)
4. [Threshold Parameters](#threshold-parameters)
5. [Pipeline Stage Parameters](#pipeline-stage-parameters)
6. [Processing Mode Parameters](#processing-mode-parameters)
7. [Statistical Testing Parameters](#statistical-testing-parameters)
8. [Summary & Recommendations](#summary--recommendations)

---

## Complete Parameter List

| Parameter | Type | Category | Mutually Exclusive? | Required? |
|-----------|------|----------|---------------------|-----------|
| `--ticker` | str | Security Selection | Yes (with orderbookid, auto-discover) | No |
| `--orderbookid` | int | Security Selection | Yes (with ticker, auto-discover) | No |
| `--auto-discover` | flag | Security Selection | Yes (with ticker, orderbookid) | No |
| `--date` | str | Date Selection | No | Conditional |
| `--list-securities` | flag | Discovery | No | No |
| `--list-dates` | flag | Discovery | No | No |
| `--min-orders` | int | Threshold | No | No |
| `--min-trades` | int | Threshold | No | No |
| `--stage` | int[] | Pipeline Control | No | No |
| `--parallel` | flag | Processing Mode | Yes (with sequential) | No |
| `--sequential` | flag | Processing Mode | Yes (with parallel) | No |
| `--enable-stats` | flag | Statistics | Yes (with disable-stats) | No |
| `--disable-stats` | flag | Statistics | Yes (with enable-stats) | No |

---

# 1. Security Selection Parameters

## 1.1 `--ticker` Parameter

### **Definition**
```python
# src/main.py:58-59
security_group.add_argument('--ticker', type=str, default=None,
                    help=f'Ticker symbol (legacy, default: {config.TICKER})')
```

### **Where Used**
- **main.py**: Lines 58-59 (definition), 160-176 (usage)
- **data_explorer.py**: Lines 689-690 (definition), 768-775 (usage)

### **What It Does**
```python
# main.py:160-176
elif args.ticker:
    if not date:
        raise ValueError("--ticker requires --date argument")
    
    # Look up orderbookid from ticker
    orderbookid = discovery.get_orderbookid_from_ticker(args.ticker, date)
    
    if not orderbookid:
        raise ValueError(f"Ticker {args.ticker} not found for date {date}")
    
    # Get security info
    all_securities = discovery.discover_securities_for_date(date)
    security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
    
    securities_to_process = [security]
```

### **Purpose**
- User-friendly way to specify a security using human-readable ticker
- Converts ticker → orderbookid internally
- Marked as "legacy" but still fully supported

### **Usage Example**
```bash
# Process CBA using ticker
python src/main.py --ticker cba --date 20240905
```

### **Necessity: ✅ USEFUL BUT NOT ESSENTIAL**

**Reasons**:
- Convenience layer over orderbookid
- Human-readable (easier to remember "cba" than "85603")
- Backward compatibility with older scripts

**Could Be Removed?**
- Yes, users could use `--orderbookid` instead
- But UX would be worse (need to remember numeric IDs)

**Recommendation**: ✅ **Keep** - Good UX, low maintenance cost

---

## 1.2 `--orderbookid` Parameter

### **Definition**
```python
# src/main.py:60-61
security_group.add_argument('--orderbookid', type=int, default=None,
                    help='OrderbookID to process')
```

### **Where Used**
- **main.py**: Lines 60-61 (definition), 143-158 (usage)
- **data_explorer.py**: Lines 691-692 (definition), 759-767 (usage)

### **What It Does**
```python
# main.py:143-158
elif args.orderbookid:
    if not date:
        raise ValueError("--orderbookid requires --date argument")
    
    # Validate orderbookid exists
    all_securities = discovery.discover_securities_for_date(date)
    security = next((s for s in all_securities if s.orderbookid == args.orderbookid), None)
    
    if not security:
        raise ValueError(f"OrderbookID {args.orderbookid} not found for date {date}")
    
    if not (security.in_orders and security.in_trades):
        raise ValueError(f"OrderbookID {args.orderbookid} missing order or trade data")
    
    securities_to_process = [security]
```

### **Purpose**
- Direct specification of security by exchange-assigned numeric ID
- Primary/canonical way to identify securities
- No ambiguity (unlike ticker which could be mislabeled)

### **Usage Example**
```bash
# Process CBA using orderbookid
python src/main.py --orderbookid 85603 --date 20240905
```

### **Necessity: ✅ ESSENTIAL**

**Reasons**:
- orderbookid is the primary key in all raw data
- Ticker only exists in filenames (metadata, not data)
- Most precise/unambiguous identifier
- See: WHY_ORDERBOOKID_IS_ESSENTIAL.md for detailed analysis

**Could Be Removed?**
- NO - This is the fundamental identifier
- Ticker is just a convenience layer over orderbookid

**Recommendation**: ✅ **Keep** - Absolutely essential

---

## 1.3 `--auto-discover` Parameter

### **Definition**
```python
# src/main.py:62-63
security_group.add_argument('--auto-discover', action='store_true',
                    help='Auto-discover and process all valid securities for the date')
```

### **Where Used**
- **main.py**: Lines 62-63 (definition), 129-141 (usage)
- **data_explorer.py**: Lines 693-694 (definition), 734-757 (usage)

### **What It Does**
```python
# main.py:129-141
if args.auto_discover:
    if not date:
        raise ValueError("--auto-discover requires --date argument")
    
    # Discover all valid securities
    valid_securities = discovery.get_valid_securities(date)
    
    if not valid_securities:
        raise ValueError(f"No valid securities found for date {date}")
    
    securities_to_process = valid_securities
    
    print(f"\nAuto-discovered {len(securities_to_process)} valid securities:")
    for sec in securities_to_process:
        print(f"  - {sec}")
```

### **Purpose**
- Automatically find and process ALL valid securities for a date
- Eliminates need to manually specify each security
- Uses same logic as `--list-securities` to determine validity

### **Usage Example**
```bash
# Process all valid securities on 2024-09-05
python src/main.py --auto-discover --date 20240905

# Output:
# Auto-discovered 3 valid securities:
#   - SecurityInfo(orderbookid=85603, ticker='cba', ...)
#   - SecurityInfo(orderbookid=70616, ticker='bhp', ...)
#   - SecurityInfo(orderbookid=110621, ticker='drr', ...)
# (Processes each one through the pipeline)
```

### **Necessity: ✅ VERY USEFUL**

**Reasons**:
- Batch processing multiple securities without manual intervention
- Quality filtering (only processes securities with sufficient data)
- Reproducible (same logic as --list-securities)

**Could Be Removed?**
- Technically yes, but would require:
  - Manually running --list-securities
  - Manually running --orderbookid for each security
  - Writing a shell script to automate
- Much worse UX

**Recommendation**: ✅ **Keep** - High value for batch processing

---

### **Security Selection Summary**

These 3 parameters are **mutually exclusive** (can only use one):

```python
security_group = parser.add_mutually_exclusive_group()
security_group.add_argument('--ticker', ...)        # Human-readable
security_group.add_argument('--orderbookid', ...)   # Precise/canonical
security_group.add_argument('--auto-discover', ...) # Batch mode
```

**Decision Tree**:
```
Do you know the exact orderbookid?
├─ YES → Use --orderbookid 85603
└─ NO
   ├─ Know the ticker? → Use --ticker cba
   └─ Want to process all? → Use --auto-discover
```

**All 3 are useful and serve different needs:**
- ✅ `--orderbookid`: Essential (primary identifier)
- ✅ `--ticker`: Useful (better UX)
- ✅ `--auto-discover`: Very useful (batch processing)

---

# 2. Date Parameter

## 2.1 `--date` Parameter

### **Definition**
```python
# src/main.py:65-66
parser.add_argument('--date', type=str, default=None,
                    help=f'Date in YYYYMMDD format (default: {config.DATE})')
```

### **Where Used**
- **main.py**: Line 65 (definition), Line 114 (usage), Lines 131, 145, 162 (validation)
- **data_explorer.py**: Line 703 (definition), Lines 724, 730 (validation)
- **security_discovery.py**: Line 327 (definition), Line 349 (usage)

### **What It Does**
```python
# main.py:114
date = args.date or config.DATE

# Used in:
# - discovery.get_available_dates() - filter by date
# - discovery.discover_securities_for_date(date) - scan files for date
# - config.get_input_files(ticker, date) - construct file paths
```

### **Purpose**
- Specify which trading day to process
- Required for most operations (except Stage 4 aggregation)
- Format: YYYYMMDD (e.g., "20240905")

### **Usage Examples**
```bash
# Explicit date
python src/main.py --orderbookid 85603 --date 20240905

# Uses config.DATE if not specified
python src/main.py --orderbookid 85603
```

### **Necessity: ✅ ESSENTIAL**

**Reasons**:
- Data is partitioned by date
- File naming includes date: `{ticker}_{date}_{type}.csv`
- Different dates have different securities
- Must match raw data file dates

**Could Be Removed?**
- NO - Fundamental requirement
- Could default to "latest date" but that's fragile
- Explicit is better than implicit

**Recommendation**: ✅ **Keep** - Absolutely essential

---

# 3. Discovery Parameters

## 3.1 & 3.2: `--list-securities` and `--list-dates`

**Already analyzed in detail in LIST_PARAMETERS_ANALYSIS.md**

### **Quick Summary**

| Parameter | Purpose | Necessity |
|-----------|---------|-----------|
| `--list-dates` | Show available dates in raw data | ✅ Very useful |
| `--list-securities` | Show securities for a date | ✅ Very useful |

**Both are informational tools that exit without running pipeline.**

See: `LIST_PARAMETERS_ANALYSIS.md` for complete analysis.

**Recommendation**: ✅ **Keep both** - Critical for discovery workflow

---

# 4. Threshold Parameters

## 4.1 `--min-orders` Parameter

### **Definition**
```python
# src/main.py:73-74
parser.add_argument('--min-orders', type=int, default=config.MIN_ORDERS_THRESHOLD,
                    help=f'Minimum orders threshold for valid security (default: {config.MIN_ORDERS_THRESHOLD})')
```

### **Where Used**
- **main.py**: Line 73 (definition), Line 101 (passed to SecurityDiscovery)
- **data_explorer.py**: Line 701 (definition), Line 709 (passed to SecurityDiscovery)
- **security_discovery.py**: Line 323 (definition), Line 337 (passed to SecurityDiscovery)

### **What It Does**
```python
# Used in SecurityDiscovery.get_valid_securities()
# security_discovery.py:230-232
if sec.order_count < self.min_orders:
    logger.info(f"Skipping {sec.orderbookid}: Only {sec.order_count} orders (min: {self.min_orders})")
    continue
```

### **Purpose**
- Quality filter: Skip securities with too few orders
- Prevents processing securities with insufficient data
- Customizable threshold (default: 10)

### **Usage Examples**
```bash
# Default threshold (10 orders)
python src/main.py --list-securities --date 20240905

# Stricter threshold (1000 orders)
python src/main.py --list-securities --date 20240905 --min-orders 1000

# Looser threshold (1 order)
python src/main.py --auto-discover --date 20240905 --min-orders 1
```

### **Necessity: ✅ USEFUL**

**Reasons**:
- Data quality control
- Prevents wasting computation on low-quality data
- Customizable based on analysis needs
- Affects both `--list-securities` and `--auto-discover`

**Could Be Removed?**
- Yes, could hardcode default value
- But users might need different thresholds for different analyses
- Flexibility is valuable

**Recommendation**: ✅ **Keep** - Useful quality control knob

---

## 4.2 `--min-trades` Parameter

### **Definition**
```python
# src/main.py:75-76
parser.add_argument('--min-trades', type=int, default=config.MIN_TRADES_THRESHOLD,
                    help=f'Minimum trades threshold for valid security (default: {config.MIN_TRADES_THRESHOLD})')
```

### **Where Used**
- **main.py**: Line 75 (definition), Line 101 (passed to SecurityDiscovery)
- **data_explorer.py**: Line 702 (definition), Line 709 (passed to SecurityDiscovery)
- **security_discovery.py**: Line 324 (definition), Line 338 (passed to SecurityDiscovery)

### **What It Does**
```python
# Used in SecurityDiscovery.get_valid_securities()
# security_discovery.py:234-236
if sec.trade_count < self.min_trades:
    logger.info(f"Skipping {sec.orderbookid}: Only {sec.trade_count} trades (min: {self.min_trades})")
    continue
```

### **Purpose**
- Quality filter: Skip securities with too few trades
- Complementary to --min-orders
- Ensures security has actual execution activity

### **Usage Examples**
```bash
# Default threshold (10 trades)
python src/main.py --auto-discover --date 20240905

# Require significant trading activity (500 trades)
python src/main.py --auto-discover --date 20240905 --min-trades 500
```

### **Necessity: ✅ USEFUL**

**Reasons**:
- Trade data is essential for analysis
- Securities with orders but no trades are incomplete
- Customizable based on analysis needs

**Could Be Removed?**
- Yes, but same reasoning as --min-orders
- Flexibility is valuable for different use cases

**Recommendation**: ✅ **Keep** - Useful quality control knob

---

### **Threshold Parameters Summary**

| Parameter | Default | Purpose | Necessity |
|-----------|---------|---------|-----------|
| `--min-orders` | 10 | Skip securities with few orders | ✅ Useful |
| `--min-trades` | 10 | Skip securities with few trades | ✅ Useful |

**Both work together to define "valid" security**:
```python
valid_security = (
    sec.in_orders and sec.in_trades and
    sec.order_count >= min_orders and
    sec.trade_count >= min_trades
)
```

**Recommendation**: ✅ **Keep both** - Useful quality control

---

# 5. Pipeline Stage Parameters

## 5.1 `--stage` Parameter

### **Definition**
```python
# src/main.py:79-80
parser.add_argument('--stage', type=int, choices=[1, 2, 3, 4], action='append',
                    help='Pipeline stage(s) to run (can specify multiple): 1=extraction, 2=simulation, 3=analysis, 4=aggregation')
```

### **Where Used**
- **main.py**: Line 79 (definition), Line 124 (usage), Lines 180, 198, 204, 211 (validation)

### **What It Does**
```python
# main.py:124
stages = args.stage if args.stage else None  # None means run all stages

# Used throughout pipeline execution:
# - Stage 1: Data Extraction & Preparation (extract_orders, extract_trades, etc.)
# - Stage 2: Simulation & LOB States (sweep simulation, matching)
# - Stage 3: Per-Security Analysis (execution analysis, volume analysis)
# - Stage 4: Cross-Security Aggregation (aggregate results)
```

### **Purpose**
- Run specific stage(s) instead of full pipeline
- Useful for:
  - Re-running a stage after fixing bugs
  - Skipping expensive stages
  - Testing/debugging
- Can specify multiple: `--stage 1 --stage 2`

### **Usage Examples**
```bash
# Run all stages (default)
python src/main.py --orderbookid 85603 --date 20240905

# Run only Stage 1 (data extraction)
python src/main.py --orderbookid 85603 --date 20240905 --stage 1

# Run Stages 1 and 2 only
python src/main.py --orderbookid 85603 --date 20240905 --stage 1 --stage 2

# Run only Stage 4 (aggregation - no security needed)
python src/main.py --stage 4
```

### **Stage Descriptions**

| Stage | Name | What It Does | Requires Security? |
|-------|------|--------------|-------------------|
| **1** | Data Extraction | Extract orders/trades, partition, preprocess | ✅ Yes |
| **2** | Simulation | Sweep matching simulation, metrics | ✅ Yes |
| **3** | Analysis | Execution analysis, volume segmentation | ✅ Yes |
| **4** | Aggregation | Combine results across securities | ❌ No |

### **Necessity: ✅ VERY USEFUL**

**Reasons**:
- **Development**: Test individual stages
- **Debugging**: Re-run failed stage without full pipeline
- **Efficiency**: Skip completed stages
- **Flexibility**: Run aggregation only (Stage 4)

**Real-World Scenarios**:
```bash
# Scenario 1: Stage 2 failed, Stage 1 already complete
python src/main.py --orderbookid 85603 --date 20240905 --stage 2

# Scenario 2: Update aggregation logic, re-aggregate
python src/main.py --stage 4

# Scenario 3: Just extract data, no analysis yet
python src/main.py --orderbookid 85603 --date 20240905 --stage 1
```

**Could Be Removed?**
- Yes, always run full pipeline
- But development/debugging would be painful
- Would waste significant time re-running completed stages

**Recommendation**: ✅ **Keep** - Essential for development workflow

---

# 6. Processing Mode Parameters

## 6.1 `--parallel` Parameter

### **Definition**
```python
# src/main.py:83-84
parser.add_argument('--parallel', action='store_true',
                    help='Enable parallel processing for Stage 2 (overrides config)')
```

### **Where Used**
- **main.py**: Line 83 (definition), Lines 220-225 (usage)

### **What It Does**
```python
# main.py:220-225
if args.parallel:
    enable_parallel = True
elif args.sequential:
    enable_parallel = False
else:
    enable_parallel = config.ENABLE_PARALLEL_PROCESSING

# Used in Stage 2 to determine whether to use ProcessPoolExecutor
# partition_processor.py: process_partitions_parallel() vs sequential loop
```

### **Purpose**
- Override config setting to force parallel processing
- Useful when processing multiple securities with `--auto-discover`
- Speeds up Stage 2 (simulation) for multiple partitions

### **Usage Examples**
```bash
# Force parallel processing (override config)
python src/main.py --auto-discover --date 20240905 --parallel

# When processing single security, parallel doesn't help much
python src/main.py --orderbookid 85603 --date 20240905 --parallel
```

### **Necessity: ⚠️ NICE TO HAVE**

**Reasons**:
- **Performance**: Faster when processing multiple securities
- **Flexibility**: Override config without editing files
- **Testing**: Test parallel vs sequential behavior

**Could Be Removed?**
- Yes, users could edit config file
- But CLI override is more convenient
- No harm in keeping it

**Recommendation**: ✅ **Keep** - Useful performance tuning knob

---

## 6.2 `--sequential` Parameter

### **Definition**
```python
# src/main.py:85-86
parser.add_argument('--sequential', action='store_true',
                    help='Force sequential processing for Stage 2 (overrides config)')
```

### **Where Used**
- **main.py**: Line 85 (definition), Lines 220-225 (usage)

### **What It Does**
```python
# main.py:222-223
elif args.sequential:
    enable_parallel = False
```

### **Purpose**
- Override config to force sequential processing
- Useful for:
  - Debugging (parallel can hide errors)
  - Low-memory systems
  - Deterministic output order

### **Usage Examples**
```bash
# Force sequential processing (easier to debug)
python src/main.py --auto-discover --date 20240905 --sequential
```

### **Necessity: ⚠️ NICE TO HAVE**

**Reasons**:
- **Debugging**: Easier to trace errors sequentially
- **Resource Control**: Avoid overwhelming system
- **Deterministic**: Same order every run

**Could Be Removed?**
- Yes, users could edit config file
- Parallel mode has good error handling anyway

**Recommendation**: ✅ **Keep** - Useful debugging tool

---

### **Processing Mode Summary**

These are **mutually exclusive** CLI overrides:

| Parameter | Effect | Use Case |
|-----------|--------|----------|
| `--parallel` | Force parallel | Speed up multi-security processing |
| `--sequential` | Force sequential | Debugging, low memory |
| *(none)* | Use config default | Normal operation |

**Recommendation**: ✅ **Keep both** - Useful overrides

---

# 7. Statistical Testing Parameters

## 7.1 `--enable-stats` Parameter

### **Definition**
```python
# src/main.py:90-91
stats_group.add_argument('--enable-stats', action='store_true',
                    help='Enable statistical tests (t-tests, p-values, confidence intervals)')
```

### **Where Used**
- **main.py**: Line 90 (definition), Lines 228-233 (usage)
- **aggregate_volume_analysis.py**: Line 432 (usage)
- **analyze_aggregated_results.py**: Line 763 (usage)

### **What It Does**
```python
# main.py:228-233
if args.enable_stats:
    enable_stats = True
elif args.disable_stats:
    enable_stats = False
else:
    enable_stats = config.ENABLE_STATISTICAL_TESTS

# Creates StatisticsEngine with statistical testing enabled
stats_engine = StatisticsEngine(
    enable_stats=enable_stats,
    force_simple=config.FORCE_SIMPLE_STATS
)
```

### **Purpose**
- Enable advanced statistical tests:
  - T-tests (compare real vs simulated)
  - P-values (statistical significance)
  - Confidence intervals
  - Requires scipy library

### **Usage Examples**
```bash
# Enable statistical tests
python src/main.py --orderbookid 85603 --date 20240905 --enable-stats

# Output includes:
# - t-statistic: 2.45
# - p-value: 0.014
# - 95% CI: [1.23, 4.56]
```

### **Necessity: ⚠️ OPTIONAL ENHANCEMENT**

**Reasons**:
- **Academic Rigor**: Statistical significance testing
- **Publication Quality**: Needed for research papers
- **Validation**: Confirm results aren't random

**Could Be Removed?**
- Yes, basic descriptive statistics still work
- System has 3-tier stats (scipy → statistics → basic)
- Degrades gracefully if scipy not installed

**Recommendation**: ✅ **Keep** - Valuable for research use cases

---

## 7.2 `--disable-stats` Parameter

### **Definition**
```python
# src/main.py:92-93
stats_group.add_argument('--disable-stats', action='store_true',
                    help='Disable statistical tests (only descriptive statistics)')
```

### **Where Used**
- **main.py**: Line 92 (definition), Lines 230-233 (usage)
- **aggregate_volume_analysis.py**: Line 434 (usage)

### **What It Does**
```python
# main.py:230-231
elif args.disable_stats:
    enable_stats = False
```

### **Purpose**
- Force disable statistical tests even if config enables them
- Useful when:
  - scipy not installed
  - Want faster execution
  - Don't need statistical rigor

### **Usage Examples**
```bash
# Disable statistical tests (faster)
python src/main.py --orderbookid 85603 --date 20240905 --disable-stats
```

### **Necessity: ⚠️ OPTIONAL**

**Reasons**:
- Opposite of --enable-stats
- Provides explicit control
- Useful in environments without scipy

**Could Be Removed?**
- Yes, system already degrades gracefully
- --enable-stats alone might be sufficient

**Recommendation**: ⚠️ **Consider removing** - Redundant with config

---

### **Statistical Testing Summary**

These are **mutually exclusive** overrides:

| Parameter | Effect | Use Case |
|-----------|--------|----------|
| `--enable-stats` | Force enable | Research, publication |
| `--disable-stats` | Force disable | Speed, no scipy |
| *(none)* | Use config default | Normal operation |

**Recommendation**:
- ✅ **Keep `--enable-stats`** - Valuable for research
- ⚠️ **Remove `--disable-stats`** - Redundant (system degrades gracefully)

---

# 8. Summary & Recommendations

## Complete Parameter Assessment

| Parameter | Category | Necessity | Recommendation |
|-----------|----------|-----------|----------------|
| `--ticker` | Security | ✅ Useful | **KEEP** - Good UX |
| `--orderbookid` | Security | ✅✅ Essential | **KEEP** - Core identifier |
| `--auto-discover` | Security | ✅✅ Very Useful | **KEEP** - Batch processing |
| `--date` | Date | ✅✅ Essential | **KEEP** - Core requirement |
| `--list-securities` | Discovery | ✅✅ Very Useful | **KEEP** - Critical for UX |
| `--list-dates` | Discovery | ✅✅ Very Useful | **KEEP** - Critical for UX |
| `--min-orders` | Threshold | ✅ Useful | **KEEP** - Quality control |
| `--min-trades` | Threshold | ✅ Useful | **KEEP** - Quality control |
| `--stage` | Pipeline | ✅✅ Very Useful | **KEEP** - Dev workflow |
| `--parallel` | Processing | ✅ Useful | **KEEP** - Performance |
| `--sequential` | Processing | ✅ Useful | **KEEP** - Debugging |
| `--enable-stats` | Statistics | ✅ Useful | **KEEP** - Research value |
| `--disable-stats` | Statistics | ⚠️ Optional | **REMOVE?** - Redundant |

---

## Redundancy Analysis

### **Parameters Duplicated Across Files**

| Parameter | main.py | data_explorer.py | security_discovery.py | Reason |
|-----------|---------|------------------|----------------------|--------|
| `--ticker` | ✅ | ✅ | ❌ | Each is CLI entry point |
| `--orderbookid` | ✅ | ✅ | ❌ | Each is CLI entry point |
| `--auto-discover` | ✅ | ✅ | ❌ | Each is CLI entry point |
| `--date` | ✅ | ✅ | ✅ | Required by all |
| `--list-securities` | ✅ | ✅ | ❌ | Discovery feature |
| `--list-dates` | ✅ | ✅ | ✅ | Discovery feature |
| `--min-orders` | ✅ | ✅ | ✅ | Quality threshold |
| `--min-trades` | ✅ | ✅ | ✅ | Quality threshold |

**Duplication is acceptable because**:
- Each file is standalone CLI entry point
- Convenience for users
- Low maintenance burden

---

## Key Findings

### **1. Essential Parameters (Cannot Remove)**
- ✅✅ `--orderbookid` - Primary identifier in all data
- ✅✅ `--date` - Required for file paths and partitioning
- ✅✅ `--auto-discover` - Critical for batch processing
- ✅✅ `--stage` - Essential development tool

### **2. Very Useful Parameters (High Value)**
- ✅ `--list-securities` - Discovery workflow
- ✅ `--list-dates` - Discovery workflow
- ✅ `--ticker` - Better UX than orderbookid

### **3. Useful Parameters (Nice To Have)**
- ✅ `--min-orders` / `--min-trades` - Quality control
- ✅ `--parallel` / `--sequential` - Performance tuning
- ✅ `--enable-stats` - Research/publication

### **4. Potentially Redundant**
- ⚠️ `--disable-stats` - System degrades gracefully anyway

---

## Recommendations by Priority

### **Priority 1: Keep Everything Except One**

**Remove**:
- ❌ `--disable-stats` - Redundant, system degrades gracefully

**Keep All Others** - They all serve distinct, valuable purposes

### **Priority 2: Documentation**

Add to docstring/help:
```python
# Examples section should show:
# 1. Basic usage (orderbookid + date)
# 2. Discovery workflow (list-dates → list-securities → process)
# 3. Batch processing (auto-discover)
# 4. Stage-specific runs
# 5. Performance tuning (parallel)
```

### **Priority 3: Future Enhancements**

Consider adding:
- `--config FILE` - Use different config file
- `--output-dir DIR` - Override output directory
- `--dry-run` - Show what would be processed without running
- `--verbose` - Increase logging detail

---

## Parameter Usage Patterns

### **Pattern 1: New User Discovery**
```bash
# 1. What dates do I have?
python src/main.py --list-dates

# 2. What securities on that date?
python src/main.py --list-securities --date 20240905

# 3. Process one security
python src/main.py --orderbookid 85603 --date 20240905
```

### **Pattern 2: Batch Processing**
```bash
# Process all valid securities
python src/main.py --auto-discover --date 20240905 --parallel
```

### **Pattern 3: Development/Debug**
```bash
# Re-run Stage 2 only (simulation)
python src/main.py --orderbookid 85603 --date 20240905 --stage 2 --sequential
```

### **Pattern 4: Quality Control**
```bash
# Strict quality thresholds
python src/main.py --auto-discover --date 20240905 \
    --min-orders 1000 --min-trades 500
```

### **Pattern 5: Research/Publication**
```bash
# Enable statistical tests
python src/main.py --orderbookid 85603 --date 20240905 --enable-stats
```

---

## Conclusion

### **Overall Assessment**: ✅ Well-Designed CLI

**Strengths**:
- Clear parameter names
- Logical groupings (mutually exclusive where appropriate)
- Good defaults
- Flexible without being overwhelming

**Minor Issues**:
- One redundant parameter (--disable-stats)
- Some duplication across files (acceptable)

**Final Recommendation**: 
- Remove: `--disable-stats`
- Keep: All other parameters
- Document: Add more usage examples
- Monitor: Parameter duplication across files

The CLI is well-thought-out and serves user needs effectively.
