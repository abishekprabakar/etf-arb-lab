"""The deterministic tape generator."""

import math
import unittest

from etfarb.basket import premium_discount_bps
from etfarb.market import (
    Constituent,
    MarketError,
    Rng,
    TapeConfig,
    sample_fund,
    simulate_tape,
)


class TestRng(unittest.TestCase):
    def test_same_seed_same_stream(self):
        a = [Rng(11).normal() for _ in range(50)]
        b = [Rng(11).normal() for _ in range(50)]
        self.assertEqual(a, b)

    def test_different_seeds_diverge(self):
        self.assertNotEqual(Rng(11).normal(), Rng(12).normal())

    def test_uniform_is_open_interval(self):
        rng = Rng(3)
        for _ in range(2_000):
            u = rng.uniform()
            self.assertGreater(u, 0.0)
            self.assertLess(u, 1.0)

    def test_normal_moments_are_plausible(self):
        rng = Rng(99)
        draws = [rng.normal() for _ in range(20_000)]
        mean = sum(draws) / len(draws)
        var = sum((d - mean) ** 2 for d in draws) / (len(draws) - 1)
        self.assertLess(abs(mean), 0.05)
        self.assertLess(abs(math.sqrt(var) - 1.0), 0.05)


class TestTape(unittest.TestCase):
    def setUp(self):
        self.unit, self.constituents, self.fx = sample_fund()

    def test_length_and_labels(self):
        tape = simulate_tape(self.constituents, self.unit, TapeConfig(minutes=390), self.fx)
        self.assertEqual(len(tape), 390)
        self.assertEqual(tape[0].label, "09:30")
        self.assertEqual(tape[-1].label, "15:59")

    def test_quotes_are_never_crossed(self):
        tape = simulate_tape(self.constituents, self.unit, TapeConfig(minutes=200), self.fx)
        for tick in tape:
            self.assertLessEqual(tick.etf.bid, tick.etf.ask)
            for q in tick.quotes.values():
                self.assertLessEqual(q.bid, q.ask)

    def test_every_basket_name_is_quoted(self):
        tape = simulate_tape(self.constituents, self.unit, TapeConfig(minutes=5), self.fx)
        for tick in tape:
            self.assertEqual(set(tick.quotes), set(self.unit.tickers))

    def test_reproducible_from_seed(self):
        a = simulate_tape(self.constituents, self.unit, TapeConfig(minutes=60), self.fx, seed=5)
        b = simulate_tape(self.constituents, self.unit, TapeConfig(minutes=60), self.fx, seed=5)
        self.assertEqual([t.etf.mid for t in a], [t.etf.mid for t in b])

    def test_basis_mean_reverts_rather_than_drifting(self):
        """With a 12-minute half-life the premium should stay bounded."""
        tape = simulate_tape(
            self.constituents, self.unit, TapeConfig(minutes=390), self.fx, seed=31
        )
        premiums = [
            premium_discount_bps(t.etf.mid, self.unit.inav(t.quotes, self.fx))
            for t in tape
        ]
        self.assertLess(max(abs(p) for p in premiums), 100.0)

    def test_zero_shock_pins_etf_to_inav(self):
        config = TapeConfig(minutes=30, basis_shock_bps=0.0, etf_spread_bps=0.0)
        tape = simulate_tape(self.constituents, self.unit, config, self.fx)
        for tick in tape:
            inav = self.unit.inav(tick.quotes, self.fx)
            self.assertAlmostEqual(tick.etf.mid, inav, places=9)

    def test_zero_vol_freezes_prices(self):
        flat = [Constituent(c.ticker, c.price, annual_vol=0.0) for c in self.constituents]
        tape = simulate_tape(flat, self.unit, TapeConfig(minutes=10), self.fx)
        first = {k: q.mid for k, q in tape[0].quotes.items()}
        last = {k: q.mid for k, q in tape[-1].quotes.items()}
        for ticker in first:
            self.assertAlmostEqual(first[ticker], last[ticker], places=8)

    def test_missing_constituent_raises(self):
        with self.assertRaises(MarketError):
            simulate_tape(self.constituents[:2], self.unit, TapeConfig(minutes=2), self.fx)

    def test_bad_config_raises(self):
        with self.assertRaises(MarketError):
            TapeConfig(minutes=0)
        with self.assertRaises(MarketError):
            TapeConfig(basis_halflife=0.0)

    def test_negative_price_rejected(self):
        with self.assertRaises(MarketError):
            Constituent("AAA", -1.0)


class TestSampleFund(unittest.TestCase):
    def test_multi_currency(self):
        unit, _, fx = sample_fund()
        self.assertEqual(unit.currencies(), ["EUR", "JPY", "USD"])
        self.assertEqual(set(fx), {"EUR", "JPY"})

    def test_nav_per_share_is_realistic(self):
        unit, constituents, fx = sample_fund()
        tape = simulate_tape(constituents, unit, TapeConfig(minutes=1), fx)
        inav = unit.inav(tape[0].quotes, fx)
        self.assertGreater(inav, 10.0)
        self.assertLess(inav, 1_000.0)


if __name__ == "__main__":
    unittest.main()
