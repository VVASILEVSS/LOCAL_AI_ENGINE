import json
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR.parent / "results" / "unified_dataset_v10.csv"

df = pd.read_csv(DATA_FILE)

print(f"Файл: {DATA_FILE}")
print(f"Строк: {len(df)}")
print()

# -----------------------------
# 1) Проверка обязательных колонок
# -----------------------------
required_cols = [
    "symbol",
    "tf_profile",
    "timeframe",
    "source_file",
    "rows_count",
    "candidate_index",
    "label_index",
    "pivot_right",
    "context_n",
    "label_anchor",
    "label_value",
    "label_datetime",
    "candidate_time",
    "candidate_type",
    "prev_price",
    "curr_price",
    "price_move_pct",
    "prev_flow",
    "curr_flow",
    "flow_pct_change",
    "flow_abs_change",
    "flow_scale",
    "min_flow_abs_threshold",
    "atr",
    "candidate_score",
    "candidate_quality",
    "candidate_strength",
    "candidate_flow_ratio",
    "candidate_atr_ratio",
    "context_json",
]

missing = [c for c in required_cols if c not in df.columns]
if missing:
    print("❌ Не хватает колонок:")
    for c in missing:
        print(f"  - {c}")
else:
    print("✅ Все обязательные колонки присутствуют.")

print()

# -----------------------------
# 2) Проверка времени
# -----------------------------
time_cols = [c for c in ["label_datetime", "candidate_time"] if c in df.columns]
for col in time_cols:
    try:
        pd.to_datetime(df[col], errors="raise")
        print(f"✅ {col}: все значения корректны.")
    except Exception as e:
        print(f"❌ Ошибка в колонке {col}: {e}")

print()

# -----------------------------
# 3) Проверка JSON
# -----------------------------
json_errors = 0
for i, row in df.iterrows():
    try:
        json.loads(row["context_json"])
    except Exception as e:
        print(f"❌ row {i} context_json error: {e}")
        json_errors += 1

if json_errors == 0:
    print("✅ Все context_json — валидный JSON.")
else:
    print(f"❌ Некорректных context_json: {json_errors}")

print()

# -----------------------------
# 4) Проверка score / quality
# -----------------------------
if "candidate_score" in df.columns:
    try:
        scores = pd.to_numeric(df["candidate_score"], errors="coerce")
        bad_scores = scores.isna().sum()
        out_of_range = ((scores < 0) | (scores > 100)).sum()

        print(f"✅ candidate_score: min={scores.min()}, max={scores.max()}")
        if bad_scores > 0:
            print(f"⚠️ Пустых/некорректных candidate_score: {bad_scores}")
        if out_of_range > 0:
            print(f"⚠️ score вне диапазона 0..100: {out_of_range}")
    except Exception as e:
        print(f"❌ Ошибка при проверке candidate_score: {e}")

if "candidate_quality" in df.columns:
    qualities = df["candidate_quality"].fillna("NA").astype(str).str.lower()
    print("✅ candidate_quality distribution:")
    print(qualities.value_counts(dropna=False).to_string())

print()

# -----------------------------
# 5) Проверка обязательных значений
# -----------------------------
check_cols = [
    "symbol",
    "tf_profile",
    "candidate_index",
    "label_index",
    "candidate_type",
    "candidate_score",
    "candidate_quality",
]

for col in check_cols:
    if col in df.columns:
        empty_count = df[col].isna().sum() + (df[col].astype(str).str.strip() == "").sum()
        print(f"⚠️ Пустых значений в {col}: {empty_count}")

print()

# -----------------------------
# 6) Доп. логика: label_in_window в JSON
# -----------------------------
label_hits = 0
label_misses = 0

for i, row in df.iterrows():
    try:
        ctx = json.loads(row["context_json"])
        has_yes = any(
            isinstance(item, dict) and str(item.get("label_in_window", "")).upper() == "YES"
            for item in ctx
        )
        if has_yes:
            label_hits += 1
        else:
            label_misses += 1
    except Exception:
        label_misses += 1

print(f"✅ context_json с label_in_window=YES: {label_hits}")
print(f"⚠️ context_json без label_in_window=YES: {label_misses}")

print()

# -----------------------------
# 7) Итог
# -----------------------------
if json_errors == 0 and not missing:
    print("🎉 Валидация пройдена.")
else:
    print("⚠️ Валидация завершена с замечаниями.")