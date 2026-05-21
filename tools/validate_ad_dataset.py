import pandas as pd
import json

df = pd.read_csv("unified_dataset_cleaned.csv")
# Проверка времени
try:
    for col in ["time_iso", "label_time"]:
        pd.to_datetime(df[col])
    print("Все временные поля корректны.")
except Exception as e:
    print(f"Ошибка времени: {e}")

errs = 0
for i, row in df.iterrows():
    try:
        json.loads(row['context_ohlcv_json'])
    except Exception as e:
        print(f"row {i} json error: {e}")
        errs += 1
if errs == 0:
    print("Все context_ohlcv_json – валидный JSON.")
else:
    print(f"Всего некорректных context json: {errs}")