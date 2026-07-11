# Гайд: Приведение AD датасета к ML/LLM-READY формату

**Цели:**
- Сделать датасет строго однородным, чистым и пригодным для обучения ML/LLM и хардкорного анализа, без багов сериализации.
- Сделать преобразование максимально повторяемым: будут скрипты, тесты и инструкции.

---

## 1. Шаблон итоговой строки (эталон):

```csv
symbol,tf,candidate_idx,time_iso,label_time,label_price,prev_price,curr_price,prev_flow,curr_flow,flow_abs_change,flow_pct_change,price_move_pct,atr,flow_scale,pivot_left,pivot_right,context_start_idx,context_end_idx,top_price,bottom_price,mid_price,delta_volume,momentum,ratio,strength,action,comment,llm_feedback,context_ohlcv_json
BTCUSDT,15m,155,2026-05-13 16:00:00,2026-05-13 11:00:00,78754.65,80412.15,78754.65,-0.768,-0.553,0.215,0.279,0.021,253.06,1103.72,12,12,145,165,80210,78754.65,79482.33,-0.491,-0.003,6.97,strong,,,"[{""open"":80149.73,""close"":79850,""high"":80210,""volume"":414.03,""low"":79780,""time"":""2026-05-13 13:30:00"",""idx"":145}]"
```

---

## 2. Структура папок/файлов проекта

```
/data/
  raw/unified_dataset.csv
  cleaned/unified_dataset_cleaned.csv
/scripts/
  clean_ad_dataset.py
  validate_ad_dataset.py
/docs/
  unified_dataset_fields.md
  cleaning_log.md
```

---

## 3. Скрипты

### 3.1 Скрипт для чистки/нормализации

**Путь:** `/scripts/clean_ad_dataset.py`

Что делает:
- Приводит все названия к snake_case по эталону.
- Выкидывает дублирующиеся/ненужные или конфликтующие столбцы и пустые текстовые колонки, если они не нужны.
- Приводит time поля к `time_iso`.
- Проверяет все json (context) — делает их одной строкой, убирает пробелы и лишние кавычки.
- Все времена из других полей переносит/переименовывает в нужное место.
- Все float/integer — в float, по эталону (разделитель — точка).
- Сохраняет только нужные столбцы и в нужном порядке.

**Команда запуска:**
```bash
python3 scripts/clean_ad_dataset.py --input data/raw/unified_dataset.csv --output data/cleaned/unified_dataset_cleaned.csv
```

---

### 3.2 Скрипт для валидации

**Путь:** `/scripts/validate_ad_dataset.py`

Что делает:
- Проверяет что все json-поля читаются.
- Проверяет что все datetime поля преобразуются без ошибок.
- Проверяет, что порядок и названия столбцов совпадают с эталонным списком.
- В случае ошибок — пишет в `docs/cleaning_log.md`.

**Команда запуска:**
```bash
python3 scripts/validate_ad_dataset.py --input data/cleaned/unified_dataset_cleaned.csv
```

---

## 4. Гайд: пошаговая инструкция

### 4.1 Куда класть файлы

- Исходный сырой датасет: `data/raw/unified_dataset.csv`
- Обработанный датасет: `data/cleaned/unified_dataset_cleaned.csv`

### 4.2 Запуск обработки

1. Проверьте что вы находитесь в корне проекта.
2. Запустите чистку данных:
    ```bash
    python3 scripts/clean_ad_dataset.py --input data/raw/unified_dataset.csv --output data/cleaned/unified_dataset_cleaned.csv
    ```
3. После успешной чистки запустите валидацию:
    ```bash
    python3 scripts/validate_ad_dataset.py --input data/cleaned/unified_dataset_cleaned.csv
    ```
4. Ошибки (если найдены) ищите в `docs/cleaning_log.md`.

---

## 5. Список обязательных полей

Смотри шаблон выше и файл `docs/unified_dataset_fields.md`.

---

## 6. Пример эталонной строки:

См. выше (шаблон).

---

## 7. Полезные команды

```bash
# Проверить структуру cleaned файла
head -1 data/cleaned/unified_dataset_cleaned.csv

# Посмотреть пару строк (пример)
head -3 data/cleaned/unified_dataset_cleaned.csv

# Быстрая проверка формата JSON поля:
python3 -c "import pandas as pd, json; df=pd.read_csv('data/cleaned/unified_dataset_cleaned.csv'); [json.loads(x) for x in df['context_ohlcv_json']]"
```

--- 