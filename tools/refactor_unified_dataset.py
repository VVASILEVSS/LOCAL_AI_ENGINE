import pandas as pd
import json

# -- НОВОЕ название колонок в том порядке, что задано --
new_columns = [
    "symbol","tf","candidate_idx","time_iso","label_time","label_price",
    "prev_price","curr_price","prev_flow","curr_flow","flow_abs_change","flow_pct_change",
    "price_move_pct","atr","flow_scale","pivot_left","pivot_right",
    "context_start_idx","context_end_idx","top_price","bottom_price","mid_price",
    "delta_volume","momentum","ratio","strength",
    "action","comment","llm_feedback","context_ohlcv_json"
]

# -- Соответствие старых названий новым --
renames = {
    'tf_profile': 'tf',
    'candidate_time': 'time_iso',
    'label_index': 'candidate_idx', # если индексы совпадают, иначе сохранить оба
    'label_time': 'label_time',
    'label_end_time': None,  # удаляем
    'label_price': 'label_price',
    'prevPrice': 'prev_price',
    'currPrice': 'curr_price',
    'prevFlow': 'prev_flow',
    'currFlow': 'curr_flow',
    'flowAbsChange': 'flow_abs_change',
    'flowPctChange': 'flow_pct_change',
    'priceMovePct': 'price_move_pct',
    'atr': 'atr',
    'flowScale': 'flow_scale',
    'pivotLeft': 'pivot_left',
    'pivotRight': 'pivot_right',
    'context_start_index': 'context_start_idx',
    'context_end_index': 'context_end_idx',
    'top_price': 'top_price',
    'bottom_price': 'bottom_price',
    'mid_price': 'mid_price',
    'delta_volume': 'delta_volume',
    'momentum': 'momentum',
    'ratio': 'ratio',
    'strength': 'strength',
    'action': 'action',
    'comment': 'comment',
    'llm_feedback': 'llm_feedback',
    'context_ohlcv_json': 'context_ohlcv_json',
    # ... добавить все найденные вариации!
}

df = pd.read_csv('unified_dataset.csv', dtype=str, keep_default_na=False)

# -- Переименование колонок --
df.rename(columns=renames, inplace=True)
# -- Оставляем только нужные (и в нужном порядке) --
df = df.reindex(columns=new_columns)

# -- Приведение типов --
for col in ['candidate_idx', 'pivot_left', 'pivot_right', 'context_start_idx', 'context_end_idx']:
    if col in df:
        df[col] = df[col].astype(float).astype('Int64')

float_cols = ['label_price','prev_price','curr_price','prev_flow','curr_flow','flow_abs_change','flow_pct_change',
              'price_move_pct','atr','flow_scale','top_price','bottom_price','mid_price','delta_volume','momentum','ratio']
for col in float_cols:
    if col in df:
        df[col] = pd.to_numeric(df[col], errors='coerce')

# -- Временные метки в iso --
for col in ['time_iso', 'label_time']:
    if col in df:
        df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime("%Y-%m-%d %H:%M:%S")

# -- Сериализация json: убрать переносы и привести к one-line --
def clean_json(val):
    if not val or pd.isna(val):
        return ''
    try:
        # Иногда в строке уже правильный json
        raw = val.replace('\n','').replace('\r','').replace("'",'"').replace('""','"')
        # Попробуем загрузить и тут же сериализовать обратно
        js = json.loads(raw)
        return json.dumps(js, ensure_ascii=False)
    except Exception as e:
        return f'__ERROR__ {str(e)}'

if 'context_ohlcv_json' in df:
    df['context_ohlcv_json'] = df['context_ohlcv_json'].apply(clean_json)

# -- Заполнить пустые текстовые поля пустыми строками --
for tcol in ["action","comment","llm_feedback"]:
    if tcol in df:
        df[tcol] = df[tcol].replace({pd.NA: '', None: ''})

# -- Валидация: лог ошибок --
errs = []
for i, row in df.iterrows():
    val = row['context_ohlcv_json']
    if val.startswith('__ERROR__'):
        errs.append((i, val))
if errs:
    print("Find errors in context_ohlcv_json:", errs)

df.to_csv('unified_dataset_clean.csv', index=False)