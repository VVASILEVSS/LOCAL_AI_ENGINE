# ZigZag Comparison Report

## Settings
- **market_type**: future
- **mode**: hybrid_atr
- **length**: None
- **percent**: None
- **confirmation_mode**: close
- **limit**: 300
- **debug**: False
- **timeframes**: ['15m', '1h', '4h', '1d']

## Summary Table

| Symbol | Global Bias | Stack Bias | Dominant Bias | Alignment | Quality | Confidence | Consistency | Early Reversal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XAUT/USDT | bullish | bullish | bullish | aligned | 88.5 | 100.0 | 100.0 | no |
| BTC/USDT | bearish | bearish | bearish | mixed | 69.52 | 78.0 | 80.0 | yes |
| ETH/USDT | bearish | mixed | bearish | mixed | 50.43 | 48.67 | 60.0 | yes |

## Global Bias Overview

This section shows the main higher-timeframe context for each symbol.

### BTC/USDT
- **Global Bias**: bearish
- **Stack Bias**: bearish
- **Dominant Bias**: bearish
- **Alignment**: mixed
- **Dominant TF**: 1h
- **Signal Quality State**: moderate
- **Verdict**: bearish bias, watch lower-TF reversal

### XAUT/USDT
- **Global Bias**: bullish
- **Stack Bias**: bullish
- **Dominant Bias**: bullish
- **Alignment**: aligned
- **Dominant TF**: 1h
- **Signal Quality State**: strong
- **Verdict**: clean bullish continuation

### ETH/USDT
- **Global Bias**: bearish
- **Stack Bias**: mixed
- **Dominant Bias**: bearish
- **Alignment**: mixed
- **Dominant TF**: 1h
- **Signal Quality State**: weak
- **Verdict**: weak / noisy setup

## Early Reversal Signals

This section highlights lower-timeframe disagreement with the weighted higher-timeframe bias.

### BTC/USDT
- **Early Reversal**: yes
- **Strength**: 0.5
- **Type**: soft
- **Leading TF**: 1h
- **Weighted Bias**: bearish
- **Disagreement Count**: 1
- **Signals**:
  - 1h: bullish
- **Context**: Lower TF disagrees with weighted bias

### XAUT/USDT
- **Early Reversal**: no
- **Strength**: 0.0
- **Type**: none
- **Leading TF**: none
- **Weighted Bias**: bullish
- **Disagreement Count**: 0
- **Signals**: none
- **Context**: No early reversal disagreement detected

### ETH/USDT
- **Early Reversal**: yes
- **Strength**: 0.5
- **Type**: soft
- **Leading TF**: 15m
- **Weighted Bias**: bearish
- **Disagreement Count**: 1
- **Signals**:
  - 15m: bullish
- **Context**: Lower TF disagrees with weighted bias

## Key Level Confluence

This section shows important price zones confirmed by multiple timeframes.

### BTC/USDT
- **80421.44** | TFs=15m, 1h, 1d | count=12 | priority=high | spread=557.5
- **79596.84** | TFs=15m, 4h | count=5 | priority=medium | spread=289.2
- **74868.0** | TFs=1h, 4h | count=2 | priority=medium | spread=0.0
- **79137.4** | TFs=1h, 4h | count=2 | priority=medium | spread=0.0
- **82460.5** | TFs=15m, 1h | count=2 | priority=medium | spread=0.0

### XAUT/USDT
- **4636.62** | TFs=15m, 1h, 4h | count=7 | priority=high | spread=26.6
- **4746.85** | TFs=15m, 1h, 4h | count=5 | priority=high | spread=22.08
- **4716.84** | TFs=15m, 4h | count=5 | priority=medium | spread=5.75
- **4507.77** | TFs=1h, 4h | count=4 | priority=medium | spread=13.44
- **4665.73** | TFs=15m, 4h | count=4 | priority=medium | spread=18.58

### ETH/USDT
- **2316.24** | TFs=15m, 1h | count=6 | priority=medium | spread=10.65
- **2342.66** | TFs=15m, 1h | count=4 | priority=medium | spread=8.69
- **2422.68** | TFs=1h, 4h | count=3 | priority=medium | spread=0.48
- **1936.54** | TFs=4h, 1d | count=2 | priority=medium | spread=0.0
- **2218.83** | TFs=1h, 4h | count=2 | priority=medium | spread=0.0

## Detailed Results

### BTC/USDT
- global_bias: bearish
- stack_bias: bearish
- dominant_bias: bearish
- alignment: mixed
- quality_score: 69.52
- confidence_score: 78.0
- consistency_score: 80.0
- signal_quality_state: moderate
- early_reversal: True
- pattern_mismatch: True
- pattern_mismatch_severity: soft
- pattern_conflict: False
- pattern_conflict_severity: soft

### XAUT/USDT
- global_bias: bullish
- stack_bias: bullish
- dominant_bias: bullish
- alignment: aligned
- quality_score: 88.5
- confidence_score: 100.0
- consistency_score: 100.0
- signal_quality_state: strong
- early_reversal: False
- pattern_mismatch: True
- pattern_mismatch_severity: soft
- pattern_conflict: False
- pattern_conflict_severity: soft

### ETH/USDT
- global_bias: bearish
- stack_bias: mixed
- dominant_bias: bearish
- alignment: mixed
- quality_score: 50.43
- confidence_score: 48.67
- consistency_score: 60.0
- signal_quality_state: weak
- early_reversal: True
- pattern_mismatch: True
- pattern_mismatch_severity: soft
- pattern_conflict: False
- pattern_conflict_severity: soft

## Notes

- **Global Bias** is weighted toward higher timeframes.
- **Early Reversal** means lower TFs are starting to disagree with the weighted higher-TF bias.
- **Key Level Confluence** keeps only useful clusters and filters low-signal noise.
- **pattern_mismatch** flags local structure/pattern disagreement.
- **pattern_conflict** is a stronger flag when mismatches cluster across multiple or higher TFs.
- **signal_quality_state** provides a quick operational verdict.