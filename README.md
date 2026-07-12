# LOCAL_AI_ENGINE

Telegram-бот для технического анализа криптовалют через LLM (локальную или облачную).

## Архитектура

```
main.py                     — точка входа, graceful shutdown
core/
├── config.py               — TOKEN, LLM endpoint/model, system prompts
├── handlers.py             — Telegram-команды, inline-клавиатуры
├── scheduler.py            — APScheduler, автоанализ по таймеру
├── ollama_service.py       — единый транспорт LLM (cloud + local)
├── ollama_client.py        — JSON-парсинг LLM-ответа, risk-rules
├── auto_chart.py           — OHLCV-графики, Fib, structural levels
├── state_tracker.py        — история уровней, зоны (saved/broken/rebuilt)
├── volume_filters.py       — A/D-объём, divergence, bullish/bearish bias
├── data_provider.py        — OHLCV через ccxt, CSV-архив
├── db.py                   — SQLite (прогнозы, настройки, backtest)
├── utils.py                — markets cache, symbol validation
├── binance_metrics.py      — дополнительные метрики Binance
├── liquidity_magnet/       — liquidity pools, equal highs/lows
│   ├── __init__.py
│   └── liquidity_magnet.py
├── liquidity_heatmap.py    — heatmap (планируется к интеграции)
└── zigzag/                 — изолированный ZigZag-модуль
    ├── structural_zigzag.py
    ├── benchmark_zigzag.py
    └── ...
```

## Установка

```bash
git clone https://github.com/VVASILEVSS/LOCAL_AI_ENGINE.git
cd LOCAL_AI_ENGINE
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## Настройка

1. Скопируй `.env.example` в `.env`
2. Заполни `TOKEN` (Telegram Bot Token от @BotFather)
3. Заполни `MY_CHAT_ID` (твой Telegram ID)
4. Настрой LLM:

### Облачная LLM (Alibaba GLM и др.) — рекомендуется
```env
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode
MODEL_NAME=qwen-plus
```

### Локальная LLM (LM Studio / Ollama)
```env
LLM_API_KEY=
LOCAL_AI_ENDPOINT=http://localhost:1234/v1/chat/completions
MODEL_NAME=qwen_qwen2.5-vl-7b-instruct
```

## Запуск

```bash
# Linux/macOS
chmod +x start_bot.sh
./start_bot.sh

# Windows
start_bot.bat

# Или напрямую
python main.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/scan BTC` | Быстрый анализ по выбранным ТФ |
| `/add ETH/USDT` | Добавить инструмент |
| `/remove XAGUSDT` | Удалить инструмент |
| `/settings` | Текущая конфигурация |
| `/timeframes` | Выбрать таймфреймы |
| `/timer 30` | Интервал автоотчётов (мин) |
| `/filter on/off` | Фильтр сигналов |
| `/export` | Экспорт CSV + бэктест |
| Фото → `/analyze_all` | Анализ скриншотов графиков |

## Пайплайн анализа

```
Binance API (OHLCV)
    ↓
auto_chart.py (графики + метрики)
    ↓
zigzag/ (структурные уровни)
    ↓
volume_filters.py (A/D контекст)
    ↓
ollama_service.py → LLM (cloud/local)
    ↓
ollama_client.py (JSON-парсинг + normalize)
    ↓
enforce_risk_rules (иерархия сигналов, state)
    ↓
state_tracker.py (сохранение истории)
    ↓
Telegram + SQLite
```

## Обмен файлами

Папка `exchange/` — общая зона между оператором и AI-агентами:
- `exchange/inbox/` — задачи от оператора
- `exchange/outbox/` — отчёты от агентов

## Пары и таймфреймы

- Пары: BTCUSDT, ETHUSDT, XAUTUSDT (spot), XAGUSDT (futures)
- ТФ: 15m, 1h, 4h, 1D
- Интервал автоанализа: 60 минут (настраивается)