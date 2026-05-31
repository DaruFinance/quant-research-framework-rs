//! Deflated Sharpe Ratio (Bailey & López de Prado, 2014) — Rust mirror.
//!
//! 1:1 port of `backtester/dsr.py`. Selection-bias correction for an
//! in-sample maximised Sharpe ratio: when a strategy parameter is chosen
//! by maximising SR over a grid of N trials, the maximised SR is
//! upward-biased. The deflated SR is the probability that the true SR
//! exceeds zero conditional on the observed SR, the trial count, and the
//! higher moments of the per-trade returns.
//!
//! Formulas (Bailey & López de Prado 2014, JPM 40(5):94--107):
//!
//! ```text
//!     SR_0 = sqrt(V[SR_n]) * ((1 - euler_gamma) * Phi^{-1}(1 - 1/N)
//!                           + euler_gamma * Phi^{-1}(1 - 1/(N * e)))
//!     DSR  = Phi( (SR_hat - SR_0) * sqrt(T - 1)
//!                / sqrt(1 - g_3 * SR_hat + (g_4 - 1) * SR_hat^2 / 4) )
//! ```
//!
//! where `g_4` is the *raw* fourth standardised moment (= 3 for a Normal,
//! NOT excess kurtosis); see the comment block in `deflated_sharpe_ratio`.
//!
//! Like the Python module this is a *post-processing* utility — it does
//! not run inside the engine and does not affect the engine's stdout
//! metric block. The cross-language guarantee for this module is the
//! dedicated `tools/parity_dsr.py` scalar harness, NOT the stdout-metric
//! parity surface.
//!
//! `Phi` and `Phi^{-1}` are taken from `statrs` (`Normal::cdf` /
//! `Normal::inverse_cdf`, matching scipy `norm.cdf` / `norm.ppf` to
//! ~1e-12). The coarse A&S 7.1.26 `erf` in `t5_statarb::screen` is
//! deliberately NOT reused here: it has no inverse and its 1.5e-7 forward
//! error would be amplified into `SR_0` at the `1 - 1/N` tail.

use statrs::distribution::{ContinuousCDF, Normal};

/// Euler–Mascheroni constant. Matches `backtester.dsr.EULER_GAMMA`.
pub const EULER_GAMMA: f64 = 0.5772156649015329;

/// Standard-normal helper. `Normal::new(0, 1)` is infallible for these
/// arguments; we unwrap rather than thread a `Result` to keep the public
/// signatures identical to the Python module.
fn standard_normal() -> Normal {
    Normal::new(0.0, 1.0).expect("standard normal is well-defined")
}

/// Sample variance with `ddof = 1` (matches `numpy.var(arr, ddof=1)`).
/// Caller guarantees `xs.len() >= 2`.
fn sample_var_ddof1(xs: &[f64]) -> f64 {
    let n = xs.len() as f64;
    let mean = xs.iter().sum::<f64>() / n;
    let ss: f64 = xs.iter().map(|&x| (x - mean) * (x - mean)).sum();
    ss / (n - 1.0)
}

/// `E[max SR_n]` under the null that the true SR is zero — the `SR_0`
/// quantity of Bailey & López de Prado 2014 §3.
///
/// Mirrors `expected_max_sharpe_under_null`: non-finite trials are
/// dropped; fewer than two finite trials, or non-positive trial-Sharpe
/// variance, return `0.0`.
pub fn expected_max_sharpe_under_null(trial_sharpes: &[f64]) -> f64 {
    let finite: Vec<f64> = trial_sharpes
        .iter()
        .copied()
        .filter(|s| s.is_finite())
        .collect();
    let n = finite.len();
    if n < 2 {
        return 0.0;
    }
    let var_sr = sample_var_ddof1(&finite);
    if var_sr <= 0.0 {
        return 0.0;
    }
    let nd = standard_normal();
    let nf = n as f64;
    let e = std::f64::consts::E;
    var_sr.sqrt()
        * ((1.0 - EULER_GAMMA) * nd.inverse_cdf(1.0 - 1.0 / nf)
            + EULER_GAMMA * nd.inverse_cdf(1.0 - 1.0 / (nf * e)))
}

/// Probability the true Sharpe exceeds zero, conditional on the observed
/// in-sample maximised Sharpe, the per-trial Sharpe variance, and the
/// per-trade return moments. Returns a value in `[0, 1]`, or `NaN` on any
/// of the degenerate guards (mirrors `deflated_sharpe_ratio`).
pub fn deflated_sharpe_ratio(
    sharpe_chosen: f64,
    trial_sharpes: &[f64],
    returns: &[f64],
) -> f64 {
    let rets: Vec<f64> = returns.iter().copied().filter(|r| r.is_finite()).collect();
    let t = rets.len();
    if t < 3 || !sharpe_chosen.is_finite() {
        return f64::NAN;
    }

    let sr_0 = expected_max_sharpe_under_null(trial_sharpes);

    // Per-trade-return higher moments. Bailey & López de Prado (2014),
    // JPM 40(5):94--107, eq. (9): the variance-correction term is
    //     sqrt( 1 - g_3 * SR + (g_4 - 1) * SR^2 / 4 )
    // where g_4 is the *raw* fourth standardised moment
    //     g_4 = E[(x - mu)^4] / sigma^4   (= 3 for a Normal),
    // NOT excess kurtosis. The (g_4 - 1) coefficient is therefore correct
    // as written below: it reduces to (3 - 1)/4 = 0.5 in the Normal case.
    let tf = t as f64;
    let mu = rets.iter().sum::<f64>() / tf;
    let sd = sample_var_ddof1(&rets).sqrt(); // std, ddof=1
    if sd <= 0.0 {
        return f64::NAN;
    }
    // g_3 / g_4 use the population mean of z^k (division by t, not t-1),
    // matching numpy's `np.mean(z ** 3)` / `np.mean(z ** 4)`.
    let mut sum_z3 = 0.0f64;
    let mut sum_z4 = 0.0f64;
    for &r in &rets {
        let z = (r - mu) / sd;
        let z2 = z * z;
        sum_z3 += z2 * z;
        sum_z4 += z2 * z2;
    }
    let g_3 = sum_z3 / tf;
    let g_4 = sum_z4 / tf;

    let denom_sq =
        1.0 - g_3 * sharpe_chosen + (g_4 - 1.0) * sharpe_chosen * sharpe_chosen / 4.0;
    if denom_sq <= 0.0 {
        return f64::NAN;
    }

    let z_hat = (sharpe_chosen - sr_0) * (tf - 1.0).sqrt() / denom_sq.sqrt();
    standard_normal().cdf(z_hat)
}

/// One-line summary mirroring `backtester.dsr.report`. Kept for API
/// parity; nothing in the engine calls it.
pub fn report(sharpe_chosen: f64, trial_sharpes: &[f64], returns: &[f64]) -> String {
    let sr_0 = expected_max_sharpe_under_null(trial_sharpes);
    let dsr = deflated_sharpe_ratio(sharpe_chosen, trial_sharpes, returns);
    let n = trial_sharpes.iter().filter(|s| s.is_finite()).count();
    format!(
        "  DSR  | SR_chosen:{:6.2}  E[max SR|null,N={}]:{:6.2}  haircut:{:6.2}  P(SR_true>0):{:5.3}",
        sharpe_chosen,
        n,
        sr_0,
        sharpe_chosen - sr_0,
        dsr
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    // Golden values precomputed once from backtester/dsr.py on the exact
    // input arrays below; asserted to <1e-9 to pin the port independently
    // of the cross-process parity harness.
    fn approx(a: f64, b: f64, tol: f64) {
        assert!(
            (a - b).abs() <= tol,
            "expected {b} got {a} (|Δ|={})",
            (a - b).abs()
        );
    }

    #[test]
    fn sr0_fewer_than_two_trials_is_zero() {
        assert_eq!(expected_max_sharpe_under_null(&[]), 0.0);
        assert_eq!(expected_max_sharpe_under_null(&[1.3]), 0.0);
        // non-finite trials dropped -> only one finite -> 0.0
        assert_eq!(
            expected_max_sharpe_under_null(&[1.3, f64::NAN, f64::INFINITY]),
            0.0
        );
    }

    #[test]
    fn sr0_zero_variance_is_zero() {
        assert_eq!(expected_max_sharpe_under_null(&[0.5, 0.5, 0.5, 0.5]), 0.0);
    }

    #[test]
    fn dsr_too_few_returns_is_nan() {
        assert!(deflated_sharpe_ratio(1.0, &[0.1, 0.2, 0.3], &[0.01, 0.02]).is_nan());
    }

    #[test]
    fn dsr_nonfinite_sharpe_is_nan() {
        let rets = [0.01, -0.02, 0.03, 0.0, 0.015];
        assert!(deflated_sharpe_ratio(f64::NAN, &[0.1, 0.2], &rets).is_nan());
        assert!(deflated_sharpe_ratio(f64::INFINITY, &[0.1, 0.2], &rets).is_nan());
    }

    #[test]
    fn dsr_zero_dispersion_returns_is_nan() {
        // constant returns -> sd == 0 -> NaN
        assert!(deflated_sharpe_ratio(1.0, &[0.1, 0.2, 0.3], &[0.5; 8]).is_nan());
    }

    #[test]
    fn golden_clean_case() {
        // trial_sharpes = [0.1, 0.4, 0.9, 0.6, 0.3, 0.8]
        // returns       = [0.012, -0.004, 0.021, -0.011, 0.008, 0.015,
        //                  -0.006, 0.019, 0.003, -0.009]
        // sharpe_chosen = 0.9
        // Computed from backtester/dsr.py (scipy norm):
        //   expected_max_sharpe_under_null -> 0.3979082244143515
        //   deflated_sharpe_ratio          -> 0.9301178821563774
        let trials = [0.1, 0.4, 0.9, 0.6, 0.3, 0.8];
        let rets = [
            0.012, -0.004, 0.021, -0.011, 0.008, 0.015, -0.006, 0.019, 0.003, -0.009,
        ];
        let sr0 = expected_max_sharpe_under_null(&trials);
        approx(sr0, 0.3979082244143515, 1e-9);
        let dsr = deflated_sharpe_ratio(0.9, &trials, &rets);
        approx(dsr, 0.9301178821563774, 1e-9);
        assert!((0.0..=1.0).contains(&dsr), "dsr out of range: {dsr}");
    }
}
