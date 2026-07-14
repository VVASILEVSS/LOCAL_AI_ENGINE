# Запуск бота LOCAL_AI_ENGINE

## Быстрый старт

```bash
git pull
pip install -r requirements.txt
python main.py
```

## Веб-дашборд (статистика + автозапуск)

```bash
pip install flask
python web_dashboard.py
# → http://localhost:5000
```

## Запуск бота + веб-дашборд одновременно

```bash
# В одном терминале — бот (Telegram):
python main.py

# В другом терминале — дашборд (только чтение статистики):
python web_dashboard.py
```

---

## .env (обязательные переменные)

```env
# Telegram
TOKEN=your_bot_token
MY_CHAT_ID=your_chat_id

# Локальная LLM (LM Studio / Ollama)
LOCAL_AI_ENDPOINT=http://localhost:1234/v1/chat/completions
MODEL_NAME=qwen_qwen2.5-vl-7b-instruct

# Или облачная LLM (раскомментировать вместо локальной)
# LLM_API_KEY=sk-...
# LLM_BASE_URL=https://...maas.aliyuncs.com/compatible-mode
# MODEL_NAME=qwen2.5-vl-7b-instruct

# Опционально
# PROMPT_VARIANT=A
# WEB_PORT=5000
```

---

## Остановка

`Ctrl+C` в терминале где запущен бот.

---

## Полезные команды

```bash
# Проверить LLM доступность
python -c "from core.ollama_service import health_check; import asyncio; print(asyncio.run(health_check()))"

# Проверить .env
python -c "from core.config import LLM_MODE, MODEL_NAME; print(f'{LLM_MODE} | {MODEL_NAME}')"

# Статистика backtest из БД
python -c "from core.backtest import get_backtest_stats_dict; import json; print(json.dumps(get_backtest_stats_dict(), indent=2, default=str))"

# Очистить логи (если нужно)
rm -f logs/*.log
```

---

## Расписание

| Событие | Время |
|---------|-------|
| Интервальный анализ | Каждые 60 мин (настраивается через `/timer` в боте) |
| Ежедневный анализ | 09:00 Asia/Qyzylorda (UTC+5) |
| Проверка прогнозов | Каждые 60 мин (4ч после прогноза) |

## Символы

- **BTCUSDT** — Bitcoin (futures + spot)
- **XAUTUSDT** — PAX Gold (spot only, выходные работает)

## Таймфреймы

1D → 4H → 1H → 15M (старшие приоритетнее)
