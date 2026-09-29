"""A deterministic simulated intraday tape.

No network, no API keys. Constituent prices follow a seeded geometric random walk
with per-name volatility; the ETF's own quote is driven by the basket but carries
a mean-reverting *basis* that widens on shocks and decays as arbitrageurs lean on
it. That is the behaviour the rest of the library is built to measure, so it has
to be in the generator rather than assumed away.

The pseudo-random source is a self-contained 64-bit linear congruential
generator, so a given seed produces identical output on every platform and every
Python build - the README's sample output is reproducible from the seed alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Sequence

from .basket import CreationUnit, Quote


class MarketError(ValueError):
    """Raised on an invalid market-simulation parameter."""


class Rng:
    """A small, fully specified LCG with a Box-Muller normal draw.

    Constants are those of Knuth's MMIX generator.
    """

    _A = 6364136223846793005
    _C = 1442695040888963407
    _M = 1 << 64

    def __init__(self, seed: int) -> None:
        self._state = seed % self._M
        self._spare: float | None = None

    def next_u64(self) -> int:
        self._state = (self._A * self._state + self._C) % self._M
        return self._state

    def uniform(self) -> float:
        """Uniform on (0, 1), never exactly 0 or 1."""
        return (self.next_u64() >> 11) / float(1 << 53) * (1 - 2e-16) + 1e-16

    def normal(self) -> float:
        if self._spare is not None:
            value, self._spare = self._spare, None
            return value
        u1, u2 = self.uniform(), self.uniform()
        r = math.sqrt(-2.0 * math.log(u1))
        theta = 2.0 * math.pi * u2
        self._spare = r * math.sin(theta)
        return r * math.cos(theta)


@dataclass(frozen=True)
class Constituent:
    """Simulation parameters for one name in the basket."""

    ticker: str
    price: float
    annual_vol: float = 0.22
    spread_bps: float = 3.0

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise MarketError(f"{self.ticker}: price must be positive")
        if self.annual_vol < 0:
            raise MarketError(f"{self.ticker}: vol cannot be negative")
        if self.spread_bps < 0:
            raise MarketError(f"{self.ticker}: spread cannot be negative")


@dataclass(frozen=True)
class Tick:
    """One timestamp of the tape."""

    label: str
    quotes: Dict[str, Quote]
    etf: Quote


@dataclass
class TapeConfig:
    """How the intraday tape behaves.

    minutes:          number of one-minute bars (390 is a US equity session)
    etf_spread_bps:   full quoted spread of the ETF, in bps
    basis_halflife:   minutes for a basis shock to decay by half
    basis_shock_bps:  standard deviation of the per-minute basis innovation
    market_vol_mult:  multiplier applied to every constituent's volatility
    """

    minutes: int = 390
    etf_spread_bps: float = 2.0
    basis_halflife: float = 12.0
    basis_shock_bps: float = 1.6
    market_vol_mult: float = 1.0

    def __post_init__(self) -> None:
        if self.minutes <= 0:
            raise MarketError("minutes must be > 0")
        if self.basis_halflife <= 0:
            raise MarketError("basis_halflife must be > 0")
        if self.etf_spread_bps < 0 or self.basis_shock_bps < 0:
            raise MarketError("spread and shock parameters cannot be negative")


def _clock_label(minute: int) -> str:
    """Minute index to a wall-clock label on a 09:30-16:00 session."""
    total = 9 * 60 + 30 + minute
    return f"{total // 60:02d}:{total % 60:02d}"


def simulate_tape(
    constituents: Sequence[Constituent],
    unit: CreationUnit,
    config: TapeConfig = TapeConfig(),
    fx: Mapping[str, float] | None = None,
    seed: int = 20260924,
) -> List[Tick]:
    """Generate a full session of quotes for the basket and the ETF.

    The ETF mid is set to iNAV scaled by an exponentially decaying basis with
    fresh shocks each minute, which is how a real fund behaves: dislocations open
    quickly and close over minutes, not instantly.
    """
    if not constituents:
        raise MarketError("need at least one constituent")
    missing = set(unit.tickers) - {c.ticker for c in constituents}
    if missing:
        raise MarketError(f"no simulation parameters for {sorted(missing)}")

    rng = Rng(seed)
    prices = {c.ticker: c.price for c in constituents}
    params = {c.ticker: c for c in constituents}
    minutes_per_year = 252.0 * 390.0
    decay = 0.5 ** (1.0 / config.basis_halflife)

    basis_bps = 0.0
    ticks: List[Tick] = []

    for minute in range(config.minutes):
        quotes: Dict[str, Quote] = {}
        for ticker in unit.tickers:
            c = params[ticker]
            sigma = c.annual_vol * config.market_vol_mult / math.sqrt(minutes_per_year)
            drift = -0.5 * sigma * sigma
            prices[ticker] *= math.exp(drift + sigma * rng.normal())
            half = prices[ticker] * c.spread_bps / 2e4
            quotes[ticker] = Quote(bid=prices[ticker] - half, ask=prices[ticker] + half)

        inav = unit.inav(quotes, fx)
        basis_bps = basis_bps * decay + config.basis_shock_bps * rng.normal()
        etf_mid = inav * (1.0 + basis_bps / 1e4)
        etf_half = etf_mid * config.etf_spread_bps / 2e4
        ticks.append(
            Tick(
                label=_clock_label(minute),
                quotes=quotes,
                etf=Quote(bid=etf_mid - etf_half, ask=etf_mid + etf_half),
            )
        )

    return ticks


def sample_fund() -> tuple:
    """A five-name global equity fund used by the CLI and the README.

    Two names settle in EUR and JPY, so the FX leg is exercised rather than
    stubbed out. Returns ``(unit, constituents, fx)``.
    """
    from .basket import Holding

    constituents = [
        Constituent("AXN", price=182.40, annual_vol=0.24, spread_bps=2.0),
        Constituent("BRQ", price=96.15, annual_vol=0.19, spread_bps=3.0),
        Constituent("CDL", price=311.80, annual_vol=0.31, spread_bps=4.0),
        Constituent("DVM", price=54.70, annual_vol=0.21, spread_bps=6.0),
        Constituent("EHT", price=2380.00, annual_vol=0.26, spread_bps=9.0),
    ]
    unit = CreationUnit(
        etf="GLBX",
        holdings=[
            Holding("AXN", shares=7_250.0),
            Holding("BRQ", shares=10_500.0),
            Holding("CDL", shares=3_200.0),
            Holding("DVM", shares=15_250.0, currency="EUR"),
            Holding("EHT", shares=27_000.0, currency="JPY"),
        ],
        shares_per_unit=50_000,
        cash_component=92_500.0,
        expense_ratio=0.0019,
        creation_fee=500.0,
    )
    fx = {"EUR": 1.0840, "JPY": 0.006630}
    return unit, constituents, fx
