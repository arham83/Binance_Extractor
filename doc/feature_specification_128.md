# Proposed specification: 128 candle features

Version 1.0 • Prepared 17 September 2026

This specification makes the supplied seven-group feature plan reproducible. The lookback windows, normalizations, and edge-case conventions below are proposed defaults, not settings established by a performance study. The implemented calculations were checked on the supplied January 2022 sample. No prediction target or model performance study has been evaluated, so predictive value has not been established.

The full ordered column registry is also available in [feature_manifest_128.json](../feature_eng/feature_manifest_128.json).

| Feature group | Count |
| --- | ---: |
| Price and candle shape | 12 |
| Trend | 20 |
| Momentum | 24 |
| Volatility | 20 |
| Volume and activity | 24 |
| Distribution and regime changes | 16 |
| Trade flow and calendar | 12 |
| **Total** | **128** |

## Data and timing

Assume a single instrument, regularly spaced intraday candles, positive prices, and timestamps normalized to UTC. A window of 20 means 20 candles, not necessarily 20 minutes or days. The intraday assumption matters: time-of-day features become constant on daily data.

Each row needs open \(O_t\), high \(H_t\), low \(L_t\), close \(C_t\), base-asset volume \(V_t\), trade count \(N_t\), taker-buy base-asset volume \(B_t\), and the candle's exclusive closing boundary \(T_t\). Use the same volume units for \(B\) and \(V\). Require \(L_t\leq O_t,C_t\leq H_t\), \(V_t\geq0\), integer \(N_t\geq0\), and \(0\leq B_t\leq V_t\).

OHLCV alone cannot determine taker-buy volume or the number of trades. For example, Binance's regular kline response provides trade count and taker-buy base volume as separate fields. [Binance market-data schema](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)

Compute a feature row only once candle \(t\) is complete and its data have arrived. Its price/volume inputs must end at \(t\). Use the row for predictions or decisions afterward; a backtest must account for when an order could actually execute. If predicting at the start of candle \(t\), use the completed feature row from \(t-1\).

Use the exclusive end of the interval for \(T_t\), rather than an exchange's inclusive final millisecond, so calendar features align to exact boundaries. Compute each instrument separately. Reject duplicate timestamps and malformed candles. Treat gaps or missing required inputs as breaks in the affected calculations: restart their rolling history and recursive smoothing after the break. Do not silently compress gaps into adjacent candles.

The dataset pipeline rejects missing or nonfinite required OHLCV cells rather
than silently repairing or dropping them. Resolve invalid rows explicitly before
running it. With `gap_policy: reset`, missing timestamps then restart all indicator
history. The low-level `compute_features` function accepts one already validated,
contiguous segment. Missing optional trade-count or taker-buy inputs remain missing
only in the features and windows that depend on them.

## Shared definitions

All logs are natural logs. Let

\[
r_t=\log(C_t/C_{t-1}), \qquad P_t=(H_t+L_t+C_t)/3.
\]

For a series \(x\), define the current trailing window
\(W_n(t)=\{t-n+1,\ldots,t\}\), its mean \(\mu_n(x)_t\), and population variance

\[
v_n(x)_t=\frac1n\sum_{i\in W_n(t)}(x_i-\mu_n(x)_t)^2,
\qquad \sigma_n(x)_t=\sqrt{v_n(x)_t}.
\]

All such windows require all \(n\) valid observations. Use ddof = 0, no annualization, and no centered windows. The previous non-overlapping window of the same size ends at \(t-n\).

Define \(\operatorname{EMA}_n(x)\) with \(\alpha=2/(n+1)\), seeded by the arithmetic mean of the first \(n\) consecutive valid inputs. Continue with \(E_t=\alpha x_t+(1-\alpha)E_{t-1}\). Wilder smoothing, \(\operatorname{RMA}_n(x)\), uses the same seeding rule and \(\alpha=1/n\). Initial values before the seed are missing. [EMA definition](https://ta-lib.org/functions/ema.html)

Unless an exception is stated below, undefined ratios, correlations, or moments produce missing values (NaN), never infinity or an arbitrary epsilon-adjusted result. Missing inputs and insufficient history are also NaN. The explicit neutral values below apply only to fully observed, degenerate windows; they do not replace missing data.

## Price and candle shape — 12

Eight historical returns use \(h\in\{1,2,3,5,10,20,40,80\}\):

\[
\text{log\_return}_h(t)=\log(C_t/C_{t-h}).
\]

Four candle dimensions use the previous close as their common normalizer:

\[
\begin{aligned}
\text{candle\_body}_t&=(C_t-O_t)/C_{t-1},\\
\text{candle\_range}_t&=(H_t-L_t)/C_{t-1},\\
\text{upper\_wick}_t&=(H_t-\max(O_t,C_t))/C_{t-1},\\
\text{lower\_wick}_t&=(\min(O_t,C_t)-L_t)/C_{t-1}.
\end{aligned}
\]

The body is signed. For valid data, range equals absolute body plus both wicks.

## Trend — 20

Six EMA distances use \(n\in\{5,10,20,40,80,160\}\):
\[
\text{ema\_distance}_n=C_t/\operatorname{EMA}_n(C)_t-1.
\]

Four SMA distances use \(n\in\{10,20,50,100\}\):
\[
\text{sma\_distance}_n=C_t/\mu_n(C)_t-1.
\]

Five regression slopes and five regression \(R^2\) values use \(n\in\{10,20,40,80,160\}\). Within each window, regress \(y_j=\log C_{t-n+1+j}\) on \(x_j=j\), for \(j=0,\ldots,n-1\), with an intercept:

\[
b=\frac{\sum_j(x_j-\bar x)(y_j-\bar y)}
         {\sum_j(x_j-\bar x)^2},\quad
a=\bar y-b\bar x,\quad
R^2=1-\frac{\sum_j(y_j-a-bx_j)^2}{\sum_j(y_j-\bar y)^2}.
\]

Output \(b\) as log_slope_n, in log-price units per candle, and \(R^2\) as log_r2_n. For exactly constant prices set slope and \(R^2\) to zero; the latter is an explicit convention because the usual \(R^2\) is undefined.

## Momentum — 24

**Six RSI variants:** \(n\in\{5,7,14,21,28,42\}\). Use close-price differences, not log returns:

\[
u_t=\max(C_t-C_{t-1},0),\quad d_t=\max(C_{t-1}-C_t,0),
\quad
\text{rsi}_n=100\frac{\operatorname{RMA}_n(u)}
{\operatorname{RMA}_n(u)+\operatorname{RMA}_n(d)}.
\]

If both smoothed inputs are zero, return 50. If only losses are zero, return 100; if only gains are zero, return 0. The smoothing follows Wilder's definition; the fully flat value is this specification's convention. [RSI reference](https://ta-lib.org/functions/rsi.html)

**Eight stochastic features:** \(n\in\{7,14,21,28\}\), with two outputs per window. Use fast stochastic:

\[
K_{n,t}=100\frac{C_t-\min_{i\in W_n(t)}L_i}
{\max_{i\in W_n(t)}H_i-\min_{i\in W_n(t)}L_i},
\qquad D_{n,t}=\frac{K_{n,t}+K_{n,t-1}+K_{n,t-2}}3.
\]

Output stoch_k_n and stoch_d_n. A zero high–low window produces \(K=50\); \(D\) still requires three valid \(K\) values. This neutral convention differs from TA-Lib's documented zero-range result of zero. [Fast stochastic reference](https://ta-lib.org/functions/stochf.html)

**Four CCI variants:** \(n\in\{10,20,40,80\}\):

\[
A_{n,t}=\frac1n\sum_{i\in W_n(t)}|P_i-\mu_n(P)_t|,
\qquad
\text{cci}_n=\frac{P_t-\mu_n(P)_t}{0.015A_{n,t}}.
\]

Every deviation uses the same current-window mean. Set CCI to zero when \(A_{n,t}=0\). [CCI reference](https://ta-lib.org/functions/cci.html)

**Six MACD features:** three \((f,s,q)\) settings, \((6,13,5)\), \((12,26,9)\), and \((24,52,18)\), each producing a normalized line and histogram:

\[
M_t=\operatorname{EMA}_f(C)_t-\operatorname{EMA}_s(C)_t,\quad
S_t=\operatorname{EMA}_q(M)_t,
\quad
\text{macd\_line}_{f,s,q}=M_t/C_t,\quad
\text{macd\_hist}_{f,s,q}=(M_t-S_t)/C_t.
\]

Compute the signal from the unnormalized MACD line, then normalize both outputs by the current close. Seed the signal from its first \(q\) valid line values. The signal is an intermediate, not an additional feature. Independently seeded EMAs and the neutral conventions in this document mean bit-for-bit compatibility with a particular indicator library is not implied. [MACD definition](https://ta-lib.org/functions/macd.html)

## Volatility — 20

**Six return volatilities:** return_vol_n is \(\sigma_n(r)_t\), for \(n\in\{5,10,20,40,80,160\}\).

**Four normalized ATRs:** \(n\in\{7,14,28,56\}\):

\[
TR_t=\max(H_t-L_t,|H_t-C_{t-1}|,|L_t-C_{t-1}|),
\qquad \text{atr\_normalized}_n=\operatorname{RMA}_n(TR)_t/C_t.
\]

The first candle has no valid true range without its previous close. Output a fraction, without multiplying by 100. [ATR reference](https://ta-lib.org/functions/atr.html)

**Eight Bollinger features:** \(n\in\{10,20,40,80\}\), two outputs each:

\[
m_t=\mu_n(C)_t,\quad U_t=m_t+2\sigma_n(C)_t,\quad
D_t=m_t-2\sigma_n(C)_t,
\]
\[
\text{bb\_position}_n=(C_t-D_t)/(U_t-D_t),\qquad
\text{bb\_width}_n=(U_t-D_t)/m_t.
\]

Do not clip position: it can lie outside \([0,1]\). When the bands coincide, set position to 0.5 and width to zero. Here \(D_t\) denotes the lower band, not stochastic %D. [Bollinger band definition](https://ta-lib.org/functions/bbands.html)

**Two downside deviations:** \(n\in\{20,80\}\):

\[
\text{downside\_deviation}_n=
\sqrt{\frac1n\sum_{i\in W_n(t)}\min(r_i,0)^2}.
\]

This is downside deviation relative to zero, averaged over all \(n\) observations; it is not the standard deviation of the negative observations.

## Volume and activity — 24

**Six relative-volume features:** \(n\in\{5,10,20,40,80,160\}\):
\[
\text{relative\_volume}_n=V_t/\mu_n(V)_{t-1}.
\]

**Four volume z-scores:** \(n\in\{20,40,80,160\}\):
\[
\text{volume\_zscore}_n=(V_t-\mu_n(V)_{t-1})/\sigma_n(V)_{t-1}.
\]

Both baselines exclude the current candle. If the relevant denominator is zero, return NaN, including a constant-volume z-score baseline.

**Four rolling OBV balances:** \(n\in\{10,20,40,80\}\):
\[
\text{obv\_balance}_n=
\frac{\sum_{i\in W_n(t)}\operatorname{sign}(C_i-C_{i-1})V_i}
{\sum_{i\in W_n(t)}V_i}.
\]

This custom rolling balance is in \([-1,1]\). It is not the cumulative level of conventional OBV. Unchanged closes contribute zero signed volume.

**Four Money Flow Indices:** \(n\in\{7,14,28,56\}\). Let \(F_i=P_iV_i\) and

\[
F^+_{n,t}=\sum_{i\in W_n(t)}F_i\,1[P_i>P_{i-1}],\qquad
F^-_{n,t}=\sum_{i\in W_n(t)}F_i\,1[P_i<P_{i-1}],
\]
\[
\text{mfi}_n=100F^+_{n,t}/(F^+_{n,t}+F^-_{n,t}).
\]

Equal typical prices contribute to neither sum. When both sums are zero, return 50 by convention, unlike TA-Lib's documented result of zero. The first input's flow classification requires a previous typical price. [MFI reference](https://ta-lib.org/functions/mfi.html)

**Four Chaikin Money Flows:** \(n\in\{10,20,40,80\}\):
\[
a_i=(2C_i-H_i-L_i)/(H_i-L_i),\qquad
\text{cmf}_n=\frac{\sum_{i\in W_n(t)}a_iV_i}{\sum_{i\in W_n(t)}V_i}.
\]

Set \(a_i=0\) for a zero-range candle. Keep NaN for zero total window volume; this differs from TA-Lib's zero-volume convention. [CMF reference](https://ta-lib.org/functions/cmf.html)

**Two activity levels:** log1p_volume \(=\log(1+V_t)\) and log1p_trade_count \(=\log(1+N_t)\).

These are the original volume and trade-count slots with a fixed log transform. The transform compresses large activity values but does not make the volume level independent of asset units. Preserve consistent units and fit any subsequent scaling only on training data.

## Distribution and regime changes — 16

**Eight distribution features:** skewness and excess kurtosis for each \(n\in\{20,40,80,160\}\). Define central moments \(m_k=n^{-1}\sum_{i\in W_n(t)}(r_i-\mu_n(r)_t)^k\):

\[
\text{return\_skew}_n=m_3/m_2^{3/2},\qquad
\text{return\_excess\_kurtosis}_n=m_4/m_2^2-3.
\]

These are uncorrected moment estimators, not bias-corrected sample skewness/kurtosis. Constant-return windows yield NaN. Estimates from the shortest windows may be noisy.

**Four return autocorrelations:** use lags \(k\in\{1,2,3,5\}\) and 80 paired observations:

\[
\text{return\_autocorr\_80\_lag}_k=
\operatorname{PearsonCorr}\left(
(r_i)_{i=t-79}^{t},(r_{i-k})_{i=t-79}^{t}
\right).
\]

Each vector is demeaned by its own mean. These use \(80+k\) returns, not a single 80-return window with \(k\) pairs discarded.

**Four historical regime-change features:** two outputs for each \(n\in\{20,80\}\). Compare current returns \(t-n+1,\ldots,t\) with previous returns \(t-2n+1,\ldots,t-n\). Let their means be \(\mu_A,\mu_B\), and population variances be \(v_A,v_B\):

\[
\text{mean\_change}_n=(\mu_A-\mu_B)/\sqrt{(v_A+v_B)/2},
\qquad
\text{log\_variance\_ratio}_n=\log(v_A/v_B).
\]

The mean-change value is a standardized difference, not a statistical test or p-value. Return NaN for zero pooled variance, or for either zero variance in the log variance ratio. Both comparison windows are historical at time \(t\).

## Trade flow and calendar — 12

**Five taker-buy imbalances:** \(n\in\{1,5,10,20,40\}\):

\[
\text{taker\_buy\_imbalance}_n=
\frac{2\sum_{i\in W_n(t)}B_i-\sum_{i\in W_n(t)}V_i}
{\sum_{i\in W_n(t)}V_i}.
\]

Aggregate volumes before taking the ratio. This volume-weighted quantity lies in \([-1,1]\), with positive values indicating more buyer-initiated than seller-initiated volume. A zero-volume window gives NaN.

**Three return–volume correlations:** \(n\in\{20,40,80\}\):

\[
\text{return\_volume\_corr}_n=
\operatorname{PearsonCorr}\left(
(r_i)_{i\in W_n(t)},(\log(1+V_i))_{i\in W_n(t)}
\right).
\]

This measures contemporaneous association between signed return and activity. It is distinct from correlation with absolute returns. A constant input vector gives NaN.

**Four cyclical time features:** at the UTC exclusive candle-end boundary, let \(s\) be elapsed seconds since midnight, including any fractional second, and \(d\in\{0,\ldots,6\}\) the weekday with Monday = 0. Define \(\phi_d=s/86400\), \(\phi_w=(d+\phi_d)/7\), then output

\[
\text{time\_of\_day\_sin}=\sin(2\pi\phi_d),\quad
\text{time\_of\_day\_cos}=\cos(2\pi\phi_d),
\]
\[
\text{time\_of\_week\_sin}=\sin(2\pi\phi_w),\quad
\text{time\_of\_week\_cos}=\cos(2\pi\phi_w).
\]

## Readiness and evaluation

Under complete, nondegenerate data, the earliest fully populated row requires 161 consecutive candles: the longest return windows need 160 returns, and the volume baselines need 160 earlier volumes plus the current volume. This is an availability minimum, not a claim that recursively smoothed indicators have become insensitive to their seeds. Use consistent prehistory across training and deployment, and check sensitivity to additional prehistory.

Preserve warm-up and undefined values. Never backfill them from future observations. Keep timestamps, asset IDs, target labels, and readiness/missingness metadata outside the 128 model-input columns. Appending missingness flags to the model inputs would increase the feature count. A fixed 128-column schema can contain missing values; it does not mean all 128 signals are observed for every market.

Evaluate with time-ordered training and validation. Fit imputation, scaling, feature selection, and any learned clipping limits on each training fold only. If targets use future intervals, remove training examples whose label information reaches the validation period; choose the split gap from those label intervals, not automatically from the 160-candle feature window. Scikit-learn's time-series splitter supports excluding observations at the end of each training split through its gap parameter. [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

The count audit confirms 128 distinct column definitions, not 128 independent signals. Many windows and indicators overlap. Compare the full set with smaller subsets and group ablations using the same validation periods. Final window selection depends on candle duration, target horizon, available history, and measured out-of-sample performance.

## Ordered feature registry

Read each row left to right; IDs match the JSON manifest. Intermediates such as typical price, MACD signal, and Bollinger bands are excluded from the feature count.

| IDs | Group | Family | Count | Exact column names |
| --- | --- | --- | ---: | --- |
| 1–8 | Price and candle shape | Historical log returns | 8 | `log_return_1`, `log_return_2`, `log_return_3`, `log_return_5`, `log_return_10`, `log_return_20`, `log_return_40`, `log_return_80` |
| 9–12 | Price and candle shape | Candle shape | 4 | `candle_body`, `candle_range`, `upper_wick`, `lower_wick` |
| 13–18 | Trend | EMA distance | 6 | `ema_distance_5`, `ema_distance_10`, `ema_distance_20`, `ema_distance_40`, `ema_distance_80`, `ema_distance_160` |
| 19–22 | Trend | SMA distance | 4 | `sma_distance_10`, `sma_distance_20`, `sma_distance_50`, `sma_distance_100` |
| 23–27 | Trend | Log-price regression slope | 5 | `log_slope_10`, `log_slope_20`, `log_slope_40`, `log_slope_80`, `log_slope_160` |
| 28–32 | Trend | Log-price regression R squared | 5 | `log_r2_10`, `log_r2_20`, `log_r2_40`, `log_r2_80`, `log_r2_160` |
| 33–38 | Momentum | RSI | 6 | `rsi_5`, `rsi_7`, `rsi_14`, `rsi_21`, `rsi_28`, `rsi_42` |
| 39–46 | Momentum | Fast stochastic | 8 | `stoch_k_7`, `stoch_d_7`, `stoch_k_14`, `stoch_d_14`, `stoch_k_21`, `stoch_d_21`, `stoch_k_28`, `stoch_d_28` |
| 47–50 | Momentum | CCI | 4 | `cci_10`, `cci_20`, `cci_40`, `cci_80` |
| 51–56 | Momentum | Normalized MACD | 6 | `macd_line_6_13_5`, `macd_hist_6_13_5`, `macd_line_12_26_9`, `macd_hist_12_26_9`, `macd_line_24_52_18`, `macd_hist_24_52_18` |
| 57–62 | Volatility | Return volatility | 6 | `return_vol_5`, `return_vol_10`, `return_vol_20`, `return_vol_40`, `return_vol_80`, `return_vol_160` |
| 63–66 | Volatility | Normalized ATR | 4 | `atr_normalized_7`, `atr_normalized_14`, `atr_normalized_28`, `atr_normalized_56` |
| 67–74 | Volatility | Bollinger bands | 8 | `bb_position_10`, `bb_width_10`, `bb_position_20`, `bb_width_20`, `bb_position_40`, `bb_width_40`, `bb_position_80`, `bb_width_80` |
| 75–76 | Volatility | Downside deviation | 2 | `downside_deviation_20`, `downside_deviation_80` |
| 77–82 | Volume and activity | Relative volume | 6 | `relative_volume_5`, `relative_volume_10`, `relative_volume_20`, `relative_volume_40`, `relative_volume_80`, `relative_volume_160` |
| 83–86 | Volume and activity | Volume z-score | 4 | `volume_zscore_20`, `volume_zscore_40`, `volume_zscore_80`, `volume_zscore_160` |
| 87–90 | Volume and activity | Rolling OBV balance | 4 | `obv_balance_10`, `obv_balance_20`, `obv_balance_40`, `obv_balance_80` |
| 91–94 | Volume and activity | MFI | 4 | `mfi_7`, `mfi_14`, `mfi_28`, `mfi_56` |
| 95–98 | Volume and activity | CMF | 4 | `cmf_10`, `cmf_20`, `cmf_40`, `cmf_80` |
| 99–100 | Volume and activity | Activity levels | 2 | `log1p_volume`, `log1p_trade_count` |
| 101–108 | Distribution and regime changes | Return distribution | 8 | `return_skew_20`, `return_excess_kurtosis_20`, `return_skew_40`, `return_excess_kurtosis_40`, `return_skew_80`, `return_excess_kurtosis_80`, `return_skew_160`, `return_excess_kurtosis_160` |
| 109–112 | Distribution and regime changes | Return autocorrelation | 4 | `return_autocorr_80_lag_1`, `return_autocorr_80_lag_2`, `return_autocorr_80_lag_3`, `return_autocorr_80_lag_5` |
| 113–116 | Distribution and regime changes | Adjacent-window regime change | 4 | `mean_change_20`, `log_variance_ratio_20`, `mean_change_80`, `log_variance_ratio_80` |
| 117–121 | Trade flow and calendar | Taker-buy imbalance | 5 | `taker_buy_imbalance_1`, `taker_buy_imbalance_5`, `taker_buy_imbalance_10`, `taker_buy_imbalance_20`, `taker_buy_imbalance_40` |
| 122–124 | Trade flow and calendar | Return-volume correlation | 3 | `return_volume_corr_20`, `return_volume_corr_40`, `return_volume_corr_80` |
| 125–128 | Trade flow and calendar | Cyclical time | 4 | `time_of_day_sin`, `time_of_day_cos`, `time_of_week_sin`, `time_of_week_cos` |
