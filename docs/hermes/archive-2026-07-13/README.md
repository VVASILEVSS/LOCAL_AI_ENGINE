# Hermes Agent — Архив 2026-07-13

Санитизированный архив конфигурации, памяти, чатов и настроек Hermes Agent (desktop app) для резервного копирования и переноса на другую машину.

## Структура

```
archive-2026-07-13/
├── README.md              ← этот файл
├── config/
│   ├── config.yaml        ← конфиг Hermes (API ключи замаскированы ***REDACTED***)
│   ├── .env.example       ← шаблон .env (все ключи закомментированы)
│   └── SOUL.md            ← identity файл (загружается всегда)
├── memories/
│   ├── MEMORY.md          ← долговременная память агента (заметки, факты, conventions)
│   └── USER.md            ← профиль пользователя
├── sessions/
│   └── request_dump_*.json ← 32 дампа API запросов (полные чаты с LLM)
└── cron/
    └── jobs.json          ← cron jobs конфигурация
```

## ⚠️ Секреты

Все API ключи **замаскированы**:
- `config.yaml` — `api_key` значения заменены на `***REDACTED***`
- `.env` → `.env.example` — все ключи закомментированы
- `auth.json` — **исключён** (содержит OAuth токены, если бы они были)
- `state.db` — **исключён** (85MB, пустой — данные в WAL/desktop памяти)
- `logs/` — **исключены** (могут содержать секреты в stack traces)

Проверено: `grep -oE 'sk-[a-zA-Z0-9_-]{20,}|AQ\.[a-zA-Z0-9_-]{20,}|89b0993d[a-zA-Z0-9.]{20,}'` → 0 совпадений во всех файлах архива.

## Что нужно заполнить вручную на новой машине

### config.yaml

Раскомментировать и заполнить в секции `model:`:
```yaml
model:
  default: glm-5.2-fast-preview
  provider: alibaba
  api_key: ***REDACTED***  ← вставить реальный DASHSCOPE_API_KEY
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
```

### .env

Скопировать `.env.example` → `.env` и заполнить:
```bash
OPENROUTER_API_KEY=...
GLM_API_KEY=...
GLM_BASE_URL=...
GOOGLE_API_KEY=...
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=...
```

## Источник

- **Hermes home:** `C:\Users\Asus-pc\AppData\Local\hermes\`
- **Profile:** `default` (нет дополнительных профилей)
- **Hermes version:** v0.18.2 (2026.7.7.2), upstream `902379ea`
- **Desktop app:** `C:\Users\Asus-pc\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\`
- **Sessions в state.db:** 0 (desktop хранит чаты в памяти + request_dump JSON на диске)
- **request_dump JSON:** 32 файла, 7.1MB — полные API запросы (включая system prompt, user messages, tool calls, LLM responses)

## Sessions (32 дампа)

Формат имени: `request_dump_<session_id>_<timestamp>.json`

Сессии:
- `20260708_224620_551856` — первые 3 дампа (setup)
- `20260708_230109_1b1ea8` — 10 дампов (рабочая сессия, LOCAL_AI_ENGINE)
- `20260710_095245_bb38a9` — короткая сессия
- `20260710_120130_4f4575` — 6 дампов
- `20260710_121702_7ba46e` — 13 дампов (Volume Profile работа)

Каждый дамп содержит:
- `model` — модель LLM
- `messages` — полный массив сообщений (system + user + assistant + tool)
- `temperature`, `max_tokens` — параметры запроса
- `tools` — массив tool schemas

## Как восстановить

1. Установить Hermes Agent на новой машине: `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`
2. Запустить `hermes setup` — мастер настроит модель/провайдер
3. Скопировать `config/config.yaml` → `~/.hermes/config.yaml` (или `AppData\Local\hermes\config.yaml` на Windows), вставив реальные API ключи
4. Скопировать `config/.env.example` → `~/.hermes/.env`, переименовать в `.env`, вставить ключи
5. Скопировать `config/SOUL.md` → `~/.hermes/SOUL.md`
6. Скопировать `memories/` → `~/.hermes/memories/`
7. Скопировать `cron/jobs.json` → `~/.hermes/cron/jobs.json`
8. Запустить `hermes` или `hermes desktop`

Sessions (request_dump JSON) — это архив чатов для истории, они не восстанавливаются в state.db автоматически. Для просмотра можно открыть в любом JSON viewer.

## Дата архивации

2026-07-13, конец сессии.
