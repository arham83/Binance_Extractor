"""Validate daily Binance USD-M candles and publish one CSV/Parquet per range.

Archives are read directly; memory use is bounded to one UTC day. A candle's
``feature_available_at`` is its nominal close boundary, not publication latency.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


DAY_MS = 86_400_000
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": DAY_MS,
}
SOURCE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
OUTPUT_COLUMNS = [
    "market", "symbol", "interval", "open_time", "close_time", "feature_available_at",
    "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
    "taker_buy_base_volume", "taker_buy_quote_volume", "kline_ignore",
]
NUMERIC_SOURCES = {
    "open": "open", "high": "high", "low": "low", "close": "close",
    "volume": "volume", "quote_volume": "quote_volume",
    "taker_buy_base_volume": "taker_buy_volume",
    "taker_buy_quote_volume": "taker_buy_quote_volume", "kline_ignore": "ignore",
}
TIME_COLUMNS = ("open_time", "close_time", "feature_available_at")
HEADER_ALIASES = (
    {"opentime"}, {"open"}, {"high"}, {"low"}, {"close"}, {"volume"},
    {"closetime"}, {"quotevolume", "quoteassetvolume"},
    {"count", "tradecount", "numberoftrades"},
    {"takerbuyvolume", "takerbuybasevolume", "takerbuybaseassetvolume"},
    {"takerbuyquotevolume", "takerbuyquoteassetvolume"}, {"ignore"},
)


def _day_ms(day: date) -> int:
    return int(datetime.combine(day, time(), timezone.utc).timestamp()) * 1000


def _utc(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _integer(value: str, name: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{name}: expected a nonnegative integer, got {value!r}")
    parsed = int(value)
    if parsed > 2**63 - 1:
        raise ValueError(f"{name}: exceeds int64")
    return parsed


def _number(value: str, name: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(value)
        finite_float = math.isfinite(float(parsed))
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError(f"{name}: invalid number {value!r}") from exc
    if not parsed.is_finite() or not finite_float or parsed < 0 or (positive and parsed == 0):
        requirement = "positive" if positive else "nonnegative"
        raise ValueError(f"{name}: expected a finite {requirement} number")
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        raise ValueError(f"{name}: invalid number {value!r}")
    return parsed


def _read_day(path: Path, day: date, market: str, symbol: str, interval: str) -> list[dict]:
    """Read exactly the named daily member and reject incomplete/invalid days."""
    step = INTERVAL_MS[interval]
    beginning = _day_ms(day)
    expected_rows = DAY_MS // step
    records = []
    previous = None
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        expected_member = path.with_suffix(".csv").name
        if len(members) != 1 or members[0].filename != expected_member:
            raise ValueError(f"{path}: expected exactly one member named {expected_member}")
        with archive.open(members[0]) as zipped:
            reader = csv.reader(io.TextIOWrapper(zipped, encoding="utf-8-sig", newline=""))
            for row_number, values in enumerate(reader, start=1):
                values = [value.strip() for value in values]
                if row_number == 1 and values and not re.fullmatch(r"[0-9]+", values[0]):
                    normalized = [re.sub(r"[ _]", "", value.lower()) for value in values]
                    if len(values) != 12 or any(
                        name not in aliases for name, aliases in zip(normalized, HEADER_ALIASES)
                    ):
                        raise ValueError(f"{path}: unexpected kline header")
                    continue
                if len(values) != 12:
                    raise ValueError(f"{path}, row {row_number}: expected 12 kline columns")
                raw = dict(zip(SOURCE_COLUMNS, values))
                opened = _integer(raw["open_time"], "open_time")
                closed = _integer(raw["close_time"], "close_time")
                if not beginning <= opened < beginning + DAY_MS:
                    raise ValueError(f"{path}: timestamp outside its UTC date (expected milliseconds)")
                if opened % step or closed != opened + step - 1:
                    raise ValueError(f"{path}: invalid {interval} candle boundaries")
                if previous is not None and opened <= previous:
                    raise ValueError(f"{path}: duplicate or out-of-order candle at {_utc(opened)}")
                expected = beginning + len(records) * step
                if opened != expected:
                    raise ValueError(f"{path}: missing candle at {_utc(expected)}")
                parsed = {
                    column: _number(raw[column], column, positive=column in {"open", "high", "low", "close"})
                    for column in NUMERIC_SOURCES.values()
                }
                if not parsed["low"] <= min(parsed["open"], parsed["close"]) <= max(
                    parsed["open"], parsed["close"]
                ) <= parsed["high"]:
                    raise ValueError(f"{path}, row {row_number}: inconsistent candle OHLC")
                if parsed["taker_buy_volume"] > parsed["volume"] or (
                    parsed["taker_buy_quote_volume"] > parsed["quote_volume"]
                ):
                    raise ValueError(f"{path}, row {row_number}: taker buy volume exceeds total volume")
                records.append({
                    "market": market, "symbol": symbol, "interval": interval,
                    "open_time": opened, "close_time": closed, "feature_available_at": opened + step,
                    **{output: raw[source] for output, source in NUMERIC_SOURCES.items()},
                    "trade_count": _integer(raw["count"], "count"),
                })
                previous = opened
    if len(records) != expected_rows:
        raise ValueError(f"{path}: missing {expected_rows - len(records)} expected candles; "
                         f"found {len(records)}, expected {expected_rows}")
    return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _temporary_path(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    return Path(name)


def _publish(temporary: Path, output: Path, report_temporary: Path, report: Path) -> None:
    """Commit output last so a report-publication error cannot replace good data."""
    previous_report = report.read_bytes() if report.exists() else None
    os.replace(report_temporary, report)
    try:
        os.replace(temporary, output)
    except BaseException:
        if previous_report is None:
            report.unlink(missing_ok=True)
        else:
            report_temporary.write_bytes(previous_report)
            os.replace(report_temporary, report)
        raise


def _parquet_schema():
    import pyarrow as pa

    fields = []
    for column in OUTPUT_COLUMNS:
        if column in TIME_COLUMNS:
            kind = pa.timestamp("ms", tz="UTC")
        elif column == "trade_count":
            kind = pa.int64()
        elif column in NUMERIC_SOURCES:
            kind = pa.float64()
        else:
            kind = pa.string()
        fields.append(pa.field(column, kind, nullable=False))
    return pa.schema(fields)


def process_klines(market: str, symbol: str, interval: str, start: date, end: date,
                   raw_folder: Path, output_path: Path) -> dict:
    """Validate inclusive daily archives and atomically write CSV or Parquet.

    CSV retains source decimal text; Parquet uses float64 measurements, int64
    trade counts, and timezone-aware millisecond timestamps. Failure while
    reading or validating leaves an existing final output untouched.
    """
    if market != "um":
        raise ValueError("The kline processor currently supports market: um only")
    if not re.fullmatch(r"[A-Z0-9_]+", symbol):
        raise ValueError("symbol must be an uppercase Binance USD-M symbol")
    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported candle interval: {interval}; choose {', '.join(INTERVAL_MS)}")
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("start and end must be dates with start on or before end")
    output_path, raw_folder = Path(output_path), Path(raw_folder)
    kind = output_path.suffix.lower()
    if kind not in {".csv", ".parquet"}:
        raise ValueError("output_path must have a .csv or .parquet suffix")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_path.with_suffix(".validation.json")
    temporary = _temporary_path(output_path)
    report_temporary = None
    sources = []
    rows_written = 0
    sink = parquet_writer = None
    try:
        if kind == ".csv":
            sink = temporary.open("w", encoding="utf-8", newline="")
            writer = csv.DictWriter(sink, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
        else:
            import pyarrow as pa
            import pyarrow.parquet as pq

            schema = _parquet_schema()
            parquet_writer = pq.ParquetWriter(temporary, schema=schema, compression="zstd")
        day = start
        while day <= end:
            source = raw_folder / market / symbol / interval / f"{symbol}-{interval}-{day}.zip"
            records = _read_day(source, day, market, symbol, interval)
            if kind == ".csv":
                writer.writerows({**record, **{name: _utc(record[name]) for name in TIME_COLUMNS}}
                                 for record in records)
            else:
                columns = []
                for field in schema:
                    values = [record[field.name] for record in records]
                    if field.name in NUMERIC_SOURCES:
                        values = [float(value) for value in values]
                    if field.name in TIME_COLUMNS:
                        array = pa.array(values, type=pa.int64()).cast(field.type)
                    else:
                        array = pa.array(values, type=field.type)
                    columns.append(array)
                parquet_writer.write_table(pa.Table.from_arrays(columns, schema=schema))
            rows_written += len(records)
            sources.append({"date": str(day), "path": str(source), "rows": len(records),
                            "size_bytes": source.stat().st_size, "sha256": _sha256(source)})
            day += timedelta(days=1)
        if sink is not None:
            sink.close()
            sink = None
        if parquet_writer is not None:
            parquet_writer.close()
            parquet_writer = None
        units = {
            "open_time": "UTC candle open", "close_time": "UTC inclusive candle close",
            "feature_available_at": "UTC next candle boundary; nominal completion, not publication latency",
            **{name: "quote asset per base asset" for name in ("open", "high", "low", "close")},
            "volume": "base asset", "quote_volume": "quote asset", "trade_count": "underlying executions",
            "taker_buy_base_volume": "base asset", "taker_buy_quote_volume": "quote asset",
            "kline_ignore": "unused source field",
        }
        types = {}
        for column in OUTPUT_COLUMNS:
            if column in TIME_COLUMNS:
                dtype = "ISO 8601 UTC milliseconds" if kind == ".csv" else "timestamp[ms, UTC]"
            elif column == "trade_count":
                dtype = "int64"
            elif column in NUMERIC_SOURCES:
                dtype = "decimal text (source precision)" if kind == ".csv" else "float64"
            else:
                dtype = "string"
            types[column] = {"type": dtype, "unit": units.get(column, "identifier")}
        report = {
            "status": "passed", "market": market, "symbol": symbol, "interval": interval,
            "start_date": str(start), "end_date": str(end), "rows": rows_written,
            "expected_rows": ((end - start).days + 1) * DAY_MS // INTERVAL_MS[interval],
            "missing_candles": 0, "duplicate_candles": 0, "out_of_order_candles": 0,
            "first_open_time": _utc(_day_ms(start)),
            "last_open_time": _utc(_day_ms(end) + DAY_MS - INTERVAL_MS[interval]),
            "source_files": sources, "output_path": str(output_path),
            "output_format": kind.lstrip("."), "output_sha256": _sha256(temporary),
            "schema": types,
        }
        report_temporary = _temporary_path(report_path)
        report_temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        _publish(temporary, output_path, report_temporary, report_path)
        return report
    finally:
        if sink is not None:
            sink.close()
        if parquet_writer is not None:
            parquet_writer.close()
        temporary.unlink(missing_ok=True)
        if report_temporary is not None:
            report_temporary.unlink(missing_ok=True)
