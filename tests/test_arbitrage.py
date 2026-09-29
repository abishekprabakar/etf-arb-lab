"""The no-arbitrage band and the creation/redemption round trip."""

import unittest

from etfarb.arbitrage import (
    ArbitrageError,
    Frictions,
    no_arb_band,
    round_trip,
    scan,
)
from etfarb.basket import CreationUnit, Holding, Quote
from etfarb.market import TapeConfig, sample_fund, simulate_tape

# A frictionless, single-name, no-FX unit makes the arithmetic checkable by hand.
UNIT = CreationUnit(
    etf="TST",
    holdings=[Holding("AAA", 1_000.0)],
    shares_per_unit=1_000,
    cash_component=0.0,
    expense_ratio=0.0,
    creation_fee=0.0,
)
TIGHT = {"AAA": Quote(100.0, 100.0)}
FREE = Frictions(
    commission_bps=0.0, borrow_rate=0.0, funding_rate=0.0,
    settlement_days=0.0, hedge_slippage_bps=0.0,
)


class TestRoundTrip(unittest.TestCase):
    def test_at_fair_value_no_profit(self):
        # iNAV = 100. ETF quoted at exactly 100 with no spread and no costs.
        trade = round_trip(UNIT, TIGHT, Quote(100.0, 100.0), None, FREE)
        self.assertAlmostEqual(trade.net_pnl, 0.0)
        self.assertFalse(trade.profitable)

    def test_premium_triggers_creation(self):
        trade = round_trip(UNIT, TIGHT, Quote(101.0, 101.0), None, FREE)
        self.assertEqual(trade.direction, "create")
        # Sell 1,000 shares at 101, buy the basket for 100,000.
        self.assertAlmostEqual(trade.gross_pnl, 1_000.0)
        self.assertAlmostEqual(trade.net_pnl, 1_000.0)
        self.assertAlmostEqual(trade.net_pnl_bps, 100.0)

    def test_discount_triggers_redemption(self):
        trade = round_trip(UNIT, TIGHT, Quote(99.0, 99.0), None, FREE)
        self.assertEqual(trade.direction, "redeem")
        self.assertAlmostEqual(trade.gross_pnl, 1_000.0)

    def test_cash_component_is_delivered_not_pocketed(self):
        """A unit that is half cash must not book the cash as arbitrage profit."""
        unit = CreationUnit(
            etf="TST",
            holdings=[Holding("AAA", 500.0)],
            shares_per_unit=1_000,
            cash_component=50_000.0,
        )
        # Basket 50,000 + cash 50,000 = 100,000 over 1,000 shares -> iNAV 100.
        self.assertAlmostEqual(unit.inav(TIGHT), 100.0)
        trade = round_trip(unit, TIGHT, Quote(100.0, 100.0), None, FREE)
        self.assertAlmostEqual(trade.net_pnl, 0.0)
        self.assertAlmostEqual(trade.notional, 100_000.0)

    def test_creation_fee_reduces_pnl(self):
        unit = CreationUnit(
            etf="TST",
            holdings=[Holding("AAA", 1_000.0)],
            shares_per_unit=1_000,
            creation_fee=400.0,
        )
        trade = round_trip(unit, TIGHT, Quote(101.0, 101.0), None, FREE)
        self.assertAlmostEqual(trade.total_cost, 400.0)
        self.assertAlmostEqual(trade.net_pnl, 600.0)

    def test_costs_scale_with_units(self):
        unit = CreationUnit(
            etf="TST",
            holdings=[Holding("AAA", 1_000.0)],
            shares_per_unit=1_000,
            creation_fee=400.0,
        )
        one = round_trip(unit, TIGHT, Quote(101.0, 101.0), None, FREE, units=1)
        three = round_trip(unit, TIGHT, Quote(101.0, 101.0), None, FREE, units=3)
        self.assertAlmostEqual(3 * one.net_pnl, three.net_pnl)
        self.assertEqual(three.etf_shares, 3_000)
        # Per-unit economics are scale invariant.
        self.assertAlmostEqual(one.net_pnl_bps, three.net_pnl_bps)

    def test_spreads_are_crossed_not_split(self):
        """The AP sells at the bid and buys at the offer, never at mid."""
        wide = {"AAA": Quote(99.0, 101.0)}
        # iNAV is still 100 (mid), but the basket costs 101,000 to buy.
        trade = round_trip(UNIT, wide, Quote(100.5, 101.5), None, FREE)
        self.assertEqual(trade.direction, "create")
        self.assertAlmostEqual(trade.gross_pnl, 100.5 * 1_000 - 101_000.0)
        self.assertLess(trade.net_pnl, 0.0)

    def test_carry_charged_over_settlement(self):
        f = Frictions(
            commission_bps=0.0, borrow_rate=0.01, funding_rate=0.04,
            settlement_days=365.0, hedge_slippage_bps=0.0,
        )
        trade = round_trip(UNIT, TIGHT, Quote(101.0, 101.0), None, f)
        # A full year of 5% carry on a 100,000 notional.
        self.assertAlmostEqual(trade.total_cost, 5_000.0)

    def test_borrow_only_charged_when_shorting(self):
        f = Frictions(
            commission_bps=0.0, borrow_rate=0.01, funding_rate=0.0,
            settlement_days=365.0, hedge_slippage_bps=0.0,
        )
        create = round_trip(UNIT, TIGHT, Quote(101.0, 101.0), None, f)
        redeem = round_trip(UNIT, TIGHT, Quote(99.0, 99.0), None, f)
        self.assertGreater(create.total_cost, 0.0)
        self.assertAlmostEqual(redeem.total_cost, 0.0)

    def test_zero_units_rejected(self):
        with self.assertRaises(ArbitrageError):
            round_trip(UNIT, TIGHT, Quote(101.0, 101.0), None, FREE, units=0)


class TestBand(unittest.TestCase):
    def test_frictionless_band_is_zero(self):
        band = no_arb_band(UNIT, TIGHT, Quote(100.0, 100.0), None, FREE)
        self.assertAlmostEqual(band.half_width, 0.0)

    def test_components_sum_to_half_width(self):
        unit, constituents, fx = sample_fund()
        tape = simulate_tape(constituents, unit, TapeConfig(minutes=1), fx)
        band = no_arb_band(unit, tape[0].quotes, tape[0].etf, fx)
        self.assertAlmostEqual(
            sum(v for _, v in band.items()), band.half_width
        )

    def test_wider_spreads_widen_the_band(self):
        tight = no_arb_band(UNIT, TIGHT, Quote(100.0, 100.0), None, FREE)
        wide = no_arb_band(UNIT, {"AAA": Quote(99.0, 101.0)}, Quote(99.0, 101.0), None, FREE)
        self.assertGreater(wide.half_width, tight.half_width)

    def test_settlement_lag_widens_the_band(self):
        short = no_arb_band(UNIT, TIGHT, Quote(100.0, 100.0), None, Frictions(settlement_days=1.0))
        long = no_arb_band(UNIT, TIGHT, Quote(100.0, 100.0), None, Frictions(settlement_days=5.0))
        self.assertGreater(long.half_width, short.half_width)

    def test_negative_settlement_rejected(self):
        with self.assertRaises(ArbitrageError):
            Frictions(settlement_days=-1.0)


class TestScan(unittest.TestCase):
    def setUp(self):
        self.unit, self.constituents, self.fx = sample_fund()
        self.tape = simulate_tape(
            self.constituents, self.unit, TapeConfig(minutes=120), self.fx, seed=7
        )

    def test_every_hit_is_outside_the_band_and_profitable(self):
        hits = scan(self.unit, self.tape, self.fx)
        for opp in hits:
            self.assertGreater(abs(opp.premium_bps), opp.band_bps)
            self.assertGreater(opp.trade.net_pnl, 0.0)
            self.assertAlmostEqual(opp.edge_bps, abs(opp.premium_bps) - opp.band_bps)

    def test_edge_matches_realised_bps(self):
        """Net P&L in bps should land within a basis point of the measured edge."""
        for opp in scan(self.unit, self.tape, self.fx):
            self.assertAlmostEqual(opp.trade.net_pnl_bps, opp.edge_bps, delta=1.0)

    def test_a_huge_band_removes_every_signal(self):
        fat = Frictions(commission_bps=500.0)
        self.assertEqual(scan(self.unit, self.tape, self.fx, fat), [])

    def test_scan_is_deterministic(self):
        a = scan(self.unit, self.tape, self.fx)
        b = scan(self.unit, self.tape, self.fx)
        self.assertEqual([o.index for o in a], [o.index for o in b])


if __name__ == "__main__":
    unittest.main()
