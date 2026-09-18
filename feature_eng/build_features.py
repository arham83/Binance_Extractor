"""Build the agreed 128 features from raw Binance candle Parquet partitions.

Every complete instrument series is assembled before calculation, so recursive
indicators retain their history across source files and calendar months. Source
files are read only. Outputs contain the 128 model features plus candle time and
symbol; source and series details remain in the separate run summary.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .calculate_features import calculate
from .feature_engine import FEATURE_NAMES


SERIES_KEYS = ("exchange", "product", "symbol", "interval", "price_family")
TIME_COLUMNS = ("open_time_ms", "close_time_ms", "completed_at_ms")
RAW_COLUMNS = {
    "open": "open_raw", "high": "high_raw", "low": "low_raw",
    "close": "close_raw", "volume": "base_volume_raw",
    "trade_count": "trade_count",
    "taker_buy_base_volume": "taker_buy_base_volume_raw",
}
AVAILABILITY_COLUMN = "completed_values_are_predecision_data_only_after_ms"
IDENTIFIER_COLUMNS = ("candle_end_utc", "symbol")
REQUIRED_COLUMNS = (*SERIES_KEYS, *TIME_COLUMNS, *RAW_COLUMNS.values())
EXTRACTOR_KEYS = ("market", "symbol", "interval")
EXTRACTOR_TIMES = {
    "open_time": "open_time_ms",
    "close_time": "close_time_ms",
    "feature_available_at": "completed_at_ms",
}
EXTRACTOR_COLUMNS = (*EXTRACTOR_KEYS, *EXTRACTOR_TIMES, *RAW_COLUMNS)


def _fixed_interval(value: str) -> pd.Timedelta:
    """Parse Binance m as minutes; uppercase M denotes unsupported months."""
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h|d|w)", value)
    if not match:
        raise ValueError(f"Unsupported fixed candle interval {value!r}; use e.g. 1m, 5m, 1h, 1d.")
    units = {"s": "s", "m": "min", "h": "h", "d": "D", "w": "W"}
    return pd.Timedelta(int(match[1]), unit=units[match[2]])


def _write_summary(path: Path, summary: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _symbol_output_files(symbols: set[str]) -> dict[str, str]:
    """Keep familiar tickers readable without allowing unsafe/colliding paths.

    Legacy inputs can contain arbitrary instrument names. Only short ASCII
    identifiers are used literally, and case-fold collisions are disambiguated
    for case-insensitive filesystems as well as case-sensitive ones.
    """
    stems = {
        symbol: symbol if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", symbol)
        else "symbol-" + hashlib.sha256(symbol.encode("utf-8")).hexdigest()
        for symbol in sorted(symbols)
    }
    counts: dict[str, int] = {}
    for stem in stems.values():
        folded = stem.casefold()
        counts[folded] = counts.get(folded, 0) + 1
    result = {}
    for symbol, stem in stems.items():
        if counts[stem.casefold()] > 1:
            stem += "-" + hashlib.sha256(symbol.encode("utf-8")).hexdigest()
        result[symbol] = stem + "-features.parquet"
    if len({filename.casefold() for filename in result.values()}) != len(result):
        raise ValueError("Symbols cannot be mapped to unique output filenames.")
    return result


def _integer_column(raw: pd.DataFrame, name: str) -> np.ndarray:
    values = pd.to_numeric(raw[name], errors="raise")
    if values.isna().any():
        raise ValueError(f"Missing required input values in {name}.")
    array = values.to_numpy()
    if not np.isfinite(array).all() or (array != np.floor(array)).any():
        raise ValueError(f"{name} must contain finite integer values.")
    limits = np.iinfo(np.int64)
    if (array < limits.min).any() or (array > limits.max).any():
        raise ValueError(f"{name} is outside the supported integer range.")
    return array.astype(np.int64, copy=False)


def _open_dataset(files: list[Path]) -> tuple[ds.Dataset, str]:
    schemas = []
    layouts = set()
    for path in files:
        schema = pq.read_schema(path)
        names = set(schema.names)
        if set(REQUIRED_COLUMNS).issubset(names):
            layouts.add("legacy")
        elif set(EXTRACTOR_COLUMNS).issubset(names):
            layouts.add("extractor")
            for name in EXTRACTOR_TIMES:
                dtype = schema.field(name).type
                if not pa.types.is_timestamp(dtype) or dtype.tz is None:
                    raise ValueError(f"{path}: {name} must be a timezone-aware Parquet timestamp.")
        else:
            missing = sorted(set(EXTRACTOR_COLUMNS) - names)
            legacy_missing = sorted(set(REQUIRED_COLUMNS) - names)
            raise ValueError(
                f"{path}: unsupported candle schema. Missing extractor columns: {', '.join(missing)}; "
                f"missing legacy raw columns: {', '.join(legacy_missing)}"
            )
        schemas.append(schema)
    if len(layouts) != 1:
        raise ValueError("Input mixes extractor and legacy candle schemas; select one layout per run.")
    schema = pa.unify_schemas(schemas, promote_options="permissive")
    # Explicit file paths avoid inferring year/month from Hive directory names.
    return ds.dataset([str(path) for path in files], format="parquet", schema=schema), layouts.pop()


def _series_keys(dataset: ds.Dataset, symbols: list[str] | None, layout: str) -> list[tuple[str, ...]]:
    expression = ds.field("symbol").isin(symbols) if symbols else None
    keys: set[tuple[str, ...]] = set()
    columns = EXTRACTOR_KEYS if layout == "extractor" else SERIES_KEYS
    scanner = dataset.scanner(columns=list(columns), filter=expression, batch_size=65536)
    for batch in scanner.to_batches():
        frame = batch.to_pandas()
        if frame.isna().any().any():
            raise ValueError("Series identity columns must not contain nulls.")
        for values in frame.drop_duplicates().itertuples(index=False, name=None):
            if not all(isinstance(value, str) and value for value in values):
                raise ValueError("Series identity columns must contain nonempty strings.")
            if layout == "extractor":
                market, symbol, interval = values
                if market not in ("um", "cm", "spot"):
                    raise ValueError(f"Unsupported Binance market {market!r}.")
                values = ("binance", market, symbol, interval, "trade")
            keys.add(values)
    if symbols:
        missing = set(symbols) - {key[2] for key in keys}
        if missing:
            raise ValueError(f"Requested symbols were not found: {', '.join(sorted(missing))}")
    if not keys:
        raise ValueError("The selected input contains no candle rows.")
    return sorted(keys)


def _normalize_extractor(raw: pd.DataFrame) -> pd.DataFrame:
    """Adapt this repository's typed kline Parquet without guessing epoch units."""
    raw = raw.rename(columns=RAW_COLUMNS)
    for source, target in EXTRACTOR_TIMES.items():
        timestamps = pd.DatetimeIndex(raw[source])
        if timestamps.tz is None or timestamps.hasnans:
            raise ValueError(f"{source} must contain valid timezone-aware timestamps.")
        # round_ok=False catches submillisecond timestamps rather than silently
        # moving an availability boundary earlier.
        raw[target] = timestamps.as_unit("ms", round_ok=False).asi8
    return raw


def _canonical(raw: pd.DataFrame, key: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    step = _fixed_interval(key[3])
    for name in TIME_COLUMNS:
        raw[name] = _integer_column(raw, name)
    if not raw["open_time_ms"].is_monotonic_increasing:
        raw = raw.sort_values("open_time_ms", kind="stable").reset_index(drop=True)
    if raw["open_time_ms"].duplicated().any():
        raise ValueError(f"Duplicate candle keys for {dict(zip(SERIES_KEYS, key))}; resolve overlapping files explicitly.")
    opened = pd.DatetimeIndex(pd.to_datetime(raw["open_time_ms"], unit="ms", utc=True))
    completed = pd.DatetimeIndex(pd.to_datetime(raw["completed_at_ms"], unit="ms", utc=True))
    if not completed.equals(opened + step):
        raise ValueError("completed_at_ms must equal open_time_ms plus the candle interval.")
    if not (raw["close_time_ms"].to_numpy() == raw["completed_at_ms"].to_numpy() - 1).all():
        raise ValueError("close_time_ms must be the inclusive last millisecond before completed_at_ms.")
    availability = AVAILABILITY_COLUMN
    if availability in raw:
        boundaries = _integer_column(raw, availability)
        if not np.array_equal(boundaries, raw["completed_at_ms"].to_numpy()):
            raise ValueError(f"{availability} must match completed_at_ms for this raw schema.")
    canonical = {}
    for name, source in RAW_COLUMNS.items():
        values = pd.to_numeric(raw[source], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"Missing or nonfinite required input values in {source}.")
        canonical[name] = values
    return pd.DataFrame(canonical, index=completed), raw


def generate_dataset(
    input_path: Path,
    output_dir: Path,
    *,
    symbols: list[str] | None = None,
    gap_policy: str = "reset",
) -> dict:
    """Write one SYMBOL-features.parquet per selected symbol.

    A complete series, including its full feature matrix, must fit in memory.
    Only one series is calculated and appended at a time. Each symbol must
    identify one market/interval/price series, because those metadata columns
    are not exported. Gaps restart history by default. Failed runs retain a summary marked
    failed and .tmp outputs; final Parquet files are published only after all
    series succeed. Choose a new output directory after resolving a failure.
    Source files are never edited.
    """
    input_path, output_dir = Path(input_path).resolve(), Path(output_dir).resolve()
    if gap_policy not in ("reset", "rows"):
        raise ValueError("gap_policy must be reset or rows.")
    if not input_path.exists():
        raise ValueError(f"Input does not exist: {input_path}")
    if output_dir.exists():
        raise ValueError(f"Output directory already exists; choose a new directory: {output_dir}")
    if input_path.is_dir() and output_dir.is_relative_to(input_path):
        raise ValueError("Output directory cannot be within the input dataset discovery tree.")
    if input_path.is_file():
        if input_path.suffix.lower() != ".parquet":
            raise ValueError("Input must be a Parquet file or a directory containing Parquet files.")
        files = [input_path]
    else:
        files = sorted(path for path in input_path.rglob("*.parquet") if path.is_file())
    if not files:
        raise ValueError("No Parquet input files were found.")
    dataset, layout = _open_dataset(files)
    keys = _series_keys(dataset, symbols, layout)
    output_files = _symbol_output_files({key[2] for key in keys})
    symbol_keys = {symbol: [key for key in keys if key[2] == symbol] for symbol in output_files}
    for symbol, grouped_keys in symbol_keys.items():
        if len(grouped_keys) > 1:
            raise ValueError(
                f"Multiple market/interval/price series found for {symbol}. "
                "Select one series per symbol in the input path; output files "
                "contain only candle_end_utc, symbol and the 128 features."
            )
    if layout == "extractor":
        projected = list(dict.fromkeys([*EXTRACTOR_TIMES, *RAW_COLUMNS]))
    else:
        projected = list(dict.fromkeys([*TIME_COLUMNS, *RAW_COLUMNS.values()]))
        if AVAILABILITY_COLUMN in dataset.schema.names:
            projected.append(AVAILABILITY_COLUMN)
    # Validate input timestamps, but export just the row identifiers and features.
    output_schema = pa.schema([
        pa.field("candle_end_utc", pa.timestamp("ns", tz="UTC")),
        pa.field("symbol", pa.string()),
        *(pa.field(name, pa.float64()) for name in FEATURE_NAMES),
    ])
    summary = {
        "status": "running", "input": str(input_path), "output": str(output_dir),
        "input_files": len(files), "input_paths": [str(path) for path in files],
        "feature_count": len(FEATURE_NAMES), "series_count": len(keys),
        "processed_series": 0, "rows": 0, "gap_policy": gap_policy, "series": [],
        "output_files": list(output_files.values()), "input_layout": layout,
        "identifier_columns": list(IDENTIFIER_COLUMNS), "output_columns": output_schema.names,
        "notes": [
            "Read only completed candles. Actual publication/arrival time can be unknown.",
            "Series history continues across files and months; actual time gaps follow gap_policy.",
            "One file and one market/interval/price series per symbol.",
            "Warm-up and undefined features remain null; no imputation or global scaling is applied.",
            "Output contains only candle_end_utc, symbol and the 128 columns in feature_columns.json.",
            "Source paths, market, interval and other provenance are kept in this summary, not feature columns.",
            "For extractor input, product retains the Binance market code (um, cm, or spot); price_family is trade.",
        ],
    }
    output_dir.mkdir(parents=True)
    summary_path = output_dir / "summary.json"
    _write_summary(summary_path, summary)
    try:
        (output_dir / "feature_columns.json").write_text(
            json.dumps(list(FEATURE_NAMES), indent=2) + "\n", encoding="utf-8"
        )
        number = 0
        for symbol, grouped_keys in symbol_keys.items():
            output_file = output_files[symbol]
            temporary_output = output_dir / (output_file + ".tmp")
            with pq.ParquetWriter(temporary_output, output_schema, compression="zstd") as writer:
                for key in grouped_keys:
                    number += 1
                    key_dict = dict(zip(SERIES_KEYS, key))
                    print(f"[{number}/{len(keys)}] Calculating {key_dict}", flush=True)
                    expression = None
                    filters = ({"market": key[1], "symbol": key[2], "interval": key[3]}
                               if layout == "extractor" else key_dict)
                    for name, value in filters.items():
                        term = ds.field(name) == value
                        expression = term if expression is None else expression & term
                    raw = dataset.to_table(columns=projected, filter=expression).to_pandas()
                    if layout == "extractor":
                        raw = _normalize_extractor(raw)
                    canonical, raw = _canonical(raw, key)
                    print(f"  Loaded {len(canonical):,} completed candles; calculating features.", flush=True)
                    features, report = calculate(
                        canonical, interval=str(_fixed_interval(key[3])),
                        timestamp_role="end", gap_policy=gap_policy,
                    )
                    series_id = hashlib.sha256(json.dumps(key).encode("utf-8")).hexdigest()[:24]
                    print(f"  Writing {len(features):,} feature rows.", flush=True)
                    # Bound export memory while keeping one file per symbol.
                    for start in range(0, len(features), 32768):
                        stop = min(start + 32768, len(features))
                        result = {"candle_end_utc": features.index[start:stop],
                                  "symbol": [symbol] * (stop - start)}
                        result.update({name: features[name].iloc[start:stop] for name in FEATURE_NAMES})
                        writer.write_table(
                            pa.Table.from_pydict(result, schema=output_schema),
                            row_group_size=32768,
                        )
                        del result
                    entry = {**report, "key": key_dict, "series_id": series_id, "output_files": [output_file]}
                    summary["series"].append(entry)
                    summary["processed_series"] += 1
                    summary["rows"] += len(features)
                    _write_summary(summary_path, summary)
                    print(f"  Added {len(features):,} rows to {output_file}.", flush=True)
                    del features, canonical, raw
        # Publishing starts only after every independent series has succeeded.
        for output_file in output_files.values():
            (output_dir / (output_file + ".tmp")).replace(output_dir / output_file)
        summary["status"] = "complete"
        _write_summary(summary_path, summary)
    except (Exception, KeyboardInterrupt) as error:
        # If publication or the final summary write fails, move any already
        # published files back to staging so the failed run is unambiguous.
        rollback_errors = []
        for output_file in output_files.values():
            published = output_dir / output_file
            if published.exists():
                try:
                    published.replace(output_dir / (output_file + ".tmp"))
                except OSError as rollback_error:
                    rollback_errors.append(f"{output_file}: {rollback_error}")
        summary["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        summary["error"] = f"{type(error).__name__}: {error}"
        if rollback_errors:
            summary["publication_rollback_errors"] = rollback_errors
        _write_summary(summary_path, summary)
        raise
    return summary
