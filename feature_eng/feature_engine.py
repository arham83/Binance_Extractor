"""Calculate the 128 candle features in doc/feature_specification_128.md.

``compute_features`` processes one instrument and one contiguous segment of
completed candles. Its index must contain exclusive candle-end times in UTC.
The caller must split data at gaps or missing required observations. Warm-up,
missing optional inputs, and undefined ratios remain NaN; no values are filled.

Dependencies: NumPy and pandas. The ordered JSON registry lives beside this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


with Path(__file__).with_name("feature_manifest_128.json").open(encoding="utf-8") as _file:
    _manifest = json.load(_file)
FEATURE_NAMES = tuple(feature["name"] for feature in _manifest["features"])
if len(FEATURE_NAMES) != 128 or len(set(FEATURE_NAMES)) != 128:
    raise ValueError("Feature manifest must contain exactly 128 unique names.")

_REQUIRED = ("open", "high", "low", "close", "volume")
_BATCH_ROWS = 8192


def _divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Division with missing output for a zero denominator."""
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return numerator / denominator.where(denominator.ne(0))


def _smooth(values: pd.Series, period: int, alpha: float) -> pd.Series:
    """Recursive smoothing, seeded with an SMA after each missing-data break."""
    array = values.to_numpy(dtype=float)
    output = np.full(len(array), np.nan)
    valid = np.isfinite(array)
    boundaries = np.diff(np.r_[False, valid, False].astype(np.int8))
    starts, stops = np.flatnonzero(boundaries == 1), np.flatnonzero(boundaries == -1)
    for start, stop in zip(starts, stops):
        if stop - start < period:
            continue
        seed_at = start + period - 1
        seeded = array[seed_at:stop].copy()
        seeded[0] = array[start : seed_at + 1].mean()
        output[seed_at:stop] = (
            pd.Series(seeded).ewm(alpha=alpha, adjust=False, ignore_na=False).mean().to_numpy()
        )
    return pd.Series(output, index=values.index)


def _std(values: pd.Series, period: int) -> pd.Series:
    """Population deviation from each window, without rolling-state residue.

    Incremental add/remove variance can retain error from a large observation
    after it has left the window. That error can dominate the true variance
    after a volatility collapse. Center bounded batches directly instead; a
    within-window origin also preserves precision for large levels with small
    fluctuations. Missing observations propagate and constant windows give zero.
    """
    output = np.full(len(values), np.nan)
    if len(values) >= period:
        windows = sliding_window_view(values.to_numpy(dtype=float), period)
        for offset in range(0, len(windows), _BATCH_ROWS):
            batch = windows[offset : offset + _BATCH_ROWS]
            centered = batch - batch[:, :1]
            centered -= centered.mean(axis=1, keepdims=True)
            variance = np.einsum("ij,ij->i", centered, centered) / period
            start = offset + period - 1
            output[start : start + len(batch)] = np.sqrt(variance)
    return pd.Series(output, index=values.index)


def _regression(log_close: pd.Series, period: int) -> tuple[pd.Series, pd.Series]:
    """Centered, intercept-inclusive rolling regression without large raw sums."""
    count = len(log_close)
    slopes = np.full(count, np.nan)
    r_squared = np.full(count, np.nan)
    if count >= period:
        windows = sliding_window_view(log_close.to_numpy(), period)
        centered_x = np.arange(period, dtype=float) - (period - 1) / 2
        sum_x_squared = np.square(centered_x).sum()
        for offset in range(0, len(windows), _BATCH_ROWS):
            batch = windows[offset : offset + _BATCH_ROWS]
            centered_y = batch - batch[:, :1]
            centered_y -= centered_y.mean(axis=1, keepdims=True)
            numerator = np.einsum("ij,j->i", centered_y, centered_x)
            slope = numerator / sum_x_squared
            sum_y_squared = np.einsum("ij,ij->i", centered_y, centered_y)
            constant = batch.max(axis=1) == batch.min(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                r2 = np.square(numerator) / (sum_x_squared * sum_y_squared)
            # Exact OLS R² is in [0, 1]; contain floating-point round-off only.
            r2 = np.clip(r2, 0.0, 1.0)
            slope[constant], r2[constant] = 0.0, 0.0
            target = slice(offset + period - 1, offset + period - 1 + len(batch))
            slopes[target], r_squared[target] = slope, r2
    return pd.Series(slopes, index=log_close.index), pd.Series(r_squared, index=log_close.index)


def _cci(typical: pd.Series, period: int) -> pd.Series:
    output = np.full(len(typical), np.nan)
    if len(typical) >= period:
        windows = sliding_window_view(typical.to_numpy(), period)
        for offset in range(0, len(windows), _BATCH_ROWS):
            batch = windows[offset : offset + _BATCH_ROWS]
            centered = batch - batch[:, :1]
            centered -= centered.mean(axis=1, keepdims=True)
            mean_deviation = np.abs(centered).mean(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                value = centered[:, -1] / (0.015 * mean_deviation)
            # Equality test also handles rounding in a constant window's mean.
            constant = batch.max(axis=1) == batch.min(axis=1)
            value[(mean_deviation == 0) | constant] = 0.0
            output[offset + period - 1 : offset + period - 1 + len(batch)] = value
    return pd.Series(output, index=typical.index)


def _moments(returns: pd.Series, period: int) -> tuple[pd.Series, pd.Series]:
    skewness = np.full(len(returns), np.nan)
    excess_kurtosis = np.full(len(returns), np.nan)
    if len(returns) >= period:
        windows = sliding_window_view(returns.to_numpy(), period)
        for offset in range(0, len(windows), _BATCH_ROWS):
            batch = windows[offset : offset + _BATCH_ROWS]
            centered = batch - batch.mean(axis=1, keepdims=True)
            squared = centered * centered
            m2 = squared.mean(axis=1)
            m3 = (squared * centered).mean(axis=1)
            m4 = (squared * squared).mean(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                skew = m3 / np.power(m2, 1.5)
                kurt = m4 / np.square(m2) - 3.0
            constant = batch.max(axis=1) == batch.min(axis=1)
            skew[constant], kurt[constant] = np.nan, np.nan
            target = slice(offset + period - 1, offset + period - 1 + len(batch))
            skewness[target], excess_kurtosis[target] = skew, kurt
    return pd.Series(skewness, index=returns.index), pd.Series(excess_kurtosis, index=returns.index)


def _correlation(left: pd.Series, right: pd.Series, period: int) -> pd.Series:
    """Pearson correlation using separately centered, fully observed vectors."""
    output = np.full(len(left), np.nan)
    if len(left) >= period:
        windows_left = sliding_window_view(left.to_numpy(), period)
        windows_right = sliding_window_view(right.to_numpy(), period)
        for offset in range(0, len(windows_left), _BATCH_ROWS):
            batch_left = windows_left[offset : offset + _BATCH_ROWS]
            batch_right = windows_right[offset : offset + _BATCH_ROWS]
            x = batch_left - batch_left.mean(axis=1, keepdims=True)
            y = batch_right - batch_right.mean(axis=1, keepdims=True)
            numerator = np.einsum("ij,ij->i", x, y)
            denominator = np.sqrt(np.einsum("ij,ij->i", x, x) * np.einsum("ij,ij->i", y, y))
            with np.errstate(divide="ignore", invalid="ignore"):
                values = numerator / denominator
            constant = (batch_left.max(axis=1) == batch_left.min(axis=1)) | (
                batch_right.max(axis=1) == batch_right.min(axis=1)
            )
            values[constant] = np.nan
            values = np.clip(values, -1.0, 1.0)
            output[offset + period - 1 : offset + period - 1 + len(values)] = values
    return pd.Series(output, index=left.index)


def _validate(data: pd.DataFrame) -> None:
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame.")
    if data.columns.has_duplicates:
        raise ValueError("Input column names must be unique.")
    missing = sorted(set(_REQUIRED) - set(data.columns))
    if missing:
        raise ValueError(f"Missing required OHLCV columns: {', '.join(missing)}")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise ValueError("Use a timezone-aware UTC DatetimeIndex of exclusive candle-end times.")
    if str(data.index.tz) not in {"UTC", "Etc/UTC", "GMT", "Etc/GMT", "UTC+00:00"}:
        raise ValueError("Convert the DatetimeIndex to UTC before calculating features.")
    if data.index.hasnans or not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("Candle-end times must be present, chronological, and unique.")
    try:
        required = data.loc[:, list(_REQUIRED)].to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("Required OHLCV columns must contain numeric values.") from error
    if not np.isfinite(required).all():
        raise ValueError("Split at missing/nonfinite required inputs before calculating a segment.")
    if not len(data):
        return
    opening, high, low, close, volume = required.T
    if (required[:, :4] <= 0).any() or (volume < 0).any():
        raise ValueError("Prices must be positive and volume must be nonnegative.")
    if ((low > np.minimum(opening, close)) | (high < np.maximum(opening, close)) | (low > high)).any():
        raise ValueError("Each candle must satisfy low <= open, close <= high.")
    for name in ("trade_count", "taker_buy_base_volume"):
        if name not in data:
            continue
        values = data[name].to_numpy(dtype=float)
        observed = ~np.isnan(values)
        if not np.isfinite(values[observed]).all() or (values[observed] < 0).any():
            raise ValueError(f"{name} must contain nonnegative finite values or NaN.")
        if name == "trade_count" and (values[observed] != np.floor(values[observed])).any():
            raise ValueError("trade_count must contain integers or NaN.")
        if name == "taker_buy_base_volume" and (values[observed] > volume[observed]).any():
            raise ValueError("taker_buy_base_volume cannot exceed volume.")


def compute_features(data: pd.DataFrame) -> pd.DataFrame:
    """Return all 128 features, in manifest order, for one contiguous segment.

    Required columns: open, high, low, close, volume. Optional columns:
    trade_count, taker_buy_base_volume. All prices are positive, volume is in
    consistent base-asset units, and the UTC index contains exclusive candle-end
    boundaries. Every output row uses that row and earlier observations only.
    An absent optional field yields NaN for its dependent feature columns.
    """
    _validate(data)
    if data.empty:
        return pd.DataFrame(index=data.index, columns=FEATURE_NAMES, dtype=float)

    opening, high, low, close, volume = (data[name].astype(float) for name in _REQUIRED)
    previous = close.shift(1)
    returns = np.log(close / previous)
    typical = (high + low + close) / 3.0
    price_change = close.diff()
    features: dict[str, pd.Series | np.ndarray] = {}
    ema_cache: dict[int, pd.Series] = {}

    def ema(period: int) -> pd.Series:
        if period not in ema_cache:
            ema_cache[period] = _smooth(close, period, 2.0 / (period + 1))
        return ema_cache[period]

    for horizon in (1, 2, 3, 5, 10, 20, 40, 80):
        features[f"log_return_{horizon}"] = np.log(close / close.shift(horizon))
    features["candle_body"] = (close - opening) / previous
    features["candle_range"] = (high - low) / previous
    features["upper_wick"] = (high - np.maximum(opening, close)) / previous
    features["lower_wick"] = (np.minimum(opening, close) - low) / previous

    for period in (5, 10, 20, 40, 80, 160):
        features[f"ema_distance_{period}"] = close / ema(period) - 1.0
    for period in (10, 20, 50, 100):
        features[f"sma_distance_{period}"] = close / close.rolling(period).mean() - 1.0
    log_close = np.log(close)
    for period in (10, 20, 40, 80, 160):
        features[f"log_slope_{period}"], features[f"log_r2_{period}"] = _regression(log_close, period)

    gains, losses = price_change.clip(lower=0), (-price_change).clip(lower=0)
    for period in (5, 7, 14, 21, 28, 42):
        gain = _smooth(gains, period, 1.0 / period)
        loss = _smooth(losses, period, 1.0 / period)
        total = gain + loss
        features[f"rsi_{period}"] = (100.0 * _divide(gain, total)).mask(total.eq(0), 50.0)
    for period in (7, 14, 21, 28):
        lowest, highest = low.rolling(period).min(), high.rolling(period).max()
        span = highest - lowest
        k = (100.0 * _divide(close - lowest, span)).mask(span.eq(0), 50.0)
        features[f"stoch_k_{period}"] = k
        features[f"stoch_d_{period}"] = k.rolling(3).mean()
    for period in (10, 20, 40, 80):
        features[f"cci_{period}"] = _cci(typical, period)
    for fast, slow, signal_period in ((6, 13, 5), (12, 26, 9), (24, 52, 18)):
        line = ema(fast) - ema(slow)
        signal = _smooth(line, signal_period, 2.0 / (signal_period + 1))
        features[f"macd_line_{fast}_{slow}_{signal_period}"] = line / close
        features[f"macd_hist_{fast}_{slow}_{signal_period}"] = (line - signal) / close

    for period in (5, 10, 20, 40, 80, 160):
        features[f"return_vol_{period}"] = _std(returns, period)
    true_range = pd.Series(
        np.maximum.reduce([(high - low).to_numpy(), (high - previous).abs().to_numpy(), (low - previous).abs().to_numpy()]),
        index=data.index,
    )
    for period in (7, 14, 28, 56):
        features[f"atr_normalized_{period}"] = _smooth(true_range, period, 1.0 / period) / close
    for period in (10, 20, 40, 80):
        mean, deviation = close.rolling(period).mean(), _std(close, period)
        width = 4.0 * deviation
        features[f"bb_position_{period}"] = _divide(close - (mean - 2.0 * deviation), width).mask(width.eq(0), 0.5)
        features[f"bb_width_{period}"] = width / mean
    downside_squared = returns.clip(upper=0).pow(2)
    for period in (20, 80):
        features[f"downside_deviation_{period}"] = np.sqrt(downside_squared.rolling(period).mean())

    for period in (5, 10, 20, 40, 80, 160):
        features[f"relative_volume_{period}"] = _divide(volume, volume.rolling(period).mean().shift(1))
    for period in (20, 40, 80, 160):
        baseline = volume.rolling(period).mean().shift(1)
        features[f"volume_zscore_{period}"] = _divide(volume - baseline, _std(volume, period).shift(1))
    signed_volume = np.sign(price_change) * volume
    for period in (10, 20, 40, 80):
        features[f"obv_balance_{period}"] = _divide(signed_volume.rolling(period).sum(), volume.rolling(period).sum())

    typical_change = typical.diff()
    money_flow = typical * volume
    positive_flow = money_flow.where(typical_change.gt(0), 0.0).where(typical_change.notna())
    negative_flow = money_flow.where(typical_change.lt(0), 0.0).where(typical_change.notna())
    for period in (7, 14, 28, 56):
        positive, negative = positive_flow.rolling(period).sum(), negative_flow.rolling(period).sum()
        total = positive + negative
        features[f"mfi_{period}"] = (100.0 * _divide(positive, total)).mask(total.eq(0), 50.0)

    candle_span = high - low
    multiplier = _divide(2.0 * close - high - low, candle_span).mask(candle_span.eq(0), 0.0)
    for period in (10, 20, 40, 80):
        features[f"cmf_{period}"] = _divide((multiplier * volume).rolling(period).sum(), volume.rolling(period).sum())
    log_volume = np.log1p(volume)
    features["log1p_volume"] = log_volume
    features["log1p_trade_count"] = (
        np.log1p(data["trade_count"].astype(float))
        if "trade_count" in data else pd.Series(np.nan, index=data.index)
    )

    for period in (20, 40, 80, 160):
        features[f"return_skew_{period}"], features[f"return_excess_kurtosis_{period}"] = _moments(returns, period)
    for lag in (1, 2, 3, 5):
        features[f"return_autocorr_80_lag_{lag}"] = _correlation(returns, returns.shift(lag), 80)
    for period in (20, 80):
        mean = returns.rolling(period).mean()
        variance = _std(returns, period).pow(2)
        previous_variance = variance.shift(period)
        pooled_deviation = np.sqrt((variance + previous_variance) / 2.0)
        features[f"mean_change_{period}"] = _divide(mean - mean.shift(period), pooled_deviation)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.log(_divide(variance, previous_variance))
        features[f"log_variance_ratio_{period}"] = ratio.where(variance.gt(0) & previous_variance.gt(0))

    buy_volume = (
        data["taker_buy_base_volume"].astype(float)
        if "taker_buy_base_volume" in data else pd.Series(np.nan, index=data.index)
    )
    for period in (1, 5, 10, 20, 40):
        total_volume = volume.rolling(period).sum()
        total_buy = buy_volume.rolling(period, min_periods=period).sum()
        features[f"taker_buy_imbalance_{period}"] = _divide(2.0 * total_buy - total_volume, total_volume)
    for period in (20, 40, 80):
        features[f"return_volume_corr_{period}"] = _correlation(returns, log_volume, period)

    timestamps = data.index.tz_convert("UTC")
    seconds = (
        timestamps.hour * 3600.0 + timestamps.minute * 60.0 + timestamps.second
        + timestamps.microsecond / 1_000_000.0 + timestamps.nanosecond / 1_000_000_000.0
    )
    day_phase = np.asarray(seconds / 86400.0)
    week_phase = (np.asarray(timestamps.dayofweek) + day_phase) / 7.0
    features["time_of_day_sin"] = np.sin(2.0 * np.pi * day_phase)
    features["time_of_day_cos"] = np.cos(2.0 * np.pi * day_phase)
    features["time_of_week_sin"] = np.sin(2.0 * np.pi * week_phase)
    features["time_of_week_cos"] = np.cos(2.0 * np.pi * week_phase)

    if set(features) != set(FEATURE_NAMES):
        raise RuntimeError("Calculated columns do not match the 128-feature manifest.")
    # A year of minute candles already needs about 0.54 GB for 128 float columns.
    # Preserve the calculated arrays instead of making several full-frame copies.
    ordered: dict[str, np.ndarray] = {}
    for name in FEATURE_NAMES:
        array = np.asarray(features[name], dtype=float)
        infinite = np.isinf(array)
        if infinite.any():
            array = array.copy()
            array[infinite] = np.nan
        ordered[name] = array
    return pd.DataFrame(ordered, index=data.index, copy=False)
