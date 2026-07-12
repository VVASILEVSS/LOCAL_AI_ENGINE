# P2: Логирование в файл, Docker, Type Hints

**От:** Hermes Agent (glm-5.2-fast-preview)
**Дата:** 2026-07-12 16:00 KST
**Верификация:** 47/47 PASS

---

## P2-1: Логирование в файл

### Было
```python
# main.py
logging.basicConfig(level=logging.INFO)  # → только консоль, без файла
print("Бот запущен!")                     # → нет в логах
```

### Стало
- **`core/logging_setup.py`** (115 строк) — централизованный модуль:
  - `RotatingFileHandler`: 10 MB × 5 файлов → `logs/bot.log`
  - `StreamHandler` (консоль) с тем же форматом
  - Формат: `2026-07-12 15:54:41 | INFO | core.module:func:42 | message`
  - `LOG_LEVEL` env var (default: INFO)
  - Подавление шума: httpx, matplotlib, apscheduler, PIL, asyncio → WARNING
  - `get_logger(__name__)` — auto-init если не настроено
- **`main.py`**: `print()` → `logger.info()`, `basicConfig` → `setup_logging()`
- **`.env.example`**: +`LOG_LEVEL=INFO`
- **`.gitignore`**: уже исключал `logs/`, `*.log`

### Файлы
| Файл | Изменение |
|------|-----------|
| `core/logging_setup.py` | **NEW** (115 строк) |
| `main.py` | `basicConfig` → `setup_logging()`, `print` → `logger` |
| `.env.example` | +`LOG_LEVEL` |

---

## P2-2: Docker

### Было
- Не было Dockerfile, docker-compose, .dockerignore
- `start_bot.sh` — только локальный запуск (Linux/macOS)

### Стало

**`Dockerfile`** (45 строк):
- Base: `python:3.13-slim` (~150 MB)
- System deps: `libfreetype6`, `libpng16-16` (для matplotlib)
- `WORKDIR /app`
- `COPY requirements.txt` → `pip install` (layer caching)
- `COPY core/ main.py`
- `mkdir -p logs data/state`
- `HEALTHCHECK`: `python -c "import aiogram, ccxt, httpx"`
- `CMD ["python", "main.py"]`

**`docker-compose.yml`** (35 строк):
- `restart: unless-stopped`
- `env_file: .env`
- Volumes: `./data:/app/data`, `./logs:/app/logs` (persistence)
- Memory limit: 512M (reservation 256M)
- Docker logging: json-file, 10MB × 3

**`.dockerignore`** (55 строк):
- Исключает: `.env`, `.venv/`, `__pycache__/`, `.git/`, `*.db`, `logs/`, `data/`, `_junk/`, `exchange/`, `*.md`, `Dockerfile`, `docker-compose.yml`

### Использование
```bash
cp .env.example .env  # заполнить TOKEN, MY_CHAT_ID, LLM_API_KEY
docker compose up -d --build
docker compose logs -f bot
```

### Файлы
| Файл | Статус |
|------|--------|
| `Dockerfile` | **NEW** |
| `docker-compose.yml` | **NEW** |
| `.dockerignore` | **NEW** |

---

## P2-3: Type Hints

### Было
| Модуль | Функций | С type hints | % |
|--------|---------|-------------|---|
| `core/db.py` | 9 | 3 | 33% |
| `core/handlers.py` | 20 | 7 | 35% |
| `core/scheduler.py` | 10 | 7 | 70% |
| `core/data_provider.py` | 16 | 15 | 94% |
| `core/ollama_service.py` | 7 | 6 | 86% |

### Стало — 100% покрытие в ключевых модулях
| Модуль | Функций | С type hints | % |
|--------|---------|-------------|---|
| `core/db.py` | 9 | 9 | **100%** |
| `core/handlers.py` | 20 | 20 | **100%** |
| `core/scheduler.py` | 10 | 10 | **100%** |
| `core/data_provider.py` | 16 | 16 | **100%** |
| `core/ollama_service.py` | 7 | 6 | 86% |
| `core/auto_chart.py` | 18 | 18 | **100%** |
| `core/state_tracker.py` | 20 | 20 | **100%** |
| `main.py` | 1 | 1 | **100%** |
| `core/logging_setup.py` | 2 | 2 | **100%** |

### Изменения
- **`core/db.py`**: +`from typing import Any, Optional`, все 9 функций → `-> None`/`-> int`/`-> str`/`-> Any`/`-> dict[str, Any]`
- **`core/handlers.py`**: 13 функций → `-> None` (aiogram callbacks)
- **`core/scheduler.py`**: 4 функции → `-> None`
- **`core/data_provider.py`**: `fetch_from_binance` → `-> pd.DataFrame`
- **`mypy.ini`** (NEW, 35 строк):
  - `python_version = 3.13`
  - `check_untyped_defs = True` — проверяет даже функции без hints
  - `warn_return_any`, `warn_redundant_casts`, `warn_unused_ignores`
  - `ignore_missing_imports` для ccxt, aiogram, apscheduler, matplotlib, PIL, pandas, numpy
  - Exclude: `backups/`, `liquidity_magnet/`, `tests/`

### Файлы
| Файл | Изменение |
|------|-----------|
| `core/db.py` | +type hints (9 функций) |
| `core/handlers.py` | +type hints (13 функций) |
| `core/scheduler.py` | +type hints (4 функции) |
| `core/data_provider.py` | +`-> pd.DataFrame` |
| `mypy.ini` | **NEW** |
| `.gitignore` | +`data/` |

---

## Верификация

```
P2 VERIFICATION: 47 PASS / 0 FAIL

[1] LOGGING:       10/10 ✓
[2] DOCKER:        22/22 ✓
[3] TYPE HINTS:    15/15 ✓
```

---

## Что НЕ сделано (P3 — следующий приоритет)

- **Self-consistency** (3 прогона LLM, голосование) — P2 из плана сигналов
- **Temperature: 0.2** в `ollama_service.py` — P2 из плана сигналов
- **Few-shot примеры** в промпте — P2 из плана сигналов
- **Backtest pipeline** — сохранять прогнозы, проверять точность
- **Мульти-символ** — ETH, DXY корреляция
