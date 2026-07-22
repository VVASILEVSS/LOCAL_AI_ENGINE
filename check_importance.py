import pickle, json

# Загрузка модели
pipe = pickle.load(open('results/model.pkl', 'rb'))
clf = pipe.named_steps['clf']
feats = pipe.feature_names_in_
importances = clf.feature_importances_

# Сортировка по важности
ranked = sorted(zip(feats, importances), key=lambda x: -x[1])

print("=" * 60)
print(f"РЕАЛЬНАЯ ВАЖНОСТЬ ФИЧЕЙ модели (всего {len(ranked)})")
print("=" * 60)

# OHLCV fallback уже строит эти фичи (31 совпадает по данным check_dataset)
ohlcv_can_build = {
    'atr','bb_width','body_ratio','roc_5','vol_avg','vol_std','vol_trend',
    'vol_ratio_row','return_last','return_mean','rsi','wick_lower','wick_upper',
    'close_position','price_range_pct','ema_slope_pct','tf_4h','tf_ordinal',
    'vol_median_ratio','vol_ratio_entry','is_bear','higher_highs_ratio',
    'lower_lows_ratio','up_bars_ratio','return_3bar','return_6bar','return_full',
    'return_std','engulfing_strength','dist_to_high_pct','dist_to_low_pct',
    'range_contraction','price_accel','macd_pct','price_move_pct','price_range'
}

# Модель требует 50 фичей — которые не строит OHLCV
missing_from_model = set(feats) - ohlcv_can_build

print("\n--- ВСЕ 50 фичей по важности ---")
print(f"{'#':>3} {'СТАТУС':>10} {'ФИЧА':35} {'ВЕС':>8}")
print("-" * 60)
for i, (feat, imp) in enumerate(ranked, 1):
    if feat in ohlcv_can_build:
        status = "  OK"
    else:
        status = " НУЛЬ!"
    print(f"{i:3d} {status:>10} {feat:35s} {imp:.4f}")

print(f"\n--- ПРОПУЩЕННЫЕ (нуждают в 0.0) --- {len(missing_from_model)} шт")
missing_ranked = [(f, imp) for f, imp in ranked if f in missing_from_model]
missing_weight = sum(imp for _, imp in missing_ranked)
total_weight = sum(imp for _, imp in ranked)
print(f"Их суммарный вес: {missing_weight:.4f} из {total_weight:.4f} = {missing_weight/total_weight*100:.1f}%")
print()
print(f"{'#':>3} {'ФИЧА':35} {'ВЕС':>8} {'МОЖНО СЧИТАТЬ?'}")
print("-" * 60)

# Какие можно вычислить из OHLCV
computable = {
    'atr_pct': 'atr/close*100 — легко',
    'rsi_momentum': 'rsi[тек] - rsi[пред] — нужно 2 RSI',
    'row_atr_pct': 'candle_range/atr*100 — легко',
    'vol_std_pct': 'vol_std/vol_avg*100 — легко',
    'volatility_pct': 'std(pct_changes)*100 — уже есть return_std',
    'tf_1h': 'ref_tf=="1h" — легко',
    'tf_1d': 'ref_tf=="1d" — легко',
    'tf_15m': 'ref_tf=="15m" — легко',
    # divergence-зависимые — нельзя из чистого OHLCV:
    'bias_above_threshold': 'нельзя (нужен divergence)',
    'bias_abs_score': 'нельзя',
    'bias_dir': 'нельзя',
    'bias_score': 'нельзя',
    'cmf_score': 'нельзя (CMF = divergence)',
    'flow_abs_change': 'нельзя (flow = divergence)',
    'flow_pct_change': 'нельзя',
    'flow_scale': 'нельзя',
    'price_flow_ratio': 'нельзя',
    'pv_correlation': 'можно: corr(price,volume)',
    'regime_score': 'можно: упрощённый из SMA/ATR',
}

for i, (feat, imp) in enumerate(missing_ranked, 1):
    note = computable.get(feat, '???')
    print(f"{i:3d} {feat:35s} {imp:.4f}   {note}")

# Итог
easy_weight = sum(imp for f, imp in missing_ranked 
                  if f in ('atr_pct','rsi_momentum','row_atr_pct','vol_std_pct',
                           'volatility_pct','tf_1h','tf_1d','tf_15m','pv_correlation','regime_score'))
div_weight = missing_weight - easy_weight

print(f"\n{'='*60}")
print(f"  Легко добавить:    +{easy_weight:.4f} веса ({easy_weight/total_weight*100:.1f}%)")
print(f"  Нельзя (divergence): {div_weight:.4f} веса ({div_weight/total_weight*100:.1f}%)")
print(f"  После добавления:   будет {len(ohlcv_can_build)+10}/50 = {len(ohlcv_can_build)+10} из 50")
print(f"{'='*60}")