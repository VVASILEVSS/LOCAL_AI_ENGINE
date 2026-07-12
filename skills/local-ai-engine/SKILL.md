---
name: local-ai-engine
description: >
  Skill для работы с проектом LOCAL_AI_ENGINE — торговый бот на Python с анализом order-flow,
  zigzag-индикаторами, liquidity magnet, LLM-интеграцией (Ollama) и AD-датасетом для ML/LLM.
  Использовать при любом упоминании: LOCAL_AI_ENGINE, AD-датасет, unified_dataset, zigzag,
  order-flow, liquidity magnet, indicator_test_full, diag_candidates, трассировка, traces,
  clean_ad_dataset, validate_ad_dataset, Hermes, Гермес, проект бота, BHMB. Также когда
  пользователь просит работать с файлами в репозитории VVASILEVSS/LOCAL_AI_ENGINE на GitHub.
---

# LOCAL_AI_ENGINE — Skill для Гермес-агента

## Репозиторий

- **GitHub:** `https://github.com/VVASILEVSS/LOCAL_AI_ENGINE`
- **Токен:** `GITHUB_TOKEN` (переменная окружения, подставляется агентом)
- **Клонирование:** `git clone https://$GITHUB_TOKEN@github.com/VVASILEVSS/LOCAL_AI_ENGINE.git`
- **Приватный** репо — доступ только с токеном

## Структура проекта

```
LOCAL_AI_ENGINE/
├── core/                          # Основная логика бота
│   ├── __init__.py
│   ├── auto_chart.py              # Генерация графиков
│   ├── binance_metrics.py         # Метрики Binance
│   ├── config.py                  # Конфигурация (.env)
│   ├── db.py                      # SQLite (forecasts.db)
│   ├── handlers.py                # Обработчики Telegram
│   ├── ollama_client.py           # LLM-клиент (Ollama)
│   ├── scheduler.py               # Планировщик задач
│   ├── utils.py                   # Утилиты
│   ├── liquidity_magnet.py        # Модуль магнита ликвидности
│   ├── liquidity_heatmap.py       # Тепловая карта ликвидности
│   ├── state_tracker.py           # Трекер состояний
│   ├── volume_filters.py          # Фильтры объёма
│   └── zigzag/                    # Zigzag-индикатор
│       ├── __init__.py
│       ├── structural_zigzag.py   # Структурный zigzag
│       ├── benchmark_zigzag.py    # Бенчмарк
│       ├── compare_zigzag.py      # Сравнение алгоритмов
│       └── ...
├── tests/
│   ├── AD/                        # === АНОМАЛИЯ-ДЕТЕКТОР (основной фокус) ===
│   │   ├── README.md              # Гайд по очистке датасета
│   │   ├── data/
│   │   │   ├── raw/               # Сырые CSV по парам/таймфреймам
│   │   │   │   └── unified_dataset.csv  # Единый датасет (44KB)
│   │   │   ├── BTCUSDT_{15m,1h,4h,1d}.csv
│   │   │   ├── ETHUSDT_{15m,1h,4h,1d}.csv
│   │   │   └── XAUTUSDT_{15m,1h,4h,1d}.csv
│   │   ├── traces/                # Результаты трассировки (trace CSV)
│   │   ├── results/               # JSON-результаты (candidates, summary, tune)
│   │   ├── backup/                # Запакованные бэкапы (.zip)
│   │   ├── scripts/               # Все скрипты AD
│   │   │   ├── indicator_test_full.py      # ГЛАВНЫЙ — полный трассировщик
│   │   │   ├── indicator_test_full_trace.py # Обёртка для run_trace.ps1
│   │   │   ├── clean_ad_dataset.py         # Очистка датасета к ML-READY
│   │   │   ├── validate_ad_dataset.py      # Валидация очищенного датасета
│   │   │   ├── inspect_trace.py            # Инспекция trace-файлов
│   │   │   ├── diag_candidates.py          # Диагностика кандидатов
│   │   │   ├── diag_pivots.py              # Диагностика пивотов
│   │   │   ├── diag_simple.py              # Простая диагностика
│   │   │   ├── run_trace.ps1               # Запуск трассировки (PS)
│   │   │   ├── run_tuner.ps1               # Запуск тюнера (PS)
│   │   │   ├── run_tests.ps1               # Запуск тестов (PS)
│   │   │   ├── check_compute_full.py       # Проверка вычислений
│   │   │   ├── test_prefixes.py            # Тест префиксов
│   │   │   └── indicator_test.py           # Базовый тест
│   │   ├── docs/
│   │   │   └── unified_dataset_fields.md   # Описание полей датасета
│   │   ├── logs/                  # Логи
│   │   ├── other/                 # Временные/старые скрипты
│   │   ├── pine/                  # PineScript (TradingView)
│   │   └── шпаргалка/             # Справочные материалы
│   ├── зиг заг/                   # Zigzag-тесты
│   ├── ликвидации/                # Материалы по ликвидациям
│   └── diag_candidates.py         # (устаревшее, теперь в AD/scripts/)
├── tools/                         # Утилиты проекта
│   ├── autotune_diag.py
│   ├── extract_best.py
│   ├── generate_unified_dataset.ps1
│   ├── normalize_dataset.py
│   ├── refactor_unified_dataset.py
│   ├── transform_ad_dataset.py
│   ├── unified_dataset.csv        # Копия датасета (tools level)
│   └── validate_ad_dataset.py     # (старая версия)
├── TZ/                            # Технические задания
├── data/                          # (пока пусто, данные в tests/AD/data/)
├── docs/                          # Документация
├── scripts/                       # Общие скрипты
├── main.py                        # Точка входа бота
├── .env                           # Секреты (НЕ коммитить)
├── .gitignore
└── requirements.txt
```

## Ключевые концепции

### AD (Anomaly Detector) — датасет
Единый датасет `unified_dataset.csv` содержит кандидаты на аномалии order-flow
по парам BTCUSDT, ETHUSDT, XAUTUSDT на таймфреймах 15m, 1h, 4h, 1d.

**Пайплайн работы с датасетом:**
1. Генерация: `indicator_test_full.py` → трассировка OHLCV → candidates JSON
2. Объединение: `generate_unified_dataset.ps1` / `tools/` → `unified_dataset.csv`
3. Очистка: `python scripts/clean_ad_dataset.py --input data/raw/unified_dataset.csv --output data/cleaned/unified_dataset_cleaned.csv`
4. Валидация: `python scripts/validate_ad_dataset.py --input data/cleaned/unified_dataset_cleaned.csv --log docs/cleaning_log.md`

### Поля датасета (31 столбец, эталонный порядок)

symbol, tf, candidate_idx, time_iso, label_time, label_price, prev_price, curr_price, prev_flow, curr_flow, flow_abs_change, flow_pct_change, price_move_pct, atr, flow_scale, pivot_left, pivot_right, context_start_idx, context_end_idx, top_price, bottom_price, mid_price, delta_volume, momentum, ratio, strength, action, comment, llm_feedback, context_ohlcv_json

Подробное описание каждого поля — в `tests/AD/docs/unified_dataset_fields.md`.

### indicator_test_full.py
Главный скрипт AD. Принимает CSV с OHLCV-данными, вычисляет:
- Order-flow (покупки/продажи по объёму)
- Zigzag-пивоты (структурные развороты)
- ATR (волатильность)
- Контекстное окно свечей вокруг кандидата
- Генерирует trace-файл с полным набором признаков

### Zigzag
Структурный индикатор разворота тренда. Реализация в `core/zigzag/structural_zigzag.py`.
Определяет пивоты (high/low точки) и строит структуру рынка.

### Liquidity Magnet
Модуль поиска зон ликвидности. Реализация в `core/liquidity_magnet.py`.
Ищет кластеры стоп-лоссов и лимитных ордеров.

## Типовые задачи агента

### Чтение/анализ файлов из репо
```bash
# Клонировать (если нет локально)
git clone https://$GITHUB_TOKEN@github.com/VVASILEVSS/LOCAL_AI_ENGINE.git /tmp/LOCAL_AI_ENGINE

# Или обновить
cd /tmp/LOCAL_AI_ENGINE && git pull
```

### Запуск очистки датасета
```bash
cd /tmp/LOCAL_AI_ENGINE/tests/AD
python scripts/clean_ad_dataset.py --input data/raw/unified_dataset.csv --output data/cleaned/unified_dataset_cleaned.csv
python scripts/validate_ad_dataset.py --input data/cleaned/unified_dataset_cleaned.csv --log docs/cleaning_log.md
```

### Запуск трассировки
```powershell
cd tests\AD
.\scripts\run_trace.ps1 BTCUSDT 1h
```

### Анализ trace-файла
Читать `traces/{SYMBOL}_{TF}_trace.csv`, проверять:
- Корректность JSON в `context_ohlcv_json`
- Соответствие полей эталону
- Консистентность (prices, indices)

### Работа с GitHub
```bash
# Статус
git status
git log --oneline -10

# Коммит и пуш
git add .
git commit -m "описание"
git push

# Ветки
git branch -a
git checkout -b feature/название
```

## Правила работы

1. **Не удаляй данные** без явного запроса. Архивируй в zip если нужно.
2. **Не коммить** `.env`, `*.db`, `__pycache__`, `source/` (venv), `data/`, `traces/`.
3. **Проверяй JSON** в `context_ohlcv_json` перед любыми манипуляциями.
4. **Эталонный формат datetime:** `YYYY-MM-DD HH:MM:SS` (ISO без T).
5. **Разделитель в числах:** точка (не запятая).
6. При работе с датасетом всегда сверяйся с `docs/unified_dataset_fields.md`.
7. Если пользователь просит «навести порядок» — сначала сделай `git status` и покажи дерево.