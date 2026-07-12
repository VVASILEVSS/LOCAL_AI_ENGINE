# Hermes Agent — Quick Start (новая машина)

## 1. Клонирование

```bash
git clone https://github.com/VVASILEVSS/LOCAL_AI_ENGINE.git
cd LOCAL_AI_ENGINE
git log --oneline -10   # проверить, что 5a818e0 на вершине
```

## 2. Окружение

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows git-bash
pip install -r requirements.txt
pip install tzdata
```

## 3. .env (создать вручную в корне)

```env
LLM_API_KEY=***          # из Hermes config.yaml, секция providers.qwen
LLM_BASE_URL=https://ws-yreibc51vw8gp9za.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
MODEL_NAME=glm-5.2-fast-preview
```

## 4. Запуск

```bash
source .venv/Scripts/activate && PYTHONPATH="" python your_script.py
```

**ВСЕГДА** `PYTHONPATH=""` — иначе Hermes venv контаминирует.

## 5. Полный контекст

**ЧИТАЙ:** `docs/hermes/CONTEXT.md` — все нюансы, баги, обходы, commit stack.

## 6. Текущий статус (2026-07-12)

- **P3-2 ZigZag**: ✅ DONE (`5a818e0`)
- **P3-1 Backtest**: Super Z (`28afeca`) — проверить `git log`
- **P3-4 A/B промпты**: заблокирован P3-1
