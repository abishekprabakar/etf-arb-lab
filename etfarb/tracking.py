"""Tracking difference and tracking error, decomposed.

A fund's NAV does not return exactly what its index returns, and the gap is not
one number with one cause. Five effects dominate for a physically replicating
equity ETF:

1. the management fee, accrued daily against NAV;
2. cash drag - uninvested cash earning the policy rate instead of the index;
3. dividend withholding tax on foreign holdings;
4. securities-lending revenue rebated to the fund, which offsets the rest;
5. sampling error, where the fund does not hold every index name exactly.

The decomposition here is a *sequential build-up*: start from the index's
compounded return and switch on one effect at a time, recording the change in
cumulative return each switch causes. Because each step is measured against the
one before it, the components sum to the total tracking difference exactly -
there is no unexplained residual and no double counting of compounding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence

from .market import Rng


class TrackingError(ValueError):
    """Raised on invalid tracking-model inputs."""


@dataclass(frozen=True)
class FundPolicy:
    """The fund-level parameters that drive tracking difference.

    expense_ratio:      annual management fee
    cash_weight:        average fraction of assets held in cash
    cash_rate:          annual rate earned on that cash
    foreign_weight:     fraction of assets subject to dividend withholding
    dividend_yield:     annual gross dividend yield of the portfolio
    withholding_rate:   treaty withholding rate on foreign dividends
    lending_yield:      annual securities-lending revenue rebated to the fund
    sampling_te:        annualised standard deviation of sampling noise
    """

    expense_ratio: float = 0.0019
    cash_weight: float = 0.004
    cash_rate: float = 0.0510
    foreign_weight: float = 0.38
    dividend_yield: float = 0.0180
    withholding_rate: float = 0.15
    lending_yield: float = 0.0006
    sampling_te: float = 0.0015

    def __post_init__(self) -> None:
        for name in (
            "expense_ratio", "cash_weight", "cash_rate", "foreign_weight",
            "dividend_yield", "withholding_rate", "lending_yield", "sampling_te",
        ):
            if getattr(self, name) < 0:
                raise TrackingError(f"{name} cannot be negative")
        if self.cash_weight > 1 or self.foreign_weight > 1 or self.withholding_rate > 1:
            raise TrackingError("weights and rates must be fractions <= 1")


@dataclass(frozen=True)
class TrackingResult:
    """Decomposed tracking difference over a holding period."""

    days: int
    index_return: float
    nav_return: float
    tracking_difference: float
    components: Dict[str, float]
    tracking_error_annual: float

    @property
    def reconciles(self) -> bool:
        """True when the components sum to the tracking difference."""
        return abs(sum(self.components.values()) - self.tracking_difference) < 1e-12

    def annualised_difference(self) -> float:
        """Tracking difference restated as an annual rate."""
        if self.days == 0:
            return 0.0
        return (1.0 + self.tracking_difference) ** (252.0 / self.days) - 1.0


def simulate_index(
    days: int,
    annual_vol: float = 0.17,
    annual_drift: float = 0.07,
    seed: int = 4242,
) -> List[float]:
    """A seeded daily index return series."""
    if days <= 0:
        raise TrackingError("days must be > 0")
    if annual_vol < 0:
        raise TrackingError("annual_vol cannot be negative")
    rng = Rng(seed)
    sigma = annual_vol / math.sqrt(252.0)
    mu = annual_drift / 252.0 - 0.5 * sigma * sigma
    return [math.exp(mu + sigma * rng.normal()) - 1.0 for _ in range(days)]


def _compound(returns: Sequence[float]) -> float:
    total = 1.0
    for r in returns:
        total *= 1.0 + r
    return total - 1.0


def decompose(
    index_returns: Sequence[float],
    policy: FundPolicy = FundPolicy(),
    seed: int = 909,
) -> TrackingResult:
    """Decompose NAV tracking difference into its causes.

    Each effect is switched on in turn against the series built so far, and the
    change in compounded return is that effect's contribution.
    """
    if not index_returns:
        raise TrackingError("index_returns cannot be empty")

    days = len(index_returns)
    rng = Rng(seed)
    sampling_daily = policy.sampling_te / math.sqrt(252.0)
    noise = [sampling_daily * rng.normal() for _ in range(days)]

    fee = policy.expense_ratio / 252.0
    cash_gap = (policy.cash_rate / 252.0)
    withhold = policy.foreign_weight * policy.dividend_yield * policy.withholding_rate / 252.0
    lending = policy.lending_yield / 252.0

    # Each layer is a function of the index return for a given day.
    layers: List[tuple] = [
        ("cash drag", lambda r, i: (1 - policy.cash_weight) * r + policy.cash_weight * cash_gap),
        ("management fee", lambda r, i: r - fee),
        ("dividend withholding", lambda r, i: r - withhold),
        ("securities lending", lambda r, i: r + lending),
        ("sampling error", lambda r, i: r + noise[i]),
    ]

    series = list(index_returns)
    index_total = _compound(series)
    previous_total = index_total
    components: Dict[str, float] = {}

    for name, fn in layers:
        # The cash-drag layer rebuilds from the index series; the rest are additive
        # adjustments to whatever the previous layer produced.
        if name == "cash drag":
            series = [fn(index_returns[i], i) for i in range(days)]
        else:
            series = [fn(series[i], i) for i in range(days)]
        total = _compound(series)
        components[name] = total - previous_total
        previous_total = total

    nav_total = previous_total
    diffs = [series[i] - index_returns[i] for i in range(days)]
    mean = sum(diffs) / days
    if days > 1:
        var = sum((d - mean) ** 2 for d in diffs) / (days - 1)
    else:
        var = 0.0
    te = math.sqrt(var) * math.sqrt(252.0)

    return TrackingResult(
        days=days,
        index_return=index_total,
        nav_return=nav_total,
        tracking_difference=nav_total - index_total,
        components=components,
        tracking_error_annual=te,
    )


def premium_statistics(premiums_bps: Sequence[float]) -> Dict[str, float]:
    """Summary statistics of a premium/discount series, in basis points."""
    if not premiums_bps:
        raise TrackingError("premium series cannot be empty")
    ordered = sorted(premiums_bps)
    n = len(ordered)
    mean = sum(ordered) / n
    var = sum((p - mean) ** 2 for p in ordered) / (n - 1) if n > 1 else 0.0

    def pct(p: float) -> float:
        if n == 1:
            return ordered[0]
        pos = p * (n - 1)
        low = int(math.floor(pos))
        high = min(low + 1, n - 1)
        return ordered[low] + (pos - low) * (ordered[high] - ordered[low])

    return {
        "mean": mean,
        "stdev": math.sqrt(var),
        "min": ordered[0],
        "p05": pct(0.05),
        "median": pct(0.50),
        "p95": pct(0.95),
        "max": ordered[-1],
        "pct_premium": 100.0 * sum(1 for p in ordered if p > 0) / n,
    }
