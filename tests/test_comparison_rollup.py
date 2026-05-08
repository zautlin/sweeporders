"""Tests for report.py's _comparison_rollup — aggregates trade_level_comparison
parquets into real-vs-sim summary rows.

Pre-fix: report.py only emits real-only rollups. The research question
("would dark have done better?") needs sim columns side-by-side with real,
plus distribution stats for the headline metrics, plus match_status counts.

Design (settled in chat):
- For each real metric M present in the comparison frame, emit:
    real_avg_M
    sim_avg_M_overlap          (mean over rows where sim_total_matches > 0)
    sim_avg_M_all_survivors    (mean with NULL sim_M coalesced to 0)
- For HEADLINE_METRICS (4 picked metrics), additionally emit:
    real_median_M, real_p25_M, real_p75_M
    sim_median_M_overlap, sim_p25_M_overlap, sim_p75_M_overlap
    sim_median_M_all_survivors, sim_p25_M_all_survivors, sim_p75_M_all_survivors
- Match-status counts: n_exact_match, n_close_match, n_partial_match,
  n_poor_match, n_other_match (catch-all for unknown statuses).
- Engagement rate: pct_with_sim_activity = n_with_sim / n_total.
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import polars as pl
import pytest

import report


def _fixture_df():
    """4 orders, single date+ticker. Two have sim activity (sim_total_matches>0),
    two do not (sim_total_matches NULL). Numbers chosen so means differ between
    overlap-only and all-survivors (NULL→0) flavours."""
    return pl.DataFrame({
        'orderid':                       [1,    2,    3,    4],
        'date':                          ['2024-09-05'] * 4,
        'ticker':                        ['aaa'] * 4,
        # Real metrics — all 4 rows have real fills
        'fill_rate_pct':                 [100.0, 90.0, 80.0, 70.0],   # mean = 85
        'exec_cost_arrival_bps':         [-1.0, -0.5, 0.5, 1.0],      # mean = 0
        'price_improvement_bps':         [2.0,  1.0,  3.0, 4.0],      # mean = 2.5
        'time_to_first_fill_sec':        [10.0, 20.0, 30.0, 40.0],    # mean = 25
        'total_execution_value':         [1000.0, 2000.0, 3000.0, 4000.0],  # mean=2500 (non-headline)
        # Sim metrics — only rows 1, 2 have sim activity
        'sim_total_matches':             [3,    1,    0,    0],
        'sim_fill_rate_pct':             [50.0, 80.0, None, None],    # overlap mean=65; all_survivors mean=(50+80+0+0)/4=32.5
        'sim_exec_cost_arrival_bps':     [2.0,  4.0,  None, None],    # overlap mean=3; all_survivors mean=(2+4)/4=1.5
        'sim_price_improvement_bps':     [5.0,  7.0,  None, None],    # overlap mean=6; all_survivors mean=(5+7)/4=3
        'sim_time_to_first_fill_sec':    [5.0,  15.0, None, None],    # overlap mean=10; all_survivors mean=(5+15)/4=5
        'sim_total_execution_value':     [500.0, 1500.0, None, None], # overlap mean=1000; all_survivors=(500+1500)/4=500
        # Match status
        'match_status':                  ['EXACT_MATCH', 'CLOSE_MATCH', 'POOR_MATCH', 'PARTIAL_MATCH'],
    })


def test_comparison_rollup_emits_real_avg():
    df = _fixture_df()
    out = report._comparison_rollup(df, ['date', 'ticker'])
    row = out.to_dicts()[0]

    assert row['real_avg_fill_rate_pct'] == pytest.approx(85.0)
    assert row['real_avg_exec_cost_arrival_bps'] == pytest.approx(0.0)
    assert row['real_avg_price_improvement_bps'] == pytest.approx(2.5)
    assert row['real_avg_time_to_first_fill_sec'] == pytest.approx(25.0)
    assert row['real_avg_total_execution_value'] == pytest.approx(2500.0)


def test_comparison_rollup_sim_overlap_only_excludes_no_sim_rows():
    """sim_avg_<metric>_overlap should exclude rows where sim_total_matches==0
    or sim_<metric> is NULL — i.e. only the engagement subset."""
    df = _fixture_df()
    out = report._comparison_rollup(df, ['date', 'ticker'])
    row = out.to_dicts()[0]

    assert row['sim_avg_fill_rate_pct_overlap'] == pytest.approx(65.0)
    assert row['sim_avg_exec_cost_arrival_bps_overlap'] == pytest.approx(3.0)
    assert row['sim_avg_price_improvement_bps_overlap'] == pytest.approx(6.0)
    assert row['sim_avg_time_to_first_fill_sec_overlap'] == pytest.approx(10.0)


def test_comparison_rollup_sim_all_survivors_coerces_null_to_zero():
    """sim_avg_<metric>_all_survivors should treat no-engagement rows as
    zero, capturing the 'dark would have failed to fill' signal."""
    df = _fixture_df()
    out = report._comparison_rollup(df, ['date', 'ticker'])
    row = out.to_dicts()[0]

    # 4 rows total: rows 1,2 contribute their values; rows 3,4 contribute 0.
    assert row['sim_avg_fill_rate_pct_all_survivors'] == pytest.approx(32.5)        # (50+80+0+0)/4
    assert row['sim_avg_exec_cost_arrival_bps_all_survivors'] == pytest.approx(1.5) # (2+4+0+0)/4
    assert row['sim_avg_price_improvement_bps_all_survivors'] == pytest.approx(3.0) # (5+7+0+0)/4
    assert row['sim_avg_time_to_first_fill_sec_all_survivors'] == pytest.approx(5.0) # (5+15+0+0)/4


def test_comparison_rollup_headline_metrics_have_distribution():
    """The 4 HEADLINE_METRICS get median + p25 + p75 columns (real + both
    sim flavours). Non-headline metrics (e.g. total_execution_value) do not."""
    df = _fixture_df()
    out = report._comparison_rollup(df, ['date', 'ticker'])
    cols = out.columns

    # Real headline distribution
    for m in ['fill_rate_pct', 'exec_cost_arrival_bps',
              'price_improvement_bps', 'time_to_first_fill_sec']:
        assert f'real_median_{m}' in cols, f'missing real_median_{m}'
        assert f'real_p25_{m}' in cols
        assert f'real_p75_{m}' in cols
        # Sim distributions in both flavours
        assert f'sim_median_{m}_overlap' in cols
        assert f'sim_p25_{m}_overlap' in cols
        assert f'sim_p75_{m}_overlap' in cols
        assert f'sim_median_{m}_all_survivors' in cols

    # Non-headline metric should NOT have median/p25/p75
    assert 'real_median_total_execution_value' not in cols
    assert 'sim_median_total_execution_value_overlap' not in cols


def test_comparison_rollup_match_status_counts():
    """Per-cut row counts by match_status, plus n_other_match catch-all."""
    df = _fixture_df()
    out = report._comparison_rollup(df, ['date', 'ticker'])
    row = out.to_dicts()[0]

    assert row['n_exact_match']   == 1
    assert row['n_close_match']   == 1
    assert row['n_partial_match'] == 1
    assert row['n_poor_match']    == 1
    assert row['n_other_match']   == 0
    assert row['n_total']         == 4


def test_comparison_rollup_catches_unknown_match_status():
    """If aggregate.py ever emits a status outside the canonical 4, it
    should surface in n_other_match rather than vanish."""
    df = _fixture_df().with_columns(
        pl.Series('match_status',
                  ['EXACT_MATCH', 'CLOSE_MATCH', 'WEIRD_NEW_STATUS', 'PARTIAL_MATCH'])
    )
    out = report._comparison_rollup(df, ['date', 'ticker'])
    row = out.to_dicts()[0]
    assert row['n_poor_match']  == 0
    assert row['n_other_match'] == 1


def test_comparison_rollup_engagement_rate():
    """pct_with_sim_activity = (rows with sim_total_matches > 0) / n_total."""
    df = _fixture_df()
    out = report._comparison_rollup(df, ['date', 'ticker'])
    row = out.to_dicts()[0]

    assert row['n_with_sim'] == 2
    assert row['n_total']    == 4
    assert row['pct_with_sim_activity'] == pytest.approx(50.0)


def test_comparison_rollup_groups_by_group_cols():
    """Multiple groups should produce one row per group."""
    df_a = _fixture_df()
    df_b = _fixture_df().with_columns(pl.lit('bbb').alias('ticker'))
    df = pl.concat([df_a, df_b])

    out = report._comparison_rollup(df, ['date', 'ticker'])
    assert out.shape[0] == 2
    tickers = sorted(out['ticker'].to_list())
    assert tickers == ['aaa', 'bbb']
