#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config/config_feature_eng.yaml"
if [[ $# -gt 0 && "$1" != --* ]]; then
  CONFIG_FILE="$1"
  shift
fi

if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  echo "Create the environment first: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

exec "$SCRIPT_DIR/.venv/bin/python" -u "$SCRIPT_DIR/feature_eng/main.py" --config "$CONFIG_FILE" "$@"
