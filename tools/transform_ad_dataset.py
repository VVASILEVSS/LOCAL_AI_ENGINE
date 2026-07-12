import pandas as pd
import json
import re

SRC = 'unified_dataset.csv'
DST = 'unified_dataset_clean.csv'

# Карта переименований
ALT_COL_MAP = {
    "profile": "tf",
    "tf_profile": "tf",
    "candidate_i": "candidate_idx",
    "candidate_idx": "candidate_idx",
    "time_iso": "time_iso",
    "candidate_time": "time_iso",  # используем только один основной time_iso
    "label_index": "label_idx",
    "label_time": "label_time",
    "label_price": "label_price",
    # ...прочие сопоставления
}

def to_snake(name):
    # строгое склеивание (пример: MinFlowAbs -> min_flow_abs)
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower().replace('-', '_').replace(' ', '_')

def fix_json_context(val):
    # Приводим json к одной строке, без переносов
    if pd.isna(val) or val.strip() in ["", "null"]:
        return "[]"
    s = val.replace('\n', '').replace('\r', '')
    try:
        obj = json.loads(s)
    except Exception:
        try:
            obj = json.loads(json.loads(s))
        except Exception:
            return "[]"
    # Приводим числа к float (кроме времени и idx)
    for bar in obj:
        for k in bar:
            if k not in ['time', 'idx']:
                try:
                    bar[k] = float(bar[k])
                except Exception:
                    pass
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))

def main():
    df = pd.read_csv(SRC, low_memory=False)
    # Колонки -> snake_case
    df.columns = [to_snake(ALT_COL_MAP.get(c, c)) for c in df.columns]

    # Удалить дублирующие и лишние временные поля, если они есть
    time_cols = ['candidate_time', 'time_iso', 'label_time', 'label_end_time']
    main_time = 'time_iso'
    for c in time_cols:
        if c != main_time and c in df.columns:
            df.drop(columns=[c], inplace=True, errors='ignore')

    # Переименовать tf, привести к snake_case
    if 'tf_profile' in df.columns:
        df.rename(columns={'tf_profile': 'tf'}, inplace=True)
    if 'profile' in df.columns:
        df.rename(columns={'profile': 'tf'}, inplace=True)

    # context_ohlcv_json привести к однострочному валидному json
    df['context_ohlcv_json'] = df['context_ohlcv_json'].apply(fix_json_context)

    # action, comment, llm_feedback — если не нужны, оставить, но привести к ''
    for col in ['action', 'comment', 'llm_feedback']:
        if col in df.columns:
            df[col] = df[col].fillna('')

    # Для etalon-формата — оставить только нужные колонки!
    keep_order = ['symbol','tf','candidate_idx','time_iso','label_time','label_price','prev_price','curr_price',
                  'prev_flow','curr_flow','flow_abs_change','flow_pct_change','price_move_pct','atr','flow_scale',
                  'pivot_left','pivot_right','context_start_idx','context_end_idx','top_price','bottom_price','mid_price',
                  'delta_volume','momentum','ratio','strength','action','comment','llm_feedback','context_ohlcv_json']
    # Убираем из order несуществующие столбцы:
    keep_order = [col for col in keep_order if col in df.columns]
    df = df[keep_order]

    # Floats без лишних знаков: float(fmt) либо int(fmt) если целое
    float_cols = [
        'prev_price', 'curr_price', 'prev_flow', 'curr_flow', 'flow_abs_change', 'flow_pct_change',
        'price_move_pct', 'atr', 'flow_scale', 'delta_volume', 'momentum', 'ratio', 'mid_price',
        'top_price', 'bottom_price', 'label_price'  # и т.д
    ]
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(8)
    int_cols = ['candidate_idx','pivot_left','pivot_right','context_start_idx','context_end_idx']
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    # Сохраняем CSV — без индекса, sep=',', encoding='utf-8'
    df.to_csv(DST, index=False, encoding='utf-8')

if __name__ == '__main__':
    main()