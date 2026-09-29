"""etf-arb-lab: ETF primary/secondary market arbitrage mechanics in pure Python.

Modules
-------
basket      Creation-unit definition and intraday indicative NAV (iNAV).
market      Deterministic simulated intraday tape for constituents and the ETF.
arbitrage   No-arbitrage band, round-trip creation/redemption P&L, opportunity scan.
tracking    Tracking difference decomposition over a holding period.
"""

__version__ = "1.0.0"

__all__ = ["basket", "market", "arbitrage", "tracking"]
