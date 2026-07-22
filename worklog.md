---
Task ID: 3
Agent: main
Task: Update diag_candidates.py, diag_pivots.py, PS1, validate to v2.3 with BTC-optimized profiles and regime/bias scoring

Work Log:
- Read all current files: diag_candidates.py (v2.2), diag_pivots.py (v2.1), run_candidates.py (v2.1), autotune_diag.py (v2.0), v11generate_unified_dataset.ps1, validate_ad_dataset.py
- Analyzed user's experimental BTC parameters and mapped them to profile structure
- Updated diag_candidates.py to v2.3: new _calc_regime(), _calc_bias() functions, BTC 1h profile (pivotLeft/Right=9, profPricePct=0.0075, profFlowPct=0.004, profAtrMult=0.4, cooldown=10), regime params (lookback=11, emaSlope=0.08, emaRange=0.12, confirm=3), bias weights (flowSlope=60, cmf=25, regime=15, threshold=2), 9 new fields per candidate JSON
- Updated diag_pivots.py to v2.3: synchronized all profiles and _apply_overrides() with new regime/bias parameters
- Updated run_candidates.py: version bump to v2.3
- Updated v11generate_unified_dataset.ps1: 9 new columns (regime_score, regime_trend, regime_confirmed, bias_score, bias_dir, bias_above, flow_slope, cmf_score) — total 42 columns
- Updated validate_ad_dataset.py: EXPECTED_COL_COUNT 33→42, added 9 new required columns

Stage Summary:
- v2.3 deployed with BTC-optimized parameters and regime/bias scoring
- All 4 TF profiles (15m/1h/4h/1d) have regime/bias parameters
- Unified dataset now has 42 columns (was 33)
- Candidate JSON now includes: regimeScore, regimeTrend, regimeConfirmed, biasScore, biasDir, biasAbove, flowSlope, cmfScore

---
Task ID: 4
Agent: main
Task: Add pagination to data_provider.py + create fetch_ohlcv.py CLI for deep historical data

Work Log:
- Analyzed data_provider.py: single fetch_ohlcv() call with limit=500 (Binance max=1000 per request)
- Updated data_provider.py to v2.1: added auto-pagination for limit>1000, batch fetching with `since` parameter, deduplication, rate limiting (0.3s between batches), _TF_MS mapping for all timeframes
- Created tools/fetch_ohlcv.py: CLI script for easy data fetching with --symbol, --tf, --limit, --spot, --force flags

Stage Summary:
- data_provider.py v2.1 supports limit up to ~10000 bars with automatic pagination
- tools/fetch_ohlcv.py provides convenient CLI: `python tools/fetch_ohlcv.py --symbol BTCUSDT --tf 1h --limit 5000`
- User can now fetch 5000 bars of 1h data (~208 days) for better candidate density

---
Task ID: 5
Agent: main
Task: Step 5 — divergence context integration + Pine export + ollama_client.py modification

Work Log:
- Read ollama_client.py (1753 lines) — identified PRO_TA_USER_PROMPT template and analyze_multi_images() function
- Read volume_filters.py — existing A/D context provider (current-snapshot, no divergence history)
- Created core/divergence_context.py: DivergenceSignal dataclass, load_candidates(), resolve_timestamps(), filter_by_lookback(), format_for_prompt(), get_multi_context(), get_multi_symbol_context()
- Created tools/export_pine_params.py: reads autotune JSON, exports Pine Script input() declarations, auto-rollback (current→prev), per-symbol folders
- Modified ollama_client.py: added `from core.divergence_context import get_multi_context`, added `{divergence_context}` block in PRO_TA_USER_PROMPT, added divergence_str computation in analyze_multi_images() with try/except fallback

Stage Summary:
- divergence_context.py reads *_candidates.json + source CSV, resolves i→datetime, filters by lookback_hours, formats for LLM
- export_pine_params.py exports autotune results to results/pine_exports/{SYMBOL}/{SYMBOL}_{TF}_params.pine with rollback
- ollama_client.py now includes historical A/D divergences in LLM prompt between state_context and backtest
- Configuration via prev_analysis dict keys: symbol, divergence_timeframes, divergence_lookback_hours

---
Task ID: 6
Agent: main
Task: Improve ML filter log output — add informative header and summary line

Work Log:
- Read ML filter block in ollama_client.py (lines 1462-1684)
- Replaced single log line with informative header: `ML FILTER [PHASE1] SYMBOL | signal_type | timeframe | price=XXXX`
- Added summary line after prediction: `→ PASS/FILTERED | confidence=XX.X% | model=... | features=X/50`
- Extracted _ml_sym, _ml_price, _ml_tf from data dict for header
- Extracted _ml_verdict, _ml_matched as local vars for summary line

Stage Summary:
- ML filter log now shows clear header with signal context (symbol, type, TF, price)
- Summary line shows verdict, confidence %, model name, and feature match count
- Example output:
  ML FILTER [PHASE1] XAUTUSDT | false_breakout_down | 4h | price=2430.5000
    OHLCV fallback built 28 features matching model
    P(good)=0.731 | threshold=0.75
    → FILTERED | confidence=73.1% | model=RandomForestClassifier | features=28/50
