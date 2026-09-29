"""Creation-unit baskets and indicative NAV.

An ETF's primary market trades in *creation units*: a fixed, published basket of
constituent shares plus a balancing cash component, exchanged with the fund for a
fixed number of ETF shares. The fair value of one ETF share during the trading
day is the indicative NAV (iNAV):

    iNAV = (sum(shares_i * price_i * fx_i) + cash_component - accrued_fee)
           / shares_per_creation_unit

Everything here is deterministic and dependency-free: the standard library only,
Python 3.9 and up. Values are carried as floats and rounded only for display, so
the round-trip identities the tests assert hold to floating-point tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping


class BasketError(ValueError):
    """Raised when a basket, quote set or FX table is internally inconsistent."""


@dataclass(frozen=True)
class Holding:
    """One line of a creation basket.

    ticker:   constituent identifier
    shares:   shares delivered per creation unit (fractional allowed)
    currency: settlement currency of the constituent
    """

    ticker: str
    shares: float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.shares <= 0:
            raise BasketError(f"{self.ticker}: shares per creation unit must be > 0")
        if len(self.currency) != 3:
            raise BasketError(f"{self.ticker}: currency must be a 3-letter code")


@dataclass(frozen=True)
class Quote:
    """A two-sided quote for one instrument, in its own currency."""

    bid: float
    ask: float

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise BasketError("quotes must be positive")
        if self.ask < self.bid:
            raise BasketError(f"crossed quote: bid {self.bid} > ask {self.ask}")

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def half_spread_bps(self) -> float:
        """Cost in basis points of crossing half the spread."""
        return 1e4 * (0.5 * self.spread) / self.mid


@dataclass
class CreationUnit:
    """A published creation basket.

    etf:              the fund's ticker
    holdings:         the in-kind share list
    shares_per_unit:  ETF shares issued per creation unit (typically 25k-100k)
    cash_component:   balancing cash delivered with the basket, in USD; negative
                      when the fund pays cash back to the authorised participant
    expense_ratio:    annual management fee as a decimal fraction of NAV
    creation_fee:     flat processing fee per creation or redemption, in USD
    """

    etf: str
    holdings: List[Holding]
    shares_per_unit: int
    cash_component: float = 0.0
    expense_ratio: float = 0.0
    creation_fee: float = 0.0

    def __post_init__(self) -> None:
        if not self.holdings:
            raise BasketError("a creation unit needs at least one holding")
        if self.shares_per_unit <= 0:
            raise BasketError("shares_per_unit must be > 0")
        if not 0.0 <= self.expense_ratio < 0.10:
            raise BasketError("expense_ratio must be a decimal fraction below 10%")
        if self.creation_fee < 0:
            raise BasketError("creation_fee cannot be negative")
        seen = set()
        for h in self.holdings:
            if h.ticker in seen:
                raise BasketError(f"duplicate holding {h.ticker}")
            seen.add(h.ticker)

    @property
    def tickers(self) -> List[str]:
        return [h.ticker for h in self.holdings]

    def currencies(self) -> List[str]:
        return sorted({h.currency for h in self.holdings})

    # ---- valuation -------------------------------------------------------

    @staticmethod
    def _fx_rate(fx: Mapping[str, float], currency: str) -> float:
        if currency == "USD":
            return 1.0
        try:
            rate = fx[currency]
        except KeyError:
            raise BasketError(f"missing FX rate for {currency}") from None
        if rate <= 0:
            raise BasketError(f"FX rate for {currency} must be positive")
        return rate

    def basket_value(
        self,
        quotes: Mapping[str, Quote],
        fx: Mapping[str, float] | None = None,
        side: str = "mid",
    ) -> float:
        """Market value of the in-kind share list, in USD.

        ``side`` selects the price an arbitrageur would actually transact at:
        ``"ask"`` to buy the basket, ``"bid"`` to sell it, ``"mid"`` to value it.
        """
        if side not in ("bid", "ask", "mid"):
            raise BasketError("side must be 'bid', 'ask' or 'mid'")
        fx = fx or {}
        total = 0.0
        for h in self.holdings:
            try:
                q = quotes[h.ticker]
            except KeyError:
                raise BasketError(f"missing quote for {h.ticker}") from None
            px = {"bid": q.bid, "ask": q.ask, "mid": q.mid}[side]
            total += h.shares * px * self._fx_rate(fx, h.currency)
        return total

    def accrued_fee(
        self,
        quotes: Mapping[str, Quote],
        fx: Mapping[str, float] | None = None,
        days: float = 0.0,
    ) -> float:
        """Management fee accrued against one creation unit over ``days`` days.

        Straight-line on a 365-day year against gross assets (in-kind holdings
        plus the cash component), which is how a daily NAV accrual works.
        """
        if days < 0:
            raise BasketError("days cannot be negative")
        gross = self.basket_value(quotes, fx, side="mid") + self.cash_component
        return gross * self.expense_ratio * days / 365.0

    def inav(
        self,
        quotes: Mapping[str, Quote],
        fx: Mapping[str, float] | None = None,
        accrued: float = 0.0,
    ) -> float:
        """Indicative NAV per ETF share, in USD."""
        if accrued < 0:
            raise BasketError("accrued fee cannot be negative")
        net = self.basket_value(quotes, fx, side="mid") + self.cash_component - accrued
        if net <= 0:
            raise BasketError("basket value net of cash and fees is non-positive")
        return net / self.shares_per_unit

    def weights(
        self,
        quotes: Mapping[str, Quote],
        fx: Mapping[str, float] | None = None,
    ) -> Dict[str, float]:
        """Market-value weights of the in-kind holdings (cash component excluded)."""
        total = self.basket_value(quotes, fx, side="mid")
        fx = fx or {}
        out: Dict[str, float] = {}
        for h in self.holdings:
            value = h.shares * quotes[h.ticker].mid * self._fx_rate(fx, h.currency)
            out[h.ticker] = value / total
        return out

    def basket_spread_bps(
        self,
        quotes: Mapping[str, Quote],
        fx: Mapping[str, float] | None = None,
    ) -> float:
        """Weighted half-spread of the underlying basket, in basis points.

        This is what it costs to assemble the basket in the open market, and it is
        the single largest term in the no-arbitrage band for most funds.
        """
        mid = self.basket_value(quotes, fx, side="mid")
        ask = self.basket_value(quotes, fx, side="ask")
        return 1e4 * (ask - mid) / mid


def premium_discount_bps(etf_price: float, inav: float) -> float:
    """Premium (positive) or discount (negative) of an ETF price to iNAV, in bps."""
    if inav <= 0:
        raise BasketError("iNAV must be positive")
    if etf_price <= 0:
        raise BasketError("ETF price must be positive")
    return 1e4 * (etf_price - inav) / inav
