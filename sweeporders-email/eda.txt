"""Exploratory Data Analysis (EDA) for SweepOrders raw data.

Scans data/raw/ and prints a structured report describing what's there
BEFORE you run the pipeline. Handles both server PascalCase and local
lowercase column schemas via config.normalize_column_names.

Usage:
    python eda.py                          # full report on data/raw/
    python eda.py --dates 20240505         # restrict to one trade-date
    python eda.py --tickers cba,bhp        # restrict to listed tickers
    python eda.py --quick                  # skip per-row scans (file inventory only)

Sections produced:
    1. File inventory          — files per folder, sizes, paired csv/parquet
    2. Tickers and dates       — distinct tickers (from filenames) and trade
                                 dates (from inside the files)
    3. Orders breakdown        — total rows, order-type histogram, sweep
                                 counts, qualifying-sweep counts, change-reason
                                 histogram, time range
    4. Trades breakdown        — total rows, deal-source histogram, side
                                 histogram, time range, orphan-id check
    5. NBBO coverage           — rows, distinct orderbookids, time range
    6. Session states          — distinct session names / types
    7. Reference & participants — distinct orderbookids, participant types
    8. Cross-checks            — orders-without-trades, duplicate raw files,
                                 schema mismatch warnings
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

import config
from config import normalize_column_names, col


REPO    = Path(__file__).resolve().parent
RAW_DIR = REPO / "data" / "raw"

# ── helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    line = "=" * 80
    print(f"\n{line}\n  {title}\n{line}")


def _subsection(title: str) -> None:
    print(f"\n  ── {title} ──")


def _hsize(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:6.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:6.1f} TB"


def _fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, (int, float)) else str(n)


def _fmt_ts(ns) -> str:
    """Nanosecond epoch → ISO 8601 string. Returns '∅' for null/zero."""
    if ns is None or pd.isna(ns) or ns == 0:
        return "∅"
    try:
        return pd.to_datetime(int(ns), unit="ns").strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        return f"<bad ts: {ns}>"


def _glob_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix in {".csv", ".parquet"})


def _read_filtered(path: Path, kind: str, dates_filter: list[str] | None,
                   tickers_filter: list[str] | None) -> pd.DataFrame | None:
    """Read a single file, normalize its columns, optionally filter by date / ticker."""
    if tickers_filter and not any(t in path.name.lower() for t in tickers_filter):
        return None
    try:
        if path.suffix == ".parquet":
            df = duckdb.sql(f"SELECT * FROM '{path}'").df()
        else:
            df = pd.read_csv(path, low_memory=False)
    except Exception as e:
        print(f"    (read error on {path.name}: {e})")
        return None
    df = normalize_column_names(df, kind)
    if dates_filter and "tradedate" in df.columns and len(df) > 0:
        try:
            date_str = pd.to_datetime(df["tradedate"], unit="ns").dt.strftime("%Y%m%d")
            df = df[date_str.isin(dates_filter)].copy()
        except (ValueError, OverflowError):
            pass
    return df


def _ticker_from_filename(p: Path) -> str | None:
    m = re.match(r"([a-zA-Z]+)_", p.name)
    return m.group(1).lower() if m else None


def _date_from_filename(p: Path) -> str | None:
    m = re.search(r"(\d{8})", p.name)
    return m.group(1) if m else None


def _value_counts_pretty(series, top_n: int = 20) -> str:
    """Return a multi-line right-aligned histogram of the top N values."""
    if series is None or len(series) == 0:
        return "    (empty)"
    counts = series.value_counts(dropna=False).head(top_n)
    lines = []
    for val, n in counts.items():
        v_str = "<NA>" if pd.isna(val) else str(val)
        lines.append(f"      {v_str:>20}  {_fmt_int(int(n)):>12}")
    if len(series.value_counts(dropna=False)) > top_n:
        remaining = len(series.value_counts(dropna=False)) - top_n
        lines.append(f"      ({remaining} more)")
    return "\n".join(lines) if lines else "    (empty)"


# ── section 1: file inventory ─────────────────────────────────────────────────

def section_file_inventory():
    _section("1. FILE INVENTORY (data/raw/)")
    if not RAW_DIR.exists():
        print(f"  data/raw/ does not exist at {RAW_DIR}")
        return
    total_files = 0
    total_bytes = 0
    for folder in ("orders", "trades", "nbbo", "session", "reference", "participants"):
        path = RAW_DIR / folder
        files = _glob_files(path)
        total_files += len(files)
        size = sum(f.stat().st_size for f in files)
        total_bytes += size
        if files:
            print(f"\n  {folder}/  ({len(files)} files, {_hsize(size)})")
            # group csv and parquet by base name
            by_base = {}
            for f in files:
                base = f.stem
                by_base.setdefault(base, []).append(f)
            for base, fs in sorted(by_base.items()):
                exts = ", ".join(f.suffix for f in sorted(fs))
                size = sum(f.stat().st_size for f in fs)
                print(f"      {base:40} [{exts:18}] {_hsize(size)}")
        else:
            print(f"\n  {folder}/  (empty or missing)")
    print(f"\n  TOTAL: {total_files} files, {_hsize(total_bytes)}")


# ── section 2: tickers and dates ──────────────────────────────────────────────

def section_tickers_and_dates(dates_filter, tickers_filter):
    _section("2. TICKERS AND DATES")
    orders_files = _glob_files(RAW_DIR / "orders")
    if tickers_filter:
        orders_files = [f for f in orders_files
                        if any(t in f.name.lower() for t in tickers_filter)]

    tickers_in_filename = sorted({t for f in orders_files
                                  if (t := _ticker_from_filename(f))})
    dates_in_filename = sorted({d for f in orders_files
                                if (d := _date_from_filename(f))})
    print(f"  Tickers in filenames ({len(tickers_in_filename)}):  {', '.join(tickers_in_filename) or '∅'}")
    print(f"  Dates   in filenames ({len(dates_in_filename)}):  {', '.join(dates_in_filename) or '∅'}")

    # Read actual TradeDate from each orders file
    print("\n  Trade dates inside files (from TradeDate / timestamp):")
    seen_dates = set()
    for f in orders_files:
        df = _read_filtered(f, "orders", dates_filter, tickers_filter)
        if df is None or len(df) == 0:
            continue
        ts_col = "tradedate" if "tradedate" in df.columns else "timestamp" if "timestamp" in df.columns else None
        if ts_col is None:
            print(f"      {f.name}: no tradedate / timestamp column")
            continue
        try:
            unique = (pd.to_datetime(df[ts_col], unit="ns")
                        .dt.strftime("%Y-%m-%d").unique())
            seen_dates.update(unique)
            print(f"      {f.name:40} {sorted(unique)}")
        except Exception as e:
            print(f"      {f.name}: {e}")
    print(f"\n  Distinct trade dates across all orders files: {sorted(seen_dates)}")


# ── section 3: orders breakdown ───────────────────────────────────────────────

def section_orders(dates_filter, tickers_filter):
    _section("3. ORDERS BREAKDOWN")
    orders_files = _glob_files(RAW_DIR / "orders")
    if tickers_filter:
        orders_files = [f for f in orders_files
                        if any(t in f.name.lower() for t in tickers_filter)]
    # Prefer .parquet over .csv when both exist for the same base
    by_base = {}
    for f in orders_files:
        by_base.setdefault(f.stem, []).append(f)
    chosen = [next((f for f in fs if f.suffix == ".parquet"), fs[0])
              for fs in by_base.values()]

    total_rows = 0
    total_sweeps = 0
    total_qualifying = 0
    by_type_all = pd.Series(dtype="int64")
    by_changereason_all = pd.Series(dtype="int64")
    distinct_orderbooks = set()
    earliest = None
    latest = None

    for f in chosen:
        df = _read_filtered(f, "orders", dates_filter, tickers_filter)
        if df is None or len(df) == 0:
            continue
        n = len(df)
        total_rows += n
        ot_col = "exchangeordertype" if "exchangeordertype" in df.columns else "ordertype"
        if ot_col in df.columns:
            counts = df[ot_col].value_counts()
            by_type_all = by_type_all.add(counts, fill_value=0)
            sweeps_n = int(counts.get(2048, 0))
            total_sweeps += sweeps_n
        if "changereason" in df.columns:
            by_changereason_all = by_changereason_all.add(
                df["changereason"].value_counts(), fill_value=0)
        if "orderbookid" in df.columns:
            distinct_orderbooks.update(df["orderbookid"].dropna().unique().tolist())
        if "timestamp" in df.columns and len(df) > 0:
            try:
                ts = pd.to_numeric(df["timestamp"], errors="coerce").dropna()
                if len(ts) > 0:
                    f_min, f_max = int(ts.min()), int(ts.max())
                    earliest = f_min if earliest is None else min(earliest, f_min)
                    latest   = f_max if latest   is None else max(latest, f_max)
            except Exception:
                pass

        # qualifying = sweeps that fully filled lit:
        #   final changereason==3 AND final leavesquantity==0 AND has changereason==6 event
        if all(c in df.columns for c in ("orderid", "exchangeordertype",
                                          "changereason", "leavesquantity",
                                          "timestamp")):
            sw = df[df[ot_col] == 2048].copy()
            if len(sw) > 0:
                sw_sorted = sw.sort_values(["orderid", "timestamp"])
                grp = sw_sorted.groupby("orderid")
                last_state = grp.tail(1).set_index("orderid")
                has_new = grp["changereason"].apply(lambda s: (s == 6).any())
                ok = (last_state["changereason"] == 3) & \
                     (last_state["leavesquantity"] == 0) & \
                     has_new.reindex(last_state.index, fill_value=False)
                total_qualifying += int(ok.sum())

    print(f"  Total order rows                   : {_fmt_int(total_rows)}")
    print(f"  Total Centre Point sweep rows (2048): {_fmt_int(total_sweeps)}")
    print(f"  Sweeps that qualify for simulation : {_fmt_int(total_qualifying)}")
    print(f"  Distinct orderbookids              : {len(distinct_orderbooks)}  {sorted(distinct_orderbooks)}")
    print(f"  Time range (timestamp)             : {_fmt_ts(earliest)}  →  {_fmt_ts(latest)}")

    _subsection("Order-type distribution (top 20)")
    by_type_all = by_type_all.astype(int).sort_values(ascending=False)
    if len(by_type_all):
        for ot, n in by_type_all.head(20).items():
            label = {64: "64", 256: "256", 2048: "2048 (sweep)",
                     4096: "4096", 4098: "4098"}.get(int(ot), str(int(ot)))
            print(f"      {label:>20}  {_fmt_int(int(n)):>12}")
        if len(by_type_all) > 20:
            print(f"      ({len(by_type_all)-20} more types)")
    else:
        print("      (no exchangeordertype / ordertype column)")

    _subsection("ChangeReason distribution (top 20)")
    by_changereason_all = by_changereason_all.astype(int).sort_values(ascending=False)
    if len(by_changereason_all):
        for cr, n in by_changereason_all.head(20).items():
            print(f"      {cr:>20}  {_fmt_int(int(n)):>12}")
    else:
        print("      (changereason column not present)")


# ── section 4: trades breakdown ───────────────────────────────────────────────

def section_trades(dates_filter, tickers_filter):
    _section("4. TRADES BREAKDOWN")
    trades_files = _glob_files(RAW_DIR / "trades")
    if tickers_filter:
        trades_files = [f for f in trades_files
                        if any(t in f.name.lower() for t in tickers_filter)]
    by_base = {}
    for f in trades_files:
        by_base.setdefault(f.stem, []).append(f)
    chosen = [next((f for f in fs if f.suffix == ".parquet"), fs[0])
              for fs in by_base.values()]

    total_rows = 0
    by_dealsource = pd.Series(dtype="int64")
    by_side = pd.Series(dtype="int64")
    earliest = None
    latest = None
    distinct_orders = 0

    for f in chosen:
        df = _read_filtered(f, "trades", dates_filter, tickers_filter)
        if df is None or len(df) == 0:
            continue
        total_rows += len(df)
        if "dealsource" in df.columns:
            by_dealsource = by_dealsource.add(df["dealsource"].value_counts(), fill_value=0)
        if "side" in df.columns:
            by_side = by_side.add(df["side"].value_counts(), fill_value=0)
        if "orderid" in df.columns:
            distinct_orders += df["orderid"].nunique()
        if "tradetime" in df.columns:
            try:
                ts = pd.to_numeric(df["tradetime"], errors="coerce").dropna()
                if len(ts) > 0:
                    earliest = int(ts.min()) if earliest is None else min(earliest, int(ts.min()))
                    latest   = int(ts.max()) if latest   is None else max(latest, int(ts.max()))
            except Exception:
                pass

    print(f"  Total trade rows           : {_fmt_int(total_rows)}")
    print(f"  Distinct order_ids touched : {_fmt_int(distinct_orders)}")
    print(f"  Time range (tradetime)     : {_fmt_ts(earliest)}  →  {_fmt_ts(latest)}")

    _subsection("DealSource distribution")
    by_dealsource = by_dealsource.astype(int).sort_values(ascending=False)
    if len(by_dealsource):
        for ds, n in by_dealsource.items():
            label = {1: "1 (lit/continuous)", 46: "46 (preference)",
                     47: "47 (centre point)", 50: "50 (APB)", 51: "51 (APB)"}.get(int(ds), str(int(ds)))
            print(f"      {label:>20}  {_fmt_int(int(n)):>12}")
    else:
        print("      (dealsource column not present)")

    _subsection("Side distribution")
    if len(by_side):
        by_side = by_side.astype(int)
        for s, n in by_side.items():
            label = {1: "1 (buy)", 2: "2 (sell)"}.get(int(s), str(int(s)))
            print(f"      {label:>20}  {_fmt_int(int(n)):>12}")
    else:
        print("      (side column not present)")


# ── section 5: NBBO ───────────────────────────────────────────────────────────

def section_nbbo(dates_filter, tickers_filter):
    _section("5. NBBO COVERAGE")
    files = _glob_files(RAW_DIR / "nbbo")
    if tickers_filter:
        files = [f for f in files
                 if any(t in f.name.lower() for t in tickers_filter)]
    by_base = {}
    for f in files:
        by_base.setdefault(f.stem, []).append(f)
    chosen = [next((f for f in fs if f.suffix == ".parquet"), fs[0])
              for fs in by_base.values()]
    if not chosen:
        print("  (no NBBO files found)")
        return
    total_rows = 0
    obids = set()
    earliest = None
    latest = None
    for f in chosen:
        df = _read_filtered(f, "nbbo", dates_filter, tickers_filter)
        if df is None or len(df) == 0:
            print(f"      {f.name:40} (empty after filters)")
            continue
        total_rows += len(df)
        if "orderbookid" in df.columns:
            obids.update(df["orderbookid"].dropna().unique().tolist())
        if "timestamp" in df.columns:
            try:
                ts = pd.to_numeric(df["timestamp"], errors="coerce").dropna()
                if len(ts) > 0:
                    earliest = int(ts.min()) if earliest is None else min(earliest, int(ts.min()))
                    latest   = int(ts.max()) if latest   is None else max(latest, int(ts.max()))
            except Exception:
                pass
        print(f"      {f.name:40} {_fmt_int(len(df))} rows")
    print(f"\n  Total NBBO rows      : {_fmt_int(total_rows)}")
    print(f"  Distinct orderbookids: {len(obids)}  {sorted(obids)}")
    print(f"  Time range           : {_fmt_ts(earliest)}  →  {_fmt_ts(latest)}")


# ── section 6: session ────────────────────────────────────────────────────────

def section_session(dates_filter):
    _section("6. SESSION STATES")
    files = _glob_files(RAW_DIR / "session")
    if not files:
        print("  (no session files found)")
        return
    by_base = {}
    for f in files:
        by_base.setdefault(f.stem, []).append(f)
    chosen = [next((f for f in fs if f.suffix == ".parquet"), fs[0])
              for fs in by_base.values()]
    for f in chosen:
        df = _read_filtered(f, "session", dates_filter, None)
        if df is None or len(df) == 0:
            print(f"      {f.name}: empty after filters")
            continue
        names = df["Name"] if "Name" in df.columns else df.get("name")
        types = df["Type"] if "Type" in df.columns else df.get("type")
        print(f"\n  {f.name}: {_fmt_int(len(df))} rows")
        if names is not None:
            print("    Distinct Name values:")
            for k, n in names.value_counts().items():
                print(f"      {str(k):>20}  {_fmt_int(int(n)):>12}")
        if types is not None:
            print("    Distinct Type values:")
            for k, n in types.value_counts().items():
                print(f"      {str(k):>20}  {_fmt_int(int(n)):>12}")


# ── section 7: reference & participants ───────────────────────────────────────

def section_reference_participants(dates_filter):
    _section("7. REFERENCE & PARTICIPANTS")
    for kind, folder in [("reference", "reference"), ("participants", "participants")]:
        files = _glob_files(RAW_DIR / folder)
        if not files:
            print(f"\n  {kind}/  (no files found)")
            continue
        by_base = {}
        for f in files:
            by_base.setdefault(f.stem, []).append(f)
        chosen = [next((f for f in fs if f.suffix == ".parquet"), fs[0])
                  for fs in by_base.values()]
        print(f"\n  {kind}/  ({len(chosen)} files)")
        for f in chosen:
            df = _read_filtered(f, kind, dates_filter, None)
            if df is None or len(df) == 0:
                print(f"      {f.name}: empty after filters")
                continue
            extras = []
            if kind == "reference" and "Name" in df.columns:
                names = df["Name"].dropna().unique().tolist()
                extras.append(f"names={sorted(names)[:10]}")
            if kind == "participants" and "ParticipantType" in df.columns:
                pt_counts = df["ParticipantType"].value_counts().to_dict()
                extras.append(f"participant_type_counts={pt_counts}")
            print(f"      {f.name}: {_fmt_int(len(df))} rows  {' '.join(extras)}")


# ── section 8: cross-checks ───────────────────────────────────────────────────

def section_cross_checks(dates_filter, tickers_filter):
    _section("8. CROSS-CHECKS")
    orders_files = _glob_files(RAW_DIR / "orders")
    trades_files = _glob_files(RAW_DIR / "trades")

    # Duplicate raw files (same csv + parquet for same base — informational)
    pairs = {}
    for f in orders_files:
        pairs.setdefault(f.stem, []).append(f.suffix)
    csv_only = [b for b, exts in pairs.items() if exts == [".csv"]]
    pq_only  = [b for b, exts in pairs.items() if exts == [".parquet"]]
    both     = [b for b, exts in pairs.items() if set(exts) == {".csv", ".parquet"}]
    if csv_only:
        print(f"  orders/  csv-only   : {csv_only}")
    if pq_only:
        print(f"  orders/  parquet-only: {pq_only}")
    if both:
        print(f"  orders/  csv+parquet (parquet preferred at read): {len(both)} bases")

    # Orders without trades and vice-versa (filename-based)
    o_bases = {f.stem.replace("_orders", "") for f in orders_files}
    t_bases = {f.stem.replace("_trades", "") for f in trades_files}
    orphans_orders = sorted(o_bases - t_bases)
    orphans_trades = sorted(t_bases - o_bases)
    if orphans_orders:
        print(f"  orders without matching trades file: {orphans_orders}")
    if orphans_trades:
        print(f"  trades without matching orders file: {orphans_trades}")
    if not orphans_orders and not orphans_trades:
        print("  ✓ orders and trades are paired by filename")

    # Detect schema family for each orders file
    print("\n  Schema family per orders file:")
    for f in sorted(orders_files):
        if f.suffix not in {".csv", ".parquet"}:
            continue
        try:
            if f.suffix == ".parquet":
                cols = duckdb.sql(f"DESCRIBE SELECT * FROM '{f}'").df().column_name.tolist()
            else:
                cols = pd.read_csv(f, nrows=0).columns.tolist()
        except Exception as e:
            print(f"      {f.name:40}  read error: {e}")
            continue
        is_pascal = any(c in cols for c in ("OrderId", "ExchangeOrderType", "OrderBookId"))
        is_local  = any(c in cols for c in ("order_id", "exchangeordertype", "security_code"))
        family = "PascalCase (server)" if is_pascal else "lowercase (local)" if is_local else "unknown"
        print(f"      {f.name:40}  {family}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(prog="eda.py",
        description="Exploratory data analysis on data/raw/ — run before processing.")
    p.add_argument("--dates",   default=None, help="Comma-separated YYYYMMDD filter.")
    p.add_argument("--tickers", default=None, help="Comma-separated ticker filter (filename substring).")
    p.add_argument("--quick",   action="store_true",
                   help="Skip per-row scans; print file inventory + filename-based facts only.")
    args = p.parse_args()

    dates_filter   = [d.strip() for d in args.dates.split(",")] if args.dates else None
    tickers_filter = [t.strip().lower() for t in args.tickers.split(",")] if args.tickers else None

    if dates_filter:   print(f"[eda] dates filter   : {dates_filter}")
    if tickers_filter: print(f"[eda] tickers filter : {tickers_filter}")
    if args.quick:     print("[eda] quick mode (file inventory only)")

    section_file_inventory()
    section_tickers_and_dates(dates_filter, tickers_filter)
    if not args.quick:
        section_orders(dates_filter, tickers_filter)
        section_trades(dates_filter, tickers_filter)
        section_nbbo(dates_filter, tickers_filter)
        section_session(dates_filter)
        section_reference_participants(dates_filter)
    section_cross_checks(dates_filter, tickers_filter)

    print("\n" + "=" * 80)
    print("  EDA complete.  Run process.py / aggregate.py / report.py next.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
