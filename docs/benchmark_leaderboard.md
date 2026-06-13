# Robustness Benchmark — leaderboard (spec 1.0.0)

> Frozen, deterministic, cite-able. Re-running `python tools/benchmark.py`
> reproduces every number byte-for-byte (pinned image). Each cell is ONE
> strategy x ONE dataset through the FULL ROLLING WALK-FORWARD
> (per-window in-sample optimise, out-of-sample evaluate); metrics
> aggregate the concatenated OOS windows. The CANONICAL golden is the
> Rust CSV; Python is the 1e-3 cross-check. See `docs/benchmark.md`.

**Corpus breadth:** 6 strategies x 6 datasets = 36 cells. A strategy evaluated over K walk-forward windows is STILL ONE strategy, not K — windows are in-sample geometry, never multiplied into corpus size (effective-trials discipline).

**WFO geometry is per-dataset, not uniform.** `win` = number of rolling windows actually executed; on datasets smaller than the OOS span the engine runs fewer/shorter windows, and `OOS-disp` is `n/a` when there is <2 windows. See `windows`/`eff_oos_bars` in the golden CSV.

**NET = realistic frictions** (crypto 0.02% fee / 0.03% slip / 0.01% funding; FX forex-mode). **GROSS = zero-cost / FRICTIONLESS** — a labeled comparison column shown for context ONLY; it is NOT a tradeable result. All metrics are OUT-OF-SAMPLE; ROI/MDD are account-fraction (FX in R-units), MDD on the OOS-only equity. `OOS-disp` is the across-window OOS-Sharpe std (stability). DSR/PBO are computed on the NET stream only. `engine=both` cells are cross-engine parity-checked at 1e-3; `engine=python` cells are Python-only (real numbers, not cross-checked) and flip to cross-engine once item 5 ports the indicator.

| Dataset | Strategy | Engine | win | NET ROI | NET Sharpe | NET PF | NET MaxDD | OOS-disp | *GROSS ROI* | *GROSS Sharpe* | *GROSS PF* | DSR |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| BTCUSDT_30m | ema_cross | both | 1 | -0.0034 | -0.3218 | 0.9698 | 0.0139 | n/a | *0.0171* | *1.3579* | *1.1155* | 0.0000 |
| BTCUSDT_30m | rsi_revert | python | 1 | -0.0044 | -0.4957 | 0.9490 | 0.0151 | n/a | *0.0044* | *0.4577* | *1.0461* | 0.0000 |
| BTCUSDT_30m | macd_zero | both | 1 | -0.0131 | -0.6617 | 0.9693 | 0.0274 | n/a | *0.0596* | *2.9666* | *1.1450* | 0.0000 |
| BTCUSDT_30m | atr_cross | python | 1 | -0.0054 | -0.6705 | 0.9215 | 0.0076 | n/a | *-0.0006* | *-0.0713* | *0.9917* | 0.0000 |
| BTCUSDT_30m | engine_ema | both | 1 | -0.0282 | -1.7528 | 0.9049 | 0.0416 | n/a | *0.0304* | *1.8532* | *1.1071* | 0.0000 |
| BTCUSDT_30m | stoch_kd | python | 1 | -0.0434 | -3.1359 | 0.8465 | 0.0488 | n/a | *0.0056* | *0.3506* | *1.0141* | 0.0000 |
| DOGEUSDT_30m | engine_ema | both | 1 | -0.0043 | -0.2179 | 0.9900 | 0.0330 | n/a | *0.0548* | *2.7103* | *1.1295* | 0.0000 |
| DOGEUSDT_30m | atr_cross | python | 1 | -0.0026 | -0.2559 | 0.9754 | 0.0169 | n/a | *0.0016* | *0.1532* | *1.0149* | 0.0000 |
| DOGEUSDT_30m | rsi_revert | python | 1 | -0.0027 | -0.3050 | 0.9645 | 0.0079 | n/a | *0.0150* | *1.6615* | *1.2073* | 0.0000 |
| DOGEUSDT_30m | stoch_kd | python | 1 | -0.0078 | -0.4256 | 0.9795 | 0.0372 | n/a | *0.0677* | *3.2126* | *1.1483* | 0.0000 |
| DOGEUSDT_30m | ema_cross | both | 1 | -0.0057 | -0.5191 | 0.9539 | 0.0123 | n/a | *0.0156* | *1.3117* | *1.1201* | 0.0000 |
| DOGEUSDT_30m | macd_zero | both | 1 | -0.0284 | -1.2006 | 0.9519 | 0.0411 | n/a | *0.0496* | *2.0014* | *1.0833* | 0.0000 |
| EURUSD_1h | stoch_kd | python | 1 | 241.5368 | 3.8640 | 1.2988 | 32.0880 | n/a | *241.5368* | *3.8640* | *1.2988* | 1.0000 |
| EURUSD_1h | macd_zero | both | 1 | 239.4443 | 2.9377 | 1.1609 | 32.0944 | n/a | *183.1788* | *2.2707* | *1.1226* | 1.0000 |
| EURUSD_1h | engine_ema | both | 1 | 94.5378 | 1.6786 | 1.1305 | 29.0372 | n/a | *94.6008* | *1.6797* | *1.1306* | 0.0000 |
| EURUSD_1h | ema_cross | both | 1 | 42.8764 | 1.3511 | 1.1939 | 16.0336 | n/a | *36.8708* | *1.1440* | *1.1582* | 0.0000 |
| EURUSD_1h | rsi_revert | python | 1 | 25.9080 | 0.9530 | 1.1560 | 25.0356 | n/a | *25.9080* | *0.9530* | *1.1560* | 0.0000 |
| EURUSD_1h | atr_cross | python | 1 | -13.1044 | -0.4765 | 0.9342 | 43.0812 | n/a | *-9.1044* | *-0.3292* | *0.9540* | 0.0000 |
| SOLUSDT_1h | macd_zero | both | 1 | 0.0240 | 1.7308 | 1.1384 | 0.0087 | n/a | *0.0453* | *3.1900* | *1.2743* | 1.0000 |
| SOLUSDT_1h | engine_ema | both | 1 | 0.0135 | 1.2201 | 1.1210 | 0.0062 | n/a | *0.0305* | *2.7136* | *1.2950* | 1.0000 |
| SOLUSDT_1h | rsi_revert | python | 1 | 0.0022 | 0.4677 | 1.1073 | 0.0043 | n/a | *0.0000* | *0.0000* | *1.0000* | 0.0000 |
| SOLUSDT_1h | atr_cross | python | 1 | 0.0019 | 0.3546 | 1.0686 | 0.0042 | n/a | *0.0077* | *1.1008* | *1.1761* | 0.0000 |
| SOLUSDT_1h | ema_cross | both | 1 | 0.0023 | 0.2791 | 1.0346 | 0.0069 | n/a | *0.0126* | *1.3780* | *1.1727* | 0.0000 |
| SOLUSDT_1h | stoch_kd | python | 1 | 0.0009 | 0.0929 | 1.0094 | 0.0165 | n/a | *0.0218* | *1.8872* | *1.1731* | 0.0000 |
| SYNTH_100k | atr_cross | python | 1 | -0.0107 | -1.2313 | 0.8717 | 0.0132 | n/a | *-0.0150* | *-1.8031* | *0.8148* | 0.0000 |
| SYNTH_100k | ema_cross | both | 1 | -0.0169 | -1.6860 | 0.8503 | 0.0189 | n/a | *0.0078* | *0.7561* | *1.0778* | 0.0000 |
| SYNTH_100k | stoch_kd | python | 1 | -0.0308 | -1.8991 | 0.8923 | 0.0442 | n/a | *-0.0012* | *-0.0767* | *0.9953* | 0.0000 |
| SYNTH_100k | rsi_revert | python | 1 | -0.0141 | -2.1954 | 0.7303 | 0.0173 | n/a | *-0.0110* | *-1.6252* | *0.7982* | 0.0000 |
| SYNTH_100k | macd_zero | both | 1 | -0.0917 | -3.6720 | 0.8646 | 0.0990 | n/a | *-0.0143* | *-0.5715* | *0.9770* | 0.0000 |
| SYNTH_100k | engine_ema | both | 1 | -0.0819 | -4.2545 | 0.8082 | 0.0840 | n/a | *-0.0333* | *-1.6323* | *0.9253* | 0.0000 |
| USDJPY_1h | rsi_revert | python | 1 | -48.1168 | -1.7316 | 0.7918 | 51.1164 | n/a | *182.7964* | *4.2764* | *1.5438* | 0.0000 |
| USDJPY_1h | ema_cross | both | 1 | -150.1896 | -4.5817 | 0.6180 | 159.1836 | n/a | *111.7356* | *2.3904* | *1.2392* | 0.0000 |
| USDJPY_1h | stoch_kd | python | 1 | -262.3768 | -5.5572 | 0.6603 | 264.3744 | n/a | *558.2820* | *6.6337* | *1.4015* | 0.0000 |
| USDJPY_1h | atr_cross | python | 1 | -159.1436 | -6.0647 | 0.4852 | 164.1360 | n/a | *42.8264* | *1.1525* | *1.1363* | 0.0000 |
| USDJPY_1h | engine_ema | both | 1 | -507.3260 | -9.6839 | 0.5456 | 510.3256 | n/a | *499.9876* | *5.9952* | *1.3580* | 0.0000 |
| USDJPY_1h | macd_zero | both | 1 | -736.8144 | -11.1460 | 0.5695 | 756.8032 | n/a | *661.7536* | *6.2462* | *1.2825* | 0.0000 |

_GROSS columns are italicised to mark them frictionless / non-tradeable._

## Corpus-level PBO (per dataset, core strategies)

Probability of Backtest Overfitting across the core corpus (Bailey-Borwein-Lopez de Prado-Zhu 2014, S=16), over the strategies' WFO OOS equity curves. Computed on the NET stream. Python-only annotation; not a parity cell. Lower is better.

| Dataset | PBO |
|---|--:|
| BTCUSDT_30m | 0.7556 |
| DOGEUSDT_30m | 0.1344 |
| EURUSD_1h | 0.9672 |
| SOLUSDT_1h | 0.6348 |
| SYNTH_100k | 0.9566 |
| USDJPY_1h | 0.1402 |
