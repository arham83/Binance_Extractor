# Binance Data Extractor

Download Binance USD-M futures candlesticks at the configured interval and write
one combined date-range file per coin. Each coin and interval has its own raw and
processed folders. The current configuration downloads **1-minute BTCUSDT and
ETHUSDT klines for July–August 2026**, in Parquet format.

## Setup

Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The files come from Binance's public HTTPS archive. No AWS credentials or
Requester Pays account is used.

## Configuration

Edit the root `config.yaml`:

```yaml
market: um
coins: [BTC, ETH]
quote_asset: USDT
interval: 1m
start_date: 2026-07-01
end_date: 2026-08-31
output_format: parquet

datasets: [klines]
download_workers: 4
download_retries: 3
raw_folder: data/raw
preprocessed_folder: data/pre_processed
```

- **Coins:** `coins: [BTC]` for one coin, or `coins: [BTC, ETH]` for several.
  Lowercase tickers work. BTC becomes BTCUSDT using `quote_asset: USDT`.
  Full pairs such as `BTCUSDT` and `ETHUSDC` are accepted without adding a suffix.
  `quote_asset` may be USDT or USDC and applies only to bare tickers. Use the
  exchange's actual ticker (for example, some instruments have a `1000` prefix).
- **Single-coin compatibility:** `coin: BTC` or the old `symbol: BTCUSDT` also works.
  Set exactly one of `coins`, `coin`, or `symbol`.
- **Interval:** `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`,
  `12h`, or `1d`. One minute means 1,440 candles per complete UTC day.
- **Dates:** inclusive UTC dates in `YYYY-MM-DD` format. The end date must be before
  today UTC. Binance daily files may take time to appear, and a contract's listing
  date can limit available history. Missing required files fail the run explicitly.
- **Output:** `parquet` for typed compact files, or `csv` for readable tables.
  CSV retains original numeric text; Parquet stores numerical measures as float64,
  trade counts as int64, and timestamps as timezone-aware UTC milliseconds.
- **Folders:** relative to the YAML file's folder, or absolute paths.
- **Market:** the main pipeline currently supports `um` (USD-M futures).
  Spot and coin-margined contracts are not interchangeable with this data.

## Run

```bash
./run_extractor.sh
# Equivalent:
.venv/bin/python -u core/pipeline.py --config config.yaml
```

Other configurations and individual stages:

```bash
./run_extractor.sh path/to/config.yaml
./run_extractor.sh --download-only
./run_extractor.sh --process-only
# Flags also work after a custom configuration path:
./run_extractor.sh path/to/config.yaml --process-only
```

Downloads verify Binance SHA-256 checksum files. Existing archives are reused
only after checking them against the published checksum. The processor reads only
the selected dates and holds one day's candles in memory at a time. Interrupted
or malformed runs do not publish an incomplete output file. Rerunning reuses
verified downloads and rebuilds the selected processed files.

## Output folders

```text
data/
├── raw/um/
│   ├── BTCUSDT/1m/BTCUSDT-1m-YYYY-MM-DD.zip
│   └── ETHUSDT/1m/ETHUSDT-1m-YYYY-MM-DD.zip
└── pre_processed/um/
    ├── BTCUSDT/1m/
    │   ├── BTCUSDT-1m-2026-07-01-2026-08-31-klines.parquet
    │   └── BTCUSDT-1m-2026-07-01-2026-08-31-klines.validation.json
    └── ETHUSDT/1m/
        ├── ETHUSDT-1m-2026-07-01-2026-08-31-klines.parquet
        └── ETHUSDT-1m-2026-07-01-2026-08-31-klines.validation.json
```

Each raw symbol folder also contains download manifests scoped by date range,
interval and datasets. Each output's validation report records coverage, hashes,
schema and source archives. The 62-day example produces **89,280 rows per coin**.

Each kline output has 16 columns: market, symbol, interval, full UTC open/close
timestamps, `feature_available_at`, OHLC, base/quote volume, underlying trade
count, taker-buy base/quote volume, and Binance's unused `ignore` value. Missing
minutes, duplicates, malformed values and inconsistent OHLC cause validation
failures; missing minutes are not silently filled.

## Optional hourly trade summaries

The earlier combined workflow remains available by explicitly setting:

```yaml
interval: 1h
output_format: csv
datasets: [klines, aggTrades]
trade_chunksize: 250000
```

This produces one hourly row with candle values plus aggregate-record count,
VWAP, taker-buy/sell volume, imbalance and source-reconciliation flags. It uses
`core/trade_processor.py`. Kline-only processing uses `core/kline_processor.py`.
Combined trade processing at other intervals is not implemented; `1m` mode should
use `datasets: [klines]`.

Full-candle values are only available after the candle ends.
`feature_available_at` is the nominal next interval boundary, not a measured
exchange publication or receipt time. Use completed candles for subsequent ML
decisions. Public market data does not contain account portfolios or trade-quality
labels. Aggregated trade groups near boundaries may not exactly reproduce the
published candle; the combined workflow preserves and flags these differences.

The old `core/preprocesser.py` and `core/merger.py` are retained for legacy manual
workflows; `run_extractor.sh` now calls `core/pipeline.py`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Official schemas, archive layout and checksums:
[Binance Public Data](https://github.com/binance/binance-public-data).
