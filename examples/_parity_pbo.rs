//! Cross-language parity harness binary (PBO/CSCV).
//! Static runner used by tools/parity_pbo.py.
#![cfg(feature = "overfit")]

use quant_research_framework_rs::pbo::cscv;

fn parse_matrix(t: usize, n: usize, body: &str) -> Vec<Vec<f64>> {
    let mut m = vec![vec![0.0f64; n]; t];
    for (ti, row) in body.split(';').enumerate() {
        for (ni, tok) in row.split(',').enumerate() {
            m[ti][ni] = tok.trim().parse::<f64>().expect("parse f64");
        }
    }
    m
}

fn main() {
    let path = std::env::args().nth(1).expect("fixture file path arg");
    let contents = std::fs::read_to_string(&path).expect("read fixture");
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.splitn(5, '|').collect();
        assert!(parts.len() == 5, "bad fixture line: {line}");
        let name = parts[0].trim();
        let s: usize = parts[1].trim().parse().expect("S");
        let t: usize = parts[2].trim().parse().expect("T");
        let n: usize = parts[3].trim().parse().expect("N");
        let m = parse_matrix(t, n, parts[4]);
        let res = cscv(&m, s);
        println!("{name}_pbo={:.12}", res.pbo);
        println!("{name}_nsplits={:.12}", res.n_splits as f64);
    }
}
