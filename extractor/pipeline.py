#!/usr/bin/env python3
"""Download configurable Binance klines into separate folders per symbol."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from importlib import import_module
from pathlib import Path
import re
import sys

import yaml

PROJECT = Path(__file__).resolve().parents[1]
INTERVAL_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
    "12h": 720, "1d": 1440,
}


def _symbols(settings: dict) -> list[str]:
    """Accept base coins or complete USD-M pairs, without ambiguous aliases."""
    selectors = [key for key in ("coins", "coin", "symbol") if key in settings]
    if len(selectors) != 1:
        raise ValueError("Set exactly one of coins (list), coin, or symbol")
    selector = selectors[0]
    values = settings[selector] if selector == "coins" else [settings[selector]]
    if not isinstance(values, list) or not values:
        raise ValueError("coins must be a nonempty list, for example [BTC, ETH]")
    quote = settings.get("quote_asset", "USDT")
    if not isinstance(quote, str) or quote.strip().upper() not in ("USDT", "USDC"):
        raise ValueError("quote_asset must be USDT or USDC")
    quote = quote.strip().upper()
    result = []
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]+", value.strip()):
            raise ValueError("Each coin must be a ticker such as BTC or ETH, or a pair such as BTCUSDT")
        symbol = value.strip().upper()
        if symbol in ("USDT", "USDC"):
            raise ValueError("Specify a base coin or full pair, not only a quote asset")
        if not symbol.endswith(("USDT", "USDC")):
            symbol += quote
        if symbol in result:
            raise ValueError(f"Duplicate coin/pair after normalization: {symbol}")
        result.append(symbol)
    return result


def load_config(path: Path) -> dict:
    path = Path(path)
    settings = yaml.safe_load(path.read_text())
    if not isinstance(settings, dict):
        raise ValueError("Configuration must be a YAML mapping")
    if settings.get("market") != "um":
        raise ValueError("This pipeline supports market: um (USD-M futures)")
    settings["symbols"] = _symbols(settings)
    if not isinstance(settings.get("interval"), str) or settings["interval"] not in INTERVAL_MINUTES:
        raise ValueError("interval must be one of: " + ", ".join(INTERVAL_MINUTES))
    if settings.get("output_format") not in ("csv", "parquet"):
        raise ValueError("output_format must be csv or parquet")
    datasets = settings.get("datasets", ["klines"])
    if (not isinstance(datasets, list) or not datasets
            or any(not isinstance(item, str) for item in datasets)
            or len(datasets) != len(set(datasets))
            or "klines" not in datasets
            or any(item not in ("klines", "aggTrades") for item in datasets)):
        raise ValueError("datasets must be [klines] or [klines, aggTrades]")
    if "aggTrades" in datasets and (settings["interval"] != "1h" or settings["output_format"] != "csv"):
        raise ValueError("Combined aggTrades mode requires interval: 1h and output_format: csv; use datasets: [klines] for other intervals")
    settings["datasets"] = datasets
    for name in ("start_date", "end_date"):
        try:
            value = str(settings[name])
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("invalid date format")
            settings[name] = date.fromisoformat(value)
        except (KeyError, ValueError):
            raise ValueError(f"{name} must be a YYYY-MM-DD date") from None
    if settings["start_date"] > settings["end_date"]:
        raise ValueError("start_date must be on or before end_date")
    if settings["end_date"] >= datetime.now(timezone.utc).date():
        raise ValueError("end_date must be before today UTC; daily archives contain completed days")
    for name, default in (("download_workers", 4), ("download_retries", 3), ("trade_chunksize", 250000)):
        settings.setdefault(name, default)
        if type(settings[name]) is not int or settings[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if settings["download_workers"] > 4:
        raise ValueError("download_workers must be between 1 and 4")
    for name, default in (("raw_folder", "data/raw"), ("preprocessed_folder", "data/pre_processed")):
        value = settings.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty folder path")
        settings[name] = (path.resolve().parent / value).resolve()
    return settings


def run_pipeline(settings: dict, *, download_only: bool = False, process_only: bool = False) -> list[dict]:
    if download_only and process_only:
        raise ValueError("download_only and process_only are mutually exclusive")
    start, end = settings["start_date"], settings["end_date"]
    market, interval = settings["market"], settings["interval"]
    days = (end - start).days + 1
    combined = "aggTrades" in settings["datasets"]
    prefix = f"{__package__}." if __package__ else ""
    # Check only the dependencies this mode needs, before any network operation.
    try:
        if not download_only:
            if combined:
                process = import_module(prefix + "trade_processor").process_range
            else:
                process = import_module(prefix + "kline_processor").process_klines
                if settings["output_format"] == "parquet":
                    import_module("pyarrow.parquet")
        if not process_only:
            download_range = import_module(prefix + "downloader").download_range
    except ImportError as exc:
        raise RuntimeError(
            "A required Python dependency is missing. Run: "
            ".venv/bin/python -m pip install -r requirements.txt"
        ) from exc
    expected_rows = days * (1440 // INTERVAL_MINUTES[interval])
    results = []
    for symbol in settings["symbols"]:
        print(f"{market}/{symbol}/{interval}: {start} through {end} UTC, inclusive", flush=True)
        print(f"Expected: {days * len(settings['datasets']):,} archives -> {expected_rows:,} rows", flush=True)
        if not process_only:
            download_result = download_range(
                market=market, symbol=symbol, interval=interval, start=start, end=end,
                output=settings["raw_folder"], retries=settings["download_retries"],
                datasets=settings["datasets"], workers=settings["download_workers"],
            )
            if download_only:
                results.append(download_result)
                continue
        dataset = "combined" if combined else "klines"
        output = (settings["preprocessed_folder"] / market / symbol / interval
                  / f"{symbol}-{interval}-{start}-{end}-{dataset}.{settings['output_format']}")
        kwargs = dict(market=market, symbol=symbol, interval=interval, start=start, end=end,
                      raw_folder=settings["raw_folder"], output_path=output)
        if combined:
            summary = process(**kwargs, chunksize=settings["trade_chunksize"])
        else:
            summary = process(**kwargs)
        results.append(summary)
        print(f"Final file: {output}", flush=True)
        print(f"Validation: {summary['status']}; {summary['rows']:,} rows. "
              f"Report: {output.with_suffix('.validation.json')}", flush=True)
        if combined:
            print(f"{summary['mismatched_hours']:,} hours have source differences", flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT / "config.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download-only", action="store_true")
    mode.add_argument("--process-only", action="store_true", help="Rebuild from local archives without network requests")
    args = parser.parse_args()
    run_pipeline(load_config(args.config), download_only=args.download_only, process_only=args.process_only)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, yaml.YAMLError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
