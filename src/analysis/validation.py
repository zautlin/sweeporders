"""
Validation Module

Compares simulated dark pool trades against actual Centre Point trades
to measure simulation accuracy and identify systematic biases.

Metrics:
- Fill rate: simulated vs. real
- Execution price: simulated midpoint vs. real trade price
- Timing: simulated match time vs. real trade time
- Match count: simulated matches vs. actual trades
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.column_schema import col


class SimulationValidator:
    """Validate simulation results against real Centre Point trades."""
    
    def __init__(self, partition_dir):
        """Initialize validator.
        
        Args:
            partition_dir: Path to partition directory (e.g., data/processed/2024-09-05/110621)
        """
        self.partition_dir = Path(partition_dir)
        self.simulated_trades = None
        self.real_trades = None
        self.sweep_orders = None
        self.validation_results = {}
        
    def load_data(self):
        """Load simulated and real trade data."""
        # Load simulated trades
        sim_file = self.partition_dir / 'cp_trades_simulation.csv'
        if sim_file.exists():
            self.simulated_trades = pd.read_csv(sim_file)
        else:
            raise FileNotFoundError(f"Simulated trades not found: {sim_file}")
        
        # Load real Centre Point trades
        real_file = self.partition_dir / 'cp_trades_matched.csv.gz'
        if real_file.exists():
            self.real_trades = pd.read_csv(real_file)
        else:
            raise FileNotFoundError(f"Real trades not found: {real_file}")
        
        # Load sweep orders for context
        orders_file = self.partition_dir / 'cp_orders_filtered.csv.gz'
        if orders_file.exists():
            self.sweep_orders = pd.read_csv(orders_file)
        
        return self
    
    def calculate_fill_rate_comparison(self):
        """Compare fill rates between simulated and real trades."""
        if self.simulated_trades is None or self.real_trades is None:
            return None
        
        # Group by order ID
        sim_fills = self.simulated_trades.groupby('orderid').agg({
            'quantity': 'sum',
            'side': 'first'
        }).reset_index()
        sim_fills.columns = ['orderid', 'sim_qty_filled', 'side']
        
        real_fills = self.real_trades.groupby('orderid').agg({
            'quantity': 'sum',
            'side': 'first'
        }).reset_index()
        real_fills.columns = ['orderid', 'real_qty_filled', 'side']
        
        # Merge
        comparison = pd.merge(sim_fills, real_fills, on='orderid', how='outer')
        
        # Fill NaN with 0
        comparison['sim_qty_filled'] = comparison['sim_qty_filled'].fillna(0)
        comparison['real_qty_filled'] = comparison['real_qty_filled'].fillna(0)
        
        # Calculate fill rate ratio
        comparison['fill_rate_ratio'] = np.where(
            comparison['real_qty_filled'] > 0,
            comparison['sim_qty_filled'] / comparison['real_qty_filled'],
            np.where(comparison['sim_qty_filled'] > 0, np.inf, 1.0)
        )
        
        # Statistics
        stats = {
            'total_orders': len(comparison),
            'orders_filled_both': len(comparison[(comparison['sim_qty_filled'] > 0) & 
                                                   (comparison['real_qty_filled'] > 0)]),
            'orders_filled_sim_only': len(comparison[(comparison['sim_qty_filled'] > 0) & 
                                                       (comparison['real_qty_filled'] == 0)]),
            'orders_filled_real_only': len(comparison[(comparison['sim_qty_filled'] == 0) & 
                                                        (comparison['real_qty_filled'] > 0)]),
            'mean_fill_rate_ratio': comparison['fill_rate_ratio'][comparison['fill_rate_ratio'] != np.inf].mean(),
            'median_fill_rate_ratio': comparison['fill_rate_ratio'][comparison['fill_rate_ratio'] != np.inf].median(),
            'total_sim_qty': comparison['sim_qty_filled'].sum(),
            'total_real_qty': comparison['real_qty_filled'].sum(),
            'qty_fill_ratio': comparison['sim_qty_filled'].sum() / comparison['real_qty_filled'].sum() 
                              if comparison['real_qty_filled'].sum() > 0 else np.inf
        }
        
        self.validation_results['fill_rate'] = stats
        return stats
    
    def calculate_price_accuracy(self):
        """Compare execution prices between simulated and real trades."""
        if self.simulated_trades is None or self.real_trades is None:
            return None
        
        # Aggregate by order ID (VWAP calculation)
        sim_vwap = self.simulated_trades.groupby('orderid').apply(
            lambda x: (x['tradeprice'] * x['quantity']).sum() / x['quantity'].sum()
            if x['quantity'].sum() > 0 else np.nan
        ).reset_index()
        sim_vwap.columns = ['orderid', 'sim_vwap']
        
        real_vwap = self.real_trades.groupby('orderid').apply(
            lambda x: (x['tradeprice'] * x['quantity']).sum() / x['quantity'].sum()
            if x['quantity'].sum() > 0 else np.nan
        ).reset_index()
        real_vwap.columns = ['orderid', 'real_vwap']
        
        # Merge
        price_comparison = pd.merge(sim_vwap, real_vwap, on='orderid', how='inner')
        
        # Calculate price difference (in basis points)
        price_comparison['price_diff_bps'] = (
            (price_comparison['sim_vwap'] - price_comparison['real_vwap']) / 
            price_comparison['real_vwap'] * 10000
        )
        
        # Statistics
        stats = {
            'orders_compared': len(price_comparison),
            'mean_price_diff_bps': price_comparison['price_diff_bps'].mean(),
            'median_price_diff_bps': price_comparison['price_diff_bps'].median(),
            'std_price_diff_bps': price_comparison['price_diff_bps'].std(),
            'min_price_diff_bps': price_comparison['price_diff_bps'].min(),
            'max_price_diff_bps': price_comparison['price_diff_bps'].max(),
            'pct_better_sim': (price_comparison['price_diff_bps'] < 0).mean() * 100,  # Sim better (lower cost)
            'pct_better_real': (price_comparison['price_diff_bps'] > 0).mean() * 100,  # Real better
        }
        
        self.validation_results['price_accuracy'] = stats
        return stats
    
    def calculate_timing_accuracy(self):
        """Compare execution timing between simulated and real trades."""
        if self.simulated_trades is None or self.real_trades is None:
            return None
        
        # Aggregate by order ID (first and last execution)
        sim_timing = self.simulated_trades.groupby('orderid').agg({
            'tradetime': ['min', 'max'],
            'quantity': 'sum'
        }).reset_index()
        sim_timing.columns = ['orderid', 'sim_first_time', 'sim_last_time', 'sim_qty']
        sim_timing['sim_duration_ns'] = sim_timing['sim_last_time'] - sim_timing['sim_first_time']
        
        real_timing = self.real_trades.groupby('orderid').agg({
            'tradetime': ['min', 'max'],
            'quantity': 'sum'
        }).reset_index()
        real_timing.columns = ['orderid', 'real_first_time', 'real_last_time', 'real_qty']
        real_timing['real_duration_ns'] = real_timing['real_last_time'] - real_timing['real_first_time']
        
        # Merge
        timing_comparison = pd.merge(sim_timing, real_timing, on='orderid', how='inner')
        
        # Convert to seconds
        timing_comparison['sim_duration_sec'] = timing_comparison['sim_duration_ns'] / 1e9
        timing_comparison['real_duration_sec'] = timing_comparison['real_duration_ns'] / 1e9
        
        # Calculate timing difference
        timing_comparison['duration_diff_sec'] = (
            timing_comparison['sim_duration_sec'] - timing_comparison['real_duration_sec']
        )
        
        # Statistics
        stats = {
            'orders_compared': len(timing_comparison),
            'mean_sim_duration_sec': timing_comparison['sim_duration_sec'].mean(),
            'mean_real_duration_sec': timing_comparison['real_duration_sec'].mean(),
            'mean_duration_diff_sec': timing_comparison['duration_diff_sec'].mean(),
            'median_duration_diff_sec': timing_comparison['duration_diff_sec'].median(),
            'pct_faster_sim': (timing_comparison['duration_diff_sec'] < 0).mean() * 100,
            'pct_slower_sim': (timing_comparison['duration_diff_sec'] > 0).mean() * 100,
        }
        
        self.validation_results['timing_accuracy'] = stats
        return stats
    
    def calculate_match_count_comparison(self):
        """Compare number of matches between simulated and real trades."""
        if self.simulated_trades is None or self.real_trades is None:
            return None
        
        # Count unique match groups (each match = 2 trades: passive + aggressive)
        sim_match_count = len(self.simulated_trades['matchgroupid'].unique())
        real_match_count = len(self.real_trades['matchgroupid'].unique())
        
        # Count trades
        sim_trade_count = len(self.simulated_trades)
        real_trade_count = len(self.real_trades)
        
        stats = {
            'simulated_matches': sim_match_count,
            'real_matches': real_match_count,
            'match_ratio': sim_match_count / real_match_count if real_match_count > 0 else np.inf,
            'simulated_trades': sim_trade_count,
            'real_trades': real_trade_count,
            'trade_ratio': sim_trade_count / real_trade_count if real_trade_count > 0 else np.inf,
        }
        
        self.validation_results['match_count'] = stats
        return stats
    
    def run_full_validation(self):
        """Run all validation metrics."""
        print("="*80)
        print("SIMULATION VALIDATION REPORT")
        print("="*80)
        print(f"Partition: {self.partition_dir.name}")
        print()
        
        # Load data
        print("Loading data...")
        self.load_data()
        print(f"  Simulated trades: {len(self.simulated_trades):,}")
        print(f"  Real trades: {len(self.real_trades):,}")
        print()
        
        # Run all validations
        print("Running validation metrics...")
        
        print("  1. Fill rate comparison...")
        fill_stats = self.calculate_fill_rate_comparison()
        
        print("  2. Price accuracy...")
        price_stats = self.calculate_price_accuracy()
        
        print("  3. Timing accuracy...")
        timing_stats = self.calculate_timing_accuracy()
        
        print("  4. Match count comparison...")
        match_stats = self.calculate_match_count_comparison()
        
        # Print results
        print()
        print("="*80)
        print("VALIDATION RESULTS")
        print("="*80)
        
        # Fill Rate
        print("\n1. FILL RATE COMPARISON")
        print("-" * 80)
        print(f"  Total orders:                      {fill_stats['total_orders']:,}")
        print(f"  Orders filled (both):              {fill_stats['orders_filled_both']:,}")
        print(f"  Orders filled (sim only):          {fill_stats['orders_filled_sim_only']:,}")
        print(f"  Orders filled (real only):         {fill_stats['orders_filled_real_only']:,}")
        print(f"  Mean fill rate ratio (sim/real):   {fill_stats['mean_fill_rate_ratio']:.2f}")
        print(f"  Median fill rate ratio:            {fill_stats['median_fill_rate_ratio']:.2f}")
        print(f"  Total qty (sim):                   {fill_stats['total_sim_qty']:,.0f}")
        print(f"  Total qty (real):                  {fill_stats['total_real_qty']:,.0f}")
        print(f"  Quantity fill ratio:               {fill_stats['qty_fill_ratio']:.2f}")
        
        # Price Accuracy
        print("\n2. PRICE ACCURACY")
        print("-" * 80)
        print(f"  Orders compared:                   {price_stats['orders_compared']:,}")
        print(f"  Mean price diff (bps):             {price_stats['mean_price_diff_bps']:+.2f}")
        print(f"  Median price diff (bps):           {price_stats['median_price_diff_bps']:+.2f}")
        print(f"  Std dev (bps):                     {price_stats['std_price_diff_bps']:.2f}")
        print(f"  Range (bps):                       [{price_stats['min_price_diff_bps']:+.2f}, {price_stats['max_price_diff_bps']:+.2f}]")
        print(f"  Sim better (lower cost):           {price_stats['pct_better_sim']:.1f}%")
        print(f"  Real better:                       {price_stats['pct_better_real']:.1f}%")
        
        # Timing Accuracy
        print("\n3. TIMING ACCURACY")
        print("-" * 80)
        print(f"  Orders compared:                   {timing_stats['orders_compared']:,}")
        print(f"  Mean sim duration (sec):           {timing_stats['mean_sim_duration_sec']:.3f}")
        print(f"  Mean real duration (sec):          {timing_stats['mean_real_duration_sec']:.3f}")
        print(f"  Mean duration diff (sec):          {timing_stats['mean_duration_diff_sec']:+.3f}")
        print(f"  Median duration diff (sec):        {timing_stats['median_duration_diff_sec']:+.3f}")
        print(f"  Sim faster:                        {timing_stats['pct_faster_sim']:.1f}%")
        print(f"  Sim slower:                        {timing_stats['pct_slower_sim']:.1f}%")
        
        # Match Count
        print("\n4. MATCH COUNT COMPARISON")
        print("-" * 80)
        print(f"  Simulated matches:                 {match_stats['simulated_matches']:,}")
        print(f"  Real matches:                      {match_stats['real_matches']:,}")
        print(f"  Match ratio (sim/real):            {match_stats['match_ratio']:.2f}")
        print(f"  Simulated trades:                  {match_stats['simulated_trades']:,}")
        print(f"  Real trades:                       {match_stats['real_trades']:,}")
        print(f"  Trade ratio (sim/real):            {match_stats['trade_ratio']:.2f}")
        
        # Overall Assessment
        print("\n" + "="*80)
        print("OVERALL ASSESSMENT")
        print("="*80)
        
        # Calculate overall accuracy score
        accuracy_scores = []
        
        # Fill rate accuracy (1.0 = perfect)
        fill_accuracy = min(fill_stats['qty_fill_ratio'], 2.0) - max(0, fill_stats['qty_fill_ratio'] - 1.0)
        fill_accuracy = max(0, 1.0 - abs(fill_stats['qty_fill_ratio'] - 1.0))
        accuracy_scores.append(('Fill Rate', fill_accuracy))
        
        # Price accuracy (based on mean absolute error)
        price_accuracy = max(0, 1.0 - abs(price_stats['mean_price_diff_bps']) / 10)  # 10 bps = 0% accuracy
        accuracy_scores.append(('Price', price_accuracy))
        
        # Timing accuracy
        if timing_stats['mean_real_duration_sec'] > 0:
            timing_error = abs(timing_stats['mean_duration_diff_sec']) / timing_stats['mean_real_duration_sec']
            timing_accuracy = max(0, 1.0 - timing_error)
        else:
            timing_accuracy = 1.0 if timing_stats['mean_duration_diff_sec'] == 0 else 0.0
        accuracy_scores.append(('Timing', timing_accuracy))
        
        # Match count accuracy
        match_accuracy = max(0, 1.0 - abs(match_stats['match_ratio'] - 1.0))
        accuracy_scores.append(('Match Count', match_accuracy))
        
        # Print scores
        overall_score = np.mean([score for _, score in accuracy_scores])
        
        for metric, score in accuracy_scores:
            bar = '█' * int(score * 20) + '░' * (20 - int(score * 20))
            print(f"  {metric:15} [{bar}] {score*100:5.1f}%")
        
        print(f"  {'OVERALL':15} [{'█' * int(overall_score * 20)}{'░' * (20 - int(overall_score * 20))}] {overall_score*100:5.1f}%")
        
        # Recommendations
        print("\n" + "="*80)
        print("RECOMMENDATIONS")
        print("="*80)
        
        if fill_stats['qty_fill_ratio'] > 1.5:
            print("  ⚠ OVER-FILLING: Simulation fills more than real market")
            print("    → Check MAQ constraints and crossing prevention logic")
        elif fill_stats['qty_fill_ratio'] < 0.5:
            print("  ⚠ UNDER-FILLING: Simulation fills less than real market")
            print("    → Check time window and eligible order logic")
        
        if abs(price_stats['mean_price_diff_bps']) > 2:
            if price_stats['mean_price_diff_bps'] > 0:
                print("  ⚠ PRICE BIAS: Simulated prices higher than real")
                print("    → Check mid-tick improvement logic")
            else:
                print("  ⚠ PRICE BIAS: Simulated prices lower than real")
                print("    → May be too optimistic, check NBBO timing")
        
        if timing_stats['mean_duration_diff_sec'] > 1:
            print("  ⚠ TIMING: Simulation slower than real market")
            print("    → Check effective timestamp logic for amendments")
        elif timing_stats['mean_duration_diff_sec'] < -1:
            print("  ⚠ TIMING: Simulation faster than real market")
            print("    → May not be accounting for all delays")
        
        if match_stats['match_ratio'] > 1.5:
            print("  ⚠ MATCH COUNT: Too many simulated matches")
            print("    → Check matching logic constraints")
        elif match_stats['match_ratio'] < 0.5:
            print("  ⚠ MATCH COUNT: Too few simulated matches")
            print("    → Check eligible order filtering")
        
        if overall_score >= 0.8:
            print("\n  ✓ Simulation accuracy is GOOD (≥80%)")
            print("    → Ready for production analysis")
        elif overall_score >= 0.6:
            print("\n  ⚠ Simulation accuracy is MODERATE (60-80%)")
            print("    → Address recommendations above before production use")
        else:
            print("\n  ✗ Simulation accuracy is LOW (<60%)")
            print("    → Major revision needed before production use")
        
        print()
        print("="*80)
        
        return self.validation_results
    
    def save_results(self, output_file=None):
        """Save validation results to CSV.
        
        Args:
            output_file: Optional output file path
        """
        if not self.validation_results:
            raise ValueError("No validation results. Run run_full_validation() first.")
        
        # Flatten results
        rows = []
        for category, metrics in self.validation_results.items():
            for metric, value in metrics.items():
                rows.append({
                    'category': category,
                    'metric': metric,
                    'value': value
                })
        
        results_df = pd.DataFrame(rows)
        
        if output_file is None:
            output_file = self.partition_dir / 'validation_results.csv'
        
        results_df.to_csv(output_file, index=False)
        print(f"Validation results saved to: {output_file}")
        
        return results_df


def validate_partition(partition_dir, output_file=None):
    """Convenience function to validate a single partition.
    
    Args:
        partition_dir: Path to partition directory
        output_file: Optional output file path
        
    Returns:
        dict: Validation results
    """
    validator = SimulationValidator(partition_dir)
    validator.run_full_validation()
    
    if output_file:
        validator.save_results(output_file)
    
    return validator.validation_results


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        partition_dir = sys.argv[1]
    else:
        # Default to DRR 2024-09-05
        partition_dir = 'data/processed/2024-09-05/110621'
    
    print(f"Validating partition: {partition_dir}")
    print()
    
    validate_partition(partition_dir)
