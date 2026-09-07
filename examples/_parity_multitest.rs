//! Cross-language parity harness binary (multitest/dsr-gapfill/haircut).
//! Static runner used by tools/parity_multitest.py.
#![cfg(feature = "overfit")]

use quant_research_framework_rs::multitest::{
    bonferroni, holm, bh_fdr, sharpe_pvalues,
    white_reality_check_indexed, romano_wolf_indexed,
};
use quant_research_framework_rs::dsr::{
    probabilistic_sharpe_ratio, min_track_record_length, min_backtest_length,
};
use quant_research_framework_rs::haircut::haircut_sharpe_ratio;

const ALPHA: f64 = 0.05;

fn pcsv(s: &str) -> Vec<f64> {
    s.split(',').map(|x| x.trim().parse::<f64>().expect("f64")).collect()
}

fn fmt(v: f64) -> String {
    if v.is_nan() { "nan".to_string() }
    else if v.is_infinite() { if v > 0.0 {"inf".into()} else {"-inf".into()} }
    else { format!("{:.12}", v) }
}

fn main() {
    let path = std::env::args().nth(1).expect("fixture path");
    let contents = std::fs::read_to_string(&path).expect("read fixture");
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') { continue; }
        let mut it = line.splitn(2, '|');
        let kind = it.next().unwrap();
        let rest = it.next().unwrap_or("");

        match kind {
            "PVAL" => {
                let p: Vec<&str> = rest.splitn(2, '|').collect();
                let name = p[0];
                let pv = pcsv(p[1]);
                for (tag, mask) in [
                    ("bon", bonferroni(&pv, ALPHA)),
                    ("holm", holm(&pv, ALPHA)),
                    ("bh", bh_fdr(&pv, ALPHA)),
                ] {
                    for (i, b) in mask.iter().enumerate() {
                        println!("{name}_{tag}_{i}={:.12}", if *b {1.0} else {0.0});
                    }
                }
            }
            "SHPV" => {
                let p: Vec<&str> = rest.splitn(3, '|').collect();
                let name = p[0];
                let t: usize = p[1].trim().parse().unwrap();
                let sh = pcsv(p[2]);
                for (i, pv) in sharpe_pvalues(&sh, t).iter().enumerate() {
                    println!("{name}_p_{i}={:.12}", pv);
                }
            }
            "PSR" => {
                let p: Vec<&str> = rest.splitn(5, '|').collect();
                let name = p[0];
                let sc: f64 = p[1].trim().parse().unwrap();
                let srb: f64 = p[2].trim().parse().unwrap();
                let prob: f64 = p[3].trim().parse().unwrap();
                let rets = pcsv(p[4]);
                println!("{name}_psr={}", fmt(probabilistic_sharpe_ratio(sc, &rets, srb)));
                println!("{name}_mtrl={}", fmt(min_track_record_length(sc, &rets, srb, prob)));
            }
            "MBTL" => {
                let p: Vec<&str> = rest.splitn(3, '|').collect();
                let name = p[0];
                let nt: usize = p[1].trim().parse().unwrap();
                let srt: f64 = p[2].trim().parse().unwrap();
                println!("{name}_mbtl={}", fmt(min_backtest_length(nt, srt)));
            }
            "HCUT" => {
                let p: Vec<&str> = rest.splitn(6, '|').collect();
                let name = p[0];
                let sr: f64 = p[1].trim().parse().unwrap();
                let t: usize = p[2].trim().parse().unwrap();
                let nt: usize = p[3].trim().parse().unwrap();
                let meth: u8 = p[4].trim().parse().unwrap();
                let freq: f64 = p[5].trim().parse().unwrap();
                let h = haircut_sharpe_ratio(sr, t, nt, meth, freq);
                println!("{name}_hc_sr={}", fmt(h.haircut_sr));
                println!("{name}_hc_pct={}", fmt(h.haircut_pct));
                println!("{name}_hc_padj={}", fmt(h.p_adj));
            }
            "BOOT" => {
                let p: Vec<&str> = rest.splitn(6, '|').collect();
                let name = p[0];
                let t: usize = p[1].trim().parse().unwrap();
                let n: usize = p[2].trim().parse().unwrap();
                let _nres: usize = p[3].trim().parse().unwrap();
                let mut r = vec![vec![0.0f64; n]; t];
                for (ti, row) in p[4].split(';').enumerate() {
                    for (ni, tok) in row.split(',').enumerate() {
                        r[ti][ni] = tok.trim().parse::<f64>().unwrap();
                    }
                }
                let idx: Vec<Vec<usize>> = p[5].split(';')
                    .map(|row| row.split(',')
                        .map(|x| x.trim().parse::<usize>().unwrap()).collect())
                    .collect();
                let wrc = white_reality_check_indexed(&r, &idx);
                println!("{name}_wrc_vobs={}", fmt(wrc.v_obs));
                println!("{name}_wrc_pval={}", fmt(wrc.pvalue));
                let rw = romano_wolf_indexed(&r, &idx, ALPHA);
                println!("{name}_rw_crit={}", fmt(rw.crit));
                for (i, to) in rw.t_obs.iter().enumerate() {
                    println!("{name}_rw_tobs_{i}={}", fmt(*to));
                }
                for (i, b) in rw.rejected.iter().enumerate() {
                    println!("{name}_rw_{i}={:.12}", if *b {1.0} else {0.0});
                }
            }
            _ => {}
        }
    }
}
