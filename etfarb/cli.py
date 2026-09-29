"""Command-line front end for etf-arb-lab.

    python3 -m etfarb.cli report
    python3 -m etfarb.cli inav
    python3 -m etfarb.cli band --settlement-days 2
    python3 -m etfarb.cli scan --minutes 390 --units 2
    python3 -m etfarb.cli tracking --days 252
"""

from __future__ import annotations

import argparse
import sys
from typing import List

from .arbitrage import Frictions, no_arb_band, round_trip, scan as scan_tape
from .basket import premium_discount_bps
from .market import TapeConfig, sample_fund, simulate_tape
from .tracking import FundPolicy, decompose, premium_statistics, simulate_index

RULE = "-" * 72


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _frictions(args) -> Frictions:
    return Frictions(
        commission_bps=args.commission_bps,
        borrow_rate=args.borrow_rate,
        funding_rate=args.funding_rate,
        settlement_days=args.settlement_days,
        hedge_slippage_bps=args.slippage_bps,
    )


def cmd_inav(args) -> int:
    unit, constituents, fx = sample_fund()
    tape = simulate_tape(constituents, unit, TapeConfig(minutes=1), fx, seed=args.seed)
    quotes, etf = tape[0].quotes, tape[0].etf

    inav = unit.inav(quotes, fx)
    weights = unit.weights(quotes, fx)

    print(f"{unit.etf} creation unit - {unit.shares_per_unit:,} ETF shares per unit")
    print(RULE)
    print(f"{'ticker':<8}{'shares':>12}{'ccy':>6}{'price':>12}{'USD value':>16}{'weight':>9}")
    for h in unit.holdings:
        px = quotes[h.ticker].mid
        rate = 1.0 if h.currency == "USD" else fx[h.currency]
        print(
            f"{h.ticker:<8}{h.shares:>12,.0f}{h.currency:>6}{px:>12,.2f}"
            f"{_money(h.shares * px * rate):>16}{100 * weights[h.ticker]:>8.2f}%"
        )
    print(RULE)
    basket = unit.basket_value(quotes, fx)
    print(f"{'in-kind basket':<26}{_money(basket):>28}")
    print(f"{'cash component':<26}{_money(unit.cash_component):>28}")
    print(f"{'net asset value':<26}{_money(basket + unit.cash_component):>28}")
    print(f"{'iNAV per share':<26}{inav:>28,.4f}")
    print(f"{'ETF bid / ask':<26}{etf.bid:>13,.4f} /{etf.ask:>13,.4f}")
    print(
        f"{'premium to iNAV':<26}"
        f"{premium_discount_bps(etf.mid, inav):>27.2f} bps"
    )
    print(f"{'basket half-spread':<26}{unit.basket_spread_bps(quotes, fx):>27.2f} bps")
    print(f"{'daily fee accrual':<26}{_money(unit.accrued_fee(quotes, fx, 1.0)):>28}")
    return 0


def cmd_band(args) -> int:
    unit, constituents, fx = sample_fund()
    tape = simulate_tape(constituents, unit, TapeConfig(minutes=1), fx, seed=args.seed)
    quotes, etf = tape[0].quotes, tape[0].etf
    band = no_arb_band(unit, quotes, etf, fx, _frictions(args))

    print(f"No-arbitrage band for {unit.etf} (one-sided, basis points)")
    print(RULE)
    for name, value in band.items():
        bar = "#" * int(round(value * 4))
        print(f"{name:<24}{value:>8.2f}  {bar}")
    print(RULE)
    print(f"{'half-width':<24}{band.half_width:>8.2f}")
    print(f"{'full band':<24}{2 * band.half_width:>8.2f}")
    print()
    print(
        f"A premium inside +/-{band.half_width:.2f} bps is not mispricing: correcting"
    )
    print("it costs more than it is worth. Arbitrage only bites outside the band.")
    return 0


def cmd_scan(args) -> int:
    unit, constituents, fx = sample_fund()
    config = TapeConfig(
        minutes=args.minutes,
        etf_spread_bps=args.etf_spread_bps,
        basis_halflife=args.basis_halflife,
        basis_shock_bps=args.basis_shock_bps,
    )
    tape = simulate_tape(constituents, unit, config, fx, seed=args.seed)
    frictions = _frictions(args)

    premiums: List[float] = []
    for tick in tape:
        premiums.append(premium_discount_bps(tick.etf.mid, unit.inav(tick.quotes, fx)))

    stats = premium_statistics(premiums)
    band = no_arb_band(unit, tape[0].quotes, tape[0].etf, fx, frictions).half_width

    print(f"{unit.etf} intraday tape - {len(tape)} minutes, seed {args.seed}")
    print(RULE)
    print(f"{'mean premium':<24}{stats['mean']:>10.2f} bps")
    print(f"{'stdev':<24}{stats['stdev']:>10.2f} bps")
    print(f"{'5th / 95th pctile':<24}{stats['p05']:>10.2f} /{stats['p95']:>8.2f} bps")
    print(f"{'min / max':<24}{stats['min']:>10.2f} /{stats['max']:>8.2f} bps")
    print(f"{'minutes at a premium':<24}{stats['pct_premium']:>10.1f} %")
    print(f"{'no-arb half-width':<24}{band:>10.2f} bps")
    print()

    opportunities = scan_tape(unit, tape, fx, frictions, units=args.units)
    if not opportunities:
        print("No actionable dislocation: the ETF never left the band by enough")
        print("to cover a round trip. That is the normal state of a liquid fund.")
        return 0

    print(f"{len(opportunities)} actionable dislocations")
    print(RULE)
    header = f"{'time':<8}{'dir':<9}{'premium':>10}{'band':>9}{'edge':>9}{'net P&L':>14}"
    print(header)
    for opp in opportunities[: args.limit]:
        print(
            f"{opp.label:<8}{opp.trade.direction:<9}{opp.premium_bps:>9.2f}"
            f"{opp.band_bps:>9.2f}{opp.edge_bps:>9.2f}{_money(opp.trade.net_pnl):>14}"
        )
    if len(opportunities) > args.limit:
        print(f"... {len(opportunities) - args.limit} more")
    print(RULE)
    best = max(opportunities, key=lambda o: o.trade.net_pnl)
    total = sum(o.trade.net_pnl for o in opportunities)
    print(f"{'signals':<24}{len(opportunities):>20}")
    print(f"{'summed net P&L':<24}{_money(total):>20}")
    print("  (an upper bound: it treats every minute as an independent trade,")
    print("   which the fund's own creation capacity would not allow)")
    print(f"{'best single trade':<24}{_money(best.trade.net_pnl):>20}  at {best.label}")
    print(f"{'  direction':<24}{best.trade.direction:>20}")
    print(f"{'  notional':<24}{_money(best.trade.notional):>20}")
    print(f"{'  gross P&L':<24}{_money(best.trade.gross_pnl):>20}")
    print(f"{'  frictions':<24}{_money(-best.trade.total_cost):>20}")
    print(f"{'  net, in bps':<24}{best.trade.net_pnl_bps:>20.2f}")
    return 0


def cmd_tracking(args) -> int:
    policy = FundPolicy(expense_ratio=args.expense_ratio)
    index = simulate_index(args.days, seed=args.seed)
    result = decompose(index, policy, seed=args.seed + 1)

    print(f"Tracking difference over {result.days} trading days")
    print(RULE)
    print(f"{'index return':<26}{100 * result.index_return:>12.4f} %")
    print(f"{'fund NAV return':<26}{100 * result.nav_return:>12.4f} %")
    print(f"{'tracking difference':<26}{1e4 * result.tracking_difference:>12.2f} bps")
    print(RULE)
    for name, value in result.components.items():
        print(f"{name:<26}{1e4 * value:>12.2f} bps")
    print(RULE)
    total = sum(result.components.values())
    print(f"{'sum of components':<26}{1e4 * total:>12.2f} bps")
    print(f"{'reconciles':<26}{str(result.reconciles):>12}")
    print(f"{'annualised difference':<26}{1e4 * result.annualised_difference():>12.2f} bps")
    print(f"{'tracking error (annual)':<26}{1e4 * result.tracking_error_annual:>12.2f} bps")
    return 0


def cmd_report(args) -> int:
    for title, fn in (
        ("1. CREATION BASKET AND iNAV", cmd_inav),
        ("2. NO-ARBITRAGE BAND", cmd_band),
        ("3. INTRADAY OPPORTUNITY SCAN", cmd_scan),
        ("4. TRACKING DIFFERENCE", cmd_tracking),
    ):
        print()
        print("=" * 72)
        print(title)
        print("=" * 72)
        fn(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etfarb",
        description="ETF creation/redemption arbitrage and tracking analytics.",
    )
    parser.add_argument("--seed", type=int, default=20260924, help="simulation seed")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_friction_args(p):
        p.add_argument("--commission-bps", type=float, default=0.5)
        p.add_argument("--borrow-rate", type=float, default=0.0035)
        p.add_argument("--funding-rate", type=float, default=0.0525)
        p.add_argument("--settlement-days", type=float, default=1.0)
        p.add_argument("--slippage-bps", type=float, default=0.0)

    def add_tape_args(p):
        p.add_argument("--minutes", type=int, default=390)
        p.add_argument("--etf-spread-bps", type=float, default=2.0)
        p.add_argument("--basis-halflife", type=float, default=12.0)
        p.add_argument("--basis-shock-bps", type=float, default=1.6)
        p.add_argument("--units", type=int, default=1)
        p.add_argument("--limit", type=int, default=12)

    def add_tracking_args(p):
        p.add_argument("--days", type=int, default=252)
        p.add_argument("--expense-ratio", type=float, default=0.0019)

    p_inav = sub.add_parser("inav", help="creation basket and indicative NAV")
    p_inav.set_defaults(func=cmd_inav)

    p_band = sub.add_parser("band", help="decompose the no-arbitrage band")
    add_friction_args(p_band)
    p_band.set_defaults(func=cmd_band)

    p_scan = sub.add_parser("scan", help="scan an intraday tape for dislocations")
    add_friction_args(p_scan)
    add_tape_args(p_scan)
    p_scan.set_defaults(func=cmd_scan)

    p_track = sub.add_parser("tracking", help="decompose tracking difference")
    add_tracking_args(p_track)
    p_track.set_defaults(func=cmd_tracking)

    p_report = sub.add_parser("report", help="run every section")
    add_friction_args(p_report)
    add_tape_args(p_report)
    add_tracking_args(p_report)
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
