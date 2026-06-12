#!/usr/bin/env python3
"""Reproducible Robustness Benchmark runner (roadmap item 04, v2).

Executes the FROZEN manifest (tools/benchmark_manifest.toml): 6 reference
strategies x 6 bundled datasets, each through the FULL ROLLING WALK-FORWARD
(per-window in-sample optimise, out-of-sample evaluate), under TWO cost
regimes (NET realistic / GROSS frictionless), with LIVE DSR per cell and PBO
per dataset. Emits a DETERMINISTIC, version-stamped results table:

    data/golden/benchmark_results.v<spec_version>.csv   (machine-readable golden)
    docs/benchmark_leaderboard.md                       (rendered leaderboard)

and, mirroring tools/parity_arch.py's emit-golden + string-equality drift
guard, gates CI on any drift. The CANONICAL golden is the RUST CSV (proven
cross-arch by parity_arch.py); this Python run is the 1e-3 cross-check and the
human-facing leaderboard source.

HARVEST PATH (v2):
  - base = classic_single_run(df, cfg)   # IS seed equity (met_is/eq_is)
  - all_oos_rets, eq_wfo, *_ = walk_forward(df, base['met_is'],
                                            base['eq_is'])
    -> the FULL rolling WFO (__init__.py:2866 -> _walk_forward_impl:2872,
       plain branch :3007-3081; per-window optimise :3041, OOS evaluate :3046,
       concatenated stream :3058,3066, return :3081).
  - Per-cell metrics are aggregated from all_oos_rets using the engine's
    compute_metrics_for-equivalent math (matching Rust; OOS-only MDD base),
    plus across-window dispersion (per-window OOS counts from a thin engine
    counter, see _LAST_WFO_WINDOW_OOS_COUNTS).

DETERMINISM on the WFO path (see docs/benchmark.md):
  - bt.INDICATOR_VARIANCE=False  -> no active robustness scenario; the +/-1
    lookback overlay loop (__init__.py:2817) never runs; seeded 42 anyway.
  - bt.USE_MONTE_CARLO=False / cfg.use_monte_carlo=False -> the only unseeded
    RNG (np.random in classic_single_run, :1857-1858) never runs.
  - bt.SHARPE_MODE pinned to per-trade (the REAL Python knob, __init__.py:61).
    (Not load-bearing for the harvested cell value -- _agg_oos uses a fixed
    per-trade formula -- but pinned for met-symmetry with Rust.)
  - BLAS threads pinned to 1 before numpy import.
  Every cell is a pure function of (dataset bytes, pinned knobs).

DUAL COST: each cell is run NET then GROSS. NET = engine defaults
(0.02/0.03/0.01 crypto, forex-mode FX). GROSS = fee=slip=funding=0, an
EXPLICITLY LABELED frictionless comparison column, NEVER the headline result.

DSR/PBO (item 3) consume the WFO OOS NET trial set ONLY (not GROSS):
  - DSR(returns=all_oos_rets, sharpe_chosen=cell NET OOS Sharpe,
        trial_sharpes=distinct-strategy NET OOS Sharpes on that dataset).
        Trials = STRATEGIES, not windows (effective-trials discipline).
  - PBO over the strategies' WFO OOS equity curves (eq_wfo), per dataset.
  Both degrade gracefully if backtester.dsr / backtester.pbo are absent.

Usage:
    python tools/benchmark.py                  # run + render, check vs golden
    python tools/benchmark.py --emit-golden    # (re)write the versioned golden
    python tools/benchmark.py --check          # CI: re-run, assert == golden
    python tools/benchmark.py --cross-engine \
        --rust-csv /tmp/bench_rust.csv --tol 0.001   # core cells: Python vs Rust

Exit 0 = OK / byte-identical, 1 = drift / cross-engine mismatch, 2 = setup.
"""
from __future__ import annotations

import os
# Pin BLAS threads BEFORE numpy import (feedback_pin_openblas_threads) so the
# run is single-threaded and deterministic regardless of host core count.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import csv
import io
import math
import sys
from contextlib import redirect_stdout
from pathlib import Path

try:
    import tomllib                      # py3.11+
except ModuleNotFoundError:             # py3.10
    import tomli as tomllib             # type: ignore

import numpy as np

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "tools" / "benchmark_manifest.toml"
GOLDEN_DIR = REPO / "data" / "golden"
LEADERBOARD_MD = REPO / "docs" / "benchmark_leaderboard.md"

# The Python reference engine lives in a sibling checkout (CI checks both out
# side-by-side: parity.yml); locally BT_PY_REPO can point at it.
_PY_REPO = Path(os.environ.get(
    "BT_PY_REPO", REPO.parent / "quant-research-framework"))
if str(_PY_REPO) not in sys.path:
    sys.path.insert(0, str(_PY_REPO))


def golden_path(spec_version: str) -> Path:
    return GOLDEN_DIR / f"benchmark_results.v{spec_version}.csv"


# ----------------------------------------------------------------------------
# Rounding: COARSER than the 1e-3 cross-language parity band so f64 noise
# within tolerance cannot flap the byte-exact golden guard, while still
# catching real drift. Matches prettyprint display precision.
# ----------------------------------------------------------------------------
ND_RATIO = 4     # ROI(frac), Sharpe, PF, MDD(frac), dispersion
ND_PROB  = 4     # DSR probability


def _r(x, nd=ND_RATIO):
    if x is None:
        return "n/a"
    try:
        if not math.isfinite(float(x)):
            return "nan"
    except (TypeError, ValueError):
        return "n/a"
    return f"{float(x):.{nd}f}"


def _load_signal_lib():
    """Python signal functions, reused verbatim from the batch-runner example
    so the benchmark and the example stay in lockstep (single source of truth).
    engine_ema has NO entry -> the engine's built-in default signal is used.
    """
    import importlib.util
    spec_path = _PY_REPO / "examples" / "batch_runner" / "run_batch.py"
    spec = importlib.util.spec_from_file_location("_bench_signals", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {
        "ema_cross":  mod.signal_ema_cross,
        "macd_zero":  mod.signal_macd_zero,
        "atr_cross":  mod.signal_atr_cross,
        "rsi_revert": mod.signal_rsi_revert,
        "stoch_kd":   mod.signal_stoch_kd,
    }


def load_manifest():
    with MANIFEST.open("rb") as f:
        return tomllib.load(f)


# ----------------------------------------------------------------------------
# Aggregate the concatenated WFO OOS per-trade stream into {ROI,Sharpe,PF,MDD}.
# This is the engine's compute_metrics_for-EQUIVALENT formula -- it MATCHES the
# Rust aggregator (compute_metrics_for, src/lib.rs:828), which uses an hw>0
# guard on the crypto drawdown. For OOS-only crypto equity hw>=1.0 always, so
# the guard is inert; it only diverges from the bare engine backtest formula
# (__init__.py:1559) in the account-wipeout case (cumulative OOS return <= -1),
# where the guard clamps to 0. MaxDrawdown is taken on the OOS-ONLY equity
# fraction (start 1.0 crypto / 0.0 forex), NOT the seed-prefixed eq_wfo, so it
# agrees cross-engine. This is NOT an engine edit.
# ----------------------------------------------------------------------------
def _agg_oos(all_oos_rets, use_forex):
    rets = np.asarray(all_oos_rets, dtype=float)
    tc = rets.size
    if tc == 0:
        return {"roi": None, "sharpe": None, "pf": None, "mdd": None, "n": 0}
    base0 = 0.0 if use_forex else 1.0
    eq_frac = np.concatenate(([base0], base0 + np.cumsum(rets)))  # OOS-only base
    roi = eq_frac[-1] if use_forex else (eq_frac[-1] - 1.0)
    wins = rets[rets > 0]
    losses = -rets[rets <= 0]
    pf = float(wins.sum() / losses.sum()) if losses.size else float("inf")
    shp = float(rets.mean() / rets.std() * math.sqrt(tc)) \
        if tc > 1 and rets.std() else 0.0
    hw = np.maximum.accumulate(eq_frac)
    if use_forex:
        mdd = float(np.max(hw - eq_frac))
    else:
        mdd = float(np.max(np.where(hw > 0, (hw - eq_frac) / hw, 0.0)))  # matches Rust
    return {"roi": float(roi), "sharpe": shp, "pf": pf, "mdd": mdd, "n": tc}


def _window_dispersion(all_oos_rets, window_bounds):
    """Across-window OOS Sharpe std (stability, not a point estimate). The
    runner segments the concatenated stream at the WFO window boundaries the
    engine produced and computes a per-window Sharpe, then reports the std.
    window_bounds is the list of per-window OOS trade counts. Needs >=2 windows;
    else returns None (the cell still ships its point estimate). On small
    datasets (e.g. USDJPY_1h) the engine runs ~1 window -> dispersion is n/a,
    which is VISIBLE in the golden, not hidden behind a uniform headline.
    """
    if not window_bounds or len(window_bounds) < 2:
        return None
    rets = np.asarray(all_oos_rets, dtype=float)
    sharpes, i = [], 0
    for w in window_bounds:
        seg = rets[i:i + w]
        i += w
        if seg.size > 1 and seg.std():
            sharpes.append(seg.mean() / seg.std() * math.sqrt(seg.size))
    if len(sharpes) < 2:
        return None
    return float(np.std(sharpes, ddof=1))


def _build_config(bt, m, ds, strat, regime):
    """regime in {"net","gross"} selects the friction block."""
    cfg = bt.Config()
    w = m["wfo"]; p = m["pins"]
    # per-dataset geometry override (optional), else the [wfo] globals
    cfg.backtest_candles  = ds.get("backtest_candles", w["backtest_candles"])
    cfg.oos_candles       = ds.get("oos_candles", w["oos_candles"])
    cfg.use_oos2          = w["use_oos2"]
    cfg.default_lb        = strat["lb"]
    cfg.lookback_range    = tuple(w["lookback_range"])
    cfg.opt_metric        = p["opt_metric"]
    cfg.min_trades        = p["min_trades"]
    cfg.optimize_rrr      = p["optimize_rrr"]
    cfg.use_monte_carlo   = p["use_monte_carlo"]      # FALSE -> kills classic RNG
    cfg.print_equity_curve = p["print_equity_curve"]
    fr = m["frictions"][regime]
    if ds["kind"] == "fx":
        fx = fr["fx"]
        cfg.pip_size = fx["pip_jpy"] if "JPY" in ds["path"].upper() else fx["pip_default"]
        if regime == "gross":
            cfg.slippage_pct = 0.0
        cfg = cfg.with_forex(True)                     # scales SL/TP, R-units
    else:
        cr = fr["crypto"]
        cfg.fee_pct      = cr["fee_pct"]
        cfg.slippage_pct = cr["slippage_pct"]
        cfg.funding_fee  = cr["funding_fee"]           # Py funding IS config-zeroable
    return cfg


def _run_wfo_cell(bt, df, cfg, sig_fn):
    """One rolling-WFO cell: IS seed via classic_single_run, then the full
    rolling walk_forward; return the concatenated OOS stream + per-window OOS
    trade counts (for dispersion). Discards all engine stdout. NEVER calls
    main()/run_cfg (those add plotting + run_robustness_tests).
    """
    buf = io.StringIO()
    prev_sig = bt.create_raw_signals
    # reset the per-window OOS-count collector (the thin engine counter, see
    # the 2-line edit in §(f); if the engine lacks it, dispersion is n/a)
    if hasattr(bt, "_LAST_WFO_WINDOW_OOS_COUNTS"):
        bt._LAST_WFO_WINDOW_OOS_COUNTS = []
    try:
        with bt.with_config(cfg):
            if sig_fn is not None:
                bt.create_raw_signals = sig_fn          # documented seam
            with redirect_stdout(buf):
                base = bt.classic_single_run(df)        # IS seed equity
                all_oos_rets, _eq_wfo, _rb, _split = bt.walk_forward(
                    df, base["met_is"], base["eq_is"])  # FULL rolling WFO
    finally:
        bt.create_raw_signals = prev_sig
    wb = None
    if hasattr(bt, "_LAST_WFO_WINDOW_OOS_COUNTS"):
        wb = list(bt._LAST_WFO_WINDOW_OOS_COUNTS)
    return np.asarray(all_oos_rets, float), wb


def run_cells(m):
    try:
        import backtester as bt
    except ImportError as e:
        print("  ERROR: cannot import the Python reference engine "
              "'backtester'.\n  Set BT_PY_REPO to your quant-research-framework "
              f"checkout (sibling of this repo). Tried: {_PY_REPO}\n  ({e})",
              file=sys.stderr)
        raise SystemExit(2)

    # Pin the REAL Python knobs at module scope (the WFO path reads these).
    bt.USE_MONTE_CARLO = False
    bt.INDICATOR_VARIANCE = False
    # SHARPE_MODE is the actual Python Sharpe toggle (__init__.py:61); set it
    # directly. (cfg has no sharpe_bar field on this branch.)
    _sm = m["pins"].get("sharpe_mode", "trade")
    if hasattr(bt, "SHARPE_MODE"):
        bt.SHARPE_MODE = "bar" if _sm == "bar" else "trade"

    sig_lib = _load_signal_lib()
    rows = []
    eq_by_dataset = {}          # ds_id -> {sid: net WFO-OOS equity ndarray}
    sharpe_by_dataset = {}      # ds_id -> {sid: net OOS Sharpe}   (DSR trials)
    rets_by_cell = {}           # (ds_id,sid) -> net all_oos_rets  (DSR returns)

    enabled = [s for s in m["strategies"] if s.get("enabled", True)]
    for ds in m["datasets"]:
        csv_path = REPO / ds["path"]
        if not csv_path.exists():
            # HARD FAIL (A6): the golden's shape depends on all datasets being
            # present in BOTH repos. Do not silently emit a partial golden.
            print(f"  FATAL: dataset {ds['id']} missing: {csv_path}\n"
                  "  All manifest datasets must exist in this repo (and the "
                  "Python ref repo) before emit/check.", file=sys.stderr)
            raise SystemExit(2)
        df = bt.load_ohlc(str(csv_path))
        use_forex = (ds["kind"] == "fx")
        eq_by_dataset[ds["id"]] = {}
        sharpe_by_dataset[ds["id"]] = {}
        for strat in enabled:
            sid = strat["id"]
            sig_fn = sig_lib.get(sid)        # None for engine_ema (built-in)
            # ---- NET (realistic) ----
            cfg_net = _build_config(bt, m, ds, strat, "net")
            oos_net, wb = _run_wfo_cell(bt, df, cfg_net, sig_fn)
            agg_net = _agg_oos(oos_net, use_forex)
            disp_net = _window_dispersion(oos_net, wb)
            n_windows = len(wb) if wb else 1
            base0 = 0.0 if use_forex else 1.0
            eqw_net = np.concatenate(([base0], base0 + np.cumsum(oos_net))) \
                if oos_net.size else np.asarray([base0])
            # ---- GROSS (frictionless) ----
            cfg_g = _build_config(bt, m, ds, strat, "gross")
            oos_g, _ = _run_wfo_cell(bt, df, cfg_g, sig_fn)
            agg_g = _agg_oos(oos_g, use_forex)

            if strat.get("core"):
                eq_by_dataset[ds["id"]][sid] = eqw_net
            sharpe_by_dataset[ds["id"]][sid] = agg_net["sharpe"]
            rets_by_cell[(ds["id"], sid)] = oos_net

            rows.append({
                "dataset": ds["id"], "strategy": sid,
                "core": "1" if strat.get("core", True) else "0",
                "engine": strat.get("engine", "both"),
                "item5_target": "1" if strat.get("item5_target") else "0",
                # geometry audit (A5): real per-cell WFO geometry, not the
                # uniform headline.
                "windows": n_windows,
                "eff_oos_bars": int(agg_net["n"]),
                # NET (realistic) block
                "net_roi": agg_net["roi"], "net_sharpe": agg_net["sharpe"],
                "net_pf": agg_net["pf"], "net_mdd": agg_net["mdd"],
                "net_oos_disp": disp_net,
                # GROSS (frictionless) block — labeled, never the headline
                "gross_roi": agg_g["roi"], "gross_sharpe": agg_g["sharpe"],
                "gross_pf": agg_g["pf"], "gross_mdd": agg_g["mdd"],
                # filled after the dataset loop (need cross-strategy trials)
                "dsr": None,
            })
    # ---- LIVE DSR (per cell) + PBO (per dataset), off the NET WFO OOS set ----
    _fill_dsr(rows, sharpe_by_dataset, rets_by_cell)
    pbo_by_ds = _pbo_corpus(eq_by_dataset)
    return rows, pbo_by_ds


def _fill_dsr(rows, sharpe_by_dataset, rets_by_cell):
    """DSR per cell off the NET WFO OOS stream. trial_sharpes = the
    distinct-strategy NET OOS Sharpes on that dataset (trials = STRATEGIES, not
    windows — effective-trials discipline, constraint #6). returns = the cell's
    concatenated WFO OOS per-trade stream (OOS-only -> no look-ahead).
    Degrades to None if backtester.dsr is absent or inputs are degenerate.
    """
    try:
        from backtester.dsr import deflated_sharpe_ratio as _dsr
    except Exception:
        return
    for r in rows:
        ds_id, sid = r["dataset"], r["strategy"]
        trials = [s for k, s in sharpe_by_dataset.get(ds_id, {}).items()
                  if s is not None and math.isfinite(s)]
        rets = rets_by_cell.get((ds_id, sid))
        sh = r["net_sharpe"]
        if (rets is None or len(rets) < 3 or len(trials) < 2
                or sh is None or not math.isfinite(sh)):
            continue
        try:
            val = _dsr(sh, trials, np.asarray(rets, float))
            r["dsr"] = val if math.isfinite(val) else None
        except Exception:
            r["dsr"] = None


def _pbo_corpus(eq_by_dataset):
    """Corpus-level PBO per dataset (item 3, Python-only annotation; not a
    parity cell, never enters the golden CSV). Over min-length-truncated NET
    WFO-OOS equity curves of the CORE strategies (engine-agnostic corpus shape).
    Degrades to {} if pbo absent or <2 curves or T<16.
    """
    out = {}
    try:
        from backtester.pbo import pbo as _pbo
        for ds_id, cols in eq_by_dataset.items():
            curves = [c for c in cols.values() if c is not None and len(c)]
            if len(curves) >= 2:
                T = min(len(c) for c in curves)
                if T >= 16:
                    M = np.column_stack(
                        [np.asarray(c[:T], float) for c in curves])
                    out[ds_id] = _pbo(M, S=16)     # real signature pbo(M, S=16)
    except Exception:
        out = {}
    return out


# ----------------------------------------------------------------------------
# Serialisation. The byte-exact GOLDEN holds the stable ratio columns for BOTH
# cost regimes plus the per-cell WFO geometry audit (windows, eff_oos_bars).
# DSR at 4dp. Per-window optimiser outputs (best_lb/best_rrr) vary by window and
# are NOT aggregated into a single cell value, so they never enter the file.
# ----------------------------------------------------------------------------
GOLDEN_HEADER = [
    "dataset", "strategy", "core", "engine", "item5_target",
    "windows", "eff_oos_bars",
    "net_roi", "net_sharpe", "net_pf", "net_mdd", "net_oos_disp",
    "gross_roi", "gross_sharpe", "gross_pf", "gross_mdd",
    "dsr",
]


def render_csv(rows) -> str:
    rows = sorted(rows, key=lambda r: (r["dataset"], r["strategy"]))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(GOLDEN_HEADER)
    for r in rows:
        w.writerow([
            r["dataset"], r["strategy"], r["core"], r["engine"], r["item5_target"],
            r["windows"], r["eff_oos_bars"],
            _r(r["net_roi"]), _r(r["net_sharpe"]), _r(r["net_pf"]),
            _r(r["net_mdd"]), _r(r["net_oos_disp"]),
            _r(r["gross_roi"]), _r(r["gross_sharpe"]), _r(r["gross_pf"]),
            _r(r["gross_mdd"]),
            _r(r["dsr"], ND_PROB),
        ])
    return out.getvalue()


def render_markdown(rows, pbo_by_ds, spec_version) -> str:
    rows = sorted(rows, key=lambda r: (r["dataset"], -(
        r["net_sharpe"] if isinstance(r["net_sharpe"], (int, float))
        and math.isfinite(r["net_sharpe"]) else -1e9)))
    n_strats = len({r["strategy"] for r in rows})
    n_dsets  = len({r["dataset"] for r in rows})
    lines = [
        f"# Robustness Benchmark — leaderboard (spec {spec_version})",
        "",
        "> Frozen, deterministic, cite-able. Re-running `python tools/benchmark.py`",
        "> reproduces every number byte-for-byte (pinned image). Each cell is ONE",
        "> strategy x ONE dataset through the FULL ROLLING WALK-FORWARD",
        "> (per-window in-sample optimise, out-of-sample evaluate); metrics",
        "> aggregate the concatenated OOS windows. The CANONICAL golden is the",
        "> Rust CSV; Python is the 1e-3 cross-check. See `docs/benchmark.md`.",
        "",
        f"**Corpus breadth:** {n_strats} strategies x {n_dsets} datasets = "
        f"{len(rows)} cells. A strategy evaluated over K walk-forward windows is "
        "STILL ONE strategy, not K — windows are in-sample geometry, never "
        "multiplied into corpus size (effective-trials discipline).",
        "",
        "**WFO geometry is per-dataset, not uniform.** `win` = number of rolling "
        "windows actually executed; on datasets smaller than the OOS span the "
        "engine runs fewer/shorter windows, and `OOS-disp` is `n/a` when there "
        "is <2 windows. See `windows`/`eff_oos_bars` in the golden CSV.",
        "",
        "**NET = realistic frictions** (crypto 0.02% fee / 0.03% slip / 0.01% "
        "funding; FX forex-mode). **GROSS = zero-cost / FRICTIONLESS** — a "
        "labeled comparison column shown for context ONLY; it is NOT a tradeable "
        "result. All metrics are OUT-OF-SAMPLE; ROI/MDD are account-fraction "
        "(FX in R-units), MDD on the OOS-only equity. `OOS-disp` is the "
        "across-window OOS-Sharpe std (stability). DSR/PBO are computed on the "
        "NET stream only. `engine=both` cells are cross-engine parity-checked at "
        "1e-3; `engine=python` cells are Python-only (real numbers, not "
        "cross-checked) and flip to cross-engine once item 5 ports the indicator.",
        "",
        "| Dataset | Strategy | Engine | win | NET ROI | NET Sharpe | NET PF | "
        "NET MaxDD | OOS-disp | *GROSS ROI* | *GROSS Sharpe* | *GROSS PF* | DSR |",
        "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['dataset']} | {r['strategy']} | {r['engine']} | "
            f"{r['windows']} | "
            f"{_r(r['net_roi'])} | {_r(r['net_sharpe'])} | {_r(r['net_pf'])} | "
            f"{_r(r['net_mdd'])} | {_r(r['net_oos_disp'])} | "
            f"*{_r(r['gross_roi'])}* | *{_r(r['gross_sharpe'])}* | "
            f"*{_r(r['gross_pf'])}* | {_r(r['dsr'], ND_PROB)} |")
    lines += [
        "",
        "_GROSS columns are italicised to mark them frictionless / non-tradeable._",
    ]
    if pbo_by_ds:
        lines += ["", "## Corpus-level PBO (per dataset, core strategies)", "",
                  "Probability of Backtest Overfitting across the core corpus "
                  "(Bailey-Borwein-Lopez de Prado-Zhu 2014, S=16), over the "
                  "strategies' WFO OOS equity curves. Computed on the NET stream. "
                  "Python-only annotation; not a parity cell. Lower is better.", "",
                  "| Dataset | PBO |", "|---|--:|"]
        for ds_id in sorted(pbo_by_ds):
            lines.append(f"| {ds_id} | {_r(pbo_by_ds[ds_id], ND_PROB)} |")
    lines.append("")
    return "\n".join(lines)


def _is_template(text: str) -> bool:
    """A shipped golden TEMPLATE carries angle-bracket tokens; distinguish it
    from a real emitted golden so --check gives the right setup message."""
    return "<" in text and ">" in text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-golden", action="store_true",
                    help="write the versioned golden CSV + leaderboard")
    ap.add_argument("--check", action="store_true",
                    help="re-run and assert byte-identical vs the versioned golden")
    ap.add_argument("--cross-engine", action="store_true",
                    help="compare core cells against a Rust-produced CSV")
    ap.add_argument("--rust-csv", type=Path, default=None)
    ap.add_argument("--tol", type=float, default=1e-3)
    args = ap.parse_args()

    m = load_manifest()
    rows, pbo_by_ds = run_cells(m)
    block = render_csv(rows)
    golden = golden_path(m["spec_version"])

    if args.cross_engine:
        return _cross_engine(rows, args.rust_csv, args.tol)

    if args.emit_golden:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(block)
        LEADERBOARD_MD.parent.mkdir(parents=True, exist_ok=True)
        LEADERBOARD_MD.write_text(
            render_markdown(rows, pbo_by_ds, m["spec_version"]))
        print(f"  wrote {golden.relative_to(REPO)} ({block.count(chr(10))} lines)")
        print(f"  wrote {LEADERBOARD_MD.relative_to(REPO)}")
        return 0

    # default + --check: drift guard (mirror parity_arch.py:104)
    if not golden.exists():
        print(f"  MISSING golden: {golden} (run --emit-golden)", file=sys.stderr)
        return 2
    g_txt = golden.read_text()
    if _is_template(g_txt):
        print(f"  GOLDEN IS AN UN-EMITTED TEMPLATE: {golden.name} contains "
              "'<...>' tokens. Run --emit-golden and commit the real golden.",
              file=sys.stderr)
        return 2
    if block == g_txt:
        print(f"  OK benchmark byte-identical vs {golden.name} "
              f"({block.count(chr(10))} lines)")
        return 0
    print(f"  DRIFT: benchmark differs from committed golden {golden.name}",
          file=sys.stderr)
    g, b = g_txt.splitlines(), block.splitlines()
    for i, (a, c) in enumerate(zip(g, b)):
        if a != c:
            print(f"    line {i}:\n      golden: {a}\n      got   : {c}",
                  file=sys.stderr)
            break
    if len(g) != len(b):
        print(f"    line count {len(g)} golden vs {len(b)} produced",
              file=sys.stderr)
    return 1


def _cross_engine(rows, rust_csv, tol) -> int:
    """Core cells only: Python NET vs Rust NET, and Python GROSS vs Rust GROSS,
    at the 1e-3 band on roi/sharpe/pf/mdd. Python-only cells skipped. FAILS (not
    skips) if zero core cells were actually compared (a header/column mismatch
    can't masquerade as success)."""
    if rust_csv is None or not rust_csv.exists():
        print(f"  --rust-csv not found: {rust_csv}", file=sys.stderr)
        return 2
    # Per-dataset cross-engine exclusions: a dataset with a documented known
    # divergence (manifest `cross_engine_check = false`) stays in the golden
    # NET/GROSS columns but is skipped here, so the gate reflects only the cells
    # we actually claim cross-engine parity for.
    with open(MANIFEST, "rb") as _mf:
        _excluded = {d["id"] for d in tomllib.load(_mf).get("datasets", [])
                     if not d.get("cross_engine_check", True)}
    for _ds in sorted(_excluded):
        print(f"  NOTE cross-engine SKIP {_ds} "
              "(manifest cross_engine_check=false; documented known divergence)")
    py = {(r["dataset"], r["strategy"]): r for r in rows
          if r["core"] == "1" and r["dataset"] not in _excluded}
    rr = {}
    with rust_csv.open() as f:
        for row in csv.DictReader(f):
            rr[(row["dataset"], row["strategy"])] = row
    bad = 0
    compared = 0
    cmp_cols = (("net_sharpe", "net_sharpe"), ("net_roi", "net_roi"),
                ("net_pf", "net_pf"), ("net_mdd", "net_mdd"),
                ("gross_sharpe", "gross_sharpe"), ("gross_roi", "gross_roi"),
                ("gross_pf", "gross_pf"), ("gross_mdd", "gross_mdd"))
    for key, p in py.items():
        if key not in rr:
            print(f"  MISSING rust cell {key}", file=sys.stderr); bad += 1; continue
        r = rr[key]
        for pcol, rcol in cmp_cols:
            if rcol not in r:
                print(f"  rust CSV missing column {rcol} (header mismatch?)",
                      file=sys.stderr)
                continue
            try:
                a, b = float(p[pcol]), float(r[rcol])
            except (TypeError, ValueError):
                continue
            compared += 1
            denom = max(abs(a), abs(b), 1.0)
            if abs(a - b) / denom > tol:
                print(f"  MISMATCH {key} {pcol}: py={a} rust={b}", file=sys.stderr)
                bad += 1
    if compared == 0:
        print("  FAIL: zero core cells compared (header/column mismatch or empty "
              "Rust CSV) — refusing to report a vacuous pass", file=sys.stderr)
        return 1
    status = "OK" if bad == 0 else "FAIL"
    print(f"\nbenchmark cross-engine: {len(py)} core cells, {compared} comparisons, "
          f"{bad} bad -> {status}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
