#!/usr/bin/env python3
"""Join USD-M daily klines and streaming aggTrades into complete UTC hours.

Each row describes a *completed* hour. ``feature_available_at`` is its nominal
completion time, not a guarantee of exchange publication latency. These rows
must not be used as features for decisions made within the same hour.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
REL_TOL = 1e-8
ABS_TOL = 1e-6
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
AGG_COLUMNS = [
    "agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]
OUTPUT_COLUMNS = [
    "market", "symbol", "interval", "open_time", "close_time", "feature_available_at",
    "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
    "taker_buy_base_volume", "taker_buy_quote_volume", "kline_ignore",
    "agg_trade_count", "agg_base_volume", "agg_quote_volume",
    "agg_taker_buy_base_volume", "agg_taker_sell_base_volume",
    "agg_taker_buy_quote_volume", "agg_taker_sell_quote_volume", "agg_vwap",
    "agg_buy_volume_fraction", "agg_volume_imbalance",
    "agg_open", "agg_high", "agg_low", "agg_close",
    "base_volume_difference", "quote_volume_difference", "base_volume_matches",
    "quote_volume_matches", "taker_buy_base_volume_matches",
    "taker_buy_quote_volume_matches", "ohlc_matches", "all_metrics_match",
]
MATCH_COLUMNS = [
    "base_volume_matches", "quote_volume_matches", "taker_buy_base_volume_matches",
    "taker_buy_quote_volume_matches", "ohlc_matches",
]


def _day_ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp()) * 1000


def _utc(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _csv_member(archive: zipfile.ZipFile, path: Path) -> str:
    members = [item.filename for item in archive.infolist()
               if not item.is_dir() and item.filename.lower().endswith(".csv")]
    if len(members) != 1:
        raise ValueError(f"Expected exactly one CSV in {path}, found {len(members)}")
    return members[0]


def _integer(value: str, label: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{label}: expected a nonnegative integer, found {value!r}")
    number = int(value)
    if number > np.iinfo(np.int64).max:
        raise ValueError(f"{label}: integer exceeds int64")
    return number


def _number(value: str, label: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid number {value!r}") from exc
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(f"{label}: expected a finite {'positive' if positive else 'nonnegative'} number")
    return number


def _klines(path: Path, day: date) -> dict[int, dict]:
    beginning = _day_ms(day)
    candles: dict[int, dict] = {}
    with zipfile.ZipFile(path) as archive:
        with archive.open(_csv_member(archive, path)) as zipped:
            reader = csv.reader(io.TextIOWrapper(zipped, encoding="utf-8-sig", newline=""))
            for row_number, fields in enumerate(reader, start=1):
                if row_number == 1 and fields and fields[0].strip().lower() == "open_time":
                    if [cell.strip().lower() for cell in fields] != KLINE_COLUMNS:
                        raise ValueError(f"Unexpected kline header in {path}")
                    continue
                if len(fields) != len(KLINE_COLUMNS):
                    raise ValueError(f"{path}, row {row_number}: expected 12 kline columns")
                raw = dict(zip(KLINE_COLUMNS, fields))
                opened = _integer(raw["open_time"], "open_time")
                closed = _integer(raw["close_time"], "close_time")
                if not beginning <= opened < beginning + DAY_MS:
                    raise ValueError(f"{path}: kline timestamp outside its UTC date (expected milliseconds)")
                if opened % HOUR_MS or closed != opened + HOUR_MS - 1:
                    raise ValueError(f"{path}: invalid 1h candle boundaries")
                if opened in candles:
                    raise ValueError(f"{path}: duplicate candle at {_utc(opened)}")
                parsed = {
                    field: _number(raw[field], field, positive=field in {"open", "high", "low", "close"})
                    for field in ("open", "high", "low", "close", "volume", "quote_volume",
                                  "taker_buy_volume", "taker_buy_quote_volume", "ignore")
                }
                if not parsed["low"] <= min(parsed["open"], parsed["close"]) <= max(
                    parsed["open"], parsed["close"]
                ) <= parsed["high"]:
                    raise ValueError(f"{path}: inconsistent candle OHLC")
                if parsed["taker_buy_volume"] > parsed["volume"] + ABS_TOL or (
                    parsed["taker_buy_quote_volume"] > parsed["quote_volume"] + ABS_TOL
                ):
                    raise ValueError(f"{path}: taker buy volume exceeds total volume")
                candles[opened] = {
                    "open_time_ms": opened, "close_time_ms": closed, **parsed,
                    "count": _integer(raw["count"], "count"),
                }
    expected = {beginning + index * HOUR_MS for index in range(24)}
    missing = sorted(expected - candles.keys())
    if missing:
        raise ValueError(f"{path}: missing {len(missing)} expected hourly candles; first {_utc(missing[0])}")
    return candles


def _int_series(series: pd.Series, label: str) -> np.ndarray:
    if not series.str.fullmatch(r"[0-9]+").all():
        raise ValueError(f"{label}: expected nonnegative integer values")
    try:
        parsed = pd.to_numeric(series, errors="raise")
        if (parsed > np.iinfo(np.int64).max).any():
            raise ValueError("integer exceeds int64")
        return parsed.to_numpy(dtype=np.int64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}: invalid int64 values") from exc


def _float_series(series: pd.Series, label: str, *, positive: bool = False) -> np.ndarray:
    try:
        values = pd.to_numeric(series, errors="raise").to_numpy(dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid numeric values") from exc
    if not np.isfinite(values).all() or (values < 0).any() or (positive and (values == 0).any()):
        raise ValueError(f"{label}: expected finite {'positive' if positive else 'nonnegative'} values")
    return values


def _empty_bucket() -> dict:
    return {
        "agg_trade_count": 0, "agg_base_volume": 0.0, "agg_quote_volume": 0.0,
        "agg_taker_buy_base_volume": 0.0, "agg_taker_sell_base_volume": 0.0,
        "agg_taker_buy_quote_volume": 0.0, "agg_taker_sell_quote_volume": 0.0,
        "agg_open": None, "agg_high": None, "agg_low": None, "agg_close": None,
    }


def _aggregate_day(path: Path, day: date, chunksize: int,
                   previous_id: int | None, previous_time: int | None) -> tuple[dict, int | None, int | None]:
    beginning = _day_ms(day)
    buckets = {beginning + index * HOUR_MS: _empty_bucket() for index in range(24)}
    with zipfile.ZipFile(path) as archive:
        with archive.open(_csv_member(archive, path)) as zipped:
            source = io.TextIOWrapper(zipped, encoding="utf-8-sig", newline="")
            first = next(csv.reader(source), None)
            if first is None:
                raise ValueError(f"Empty aggTrades CSV: {path}")
            if len(first) != len(AGG_COLUMNS):
                raise ValueError(f"{path}: expected 7 USD-M aggTrades columns")
            if first[0].strip().lower() == "agg_trade_id":
                if [cell.strip().lower() for cell in first] != AGG_COLUMNS:
                    raise ValueError(f"Unexpected aggTrades header in {path}")
            else:
                source.seek(0)
            try:
                chunks = pd.read_csv(source, header=None, dtype=str, keep_default_na=False,
                                     chunksize=chunksize, skip_blank_lines=False)
            except pd.errors.EmptyDataError:
                # A header-only archive can represent a day without executions.
                return buckets, previous_id, previous_time
            with chunks:
                for chunk in chunks:
                    if chunk.shape[1] != len(AGG_COLUMNS):
                        raise ValueError(f"{path}: expected 7 aggTrades columns")
                    chunk.columns = AGG_COLUMNS
                    ids = _int_series(chunk["agg_trade_id"], "agg_trade_id")
                    stamps = _int_series(chunk["transact_time"], "transact_time")
                    first_ids = _int_series(chunk["first_trade_id"], "first_trade_id")
                    last_ids = _int_series(chunk["last_trade_id"], "last_trade_id")
                    if not len(ids):
                        continue
                    if (ids[1:] <= ids[:-1]).any() or (previous_id is not None and ids[0] <= previous_id):
                        raise ValueError(f"{path}: duplicate or non-increasing agg_trade_id")
                    if (stamps[1:] < stamps[:-1]).any() or (
                        previous_time is not None and stamps[0] < previous_time
                    ):
                        raise ValueError(f"{path}: aggTrades timestamps are not monotonic")
                    if (stamps < beginning).any() or (stamps >= beginning + DAY_MS).any():
                        raise ValueError(f"{path}: aggTrades timestamp outside its UTC date (expected milliseconds)")
                    if (first_ids > last_ids).any():
                        raise ValueError(f"{path}: first_trade_id exceeds last_trade_id")
                    prices = _float_series(chunk["price"], "price", positive=True)
                    quantities = _float_series(chunk["quantity"], "quantity")
                    maker_text = chunk["is_buyer_maker"].str.lower()
                    if not maker_text.isin(["true", "false"]).all():
                        raise ValueError(f"{path}: malformed is_buyer_maker; expected true/false")
                    # Buyer is the maker => the aggressive/taker side is SELL.
                    taker_buy = maker_text.to_numpy() == "false"
                    quote = prices * quantities
                    if not np.isfinite(quote).all():
                        raise ValueError(f"{path}: nonfinite price * quantity")
                    frame = pd.DataFrame({
                        "hour": stamps // HOUR_MS * HOUR_MS, "price": prices,
                        "quantity": quantities, "quote": quote,
                        "buy_quantity": np.where(taker_buy, quantities, 0.0),
                        "sell_quantity": np.where(taker_buy, 0.0, quantities),
                        "buy_quote": np.where(taker_buy, quote, 0.0),
                        "sell_quote": np.where(taker_buy, 0.0, quote),
                    })
                    grouped = frame.groupby("hour", sort=False).agg(
                        agg_trade_count=("price", "size"), agg_base_volume=("quantity", "sum"),
                        agg_quote_volume=("quote", "sum"),
                        agg_taker_buy_base_volume=("buy_quantity", "sum"),
                        agg_taker_sell_base_volume=("sell_quantity", "sum"),
                        agg_taker_buy_quote_volume=("buy_quote", "sum"),
                        agg_taker_sell_quote_volume=("sell_quote", "sum"),
                        agg_open=("price", "first"), agg_high=("price", "max"),
                        agg_low=("price", "min"), agg_close=("price", "last"),
                    )
                    for hour, row in grouped.iterrows():
                        bucket = buckets[int(hour)]
                        if bucket["agg_open"] is None:
                            bucket["agg_open"] = float(row["agg_open"])
                        bucket["agg_high"] = max(bucket["agg_high"] or float("-inf"), float(row["agg_high"]))
                        bucket["agg_low"] = min(bucket["agg_low"] or float("inf"), float(row["agg_low"]))
                        bucket["agg_close"] = float(row["agg_close"])
                        bucket["agg_trade_count"] += int(row["agg_trade_count"])
                        for field in ("agg_base_volume", "agg_quote_volume", "agg_taker_buy_base_volume",
                                      "agg_taker_sell_base_volume", "agg_taker_buy_quote_volume",
                                      "agg_taker_sell_quote_volume"):
                            bucket[field] += float(row[field])
                            if not math.isfinite(bucket[field]):
                                raise ValueError(f"{path}: nonfinite accumulated {field}")
                    previous_id, previous_time = int(ids[-1]), int(stamps[-1])
    return buckets, previous_id, previous_time


def _matches(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _output_row(market: str, symbol: str, interval: str, candle: dict, bucket: dict) -> dict:
    volume = bucket["agg_base_volume"]
    buy_volume = bucket["agg_taker_buy_base_volume"]
    row = {
        "market": market, "symbol": symbol, "interval": interval,
        "open_time": _utc(candle["open_time_ms"]), "close_time": _utc(candle["close_time_ms"]),
        "feature_available_at": _utc(candle["open_time_ms"] + HOUR_MS),
        **{key: candle[key] for key in ("open", "high", "low", "close", "volume", "quote_volume")},
        "trade_count": candle["count"], "taker_buy_base_volume": candle["taker_buy_volume"],
        "taker_buy_quote_volume": candle["taker_buy_quote_volume"], "kline_ignore": candle["ignore"],
        **bucket,
        "agg_vwap": bucket["agg_quote_volume"] / volume if volume else None,
        "agg_buy_volume_fraction": buy_volume / volume if volume else None,
        "agg_volume_imbalance": (buy_volume - bucket["agg_taker_sell_base_volume"]) / volume if volume else None,
        "base_volume_difference": volume - candle["volume"],
        "quote_volume_difference": bucket["agg_quote_volume"] - candle["quote_volume"],
        "base_volume_matches": _matches(volume, candle["volume"]),
        "quote_volume_matches": _matches(bucket["agg_quote_volume"], candle["quote_volume"]),
        "taker_buy_base_volume_matches": _matches(buy_volume, candle["taker_buy_volume"]),
        "taker_buy_quote_volume_matches": _matches(bucket["agg_taker_buy_quote_volume"], candle["taker_buy_quote_volume"]),
        "ohlc_matches": all(_matches(bucket[f"agg_{key}"], candle[key]) for key in ("open", "high", "low", "close"))
            if bucket["agg_trade_count"] else candle["count"] == 0 and candle["volume"] == 0,
    }
    row["all_metrics_match"] = all(row[field] for field in MATCH_COLUMNS)
    return row


def process_range(market: str, symbol: str, interval: str, start: date, end: date,
                  raw_folder: Path, output_path: Path, chunksize: int = 250000) -> dict:
    """Process inclusive daily ZIP pairs, return/save a reconciliation summary.

    Supports USD-M only, where aggTrades quantities are in base-asset units.
    Structural errors raise before replacing the final CSV. Numerical differences
    are retained and flagged; a successful run may have ``status='mismatches'``.
    The adjacent report is ``output_path.with_suffix('.validation.json')``.
    """
    if market != "um" or interval != "1h":
        raise ValueError("Hourly trade processing currently requires market='um' and interval='1h'")
    if not re.fullmatch(r"[A-Z0-9_]+", symbol):
        raise ValueError("symbol must use uppercase letters, digits or underscore")
    if end < start:
        raise ValueError("end must be on or after start")
    if not isinstance(chunksize, int) or isinstance(chunksize, bool) or chunksize <= 0:
        raise ValueError("chunksize must be a positive integer")
    raw_folder, output_path = Path(raw_folder), Path(output_path)
    days = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    inputs = []
    for day in days:
        kline = raw_folder / market / symbol / interval / f"{symbol}-{interval}-{day.isoformat()}.zip"
        trades = raw_folder / market / symbol / "aggTrades" / f"{symbol}-aggTrades-{day.isoformat()}.zip"
        for path in (kline, trades):
            if not path.is_file():
                raise FileNotFoundError(f"Missing required daily archive: {path}")
        inputs.append((day, kline, trades))
    summary = {
        "status": "passed", "market": market, "symbol": symbol, "interval": interval,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "UTC",
        "rows": 0, "expected_rows": len(days) * 24, "agg_trade_records": 0,
        "source_files": len(inputs) * 2, "mismatched_hours": 0, "hours_without_agg_trades": 0,
        "mismatch_counts": {field: 0 for field in MATCH_COLUMNS}, "mismatch_examples": [],
        "reconciliation_tolerance": {"relative": REL_TOL, "absolute": ABS_TOL},
        "output_csv": str(output_path.resolve()),
        "notes": [
            "agg_trade_count counts aggregated records, not underlying executions or traders.",
            "All feature values describe completed hours; feature_available_at is the nominal next-hour boundary, not verified publication time.",
            "No portfolio state, wallet identities, trade quality labels or margin state are supplied.",
            "Structural coverage and ordering are checked; reconciliation differences are flagged, never overwritten.",
            "Kline and aggTrades volumes use base-asset units; quote volumes use price times base quantity (USD-M only).",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    temporary = Path(temporary_name)
    report_temporary: Path | None = None
    previous_id = previous_time = None
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for index, (day, kline_path, trades_path) in enumerate(inputs, start=1):
                candles = _klines(kline_path, day)
                buckets, previous_id, previous_time = _aggregate_day(
                    trades_path, day, chunksize, previous_id, previous_time
                )
                for hour in sorted(candles):
                    row = _output_row(market, symbol, interval, candles[hour], buckets[hour])
                    writer.writerow(row)
                    summary["rows"] += 1
                    summary["agg_trade_records"] += row["agg_trade_count"]
                    summary["hours_without_agg_trades"] += int(row["agg_trade_count"] == 0)
                    if not row["all_metrics_match"]:
                        summary["mismatched_hours"] += 1
                        failed = [field for field in MATCH_COLUMNS if not row[field]]
                        for field in failed:
                            summary["mismatch_counts"][field] += 1
                        if len(summary["mismatch_examples"]) < 20:
                            summary["mismatch_examples"].append({"open_time": row["open_time"], "failed": failed,
                                "base_volume_difference": row["base_volume_difference"],
                                "quote_volume_difference": row["quote_volume_difference"]})
                print(f"[{index}/{len(inputs)}] Processed {day}: 24 hours, "
                      f"{sum(bucket['agg_trade_count'] for bucket in buckets.values()):,} aggTrades", flush=True)
            destination.flush()
            os.fsync(destination.fileno())
        summary["status"] = "mismatches" if summary["mismatched_hours"] else "passed"
        summary["last_agg_trade_id"] = previous_id
        summary["last_agg_trade_time"] = _utc(previous_time) if previous_time is not None else None
        report_path = output_path.with_suffix(".validation.json")
        report_descriptor, report_name = tempfile.mkstemp(prefix=f".{report_path.name}.", suffix=".tmp", dir=output_path.parent)
        report_temporary = Path(report_name)
        with os.fdopen(report_descriptor, "w", encoding="utf-8") as report:
            json.dump(summary, report, indent=2, allow_nan=False)
            report.write("\n")
            report.flush()
            os.fsync(report.fileno())
        os.replace(temporary, output_path)
        os.replace(report_temporary, report_path)
    finally:
        temporary.unlink(missing_ok=True)
        if report_temporary is not None:
            report_temporary.unlink(missing_ok=True)
    return summary
