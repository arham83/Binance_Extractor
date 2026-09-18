# Formula audit: all 128 market features

Audited 18 September 2026 against [specification version 1.0](feature_specification_128.md)
and the [ordered manifest](../feature_eng/feature_manifest_128.json). All 128
definitions received independent numerical checks. At the time of this audit,
the complete suite passed **69 tests**, including formula comparisons, timing,
gaps, configuration and Parquet integration. Numerical precision defects found
during this audit were corrected. The temporary test files have since been
removed; this document preserves the audit results and is not a statement that
the test suite is still available.

## Coverage

| Feature group | Columns checked | Independent checks |
| --- | ---: | --- |
| Price and candle shape | 12 | Scalar log returns, signed body, range and wick identities |
| Trend | 20 | Explicit seeded EMA recurrence, scalar SMA, OLS formulas and Decimal regression references |
| Momentum | 24 | Wilder RSI, fast stochastic K/D, mean absolute deviation CCI, independently seeded MACD and signal |
| Volatility | 20 | Population return deviation, true range and Wilder ATR, Bollinger bands, downside second moment |
| Volume and activity | 24 | Previous-window baselines, rolling signed-volume balances, directional money flows and log activity |
| Distribution and regime changes | 16 | Central population moments, 80-pair lagged correlations, adjacent non-overlapping return windows |
| Trade flow and calendar | 12 | Volume-weighted buy imbalance, signed-return/activity correlation, UTC day/week phases |
| **Total** | **128** | Registry coverage was asserted during the audit |

The reference implementations used direct trailing slices, scalar arithmetic,
explicit recurrences, analytical answers and Decimal calculations. They did not
call the engine's rolling-statistic or smoothing helpers to obtain expected
values. Checks compared every output row, including missing-value masks.

The reference suites covered price and activity formulas. Additional checks for
price precision, activity edge cases and the feature contract covered cases
omitted by the initial validation.

## Corrections made

**Rolling variance retained old numerical error.** An incremental add/remove
variance calculation could remain inaccurate after a large observation had left
the window. Testing only perfectly flat prices did not expose the problem:
nonconstant, low-volatility windows could also be affected. The shared standard
deviation helper now calculates the population variance from each actual window
in bounded batches. Subtracting a window-local origin before centering improves
precision when the absolute level is much larger than the fluctuations.

This corrects return volatility, Bollinger features, volume z-scores and the
variance terms in regime-change features. In a reproducible 80-candle example,
Bollinger width was `2.514917679e-8`; the corrected value is
`4.263539389e-10`, agreeing with the Decimal reference. An expired volume spike
also no longer changes the z-score of a later, unrelated baseline window.

**Regression and CCI lost precision on nearly constant prices.** Their window
centering now also removes a local origin before computing means and deviations.
OLS R² and CCI match Decimal references without changing their mathematical
definitions, seed rules or missing-value conventions.

**The calendar test reference assumed nanosecond storage.** The implementation
already handled UTC timestamps correctly. The corrected reference divided
timedeltas by their duration instead of assuming the unit of their integer
representation. The audit verified that equivalent timestamps stored as
seconds, milliseconds, microseconds and nanoseconds produced the same calendar
features.

The specification now explicitly states that missing/nonfinite required OHLCV
cells are rejected. Timestamp gaps restart indicator history with `gap_policy:
reset`; malformed cells are not silently removed or filled.

## Warm-up, missingness and causal timing

Every column's first valid position was derived independently from its formula.
The first fully populated row requires 161 nondegenerate candles. The audit
verified that every warm-up restarted after a timestamp gap, and that a missing
optional input affected only its dependent features and windows.

Additional checks covered constant prices, zero volume, a sudden volatility drop,
expired spikes, increasing/decreasing prices, a single price jump, short input
prefixes, timezone conversion, and midnight/week rollover. Prefix comparisons
and changes to future candles verified that future observations did not change
previous feature rows. Features use completed candles; their timestamp is the
exclusive end boundary.

## Actual candle validation and regenerated output

All 128 outputs were also compared with independent references on six 400-candle
samples: BTCUSDT and ETHUSDT on 2021-01-01, 2023-11-01 and 2026-08-31. These samples
covered the beginning, middle and end of both local source histories. Comparisons
used `rtol=2e-8`, `atol=2e-9`, with identical missing-value masks; targeted precision
checks used their own tighter or scale-appropriate tolerances. Each sample
started its own history for these formula comparisons.

The complete corrected dataset was generated separately at
`data/features/market_128_audited/features.parquet`, with 5,958,720 rows across
BTCUSDT and ETHUSDT. The earlier `data/features/market_128` result predates the
precision fixes. The audited output's `validation.json` records its row counts,
source checksums, sampled exported values and code hashes.

The subsequent output-layout update uses the same audited formulas and writes
separate `BTCUSDT-features.parquet` and `ETHUSDT-features.parquet` files under
`data/features/market_128_by_symbol/`. Run it with `./run_feature_eng.sh`; the
entry point now lives in `feature_eng/main.py`.

The current feature files contain `candle_end_utc`, `symbol` and the 128 numerical
features. Source metadata is kept in the inputs and run summary rather than
copied into feature columns. This layout change does not change the formulas.

Correctness here means agreement with this repository's explicit definitions
within floating-point tolerances. Some neutral values and EMA/MACD initialization
rules intentionally differ from other indicator libraries; these differences are
documented in the specification. The audit does not establish predictive value
for a trained risk model.
