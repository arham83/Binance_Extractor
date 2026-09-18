# Understanding Our 128 Market Features
## A beginner's guide to what the numbers mean and why we calculate them

This guide explains the features produced by our Python code. You do not need a background in trading, statistics, or machine learning. Start with the first three sections, then use the feature sections and the complete column dictionary as a reference.

The implementation is [feature_engine.py](../feature_eng/feature_engine.py). The companion [technical specification](feature_specification_128.md) gives the exact mathematical definitions. This guide focuses on understanding those definitions.

This repository reads the extractor's **Binance candle Parquet files** and calculates each coin and interval separately. For BTCUSDT, BTC is the base asset, prices are quoted in USDT, and base volume measures BTC trading quantity. These candles include the extra trade-count and taker-buy fields needed to calculate all 128 features. The examples below use one-minute candles and small teaching examples unless explicitly identified as results from the original January 2022 reference sample.

Run instructions and configuration paths are in the [repository README](../README.md). The original reference sample is not required: this pipeline reads your local extractor output. Results for your own data are recorded in the generated `summary.json`.

A feature describes something observed in the data. Whether it helps predict a future outcome is a separate question that must be tested. The chosen windows are starting settings, not settings proven to be optimal.

## Contents

1. [What is in a candle?](#1-what-is-in-a-candle)
2. [What is a feature, and why are there 128?](#2-what-is-a-feature-and-why-are-there-128)
3. [How to read windows and numerical values](#3-how-to-read-windows-and-numerical-values)
4. [Price and candle shape](#4-price-and-candle-shape--12-features)
5. [Trend](#5-trend--20-features)
6. [Momentum](#6-momentum--24-features)
7. [Volatility](#7-volatility--20-features)
8. [Volume and activity](#8-volume-and-activity--24-features)
9. [Distribution and regime changes](#9-distribution-and-regime-changes--16-features)
10. [Trade flow and calendar](#10-trade-flow-and-calendar--12-features)
11. [Reading several features together](#11-reading-several-features-together)
12. [Understanding the generated files and missing values](#12-understanding-the-generated-files-and-missing-values)
13. [Complete dictionary of all 128 columns](#13-complete-dictionary-of-all-128-columns)

## 1. What is in a candle?

A candle summarizes trading during one fixed interval. In the sample, each interval is one minute. Instead of storing every trade in that minute, a candle records a few summary values.

| Input | Plain meaning | Extractor Parquet field | Original reference field |
| --- | --- | --- | --- |
| Open | Price of the first trade in the candle | open | open_raw |
| High | Highest traded price during the candle | high | high_raw |
| Low | Lowest traded price during the candle | low | low_raw |
| Close | Price of the last trade in the candle | close | close_raw |
| Volume | Total traded base-asset quantity during the candle | volume | base_volume_raw |
| Trade count | Number of trades during the candle | trade_count | trade_count |
| Taker-buy volume | Base-asset quantity traded when the buyer initiated the trade by taking available liquidity | taker_buy_base_volume | taker_buy_base_volume_raw |
| Open time | Start of the interval | open_time | open_time_ms |
| Completion time | Exclusive end boundary of the interval | feature_available_at | completed_at_ms |

The reader supports the extractor's numeric fields and UTC timestamps, as well as the original reference schema's text fields and epoch-millisecond timestamps. These represent the same candle inputs. “Raw” means unengineered market observations here; it does not mean a different kind of price.

**Volume and trade count are different.** Ten trades of 1 BTC each and 100 trades of 0.1 BTC each both produce 10 BTC of volume, but different trade counts. Neither field tells us how many distinct people traded.

A candle preserves the high and low, but not the complete sequence of trades. You cannot tell from one candle alone whether the high occurred before the low.

Consider this invented candle:

| Item | Value |
| --- | ---: |
| Previous candle's close | 100 |
| Current open | 101 |
| Current high | 105 |
| Current low | 99 |
| Current close | 104 |
| Current volume | 200 |
| Current trade count | 50 |
| Current taker-buy volume | 140 |

During the candle, price visited 105 and 99, began at 101, and ended at 104. The open-to-close increase was 3, while the previous-close-to-current-close increase was 4. Those are different questions, which is why we keep both candle shape and return features.

## 2. What is a feature, and why are there 128?

A **feature** is a number calculated from the information available at a particular time. It translates raw observations into a question that is easier to compare across candles.

Examples:

- “How much has price changed over the last 20 candles?”
- “Is this candle's volume unusually large?”
- “Are recent returns more variable than earlier returns?”
- “Was most trading initiated by buyers or sellers?”

A **model** is a statistical method that learns relationships between such inputs and an outcome. A **target**, sometimes called a label, is the outcome we want it to learn to predict. A future return could be a target; the 128 historical features are the inputs. Our feature-generation code does not create a target or train a model.

| Group | Count | Main question |
| --- | ---: | --- |
| Price and candle shape | 12 | What happened to price, and what shape did the latest candle have? |
| Trend | 20 | Where is price relative to its recent baseline, and how consistently has it been moving? |
| Momentum | 24 | How have recent gains, losses, and short-versus-long price measures behaved? |
| Volatility | 20 | How large or variable have price movements been? |
| Volume and activity | 24 | How much trading occurred, and how does that compare with recent activity? |
| Distribution and regime changes | 16 | Have return asymmetry, extreme observations, or recent statistical behavior changed? |
| Trade flow and calendar | 12 | Which side initiated trading, how did activity relate to returns, and where are we in the day or week? |
| **Total** | **128** | **A description of recent market conditions from several perspectives** |

We use several versions of a measurement because different time scales can tell different stories. Price may be falling over five candles while remaining above its 160-period moving average.

However, 128 columns do not mean 128 independent sources of information. Many reuse the same prices and volumes. A larger feature list is not automatically a better model.

## 3. How to read windows and numerical values

### A window is a count of candles

A window of 20 usually means the latest 20 candles, including the current completed candle. In this dataset, those are one-minute candles. On hourly data, the same setting would mean 20 hourly candles.

The horizon in log_return_20 compares the current close with the close 20 candles earlier. In contrast, log_slope_20 fits a line through 20 closing-price observations. Those related definitions do not use identical sets of timestamps.

A **lag** is a delay. A lag of 3 compares something now with the corresponding measurement three candles earlier.

Most rolling calculations include the current completed candle. The relative-volume and volume-z-score baselines deliberately use the previous candles only, so the current observation does not change its own reference level.

### A moving average smooths a noisy series

An **average**, also called a mean, adds values and divides by their count. The average of 98, 100, and 102 is 100.

A **simple moving average (SMA)** gives the latest n observations equal weight and drops older observations entirely.

An **exponential moving average (EMA)** gives more weight to recent observations. Its period controls how quickly it reacts. An EMA with period 20 does not discard everything older than 20 candles; older information continues with declining weight.

Our EMA starts with the average of its first n valid inputs. Thereafter:

> New EMA = α × current value + (1 − α) × previous EMA, with α = 2 / (n + 1).

**Wilder smoothing**, used for RSI and ATR, follows the same recursive form but uses α = 1 / n. At the same period it responds more slowly than the ordinary EMA used here. Both need an initial average before their first value exists. [EMA reference](https://ta-lib.org/functions/ema.html)

This is why independently recalculating each monthly file would change some results. Our batch code calculates the entire instrument history and saves all months for each symbol in that symbol's own Parquet file. BTCUSDT and ETHUSDT have separate output files.

### Decimal ratios, indicator scores, and log values are different

| Type of value | Example | Interpretation |
| --- | --- | --- |
| Relative price distance | EMA distance = 0.02 | Price is 2% above that EMA |
| Relative activity ratio | Relative volume = 2 | Volume is twice its reference average |
| Indicator score | RSI = 75 | The RSI score is 75 on a 0–100 scale; this is not a 75% price gain |
| Standardized value | Volume z-score = 2 | Volume is two reference standard deviations above its reference mean |
| Correlation | Correlation = −0.6 | A negative historical linear association; not a 60% loss |
| Logarithm | Log variance ratio ≈ 0.693 | The variance ratio is approximately 2, because exp(0.693) ≈ 2 |
| Time coordinate | Daily sine = −1 | One coordinate describing a time of day; it has no negative-price meaning |

**Natural logarithm**, written ln or log in our code, is a mathematical transformation. Its inverse is exp. A useful property is that it turns multiplication into addition. All logarithms in this project are natural logarithms.

A log return of 0.01 is approximately a 1% simple return. The exact simple return is exp(0.01) − 1, approximately 1.005%. This distinction matters when changes become larger.

**Normalization** means dividing a measurement by a suitable reference. A price difference of 10 has a different meaning when price is 100 than when it is 50,000. Dividing by price expresses a relative size.

### A few statistical terms used later

| Term | Meaning |
| --- | --- |
| Deviation | A value minus a reference, often the mean |
| Absolute deviation | Distance from the reference, ignoring sign |
| Variance | Average squared distance from the mean |
| Standard deviation | Square root of variance; a measure of spread in the original measurement's units |
| Correlation | A measure of linear association between two lists of observations relative to their own means |
| Regression | Fitting a mathematical relationship, here a straight line through recent log prices |
| Distribution | How observations are spread across small, large, positive, and negative values |
| Regime | A description of a period's behavior, such as low or high variability; not a directly observed label in this code |

Our rolling variance and standard deviation divide by the window count n, rather than n − 1. This is called the population convention, or ddof = 0. The features are not annualized.

## 4. Price and candle shape — 12 features

### 4.1 Historical log returns — 8

**Settings:** horizons of 1, 2, 3, 5, 10, 20, 40, and 80 candles.

These answer: **How much has the closing price changed since an earlier close?**

> log_return_h = ln(current close / close h candles earlier).

Positive values mean the current close is higher; negative values mean it is lower; zero means unchanged. For example, a rise from 100 to 102 gives ln(102 / 100) ≈ 0.0198, corresponding to a 2% simple gain.

The short versions describe recent changes; the longer versions describe the net result over more candles. A model can compare them to distinguish, for example, a short drop during a longer rise.

A return records the start-to-end change, not the full path. Price might move sharply up and down and still finish near its starting point. These eight columns describe past changes, not eight future predictions.

### 4.2 Signed candle body — 1

**Column:** candle_body.

The body measures the movement from the current candle's open to its close:

> (current close − current open) / previous close.

A positive body means the candle closed above its open. A negative body means it closed below. In our teaching candle, (104 − 101) / 100 = 0.03.

Its purpose is to distinguish upward and downward movement within the latest interval. It differs from the historical one-candle return, which starts at the previous candle's close.

### 4.3 High–low range — 1

**Column:** candle_range.

> (current high − current low) / previous close.

This describes how far apart the candle's highest and lowest traded prices were, relative to the prior close. The example gives (105 − 99) / 100 = 0.06.

It supplies information about intrabar movement that a small body could hide. A large range does not indicate direction, and it is not the sum of every movement during the candle.

### 4.4 Upper and lower wicks — 2

**Columns:** upper_wick and lower_wick.

The upper wick is the distance between the high and the higher of open and close. The lower wick is the distance between the low and the lower of open and close.

> Upper wick = (high − max(open, close)) / previous close.  
> Lower wick = (min(open, close) − low) / previous close.

Our example gives an upper wick of (105 − 104) / 100 = 0.01 and a lower wick of (101 − 99) / 100 = 0.02.

These describe price excursions beyond the open–close body. They may help a model distinguish differently shaped candles that had the same net change. A wick alone does not establish a cause, a support/resistance level, or a future reversal.

For valid candles:

> candle_range = abs(candle_body) + upper_wick + lower_wick.

## 5. Trend — 20 features

“Trend” here describes how the recent price path and baselines behave. The features do not assume that a past trend will continue.

### 5.1 Distance from an EMA — 6

**Settings:** EMA periods 5, 10, 20, 40, 80, and 160.

> ema_distance_n = current close / EMA_n − 1.

If close is 102 and the EMA is 100, the distance is 0.02: price is 2% above that EMA. If close is 98, the distance is −0.02.

These columns tell a model whether price is above or below baselines that react at different speeds. A short-period distance can respond quickly while a long-period distance retains broader context.

The value is a distance, not the growth rate of the average. A positive distance does not by itself prove the EMA is rising. Period n is a smoothing setting, not a strict n-candle cutoff.

### 5.2 Distance from an SMA — 4

**Settings:** SMA windows 10, 20, 50, and 100.

> sma_distance_n = current close / average of the latest n closes − 1.

Interpret the sign and units just like EMA distance. The difference is the reference: every close in the SMA window gets equal weight.

Its purpose is to provide comparisons with fixed-window baselines. SMA and EMA distances often overlap in information, but respond differently when older observations leave the window or recent prices change sharply.

### 5.3 Slope of a line through log prices — 5

**Settings:** 10, 20, 40, 80, and 160 closing-price observations.

Imagine drawing the straight line that best fits recent log closing prices. Its **slope** measures how much that fitted line changes per candle. The fit includes an intercept, meaning it can start at whatever vertical level best fits the data.

A slope of 0.001 means the fitted log price rises by 0.001 per candle, approximately 0.1% per candle for such a small value. A negative slope describes a downward fitted line.

This gives a model a directional summary using the whole window rather than only its first and last prices. Using log prices makes the interpretation relative to price scale. It is a description of the fitted past path, not an expected future return.

### 5.4 How well that line fits: R² — 5

**Settings:** the same 10, 20, 40, 80, and 160 observations.

**R²**, read “R squared,” compares the straight-line fit with a baseline that simply uses the window's mean log price. Values nearer 1 mean the line accounts for more of the historical variation; values nearer 0 mean it accounts for little.

For example, R² = 0.9 means this line accounts for 90% of the variation around the mean log price within that fitted window. It does not mean a 90% chance of being right about the next candle.

Use R² with the slope. A smooth decline can have a negative slope and high R². A flat-price window has undefined ordinary R²; our implementation deliberately returns 0 for that special case.

The purpose is to distinguish a relatively consistent fitted path from a noisy one, not to assign forecasting confidence.

## 6. Momentum — 24 features

Momentum indicators summarize aspects of recent price movement, such as the balance between gains and losses or the relationship between faster and slower averages. They are numerical descriptions, not automatic buy/sell instructions.

### 6.1 Relative Strength Index (RSI) — 6

**Settings:** periods 5, 7, 14, 21, 28, and 42.

For each close-to-close change, separate its gain and loss components. Wilder-smooth both, then compute:

> RSI = 100 × smoothed gains / (smoothed gains + smoothed losses).

If smoothed gains are 3 and smoothed losses are 1, RSI is 75. Values lie between 0 and 100. A score above 50 indicates greater smoothed gains than losses; below 50 indicates the reverse. A value of 50 means balance, which can include both gains and losses rather than an unchanged price.

RSI gives the model a compact measure of the recent balance of upward and downward close changes. It uses price differences, not log returns. The period controls recursive smoothing, so older observations can still matter. [RSI definition](https://ta-lib.org/functions/rsi.html)

High RSI does not mean price must fall next. If both smoothed gains and losses are zero, our convention returns 50.

### 6.2 Fast stochastic K and D — 8

**Settings:** windows 7, 14, 21, and 28; each produces K and D.

This asks where the current close sits within the highest-high to lowest-low range of recent candles:

> K = 100 × (close − window low) / (window high − window low).  
> D = average of the latest three K values.

If the window low is 90, the high is 110, and close is 105, K is 75. The close is three quarters of the way up that price range. D smooths short-lived movements in K.

The purpose is to describe location within a recent price envelope and how that location has evolved. “Stochastic” is the indicator's name; this code is not generating random values. [Fast stochastic reference](https://ta-lib.org/functions/stochf.html)

K = 75 does not imply a 75% probability of a rise. A zero-range window receives K = 50 by our convention; D still requires three valid K values.

### 6.3 Commodity Channel Index (CCI) — 4

**Settings:** windows 10, 20, 40, and 80.

First calculate **typical price**, the average of high, low, and close. Then compare the current typical price with its window average:

> CCI = (typical price − its window mean) / (0.015 × window mean absolute deviation).

Mean absolute deviation is the average distance of the window's typical prices from that same window mean, ignoring signs. It is not standard deviation.

If current typical price is 102, its mean is 100, and mean absolute deviation is 1, CCI is 2 / 0.015 ≈ 133.3.

The purpose is to describe how far price has moved from its recent typical level relative to recent dispersion. The name does not restrict it to commodities. [CCI definition](https://ta-lib.org/functions/cci.html)

CCI is not restricted to a 0–100 range: values beyond +100 or −100 are possible. A score of 133.3 is not a 133.3% return. A constant typical-price window returns 0.

### 6.4 MACD line and histogram — 6

**Settings:** (fast, slow, signal) = (6,13,5), (12,26,9), and (24,52,18). Each gives two outputs.

MACD means **Moving Average Convergence/Divergence**. Subtract the slower EMA from the faster EMA to get the raw line. The signal is an EMA of that raw line. Our outputs are:

> MACD line feature = (fast EMA − slow EMA) / current close.  
> MACD histogram feature = (raw line − raw signal) / current close.

For fast EMA 102, slow EMA 100, raw signal 1.5, and close 104, the features are 2 / 104 ≈ 0.01923 and 0.5 / 104 ≈ 0.00481.

The line describes separation between faster and slower price baselines. The histogram describes whether that separation is above or below its own smoothed reference. [MACD definition](https://ta-lib.org/functions/macd.html)

Positive histogram does not necessarily mean price is rising or that the line is positive. “Histogram” names the indicator component; our output is one number per row. The signal is an internal calculation, not an extra output column.

## 7. Volatility — 20 features

Volatility concerns the size or variability of movement. It does not tell us whether price is moving up or down.

### 7.1 Standard deviation of returns — 6

**Settings:** windows 5, 10, 20, 40, 80, and 160.

For each window, calculate the standard deviation of one-candle log returns. The feature does not calculate standard deviation of price levels.

For a short teaching example, returns of +0.01 and −0.01 have mean 0 and population standard deviation 0.01. Returns that are all +0.01 have standard deviation 0, even though price is rising consistently.

The purpose is to help a model distinguish stable return behavior from returns spread over a wider range. A value of 0.002 corresponds roughly to 0.2% one-candle return variability when log returns are small. It is not a forecast interval, an annualized volatility, or an expected loss.

### 7.2 Normalized Average True Range (ATR) — 4

**Settings:** Wilder periods 7, 14, 28, and 56.

A candle's **true range** is the largest of three distances: high minus low, the absolute difference between high and previous close, and the absolute difference between low and previous close. Comparing with the previous close includes gaps between traded price levels. Wilder-smooth true range and divide by current close:

> Normalized ATR = smoothed true range / current close.

If smoothed true range is 2 and close is 100, the feature is 0.02: the smoothed range is 2% of the current price.

This supplies a range-based view of movement that includes intrabar extremes. It can differ from return volatility because close-to-close returns omit much of the movement inside a candle. [ATR definition](https://ta-lib.org/functions/atr.html)

A normalized ATR of 0.02 is not a prediction that the next candle will move 2%. The output is a fraction, not that fraction multiplied by 100.

### 7.3 Bollinger position and width — 8

**Settings:** windows 10, 20, 40, and 80; two outputs each.

Take the SMA of closes as the middle band. Put upper and lower bands two standard deviations of closing prices above and below it. These use the dispersion of **close levels**, unlike the return-volatility features. [Bollinger band definition](https://ta-lib.org/functions/bbands.html)

**Position** asks where close sits between the bands:

> Position = (close − lower band) / (upper band − lower band).

Position 0 is the lower band, 0.5 is the middle, and 1 is the upper band. It can be below 0 or above 1; the code does not clip it.

**Width** measures their total separation:

> Width = (upper band − lower band) / middle band.

With middle 100, lower 96, upper 104, and close 102, position is 6 / 8 = 0.75 and width is 8 / 100 = 0.08.

Together they describe relative location and envelope size. They are not probabilities. Flat closing prices receive position 0.5 and width 0.

### 7.4 Downside deviation — 2

**Settings:** windows 20 and 80.

Replace positive log returns with zero, square every resulting value, average across **all** candles in the window, then take the square root:

> Downside deviation = sqrt(average of min(return, 0)²).

In a four-return teaching example, [−0.02, +0.01, 0, −0.02] becomes [−0.02, 0, 0, −0.02]. Its downside deviation is sqrt(0.0008 / 4) ≈ 0.01414.

The purpose is to describe the size of recent negative-return observations while ordinary volatility considers departures on either side of the mean. This is not the standard deviation of just the losing candles, and it is not maximum drawdown. A window with no negative returns has downside deviation 0.

## 8. Volume and activity — 24 features

These features describe how much trading occurred and how activity relates to the candle's price behavior. The following OBV, MFI, and CMF features use price-based classifications; the later taker-buy feature uses the supplied trade-initiation field.

### 8.1 Relative volume — 6

**Settings:** previous-candle windows 5, 10, 20, 40, 80, and 160.

> Relative volume = current volume / average volume of the previous n candles.

If the previous 20 candles averaged 100 BTC and the current candle traded 250 BTC, relative_volume_20 is 2.5. That is 2.5 times the recent reference level. A value of 1 means equal to the reference; 0.5 means half.

These features give the model activity context across different baselines. The current candle is excluded from its reference mean. A ratio does not directly measure liquidity, and unusually large volume does not imply a particular price direction. A zero reference mean makes the feature missing.

### 8.2 Volume z-score — 4

**Settings:** previous-candle windows 20, 40, 80, and 160.

> Volume z-score = (current volume − previous-window mean) / previous-window standard deviation.

If current volume is 150, the reference mean is 100, and its standard deviation is 25, the result is 2.

This asks how unusual current activity is relative to the reference window's own variation. Doubling a very stable volume baseline may be more unusual than doubling a highly variable baseline.

A value of 2 means two standard deviations above the mean, not twice the mean and not a 2% increase. It is not automatically a probability or a statistical significance result. If reference volume is constant, its standard deviation is zero and the result is missing.

### 8.3 Rolling OBV balance — 4

**Settings:** windows 10, 20, 40, and 80.

OBV means **On-Balance Volume**. Our feature is a normalized rolling balance, rather than the usual cumulative OBV level:

> Add volume when close rises from the previous close, subtract it when close falls, and divide the window's signed total by its total volume.

Unchanged closes contribute zero to the numerator while their volume still counts in the denominator.

For 100 volume on a rising close, 60 on a falling close, and 40 on an unchanged close, the balance is (100 − 60 + 0) / 200 = 0.2.

The purpose is to summarize whether trading volume has been associated more with upward or downward close changes. It lies between −1 and +1. Price-change magnitude does not affect the sign assigned to volume. This does not reveal actual buyer-initiated volume or the number of buyers. A zero-volume window is undefined.

### 8.4 Money Flow Index (MFI) — 4

**Settings:** windows 7, 14, 28, and 56.

Calculate typical price = (high + low + close) / 3 and multiply it by volume. Classify that amount as positive when typical price rises from the prior candle, negative when it falls, and neither when unchanged.

> MFI = 100 × sum of positive amounts / sum of positive and negative amounts.

If the positive and negative totals are 300 and 100, MFI is 75. It is a 0–100 score combining price classification with activity size. [MFI reference](https://ta-lib.org/functions/mfi.html)

The purpose is to supply a volume-weighted comparison of upward and downward typical-price changes. It differs from RSI's smoothed gain/loss sizes.

Despite its name, it does not measure net money entering an asset or directly observe which side initiated trades. Typical price × volume is the indicator's approximation, not the source's actual quote-volume field. If both totals are zero, our convention returns 50.

### 8.5 Chaikin Money Flow (CMF) — 4

**Settings:** windows 10, 20, 40, and 80.

First score where each candle closes inside its own range:

> Candle score = (2 × close − high − low) / (high − low).

A close at the high gives +1, a close at the low gives −1, and a close at the midpoint gives 0. CMF is the window's volume-weighted average of these scores. [CMF reference](https://ta-lib.org/functions/cmf.html)

For high 110, low 100, and close 108, that candle's score is 0.6. If all scored candles in a window had this value, CMF would also be 0.6, regardless of their individual volumes.

Its purpose is to describe whether substantial trading activity occurred in candles that closed toward their tops or bottoms. It is a proxy based on candle position, not actual net cash flow or measured order imbalance.

A zero-range candle contributes a score of 0. A zero total-volume window yields a missing CMF.

### 8.6 Log-transformed volume and trade count — 2

**Columns:** log1p_volume and log1p_trade_count.

> log1p_volume = ln(1 + current volume).  
> log1p_trade_count = ln(1 + current trade count).

“log1p” means the natural logarithm of one plus the input. Adding 1 allows an input of zero. Taking the log compresses large values: volume 9 becomes ln(10) ≈ 2.303, while volume 99 becomes ln(100) ≈ 4.605.

These retain current activity level as information, alongside the comparisons with historical baselines. Trade count can help distinguish trading spread across many smaller executions from trading with fewer larger executions when considered with volume.

The transformation does not standardize either column. Volume still depends on asset units. A value of 4.605 is not 4.605 BTC or 4.605 trades; undo the transformation with exp(value) − 1.


## 9. Distribution and regime changes — 16 features

These features describe the **shape of recent price changes** and whether recent behavior differs from an earlier period. A “distribution” is simply the collection of values observed in a window: how many are small, large, positive, or negative. A “regime” means a period with a particular pattern, such as unusually turbulent price changes. Our calculations describe historical changes; they do not establish that a new lasting regime has begun.

All features in this group use one-candle **log returns**, `r = ln(current close / previous close)`. A log return of `0.01` corresponds to approximately a 1% gain. Every window size counts candles.

### Return skewness — 4 features

**Columns:** `return_skew_20`, `return_skew_40`, `return_skew_80`, `return_skew_160`.

**Meaning:** Skewness describes whether unusually large deviations from the average return are stronger on one side. Positive skew means larger positive deviations dominate; negative skew means larger negative deviations dominate. A value near zero suggests roughly balanced deviations, but does not prove that the entire distribution is symmetric.

**Calculation in words:** Find the window's average return. Subtract it from each return, cube each difference, and average those cubes. Divide by the cube of the window's standard deviation. Cubing preserves each difference's sign while giving large differences much more weight.

**Example:** In a 20-candle return window, suppose 16 returns are `−0.01` and four are `+0.04`. The mean is zero, and the calculated skewness is `+1.5`. Large positive moves outweigh the more frequent small negative moves in this particular measure. Positive skew therefore does **not** mean that most candles went up.

**Purpose:** A model receives information about the direction of unusually large historical moves, beyond the average return and overall volatility.

**Limitation:** One extreme candle can change skewness sharply, especially over 20 candles. This implementation uses an uncorrected moment formula, so another library's bias-corrected estimate can differ. An exactly constant-return window produces a missing value (`NaN`) because the denominator is zero.

### Return excess kurtosis — 4 features

**Columns:** `return_excess_kurtosis_20`, `return_excess_kurtosis_40`, `return_excess_kurtosis_80`, `return_excess_kurtosis_160`.

**Meaning:** This measures how strongly extreme deviations contribute relative to the window's ordinary variation. It can distinguish a window of fairly evenly sized changes from one containing mostly small changes and a few exceptional moves.

**Calculation in words:** Subtract the window's mean from every return. Average the fourth powers of those differences, divide by the square of the return variance, then subtract 3. Variance is the average squared distance from the mean; standard deviation is its square root. Fourth powers make large deviations particularly influential. Subtracting 3 gives the “excess” form, whose theoretical normal-distribution reference is zero. See the [NIST explanation of skewness and kurtosis](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35b.htm) for the underlying concepts.

**Example:** A 20-return window contains 18 zeros, one `+0.01`, and one `−0.01`. Its excess kurtosis is `7`: the two moves dominate the variation. Those absolute moves need not be huge for this value to be high; their size relative to the other observations matters.

**Purpose:** A model can distinguish recent variation dominated by occasional shocks from variation distributed more evenly across candles.

**Limitation:** High kurtosis does not identify which direction an extreme move will take. Negative values are possible and do not represent negative volatility. Short-window estimates are noisy; this implementation applies no sample-bias correction and returns `NaN` for constant returns.

### Return autocorrelation — 4 features

**Columns:** `return_autocorr_80_lag_1`, `return_autocorr_80_lag_2`, `return_autocorr_80_lag_3`, `return_autocorr_80_lag_5`.

**Meaning:** Correlation measures how two series move together relative to their own averages, on a scale from `−1` to `+1`. “Auto” means we compare a series with an earlier version of itself. A lag of 3 pairs each return with the return three candles earlier.

**Calculation in words:** Make 80 pairs: each of the latest 80 returns and its lagged counterpart. Subtract each list's own mean, multiply paired deviations, and sum the products. Divide by the square root of the product of the two sums of squared deviations. This is Pearson correlation.

There are **80 pairs for every lag**. Lag 5 needs 85 distinct returns, which require 86 closes. It does not discard five pairs from a single 80-return window.

**Example:** If returns alternate exactly between `+0.01` and `−0.01`, lag-1 autocorrelation is `−1`, while lag-2 autocorrelation is `+1`. The first comparison captures alternation; the second captures repetition every two candles.

**Purpose:** These columns describe recent serial relationships, giving a model candidate information about persistence or alternation at several lags.

**Limitation:** A historical correlation is not a guaranteed repeatable pattern. Zero means no detected linear relationship, not no possible relationship. A constant vector makes correlation undefined and produces `NaN`.

### Standardized mean change — 2 features

**Columns:** `mean_change_20`, `mean_change_80`.

**Meaning:** This compares the average return in the most recent window with the average in the immediately preceding, non-overlapping window. For `mean_change_20`, compare the latest 20 returns with the 20 before them.

**Calculation:**

`(recent mean − previous mean) / sqrt((recent variance + previous variance) / 2)`

The denominator is the pooled standard-deviation scale used here: average the two population variances, then take the square root. It is not the standard deviation of the combined returns, and it is not an uncertainty estimate for the difference in means.

**Example:** The recent mean is `0.002`, the previous mean is `−0.001`, and both windows have standard deviation `0.01`. The feature is `(0.002 − (−0.001)) / 0.01 = 0.3`. The average return increased by 0.3 pooled standard deviations.

**Purpose:** A model can compare shifts in recent average returns on a scale that accounts for recent variability.

**Limitation:** Positive means “the average increased,” which can include moving from a larger loss to a smaller loss. This is not a statistical significance test or probability. A zero pooled variance produces `NaN`. The 80-candle version needs 160 returns, or 161 closes.

### Log variance ratio — 2 features

**Columns:** `log_variance_ratio_20`, `log_variance_ratio_80`.

**Meaning:** These use the same two adjacent windows and ask whether returns have become more or less variable.

**Calculation:** Take the **natural logarithm** of `recent variance / previous variance`.

**Example:** Previous variance is `0.000001` and recent variance is `0.000004`. The result is `ln(4) ≈ 1.386`. Variance quadrupled, while standard deviation doubled. This is a log **variance** ratio, not a log standard-deviation ratio.

**Purpose:** A model receives a direct measure of expanding or contracting historical return variability. Positive means expansion, zero means equal variances, and negative means contraction.

**Limitation:** A tiny previous variance can produce a large ratio. Either variance being zero makes this feature `NaN`. It describes a historical comparison rather than proving a lasting change.

## 10. Trade flow and calendar — 12 features

### Taker-buy imbalance — 5 features

**Columns:** `taker_buy_imbalance_1`, `taker_buy_imbalance_5`, `taker_buy_imbalance_10`, `taker_buy_imbalance_20`, `taker_buy_imbalance_40`.

**Meaning:** Every completed trade has a buyer and seller. This feature distinguishes which side initiated the trade by taking available liquidity. Taker-buy volume is the traded quantity where the buyer was the taker; the remaining classified volume is seller-initiated.

**Calculation:** Sum taker-buy base-asset volume and total base-asset volume over the window, then calculate:

`(2 × total taker-buy volume − total volume) / total volume`

Equivalently, divide buyer-initiated volume minus seller-initiated volume by total volume. Aggregate quantities **before** dividing; this is not an equally weighted average of candle imbalances.

**Example:** A window contains 10 BTC of total volume, including 6.5 BTC of taker-buy volume. Seller-initiated volume is 3.5 BTC, giving `(6.5 − 3.5) / 10 = +0.30`. Taker buys represent 65% of volume. The feature itself is neither 30% taker buys nor a 30% price gain.

**Purpose:** This supplies an observed trade-initiation measure that candle shape and closing prices cannot reconstruct. The one-candle version captures the latest candle; longer windows aggregate more trading activity.

**Limitation:** Both quantities must use the same base-asset units, such as BTC for BTC/USDT; never mix BTC volume with USDT volume. Values range from `−1` to `+1`. Positive does not mean there were more buyers than sellers or guarantee a price rise. Missing taker-buy data or zero total volume produces `NaN`.

### Return–volume correlation — 3 features

**Columns:** `return_volume_corr_20`, `return_volume_corr_40`, `return_volume_corr_80`.

**Meaning:** These measure whether higher trading activity coincided with higher or lower **signed** returns within the window.

**Calculation in words:** Pair each one-candle log return with `ln(1 + volume)` from the same candle, then calculate Pearson correlation. The log transform compresses large volume values; adding 1 allows zero volume. Volume remains in consistent base-asset units.

**Example:** In a simplified 20-candle window, ten candles have return `−0.001` and volume 1, and ten have return `+0.001` and volume 3. The two paired levels give correlation `+1`. Reversing the volume assignments gives `−1`. Real windows usually contain many values and less extreme correlations.

**Purpose:** A model can distinguish recent high-activity rising candles from high-activity falling candles, rather than receiving volume and returns only as separate summaries.

**Limitation:** This measures simultaneous association; it does not establish that volume caused the return or leads the next return. It uses signed returns, so it does not directly measure whether busy candles had larger moves in either direction. A constant return or log-volume series produces `NaN`.

### Cyclical time — 4 features

**Columns:** `time_of_day_sin`, `time_of_day_cos`, `time_of_week_sin`, `time_of_week_cos`.

**Meaning:** Time wraps around: midnight follows the end of the day, and Monday follows Sunday. Ordinary numbers make the end and beginning look far apart. Sine and cosine place time on a circle, preserving their closeness at that boundary. Use each pair together.

**Calculation in words:** Use the candle's **exclusive closing boundary in UTC**. For example, a one-minute candle beginning at `12:00:00` ends at `12:01:00`. Divide seconds since UTC midnight, including fractions, by 86,400 to obtain the day fraction. For the week fraction, add that day fraction to the weekday number (Monday 0 through Sunday 6) and divide by 7. For each fraction, multiply by `2π` and take sine and cosine.

**Example:** The daily `(sin, cos)` pair is approximately `(0, 1)` at midnight, `(1, 0)` at 06:00, `(0, −1)` at noon, and `(−1, 0)` at 18:00 UTC. Monday midnight has weekly pair `(0, 1)`. The weekly features change continuously within each day rather than assigning one fixed value to each weekday.

**Purpose:** A model can test whether behavior differs systematically by time of day or week, such as recurring changes in trading activity.

**Limitation:** These columns encode the clock, not holidays, events, or market-session rules. UTC does not follow local daylight-saving changes. On daily candles ending at one fixed time, daily features are constant. Negative sine or cosine has no bearish meaning; it is simply a coordinate on the time circle.

## 11. Reading several features together

Different features answer different questions. They do not have to agree on a single “up” or “down” message.

Consider this **hypothetical snapshot**, provided only to practice interpretation:

| Feature | Value | What we can say about the observed data |
| --- | ---: | --- |
| log_return_5 | −0.002 | The close fell approximately 0.2% over five candles |
| ema_distance_80 | 0.012 | The current close is 1.2% above the 80-period EMA |
| log_slope_80 | 0.00015 | The fitted historical log-price line rises about 0.015% per candle |
| log_r2_80 | 0.8 | That line accounts for 80% of the window's variation around mean log price |
| return_vol_20 | 0.0015 | Recent one-candle log returns have standard deviation 0.0015 |
| relative_volume_20 | 3 | Current volume is three times the previous 20-candle average |
| taker_buy_imbalance_5 | 0.4 | Buyer-initiated volume is 70% of the five-candle total |
| time_of_day_sin and time_of_day_cos | A coordinate pair | The observation occurred at a particular UTC clock position |

A short recent decline can coexist with a price above its longer baseline and a positive longer fitted slope. Elevated activity can accompany either a rise or a fall. Buyer-initiated volume is not a guarantee that the price rose, because this feature does not describe order-book depth, the sequence of trades, or each trade's price impact.

The model would receive these descriptions together. It would need a separately defined future outcome and historical examples to learn whether any combination is useful. Reading a plausible story from one row is not evidence that the story predicts the next row.

### Checking a few values by hand

Return to the teaching candle from Section 1: previous close 100, open 101, high 105, low 99, close 104, volume 200, 50 trades, and taker-buy volume 140.

| Output | Hand calculation | Result |
| --- | --- | ---: |
| log_return_1 | ln(104 / 100) | ≈ 0.039221 |
| candle_body | (104 − 101) / 100 | 0.03 |
| candle_range | (105 − 99) / 100 | 0.06 |
| upper_wick | (105 − 104) / 100 | 0.01 |
| lower_wick | (101 − 99) / 100 | 0.02 |
| taker_buy_imbalance_1 | (2 × 140 − 200) / 200 | 0.4 |
| log1p_volume | ln(201) | ≈ 5.303305 |
| log1p_trade_count | ln(51) | ≈ 3.931826 |

This example supplies enough information for these outputs. It does not supply the longer historical windows required for RSI, moving averages, or most other features. If this were literally the first recorded candle without a previous close, the return and normalized candle-shape values would also be missing.

## 12. Understanding the generated files and missing values

### One row is a description at one completed candle

The sample's candle starting at 00:00 UTC covers the minute up to, but not including, 00:01. Its feature row uses the complete candle and has candle_end_utc = 00:01 UTC. It cannot be used as if the entire candle were known at 00:00.

The extractor's `close_time` (the reference schema's `close_time_ms`) is the inclusive last millisecond, while `feature_available_at` (`completed_at_ms`) is the following boundary. The batch reader checks these conventions. Calendar features use the completion boundary. This boundary is the earliest possible availability; actual publication or feed arrival can be later and is not measured by these archive timestamps.

Most features require current-candle prices or volume. Therefore, the row describes conditions after those observations become available. The fact that the clock can be known in advance does not make the other 124 features available earlier.

### The 130 output columns

Each feature Parquet contains exactly 128 numerical features plus `candle_end_utc` and `symbol`. These two identifying columns locate the completed candle and instrument.

Use the generated `feature_columns.json` to select the intended model inputs. Fields such as `market`, `feature_available_at`, `input_file`, exchange, interval and duplicate timestamps are not exported in feature rows. The reader still uses and validates source metadata; `summary.json` records input paths and series identities separately.

This guide's final dictionary uses the same feature order as feature_manifest_128.json and the generated feature_columns.json.

### Why some cells are blank

A missing numerical value is called **NaN**, meaning “not a number,” or is stored as a null in Parquet. It does not mean zero.

There are several distinct reasons for missing values:

| Reason | Example | Meaning |
| --- | --- | --- |
| Insufficient history | return_vol_160 near the beginning of the dataset | We do not yet have 160 one-candle returns |
| Undefined denominator | Volume z-score after constant reference volume | Reference standard deviation is zero |
| Constant series | Return correlation when every return is identical | Correlation cannot be calculated for that vector |
| Zero total volume | Taker-buy imbalance in a zero-volume window | There is no traded quantity to divide by |
| Absent inputs in generic use | No taker-buy field supplied to the generic engine | Five flow features cannot be observed |

The raw batch reader for your dataset requires all seven numerical source fields and rejects missing or malformed required inputs. The generic engine can leave optional trade fields unavailable. Neither silently fabricates them.

### Why the first complete row is normally candle 161

A window of 160 returns requires 161 closing prices, because each return compares one close with the previous close. The longest relative-volume baseline also needs 160 earlier volumes plus the current candle.

If there is no earlier history and the observations are nondegenerate, the first 160 rows are not fully populated across all features. Some individual columns become available much sooner.

For the original January 2022 reference sample, the imported project reported:

| Check | Verified result |
| --- | ---: |
| Input and output candles | 44,640 |
| Required feature definitions supported | 128 |
| Initial rows without all features populated | 160 |
| Rows with all 128 feature values populated | 44,480 |
| First complete feature-row timestamp | 2022-01-01 02:41:00 UTC |
| Gaps in the sample | 0 |

These historical sample figures do not describe the current extractor files. Check your generated `summary.json` for the results of each local run. Availability at candle 161 is not a claim that every recursively smoothed value has become insensitive to its starting point.

If earlier candles are available, include them as warm-up history before selecting the dates you want to study. Do not fill early rows with later values: that would use information from the future.

### What happens at a missing minute or a month boundary?

A source-file or calendar-month boundary is only a storage boundary. The code joins and sorts an instrument's complete input history before calculating, so February retains January's earlier information.

A genuinely missing candle is different. The default policy restarts calculations after a gap, so a new warm-up period may follow. The explicit rows policy instead counts observed rows across gaps; this changes the time interpretation and is recorded in the run summary.

Series are identified by exchange, product, symbol, interval, and price family while reading the inputs. Each output file holds one symbol's history. Because only the symbol and completion time remain as identifying columns, select one series per symbol in a run; inputs containing multiple markets, intervals or price families for the same symbol are rejected. Multiple non-overlapping source files from the same series are supported.

### A few values are deliberately defined for otherwise ambiguous cases

| Case | Our convention |
| --- | --- |
| RSI has zero smoothed gains and zero smoothed losses | 50 |
| Stochastic window has zero high–low range | K = 50 |
| CCI has constant typical price | 0 |
| Regression window has constant price | Slope = 0 and R² = 0 |
| Bollinger bands have zero width | Position = 0.5 and width = 0 |
| MFI positive and negative flow totals are both zero | 50 |
| CMF candle has zero range | That candle's position multiplier = 0 |

These conventions apply only when the necessary data exist. They do not turn a missing input or an incomplete window into a valid observation. Some indicator libraries use different conventions, so matching a familiar indicator name alone does not guarantee identical values.

### What has and has not been established

The implementation was checked against hand calculations, numerical properties, future-data perturbations, timestamp rules, and continuity across files and symbols. It was also run on the supplied sample.

Those checks establish calculation behavior; they do not establish predictive value. There is no trained model or trading decision rule in this package.

When evaluating a model, define its future target separately, keep training earlier than validation, and learn scaling, imputation, and feature selection from training data only. If a training label extends into the validation period, that example must be handled so it does not reveal validation information. The final specification describes this timing issue further.

The feature set also does not directly contain an order book, bid–ask spread, funding rate, open interest, news, or execution costs. A volume feature is not a substitute for those measurements.

## 13. Complete dictionary of all 128 columns

The table below names every output feature individually. “Period” means the smoothing setting for EMA, RSI, ATR, and MACD; it is not a strict cutoff of all older information. Other n-candle windows follow the definitions above. On the supplied one-minute data, candle counts map to one-minute bars.

For repeated feature families, the calculation and purpose are the same; the horizon, window, or smoothing setting changes.

| ID | Column | What this exact column describes |
| ---: | --- | --- |
| 1 | `log_return_1` | Net log price change from the close 1 candle(s) earlier to the current close. |
| 2 | `log_return_2` | Net log price change from the close 2 candle(s) earlier to the current close. |
| 3 | `log_return_3` | Net log price change from the close 3 candle(s) earlier to the current close. |
| 4 | `log_return_5` | Net log price change from the close 5 candle(s) earlier to the current close. |
| 5 | `log_return_10` | Net log price change from the close 10 candle(s) earlier to the current close. |
| 6 | `log_return_20` | Net log price change from the close 20 candle(s) earlier to the current close. |
| 7 | `log_return_40` | Net log price change from the close 40 candle(s) earlier to the current close. |
| 8 | `log_return_80` | Net log price change from the close 80 candle(s) earlier to the current close. |
| 9 | `candle_body` | Signed open-to-close body of the latest candle, divided by the previous close. |
| 10 | `candle_range` | Latest high-to-low range, divided by the previous close. |
| 11 | `upper_wick` | Latest excursion above the open-close body, divided by the previous close. |
| 12 | `lower_wick` | Latest excursion below the open-close body, divided by the previous close. |
| 13 | `ema_distance_5` | Current close's relative distance above or below an EMA with smoothing period 5. |
| 14 | `ema_distance_10` | Current close's relative distance above or below an EMA with smoothing period 10. |
| 15 | `ema_distance_20` | Current close's relative distance above or below an EMA with smoothing period 20. |
| 16 | `ema_distance_40` | Current close's relative distance above or below an EMA with smoothing period 40. |
| 17 | `ema_distance_80` | Current close's relative distance above or below an EMA with smoothing period 80. |
| 18 | `ema_distance_160` | Current close's relative distance above or below an EMA with smoothing period 160. |
| 19 | `sma_distance_10` | Current close's relative distance above or below the mean of the latest 10 closes. |
| 20 | `sma_distance_20` | Current close's relative distance above or below the mean of the latest 20 closes. |
| 21 | `sma_distance_50` | Current close's relative distance above or below the mean of the latest 50 closes. |
| 22 | `sma_distance_100` | Current close's relative distance above or below the mean of the latest 100 closes. |
| 23 | `log_slope_10` | Direction and per-candle slope of the straight line fitted to the latest 10 log closing prices. |
| 24 | `log_slope_20` | Direction and per-candle slope of the straight line fitted to the latest 20 log closing prices. |
| 25 | `log_slope_40` | Direction and per-candle slope of the straight line fitted to the latest 40 log closing prices. |
| 26 | `log_slope_80` | Direction and per-candle slope of the straight line fitted to the latest 80 log closing prices. |
| 27 | `log_slope_160` | Direction and per-candle slope of the straight line fitted to the latest 160 log closing prices. |
| 28 | `log_r2_10` | Historical straight-line fit quality over 10 log closing prices; not forecast confidence. |
| 29 | `log_r2_20` | Historical straight-line fit quality over 20 log closing prices; not forecast confidence. |
| 30 | `log_r2_40` | Historical straight-line fit quality over 40 log closing prices; not forecast confidence. |
| 31 | `log_r2_80` | Historical straight-line fit quality over 80 log closing prices; not forecast confidence. |
| 32 | `log_r2_160` | Historical straight-line fit quality over 160 log closing prices; not forecast confidence. |
| 33 | `rsi_5` | Balance of gains and losses with Wilder smoothing period 5, on a 0–100 scale. |
| 34 | `rsi_7` | Balance of gains and losses with Wilder smoothing period 7, on a 0–100 scale. |
| 35 | `rsi_14` | Balance of gains and losses with Wilder smoothing period 14, on a 0–100 scale. |
| 36 | `rsi_21` | Balance of gains and losses with Wilder smoothing period 21, on a 0–100 scale. |
| 37 | `rsi_28` | Balance of gains and losses with Wilder smoothing period 28, on a 0–100 scale. |
| 38 | `rsi_42` | Balance of gains and losses with Wilder smoothing period 42, on a 0–100 scale. |
| 39 | `stoch_k_7` | Current close's 0–100 position inside the latest 7-candle high–low envelope. |
| 40 | `stoch_d_7` | Three-candle average of stoch_k_7; a smoothed version of that range-position score. |
| 41 | `stoch_k_14` | Current close's 0–100 position inside the latest 14-candle high–low envelope. |
| 42 | `stoch_d_14` | Three-candle average of stoch_k_14; a smoothed version of that range-position score. |
| 43 | `stoch_k_21` | Current close's 0–100 position inside the latest 21-candle high–low envelope. |
| 44 | `stoch_d_21` | Three-candle average of stoch_k_21; a smoothed version of that range-position score. |
| 45 | `stoch_k_28` | Current close's 0–100 position inside the latest 28-candle high–low envelope. |
| 46 | `stoch_d_28` | Three-candle average of stoch_k_28; a smoothed version of that range-position score. |
| 47 | `cci_10` | Current typical price's scaled deviation from its 10-candle mean using mean absolute deviation. |
| 48 | `cci_20` | Current typical price's scaled deviation from its 20-candle mean using mean absolute deviation. |
| 49 | `cci_40` | Current typical price's scaled deviation from its 40-candle mean using mean absolute deviation. |
| 50 | `cci_80` | Current typical price's scaled deviation from its 80-candle mean using mean absolute deviation. |
| 51 | `macd_line_6_13_5` | EMA(6) minus EMA(13), divided by close; part of the setting with signal period 5. |
| 52 | `macd_hist_6_13_5` | Raw EMA(6)−EMA(13) line minus its EMA(5) signal, then divided by close. |
| 53 | `macd_line_12_26_9` | EMA(12) minus EMA(26), divided by close; part of the setting with signal period 9. |
| 54 | `macd_hist_12_26_9` | Raw EMA(12)−EMA(26) line minus its EMA(9) signal, then divided by close. |
| 55 | `macd_line_24_52_18` | EMA(24) minus EMA(52), divided by close; part of the setting with signal period 18. |
| 56 | `macd_hist_24_52_18` | Raw EMA(24)−EMA(52) line minus its EMA(18) signal, then divided by close. |
| 57 | `return_vol_5` | Population standard deviation of the latest 5 one-candle log returns. |
| 58 | `return_vol_10` | Population standard deviation of the latest 10 one-candle log returns. |
| 59 | `return_vol_20` | Population standard deviation of the latest 20 one-candle log returns. |
| 60 | `return_vol_40` | Population standard deviation of the latest 40 one-candle log returns. |
| 61 | `return_vol_80` | Population standard deviation of the latest 80 one-candle log returns. |
| 62 | `return_vol_160` | Population standard deviation of the latest 160 one-candle log returns. |
| 63 | `atr_normalized_7` | Wilder-smoothed true range with period 7, divided by the current close. |
| 64 | `atr_normalized_14` | Wilder-smoothed true range with period 14, divided by the current close. |
| 65 | `atr_normalized_28` | Wilder-smoothed true range with period 28, divided by the current close. |
| 66 | `atr_normalized_56` | Wilder-smoothed true range with period 56, divided by the current close. |
| 67 | `bb_position_10` | Close's location between the 10-candle lower and upper Bollinger bands; may lie outside 0–1. |
| 68 | `bb_width_10` | Total width of the 10-candle Bollinger bands, divided by their middle SMA. |
| 69 | `bb_position_20` | Close's location between the 20-candle lower and upper Bollinger bands; may lie outside 0–1. |
| 70 | `bb_width_20` | Total width of the 20-candle Bollinger bands, divided by their middle SMA. |
| 71 | `bb_position_40` | Close's location between the 40-candle lower and upper Bollinger bands; may lie outside 0–1. |
| 72 | `bb_width_40` | Total width of the 40-candle Bollinger bands, divided by their middle SMA. |
| 73 | `bb_position_80` | Close's location between the 80-candle lower and upper Bollinger bands; may lie outside 0–1. |
| 74 | `bb_width_80` | Total width of the 80-candle Bollinger bands, divided by their middle SMA. |
| 75 | `downside_deviation_20` | Root mean squared negative log return over 20 candles, with positive returns replaced by zero. |
| 76 | `downside_deviation_80` | Root mean squared negative log return over 80 candles, with positive returns replaced by zero. |
| 77 | `relative_volume_5` | Current volume divided by mean volume in the previous 5 candles, excluding the current candle. |
| 78 | `relative_volume_10` | Current volume divided by mean volume in the previous 10 candles, excluding the current candle. |
| 79 | `relative_volume_20` | Current volume divided by mean volume in the previous 20 candles, excluding the current candle. |
| 80 | `relative_volume_40` | Current volume divided by mean volume in the previous 40 candles, excluding the current candle. |
| 81 | `relative_volume_80` | Current volume divided by mean volume in the previous 80 candles, excluding the current candle. |
| 82 | `relative_volume_160` | Current volume divided by mean volume in the previous 160 candles, excluding the current candle. |
| 83 | `volume_zscore_20` | Current volume's standardized distance from the previous 20-candle volume mean. |
| 84 | `volume_zscore_40` | Current volume's standardized distance from the previous 40-candle volume mean. |
| 85 | `volume_zscore_80` | Current volume's standardized distance from the previous 80-candle volume mean. |
| 86 | `volume_zscore_160` | Current volume's standardized distance from the previous 160-candle volume mean. |
| 87 | `obv_balance_10` | Volume signed by close-change direction, summed over 10 candles and divided by total volume. |
| 88 | `obv_balance_20` | Volume signed by close-change direction, summed over 20 candles and divided by total volume. |
| 89 | `obv_balance_40` | Volume signed by close-change direction, summed over 40 candles and divided by total volume. |
| 90 | `obv_balance_80` | Volume signed by close-change direction, summed over 80 candles and divided by total volume. |
| 91 | `mfi_7` | 0–100 share of classified typical-price-times-volume amounts on rising typical prices over 7 candles. |
| 92 | `mfi_14` | 0–100 share of classified typical-price-times-volume amounts on rising typical prices over 14 candles. |
| 93 | `mfi_28` | 0–100 share of classified typical-price-times-volume amounts on rising typical prices over 28 candles. |
| 94 | `mfi_56` | 0–100 share of classified typical-price-times-volume amounts on rising typical prices over 56 candles. |
| 95 | `cmf_10` | Volume-weighted average of within-candle close-position scores over 10 candles. |
| 96 | `cmf_20` | Volume-weighted average of within-candle close-position scores over 20 candles. |
| 97 | `cmf_40` | Volume-weighted average of within-candle close-position scores over 40 candles. |
| 98 | `cmf_80` | Volume-weighted average of within-candle close-position scores over 80 candles. |
| 99 | `log1p_volume` | Natural log of one plus current base-asset volume; a compressed activity level. |
| 100 | `log1p_trade_count` | Natural log of one plus the current number of trades; a compressed execution-count level. |
| 101 | `return_skew_20` | Asymmetry of deviations from mean log return over 20 candles. |
| 102 | `return_excess_kurtosis_20` | Relative influence of extreme log-return deviations over 20 candles, using excess kurtosis. |
| 103 | `return_skew_40` | Asymmetry of deviations from mean log return over 40 candles. |
| 104 | `return_excess_kurtosis_40` | Relative influence of extreme log-return deviations over 40 candles, using excess kurtosis. |
| 105 | `return_skew_80` | Asymmetry of deviations from mean log return over 80 candles. |
| 106 | `return_excess_kurtosis_80` | Relative influence of extreme log-return deviations over 80 candles, using excess kurtosis. |
| 107 | `return_skew_160` | Asymmetry of deviations from mean log return over 160 candles. |
| 108 | `return_excess_kurtosis_160` | Relative influence of extreme log-return deviations over 160 candles, using excess kurtosis. |
| 109 | `return_autocorr_80_lag_1` | Pearson correlation across 80 pairs of one-candle log returns separated by 1 candle(s). |
| 110 | `return_autocorr_80_lag_2` | Pearson correlation across 80 pairs of one-candle log returns separated by 2 candle(s). |
| 111 | `return_autocorr_80_lag_3` | Pearson correlation across 80 pairs of one-candle log returns separated by 3 candle(s). |
| 112 | `return_autocorr_80_lag_5` | Pearson correlation across 80 pairs of one-candle log returns separated by 5 candle(s). |
| 113 | `mean_change_20` | Difference between recent and previous non-overlapping 20-return means, scaled by pooled standard deviation. |
| 114 | `log_variance_ratio_20` | Natural log of recent 20-return variance divided by the previous non-overlapping window's variance. |
| 115 | `mean_change_80` | Difference between recent and previous non-overlapping 80-return means, scaled by pooled standard deviation. |
| 116 | `log_variance_ratio_80` | Natural log of recent 80-return variance divided by the previous non-overlapping window's variance. |
| 117 | `taker_buy_imbalance_1` | Buyer-initiated minus seller-initiated base volume, divided by total volume over 1 candle(s). |
| 118 | `taker_buy_imbalance_5` | Buyer-initiated minus seller-initiated base volume, divided by total volume over 5 candle(s). |
| 119 | `taker_buy_imbalance_10` | Buyer-initiated minus seller-initiated base volume, divided by total volume over 10 candle(s). |
| 120 | `taker_buy_imbalance_20` | Buyer-initiated minus seller-initiated base volume, divided by total volume over 20 candle(s). |
| 121 | `taker_buy_imbalance_40` | Buyer-initiated minus seller-initiated base volume, divided by total volume over 40 candle(s). |
| 122 | `return_volume_corr_20` | Contemporaneous Pearson correlation of signed log returns and log1p(volume) across 20 candles. |
| 123 | `return_volume_corr_40` | Contemporaneous Pearson correlation of signed log returns and log1p(volume) across 40 candles. |
| 124 | `return_volume_corr_80` | Contemporaneous Pearson correlation of signed log returns and log1p(volume) across 80 candles. |
| 125 | `time_of_day_sin` | Sine coordinate of the UTC candle-end position within the day; use with its cosine partner. |
| 126 | `time_of_day_cos` | Cosine coordinate of the UTC candle-end position within the day; use with its sine partner. |
| 127 | `time_of_week_sin` | Sine coordinate of the UTC candle-end position within the week beginning Monday; includes fractional day. |
| 128 | `time_of_week_cos` | Cosine coordinate of the UTC candle-end position within the week beginning Monday; includes fractional day. |
