# ZigZag module

This folder contains the isolated ZigZag experiment used for:
- swing/pivot detection,
- comparison with TradingView / Binance visual structure,
- LM Studio friendly summaries,
- auto-tuned multi-timeframe analysis.

## Files
- `structural_zigzag.py` — core ZigZag logic
- `zigzag_forecast_test.py` — multi-timeframe forecast/debug runner
- `structure_levels_test.py` — compact level/pivot summary runner
- `__init__.py` — package exports

## Main modes

### 1) `lux_channel`
Closest to the LuxAlgo-style visual structure.
Use this mode when you want to compare with TradingView screenshots.

Recommended for:
- visual matching,
- pivot alignment,
- swing structure checks.

Example:
```bash
python -m core.zigzag.structure_levels_test