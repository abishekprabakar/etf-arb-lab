# etf-arb-lab

The mechanics that keep an ETF's share price glued to the value of what it owns,
implemented in pure Python. No NumPy, no pandas, no network calls, no API keys -
the standard library and nothing else, on Python 3.9 and up.

An ETF has two prices at once: what it trades for on the exchange, and what the
basket behind it is worth. They stay close because an authorised participant can
always swap one for the other. This library models that swap end to end - the
basket, the indicative NAV, the costs of the round trip, and the band those costs
carve out around fair value - and then measures the gap that is left over the
long run as tracking difference.

The point worth internalising: **a premium is not automatically a mispricing.**
Below a certain size it is simply cheaper to leave it alone. This code computes
where that line sits and what it is made of.

## What it does

- **Creation baskets and iNAV** - a published in-kind share list plus a cash
  component, valued in multiple currencies, with daily management-fee accrual
  and market-value weights.
- **The no-arbitrage band** - decomposed into the basket half-spread, the ETF
  half-spread, the fund's flat creation fee, brokerage, and the carry on
  financing and stock borrow over the settlement lag.
- **The round trip** - creation when the fund is rich, redemption when it is
  cheap, with every leg priced at the side an arbitrageur actually transacts at,
  and the cash component delivered rather than quietly pocketed.
- **An intraday scan** - a deterministic 390-minute tape with a mean-reverting
  basis, swept for every minute the fund left the band by enough to pay for the
  trade.
- **Tracking difference** - the management fee, cash drag, dividend withholding,
  securities-lending revenue and sampling error, decomposed so the parts sum to
  the whole exactly.

## Install and run

Nothing to install.

```
git clone https://github.com/abishekprabakar/etf-arb-lab.git
cd etf-arb-lab
python3 -m etfarb.cli report
```

Individual sections:

```
python3 -m etfarb.cli inav                       # basket and indicative NAV
python3 -m etfarb.cli band --settlement-days 2   # what the band is made of
python3 -m etfarb.cli scan --units 2 --limit 20  # sweep an intraday tape
python3 -m etfarb.cli tracking --days 252 --expense-ratio 0.0035
```

Tests:

```
python3 -m unittest discover -s tests -t .
```

## How the numbers are produced

There is no market data feed here and no key-gated API. Prices come from a
seeded generator: constituents follow a geometric random walk with per-name
volatility and quoted spreads, and the ETF's mid is iNAV scaled by a basis that
takes a fresh shock every minute and decays with a 12-minute half-life. The
pseudo-random source is a self-contained 64-bit LCG with Box-Muller normals, so
a seed reproduces the same tape on any platform - including every number printed
below.

The tracking decomposition is a *sequential build-up* rather than a set of
independent estimates. Each effect is switched on against the series built so
far and charged with the change in compounded return it causes. That is why
`reconciles` is `True` in the output: the components sum to the tracking
difference to machine precision, with no plug and no double-counted compounding.

## The cash-component trap

The single easiest way to get ETF arbitrage wrong is to compare the ETF price to
the in-kind basket and forget that a creation unit is *basket plus cash*. Do that
and every round trip books a phantom profit exactly equal to the cash component -
in the sample fund below, about $92,500 a unit, or roughly 195 bps of a notional
that should be earning single-digit basis points. `round_trip()` delivers the
cash leg explicitly, and `test_cash_component_is_delivered_not_pocketed` pins it.

## Sample output

Produced by `python3 -m etfarb.cli report` at the default seed, pasted verbatim:

```
========================================================================
1. CREATION BASKET AND iNAV
========================================================================
GLBX creation unit - 50,000 ETF shares per unit
------------------------------------------------------------------------
ticker        shares   ccy       price       USD value   weight
AXN            7,250   USD      182.38    1,322,242.12   28.39%
BRQ           10,500   USD       96.06    1,008,647.17   21.66%
CDL            3,200   USD      311.26      996,018.95   21.38%
DVM           15,250   EUR       54.72      904,607.64   19.42%
EHT           27,000   JPY    2,380.36      426,108.94    9.15%
------------------------------------------------------------------------
in-kind basket                            4,657,624.82
cash component                               92,500.00
net asset value                           4,750,124.82
iNAV per share                                 95.0025
ETF bid / ask                   95.0064 /      95.0254
premium to iNAV                                  1.41 bps
basket half-spread                               2.03 bps
daily fee accrual                                24.73

========================================================================
2. NO-ARBITRAGE BAND
========================================================================
No-arbitrage band for GLBX (one-sided, basis points)
------------------------------------------------------------------------
basket half-spread          1.99  ########
ETF half-spread             1.00  ####
creation fee                1.05  ####
commission                  0.50  ##
carry over settlement       1.53  ######
hedge slippage              0.00  
------------------------------------------------------------------------
half-width                  6.08
full band                  12.16

A premium inside +/-6.08 bps is not mispricing: correcting
it costs more than it is worth. Arbitrage only bites outside the band.

========================================================================
3. INTRADAY OPPORTUNITY SCAN
========================================================================
GLBX intraday tape - 390 minutes, seed 20260924
------------------------------------------------------------------------
mean premium                  1.76 bps
stdev                         4.11 bps
5th / 95th pctile            -4.64 /    8.75 bps
min / max                    -8.57 /   16.75 bps
minutes at a premium          67.2 %
no-arb half-width             6.08 bps

65 actionable dislocations
------------------------------------------------------------------------
time    dir         premium     band     edge       net P&L
09:43   create        6.16     6.08     0.08         36.90
09:44   create        6.09     6.08     0.01          3.50
09:48   create        7.95     6.08     1.88        889.01
09:49   create        9.55     6.08     3.48      1,648.37
09:50   create       11.04     6.08     4.96      2,354.20
09:51   create       10.94     6.08     4.86      2,303.55
09:52   create       12.86     6.08     6.78      3,215.67
09:53   create       16.75     6.08    10.67      5,061.42
09:54   create       16.33     6.08    10.25      4,860.96
09:55   create       14.65     6.08     8.57      4,063.53
09:56   create       16.60     6.08    10.52      4,991.22
09:57   create       16.25     6.08    10.17      4,825.79
... 53 more
------------------------------------------------------------------------
signals                                   65
summed net P&L                     76,075.22
  (an upper bound: it treats every minute as an independent trade,
   which the fund's own creation capacity would not allow)
best single trade                   5,061.42  at 09:53
  direction                           create
  notional                      4,745,102.83
  gross P&L                         6,526.69
  frictions                        -1,465.27
  net, in bps                          10.67

========================================================================
4. TRACKING DIFFERENCE
========================================================================
Tracking difference over 252 trading days
------------------------------------------------------------------------
index return                   -9.7219 %
fund NAV return                -9.8815 %
tracking difference             -15.96 bps
------------------------------------------------------------------------
cash drag                         6.04 bps
management fee                  -17.16 bps
dividend withholding             -9.25 bps
securities lending                5.41 bps
sampling error                   -1.01 bps
------------------------------------------------------------------------
sum of components               -15.96 bps
reconciles                        True
annualised difference           -15.96 bps
tracking error (annual)          17.05 bps
```

Reading that: the band is 6.08 bps wide on each side, and the creation fee is
only a sixth of it - the basket's own spread and the cost of carrying the
position to settlement matter more. The fund spent 65 of 390 minutes outside the
band, all of them on the rich side during the morning, which is the signature of
a fund whose foreign holdings reprice before its own shares do. Over a year the
fund lagged its index by 16 bps: a 19 bp fee and 9 bps of withholding tax, partly
paid back by lending revenue and by holding cash while the index fell.

## Layout

```
etfarb/basket.py      creation units, quotes, iNAV, premium/discount
etfarb/market.py      seeded price tape and the sample multi-currency fund
etfarb/arbitrage.py   no-arbitrage band, round-trip P&L, opportunity scan
etfarb/tracking.py    tracking difference decomposition, premium statistics
etfarb/cli.py         command-line front end
tests/                78 unit tests
```

## What it is not

A pricing service. The tape is simulated, the fund is invented, and the frictions
are parameters you should replace with your own desk's. What is real is the
accounting: the identities, the sides of the spread, the cash leg, and the
reconciliation.

## License

MIT - see [LICENSE](LICENSE).
