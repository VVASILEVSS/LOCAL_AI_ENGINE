# core/tools/normalize_dataset.py
# Назначение: очистить и нормализовать unified_dataset.csv для работы с моделью.
# Отвечает за: экранирование JSON, валидация столбцов, удаление неполных строк.
# Связано с: tests/AD/unified_dataset.csv.

import json
import pandas as pd
import sys
from pathlib import Path

def normalize_json_in_csv(path_in: str, path_out: str) -> None:
    """
    Нормализует CSV с JSON в колонках:
    - Правильно экранирует JSON (\" вместо "")
    - Валидирует JSON
    - Удаляет неполные строки
    - Сохраняет в новый файл
    """
    
    print(f"📂 Читаю файл: {path_in}")
    
    try:
        # Читаем с минимальной обработкой
        df = pd.read_csv(path_in, dtype=str, na_filter=False)
    except FileNotFoundError:
        print(f"❌ Файл не найден: {path_in}")
        return
    except Exception as e:
        print(f"❌ Ошибка чтения CSV: {e}")
        return
    
    print(f"✅ Загружено {len(df)} строк, {len(df.columns)} колонок")
    print(f"📋 Колонки: {list(df.columns)}")
    
    # Проверяем обязательные колонки
    required_cols = {
        "symbol", "tf_profile", "candidate_idx", "candidate_time",
        "label_price", "context_ohlcv_json", "strength"
    }
    missing = required_cols - set(df.columns)
    if missing:
        print(f"⚠️ Отсутствуют колонки: {missing}")
        print("Продолжу, но результат может быть неполным")
    
    # --- Нормализация JSON ---
    print("\n🔧 Нормализую JSON в context_ohlcv_json...")
    
    def fix_json_column(val: str) -> str:
        """Исправляет JSON экранирование"""
        if not val or val == "":
            return "[]"
        
        # Если уже правильный JSON (с \"), то ок
        if '\\"' in val:
            try:
                json.loads(val)
                return val
            except:
                pass
        
        # Если двойные кавычки (""), заменяем на правильные \"
        fixed = val.replace('""', '\\"')
        
        # Убираем лишние кавычки в начале/конце, если они есть
        if fixed.startswith('"['):
            fixed = fixed[1:]
        if fixed.endswith(']"'):
            fixed = fixed[:-1]
        
        # Пытаемся распарсить
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON не валиден: {fixed[:100]}... ({e})")
            return "[]"
    
    valid_rows = []
    invalid_count = 0
    
    for idx, row in df.iterrows():
        try:
            # Проверяем критичные поля
            if pd.isna(row.get("label_price")) or row.get("label_price") == "":
                invalid_count += 1
                continue
            
            if pd.isna(row.get("symbol")) or row.get("symbol") == "":
                invalid_count += 1
                continue
            
            # Пытаемся исправить JSON
            if "context_ohlcv_json" in df.columns:
                json_val = row.get("context_ohlcv_json", "[]")
                row["context_ohlcv_json"] = fix_json_column(str(json_val))
            
            valid_rows.append(row)
        
        except Exception as e:
            print(f"⚠️ Строка {idx} повреждена: {e}")
            invalid_count += 1
            continue
    
    df_clean = pd.DataFrame(valid_rows)
    
    print(f"\n✅ Обработано {len(valid_rows)} валидных строк")
    print(f"❌ Пропущено {invalid_count} неполных/повреждённых строк")
    
    # --- Заполнение пустых полей ---
    print("\n🔧 Заполняю пустые поля...")
    
    fill_values = {
        "comment": "",
        "llm_feedback": "",
        "action": "analyze",
    }
    
    for col, default in fill_values.items():
        if col in df_clean.columns:
            df_clean[col] = df_clean[col].fillna(default).fillna("")
    
    # --- Типизация числовых полей ---
    print("🔧 Нормализую типы данных...")
    
    numeric_cols = [
        "label_price", "prev_price", "curr_price",
        "prev_flow", "curr_flow", "flow_abs_change", "flow_pct_change",
        "price_move_pct", "atr", "flow_scale", "delta_volume",
        "momentum", "ratio"
    ]
    
    for col in numeric_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col], errors="coerce")
    
    # --- Сохранение ---
    print(f"\n💾 Сохраняю в: {path_out}")
    
    try:
        df_clean.to_csv(path_out, index=False, encoding="utf-8")
        print(f"✅ Файл сохранён! {len(df_clean)} строк")
        
        # Статистика
        print("\n📊 СТАТИСТИКА:")
        print(f"  • Символы: {df_clean['symbol'].unique().tolist()}")
        print(f"  • TF профили: {df_clean['tf_profile'].unique().tolist() if 'tf_profile' in df_clean.columns else 'N/A'}")
        print(f"  • Strength распределение:")
        if "strength" in df_clean.columns:
            print(df_clean["strength"].value_counts())
    
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")
        return
    
    # --- Валидация результата ---
    print("\n🔍 ВАЛИДАЦИЯ:")
    try:
        df_test = pd.read_csv(path_out, nrows=1)
        print(f"✅ CSV читается корректно")
        print(f"   Колонки: {list(df_test.columns)[:5]}...")
    except Exception as e:
        print(f"❌ CSV не читается: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование:")
        print(f"  python {sys.argv[0]} <input.csv> <output.csv>")
        print("\nПример:")
        print(f"  python {sys.argv[0]} unified_dataset.csv unified_dataset_clean.csv")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    normalize_json_in_csv(input_file, output_file)