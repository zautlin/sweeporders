# Discovery Parameters Explained: --list-securities and --list-dates

## Quick Answer

### `--list-dates`
**Purpose**: Lists all available dates in your raw data files  
**Usage**: `python src/main.py --list-dates`  
**What it does**: Scans `data/raw/orders/` and `data/raw/trades/` directories and extracts dates from filenames  

### `--list-securities`
**Purpose**: Lists all available securities (stocks) for a specific date  
**Usage**: `python src/main.py --list-securities --date 20240905`  
**What it does**: Shows which securities have data on that date, how many orders/trades, and which ones are valid for processing

---

## Detailed Explanation

### 1. `--list-dates` Parameter

**Location**: `src/main.py:71` (argument definition), `src/main.py:103-112` (implementation)

**What it does**:
```python
if args.list_dates:
    dates = discovery.get_available_dates()  # Scan raw data directory
    print("\nAvailable dates in raw data:")
    if dates:
        for date in dates:
            print(f"  - {date}")
    else:
        print("  No data files found")
    print()
    exit(0)  # Exits immediately - doesn't run pipeline
```

**Implementation** (`security_discovery.py:69-94`):
```python
def get_available_dates(self) -> List[str]:
    """Scan raw data directory to find all available dates"""
    dates = set()
    
    # Scan order files in data/raw/orders/
    for file_path in orders_dir.glob('*_orders.csv'):
        # Extract date from filename pattern: {ticker}_{YYYYMMDD}_orders.csv
        date = self._extract_date_from_filename(file_path.name)
        if date:
            dates.add(date)  # e.g., "20240905"
    
    # Scan trade files in data/raw/trades/
    for file_path in trades_dir.glob('*_trades.csv'):
        date = self._extract_date_from_filename(file_path.name)
        if date:
            dates.add(date)
    
    return sorted(list(dates))
```

**Example Usage**:
```bash
$ python src/main.py --list-dates

Available dates in raw data:
  - 20240505
  - 20240905
```

**Use Case**: 
- You have multiple days of market data
- Want to quickly see which dates are available
- Before processing, check what data exists

---

### 2. `--list-securities` Parameter

**Location**: `src/main.py:69` (argument definition), `src/main.py:116-121` (implementation)

**What it does**:
```python
if args.list_securities:
    if not date:
        print("Error: --list-securities requires --date argument")
        exit(1)
    discovery.print_summary(date)  # Show all securities for that date
    exit(0)  # Exits immediately - doesn't run pipeline
```

**Implementation** (`security_discovery.py:253-300`):

1. **Discovers all securities** by scanning files like:
   - `data/raw/orders/cba_20240905_orders.csv` → orderbookid=85603, ticker="cba"
   - `data/raw/orders/bhp_20240905_orders.csv` → orderbookid=70616, ticker="bhp"
   - `data/raw/orders/drr_20240905_orders.csv` → orderbookid=110621, ticker="drr"

2. **Counts orders and trades** for each security

3. **Filters valid securities** based on thresholds:
   - Must have both orders AND trades files
   - Must have ≥ `min_orders` (default: 10)
   - Must have ≥ `min_trades` (default: 10)

4. **Prints formatted summary**:
```python
def print_summary(self, date: str):
    all_securities = self.discover_securities_for_date(date)
    valid_securities = self.get_valid_securities(date)
    
    print(f"Security Discovery Summary for {date}")
    print(f"Total securities found: {len(all_securities)}")
    print(f"Valid securities (meeting thresholds): {len(valid_securities)}")
    
    # Print table of valid securities
    # Print table of invalid securities with reasons
```

**Example Usage**:
```bash
$ python src/main.py --list-securities --date 20240905

================================================================================
Security Discovery Summary for 20240905
================================================================================

Total securities found: 4
Valid securities (meeting thresholds): 3
  - Minimum orders: 10
  - Minimum trades: 10

OrderbookID  Ticker   Orders       Trades       Status
--------------------------------------------------------------------------------
70616        BHP      156,234      45,678       ✓ Valid
85603        CBA      48,734       34,676       ✓ Valid
110621       DRR      7,523        2,456        ✓ Valid

Invalid securities (not meeting thresholds):
OrderbookID  Ticker   Orders       Trades       Reason
--------------------------------------------------------------------------------
124055       WTC      5            2            Orders < 10, Trades < 10
```

**Use Case**:
- You have multiple securities in your raw data
- Want to see what's available before processing
- Check if a security meets minimum thresholds
- Understand why a security might be skipped

---

## How They Work Together

These parameters are part of the **discovery workflow**:

```
Step 1: What dates do I have data for?
  → python src/main.py --list-dates
  → Output: 20240505, 20240905

Step 2: What securities exist on a specific date?
  → python src/main.py --list-securities --date 20240905
  → Output: CBA (85603), BHP (70616), DRR (110621)

Step 3: Process a specific security
  → python src/main.py --orderbookid 85603 --date 20240905
  → Runs full pipeline for CBA

Step 4: Process all valid securities automatically
  → python src/main.py --auto-discover --date 20240905
  → Runs pipeline for CBA, BHP, DRR (skips WTC - insufficient data)
```

---

## Code Flow Diagram

```
User runs: python src/main.py --list-dates
    ↓
main.py:103-112
    ↓
discovery.get_available_dates()
    ↓
security_discovery.py:69-94
    ↓
Scans data/raw/orders/*.csv and data/raw/trades/*.csv
    ↓
Extracts dates using regex: _(\d{8})_(?:orders|trades)\.csv
    ↓
Returns sorted list: ['20240505', '20240905']
    ↓
Prints dates and exit(0)
```

```
User runs: python src/main.py --list-securities --date 20240905
    ↓
main.py:116-121
    ↓
discovery.print_summary(date)
    ↓
security_discovery.py:253-300
    ↓
1. discover_securities_for_date(date)
   - Scans files: *_20240905_orders.csv and *_20240905_trades.csv
   - Extracts ticker from filename
   - Reads file to get orderbookid (security_code column)
   - Counts orders and trades
    ↓
2. get_valid_securities(date)
   - Filters: Must have both orders AND trades
   - Filters: order_count >= min_orders (default 10)
   - Filters: trade_count >= min_trades (default 10)
    ↓
3. Print formatted table
   - Valid securities with ✓ 
   - Invalid securities with reasons
    ↓
exit(0)
```

---

## Relationship to Other Parameters

### Used with `--auto-discover`

The `--auto-discover` parameter internally uses the same discovery logic:

```python
# main.py:129-141
if args.auto_discover:
    valid_securities = discovery.get_valid_securities(date)  # Same function!
    securities_to_process = valid_securities
    # Then runs pipeline for each security
```

**So these three are related**:
- `--list-securities`: Shows what securities exist (informational, exits)
- `--auto-discover`: Uses same discovery logic but then processes all valid securities
- `--orderbookid` or `--ticker`: Manually specify one security to process

### Used with threshold parameters

You can customize the thresholds:

```bash
# Default thresholds (min 10 orders, min 10 trades)
python src/main.py --list-securities --date 20240905

# Custom thresholds (min 1000 orders, min 500 trades)
python src/main.py --list-securities --date 20240905 --min-orders 1000 --min-trades 500
```

This filters out securities with insufficient data.

---

## Practical Examples

### Example 1: Exploring New Data

You just received new market data files:
```
data/raw/orders/
  ├── cba_20240905_orders.csv
  ├── bhp_20240905_orders.csv
  ├── wtc_20240905_orders.csv
  └── drr_20240905_orders.csv

data/raw/trades/
  ├── cba_20240905_trades.csv
  ├── bhp_20240905_trades.csv
  └── drr_20240905_trades.csv  (Note: WTC missing!)
```

**Step 1**: Check what dates exist
```bash
$ python src/main.py --list-dates
Available dates in raw data:
  - 20240905
```

**Step 2**: See what securities are available
```bash
$ python src/main.py --list-securities --date 20240905

Total securities found: 4
Valid securities: 3

OrderbookID  Ticker   Orders       Trades       Status
70616        BHP      156,234      45,678       ✓ Valid
85603        CBA      48,734       34,676       ✓ Valid
110621       DRR      7,523        2,456        ✓ Valid

Invalid securities:
OrderbookID  Ticker   Orders       Trades       Reason
4            WTC      31,545       0            No trades file  ← Missing!
```

**Step 3**: Process only valid securities
```bash
$ python src/main.py --auto-discover --date 20240905
# Automatically processes: CBA, BHP, DRR (skips WTC)
```

---

### Example 2: Quality Control

You want to ensure securities have enough data for meaningful analysis:

```bash
# Show securities with at least 1000 orders and 500 trades
$ python src/main.py --list-securities --date 20240905 \
    --min-orders 1000 --min-trades 500

Total securities found: 4
Valid securities: 2  ← Only 2 meet high threshold

OrderbookID  Ticker   Orders       Trades       Status
70616        BHP      156,234      45,678       ✓ Valid
85603        CBA      48,734       34,676       ✓ Valid

Invalid securities:
OrderbookID  Ticker   Orders       Trades       Reason
110621       DRR      523          256          Orders < 1000, Trades < 500
4            WTC      31,545       0            No trades file
```

---

### Example 3: Multiple Dates

You have data spanning multiple days:

```bash
$ python src/main.py --list-dates

Available dates in raw data:
  - 20240901
  - 20240902
  - 20240903
  - 20240904
  - 20240905

# Check each date
$ python src/main.py --list-securities --date 20240901
$ python src/main.py --list-securities --date 20240902
# ... etc

# Or process all dates programmatically
for date in 20240901 20240902 20240903; do
    python src/main.py --auto-discover --date $date
done
```

---

## When to Use These Parameters

### Use `--list-dates` when:
- ✅ You have multiple days of data
- ✅ Don't remember what dates are available
- ✅ Want to verify files are in correct naming format
- ✅ Checking if new data arrived

### Use `--list-securities` when:
- ✅ You have multiple securities in your raw data
- ✅ Want to check data quality (order/trade counts)
- ✅ Need to know which securities will be processed by `--auto-discover`
- ✅ Troubleshooting why a security isn't being processed
- ✅ Validating thresholds (`--min-orders`, `--min-trades`)

### Use `--auto-discover` when:
- ✅ You want to process ALL valid securities automatically
- ✅ Don't want to manually specify each orderbookid
- ✅ Have confidence in the discovery logic

---

## Summary Table

| Parameter | Requires | Action | Output | Runs Pipeline? |
|-----------|----------|--------|--------|----------------|
| `--list-dates` | None | Scans raw files | List of dates | ❌ No (exits) |
| `--list-securities` | `--date` | Scans files for date | Table of securities | ❌ No (exits) |
| `--auto-discover` | `--date` | Discovers + processes | Pipeline results | ✅ Yes |
| `--orderbookid` | `--date` | Direct specification | Pipeline results | ✅ Yes |
| `--ticker` | `--date` | Discovers orderbookid | Pipeline results | ✅ Yes |

---

## The Discovery Workflow

```
┌─────────────────────────┐
│  Raw Data Directory     │
│  data/raw/              │
│    orders/*.csv         │
│    trades/*.csv         │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  --list-dates           │
│  What dates exist?      │
│  Output: YYYYMMDD list  │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  --list-securities      │
│  What securities?       │
│  Output: Table with     │
│  orderbookid, ticker,   │
│  counts, validity       │
└────────┬────────────────┘
         │
         ├──────────────────────────┐
         ▼                          ▼
┌─────────────────────┐    ┌──────────────────────┐
│  --orderbookid      │    │  --auto-discover     │
│  Process one        │    │  Process all valid   │
│  manually           │    │  automatically       │
└─────────────────────┘    └──────────────────────┘
```

---

## Key Takeaways

1. **`--list-dates`** and **`--list-securities`** are **informational tools**
   - They help you explore what data is available
   - They exit immediately without running the pipeline
   - They're useful for data validation and planning

2. **They support the discovery workflow**
   - Use them BEFORE running the pipeline
   - Helps understand what will be processed by `--auto-discover`
   - Catches issues (missing files, insufficient data) early

3. **They're independent of processing**
   - Can be run anytime without affecting data
   - Safe to use repeatedly
   - No side effects (no file creation)

4. **They work with threshold parameters**
   - `--min-orders` and `--min-trades` affect what's "valid"
   - Useful for quality control
   - Same logic used by `--auto-discover`
