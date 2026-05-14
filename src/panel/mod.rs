//! Multi-asset panel plugin (Phase 2). Behind `#[cfg(feature = "panel")]`.
//!
//! Mirror of Python's ``backtester.panel`` package. Item #1 lands the
//! data loader; subsequent Phase 2 items (#4 cross-asset regime,
//! #5(iter) basket orchestrator, #6-#8 sizing/neutralisation, #44-#45
//! objective+constraints) plug into the same module.

#![cfg(feature = "panel")]

pub mod loader;
pub mod regime;
pub use loader::{PanelData, PanelError, load_panel};
pub use regime::{
    PanelRegimeDetector, PerAssetRegime, MarketRegime,
    LABEL_RANGING, LABEL_UPTREND, LABEL_DOWNTREND, LABEL_NAMES,
};
