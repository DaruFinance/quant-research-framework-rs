//! Panel-level strategy primitives (item #8 Rust mirror).

#![cfg(feature = "panel")]

pub mod long_short;
pub use long_short::{LongShortBasket, momentum_alpha};
