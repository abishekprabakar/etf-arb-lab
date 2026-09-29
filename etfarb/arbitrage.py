"""The no-arbitrage band and the creation/redemption round trip.

An ETF's secondary price is tethered to its basket by a trade an authorised
participant (AP) can always do:

* **Premium** (ETF rich to iNAV): short the ETF at the bid, buy the basket at the
  offer, deliver the basket to the fund and receive new ETF shares, use them to
  cover the short. Profit = ETF bid - basket ask - fees, per share.
* **Discount** (ETF cheap): buy the ETF at the offer, redeem it for the basket,
  sell the basket at the bid. Profit = basket bid - ETF ask - fees, per share.

The trade is not free. Crossing the basket spread, crossing the ETF spread, the
fund's flat creation fee, financing the position over the settlement lag, and the
cost of borrowing ETF shares to short all eat into it. The band those costs carve
out around iNAV is the band inside which a premium simply *persists* - it is not
mispricing, it is the cost of correcting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional

from .basket import CreationUnit, Quote, premium_discount_bps


class ArbitrageError(ValueError):
    """Raised when arbitrage inputs are inconsistent."""


@dataclass(frozen=True)
class Frictions:
    """Costs an AP faces on one creation/redemption round trip.

    creation_fee_usd: flat fund fee per unit (overrides the basket's own if given)
    commission_bps:   brokerage on the basket leg, in bps of notional
    borrow_rate:      annualised ETF borrow cost, applied over the settlement lag
    funding_rate:     annualised cost of financing the basket over the lag
    settlement_days:  calendar days of exposure between the legs
    hedge_slippage_bps: expected slippage hedging the basket leg
    """

    creation_fee_usd: Optional[float] = None
    commission_bps: float = 0.5
    borrow_rate: float = 0.0035
    funding_rate: float = 0.0525
    settlement_days: float = 1.0
    hedge_slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.settlement_days < 0:
            raise ArbitrageError("settlement_days cannot be negative")
        for name in ("commission_bps", "hedge_slippage_bps"):
            if getattr(self, name) < 0:
                raise ArbitrageError(f"{name} cannot be negative")


@dataclass(frozen=True)
class BandBreakdown:
    """The no-arbitrage band around iNAV, decomposed by cost, all in bps."""

    basket_half_spread: float
    etf_half_spread: float
    creation_fee: float
    commission: float
    carry: float
    slippage: float

    @property
    def half_width(self) -> float:
        """One-sided width of the band, in bps."""
        return (
            self.basket_half_spread
            + self.etf_half_spread
            + self.creation_fee
            + self.commission
            + self.carry
            + self.slippage
        )

    def items(self) -> List[tuple]:
        return [
            ("basket half-spread", self.basket_half_spread),
            ("ETF half-spread", self.etf_half_spread),
            ("creation fee", self.creation_fee),
            ("commission", self.commission),
            ("carry over settlement", self.carry),
            ("hedge slippage", self.slippage),
        ]


@dataclass(frozen=True)
class RoundTrip:
    """The outcome of one creation or redemption arbitrage."""

    direction: str            # "create" (ETF rich) or "redeem" (ETF cheap)
    units: int
    etf_shares: int
    gross_pnl: float          # before frictions, USD
    total_cost: float         # all frictions, USD
    net_pnl: float            # USD
    net_pnl_bps: float        # of notional
    notional: float

    @property
    def profitable(self) -> bool:
        return self.net_pnl > 0


def no_arb_band(
    unit: CreationUnit,
    quotes: Mapping[str, Quote],
    etf_quote: Quote,
    fx: Mapping[str, float] | None = None,
    frictions: Frictions = Frictions(),
) -> BandBreakdown:
    """Decompose the round-trip cost into a one-sided band around iNAV, in bps."""
    inav = unit.inav(quotes, fx)
    notional_per_unit = inav * unit.shares_per_unit

    fee_usd = (
        frictions.creation_fee_usd
        if frictions.creation_fee_usd is not None
        else unit.creation_fee
    )
    carry_rate = frictions.funding_rate + frictions.borrow_rate
    carry_bps = 1e4 * carry_rate * frictions.settlement_days / 365.0

    # The spread cost is a cost of crossing the *in-kind* basket, but it is only
    # meaningful as a fraction of the whole unit's notional, cash component and
    # all - that is the denominator the premium itself is quoted against.
    basket_mid = unit.basket_value(quotes, fx, side="mid")
    basket_ask = unit.basket_value(quotes, fx, side="ask")
    spread_bps = 1e4 * (basket_ask - basket_mid) / notional_per_unit

    return BandBreakdown(
        basket_half_spread=spread_bps,
        etf_half_spread=etf_quote.half_spread_bps,
        creation_fee=1e4 * fee_usd / notional_per_unit,
        commission=frictions.commission_bps,
        carry=carry_bps,
        slippage=frictions.hedge_slippage_bps,
    )


def round_trip(
    unit: CreationUnit,
    quotes: Mapping[str, Quote],
    etf_quote: Quote,
    fx: Mapping[str, float] | None = None,
    frictions: Frictions = Frictions(),
    units: int = 1,
) -> RoundTrip:
    """Price one creation or redemption arbitrage at the current quotes.

    The direction is chosen by where the ETF trades relative to iNAV: rich means
    create, cheap means redeem. Both legs are priced at the side the AP actually
    pays, never at mid.
    """
    if units <= 0:
        raise ArbitrageError("units must be >= 1")

    inav = unit.inav(quotes, fx)
    shares = unit.shares_per_unit * units
    fee_usd = (
        frictions.creation_fee_usd
        if frictions.creation_fee_usd is not None
        else unit.creation_fee
    ) * units

    # A creation unit is basket PLUS cash component in one direction and ETF
    # shares in the other. Leaving the cash leg out is the classic way to book a
    # phantom profit exactly equal to the cash component, so it is explicit here.
    cash_leg = unit.cash_component * units

    rich = etf_quote.mid >= inav
    if rich:
        # Sell ETF at the bid, buy the basket at the offer, deliver basket + cash.
        etf_proceeds = etf_quote.bid * shares
        basket_cost = unit.basket_value(quotes, fx, side="ask") * units + cash_leg
        gross = etf_proceeds - basket_cost
        notional = basket_cost
        direction = "create"
    else:
        # Buy ETF at the offer, redeem for basket + cash, sell the basket at the bid.
        etf_cost = etf_quote.ask * shares
        basket_proceeds = unit.basket_value(quotes, fx, side="bid") * units + cash_leg
        gross = basket_proceeds - etf_cost
        notional = etf_cost
        direction = "redeem"

    commission = notional * frictions.commission_bps / 1e4
    slippage = notional * frictions.hedge_slippage_bps / 1e4
    carry_rate = frictions.funding_rate + (frictions.borrow_rate if rich else 0.0)
    carry = notional * carry_rate * frictions.settlement_days / 365.0
    total_cost = fee_usd + commission + slippage + carry
    net = gross - total_cost

    return RoundTrip(
        direction=direction,
        units=units,
        etf_shares=shares,
        gross_pnl=gross,
        total_cost=total_cost,
        net_pnl=net,
        net_pnl_bps=1e4 * net / notional,
        notional=notional,
    )


@dataclass(frozen=True)
class Opportunity:
    """One timestamp at which the ETF traded outside the no-arbitrage band."""

    index: int
    label: str
    premium_bps: float
    band_bps: float
    edge_bps: float
    trade: RoundTrip


def scan(
    unit: CreationUnit,
    tape,
    fx: Mapping[str, float] | None = None,
    frictions: Frictions = Frictions(),
    units: int = 1,
) -> List[Opportunity]:
    """Walk an intraday tape and return every actionable dislocation.

    ``tape`` is any iterable of ``market.Tick``-shaped objects exposing
    ``label``, ``quotes`` and ``etf``.
    """
    out: List[Opportunity] = []
    for i, tick in enumerate(tape):
        inav = unit.inav(tick.quotes, fx)
        prem = premium_discount_bps(tick.etf.mid, inav)
        band = no_arb_band(unit, tick.quotes, tick.etf, fx, frictions).half_width
        if abs(prem) <= band:
            continue
        trade = round_trip(unit, tick.quotes, tick.etf, fx, frictions, units)
        if not trade.profitable:
            continue
        out.append(
            Opportunity(
                index=i,
                label=tick.label,
                premium_bps=prem,
                band_bps=band,
                edge_bps=abs(prem) - band,
                trade=trade,
            )
        )
    return out
