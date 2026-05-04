"""sweeporders config — single source of truth for paths, schema, and tunables.

Merged from src/config/{config, column_schema, system_config}.py during the lean port.
USE_POLARS_TRANSFORMS flag retained; USE_DUCKDB_IO dropped (parquet-only on _parq).
NBBO_SOURCE 'EXTERNAL' branch dropped (INTERNAL only).
PROCESSING_MODE 'stream' option removed.
ENABLE_PARALLEL_PROCESSING flipped to True for the lean port.
AGGREGATED_DIR renamed to REPORTS_DIR to match new flat layout (data/reports/).
"""
from __future__ import annotations

import multiprocessing
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

try:
    import psutil
except ImportError:
    psutil = None


# ── Path constants ─────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).parent.resolve()
DATA_DIR      = PROJECT_ROOT / "data"
RAW_DIR       = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUTS_DIR   = DATA_DIR / "outputs"
REPORTS_DIR   = DATA_DIR / "reports"   # renamed from AGGREGATED_DIR

# Raw data sub-folders (mirrors legacy RAW_FOLDERS dict)
RAW_FOLDERS = {
    'orders':       str(RAW_DIR / 'orders'),
    'trades':       str(RAW_DIR / 'trades'),
    'nbbo':         str(RAW_DIR / 'nbbo'),
    'session':      str(RAW_DIR / 'session'),
    'reference':    str(RAW_DIR / 'reference'),
    'participants': str(RAW_DIR / 'participants'),
}


# ── Order type / dealsource / NBBO constants ───────────────────────────────────
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}   # ALL CP types, including sweep-to-sweep
CENTRE_POINT_ORDER_TYPES = [64, 256, 2048, 4096, 4098]

# NBBO sentinel: indicates unavailable national_bid / national_offer
INT64_SENTINEL = -9223372036854775808

# Order side values


# ── Tunable defaults ───────────────────────────────────────────────────────────
TICKER = 'drr'
DATE   = '20240905'
MIN_ORDERS_THRESHOLD = 100   # Ignore securities with < 100 orders
MIN_TRADES_THRESHOLD = 10    # Ignore securities with < 10 trades

ENABLE_PARALLEL_PROCESSING = True   # flipped to True for the lean port (was False)

PROCESSING_MODE  = 'file'      # 'file' | 'memory'  (stream removed)
NBBO_SOURCE      = 'INTERNAL'  # Only INTERNAL supported; EXTERNAL branch dropped

# In-memory transforms — Polars-vectorised replacements for the pandas
# transforms in _prepare_sweep_orders / _prepare_all_orders_for_matching /
# Stage 3 metrics calc. Off by default (parity baseline assumes pandas path).
USE_POLARS_TRANSFORMS = False

VOLUME_BUCKET_METHOD     = 'quartile'                    # 'quartile', 'quintile', or 'custom'

# Security auto-discovery
AUTO_DISCOVERY_ENABLED = True

# Legacy tickers kept for backward compatibility

# Statistical scenario thresholds

# Stage names

# Any Price Block minimum block size (§24.1.3)
MIN_BLOCK_SIZE = 0   # APB minimum traded value (qty * price); 0 = disabled


# ── Input file helpers ─────────────────────────────────────────────────────────

def get_input_files(ticker=None, date=None):
    """Build input file paths from ticker and date. Prefers .parquet over .csv when both exist.

    Run convert_raw.py once to materialise parquet copies; subsequent pipeline
    runs then bypass the slow CSV parser.
    """
    ticker = ticker or TICKER
    date   = date   or DATE

    base = RAW_DIR
    stems = {
        'orders':       f'orders/{ticker}_{date}_orders',
        'trades':       f'trades/{ticker}_{date}_trades',
        'nbbo':         f'nbbo/{ticker}_{date}_nbbo',
        'session':      f'session/{date}_session',
        'reference':    f'reference/{date}_ob',
        'participants': f'participants/{date}_par',
    }
    out = {}
    for k, stem in stems.items():
        pq = base / f'{stem}.parquet'
        out[k] = str(pq) if pq.exists() else str(base / f'{stem}.csv')
    return out


# Default INPUT_FILES using config defaults
INPUT_FILES = get_input_files()

# Output file name constants. Intermediates use Parquet (zstd); final reports stay CSV.

# Calculated/intermediate column documentation (not accessed via col.* — internal only)


# ── Column normalization (raw schema → canonical) ──────────────────────────────
# Single source of truth for raw-vs-canonical column-name dialects.
# Add server-side aliases here when porting between environments — no other code
# changes are required because every freshly-read DataFrame in process.py /
# aggregate.py runs through `normalize_column_names(df, kind)` before being
# written to data/processed/.

COLUMN_NORMALIZATION_MAP: Dict[str, Dict[str, str]] = {
    'orders': {
        # Local-raw aliases
        'order_id':                   'orderid',
        'security_code':              'orderbookid',
        'securitycode':               'orderbookid',
        'SecurityCode':               'orderbookid',
        'totalmatchedquantity':       'matched_quantity',
        # Server-raw (PascalCase) aliases
        'OrderId':                    'orderid',
        'OrderBookId':                'orderbookid',
        'TradeDate':                  'tradedate',
        'Timestamp':                  'timestamp',
        'TimeChanged':                'timechanged',
        'TimeCreated':                'timecreated',
        'TimeValidity':               'timevalidity',
        'Sequence':                   'sequence',
        'Side':                       'side',
        'Price':                      'price',
        'OrderQuantity':              'quantity',
        'LeavesQuantity':             'leavesquantity',
        'TotalMatchedQuantity':       'matched_quantity',
        'DisplayQuantity':            'displayquantity',
        'DeltaQuantity':              'deltaquantity',
        'ShortSellQuantity':          'shortsellquantity',
        'ChangeReason':               'changereason',
        'OrderStatus':                'orderstatus',
        'OrderStatusBefore':          'orderstatusbefore',
        'OrderType':                  'ordertype',
        'ExchangeOrderType':          'exchangeordertype',
        'OrderCategory':              'ordercategory',
        'OrderBookPosition':          'orderbookposition',
        'ParticipantId':              'participantid',
        'BidPriceSnapshot':           'bid',
        'OfferPriceSnapshot':         'offer',
        'NationalBidPriceSnapshot':   'national_bid',
        'NationalOfferPriceSnapshot': 'national_offer',
        'MidTick':                    'midtick',
        'MinimumQuantity':            'minimumquantity',
        'SingleFillMinimumQuantity':  'singlefillminimumquantity',
        'PreferenceOnly':             'preferenceonly',
        'TriggerCondition':           'triggercondition',
        'TriggerPrice':               'triggerprice',
        'TriggerOrderBookId':         'triggerorderbookid',
        'TriggerSessionType':         'triggersessiontype',
        'CrossingKey':                'crossingkey',
        'BlockSize':                  'blocksize',
        'CounterOrderAttributes':     'counterorderattributes',
        'ParticipantOrderAttribute':  'participantorderattribute',
        'TransactionStatus':          'transactionstatus',
        'TradeReportCode':            'tradereportcode',
        'SubmitterId':                'submitterid',
        'UserId':                     'userid',
        'AccountId':                  'accountid',
        'ClientOrderId':              'clientorderid',
        'PreviousOrderId':            'previousorderid',
        'GiveUpParticipant':          'giveupparticipant',
        'OnBehalfOfSubmitterId':      'onbehalfofsubmitterid',
        'TransferFromUserId':         'transferfromuserid',
        'CustomerInfo':               'customerinfo',
        'ExchangeInfo':               'exchange',
        'RegulatoryData':             'regulatorydata',
        'RankingTime':                'rankingtime',
        'RequestedPosition':          'requestedposition',
        'MessageName':                'messagename',
        'isReloaded':                 'isreloaded',
    },
    'trades': {
        # Local-raw aliases
        'order_id':             'orderid',
        'security_code':        'orderbookid',
        'securitycode':         'orderbookid',
        # Server-raw (PascalCase) aliases
        'OrderId':              'orderid',
        'OrderBookId':          'orderbookid',
        'TradeDate':            'tradedate',
        'TimeStamp':            'timestamp',
        'Timestamp':            'timestamp',
        'ExecutionTimestamp':   'tradetime',
        'ModifiedTime':         'modifiedtime',
        'TimeOfAgreement':      'timeofagreement',
        'Sequence':             'sequence',
        'Side':                 'side',
        'Price':                'tradeprice',
        'ExtendedPrice':        'extendedprice',
        'Quantity':             'quantity',
        'ShortSellQuantity':    'shortsellquantity',
        'DealSource':           'dealsource',
        'OrderType':            'ordertype',
        'ExchangeOrderType':    'exchangeordertype',
        'ParticipantId':        'participantid',
        'CounterOrderCapacity': 'counterordercapacity',
        'MatchGroupId':         'matchgroupid',
        'CombinationMatchId':   'combinationmatchid',
        'CombinationOrderBookId': 'combinationorderbookid',
        'TradeNumber':          'tradenumber',
        'TradeType':            'tradetype',
        'TradeCondition':       'tradecondition',
        'TradeReportCode':      'tradereportcode',
        'TradeReportAttribute': 'tradereportattribute',
        'TradeSlipNumber':      'tradeslipnumber',
        'BigAttention':         'bigattention',
        'OpenCloseReq':         'opencloseraq',
        'AsOf':                 'asof',
        'SettlementDate':       'settlementdate',
        'UserId':                'userid',
        'AccountId':             'accountid',
        'GiveUpParticipant':     'giveupparticipant',
        'CustomerInfo':          'customerinfo',
        'ExchangeInfo':          'exchangeinfo',
        'RegulatoryData':        'regulatorydata',
    },
    'nbbo': {
        # Local-raw aliases
        'security_code':        'orderbookid',
        'securitycode':         'orderbookid',
        'bidprice':             'bid',
        'offerprice':           'offer',
        'bidquantity':          'bid_quantity',
        'offerquantity':        'offer_quantity',
        # Server-raw (PascalCase) aliases
        'OrderBookId':          'orderbookid',
        'Timestamp':            'timestamp',
        'TradeDate':            'tradedate',
        'BidPrice':             'bid',
        'OfferPrice':           'offer',
        'BidQuantity':          'bid_quantity',
        'OfferQuantity':        'offer_quantity',
        'Sequence':             'sequence',
    },
    'session': {
        'OrderBookId':          'orderbookid',
        'TradeDate':            'timestamp',
    },
    'reference': {
        'Id':                   'orderbookid',
        'TradeDate':            'timestamp',
    },
    'participants': {
        'TradeDate':            'timestamp',
    },
}


def normalize_column_names(df, data_type: str):
    """Rename a freshly-read DataFrame's columns to canonical names.

    `data_type` ∈ {'orders', 'trades', 'nbbo', 'session', 'reference', 'participants'}.
    Unknown data_type → DataFrame returned unchanged. Pandas DataFrame in / out.

    After rename, derives missing canonical columns from server schemas that
    don't carry them directly (e.g. server ORDERS has no Timestamp column —
    we synthesize it from TimeChanged so the simulator's event-time logic works).
    """
    if data_type not in COLUMN_NORMALIZATION_MAP:
        return df
    norm_map = COLUMN_NORMALIZATION_MAP[data_type]
    rename_dict = {col: norm_map[col] for col in df.columns if col in norm_map}
    df = df.rename(columns=rename_dict) if rename_dict else df

    # Server-schema fallbacks — server ORDERS rows carry TimeChanged / TimeCreated
    # but no Timestamp; simulator's _prepare_sweep_orders treats timechanged as
    # optional and fills from timestamp, so we only need to ensure 'timestamp'
    # exists. The reverse (synthesizing timechanged) is intentionally NOT done
    # — that would inject a phantom column on local data and break parity.
    if data_type == 'orders':
        if 'timestamp' not in df.columns and 'timechanged' in df.columns:
            df['timestamp'] = df['timechanged']
    elif data_type == 'trades':
        # Server TRADES has ExecutionTimestamp (→ tradetime), TimeStamp (→ timestamp),
        # ModifiedTime. Defensive: if tradetime is missing but timestamp exists,
        # use it; the simulator and metrics calc both key on tradetime.
        if 'tradetime' not in df.columns and 'timestamp' in df.columns:
            df['tradetime'] = df['timestamp']

    return df


# ── COLUMN_MAPPING ─────────────────────────────────────────────────────────────

COLUMN_MAPPING: Dict[str, Dict[str, str]] = {
    # ── Orders file columns ───────────────────────────────────────────────────
    'orders': {
        'order_id':         'order_id',
        'timestamp':        'timestamp',
        'sequence':         'sequence',
        'order_type':       'exchangeordertype',
        'security_code':    'security_code',
        'securitycode':     'securitycode',   # Alternative name
        'side':             'side',
        'quantity':         'quantity',
        'price':            'price',
        'bid':              'bid',
        'offer':            'offer',
        'national_bid':     'national_bid',
        'national_offer':   'national_offer',
        'leaves_quantity':  'leavesquantity',
        'matched_quantity': 'totalmatchedquantity',
        'order_status':     'orderstatus',
        'change_reason':    'changereason',
        'participant_id':   'participantid',
    },

    # ── Trades file columns ───────────────────────────────────────────────────
    'trades': {
        'order_id':                  'orderid',
        'trade_time':                'tradetime',
        'trade_price':               'tradeprice',
        'quantity':                  'quantity',
        'dealsource':                'dealsource',
        'dealsource_decoded':        'dealsourcedecoded',
        'passive_aggressive':        'passiveaggressive',
        'match_group_id':            'matchgroupid',
        'national_bid_snapshot':     'nationalbidpricesnapshot',
        'national_offer_snapshot':   'nationalofferpricesnapshot',
        'participant_id':            'participantid',
        'exchange_info':             'exchangeinfo',
        'side_decoded':              'sidedecoded',
        'exchange':                  'EXCHANGE',
        'security_code':             'securitycode',
        'trade_date':                'tradedate',
        'row_num':                   'row_num',
    },

    # ── NBBO file columns ─────────────────────────────────────────────────────
    'nbbo': {
        'timestamp':      'timestamp',
        'security_code':  'orderbookid',
        'bid':            'bidprice',
        'offer':          'offerprice',
        'bid_quantity':   'bidquantity',
        'offer_quantity': 'offerquantity',
        'national_bid':   'bid',    # After normalization, bidprice -> bid
        'national_offer': 'offer',  # After normalization, offerprice -> offer
    },

    # ── Session file columns ──────────────────────────────────────────────────
    'session': {
        'timestamp':     'TradeDate',
        'security_code': 'OrderBookId',
    },

    # ── Reference file columns ────────────────────────────────────────────────
    'reference': {
        'timestamp':     'TradeDate',
        'security_code': 'Id',
    },

    # ── Participants file columns ──────────────────────────────────────────────
    'participants': {
        'timestamp': 'TradeDate',
    },

    # ── Sweep order analysis columns ──────────────────────────────────────────
    'sweep': {
        'sweep_orderid':  'sweep_orderid',
        'orderid':        'orderid',
        'order_quantity': 'order_quantity',
        'orderbookid':    'orderbookid',
        'ticker':         'ticker',
        'date':           'date',
        'timestamp':      'timestamp',
        'side':           'side',
        'price':          'price',
        'arrival_bid':    'arrival_bid',
        'arrival_offer':  'arrival_offer',
    },

    # ── Metrics columns (calculated execution metrics) ─────────────────────────
    'metrics': {
        'qty_filled':              'qty_filled',
        'fill_rate_pct':           'fill_rate_pct',
        'fill_rate':               'fill_rate',
        'num_fills':               'num_fills',
        'avg_fill_size':           'avg_fill_size',
        'vwap':                    'vwap',
        'exec_cost_arrival_bps':   'exec_cost_arrival_bps',
        'exec_cost_vw_bps':        'exec_cost_vw_bps',
        'effective_spread_pct':    'effective_spread_pct',
        'execution_duration_sec':  'execution_duration_sec',
        'time_to_first_fill_sec':  'time_to_first_fill_sec',
        'vw_exec_time_sec':        'vw_exec_time_sec',
        'first_execution':         'first_execution',
        'last_execution':          'last_execution',
    },

    # ── Simulation columns (dark pool simulation results) ──────────────────────
    'simulation': {
        'simulated_qty_filled':       'simulated_qty_filled',
        'simulated_fill_ratio':       'simulated_fill_ratio',
        'simulated_fill_status':      'simulated_fill_status',
        'simulated_matched_quantity': 'simulated_matched_quantity',
        'simulated_num_fills':        'simulated_num_fills',
        'simulated_vwap':             'simulated_vwap',
        'simulated_exec_cost':        'simulated_exec_cost',
        'match_status':               'match_status',
    },

    # ── Comparison columns (real vs simulated) ─────────────────────────────────
    'comparison': {
        'real_matched_quantity':       'real_matched_quantity',
        'simulated_matched_quantity':  'simulated_matched_quantity',
        'matched_quantity_diff':       'matched_quantity_diff',
        'qty_filled_diff':             'qty_filled_diff',
        'fill_rate_diff':              'fill_rate_diff',
        'vwap_diff':                   'vwap_diff',
        'exec_cost_diff':              'exec_cost_diff',
        'exec_time_diff':              'exec_time_diff',
        'exec_cost_arrival_diff_bps':  'exec_cost_arrival_diff_bps',
        'exec_cost_vw_diff_bps':       'exec_cost_vw_diff_bps',
        'exec_time_diff_sec':          'exec_time_diff_sec',
        'better_execution':            'better_execution',
        'price_error_pct':             'price_error_pct',
    },

    # ── Statistical analysis columns ───────────────────────────────────────────
    'stats': {
        't_statistic':                't_statistic',
        'p_value':                    'p_value',
        'mean_diff':                  'mean_diff',
        'effect_size':                'effect_size',
        'cohens_d':                   'cohens_d',
        'confidence_interval_lower':  'ci_95_lower',
        'confidence_interval_upper':  'ci_95_upper',
        'significant':                'significant',
        'significance':               'significance',
        'f_statistic':                'f_statistic',
        'pearson_correlation':        'pearson_correlation',
        'spearman_correlation':       'spearman_correlation',
    },

    # ── Volume analysis columns ────────────────────────────────────────────────
    'volume': {
        'volume_bucket':                'volume_bucket',
        'volume_bucket_label':          'volume_bucket_label',
        'n_orders':                     'n_orders',
        'min_quantity':                 'min_quantity',
        'max_quantity':                 'max_quantity',
        'mean_quantity':                'mean_quantity',
        'mean_exec_cost_diff_bps':      'mean_exec_cost_diff_bps',
        'mean_exec_time_diff_sec':      'mean_exec_time_diff_sec',
        'dark_pool_better_pct':         'dark_pool_better_pct',
        'weighted_exec_cost_diff_bps':  'weighted_exec_cost_diff_bps',
        'weighted_exec_time_diff_sec':  'weighted_exec_time_diff_sec',
    },

    # ── Aggregated analysis columns (cross-security) ───────────────────────────
    'aggregated': {
        'ticker':        'ticker',
        'date':          'date',
        'orderbookid':   'orderbookid',
        'security_code': 'security_code',
        'metric':        'metric',
        'metric_key':    'metric_key',
        'unit':          'unit',
        'n_orders':      'n_orders',
        'mean':          'mean',
        'median':        'median',
        'std':           'std',
        'count':         'count',
        'better_count':  'better_count',
        'better_pct':    'better_pct',
    },

    # ── Unmatched analysis columns ─────────────────────────────────────────────
    'unmatched': {
        'root_cause':          'root_cause',
        'contra_depth':        'contra_depth',
        'contra_orders_count': 'contra_orders_count',
        'potential_fill_qty':  'potential_fill_qty',
        'price_overlap':       'price_overlap',
    },

    # ── Common identifiers (used across multiple contexts) ─────────────────────
    # Note: These are normalized/processed column names used after Stage 1
    'common': {
        'orderid':           'orderid',
        'order_id':          'order_id',
        'ticker':            'ticker',
        'date':              'date',
        'orderbookid':       'orderbookid',
        'timestamp':         'timestamp',
        'side':              'side',
        'quantity':          'quantity',
        'price':             'price',
        'sequence':          'sequence',
        'tradetime':         'tradetime',
        'tradeprice':        'tradeprice',
        'exchangeordertype': 'exchangeordertype',
        'changereason':      'changereason',
        'leavesquantity':    'leavesquantity',
        'matched_quantity':  'matched_quantity',
        'bid':               'bid',
        'offer':             'offer',
    },
}


# ── ColumnAccessor + ColumnSchema + col ────────────────────────────────────────

class ColumnAccessor:
    """
    Accessor class for column names of a specific data type.

    Provides attribute-based access to column names defined in COLUMN_MAPPING.

    Example:
        accessor = ColumnAccessor('orders', COLUMN_MAPPING['orders'])
        accessor.order_id  # Returns 'order_id'
    """

    def __init__(self, data_type: str, mapping: Dict[str, str]):
        self._data_type = data_type
        self._mapping   = mapping

    def __getattr__(self, name: str) -> str:
        if name.startswith('_'):
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        if name not in self._mapping:
            raise AttributeError(
                f"Column '{name}' not found in {self._data_type} mapping.\n"
                f"Available columns: {', '.join(sorted(self._mapping.keys()))}\n"
                f"Please add '{name}' to COLUMN_MAPPING['{self._data_type}']"
            )
        return self._mapping[name]

    def __dir__(self):
        return sorted(self._mapping.keys())

    def get(self, name: str, default: str = None) -> str:
        return self._mapping.get(name, default)

    def has(self, name: str) -> bool:
        return name in self._mapping

    def all(self) -> Dict[str, str]:
        return self._mapping.copy()


class ColumnSchema:
    """
    Main column schema system.

    Provides centralized access to all column names across different data types.
    Loads mappings directly from the module-level COLUMN_MAPPING dict.

    Usage:
        col.orders.order_id      # Returns 'order_id'
        col.trades.trade_price   # Returns 'tradeprice'
        col.common.timestamp     # Returns 'timestamp'
    """

    def __init__(self, mapping: Dict[str, Dict[str, str]] = None):
        self._accessors: Dict[str, ColumnAccessor] = {}
        column_mapping = mapping if mapping is not None else COLUMN_MAPPING
        if not isinstance(column_mapping, dict):
            raise ValueError(f"COLUMN_MAPPING must be a dict, got {type(column_mapping)}")
        for data_type, sub_mapping in column_mapping.items():
            if not isinstance(sub_mapping, dict):
                raise ValueError(
                    f"COLUMN_MAPPING['{data_type}'] must be a dict, got {type(sub_mapping)}"
                )
            self._accessors[data_type] = ColumnAccessor(data_type, sub_mapping)

    def __getattr__(self, name: str) -> ColumnAccessor:
        if name.startswith('_'):
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        if name not in self._accessors:
            raise AttributeError(
                f"Data type '{name}' not found in column mapping.\n"
                f"Available data types: {', '.join(sorted(self._accessors.keys()))}\n"
                f"Please add '{name}' to COLUMN_MAPPING"
            )
        return self._accessors[name]

    def __dir__(self):
        return sorted(self._accessors.keys())

    def get_accessor(self, data_type: str) -> Optional[ColumnAccessor]:
        return self._accessors.get(data_type)

    def has_data_type(self, data_type: str) -> bool:
        return data_type in self._accessors

    def data_types(self) -> list:
        return list(self._accessors.keys())

    def validate(self, data_type: str, required_columns: list) -> bool:
        if data_type not in self._accessors:
            raise ValueError(f"Data type '{data_type}' not found in column mapping")
        accessor = self._accessors[data_type]
        missing  = [c for c in required_columns if not accessor.has(c)]
        if missing:
            raise ValueError(
                f"Missing required columns in {data_type} mapping: {missing}\n"
                f"Please add these columns to COLUMN_MAPPING['{data_type}']"
            )
        return True

    def print_schema(self):
        print("=" * 80)
        print("COLUMN SCHEMA")
        print("=" * 80)
        for data_type in sorted(self._accessors.keys()):
            accessor = self._accessors[data_type]
            mapping  = accessor.all()
            print(f"\n{data_type.upper()}:")
            print("-" * 80)
            for std_name in sorted(mapping.keys()):
                print(f"  {std_name:30} → {mapping[std_name]}")
        print("\n" + "=" * 80)


# Global singleton — import as: from config import col
col = ColumnSchema(COLUMN_MAPPING)

# Backward-compat helper functions (mirrors column_schema.py)


# ── System / worker helpers (from system_config.py) ────────────────────────────

@dataclass
class SystemConfig:
    """System configuration based on detected resources."""
    cpu_count:           int
    num_workers:         int
    available_memory_gb: float
    chunk_size:          int
    enable_parallel:     bool

    def __str__(self):
        return (
            f"SystemConfig(\n"
            f"  CPU Cores: {self.cpu_count}\n"
            f"  Workers: {self.num_workers}\n"
            f"  Available Memory: {self.available_memory_gb:.2f} GB\n"
            f"  Chunk Size: {self.chunk_size:,}\n"
            f"  Parallel Processing: {'Enabled' if self.enable_parallel else 'Disabled'}\n"
            f")"
        )


def _calculate_optimal_workers(cpu_count: int) -> int:
    """Calculate optimal number of workers based on CPU count."""
    if cpu_count <= 2:
        return 1
    elif cpu_count <= 4:
        return cpu_count - 1
    elif cpu_count <= 8:
        return cpu_count - 2
    else:
        return min(cpu_count - 2, 16)


def _calculate_optimal_chunk_size(available_memory: int) -> int:
    """Calculate optimal chunk size based on available memory (bytes)."""
    target_memory_per_chunk = available_memory * 0.05
    estimated_chunk_size    = int(target_memory_per_chunk / 1024)
    return max(10_000, min(estimated_chunk_size, 500_000))


def detect_system_config(
    override_workers:    Optional[int] = None,
    override_chunk_size: Optional[int] = None,
) -> SystemConfig:
    """Detect system config and calculate optimal settings."""
    if psutil is not None:
        memory_info           = psutil.virtual_memory()
        available_memory_bytes = memory_info.available
        available_memory_gb   = available_memory_bytes / (1024 ** 3)
    else:
        available_memory_bytes = 8 * 1024 ** 3
        available_memory_gb   = 8.0

    cpu_count = multiprocessing.cpu_count()

    num_workers = (
        max(1, override_workers)
        if override_workers is not None
        else _calculate_optimal_workers(cpu_count)
    )

    chunk_size = (
        max(1_000, override_chunk_size)
        if override_chunk_size is not None
        else _calculate_optimal_chunk_size(available_memory_bytes)
    )

    enable_parallel = num_workers > 1

    return SystemConfig(
        cpu_count=cpu_count,
        num_workers=num_workers,
        available_memory_gb=available_memory_gb,
        chunk_size=chunk_size,
        enable_parallel=enable_parallel,
    )


def get_config_with_overrides(
    workers:    Optional[int] = None,
    chunk_size: Optional[int] = None,
) -> SystemConfig:
    """Get system config with env-var overrides. Priority: args > env vars > auto-detect."""
    env_workers    = os.getenv('SWEEP_WORKERS')
    env_chunk_size = os.getenv('SWEEP_CHUNK_SIZE')

    final_workers = workers
    if final_workers is None and env_workers is not None:
        try:
            final_workers = int(env_workers)
        except ValueError:
            pass  # silently ignore bad env var

    final_chunk_size = chunk_size
    if final_chunk_size is None and env_chunk_size is not None:
        try:
            final_chunk_size = int(env_chunk_size)
        except ValueError:
            pass  # silently ignore bad env var

    return detect_system_config(
        override_workers=final_workers,
        override_chunk_size=final_chunk_size,
    )


def auto_worker_count() -> int:
    """Return auto-detected optimal worker count (respects SWEEP_WORKERS env var)."""
    return get_config_with_overrides().num_workers


def print_system_info():
    """Print detailed system information for debugging."""
    if psutil is None:
        print("\nInstall psutil for detailed system information: pip install psutil")
        return

    print("\nDetailed System Information:")
    print("=" * 60)
    cpu_count = multiprocessing.cpu_count()
    print(f"CPU Cores (Logical): {cpu_count}")
    try:
        cpu_freq = psutil.cpu_freq()
        if cpu_freq:
            print(f"CPU Frequency: {cpu_freq.current:.2f} MHz")
    except Exception:
        pass
    mem = psutil.virtual_memory()
    print(f"\nMemory:")
    print(f"  Total:     {mem.total     / (1024**3):.2f} GB")
    print(f"  Available: {mem.available / (1024**3):.2f} GB")
    print(f"  Used:      {mem.used      / (1024**3):.2f} GB ({mem.percent}%)")
    try:
        disk = psutil.disk_usage('.')
        print(f"\nDisk (current directory):")
        print(f"  Total: {disk.total / (1024**3):.2f} GB")
        print(f"  Free:  {disk.free  / (1024**3):.2f} GB ({100 - disk.percent}%)")
    except Exception:
        pass
    print("=" * 60)


# ── Eagerly-computed system config (mirrors legacy SYSTEM_CONFIG / CHUNK_SIZE / NUM_WORKERS) ──
SYSTEM_CONFIG = get_config_with_overrides()
CHUNK_SIZE    = SYSTEM_CONFIG.chunk_size
NUM_WORKERS   = SYSTEM_CONFIG.num_workers
MAX_PARALLEL_WORKERS = NUM_WORKERS


# ── Configuration summary ──────────────────────────────────────────────────────

def print_config():
    """Print current configuration summary."""
    print("=" * 80)
    print("PIPELINE CONFIGURATION")
    print("=" * 80)
    print("\nDataset Configuration:")
    print(f"  Ticker:  {TICKER}")
    print(f"  Date:    {DATE}")
    print("\nSecurity Discovery:")
    print(f"  Auto-discovery enabled:  {AUTO_DISCOVERY_ENABLED}")
    print(f"  Min orders threshold:    {MIN_ORDERS_THRESHOLD}")
    print(f"  Min trades threshold:    {MIN_TRADES_THRESHOLD}")
    print("\nProcessing Mode:")
    print(f"  Mode: {PROCESSING_MODE}")
    print("\nSystem Configuration:")
    print(SYSTEM_CONFIG)
    print(f"\nInput Files:")
    for key, path in INPUT_FILES.items():
        print(f"  {key:15} -> {path}")
    print(f"\nDirectories:")
    print(f"  Processed:   {PROCESSED_DIR}")
    print(f"  Outputs:     {OUTPUTS_DIR}")
    print(f"  Reports:     {REPORTS_DIR}")
    print(f"\nOrder Types:")
    print(f"  Centre Point: {CENTRE_POINT_ORDER_TYPES}")
    print(f"  Sweep:        {SWEEP_ORDER_TYPE}")
    print(f"  Min block size (APB): {MIN_BLOCK_SIZE}")
    print("=" * 80)


if __name__ == '__main__':
    print_config()
    print()
    print_system_info()
