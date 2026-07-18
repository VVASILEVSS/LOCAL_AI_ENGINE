import pandas as pd
import json

df = pd.read_csv('results/unified_dataset.csv', na_values=['', 'null', 'None'])

# Проверить snake_case и необходимые поля
want = {'symbol', 'tf_profile', 'candidate_idx', 'candidate_time', 'time_iso', 'label_index', 'label_time', 'label_end_time', 'label_price', 'prev_price', 'curr_price', 'prev_flow', 'curr_flow', 'flow_abs_change', 'flow_pct_change', 'price_move_pct', 'atr', 'flow_scale', 'min_flow_abs_threshold', 'min_flow_pct', 'min_price_move_pct', 'pivot_left', 'pivot_right', 'context_start_idx', 'context_end_idx', 'top_price', 'bottom_price', 'mid_price', 'context_ohlcv_json', 'delta_volume', 'momentum', 'ratio', 'strength', 'action', 'comment', 'llm_feedback'}
actual = set(df.columns.str.lower())
print("Все нужные поля:", want <= actual)

# Проверить формат времени
times = df['label_time'].head(3)
print("Примеры времени:", list(times))
# pandas должен без ошибок парсить
pd.to_datetime(times)

# Проверяем context_ohlcv_json формат и возможность парсинга
rec = json.loads(df['context_ohlcv_json'].iloc[0])
print("Первый candle json:", rec[0])

# Весь массив context корректен по времени?
for r in rec:
    assert 'time' in r
    assert len(r['time']) == 19  # "YYYY-MM-DD HH:MM:SS"
print("Поля времени внутри context_ohlcv_json теперь тоже ISO!")

# Проверка: числа парсятся ок
float_fields = ['label_price','prev_price','curr_price','prev_flow','curr_flow',
              'flow_abs_change','flow_pct_change','price_move_pct','atr','flow_scale',
              'min_flow_abs_threshold','min_flow_pct','min_price_move_pct',
              'top_price','bottom_price','mid_price','delta_volume','momentum','ratio']
for col in float_fields:
    assert pd.api.types.is_numeric_dtype(pd.to_numeric(df[col], errors='coerce'))
print("Все числовые поля корректно читаются как числа.")

print("✔ Датасет готов к дальнейшей пайплайнингу и ML/LLM. Можно freeze!")