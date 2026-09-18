"""Calculate the agreed 128-feature schema from one instrument's candle file.

No input file is overwritten. Missing optional trade-flow data stay unavailable.
The public entry point is feature_eng/main.py; this module supplies shared calculations.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .feature_engine import compute_features


ALIASES = {
    "open": ("open",), "high": ("high",), "low": ("low",),
    "close": ("close",), "volume": ("volume", "base_volume", "base_asset_volume"),
    "trade_count": ("trade_count", "number_of_trades", "trades"),
    "taker_buy_base_volume": (
        "taker_buy_base_volume", "taker_buy_base_asset_volume",
    ),
}
TIMESTAMP_NAMES = ("timestamp", "datetime", "date", "time", "open_time", "close_time")
REQUIRED = ("open", "high", "low", "close", "volume")


def _name(value: object) -> str:
    return re.sub(r"[\s-]+", "_", str(value).strip().lower())


def load_candles(
    path: Path,
    *,
    timestamp_column: str | None = None,
    timezone: str | None = None,
    timestamp_unit: str | None = None,
) -> pd.DataFrame:
    """Read common file formats without inferring unknown timezone/epoch units."""
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        raw = pd.read_parquet(path)
    elif suffix in (".xlsx", ".xls"):
        raw = pd.read_excel(path)
    elif suffix == ".tsv":
        raw = pd.read_csv(path, sep="\t")
    elif suffix in (".csv", ".gz"):
        raw = pd.read_csv(path)
    else:
        raise ValueError("Use a CSV, CSV.gz, TSV, Parquet, or Excel input file.")
    if raw.empty:
        raise ValueError("The input file has no candle rows.")
    normalized = [_name(c) for c in raw.columns]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Column names are ambiguous after normalizing their case/spaces.")
    raw.columns = normalized
    for label in ("symbol", "ticker", "asset"):
        if label in raw and raw[label].nunique(dropna=False) > 1:
            raise ValueError("This calculator takes one instrument at a time; split by instrument first.")

    if timestamp_column:
        timestamp_column = _name(timestamp_column)
        if timestamp_column not in raw:
            raise ValueError(f"Timestamp column {timestamp_column!r} is missing.")
    else:
        candidates = [c for c in TIMESTAMP_NAMES if c in raw]
        if len(candidates) != 1:
            raise ValueError("Specify timestamp_column; exactly one timestamp column is required.")
        timestamp_column = candidates[0]

    stamp = raw[timestamp_column]
    if stamp.isna().any():
        raise ValueError("Some timestamps are missing.")
    if timestamp_unit:
        stamps = pd.DatetimeIndex(pd.to_datetime(
            pd.to_numeric(stamp, errors="raise"), unit=timestamp_unit, utc=True, errors="raise"
        ))
    else:
        if pd.api.types.is_numeric_dtype(stamp):
            raise ValueError("Numeric timestamps require timestamp_unit='s', 'ms', 'us', or 'ns'.")
        parsed = pd.to_datetime(stamp, errors="raise", format="mixed")
        try:
            stamps = pd.DatetimeIndex(parsed)
        except (TypeError, ValueError) as exc:
            raise ValueError("Mixed timezone offsets need a consistently normalized timestamp column.") from exc
        if stamps.tz is None:
            if not timezone:
                raise ValueError("Naive timestamps require timezone, for example timezone='UTC'.")
            stamps = stamps.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
        stamps = stamps.tz_convert("UTC")

    if stamps.hasnans or stamps.has_duplicates:
        raise ValueError("Timestamps must be valid and unique for this instrument.")

    canonical = {}
    for field, options in ALIASES.items():
        found = [c for c in options if c in raw]
        if len(found) > 1:
            raise ValueError(f"Multiple columns map to {field}: {found}. Keep one authoritative input.")
        if found:
            canonical[field] = pd.to_numeric(raw[found[0]], errors="raise").to_numpy(dtype=float)
        elif field in REQUIRED:
            raise ValueError(f"Missing required column {field!r}.")
    data = pd.DataFrame(canonical, index=stamps).sort_index(kind="stable")
    if not np.isfinite(data[list(REQUIRED)].to_numpy()).all():
        raise ValueError("OHLCV contains missing or nonfinite cells. Resolve these explicitly before calculating.")
    return data


def calculate(
    data: pd.DataFrame,
    *,
    interval: str,
    timestamp_role: str,
    gap_policy: str = "reset",
    end_offset: str = "0s",
) -> tuple[pd.DataFrame, dict]:
    """Calculate with explicit timestamp meaning and an explicit gap policy.

    reset: restart indicators after any missing interval (the specification).
    rows: count observed candles across closures; a declared specification variant.
    """
    step = pd.Timedelta(interval)
    offset = pd.Timedelta(end_offset)
    if pd.isna(step) or step <= pd.Timedelta(0):
        raise ValueError("The candle interval must be a valid positive duration.")
    if timestamp_role not in ("start", "end"):
        raise ValueError("timestamp_role must be start or end.")
    if gap_policy not in ("reset", "rows"):
        raise ValueError("gap_policy must be reset or rows.")
    if timestamp_role == "start" and offset != pd.Timedelta(0):
        raise ValueError("end_offset only applies to an end timestamp.")
    if pd.isna(offset) or offset < pd.Timedelta(0) or offset >= step:
        raise ValueError("end_offset must be a valid nonnegative duration shorter than the candle interval.")
    if data.empty:
        raise ValueError("No candles were supplied.")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise ValueError("The candle index must be a timezone-aware DatetimeIndex.")
    if data.index.hasnans or data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError("The candle index must be valid, unique, and chronological.")
    missing_columns = set(REQUIRED) - set(data.columns)
    if missing_columns:
        raise ValueError(f"Missing OHLCV columns: {sorted(missing_columns)}")
    if not np.isfinite(data[list(REQUIRED)].to_numpy(dtype=float)).all():
        raise ValueError("OHLCV must be finite. Resolve missing input rows explicitly.")

    # Only the index is changed below. Keep the input columns shared instead of
    # copying several years of candles before the feature arrays are allocated.
    data = data.copy(deep=False)
    data.index = data.index.tz_convert("UTC") + (step if timestamp_role == "start" else offset)
    data.index.name = "candle_end_utc"
    differences = data.index.to_series().diff()
    if (differences.dropna() < step).any():
        raise ValueError("Some candle timestamps are closer than the supplied interval.")
    gaps = differences.notna() & differences.ne(step)
    starts = np.r_[0, np.flatnonzero(gaps.to_numpy())] if gap_policy == "reset" else np.array([0])
    ends = np.r_[starts[1:], len(data)]
    chunks = [compute_features(data.iloc[start:end]) for start, end in zip(starts, ends)]
    features = chunks[0] if len(chunks) == 1 else pd.concat(chunks)
    del chunks
    unavailable = []
    if "trade_count" not in data or data["trade_count"].isna().all():
        unavailable.append("log1p_trade_count")
    if "taker_buy_base_volume" not in data or data["taker_buy_base_volume"].isna().all():
        unavailable.extend(f"taker_buy_imbalance_{n}" for n in (1, 5, 10, 20, 40))
    supported = [c for c in features if c not in unavailable]
    # Reduce one column at a time. A boolean mask for all 128 columns would
    # itself need hundreds of MB on the multi-year minute-candle dataset.
    ready = np.ones(len(features), dtype=bool)
    ready_all = np.ones(len(features), dtype=bool)
    missing_by_feature = {}
    constant_features = []
    for name, values in features.items():
        present = values.notna().to_numpy()
        missing_by_feature[name] = int(len(values) - np.count_nonzero(present))
        np.logical_and(ready_all, present, out=ready_all)
        if name not in unavailable:
            np.logical_and(ready, present, out=ready)
        if present.any() and values.min() == values.max():
            constant_features.append(name)
    report = {
        "rows": len(features),
        "first_candle_end_utc": data.index[0].isoformat(),
        "last_candle_end_utc": data.index[-1].isoformat(),
        "interval": str(step),
        "input_timestamp_role": timestamp_role,
        "end_offset": str(offset),
        "gap_policy": gap_policy,
        "gap_count": int(gaps.sum()),
        "calculation_segments": len(starts),
        "feature_count": len(features.columns),
        "features_supported_by_supplied_inputs": len(supported),
        "unavailable_due_to_missing_inputs": unavailable,
        "rows_complete_for_supported_features": int(ready.sum()),
        "first_complete_supported_row": data.index[np.flatnonzero(ready)[0]].isoformat() if ready.any() else None,
        "rows_complete_for_all_128": int(ready_all.sum()),
        "missing_values_by_feature": missing_by_feature,
        "constant_nonmissing_features": constant_features,
        "notes": [
            "Features use each completed candle and earlier observations only.",
            "Warm-up and undefined values are retained as missing; no imputation or global scaling is applied.",
            "The 128 feature columns exclude the timestamp.",
        ],
    }
    if gap_policy == "rows":
        report["notes"].append("Observed-row windows carry across time gaps; this is a declared variant of the gap-reset specification.")
    if step >= pd.Timedelta("1D"):
        report["notes"].append("Time-of-day features can be constant on daily or slower candles.")
    if not ready.any():
        report["notes"].append("No row has all supported features populated; review warm-up, gaps, and degenerate windows.")
    return features, report
