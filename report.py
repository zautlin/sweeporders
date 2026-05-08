"""sweeporders report.py - Stages 5+6 (per-security analysis + multi-cut rollups).

Reads:  data/outputs/{date}/{orderbookid}/real_trade_metrics.csv
        data/raw/orders/{ticker}_{rawdate}_orders.csv (for ticker + participant)
Writes: data/reports/
          ├── per_security/{date}_{orderbookid}.csv
          ├── by_day.csv
          ├── by_ticker.csv
          ├── by_participant.csv
          ├── by_volume_bucket.csv
          └── by_session_phase.csv

Aggregation cuts: day, ticker, participant, volume-bucket, session-phase.
(Sector deferred per user instruction.)

Run: python report.py                           # report over EVERYTHING in data/outputs/
     python report.py --dates 20240505,20240905
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import polars as pl

import config


# ─── headline metrics for distribution stats ──────────────────────────────
# These four metrics get full distribution (mean/median/p25/p75) in the
# comparison rollups. All other metrics get mean-only. Picked because they
# directly drive the routing-strategy question: did dark fill, did it cost
# less, did it price-improve, did it fill faster.
HEADLINE_METRICS = frozenset({
    "fill_rate_pct",
    "exec_cost_arrival_bps",
    "price_improvement_bps",
    "time_to_first_fill_sec",
})

# Canonical match_status values emitted by aggregate.py's comparison logic.
# Anything else falls through to n_other_match in the rollup so unknown
# statuses surface rather than vanish.
KNOWN_MATCH_STATUSES = ("EXACT_MATCH", "CLOSE_MATCH", "PARTIAL_MATCH", "POOR_MATCH")


# ─── metric columns ────────────────────────────────────────────────────────
# Numeric columns in real_trade_metrics.csv that we want to aggregate.
METRIC_COLUMNS = [
    "qty_filled",
    "order_quantity",
    "fill_ratio",
    "fill_rate_pct",
    "num_fills",
    "avg_fill_size",
    "vwap",
    "arrival_midpoint",
    "arrival_bid",
    "arrival_offer",
    "arrival_spread",
    "arrival_spread_bps",
    "limit_price",
    "price_improvement",
    "price_improvement_bps",
    "exec_cost_arrival_bps",
    "exec_cost_vw_bps",
    "effective_spread_pct",
    "slippage_bps",
    "implementation_shortfall_bps",
    "total_execution_value",
    "time_to_first_fill_sec",
    "execution_duration_sec",
    "total_duration_sec",
    "avg_time_between_fills",
    "vw_exec_time_sec",
    "first_fill_midpoint",
    "last_fill_midpoint",
    "market_drift_bps",
    "avg_execution_spread_bps",
    "spread_volatility_bps",
    "price_volatility_bps",
]


def _discover_partition_csvs(dates_filter: list[str] | None):
    """Yield (date_dir, orderbookid_dir, metrics_path) for every real_trade_metrics under data/outputs/."""
    root = Path(config.OUTPUTS_DIR)
    if not root.exists():
        return
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        if dates_filter is not None and date_dir.name not in dates_filter:
            compact = date_dir.name.replace("-", "")
            if compact not in dates_filter:
                continue
        for obid_dir in sorted(date_dir.iterdir()):
            if not obid_dir.is_dir():
                continue
            metrics = obid_dir / "real_trade_metrics.parquet"
            if metrics.exists():
                yield date_dir.name, obid_dir.name, metrics


def _build_ticker_map():
    """Map orderbookid (int) → ticker using raw orders files.
    Reads first row of each {ticker}_{date}_orders.csv to get its orderbookid."""
    import csv
    root = Path(config.RAW_DIR) / "orders"
    out: dict[int, str] = {}
    if not root.exists():
        return out
    for f in root.glob("*_orders.csv"):
        m = re.match(r"^([a-z]+)_(\d{8})_orders\.csv$", f.name)
        if not m:
            continue
        ticker = m.group(1)
        try:
            with open(f) as fh:
                row = next(csv.DictReader(fh), None)
            if row is None:
                continue
            for k in ("orderbookid", "security_code", "OrderBookId", "Id"):
                if k in row and row[k]:
                    obid = int(row[k])
                    out[obid] = ticker
                    break
        except Exception:
            continue
    return out


def _read_all_partitions(dates_filter):
    """Return (all_rows DataFrame with date+orderbookid+ticker, partitions list)."""
    ticker_map = _build_ticker_map()
    frames = []
    partitions = []
    for date_dir, obid_dir, metrics_path in _discover_partition_csvs(dates_filter):
        df = pl.read_parquet(metrics_path)
        try:
            obid_int = int(obid_dir)
        except ValueError:
            obid_int = None
        ticker = ticker_map.get(obid_int, "unknown") if obid_int is not None else "unknown"
        df = df.with_columns(
            pl.lit(date_dir).alias("date"),
            pl.lit(obid_dir).alias("orderbookid_partition"),
            pl.lit(ticker).alias("ticker"),
        )
        frames.append(df)
        partitions.append((date_dir, obid_dir, ticker, len(df)))

    if not frames:
        return pl.DataFrame(), partitions

    for i, df in enumerate(frames):
        casts = []
        for c in METRIC_COLUMNS:
            if c in df.columns:
                casts.append(pl.col(c).cast(pl.Float64, strict=False))
        if casts:
            frames[i] = df.with_columns(casts)

    all_rows = pl.concat(frames, how="diagonal_relaxed")
    return all_rows, partitions


def _available_metrics(df):
    return [c for c in METRIC_COLUMNS if c in df.columns]


def _rollup(df, group_cols):
    metrics = _available_metrics(df)
    if not metrics:
        return pl.DataFrame({c: [] for c in group_cols})
    agg_exprs = [pl.col(m).mean().alias(m) for m in metrics]
    agg_exprs.append(pl.col("orderid").n_unique().alias("n_orders"))
    if "qty_filled" in df.columns:
        agg_exprs.append(pl.col("qty_filled").sum().alias("total_qty_filled"))
    return df.group_by(group_cols).agg(agg_exprs).sort(group_cols)


def _real_metric_columns(df):
    """Real-side metric columns in a comparison frame: any column whose
    `sim_<name>` mirror also exists. Robust to schema drift between
    aggregate.py and the report.py METRIC_COLUMNS list."""
    sim_mirrors = {c[4:] for c in df.columns if c.startswith("sim_")}
    return [
        c for c in df.columns
        if c in sim_mirrors and df.schema[c].is_numeric()
    ]


def _comparison_rollup(df, group_cols):
    """Aggregate trade_level_comparison rows by `group_cols` into one row per
    group with real and sim metrics side-by-side.

    For each real metric M (any column with a `sim_M` mirror):
      real_avg_M
      sim_avg_M_overlap          — mean over rows with sim_total_matches > 0
      sim_avg_M_all_survivors    — mean with NULL sim_M coalesced to 0
                                   (captures the 'dark would have failed'
                                    signal for non-engagement rows)
    For HEADLINE_METRICS additionally: median, p25, p75 (all three flavours).

    Plus per-group counts:
      n_total, n_with_sim, pct_with_sim_activity
      n_exact_match, n_close_match, n_partial_match, n_poor_match
      n_other_match — catch-all for unknown match_status values
    """
    real_metrics = _real_metric_columns(df)
    has_sim_total = "sim_total_matches" in df.columns
    has_match_status = "match_status" in df.columns

    has_sim_engaged_expr = (
        (pl.col("sim_total_matches").fill_null(0) > 0)
        if has_sim_total
        else pl.lit(False)
    )

    agg_exprs = [pl.len().alias("n_total")]
    if has_sim_total:
        agg_exprs.append(has_sim_engaged_expr.sum().alias("n_with_sim"))

    if has_match_status:
        for status in KNOWN_MATCH_STATUSES:
            agg_exprs.append(
                (pl.col("match_status") == status).sum()
                .alias(f"n_{status.lower()}")
            )
        agg_exprs.append(
            (~pl.col("match_status").is_in(list(KNOWN_MATCH_STATUSES))).sum()
            .alias("n_other_match")
        )

    for m in real_metrics:
        sim_m = f"sim_{m}"
        agg_exprs.append(pl.col(m).mean().alias(f"real_avg_{m}"))
        if has_sim_total:
            sim_overlap = (
                pl.when(has_sim_engaged_expr)
                .then(pl.col(sim_m))
                .otherwise(None)
            )
            agg_exprs.append(sim_overlap.mean().alias(f"sim_avg_{m}_overlap"))
            agg_exprs.append(
                pl.col(sim_m).fill_null(0).mean().alias(f"sim_avg_{m}_all_survivors")
            )
        if m in HEADLINE_METRICS:
            agg_exprs.append(pl.col(m).median().alias(f"real_median_{m}"))
            agg_exprs.append(pl.col(m).quantile(0.25).alias(f"real_p25_{m}"))
            agg_exprs.append(pl.col(m).quantile(0.75).alias(f"real_p75_{m}"))
            if has_sim_total:
                sim_overlap = (
                    pl.when(has_sim_engaged_expr)
                    .then(pl.col(sim_m))
                    .otherwise(None)
                )
                agg_exprs.append(sim_overlap.median().alias(f"sim_median_{m}_overlap"))
                agg_exprs.append(sim_overlap.quantile(0.25).alias(f"sim_p25_{m}_overlap"))
                agg_exprs.append(sim_overlap.quantile(0.75).alias(f"sim_p75_{m}_overlap"))
                sim_all = pl.col(sim_m).fill_null(0)
                agg_exprs.append(sim_all.median().alias(f"sim_median_{m}_all_survivors"))
                agg_exprs.append(sim_all.quantile(0.25).alias(f"sim_p25_{m}_all_survivors"))
                agg_exprs.append(sim_all.quantile(0.75).alias(f"sim_p75_{m}_all_survivors"))

    rollup = df.group_by(group_cols).agg(agg_exprs).sort(group_cols)

    if has_sim_total:
        rollup = rollup.with_columns(
            (100.0 * pl.col("n_with_sim") / pl.col("n_total")).alias("pct_with_sim_activity")
        )

    return rollup


def _discover_comparison_partitions(dates_filter):
    """Yield (date_dir, orderbookid_dir, comparison_path) for every
    trade_level_comparison.parquet under data/outputs/."""
    root = Path(config.OUTPUTS_DIR)
    if not root.exists():
        return
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        if dates_filter is not None and date_dir.name not in dates_filter:
            compact = date_dir.name.replace("-", "")
            if compact not in dates_filter:
                continue
        for obid_dir in sorted(date_dir.iterdir()):
            if not obid_dir.is_dir():
                continue
            comp = obid_dir / "trade_level_comparison.parquet"
            if comp.exists():
                yield date_dir.name, obid_dir.name, comp


def _read_comparison_partitions(dates_filter):
    """Load all trade_level_comparison.parquet files into one polars frame
    with date / orderbookid_partition / ticker columns added."""
    ticker_map = _build_ticker_map()
    frames = []
    for date_dir, obid_dir, comp_path in _discover_comparison_partitions(dates_filter):
        df = pl.read_parquet(comp_path)
        try:
            obid_int = int(obid_dir)
        except ValueError:
            obid_int = None
        ticker = ticker_map.get(obid_int, "unknown") if obid_int is not None else "unknown"
        df = df.with_columns(
            pl.lit(date_dir).alias("date"),
            pl.lit(obid_dir).alias("orderbookid_partition"),
            pl.lit(ticker).alias("ticker"),
        )
        frames.append(df)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def _write_per_security(df, reports_dir):
    out_dir = reports_dir / "per_security"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (date, obid, ticker), part in df.group_by(["date", "orderbookid_partition", "ticker"]):
        fname = out_dir / f"{date}_{obid}_{ticker}.csv"
        part.write_csv(fname)
        written.append(fname)
    return written


def _write_empty_rollup(path, reason, group_cols):
    with open(path, "w") as f:
        f.write("# " + reason + "\n")
        f.write(",".join(group_cols + ["n_orders"]) + "\n")


def main():
    parser = argparse.ArgumentParser(
        prog="report.py",
        description="Stage 5+6: per-security summaries + multi-cut rollups."
    )
    parser.add_argument("--dates", default=None,
                        help="Comma-separated date filter; default = report on everything.")
    args = parser.parse_args()

    dates_filter = None
    if args.dates:
        dates_filter = [d.strip() for d in args.dates.split(",") if d.strip()]

    reports_dir = Path(config.REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"[report] scanning {config.OUTPUTS_DIR}" + (f" (dates={dates_filter})" if dates_filter else ""))
    df, partitions = _read_all_partitions(dates_filter)

    if df.is_empty():
        print("[report] no data found in data/outputs/. Run process.py + aggregate.py first.",
              file=sys.stderr)
        sys.exit(2)

    print(f"[report] loaded {len(df)} rows from {len(partitions)} partition(s):")
    for d, o, t, n in partitions:
        print(f"          {d} / {o} / {t}: {n} rows")

    # ── Per-security
    written = _write_per_security(df, reports_dir)
    print(f"[report] wrote {len(written)} per-security summaries → {reports_dir / 'per_security'}/")

    # ── by_day
    by_day = _rollup(df, ["date"])
    by_day.write_csv(reports_dir / "by_day.csv")
    print(f"[report] by_day.csv: {by_day.shape[0]} row(s)")

    # ── by_ticker
    by_ticker = _rollup(df, ["date", "ticker"])
    by_ticker.write_csv(reports_dir / "by_ticker.csv")
    print(f"[report] by_ticker.csv: {by_ticker.shape[0]} row(s)")

    # ── by_participant — requires participantid which isn't in real_trade_metrics.
    _write_empty_rollup(
        reports_dir / "by_participant.csv",
        "participantid not present in real_trade_metrics.csv — needs join against raw orders; deferred.",
        ["date", "ticker", "participantid"],
    )
    print("[report] by_participant.csv: stub (participantid not in current metrics — deferred)")

    # ── by_volume_bucket — compute quartile bucket from order_quantity.
    if "order_quantity" in df.columns:
        quartiles = df.select(
            pl.col("order_quantity").quantile(0.25).alias("q1"),
            pl.col("order_quantity").quantile(0.50).alias("q2"),
            pl.col("order_quantity").quantile(0.75).alias("q3"),
        ).to_dicts()[0]
        q1, q2, q3 = quartiles["q1"], quartiles["q2"], quartiles["q3"]

        def _bucket(x):
            return (
                pl.when(x < q1).then(pl.lit("Q1"))
                .when(x < q2).then(pl.lit("Q2"))
                .when(x < q3).then(pl.lit("Q3"))
                .otherwise(pl.lit("Q4"))
            )
        df_with_bucket = df.with_columns(_bucket(pl.col("order_quantity")).alias("volume_bucket"))
        by_volume = _rollup(df_with_bucket, ["date", "volume_bucket"])
        by_volume.write_csv(reports_dir / "by_volume_bucket.csv")
        print(f"[report] by_volume_bucket.csv: {by_volume.shape[0]} row(s) (quartiles: {q1:.0f}/{q2:.0f}/{q3:.0f})")
    else:
        _write_empty_rollup(
            reports_dir / "by_volume_bucket.csv",
            "order_quantity missing from metrics — cannot compute volume buckets.",
            ["date", "volume_bucket"],
        )
        print("[report] by_volume_bucket.csv: stub")

    # ── by_session_phase — requires matchingphase from raw data; deferred.
    _write_empty_rollup(
        reports_dir / "by_session_phase.csv",
        "matchingphase not present in real_trade_metrics.csv — needs join against session data; deferred.",
        ["date", "session_phase"],
    )
    print("[report] by_session_phase.csv: stub (session_phase not in current metrics — deferred)")

    print(f"\n[report] done → {reports_dir}/")


if __name__ == "__main__":
    main()
