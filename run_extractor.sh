#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${1:-$SCRIPT_DIR/config.yaml}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Configuration file not found: $CONFIG_FILE" >&2
  exit 1
fi

yaml_value() {
  awk -F ': *' -v key="$1" '$1 == key { print $2; exit }' "$CONFIG_FILE"
}

MARKET="$(yaml_value market)"
SYMBOL="$(yaml_value symbol)"
INTERVAL="$(yaml_value interval)"
START_DATE="$(yaml_value start_date)"
END_DATE="$(yaml_value end_date)"
OUTPUT_FORMAT="$(yaml_value output_format)"
RAW_FOLDER="$(yaml_value raw_folder)"
PREPROCESSED_FOLDER="$(yaml_value preprocessed_folder)"

for setting in MARKET SYMBOL INTERVAL START_DATE END_DATE OUTPUT_FORMAT RAW_FOLDER PREPROCESSED_FOLDER; do
  if [[ -z "${!setting}" ]]; then
    echo "Missing required setting in $CONFIG_FILE: $setting" >&2
    exit 1
  fi
done

if [[ "$OUTPUT_FORMAT" != "parquet" && "$OUTPUT_FORMAT" != "csv" && "$OUTPUT_FORMAT" != "xlsx" ]]; then
  echo "output_format must be parquet, csv, or xlsx" >&2
  exit 1
fi

# Use the virtual environment that contains certifi, pandas, and pyarrow.
source "$SCRIPT_DIR/.venv/bin/activate"

# 1. Download daily futures kline ZIPs into data/raw.
python "$SCRIPT_DIR/downloader.py" \
  --market "$MARKET" \
  --symbol "$SYMBOL" \
  --interval "$INTERVAL" \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --output "$SCRIPT_DIR/$RAW_FOLDER"

# 2. Unzip and preprocess every daily CSV.
python "$SCRIPT_DIR/preprocesser.py" \
  --input "$SCRIPT_DIR/$RAW_FOLDER/$MARKET/$SYMBOL/$INTERVAL" \
  --output "$SCRIPT_DIR/$PREPROCESSED_FOLDER/splitted"

# 3. Store merged yearly files directly in data/pre_processed.
python "$SCRIPT_DIR/merger.py" \
  --input "$SCRIPT_DIR/$PREPROCESSED_FOLDER/splitted" \
  --output "$SCRIPT_DIR/$PREPROCESSED_FOLDER" \
  --format "$OUTPUT_FORMAT"

echo "Done. Your yearly $OUTPUT_FORMAT files are in $PREPROCESSED_FOLDER/"
