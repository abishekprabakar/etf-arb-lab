"""Basket construction, valuation and iNAV."""

import unittest

from etfarb.basket import (
    BasketError,
    CreationUnit,
    Holding,
    Quote,
    premium_discount_bps,
)


def simple_unit(**kwargs) -> CreationUnit:
    defaults = dict(
        etf="TST",
        holdings=[Holding("AAA", 100.0), Holding("BBB", 50.0, "EUR")],
        shares_per_unit=1_000,
        cash_component=500.0,
        expense_ratio=0.0020,
        creation_fee=250.0,
    )
    defaults.update(kwargs)
    return CreationUnit(**defaults)


QUOTES = {"AAA": Quote(99.0, 101.0), "BBB": Quote(19.8, 20.2)}
FX = {"EUR": 1.10}


class TestQuote(unittest.TestCase):
    def test_mid_and_spread(self):
        q = Quote(99.0, 101.0)
        self.assertAlmostEqual(q.mid, 100.0)
        self.assertAlmostEqual(q.spread, 2.0)
        self.assertAlmostEqual(q.half_spread_bps, 100.0)

    def test_crossed_quote_rejected(self):
        with self.assertRaises(BasketError):
            Quote(101.0, 99.0)

    def test_non_positive_rejected(self):
        with self.assertRaises(BasketError):
            Quote(0.0, 1.0)


class TestHolding(unittest.TestCase):
    def test_zero_shares_rejected(self):
        with self.assertRaises(BasketError):
            Holding("AAA", 0.0)

    def test_bad_currency_rejected(self):
        with self.assertRaises(BasketError):
            Holding("AAA", 1.0, "DOLLARS")


class TestCreationUnit(unittest.TestCase):
    def test_duplicate_holding_rejected(self):
        with self.assertRaises(BasketError):
            simple_unit(holdings=[Holding("AAA", 1.0), Holding("AAA", 2.0)])

    def test_empty_basket_rejected(self):
        with self.assertRaises(BasketError):
            simple_unit(holdings=[])

    def test_absurd_expense_ratio_rejected(self):
        with self.assertRaises(BasketError):
            simple_unit(expense_ratio=0.5)

    def test_basket_value_mid(self):
        unit = simple_unit()
        # 100 * 100 USD + 50 * 20 * 1.10 EUR = 10,000 + 1,100
        self.assertAlmostEqual(unit.basket_value(QUOTES, FX), 11_100.0)

    def test_bid_below_mid_below_ask(self):
        unit = simple_unit()
        bid = unit.basket_value(QUOTES, FX, "bid")
        mid = unit.basket_value(QUOTES, FX, "mid")
        ask = unit.basket_value(QUOTES, FX, "ask")
        self.assertLess(bid, mid)
        self.assertLess(mid, ask)
        # The spread is symmetric around mid for symmetric quotes.
        self.assertAlmostEqual(mid - bid, ask - mid)

    def test_missing_fx_raises(self):
        unit = simple_unit()
        with self.assertRaises(BasketError):
            unit.basket_value(QUOTES, {})

    def test_missing_quote_raises(self):
        unit = simple_unit()
        with self.assertRaises(BasketError):
            unit.basket_value({"AAA": Quote(99.0, 101.0)}, FX)

    def test_bad_side_raises(self):
        unit = simple_unit()
        with self.assertRaises(BasketError):
            unit.basket_value(QUOTES, FX, side="last")

    def test_inav(self):
        unit = simple_unit()
        # (11,100 + 500) / 1,000
        self.assertAlmostEqual(unit.inav(QUOTES, FX), 11.6)

    def test_inav_net_of_accrued_fee(self):
        unit = simple_unit()
        self.assertAlmostEqual(unit.inav(QUOTES, FX, accrued=100.0), 11.5)

    def test_accrued_fee_is_pro_rata(self):
        unit = simple_unit()
        one_day = unit.accrued_fee(QUOTES, FX, 1.0)
        ten_days = unit.accrued_fee(QUOTES, FX, 10.0)
        self.assertAlmostEqual(10.0 * one_day, ten_days)
        # gross assets 11,600 * 0.20% / 365
        self.assertAlmostEqual(one_day, 11_600.0 * 0.0020 / 365.0)

    def test_weights_sum_to_one(self):
        unit = simple_unit()
        weights = unit.weights(QUOTES, FX)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertAlmostEqual(weights["AAA"], 10_000.0 / 11_100.0)

    def test_usd_needs_no_fx_entry(self):
        unit = simple_unit(holdings=[Holding("AAA", 100.0)])
        self.assertAlmostEqual(unit.basket_value(QUOTES), 10_000.0)


class TestPremiumDiscount(unittest.TestCase):
    def test_premium_positive(self):
        self.assertAlmostEqual(premium_discount_bps(101.0, 100.0), 100.0)

    def test_discount_negative(self):
        self.assertAlmostEqual(premium_discount_bps(99.0, 100.0), -100.0)

    def test_zero_at_fair_value(self):
        self.assertAlmostEqual(premium_discount_bps(100.0, 100.0), 0.0)

    def test_non_positive_inav_raises(self):
        with self.assertRaises(BasketError):
            premium_discount_bps(100.0, 0.0)


if __name__ == "__main__":
    unittest.main()
