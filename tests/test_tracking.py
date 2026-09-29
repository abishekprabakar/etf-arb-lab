"""Tracking difference decomposition and premium statistics."""

import unittest

from etfarb.tracking import (
    FundPolicy,
    TrackingError,
    decompose,
    premium_statistics,
    simulate_index,
)


class TestDecompose(unittest.TestCase):
    def setUp(self):
        self.index = simulate_index(252, seed=1234)

    def test_components_reconcile_exactly(self):
        result = decompose(self.index)
        self.assertTrue(result.reconciles)
        self.assertAlmostEqual(
            sum(result.components.values()), result.tracking_difference, places=15
        )

    def test_all_five_effects_present(self):
        result = decompose(self.index)
        self.assertEqual(
            set(result.components),
            {
                "cash drag",
                "management fee",
                "dividend withholding",
                "securities lending",
                "sampling error",
            },
        )

    def test_nav_minus_index_is_the_difference(self):
        result = decompose(self.index)
        self.assertAlmostEqual(
            result.nav_return - result.index_return, result.tracking_difference
        )

    def test_a_costless_fund_tracks_perfectly(self):
        policy = FundPolicy(
            expense_ratio=0.0, cash_weight=0.0, cash_rate=0.0,
            foreign_weight=0.0, dividend_yield=0.0, withholding_rate=0.0,
            lending_yield=0.0, sampling_te=0.0,
        )
        result = decompose(self.index, policy)
        self.assertAlmostEqual(result.tracking_difference, 0.0, places=12)
        self.assertAlmostEqual(result.tracking_error_annual, 0.0, places=12)

    def test_fee_is_the_dominant_negative_term(self):
        policy = FundPolicy(
            expense_ratio=0.0095, cash_weight=0.0, foreign_weight=0.0,
            lending_yield=0.0, sampling_te=0.0,
        )
        result = decompose(self.index, policy)
        self.assertLess(result.components["management fee"], 0.0)
        self.assertLess(result.tracking_difference, -0.008)

    def test_higher_fee_tracks_worse(self):
        cheap = decompose(self.index, FundPolicy(expense_ratio=0.0003, sampling_te=0.0))
        dear = decompose(self.index, FundPolicy(expense_ratio=0.0095, sampling_te=0.0))
        self.assertLess(dear.tracking_difference, cheap.tracking_difference)

    def test_sampling_noise_drives_tracking_error(self):
        clean = decompose(self.index, FundPolicy(sampling_te=0.0))
        noisy = decompose(self.index, FundPolicy(sampling_te=0.0040))
        self.assertGreater(noisy.tracking_error_annual, clean.tracking_error_annual)
        # Recovered tracking error should be near the input, within sampling noise.
        self.assertAlmostEqual(noisy.tracking_error_annual, 0.0040, delta=0.0008)

    def test_lending_revenue_helps(self):
        without = decompose(self.index, FundPolicy(lending_yield=0.0, sampling_te=0.0))
        with_lending = decompose(self.index, FundPolicy(lending_yield=0.0025, sampling_te=0.0))
        self.assertGreater(
            with_lending.tracking_difference, without.tracking_difference
        )

    def test_annualisation_of_a_full_year_is_a_no_op(self):
        result = decompose(self.index)
        self.assertAlmostEqual(
            result.annualised_difference(), result.tracking_difference, places=12
        )

    def test_half_year_annualises_upward_in_magnitude(self):
        half = decompose(simulate_index(126, seed=1234), FundPolicy(sampling_te=0.0))
        self.assertGreater(
            abs(half.annualised_difference()), abs(half.tracking_difference)
        )

    def test_deterministic(self):
        a = decompose(self.index, seed=77)
        b = decompose(self.index, seed=77)
        self.assertEqual(a.components, b.components)

    def test_empty_series_rejected(self):
        with self.assertRaises(TrackingError):
            decompose([])

    def test_negative_policy_rejected(self):
        with self.assertRaises(TrackingError):
            FundPolicy(expense_ratio=-0.001)

    def test_weight_above_one_rejected(self):
        with self.assertRaises(TrackingError):
            FundPolicy(cash_weight=1.5)


class TestSimulateIndex(unittest.TestCase):
    def test_length_and_determinism(self):
        a = simulate_index(60, seed=8)
        self.assertEqual(len(a), 60)
        self.assertEqual(a, simulate_index(60, seed=8))

    def test_zero_vol_is_pure_drift(self):
        returns = simulate_index(10, annual_vol=0.0, annual_drift=0.0252)
        for r in returns:
            self.assertAlmostEqual(r, returns[0], places=15)

    def test_zero_days_rejected(self):
        with self.assertRaises(TrackingError):
            simulate_index(0)


class TestPremiumStatistics(unittest.TestCase):
    def test_known_series(self):
        stats = premium_statistics([-2.0, -1.0, 0.0, 1.0, 2.0])
        self.assertAlmostEqual(stats["mean"], 0.0)
        self.assertAlmostEqual(stats["median"], 0.0)
        self.assertAlmostEqual(stats["min"], -2.0)
        self.assertAlmostEqual(stats["max"], 2.0)
        self.assertAlmostEqual(stats["pct_premium"], 40.0)

    def test_percentiles_are_ordered(self):
        stats = premium_statistics([float(i) for i in range(100)])
        self.assertLessEqual(stats["min"], stats["p05"])
        self.assertLessEqual(stats["p05"], stats["median"])
        self.assertLessEqual(stats["median"], stats["p95"])
        self.assertLessEqual(stats["p95"], stats["max"])

    def test_single_point(self):
        stats = premium_statistics([4.0])
        self.assertAlmostEqual(stats["mean"], 4.0)
        self.assertAlmostEqual(stats["stdev"], 0.0)
        self.assertAlmostEqual(stats["median"], 4.0)

    def test_empty_rejected(self):
        with self.assertRaises(TrackingError):
            premium_statistics([])


if __name__ == "__main__":
    unittest.main()
