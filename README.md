# Binance candles and market features

Download Binance USD-M futures candles, then calculate 128 market features from
the resulting Parquet files. Each coin and interval has its own candle folder;
feature generation writes a separate Parquet file for each symbol.

## Setup and run

Use Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 1. Download and process candles using config/config_extractor.yaml.
./run_extractor.sh

# 2. Read the local Parquet candles using config/config_feature_eng.yaml.
./run_feature_eng.sh
```

If the candle Parquet files already exist, run only the second command.
Feature engineering runs locally and does not download data. The extractor uses
Binance's public HTTPS archive and needs no AWS credentials.

## Extractor settings

Edit `config/config_extractor.yaml`. The current settings select one-minute BTC
and ETH candles from January 2021 through August 2026:

```yaml
market: um
coins: [BTC, ETH]
quote_asset: USDT
interval: 1m
start_date: 2021-01-01
end_date: 2026-08-31
output_format: parquet
datasets: [klines]
download_workers: 4
download_retries: 3
raw_folder: ../data/raw
preprocessed_folder: ../data/pre_processed
```

Paths are relative to the YAML file, so `../data` here points to the repository's
`data` folder. The older root `config.yaml` remains usable when explicitly passed
to the extractor; it is not the default configuration.

- **Coins:** base tickers such as `BTC` or full pairs such as `BTCUSDT` work.
  Lowercase names are accepted. `quote_asset` may be USDT or USDC and is appended
  only to bare tickers. The alternatives `coin: BTC` and `symbol: BTCUSDT` also
  work; set exactly one of `coins`, `coin`, or `symbol`.
- **Intervals:** `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`,
  `12h`, or `1d`. A complete UTC day contains 1,440 one-minute candles.
- **Dates:** inclusive UTC dates. The end date must be before today UTC. Missing
  archive files fail the run explicitly; available history depends on the
  instrument's listing and archive coverage.
- **Output:** use `parquet` for feature engineering. The extractor also supports
  `csv`, but the feature dataset builder reads Parquet inputs.
- **Folders:** relative to the YAML file's folder, or absolute paths.
- **Market:** this extractor supports `um` (USD-M futures).

```bash
./run_extractor.sh path/to/config.yaml
./run_extractor.sh --download-only
./run_extractor.sh --process-only
# Equivalent direct entry point:
.venv/bin/python -u extractor/pipeline.py --config config/config_extractor.yaml
```

The downloader verifies SHA-256 checksums and reuses verified archives. The candle
processor validates dates, OHLC values, duplicates and missing candles, then
publishes its output only after successful processing. Each output has a
`.validation.json` sidecar containing coverage and source archive checksums.

## Feature engineering settings

Edit `config/config_feature_eng.yaml`:

```yaml
input: ../data/pre_processed
output: ../data/features/market_128_by_symbol
symbols: []
gap_policy: reset
```

- `input`: one candle Parquet file or a folder searched recursively. The builder
  reads the extractor's typed OHLCV columns directly. The original imported
  project's `*_raw` Parquet schema is also supported.
- `output`: a **new** directory outside the input folder. Choose a different
  output path for another run; existing results are never overwritten.
- `symbols`: `[]` processes all symbols; `[BTCUSDT]` selects only Bitcoin. Use the
  full pair names stored in the Parquet files.
- `gap_policy`: `reset` restarts indicator history after missing candles. `rows`
  carries history across gaps and is recorded as a calculation variant.

The interval comes from each input file's `interval` column. To process only a
particular coin and interval, point `input` to its folder, for example
`../data/pre_processed/um/BTCUSDT/1m`. Changing the extractor configuration does
not filter previously downloaded files: the feature builder processes all
Parquet candles found under its own input path. If that folder contains
overlapping exports of the same series, select a single export or a folder of
non-overlapping files; duplicate candles are rejected.

```bash
./run_feature_eng.sh
./run_feature_eng.sh path/to/feature_settings.yaml
# Equivalent direct or module entry points:
.venv/bin/python -u feature_eng/main.py --config config/config_feature_eng.yaml
.venv/bin/python -u -m feature_eng.main --config config/config_feature_eng.yaml
```

The launcher uses the repository's `.venv` and works from any working directory.
Its default config is `config/config_feature_eng.yaml`; a custom config path is
relative to your current working directory. The Python entry point is
`feature_eng/main.py`.

History continues across input files and calendar boundaries, independently for
each exchange, market, symbol, interval and price family. One entire series and
its feature matrix must fit in memory. The 128 float64 feature columns alone
need about 1 KiB per candle (roughly 2.84 GiB for 2.98 million candles), with
additional memory needed for calculation and source data. Series are processed
one at a time and written in batches.

## Output layout

```text
data/
├── raw/um/
│   ├── BTCUSDT/1m/           # Downloaded candle ZIP archives
│   └── ETHUSDT/1m/
├── pre_processed/um/
│   ├── BTCUSDT/1m/          # Candle Parquet + validation JSON
│   └── ETHUSDT/1m/
└── features/market_128_by_symbol/
    ├── BTCUSDT-features.parquet
    ├── ETHUSDT-features.parquet
    ├── feature_columns.json
    └── summary.json
```

Each feature Parquet contains **130 columns**: `candle_end_utc`, `symbol`, and
the 128 numerical features listed in `feature_columns.json`. Source metadata
such as `market`, `feature_available_at`, `input_file`, exchange, interval and
duplicate candle timestamps is omitted from the feature files. The reader
still validates the source fields before calculating features.

`summary.json` records status, input paths, `output_files`, series identities,
row counts, gaps, warm-up and missing values. Each Parquet contains only its
named symbol, in chronological order. Select one market, interval and price
family per symbol for each run; ambiguous inputs are rejected because those
identifiers are no longer included in the feature rows. Use a result only when
its summary says `"status": "complete"`.

Features cover returns, candle shape, trends, volatility, volume, trade count,
taker-buy imbalance and UTC calendar cycles. See the
[feature guide](doc/FEATURES_EXPLAINED.md),
[exact formulas](doc/feature_specification_128.md), and
[ordered manifest](feature_eng/feature_manifest_128.json).

The first 160 candles normally lack at least one feature during warm-up.
Gaps, zero volume or constant prices may create additional undefined values.
These remain null; the pipeline does not backfill, impute, scale the full dataset
or generate prediction labels. Features use the completed candle and earlier
observations only. `candle_end_utc` marks the nominal completion
boundary, not measured exchange publication or arrival time.

Read selected columns without loading the entire wide dataset:

```python
import json
from pathlib import Path
import pandas as pd

folder = Path("data/features/market_128_by_symbol")
feature_names = json.loads((folder / "feature_columns.json").read_text())
btc = pd.read_parquet(
    folder / "BTCUSDT-features.parquet",
    columns=["candle_end_utc", "symbol", "log_return_1", "log1p_trade_count"],
)
```

These are market features for the risk model. Account positions, portfolio
balances, candidate trades and trade-outcome labels must be supplied separately.

## Optional hourly aggregated trades

The extractor's earlier combined CSV workflow is still available with:

```yaml
interval: 1h
output_format: csv
datasets: [klines, aggTrades]
trade_chunksize: 250000
```

This uses `extractor/trade_processor.py` to combine candle values with VWAP,
trade-flow summaries and source-reconciliation flags. It is separate from the
Parquet feature workflow. One-minute feature generation uses `datasets: [klines]`;
those candles already include trade count and taker-buy volume.

## Formula audit history

The temporary test files have been removed. The
[formula audit](doc/FORMULA_AUDIT.md) preserves the earlier results for all 128
formula checks, numerical corrections, and validation against actual BTC and
ETH candle samples. Those results describe the recorded audit, not a currently
available test suite. Input validation and per-run summaries remain part of the
pipeline.
The separate files in `data/features/market_128_by_symbol/` use the audited
formulas. The previous `market_128_audited` output contains the same formula
version in a combined file; the original `market_128` output predates the
numerical corrections.

Binance's archive schema is documented in
[Binance Public Data](https://github.com/binance/binance-public-data).
