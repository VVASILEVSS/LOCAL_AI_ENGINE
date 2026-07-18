import argparse
import pandas as pd
import json
import re

# Строгий эталонный список столбцов и их порядок
TARGET_COLUMNS = [
    "symbol","tf","candidate_idx","time_iso","label_time","label_price",
    "prev_price","curr_price","prev_flow","curr_flow","flow_abs_change",
    "flow_pct_change","price_move_pct","atr","flow_scale",
    "pivot_left","pivot_right","context_start_idx","context_end_idx",
    "top_price","bottom_price","mid_price","delta_volume","momentum",
    "ratio","strength","action","comment","llm_feedback","context_ohlcv_json"
]

# Сопоставление: исходное имя -> эталонное
COLUMN_MAP = {
    "symbol": "symbol",
    "tf_profile": "tf",
    "candidate_idx": "candidate_idx",
    "candidate_i": "candidate_idx",
    "candidate_index": "candidate_idx",
    "candidate_time": "time_iso",
    "time_iso": "time_iso",
    "label_time": "label_time",
    "label_time_utc": "label_time",
    "label_end_time": "label_end_time",   # будет игнорироваться
    "label_end_time_utc": "label_end_time",  # будет игнорироваться
    "label_index": "label_idx",
    "label_price": "label_price",
    "prev_price": "prev_price",
    "prevPrice": "prev_price",
    "curr_price": "curr_price",
    "currPrice": "curr_price",
    "prev_flow": "prev_flow",
    "prevFlow": "prev_flow",
    "curr_flow": "curr_flow",
    "currFlow": "curr_flow",
    "flow_abs_change": "flow_abs_change",
    "flowAbsChange": "flow_abs_change",
    "flow_pct_change": "flow_pct_change",
    "flowPctChange": "flow_pct_change",
    "price_move_pct": "price_move_pct",
    "priceMovePct": "price_move_pct",
    "atr": "atr",
    "flow_scale": "flow_scale",
    "flowScale": "flow_scale",
    "pivot_left": "pivot_left",
    "pivotLeft": "pivot_left",
    "pivot_right": "pivot_right",
    "pivotRight": "pivot_right",
    "context_start_idx": "context_start_idx",
    "contextStartIdx": "context_start_idx",
    "context_end_idx": "context_end_idx",
    "contextEndIdx": "context_end_idx",
    "top_price": "top_price",
    "topPrice": "top_price",
    "bottom_price": "bottom_price",
    "bottomPrice": "bottom_price",
    "mid_price": "mid_price",
    "midPrice": "mid_price",
    "context_ohlcv_json": "context_ohlcv_json",
    "delta_volume": "delta_volume",
    "momentum": "momentum",
    "ratio": "ratio",
    "strength": "strength",
    "action": "action",
    "comment": "comment",
    "llm_feedback": "llm_feedback"
}

def normalize_context_json(s: str):
    """Очистка вложенного OHLCV JSON: убирает \n и делает одинарную строку."""
    if pd.isna(s) or s.strip() == "":
        return "[]"
    # Remove any whitespace newlines, excessive spaces inside arrays/objects
    s = s.replace('\n', '')
    s = re.sub(r'\s{2,}', ' ', s)
    # Attempt to load/dump for pretty re-formatting
    try:
        arr = json.loads(s)
        # re-dump: no spaces, keys sorted
        return json.dumps(arr, separators=(',', ':'), ensure_ascii=False)
    except Exception:  # may be already quoted etc.
        return s.replace('\r', '').replace('\n', '')  # fallback: just flatten

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    df = pd.read_csv(args.input, dtype=str, low_memory=False)

    # 1. Переименовываем все
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})

    # 2. Дроп лишнего/ненужного/неиспользуемого
    for col in list(df.columns):
        if col not in COLUMN_MAP and col not in TARGET_COLUMNS:
            df = df.drop(col, axis=1)

    # 3. Маппим все колонки в целевые
    new_cols = {}
    for c in df.columns:
        if c in COLUMN_MAP:
            new_cols[c] = COLUMN_MAP[c]
        elif c in TARGET_COLUMNS:
            new_cols[c] = c
    df = df.rename(columns=new_cols)

    # 4. Если time_iso нет, но есть candidate_time - используем его (или наоборот)
    if "candidate_time" in df.columns and "time_iso" not in df.columns:
        df["time_iso"] = df["candidate_time"]
    elif "time_iso" in df.columns and "candidate_time" not in df.columns:
        df["candidate_time"] = df["time_iso"]

    # 5. Ковертируем в правильный порядок
    for col in TARGET_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[TARGET_COLUMNS]

    # 6. Чистка типов: всё, что числовое — float, кроме категориальных и текста
    float_cols = [
        "label_price", "prev_price", "curr_price", "prev_flow", "curr_flow",
        "flow_abs_change", "flow_pct_change", "price_move_pct", "atr", "flow_scale",
        "top_price", "bottom_price", "mid_price", "delta_volume", "momentum",
        "ratio"
    ]

    int_cols = [
        "candidate_idx","pivot_left","pivot_right","context_start_idx","context_end_idx"
    ]

    for col in float_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    for col in int_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce', downcast="integer")

    # 7. Чистим context_ohlcv_json
    df["context_ohlcv_json"] = df["context_ohlcv_json"].map(normalize_context_json)

    # 8. Все пустые заменяем на ""
    df = df.fillna("")

    # 9. Сохраняем финальный файл
    df.to_csv(args.output, index=False)

if __name__ == '__main__':
    main()