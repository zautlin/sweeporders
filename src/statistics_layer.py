"""
Three-Tier Statistics System with Graceful Degradation

Tier 1: Descriptive statistics only (always available - pandas/numpy)
Tier 2: Approximate statistical tests (no scipy - uses normal approximation)
Tier 3: Full statistical tests (scipy required - exact p-values)

Author: Centre Point Analysis Pipeline
Date: 2026-01-05
"""

import warnings
from typing import Optional, Tuple, Union
import numpy as np
import pandas as pd
from dataclasses import dataclass

# Try to import scipy
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    scipy_stats = None


@dataclass
class StatResult:
    """Container for statistical test results"""
    statistic: float
    pvalue: float
    method: str  # 'scipy' or 'approximate'
    
    def __iter__(self):
        """Allow unpacking like tuple for backward compatibility"""
        return iter((self.statistic, self.pvalue))


class StatisticsEngine:
    """
    Handles all statistical tests with three-tier fallback system
    
    Tier 1: Only descriptive stats (mean, median, std, etc.)
    Tier 2: Approximate statistical tests using normal distribution
    Tier 3: Exact statistical tests using scipy
    """
    
    def __init__(self, enable_stats: bool = False, force_simple: bool = False):
        """
        Initialize statistics engine
        
        Args:
            enable_stats: Whether to enable statistical tests (default: False)
            force_simple: Force Tier 2 even if scipy available (for testing)
        """
        self.enable_stats = enable_stats
        self.force_simple = force_simple
        self.tier = self._determine_tier()
        self._warning_shown = False
        
    def _determine_tier(self) -> int:
        """
        Determine which tier to use
        
        Returns:
            1: Descriptive only
            2: Simple stats (approximate)
            3: Full stats (scipy)
        """
        if not self.enable_stats:
            return 1  # Descriptive only
        
        if self.force_simple or not SCIPY_AVAILABLE:
            if not SCIPY_AVAILABLE and self.enable_stats and not self._warning_shown:
                warnings.warn(
                    "\n" + "="*80 + "\n"
                    "WARNING: scipy not available. Using approximate statistics.\n"
                    "P-values will use normal approximation (accurate for sample size > 30).\n"
                    "Install scipy for exact p-values: pip install scipy\n"
                    "="*80,
                    UserWarning,
                    stacklevel=2
                )
                self._warning_shown = True
            return 2  # Simple stats
        
        return 3  # Full stats with scipy
    
    def get_tier_name(self) -> str:
        """Get human-readable tier name"""
        return {
            1: "Descriptive Statistics Only",
            2: "Approximate Statistics (no scipy)",
            3: "Full Statistical Tests (scipy)"
        }[self.tier]
    
    def is_enabled(self) -> bool:
        """Check if statistical tests are enabled"""
        return self.tier > 1
    
    # =================================================================
    # T-Tests
    # =================================================================
    
    def ttest_1samp(self, data: Union[pd.Series, np.ndarray], 
                    popmean: float = 0) -> Optional[StatResult]:
        """
        One-sample t-test
        
        Tests whether the mean of a sample differs from a population mean
        
        Args:
            data: Sample data
            popmean: Population mean to test against (default: 0)
            
        Returns:
            StatResult with (statistic, pvalue) or None if stats disabled
        """
        if self.tier == 1:
            return None
        
        # Convert to numpy array and remove NaN
        data = np.asarray(data)
        data = data[~np.isnan(data)]
        
        if len(data) == 0:
            return None
        
        if self.tier == 3:
            # Use scipy for exact results
            t_stat, p_value = scipy_stats.ttest_1samp(data, popmean)
            return StatResult(statistic=float(t_stat), pvalue=float(p_value), method='scipy')
        
        # Tier 2: Approximate using normal distribution
        n = len(data)
        mean = np.mean(data)
        std = np.std(data, ddof=1)
        
        if std == 0 or n == 0:
            if mean == popmean:
                return StatResult(statistic=0.0, pvalue=1.0, method='approximate')
            else:
                return StatResult(statistic=float('inf'), pvalue=0.0, method='approximate')
        
        # Calculate t-statistic
        t_stat = (mean - popmean) / (std / np.sqrt(n))
        
        # Approximate p-value using normal distribution
        p_value = self._normal_pvalue(t_stat, two_tailed=True)
        
        if n < 30:
            warnings.warn(
                f"Sample size ({n}) is small. P-values use normal approximation "
                f"and may be less accurate. Install scipy for exact results.",
                UserWarning,
                stacklevel=2
            )
        
        return StatResult(statistic=float(t_stat), pvalue=float(p_value), method='approximate')
    
    def ttest_rel(self, a: Union[pd.Series, np.ndarray], 
                  b: Union[pd.Series, np.ndarray]) -> Optional[StatResult]:
        """
        Paired t-test (related samples)
        
        Tests whether the mean difference between paired samples is zero
        
        Args:
            a: First sample
            b: Second sample
            
        Returns:
            StatResult with (statistic, pvalue) or None if stats disabled
        """
        if self.tier == 1:
            return None
        
        # Convert to numpy arrays and remove NaN
        a = np.asarray(a)
        b = np.asarray(b)
        
        # Create mask for valid pairs
        valid = ~(np.isnan(a) | np.isnan(b))
        a = a[valid]
        b = b[valid]
        
        if len(a) == 0:
            return None
        
        if self.tier == 3:
            # Use scipy
            t_stat, p_value = scipy_stats.ttest_rel(a, b)
            return StatResult(statistic=float(t_stat), pvalue=float(p_value), method='scipy')
        
        # Tier 2: Paired t-test is just one-sample t-test on differences
        differences = a - b
        return self.ttest_1samp(differences, popmean=0)
    
    def ttest_ind(self, a: Union[pd.Series, np.ndarray], 
                  b: Union[pd.Series, np.ndarray],
                  equal_var: bool = True) -> Optional[StatResult]:
        """
        Independent t-test (unrelated samples)
        
        Tests whether the means of two independent samples differ
        
        Args:
            a: First sample
            b: Second sample
            equal_var: Assume equal variances (default: True)
            
        Returns:
            StatResult with (statistic, pvalue) or None if stats disabled
        """
        if self.tier == 1:
            return None
        
        # Convert to numpy arrays and remove NaN
        a = np.asarray(a)
        b = np.asarray(b)
        a = a[~np.isnan(a)]
        b = b[~np.isnan(b)]
        
        if len(a) == 0 or len(b) == 0:
            return None
        
        if self.tier == 3:
            # Use scipy
            t_stat, p_value = scipy_stats.ttest_ind(a, b, equal_var=equal_var)
            return StatResult(statistic=float(t_stat), pvalue=float(p_value), method='scipy')
        
        # Tier 2: Approximate independent t-test
        n1 = len(a)
        n2 = len(b)
        mean1 = np.mean(a)
        mean2 = np.mean(b)
        var1 = np.var(a, ddof=1)
        var2 = np.var(b, ddof=1)
        
        if equal_var:
            # Pooled variance
            pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
            se = np.sqrt(pooled_var * (1/n1 + 1/n2))
        else:
            # Welch's t-test
            se = np.sqrt(var1/n1 + var2/n2)
        
        if se == 0:
            if mean1 == mean2:
                return StatResult(statistic=0.0, pvalue=1.0, method='approximate')
            else:
                return StatResult(statistic=float('inf'), pvalue=0.0, method='approximate')
        
        t_stat = (mean1 - mean2) / se
        p_value = self._normal_pvalue(t_stat, two_tailed=True)
        
        if min(n1, n2) < 30:
            warnings.warn(
                f"Small sample size (n1={n1}, n2={n2}). P-values use normal "
                f"approximation and may be less accurate.",
                UserWarning,
                stacklevel=2
            )
        
        return StatResult(statistic=float(t_stat), pvalue=float(p_value), method='approximate')
    
    # =================================================================
    # ANOVA
    # =================================================================
    
    def f_oneway(self, *args) -> Optional[StatResult]:
        """
        One-way ANOVA
        
        Tests whether means of multiple groups are equal
        
        Args:
            *args: Variable number of sample arrays
            
        Returns:
            StatResult with (statistic, pvalue) or None if stats disabled or scipy unavailable
        """
        if self.tier == 1:
            return None
        
        if self.tier == 2:
            warnings.warn(
                "ANOVA (f_oneway) requires scipy for accurate results. "
                "Returning None. Install scipy to enable ANOVA tests.",
                UserWarning,
                stacklevel=2
            )
            return None
        
        # Tier 3: Use scipy
        # Remove NaN from all samples
        cleaned_args = []
        for arg in args:
            arr = np.asarray(arg)
            arr = arr[~np.isnan(arr)]
            if len(arr) > 0:
                cleaned_args.append(arr)
        
        if len(cleaned_args) < 2:
            return None
        
        f_stat, p_value = scipy_stats.f_oneway(*cleaned_args)
        return StatResult(statistic=float(f_stat), pvalue=float(p_value), method='scipy')
    
    # =================================================================
    # Non-parametric tests
    # =================================================================
    
    def wilcoxon(self, x: Union[pd.Series, np.ndarray], 
                 y: Optional[Union[pd.Series, np.ndarray]] = None,
                 zero_method: str = 'wilcox',
                 alternative: str = 'two-sided') -> Optional[StatResult]:
        """
        Wilcoxon signed-rank test
        
        Non-parametric test for paired samples
        
        Args:
            x: First sample or differences
            y: Second sample (optional)
            zero_method: How to handle zeros
            alternative: 'two-sided', 'less', or 'greater'
            
        Returns:
            StatResult with (statistic, pvalue) or None if stats disabled or scipy unavailable
        """
        if self.tier == 1:
            return None
        
        if self.tier == 2:
            warnings.warn(
                "Wilcoxon test requires scipy. Returning None. "
                "Install scipy to enable non-parametric tests.",
                UserWarning,
                stacklevel=2
            )
            return None
        
        # Tier 3: Use scipy
        x = np.asarray(x)
        x = x[~np.isnan(x)]
        
        if y is not None:
            y = np.asarray(y)
            y = y[~np.isnan(y)]
        
        if len(x) == 0:
            return None
        
        result = scipy_stats.wilcoxon(x, y, zero_method=zero_method, alternative=alternative)
        return StatResult(statistic=float(result.statistic), pvalue=float(result.pvalue), method='scipy')
    
    # =================================================================
    # Correlation
    # =================================================================
    
    def pearsonr(self, x: Union[pd.Series, np.ndarray], 
                 y: Union[pd.Series, np.ndarray]) -> Optional[StatResult]:
        """
        Pearson correlation coefficient and p-value
        
        Measures linear correlation between two variables
        
        Args:
            x: First variable
            y: Second variable
            
        Returns:
            StatResult with (correlation, pvalue) or None if stats disabled
        """
        if self.tier == 1:
            return None
        
        # Convert to numpy arrays
        x = np.asarray(x)
        y = np.asarray(y)
        
        # Remove NaN pairs
        valid = ~(np.isnan(x) | np.isnan(y))
        x = x[valid]
        y = y[valid]
        
        if len(x) < 2:
            return None
        
        if self.tier == 3:
            # Use scipy
            corr, p_value = scipy_stats.pearsonr(x, y)
            return StatResult(statistic=float(corr), pvalue=float(p_value), method='scipy')
        
        # Tier 2: Calculate correlation using numpy, approximate p-value
        corr = np.corrcoef(x, y)[0, 1]
        
        # Approximate p-value using t-distribution approximation
        n = len(x)
        if n < 3 or abs(corr) == 1:
            p_value = 0.0 if abs(corr) == 1 else 1.0
        else:
            # t = r * sqrt(n-2) / sqrt(1-r^2)
            t_stat = corr * np.sqrt(n - 2) / np.sqrt(1 - corr**2)
            p_value = self._normal_pvalue(t_stat, two_tailed=True)
        
        return StatResult(statistic=float(corr), pvalue=float(p_value), method='approximate')
    
    def spearmanr(self, x: Union[pd.Series, np.ndarray], 
                  y: Union[pd.Series, np.ndarray]) -> Optional[StatResult]:
        """
        Spearman rank correlation coefficient and p-value
        
        Measures monotonic correlation between two variables
        
        Args:
            x: First variable
            y: Second variable
            
        Returns:
            StatResult with (correlation, pvalue) or None if stats disabled
        """
        if self.tier == 1:
            return None
        
        # Convert to numpy arrays
        x = np.asarray(x)
        y = np.asarray(y)
        
        # Remove NaN pairs
        valid = ~(np.isnan(x) | np.isnan(y))
        x = x[valid]
        y = y[valid]
        
        if len(x) < 2:
            return None
        
        if self.tier == 3:
            # Use scipy
            corr, p_value = scipy_stats.spearmanr(x, y)
            return StatResult(statistic=float(corr), pvalue=float(p_value), method='scipy')
        
        # Tier 2: Calculate Spearman correlation
        # Rank the data
        x_ranked = self._rankdata(x)
        y_ranked = self._rankdata(y)
        
        # Use Pearson on ranks
        result = self.pearsonr(x_ranked, y_ranked)
        if result:
            return StatResult(statistic=result.statistic, pvalue=result.pvalue, method='approximate')
        return None
    
    # =================================================================
    # Confidence Intervals
    # =================================================================
    
    def confidence_interval(self, data: Union[pd.Series, np.ndarray], 
                           confidence: float = 0.95) -> Optional[Tuple[float, float]]:
        """
        Calculate confidence interval for the mean
        
        Args:
            data: Sample data
            confidence: Confidence level (default: 0.95)
            
        Returns:
            Tuple of (lower, upper) bounds or None if stats disabled
        """
        if self.tier == 1:
            return None
        
        data = np.asarray(data)
        data = data[~np.isnan(data)]
        
        if len(data) == 0:
            return None
        
        n = len(data)
        mean = np.mean(data)
        se = np.std(data, ddof=1) / np.sqrt(n)
        
        if se == 0:
            return (mean, mean)
        
        if self.tier == 3:
            # Use scipy t-distribution
            alpha = 1 - confidence
            df = n - 1
            t_critical = scipy_stats.t.ppf(1 - alpha/2, df)
            margin = t_critical * se
            return (mean - margin, mean + margin)
        
        # Tier 2: Use normal approximation
        # For 95% CI: z = 1.96
        z_scores = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
        z = z_scores.get(confidence, 1.96)
        
        margin = z * se
        return (mean - margin, mean + margin)
    
    # =================================================================
    # Helper Methods
    # =================================================================
    
    def _normal_pvalue(self, z: float, two_tailed: bool = True) -> float:
        """
        Calculate p-value using standard normal distribution
        
        Args:
            z: Z-score or t-statistic
            two_tailed: Whether to calculate two-tailed p-value
            
        Returns:
            P-value
        """
        # Standard normal CDF using error function
        # CDF(z) = 0.5 * (1 + erf(z / sqrt(2)))
        from math import erf, sqrt
        
        z_abs = abs(z)
        # P(Z > z) for standard normal
        p_one_tail = 0.5 * (1 - erf(z_abs / sqrt(2)))
        
        if two_tailed:
            return 2 * p_one_tail
        else:
            return p_one_tail
    
    def _rankdata(self, data: np.ndarray) -> np.ndarray:
        """
        Rank data (helper for Spearman correlation)
        
        Args:
            data: Array to rank
            
        Returns:
            Ranked array
        """
        # Simple ranking using argsort
        sorter = np.argsort(data)
        ranks = np.empty_like(sorter, dtype=float)
        ranks[sorter] = np.arange(1, len(data) + 1)
        return ranks


# Convenience function for backward compatibility
def create_stats_engine(enable_stats: bool = False, 
                       force_simple: bool = False) -> StatisticsEngine:
    """
    Create a statistics engine instance
    
    Args:
        enable_stats: Whether to enable statistical tests
        force_simple: Force Tier 2 even if scipy available
        
    Returns:
        StatisticsEngine instance
    """
    return StatisticsEngine(enable_stats=enable_stats, force_simple=force_simple)


# Module-level check for scipy availability
def check_scipy_available() -> bool:
    """Check if scipy is available"""
    return SCIPY_AVAILABLE


def print_stats_info():
    """Print information about available statistics tiers"""
    print("\n" + "="*80)
    print("Statistics System Information")
    print("="*80)
    print(f"scipy available: {SCIPY_AVAILABLE}")
    print(f"\nAvailable tiers:")
    print(f"  Tier 1: Descriptive statistics only (always available)")
    print(f"  Tier 2: Approximate statistical tests (no scipy needed)")
    print(f"  Tier 3: Full statistical tests (requires scipy)")
    
    if SCIPY_AVAILABLE:
        print(f"\nCurrent environment: Tier 3 available")
    else:
        print(f"\nCurrent environment: Tier 3 not available (scipy not installed)")
        print(f"  Install scipy: pip install scipy")
    print("="*80 + "\n")


if __name__ == '__main__':
    # Demo the statistics engine
    print_stats_info()
    
    # Test data
    data1 = np.random.normal(100, 15, 50)
    data2 = np.random.normal(105, 15, 50)
    
    # Test Tier 1 (descriptive only)
    print("\nTier 1: Descriptive Only")
    engine1 = StatisticsEngine(enable_stats=False)
    print(f"  Tier: {engine1.get_tier_name()}")
    result = engine1.ttest_1samp(data1, 100)
    print(f"  T-test result: {result}")
    
    # Test Tier 2 (approximate)
    print("\nTier 2: Approximate")
    engine2 = StatisticsEngine(enable_stats=True, force_simple=True)
    print(f"  Tier: {engine2.get_tier_name()}")
    result = engine2.ttest_1samp(data1, 100)
    if result:
        print(f"  T-test: t={result.statistic:.4f}, p={result.pvalue:.4f}, method={result.method}")
    
    # Test Tier 3 (full)
    if SCIPY_AVAILABLE:
        print("\nTier 3: Full (scipy)")
        engine3 = StatisticsEngine(enable_stats=True, force_simple=False)
        print(f"  Tier: {engine3.get_tier_name()}")
        result = engine3.ttest_1samp(data1, 100)
        if result:
            print(f"  T-test: t={result.statistic:.4f}, p={result.pvalue:.4f}, method={result.method}")
