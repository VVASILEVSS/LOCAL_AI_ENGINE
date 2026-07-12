import os
from dotenv import load_dotenv

load_dotenv()

# --- КОНФИГУРАЦИЯ БОТА ---
_raw_token = os.getenv("TOKEN")
if not _raw_token:
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: Токен бота не найден в файле .env!")
TOKEN: str = _raw_token
MY_CHAT_ID: int = int(os.getenv("MY_CHAT_ID", "0"))

# --- НАСТРОЙКИ ЛОКАЛЬНОГО ИИ ---
# Полный URL endpoint (включая /v1/chat/completions) для backward compat
# с ollama_client.py который делает POST напрямую.
LOCAL_AI_ENDPOINT = os.getenv(
    "LOCAL_AI_ENDPOINT",
    "http://localhost:1234/v1/chat/completions",
)
MODEL_NAME = os.getenv(
    "MODEL_NAME",
    "qwen_qwen2.5-vl-7b-instruct",
)

# --- НАСТРОЙКИ ОБЛАЧНОЙ LLM ---
# Пустой ключ = local mode (LM Studio / Ollama на localhost, без авторизации).
# Непустой = cloud mode (Alibaba GLM, OpenRouter и др. — любой OpenAI-compatible).
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODE = "cloud" if LLM_API_KEY else "local"

# base_url для ollama_service (БЕЗ /chat/completions на конце —
# service сам добавляет /v1/chat/completions). Для local mode это
# http://localhost:1234, для cloud — https://...maas.aliyuncs.com/compatible-mode
# (БЕЗ /v1 — service добавляет сам, иначе будет /v1/v1/ → 404).
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")

# --- A/B ТЕСТ ПРОМПТОВ (P3-4) ---
# "A" = текущий промпт (strict fact-based + 2 few-shot примера)
# "B" = альтернативный промпт (rules-based, без few-shot, приоритет signal_status)
PROMPT_VARIANT = os.getenv("PROMPT_VARIANT", "A").upper().strip()
if PROMPT_VARIANT not in ("A", "B"):
    PROMPT_VARIANT = "A"

# --- СИСТЕМНЫЙ ПРОМПТ ДЛЯ СЕРИИ + ДИНАМИКА ---
SERIES_PROMPT = """Ты — старший трейдер-аналитик. Тебе прислана серия графиков одного актива на разных таймфреймах.
ЗАДАЧА:
1. Синхронизируй данные со всех графиков. Определи единый тренд и ключевые уровни.
2. Если предоставлен ПРЕДЫДУЩИЙ АНАЛИЗ, обязательно сравни текущую ситуацию с прошлой:
   - Какие уровни пробиты/укрепились?
   - Изменился ли тренд или вероятность Long/Short?
   - Напиши блок "ДИНАМИКА" с кратким diff-отчётом.
3. Дай итоговую оценку Long/Short и рекомендации по риск-менеджменту.

ФОРМАТ ОТВЕТА (СТРОГО НА РУССКОМ):
A) ТЕКУЩАЯ ЦЕНА: <число>
B) ТРЕНД: <направление> + причина
C) КЛЮЧЕВЫЕ УРОВНИ: Поддержка / Сопротивление
D) ДИНАМИКА: (если есть прошлый анализ → что изменилось, иначе → "Первый анализ")
E) ОЦЕНКА ВЕРОЯТНОСТИ: Long: XX% | Short: XX%
F) ПРОГНОЗ: 2-3 предложения + уровни входа/стоп/тейк.
"""

# --- ГЛОБАЛЬНЫЙ КЭШ ПРЕДЫДУЩИХ АНАЛИЗОВ (для отслеживания динамики) ---
# Структура: { user_id: "текст прошлого анализа" }
USER_ANALYSIS_CACHE = {}