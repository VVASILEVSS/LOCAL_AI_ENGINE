import pickle, json, os

print("=" * 60)
print("ML DATASET INSPECTOR")
print("=" * 60)

# 1. Model features
print("\n[1] МОДЕЛЬ — ожидаемые фичи:")
pipe = pickle.load(open('results/model.pkl', 'rb'))
feats = pipe.feature_names_in_
print(f"    Всего: {len(feats)} фичей")
for i, f in enumerate(feats, 1):
    print(f"    {i:3d}. {f}")

# 2. Feature importance
print("\n[2] ТОП-15 ВАЖНЫХ ФИЧЕЙ:")
imp = json.load(open('results/feature_importance.json'))
for i, item in enumerate(imp[:15], 1):
    if isinstance(item, dict):
        f = item.get('feature', item.get('name', str(item)))
        v = item.get('importance', item.get('value', 0))
        print(f"    {i:2d}. {f:30s} {v:.4f}")
    else:
        print(f"    {i:2d}. {item}")

zero_feats = [item.get('feature','') for item in imp if isinstance(item,dict) and item.get('importance',0) < 0.001]
print(f"\n    Нулевые ({len(zero_feats)} шт):")
for f in zero_feats[:10]:
    print(f"    x   {f}")

# 3. Training dataset
print("\n[3] ТРЕНИРОВОЧНЫЙ ДАТАСЕТ (features.csv):")
import pandas as pd
if os.path.exists('results/features.csv'):
    df = pd.read_csv('results/features.csv')
    print(f"    Строк: {len(df)}, Колонок: {len(df.columns)}")
    if 'label' in df.columns:
        print(f"    Классы: {df['label'].value_counts().to_dict()}")
    print(f"    Колонки: {df.columns.tolist()}")
else:
    print("    features.csv не найден")

# 4. OHLCV data
print("\n[4] OHLCV ДАННЫЕ:")
for fn in ['BTCUSDT_4h', 'BTCUSDT_1h', 'ETHUSDT_4h', 'SOLUSDT_4h']:
    path = f'data/ohlcv/current/{fn}.csv'
    if os.path.exists(path):
        df2 = pd.read_csv(path)
        print(f"    {fn}: {len(df2)} свечей, последние 3:")
        print(df2.tail(3)[['timestamp','close','volume']].to_string(index=False))

# 5. Feature match
print("\n[5] СОВПАДЕНИЕ: модель vs OHLCV fallback")
ohlcv_can_build = {
    'atr','bb_width','body_ratio','roc_5','vol_avg','vol_std','vol_trend',
    'vol_ratio_row','return_last','return_mean','rsi','wick_lower','wick_upper',
    'close_position','price_range_pct','ema_slope_pct','tf_4h','tf_ordinal',
    'vol_median_ratio','vol_ratio_entry','is_bear','higher_highs_ratio',
    'lower_lows_ratio','up_bars_ratio','return_3bar','return_6bar','return_full',
    'return_std','engulfing_strength','dist_to_high_pct','dist_to_low_pct',
    'range_contraction','price_accel','macd_pct','price_move_pct','price_range'
}
model_set = set(feats)
matched = model_set & ohlcv_can_build
missing = model_set - ohlcv_can_build
print(f"    Модель хочет: {len(model_set)}")
print(f"    OHLCV строит: {len(ohlcv_can_build)}")
print(f"    OK совпадает: {len(matched)}")
print(f"    Нулями (=0.0): {len(missing)}")
print(f"    Пропущенные: {sorted(missing)}")

print("\n" + "=" * 60)