# Binance Data Extractor

This project downloads Binance data, preprocesses each daily file,
and merges the results into one file per year.

## Requirements

- macOS or Linux
- Bash
- Python 3.12 or newer

## Setup

From the project directory, create a virtual environment and install the Python
dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Make the runner executable if necessary:

```bash
chmod +x run_extractor.sh
```

## Configuration

Edit `config.yaml` before running the pipeline:

```yaml
market: um
symbol: BTCUSDT
interval: 5m
start_date: 2021-09-01
end_date: 2026-08-31
output_format: parquet

raw_folder: data/raw
preprocessed_folder: data/pre_processed
```

Supported output formats are `parquet`, `csv`, and `xlsx`. Dates must use the
`YYYY-MM-DD` format and are inclusive.

Common Binance futures markets:

- `um`: USD-Margined futures, such as `BTCUSDT`
- `cm`: Coin-Margined futures, such as `BTCUSD_PERP`

## Run the pipeline

Run all three stages with:

```bash
./run_extractor.sh
```

You can also provide another configuration file:

```bash
./run_extractor.sh path/to/config.yaml
```

The runner will:

1. Download the daily ZIP files.
2. Extract and preprocess each daily CSV.
3. Merge the daily CSVs into one file per year.

Existing downloads and daily preprocessed files are skipped, so interrupted runs
can be started again safely.

## Output structure

```text
data/
├── raw/
│   └── um/
│       └── BTCUSDT/
│           └── 5m/
│               └── BTCUSDT-5m-YYYY-MM-DD.zip
└── pre_processed/
    ├── BTCUSDT-5m-YYYY.parquet
    └── splitted/
        └── BTCUSDT-5m-YYYY-MM-DD.csv
```

The exact extension of each merged yearly file depends on `output_format`.

## Run individual stages

Download:

```bash
.venv/bin/python downloader.py \
  --market um \
  --symbol BTCUSDT \
  --interval 5m \
  --start 2021-09-01 \
  --end 2026-08-31 \
  --output data/raw
```

Preprocess:

```bash
.venv/bin/python preprocesser.py \
  --input data/raw/um/BTCUSDT/5m \
  --output data/pre_processed/splitted
```

Merge:

```bash
.venv/bin/python merger.py \
  --input data/pre_processed/splitted \
  --output data/pre_processed \
  --format parquet
```

To rebuild existing daily or yearly outputs, add `--overwrite` to the relevant
preprocessing or merge command.
