# LOCAL_AI_ENGINE

Telegram-бот для технического анализа криптовалют на **локальной LLM** (LM Studio).

## Стек
- **Python 3.13** + aiogram 3.x + APScheduler
- **ccxt 4.5.54** (Binance spot + futures)
- **LM Studio** локально, модель `qwen2.5-vl-7b-instruct`
- **SQLite** (`forecasts.db`) + JSON state-файлы

## Пары и таймфреймы
- BTCUSDT, ETHUSDT, XAUTUSDT (spot), XAGUSDT (futures)
- 15m, 1h, 4h, 1D

## Запуск
```bash
python -m venv venv
source venv/Scripts/activate  # Windows git-bash
pip install -r requirements.txt
# Создать .env: TOKEN=<bot_token> MY_CHAT_ID=<chat_id>
python main.py
# Требуется LM Studio на localhost:1234
```

## Архитектура
```
main.py → core/config.py (промпты) → core/scheduler.py (автоанализ)
       → core/ollama_client.py (LLM + JSON-парсер + risk-rules)
       → core/auto_chart.py (графики OHLCV + Fib)
       → core/zigzag/ (multi-TF структура)
       → core/volume_filters.py (A/D-контекст)
       → core/liquidity_magnet/ (зоны ликвидности)
       → core/state_tracker.py (история уровней)
       → core/db.py (SQLite прогнозы + backtest)
```

## Техническое задание
См. `TZ/README.md` — roadmap Wyckoff/ZigZag/A-D/Liquidity (P0→P2).

---

## 🔍 AI-аудит проекта

Полный аудит проекта (архитектура, функционал, реализация, безопасность, предложения) выполнен AI-агентом в ветке:

### 👉 [`ai-review/agent-audit`](https://github.com/VVASILEVSS/LOCAL_AI_ENGINE/tree/ai-review/agent-audit)

**Артефакты ветки:**
| Файл | Назначение |
|---|---|
| [`docs/AI_AUDIT_REVIEW.md`](docs/AI_AUDIT_REVIEW.md) | Полный аудит: оценка 6/10, 18 предложений (P0→P2) |
| [`docs/AI_AGENT_SKILL.md`](docs/AI_AGENT_SKILL.md) | Скилл: known-issues, команды, pitfalls |
| [`docs/AI_AGENT_PROMPT.md`](docs/AI_AGENT_PROMPT.md) | Промт для инициализации AI-агента |
| [`docs/AI_AUDIT_PROGRESS.md`](docs/AI_AUDIT_PROGRESS.md) | История и прогресс работы в ветке |

**Ключевые находки (P0):**
1. 🔴 Дубль `liquidity_magnet.py` (файл == пакет, идентичны)
2. 🔴 `requirements.txt` в UTF-16 (должен быть UTF-8)
3. 🔴 `config.py` хардкод endpoint/model (нет env)
4. 🔴 `main.py` перетирает settings при старте
5. 🔴 Нет иерархии сигналов (ТЗ P0.1 — модель противоречива)

**Аудитор:** Hermes Agent (GLM 5.2 Fast Preview, провайдер Alibaba DashScope)
**Дата:** 2026-07-12
