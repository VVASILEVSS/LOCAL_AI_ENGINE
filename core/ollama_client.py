import io
import base64
import json
import re
import time
import asyncio
import httpx
from PIL import Image
import logging
from collections import Counter
from typing import List, Optional, Dict, Any, Tuple

from core.config import LOCAL_AI_ENDPOINT, MODEL_NAME, LLM_MODE, LLM_API_KEY, PROMPT_VARIANT
from core.ollama_service import generate as llm_generate, LLMError

logger = logging.getLogger(__name__)
LLM_QUEUE_LOCK = asyncio.Lock()

PRO_TA_SYSTEM_PROMPT = """Ты — алгоритмический трейдер. Работай СТРОГО ПО ФАКТУ.

ЖЁСТКИЕ ПРАВИЛА:
- Запрещены сценарии, предположения, "возможно", "надеюсь".
- Каждое поле JSON отделяй запятой.
- После каждого строкового значения обязателен разделитель.
- Не пропускай запятые между полями.
- Не выдумывай данные, уровни, новости, объёмы или индикаторы.
- Анализируй только закрытые свечи.
- Если данных недостаточно, прямо укажи это.
- Приоритет у старших таймфреймов.
- Фибо используй только как контекст коррекции, не для SL/TP и не для сигналов.
- Не смешивай вход и выход: вход = триггер, выход = SL/TP.
- Работай только по подтверждённому пробою и объёму.
- Импульс не считать трендом без закрепления и подтверждения объёмом.
- Определи, не находится ли рынок в зоне завершения импульса (риск конца 5-й волны и ABC).
- Если цена между ключевыми уровнями и без подтверждения — no_signal или accumulation.
- false_breakout ставь только если есть явный выход за границу зоны, возврат внутрь и объёмное подтверждение. Если есть только реакция у уровня без явного выхода — используй retest, reversal или no_signal.

ПРИМЕРЫ ОТВЕТОВ (few-shot):

ПРИМЕР 1 — no_signal (цена между уровнями, без подтверждения):
{
  "price": 63895.5,
  "current_price": 63895.5,
  "last_closed_price": 63869.0,
  "prev_trend": "down",
  "current_substructure": "balance",
  "htf_structure": "correction",
  "htf_structure_comment": "1D: SMA cross bear, цена ниже SMA200",
  "trend_structure": "down",
  "trend_structure_comment": "4H: нисходящая структура, SMA cross bear",
  "ltf_structure": "balance",
  "ltf_structure_comment": "15m: цена внутри зоны, без пробоя",
  "accumulation_state": "none",
  "accumulation_state_comment": "Явного накопления нет",
  "wave_phase": "correction_up",
  "wave_phase_comment": "Откат вверх внутри нисходящего тренда, риск ABC вниз",
  "abc_risk": "abc_risk_down",
  "abc_risk_comment": "Коррекция вверх может быть волной B → ожидается C вниз",
  "global_structure": "correction",
  "global_structure_comment": "Коррекция внутри нисходящего тренда",
  "key_zones": { "resistance": 64245.0, "support": 63640.0 },
  "key_zones_comment": "Resistance 4H, Support 4H",
  "tf_zones_comment": "Цена внутри всех зон, пробоя нет",
  "tf_span_map": { "15m": 130.0, "1h": 350.0, "4h": 605.0, "1D": 2751.0 },
  "confluence_levels": [
    { "level": 64245.0, "timeframes": ["4H", "1D"], "priority": "high", "count": 2, "spread": 435.0, "kind": "resistance" },
    { "level": 63640.0, "timeframes": ["4H"], "priority": "medium", "count": 1, "spread": 0, "kind": "support" }
  ],
  "signal_status": "no_signal",
  "signal_status_comment": "Цена между уровнями, объём низкий, пробоя нет",
  "entry_conditions": {
    "aggressive": null,
    "conservative": null,
    "current_status": "Ожидание пробоя resistance 64245 или breakdown support 63640"
  },
  "entry_conditions_comment": "Вход только после пробоя с объёмом",
  "risk_management": {
    "primary": { "sl": null, "tp1": null, "tp2": null, "tp3": null, "rr": null },
    "alternative": { "sl": null, "tp1": null, "tp2": null, "tp3": null, "rr": null }
  },
  "risk_management_comment": "Нет сигнала — нет SL/TP",
  "scenario_status": "no_alternative",
  "scenario_status_comment": "Ждём пробоя уровня для активации сценария",
  "fact_feedback": "RSI нейтральный, funding нейтральный, объём ниже среднего",
  "confidence": "low",
  "confidence_reason": "Нет пробоя, объём не подтверждает, цена в зоне баланса",
  "missing_data": ["Пробой resistance с объёмом", "Закрытие свечи выше 64245"]
}

ПРИМЕР 2 — aggressive_breakout (пробой resistance с объёмом):
{
  "price": 64310.0,
  "current_price": 64310.0,
  "last_closed_price": 64245.0,
  "prev_trend": "up",
  "current_substructure": "breakout_up",
  "htf_structure": "trend",
  "htf_structure_comment": "1D: цена выше SMA50, структура восходящая",
  "trend_structure": "up",
  "trend_structure_comment": "4H: SMA cross bull, HH+HL",
  "ltf_structure": "breakout_up",
  "ltf_structure_comment": "15m: пробой resistance 64245 с объёмом 2.1x",
  "accumulation_state": "none",
  "accumulation_state_comment": "Накопления нет, импульс",
  "wave_phase": "impulse_up",
  "wave_phase_comment": "Импульс вверх, 3-я волна",
  "abc_risk": "none",
  "abc_risk_comment": "ABC риск отсутствует, импульс",
  "global_structure": "trend",
  "global_structure_comment": "Восходящий тренд подтверждён",
  "key_zones": { "resistance": 65000.0, "support": 64245.0 },
  "key_zones_comment": "Бывший resistance стал support",
  "tf_zones_comment": "4H resistance 64245 пробит, стал support",
  "tf_span_map": { "15m": 200.0, "1h": 400.0, "4h": 755.0, "1D": 2360.0 },
  "confluence_levels": [
    { "level": 64245.0, "timeframes": ["4H", "1h"], "priority": "high", "count": 2, "spread": 255.0, "kind": "support" }
  ],
  "signal_status": "aggressive_breakout",
  "signal_status_comment": "Пробой 4H resistance с объёмом 2.1x, закрытие выше",
  "entry_conditions": {
    "aggressive": "Лонг на 64300, SL ниже 64200, объём подтверждает",
    "conservative": "Ретест 64245 → лонг при отскоке",
    "current_status": "Пробой подтверждён, ждем ретест"
  },
  "entry_conditions_comment": "Aggressive: вход на пробое. Conservative: ждем ретест",
  "risk_management": {
    "primary": { "sl": 64150.0, "tp1": 64600.0, "tp2": 65000.0, "tp3": 66000.0, "rr": 3.5 },
    "alternative": { "sl": 64200.0, "tp1": 64500.0, "tp2": 64800.0, "tp3": null, "rr": 2.0 }
  },
  "risk_management_comment": "Primary: RR 3.5, SL ниже зоны пробоя. Alternative: tighter SL на ретесте",
  "scenario_status": "primary_valid",
  "scenario_status_comment": "Пробой подтверждён объёмом, primary сценарий активен",
  "fact_feedback": "Пробой 4H resistance, объём 2.1x, funding +0.01%, OI растёт",
  "confidence": "high",
  "confidence_reason": "Пробой с объёмом, структура подтверждена на 4H+1D, OI растёт",
  "missing_data": []
}

ВЕРНИ ТОЛЬКО ОДИН JSON-ОБЪЕКТ ПО ЭТОЙ СХЕМЕ. БЕЗ markdown, БЕЗ пояснений, БЕЗ комментариев вне JSON.
"""

# --- P3-4: A/B тест промптов ---
# Variant B: rules-based, без few-shot, акцент на приоритет signal_status.
# Гипотеза: убирая few-shot примеры (которые стоят ~5000 токенов), LLM получает
# меньше "якорей" и может лучше адаптироваться к контексту. Взволнованные правила
# приоритета помогают избежать противоречий (false_breakout + accumulation одновременно).
PRO_TA_SYSTEM_PROMPT_B = """Ты — алгоритмический трейдер. Работай СТРОГО ПО ФАКТУ.

ПРИОРИТЕТ SIGNAL_STATUS (выбери ровно один):
1. aggressive_breakout — пробой границы зоны с объёмом >1.5x, закрытие за границей.
2. retest — цена вернулась к пробитой зоне, отскок с объёмом.
3. false_breakout — выход за границу + возврат внутрь + объёмное подтверждение возврата.
4. reversal — смена направления после тренда, подтверждение объёмом и структурой.
5. accumulation — цена в диапазоне, объём <1.0x, нет пробоя.
6. no_signal — цена между уровнями, объём низкий, пробоя нет.

ЖЁСТКИЕ ПРАВИЛА:
- Если signal_status = no_signal / accumulation / false_breakout → primary risk block = null.
- Если signal_status = aggressive_breakout / retest → заполни entry_conditions + primary risk block.
- false_breakout только при явном выходе за границу + возврате внутрь + объёме. Иначе → retest или no_signal.
- TP1/TP2/TP3 бери из структурных уровней, ZigZag, confluence. Не выдумывай.
- SL за структурным уровнем, не внутри зоны.
- Фибо только для глубины коррекции, не для SL/TP.
- Приоритет у старших ТФ: 1D > 4H > 1H > 15m.
- Младший пробой не означает старший пробой.
- ABC риск: если wave_phase = correction_up → abc_risk_down; correction_down → abc_risk_up.
- Не выдумывай данные. Анализируй только закрытые свечи.
- Все числа — number or null.

ВЕРНИ ТОЛЬКО ОДИН JSON-ОБЪЕКТ ПО СХЕМЕ ИЗ USER PROMPT. БЕЗ markdown, БЕЗ пояснений.
"""


def _get_system_prompt() -> str:
    """Выбор системного промпта по PROMPT_VARIANT (P3-4 A/B тест)."""
    if PROMPT_VARIANT == "B":
        return PRO_TA_SYSTEM_PROMPT_B
    return PRO_TA_SYSTEM_PROMPT

PRO_TA_USER_PROMPT = """Тип рынка: {market_type}

Текущие данные:
{metrics}

Контекст таймфреймов:
{tf_context}

ZigZag контекст:
{zigzag_context}

Объёмный контекст:
{volume_context}

Liquidity heatmap:
{liquidity_context}

State / history context:
{state_context}

Историческая точность:
{backtest}

{multi_symbol}

Верни ТОЛЬКО валидный JSON без markdown, пояснений или комментариев. Строгая схема:
{{
  "price": <number|null>,
  "current_price": <number|null>,
  "last_closed_price": <number|null>,

  "prev_trend": "up|down|balance|unknown",
  "current_substructure": "accumulation|distribution|breakout_up|breakout_down|false_breakout_up|false_breakout_down|reversal_attempt_up|reversal_attempt_down|balance|correction_up|correction_down|unknown",

  "htf_structure": "trend|balance|correction|unknown",
  "htf_structure_comment": "краткий комментарий по старшим ТФ",

  "trend_structure": "up|down|balance|unknown",
  "trend_structure_comment": "краткий комментарий о направлении структуры",

  "ltf_structure": "up|down|balance|correction_up|correction_down|unknown",
  "ltf_structure_comment": "краткий комментарий о младшем ТФ",

  "accumulation_state": "accumulation|distribution|none|unknown",
  "accumulation_state_comment": "краткий комментарий",

  "wave_phase": "impulse_up|impulse_down|correction_up|correction_down|impulse_end_risk_up|impulse_end_risk_down|unclear",
  "wave_phase_comment": "краткий комментарий с направлением и риском ABC, если актуально",

  "abc_risk": "abc_risk_up|abc_risk_down|none|unknown",
  "abc_risk_comment": "краткий комментарий о риске ABC",

  "global_structure": "trend|balance|correction|unknown",
  "global_structure_comment": "краткий комментарий о фазе рынка",

  "key_zones": {{ "resistance": <number|null>, "support": <number|null> }},
  "key_zones_comment": "краткий комментарий",

  "tf_span_map": {{ "<ТФ>": <number|null> }},
  "confluence_levels": [
    {{
      "level": <number|null>,
      "timeframes": ["1H", "4H"],
      "priority": "low|medium|high",
      "count": <number|null>,
      "spread": <number|null>,
      "kind": "resistance|support|mixed"
    }}
  ],

  "signal_status": "aggressive_breakout|retest|false_breakout|accumulation|no_signal|reversal",
  "signal_status_comment": "краткий комментарий",

  "entry_conditions": {{
    "aggressive": "<уровень + объём or null>",
    "conservative": "<уровень ретеста + условие or null>",
    "current_status": "<статус пробоя or null>"
  }},
  "entry_conditions_comment": "краткий комментарий",

  "risk_management": {{
    "primary": {{
      "sl": <number|null>,
      "tp1": <number|null>,
      "tp2": <number|null>,
      "tp3": <number|null>,
      "rr": <number|null>
    }},
    "alternative": {{
      "sl": <number|null>,
      "tp1": <number|null>,
      "tp2": <number|null>,
      "tp3": <number|null>,
      "rr": <number|null>
    }}
  }},
  "risk_management_comment": "краткий комментарий",

  "scenario_status": "primary_valid|primary_invalidated|alternative_active|no_alternative",
  "scenario_status_comment": "краткий комментарий",

  "fact_feedback": "<кратко подтверждённые факты>",
  "confidence": "high|medium|low",
  "confidence_reason": "<структура, объём, сессия, пробой, волновая фаза>",
  "missing_data": ["<что отсутствует или не подтверждено>"]
}}

Правила:
1. htf_structure — только по 1D/4H.
2. trend_structure — только направление структуры, не фаза рынка.
3. ltf_structure — только по младшему ТФ.
4. wave_phase и abc_risk всегда должны содержать направление, если оно понятно.
5. Если wave_phase_comment указывает ABC вверх/вниз, abc_risk обязан совпадать.
6. global_structure:
   - trend = направленное движение
   - balance = боковик / сжатие
   - correction = коррекция
7. Если цена внутри диапазона без подтверждённого пробоя, не ставь false_breakout — используй accumulation или no_signal.
8. [Variant E Phase 1] tf_zones — НЕ возвращай в JSON. Зоны теперь authoritative из ZigZag structure (structure.py), передаются напрямую. В JSON возвращай ТОЛЬКО tf_zones_comment — краткий комментарий о зонах (пробой/внутри/протест), ориентируясь на zigzag_context из контекста промпта.
9. Если signal_status = false_breakout / accumulation / no_signal, primary risk block должен быть null.
10. Если есть подтверждённый пробой и объём, заполняй entry_conditions и primary risk block.
11. Если основной сценарий сломан, alternative block должен быть заполнен, если он логически следует из структуры.
12. TP1/TP2/TP3 бери из ближайших структурных уровней, ZigZag и зон ликвидности.
13. Если младший ТФ пробит, а старший нет — цели старшего ТФ могут быть TP, но не confirmation пробоя.
14. Фибо — только контекст глубины коррекции, не для SL/TP и не для сигнала.
15. Если данных не хватает — null.
16. Все комментарии на русском.
17. Только JSON, без лишнего текста.
18. Все числа — только number or null.
19. Если диапазон устойчивый и без выхода за границы, это accumulation или no_signal.
20. R:R must be > 1.0 for aggressive signals. If R:R < 1.0 — set signal_status = no_signal and explain in signal_status_comment. Aggressive entry with R:R < 1.0 is FORBIDDEN.
21. false_breakout только при выходе за границу с возвратом внутрь и подтверждением.
22. Если есть реакция у уровня без явного выхода и возврата, не ставь false_breakout — используй retest, reversal или no_signal.
23. При споре между false_breakout и retest выбирай retest.
24. Диапазоны ТФ иерархические: 15m → 1H → 4H → 1D.
25. Младший пробой не означает старший пробой.
26. [Variant E] Зоны tf_zones авторитетны из ZigZag structure — НЕ выдумывай и не пересчитывай их. tf_zones_comment ориентируйся на zigzag_context.
28. FVG (Fair Value Gaps) — это УСИЛЕНИЕ сигнала, не самостоятельный сигнал. Правила учёта:
    - Если price IN_ZONE несоответствием H4/D1 FVG (current_price_in_zone=true) — это подтверждение ликвидности, усиливает aggressive_breakout или retest.
    - Незаполненный H4/D1 FVG рядом с entry (в пределах 1 ATR) — корректирует TP (берёт границу FVG как magnet) или SL (если FVG против позиции).
    - H1 FVG (info) — НЕ влияет на signal_status, только context для signal_status_comment.
    - M15 FVG не показан в контексте — игнорируй его полностью.
    - Если signal_status=no_signal и нет других причин, FVG сам по себе НЕ переводит в aggressive_breakout. FVG усиливает уже подтверждённый структурой сигнал.
"""


def prepare_image_for_llm(img_bytes: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        logger.error(f"Ошибка подготовки фото: {e}")
        return ""


def _fmt(v: Any) -> str:
    if v is None or v == "None" or v == "":
        return "Н/Д"
    return str(v)


def parse_llm_json(raw: str) -> dict:
    clean = raw.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)

    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {"error": True, "message": "JSON not found", "raw": raw}

    candidate = clean[start:end + 1].strip()
    candidate = candidate.replace("“", '"').replace("”", '"').replace("’", "'")

    # Базовая очистка
    candidate = re.sub(r'("\s*)(\r?\n\s*")', r'\1,\2', candidate)
    candidate = re.sub(r'([}\]])(\r?\n\s*")', r'\1,\2', candidate)
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

    # Убираем двойные запятые и лишние разделители
    candidate = re.sub(r',\s*,+', ',', candidate)
    candidate = re.sub(r'([}\]])\s*,\s*([}\]])', r'\1\2', candidate)
    candidate = re.sub(r'(".*?")\s*,\s*,\s*\n', r'\1,\n', candidate)
    candidate = re.sub(r'(".*?")\s*,\s*,', r'\1,', candidate)

    try:
        data = json.loads(candidate)

        # Нормализация числовых полей верхнего уровня
        for key in ("price", "current_price", "last_closed_price"):
            if key in data:
                data[key] = _safe_float(data.get(key))

        # Нормализация tf_zones (Phase 1 + Phase 2 совместимость)
        # Phase 2: {range: [low, high], bos_price, bos_dir, bos_age}
        # Phase 1 (legacy): {upper, lower}
        # NaN guard: _safe_float("nan") returns float('nan') (truthy, not None) —
        # нормализуем к None, иначе NaN протекает в JSON-вывод и ломает валидаторы.
        def _clean_float(val):
            f = _safe_float(val)
            if f is not None and f != f:  # NaN check (NaN != NaN)
                return None
            return f

        if isinstance(data.get("tf_zones"), dict):
            normalized = {}
            for k, v in data["tf_zones"].items():
                tf_key = str(k).strip().upper().replace("MIN", "M")
                if isinstance(v, dict):
                    entry = {}
                    rng = v.get("range")
                    if isinstance(rng, list) and len(rng) == 2:
                        # Phase 2: извлекаем upper/lower из range
                        entry["lower"] = _clean_float(rng[0])
                        entry["upper"] = _clean_float(rng[1])
                        entry["range"] = [entry["lower"], entry["upper"]]
                        entry["bos_price"] = _clean_float(v.get("bos_price"))
                        raw_dir = v.get("bos_dir")
                        entry["bos_dir"] = raw_dir if raw_dir in ("up", "down") else None
                        raw_age = v.get("bos_age")
                        try:
                            entry["bos_age"] = int(raw_age) if raw_age is not None else None
                        except (TypeError, ValueError):
                            entry["bos_age"] = None
                    else:
                        # Phase 1 (legacy)
                        entry["upper"] = _clean_float(v.get("upper"))
                        entry["lower"] = _clean_float(v.get("lower"))
                    normalized[tf_key] = entry
                else:
                    normalized[tf_key] = v
            data["tf_zones"] = normalized

        # Нормализация key_zones
        if isinstance(data.get("key_zones"), dict):
            kz = data["key_zones"]
            for k in ("resistance", "support"):
                v = kz.get(k)
                if isinstance(v, list):
                    num = next((x for x in v if isinstance(x, (int, float))), None)
                    kz[k] = float(num) if num is not None else None
                else:
                    kz[k] = _safe_float(v)

        # Нормализация tf_span_map
        if isinstance(data.get("tf_span_map"), dict):
            span_map = {}
            for k, v in data["tf_span_map"].items():
                span_map[str(k).strip().upper()] = _safe_float(v)
            data["tf_span_map"] = span_map

        # Нормализация confluence_levels
        if isinstance(data.get("confluence_levels"), list):
            clean_levels = []
            for item in data["confluence_levels"]:
                if not isinstance(item, dict):
                    continue
                clean_levels.append({
                    "level": _safe_float(item.get("level")),
                    "timeframes": [str(x).upper() for x in item.get("timeframes", []) if str(x).strip()],
                    "priority": str(item.get("priority", "low")).lower(),
                    "count": int(item.get("count") or 0),
                    "spread": _safe_float(item.get("spread")),
                    "kind": str(item.get("kind", "mixed")).lower(),
                })
            data["confluence_levels"] = clean_levels

        # Нормализация risk_management
        if isinstance(data.get("risk_management"), dict):
            rm = data["risk_management"]

            if "primary" not in rm and any(k in rm for k in ("sl", "tp1", "tp2", "tp3", "rr")):
                rm = {
                    "primary": {
                        "sl": rm.get("sl"),
                        "tp1": rm.get("tp1"),
                        "tp2": rm.get("tp2"),
                        "tp3": rm.get("tp3"),
                        "rr": rm.get("rr"),
                    },
                    "alternative": {
                        "sl": None,
                        "tp1": None,
                        "tp2": None,
                        "tp3": None,
                        "rr": None,
                    },
                }

            for branch in ("primary", "alternative"):
                if branch not in rm or not isinstance(rm[branch], dict):
                    rm[branch] = {
                        "sl": None,
                        "tp1": None,
                        "tp2": None,
                        "tp3": None,
                        "rr": None,
                    }
                else:
                    for k in ("sl", "tp1", "tp2", "tp3", "rr"):
                        rm[branch][k] = _safe_float(rm[branch].get(k))

            data["risk_management"] = rm

        return data

    except json.JSONDecodeError as e:
        logger.warning(f"LLM parse failed after cleanup: {e}")
        return {
            "error": True,
            "message": f"Parse failed: {str(e)}",
            "raw": raw,
            "candidate": candidate[:4000],
        }
    
    

def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_tp_levels(direction: str, entry_price: float | None, candidates: list[float]) -> tuple[float | None, float | None, float | None]:
        if entry_price is None or not candidates:
            return None, None, None

        direction = (direction or "").lower().strip()
        if direction not in ("long", "short"):
            return None, None, None

        # Убираем дубликаты и саму цену входа
        uniq = sorted(set(round(x, 6) for x in candidates if x is not None and abs(x - entry_price) > 1e-6))
        if not uniq:
            return None, None, None

        if direction == "long":
            ordered = [x for x in uniq if x > entry_price]
            if not ordered:
                return None, None, None

            tp1 = ordered[0]
            tp2 = ordered[1] if len(ordered) > 1 else None
            tp3 = ordered[2] if len(ordered) > 2 else None

            # fallback без дублей
            if tp2 is None:
                tp2 = tp1
            if tp3 is None:
                tp3 = tp2 if tp2 != tp1 else None

            return tp1, tp2, tp3

        ordered = [x for x in reversed(uniq) if x < entry_price]
        if not ordered:
            return None, None, None

        tp1 = ordered[0]
        tp2 = ordered[1] if len(ordered) > 1 else None
        tp3 = ordered[2] if len(ordered) > 2 else None

        if tp2 is None:
            tp2 = tp1
        if tp3 is None:
            tp3 = tp2 if tp2 != tp1 else None

        return tp1, tp2, tp3


def _normalize_confluence_levels(levels: Any) -> list[dict]:
    if not isinstance(levels, list):
        return []

    out = []
    for item in levels:
        if not isinstance(item, dict):
            continue

        timeframes = item.get("timeframes", [])
        if not isinstance(timeframes, list):
            timeframes = []

        out.append({
            "level": _safe_float(item.get("level")),
            "timeframes": [str(tf).upper() for tf in timeframes if str(tf).strip()],
            "priority": str(item.get("priority", "low")).lower(),
            "count": int(item.get("count") or 0),
            "spread": _safe_float(item.get("spread")),
            "kind": str(item.get("kind", "mixed")).lower(),
        })
    return out

def _extract_zigzag_levels_from_context(data: dict) -> list[float]:
    levels: list[float] = []

    zigzag = data.get("zigzag_context") or {}
    if not isinstance(zigzag, dict):
        return levels

    confluence = zigzag.get("confluence_levels") or []
    if isinstance(confluence, list):
        for item in confluence:
            if isinstance(item, dict):
                lvl = _safe_float(item.get("level"))
                if lvl is not None:
                    levels.append(lvl)

    timeframes = zigzag.get("timeframes") or {}
    if isinstance(timeframes, dict):
        for tf_data in timeframes.values():
            if not isinstance(tf_data, dict):
                continue


            for key in ("upper", "lower", "current_price"):
                v = _safe_float(tf_data.get(key))
                if v is not None:
                    levels.append(v)

            zones = tf_data.get("levels") or {}
            if isinstance(zones, dict):
                for arr_key in ("resistance", "support"):
                    arr = zones.get(arr_key) or []
                    if isinstance(arr, list):
                        for v in arr:
                            fv = _safe_float(v)
                            if fv is not None:
                                levels.append(fv)

    return sorted(set(round(x, 6) for x in levels))


def _extract_swing_levels(data: dict) -> tuple[list[float], list[float]]:
    """
    Извлекает РЕАЛЬНЫЕ swing highs / lows из zigzag curr_structure.
    Возвращает (swing_highs, swing_lows) — отсортированные списки.
    Источник: zigzag_context.timeframes.<TF>.structure.curr_structure.{high,low}
    Это разворотные пивоты ZigZag (с протарговкой), а не границы зон.
    """
    swing_highs: list[float] = []
    swing_lows: list[float] = []

    zigzag = data.get("zigzag_context") or {}
    if not isinstance(zigzag, dict):
        return swing_highs, swing_lows

    timeframes = zigzag.get("timeframes") or {}
    if not isinstance(timeframes, dict):
        return swing_highs, swing_lows

    for tf_data in timeframes.values():
        if not isinstance(tf_data, dict):
            continue
        structure = tf_data.get("structure") or {}
        if not isinstance(structure, dict):
            continue
        curr = structure.get("curr_structure") or {}
        if not isinstance(curr, dict):
            continue
        h = _safe_float(curr.get("high"))
        l = _safe_float(curr.get("low"))
        if h is not None:
            swing_highs.append(h)
        if l is not None:
            swing_lows.append(l)

    swing_highs = sorted(set(round(x, 6) for x in swing_highs))
    swing_lows = sorted(set(round(x, 6) for x in swing_lows))
    return swing_highs, swing_lows


def enforce_risk_rules(data: dict) -> dict:
    if not isinstance(data, dict):
        return data

    def _safe_float(value):
        try:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", "."))
                if m:
                    return float(m.group(0))
            return None
        except (TypeError, ValueError):
            return None

    def _empty_risk() -> dict:
        return {
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "rr": None,
        }

    def _empty_bundle() -> dict:
        return {
            "primary": _empty_risk(),
            "alternative": _empty_risk(),
        }

    def _normalize_risk_block(block) -> dict:
        if not isinstance(block, dict):
            block = {}
        return {
            "sl": _safe_float(block.get("sl")),
            "tp1": _safe_float(block.get("tp1")),
            "tp2": _safe_float(block.get("tp2")),
            "tp3": _safe_float(block.get("tp3")),
            "rr": _safe_float(block.get("rr")),
        }

    def _normalize_tf_zones(tf_zones) -> dict:
        if not isinstance(tf_zones, dict):
            return {}

        order = {"1D": 0, "4H": 1, "1H": 2, "15M": 3, "5M": 4}
        normalized = {}

        for tf, z in tf_zones.items():
            if not isinstance(z, dict):
                continue

            tf_key = str(tf).strip().upper().replace("MIN", "M")

            # Пропускать мусорные ключи: гибридные ("1D/4H"), с пробелами, не-ТФ
            if "/" in tf_key or " " in tf_key:
                continue

            # Phase 2: {range: [low, high], bos_price, bos_dir, bos_age}
            # Phase 1 (legacy): {upper, lower}
            # Извлекаем upper/lower из range если есть, иначе из upper/lower.
            rng = z.get("range")
            upper = None
            lower = None
            bos_price = None
            bos_dir = None
            bos_age = None
            is_phase2 = False

            if isinstance(rng, list) and len(rng) == 2:
                # range = [low, high]
                lower = _safe_float(rng[0])
                upper = _safe_float(rng[1])
                bos_price = _safe_float(z.get("bos_price"))
                raw_dir = z.get("bos_dir")
                if raw_dir in ("up", "down"):
                    bos_dir = raw_dir
                raw_age = z.get("bos_age")
                try:
                    bos_age = int(raw_age) if raw_age is not None else None
                except (TypeError, ValueError):
                    bos_age = None
                is_phase2 = True
            else:
                upper = _safe_float(z.get("upper"))
                lower = _safe_float(z.get("lower"))

            if upper is None and lower is None:
                continue
            if upper is not None and lower is not None and lower > upper:
                lower, upper = upper, lower

            entry = {
                "upper": upper,
                "lower": lower,
                "source": str(z.get("source", "llm")).lower() if z.get("source") else "llm",
            }
            # Phase 2: сохраняем bos поля для downstream (backtest, state_tracker, narrative)
            if is_phase2:
                entry["range"] = [lower, upper] if (lower is not None and upper is not None) else None
                entry["bos_price"] = bos_price
                entry["bos_dir"] = bos_dir
                entry["bos_age"] = bos_age

            normalized[tf_key] = entry

        return dict(sorted(normalized.items(), key=lambda item: order.get(item[0], 99)))

    def _normalize_confluence_levels(levels) -> list:
        if not isinstance(levels, list):
            return []

        out = []
        for item in levels:
            if not isinstance(item, dict):
                continue
            timeframes = item.get("timeframes", [])
            if not isinstance(timeframes, list):
                timeframes = []
            lvl = _safe_float(item.get("level"))
            out.append({
                "level": lvl,
                "timeframes": [str(tf).upper() for tf in timeframes if str(tf).strip()],
                "priority": str(item.get("priority", "low")).lower(),
                "count": int(item.get("count") or 0),
                "spread": _safe_float(item.get("spread")),
                "kind": str(item.get("kind", "mixed")).lower(),
            })

        # FEELS-inspired: пересчитываем spread через log-distance
        # |ln(level/price)| — симметричен: +50% и -50% дают ~0.405
        # Это решает проблему D1 support на -30% получая штраф в $
        # а ближний resistance на +2% в $ почти не штрафуется.
        price_for_ld = _safe_float(data.get("price") or data.get("current_price"))
        if price_for_ld and price_for_ld > 0:
            import math
            for item in out:
                lvl = item.get("level")
                if lvl and lvl > 0:
                    item["log_distance"] = round(abs(math.log(lvl / price_for_ld)), 6)
                    # proximity_score: инвертированный-U, пик ~2% (log_dist≈0.02)
                    ld = item["log_distance"]
                    if ld < 0.002:
                        item["proximity_score"] = 0.3
                    elif ld < 0.05:
                        item["proximity_score"] = round(max(0.5, 1.0 - abs(ld - 0.02) * 10.0), 4)
                    else:
                        item["proximity_score"] = round(max(0.05, 1.0 - (ld - 0.05) * 3.0), 4)
                else:
                    item["log_distance"] = None
                    item["proximity_score"] = None

        return out

    def _extract_zigzag_levels_from_context(src: dict) -> list[float]:
        levels: list[float] = []

        zigzag = src.get("zigzag_context") or {}
        if not isinstance(zigzag, dict):
            return levels

        confluence = zigzag.get("confluence_levels") or []
        if isinstance(confluence, list):
            for item in confluence:
                if isinstance(item, dict):
                    lvl = _safe_float(item.get("level"))
                    if lvl is not None:
                        levels.append(lvl)

        timeframes = zigzag.get("timeframes") or {}
        if isinstance(timeframes, dict):
            for tf_data in timeframes.values():
                if not isinstance(tf_data, dict):
                    continue

                for key in ("upper", "lower", "current_price"):
                    v = _safe_float(tf_data.get(key))
                    if v is not None:
                        levels.append(v)

                zones = tf_data.get("levels") or {}
                if isinstance(zones, dict):
                    for k in ("resistance", "support"):
                        arr = zones.get(k) or []
                        if isinstance(arr, list):
                            for v in arr:
                                fv = _safe_float(v)
                                if fv is not None:
                                    levels.append(fv)

        return sorted(set(round(x, 6) for x in levels))

    def _sorted_tf_order(tf_zones: dict) -> list[tuple[str, dict]]:
        order_map = {"5M": 0, "15M": 1, "1H": 2, "4H": 3, "1D": 4}
        items = []
        if isinstance(tf_zones, dict):
            for tf, z in tf_zones.items():
                if isinstance(z, dict):
                    items.append((str(tf).upper(), z))
        items.sort(key=lambda x: order_map.get(x[0], 99))
        return items

    def _zone_span(z):
        if not isinstance(z, dict):
            return None
        upper = _safe_float(z.get("upper"))
        lower = _safe_float(z.get("lower"))
        if upper is None or lower is None:
            return None
        return abs(upper - lower)

    def _direction_from_data(src: dict) -> str:
        signal = str(src.get("signal_status", "")).lower()
        trend = str(src.get("trend_structure", "")).lower()
        ltf = str(src.get("ltf_structure", "")).lower()
        wave = str(src.get("wave_phase", "")).lower()
        global_structure = str(src.get("global_structure", "")).lower()
        sub = str(src.get("current_substructure", "")).lower()
        prev_trend = str(src.get("prev_trend", "")).lower()

        if signal == "aggressive_breakout":
            # Sync with backtest._detect_direction: check trend, not just signal
            # Но current_substructure приоритетнее trend (breakout_up против тренда = реальный сигнал)
            if "breakout_down" in sub:
                return "short"
            if "breakout_up" in sub:
                return "long"
            if "down" in trend or "down" in ltf or "down" in wave or "bear" in trend:
                return "short"
            return "long"
        if signal == "reversal":
            return "short"
        if signal == "retest":
            if "down" in trend or "down" in ltf or "down" in wave or prev_trend == "down" or "down" in sub:
                return "short"
            return "long"
        if signal == "false_breakout":
            if "up" in wave or "up" in ltf or "up" in trend:
                return "short"
            if "down" in wave or "down" in ltf or "down" in trend:
                return "long"
            return "short"

        if "bull" in trend or trend == "up" or ltf == "up" or wave == "impulse_up":
            return "long"
        if "bear" in trend or trend == "down" or ltf == "down" or wave == "impulse_down":
            return "short"
        if global_structure == "trend":
            return "long"
        if sub in ("breakout_up", "false_breakout_up", "accumulation", "correction_up"):
            return "long"
        if sub in ("breakout_down", "false_breakout_down", "distribution", "correction_down"):
            return "short"

        return "long"

    def _pick_tp_levels(direction: str, entry_price: float | None, candidates: list[float]) -> tuple[float | None, float | None, float | None]:
        """TF-каскад + фибо-совпадения.

        Логика (top-down по TF-лесенке):
          TP1 = граница M15 зоны (пробили зону входа)
          TP2 = граница H1 зоны (следующий TF)
          TP3 = граница H4/D1 зоны (старший TF)
        По пути между TP берем ликвидность (swing-pools) и фибо-совпадения (1.0/1.2/1.618/2.618).
        Кандидат в пределах ±0.5% от фибо-уровня → приоритет.
        Суб-структурные swing (ZigZag pivots) тоже учитываются как промежуточные цели.
        """
        if entry_price is None or not candidates:
            return None, None, None

        direction = (direction or "").lower().strip()
        if direction not in ("long", "short"):
            return None, None, None

        is_long = direction == "long"

        # Минимальная дистанция TP от entry — чтобы RR был > 1.0
        # (фильтр убирает "мизерные" цели вроде resistance pool в +1.5 от entry)
        min_tp_distance = abs(entry_price) * 0.003 if entry_price else 0.0

        # --- 1. TF-каскад: zigzag structure swing highs/lows (РЕАЛЬНЫЕ swing levels с протарговкой)
        # БЕРЁМ structure.low/high из zigzag_context, НЕ tf_zones.lower/upper
        # (tf_zones — границы зон, часто локальные min/max без протарговки, "fake lows")
        # zigzag structure.low/high — реальные swing с подтверждением (плотность, отскок, закрытие выше)
        tf_order = ["15m", "1h", "4h", "1D"]
        tf_boundary_targets: list[float] = []
        zigzag_ctx_src = data.get("zigzag_context") or {}
        if isinstance(zigzag_ctx_src, dict):
            zz_timeframes = zigzag_ctx_src.get("timeframes") or {}
            if isinstance(zz_timeframes, dict):
                for tf in tf_order:
                    tf_data = zz_timeframes.get(tf)
                    if not isinstance(tf_data, dict):
                        continue
                    structure = tf_data.get("structure") or {}
                    if not isinstance(structure, dict):
                        continue
                    curr = structure.get("curr_structure") or {}
                    if not isinstance(curr, dict):
                        continue
                    # для long — swing high (цель вверх), для short — swing low (цель вниз)
                    boundary_key = "high" if is_long else "low"
                    bv = _safe_float(curr.get(boundary_key))
                    if bv is not None and abs(bv - entry_price) > 1e-6:
                        # long: swing high выше entry; short: swing low ниже
                        if (is_long and bv > entry_price) or (not is_long and bv < entry_price):
                            tf_boundary_targets.append(bv)

        # убираем дубликаты, сортируем по близости к entry
        tf_boundary_targets = sorted(set(round(x, 6) for x in tf_boundary_targets),
                                     key=lambda v: v - entry_price if is_long else entry_price - v)

        # --- 2. Фибо extension от последнего структурного движения ---
        # Базовое движение = swing curr_structure (high-low) старшего значимого TF (H4→H1→D1)
        fibo_levels: list[float] = []
        zigzag_ctx = data.get("zigzag_context") or {}
        if isinstance(zigzag_ctx, dict):
            timeframes = zigzag_ctx.get("timeframes") or {}
            if isinstance(timeframes, dict):
                # ищем структуру в порядке D1→H4→H1 (старший TF = наиболее значимая для целей)
                for tf in ("1D", "4h", "1h"):
                    tf_data = timeframes.get(tf)
                    if not isinstance(tf_data, dict):
                        continue
                    structure = tf_data.get("structure") or {}
                    if not isinstance(structure, dict):
                        continue
                    curr = structure.get("curr_structure") or {}
                    if not isinstance(curr, dict):
                        continue
                    swing_high = _safe_float(curr.get("high"))
                    swing_low = _safe_float(curr.get("low"))
                    if swing_high is None or swing_low is None or swing_high == swing_low:
                        continue
                    # фибо extension: для long — от swing_low к swing_high, проекция вверх
                    # extension_level = swing_high + (swing_high - swing_low) * ratio
                    # для short — от swing_high к swing_low, проекция вниз
                    swing_range = abs(swing_high - swing_low)
                    for ratio in (1.0, 1.2, 1.618, 2.618):
                        if is_long:
                            fibo_price = swing_high + swing_range * ratio
                        else:
                            fibo_price = swing_low - swing_range * ratio
                        if (is_long and fibo_price > entry_price) or (not is_long and fibo_price < entry_price):
                            fibo_levels.append(round(fibo_price, 6))
                    break  # только первый найденный TF с структурой

        # --- 3. Суб-структурная ликвидность (intermediate targets) ---
        # ZigZag pivots между TF-границами + liquidity_pools (если работают корректно)
        liquidity_targets: list[float] = []
        # ZigZag resistance/support из context (перекрыто в candidates, но явно достаём для логики)
        if isinstance(zigzag_ctx, dict):
            timeframes = zigzag_ctx.get("timeframes") or {}
            if isinstance(timeframes, dict):
                for tf in ("1h", "15m", "4h"):
                    tf_data = timeframes.get(tf)
                    if not isinstance(tf_data, dict):
                        continue
                    zones = tf_data.get("zones") or {}
                    if not isinstance(zones, dict):
                        continue
                    key = "resistance" if is_long else "support"
                    arr = zones.get(key) or []
                    if isinstance(arr, list):
                        for v in arr:
                            fv = _safe_float(v)
                            if fv is not None and abs(fv - entry_price) > 1e-6:
                                if (is_long and fv > entry_price) or (not is_long and fv < entry_price):
                                    liquidity_targets.append(fv)

        # liquidity_pools (если есть)
        liq = data.get("liquidity_pools") or {}
        if isinstance(liq, dict):
            pool_key = "resistance_pools" if is_long else "support_pools"
            pools = liq.get(pool_key) or []
            if isinstance(pools, list):
                for p in pools:
                    if isinstance(p, dict):
                        lv = _safe_float(p.get("level") or p.get("price"))
                        if lv is not None and abs(lv - entry_price) > 1e-6:
                            if (is_long and lv > entry_price) or (not is_long and lv < entry_price):
                                liquidity_targets.append(lv)

        # --- 4. Сборка финальных TP с приоритетом фибо-совпадений ---
        # Объединяем все: TF-границы (главные) + ликвидность (промежуточные)
        all_targets = list(tf_boundary_targets) + list(liquidity_targets)
        # Фильтр: отбрасываем цели ближе min_tp_distance от entry (RR > 1.0)
        all_targets = [x for x in all_targets if x is not None and abs(x - entry_price) >= min_tp_distance]
        all_targets = sorted(set(round(x, 6) for x in all_targets),
                             key=lambda v: v - entry_price if is_long else entry_price - v)

        if not all_targets and not fibo_levels:
            # fallback: старая логика (ближайшие кандидаты)
            uniq = sorted(set(round(x, 6) for x in candidates if x is not None and abs(x - entry_price) > 1e-6))
            if not uniq:
                return None, None, None
            if is_long:
                ordered = [x for x in uniq if x > entry_price]
            else:
                ordered = [x for x in reversed(uniq) if x < entry_price]
            if not ordered:
                return None, None, None
            tp1 = ordered[0]
            tp2 = ordered[1] if len(ordered) > 1 else None
            tp3 = ordered[2] if len(ordered) > 2 else None
            if tp2 is None:
                tp2 = tp1
            if tp3 is None:
                tp3 = tp2 if tp2 != tp1 else None
            return tp1, tp2, tp3

        # --- 5. Приоритет фибо-совпадений ---
        # Кандидат в пределах ±0.5% от фибо-уровня → приоритет
        fibo_tolerance = 0.005  # 0.5%
        fibo_matches: list[float] = []
        for target in all_targets:
            for fl in fibo_levels:
                if fl > 0 and abs(target - fl) / fl <= fibo_tolerance:
                    fibo_matches.append(target)
                    break
        fibo_matches = sorted(set(round(x, 6) for x in fibo_matches),
                              key=lambda v: v - entry_price if is_long else entry_price - v)

        # --- 6. Финальная сборка TP1/TP2/TP3 ---
        # TF-лесенка: TP1 из M15, TP2 из H1, TP3 из H4/D1
        # Если фибо-совпадение есть на уровне TF-границы — приоритет ему
        # TF-границы в порядке M15→H1→H4→D1 (по одной на каждый TF)
        tf_pick: list[float] = []
        for v in tf_boundary_targets:
            if v not in tf_pick and abs(v - entry_price) >= min_tp_distance:
                tf_pick.append(v)

        # Фибо-совпадения как промежуточные цели (между TF-границами)
        fibo_pick: list[float] = []
        for v in fibo_matches:
            if v not in tf_pick and v not in fibo_pick and abs(v - entry_price) >= min_tp_distance:
                fibo_pick.append(v)

        # Лесенка: TP1 = ближайшая TF-граница (M15), затем фибо/ликвидность между, TP2 = H1, TP3 = H4/D1
        # Берём: 1-я TF-граница → ближайшие промежуточные (фибо) → 2-я TF-граница → 3-я TF-граница
        if len(tf_pick) >= 3:
            tp1 = tf_pick[0]
            tp2 = tf_pick[1]
            tp3 = tf_pick[2]
            # Если есть фибо-совпадение между TP1 и TP2 — вставляем как промежуточное (заменяет tp2)
            for fm in fibo_pick:
                if is_long and tp1 < fm < tp2:
                    tp2 = fm
                    break
                if not is_long and tp1 > fm > tp2:
                    tp2 = fm
                    break
        elif len(tf_pick) == 2:
            tp1 = tf_pick[0]
            tp2 = tf_pick[1]
            # Добиваем из фибо-совпадений или ликвидности
            tp3 = None
            for fm in fibo_pick:
                if fm not in (tp1, tp2) and ((is_long and fm > tp2) or (not is_long and fm < tp2)):
                    tp3 = fm
                    break
            if tp3 is None:
                # из all_targets
                for v in all_targets:
                    if v not in (tp1, tp2) and ((is_long and v > tp2) or (not is_long and v < tp2)):
                        tp3 = v
                        break
        elif len(tf_pick) == 1:
            tp1 = tf_pick[0]
            tp2 = None
            tp3 = None
            for v in all_targets:
                if v != tp1 and ((is_long and v > tp1) or (not is_long and v < tp1)):
                    if tp2 is None:
                        tp2 = v
                    elif tp3 is None and v != tp2:
                        tp3 = v
                        break
        else:
            # Нет TF-границ — fallback на all_targets
            tp1 = all_targets[0] if all_targets else None
            tp2 = all_targets[1] if all_targets and len(all_targets) > 1 else None
            tp3 = all_targets[2] if all_targets and len(all_targets) > 2 else None

        if tp1 is None and fibo_levels:
            tp1 = fibo_levels[0]
        if tp2 is None and tp1 is not None:
            tp2 = tp1
        if tp3 is None and tp2 is not None:
            tp3 = tp2 if tp2 != tp1 else None

        return tp1, tp2, tp3

    def _calc_rr(entry_price: float | None, sl: float | None, tp1: float | None) -> float | None:
        if entry_price is None or sl is None or tp1 is None:
            return None
        risk = abs(entry_price - sl)
        reward = abs(tp1 - entry_price)
        if risk <= 0 or reward <= 0:
            return None
        return round(reward / risk, 2)

    def _normalize_wave_abc(src: dict) -> None:
        wave_comment = str(src.get("wave_phase_comment", "")).lower()
        wave_phase = str(src.get("wave_phase", "")).lower()
        abc_risk = str(src.get("abc_risk", "")).lower()

        if "abc вниз" in wave_comment or "abc-коррекции вниз" in wave_comment:
            src["abc_risk"] = "abc_risk_down"
            if not src.get("abc_risk_comment"):
                src["abc_risk_comment"] = "Риск ABC вниз по волновой фазе"
        elif "abc вверх" in wave_comment or "abc-коррекции вверх" in wave_comment:
            src["abc_risk"] = "abc_risk_up"
            if not src.get("abc_risk_comment"):
                src["abc_risk_comment"] = "Риск ABC вверх по волновой фазе"
        elif wave_phase == "correction_down" and abc_risk == "none":
            src["abc_risk"] = "abc_risk_up"
            src["abc_risk_comment"] = "Риск ABC вверх после коррекции вниз"
        elif wave_phase == "correction_up" and abc_risk == "none":
            src["abc_risk"] = "abc_risk_down"
            src["abc_risk_comment"] = "Риск ABC вниз после коррекции вверх"

    # -----------------------------
    # 1) Цена
    # -----------------------------
    data["price"] = _safe_float(data.get("price"))
    data["current_price"] = _safe_float(data.get("current_price"))
    data["last_closed_price"] = _safe_float(data.get("last_closed_price"))

    if data["price"] is None:
        data["price"] = data["current_price"] or data["last_closed_price"]

        # -----------------------------
    # 2) Базовые блоки
    # -----------------------------
    data["tf_zones"] = _normalize_tf_zones(data.get("tf_zones") or {})

    # -----------------------------
    # 1b) Контаминация зон: если LLM скопировал D1 lower в младшие ТФ —
    # заменить на zigzag_context zone для этого ТФ.
    # Симптом: lower(child) == lower(parent) в пределах tolerance.
    # Z сказал: ZigZag benchmark корректный (per-TF independent).
    # -----------------------------
    def _detect_contamination(tf_zones: dict, data: dict) -> dict:
        if not isinstance(tf_zones, dict) or len(tf_zones) < 2:
            return tf_zones

        zz_ctx = data.get("zigzag_context") or {}
        if not isinstance(zz_ctx, dict):
            return tf_zones
        zz_tfs = zz_ctx.get("timeframes") or {}
        if not isinstance(zz_tfs, dict):
            return tf_zones

        # Иерархия: parent → child
        nesting = [("1D", "4H"), ("4H", "1H"), ("1H", "15M"), ("15M", "5M")]
        # 0.5% tolerance — зона считается "прилипшей" к parent lower
        tol_pct = 0.005
        price = data.get("price") or data.get("current_price") or 0.0
        price_safe = price if price > 0 else 1.0

        # Снапшот оригинальных lower/upper ДО фиксов — иначе при последовательном
        # исправлении H4 меняется, и пара (4H→1H) не детектирует H1.
        original_lowers = {}
        original_uppers = {}
        for tf_key, z in tf_zones.items():
            if isinstance(z, dict):
                original_lowers[tf_key] = z.get("lower")
                original_uppers[tf_key] = z.get("upper")

        fixed_count = 0
        for parent_tf, child_tf in nesting:
            parent = tf_zones.get(parent_tf)
            child = tf_zones.get(child_tf)
            if not isinstance(parent, dict) or not isinstance(child, dict):
                continue
            p_lower = original_lowers.get(parent_tf)
            c_lower = original_lowers.get(child_tf)
            p_upper = original_uppers.get(parent_tf)
            c_upper = original_uppers.get(child_tf)

            # Детектор контаминации: child lower ИЛИ upper ≈ parent (по оригиналам)
            lower_contaminated = (
                p_lower is not None and c_lower is not None
                and abs(p_lower - c_lower) / price_safe < tol_pct
            )
            upper_contaminated = (
                p_upper is not None and c_upper is not None
                and abs(p_upper - c_upper) / price_safe < tol_pct
            )

            if not (lower_contaminated or upper_contaminated):
                continue

            # Контаминация! Ищем правильную зону в zigzag_context
            zz_tf_data = None
            for zz_key in (child_tf, child_tf.lower(), child_tf.replace("M", "m").replace("H", "h").replace("D", "d")):
                if zz_key in zz_tfs:
                    zz_tf_data = zz_tfs[zz_key]
                    break
            if not isinstance(zz_tf_data, dict):
                continue

            fb_lower = _safe_float(zz_tf_data.get("lower"))
            fb_upper = _safe_float(zz_tf_data.get("upper"))
            if fb_lower is None or fb_upper is None or fb_upper <= fb_lower:
                continue

            changed = False
            if lower_contaminated:
                # Фиксим только если LLM lower отличается от ZigZag lower.
                # Если совпадает → LLM права, это не контаминация.
                if fb_lower is not None and abs(c_lower - fb_lower) / price_safe > tol_pct:
                    logging.warning(
                        "CONTAMINATION FIX (lower): %s lower=%.2f == %s lower=%.2f → ZigZag %.2f",
                        child_tf, c_lower, parent_tf, p_lower, fb_lower,
                    )
                    child["lower"] = fb_lower
                    changed = True
            if upper_contaminated:
                # Аналогично: фиксим только если LLM upper отличается от ZigZag upper.
                if fb_upper is not None and abs(c_upper - fb_upper) / price_safe > tol_pct:
                    logging.warning(
                        "CONTAMINATION FIX (upper): %s upper=%.2f == %s upper=%.2f → ZigZag %.2f",
                        child_tf, c_upper, parent_tf, p_upper, fb_upper,
                    )
                    child["upper"] = fb_upper
                    changed = True
            if changed:
                child["source"] = "zigzag_anticontamination"
                if "range" in child:
                    child["range"] = [fb_lower, fb_upper]
                fixed_count += 1

        if fixed_count:
            logging.info("CONTAMINATION: fixed %d zone(s) from ZigZag context", fixed_count)
        return tf_zones

    data["tf_zones"] = _detect_contamination(data["tf_zones"], data)

    # -----------------------------
    # 2) Валидация матрёшки зон + ограничение D1
    # -----------------------------
    def _validate_zone_nesting(tf_zones: dict, price: float | None) -> dict:
        """
        1. Младший ТФ должен быть ВНУТРИ старшего: lower_child >= lower_parent,
           upper_child <= upper_parent. Если нарушено — СУЖАЕМ CHILD до parent,
           потому что старший ТФ авторитетнее (D1 ⊃ 4H ⊃ 1H ⊃ 15M ⊃ 5M).
           Раньше расширяли parent — это приводило к слиянию всех зон в одну
           (матрёшка раскручивалась вверх: H1→H4→D1).
        2. D1 зона ограничивается ±10% от текущей цены (LLM иногда берёт
           исторический максимум за 100 свечей).
        Порядок вложенности: 1D ⊃ 4H ⊃ 1H ⊃ 15M ⊃ 5M
        """
        if not tf_zones or price is None:
            return tf_zones

        nesting_order = ["1D", "4H", "1H", "15M", "5M"]
        cap_pct = 0.10  # ±10% от цены

        # 2a-1: D1 cap УБРАН. Был нужен для сырых экстремумов (57758-67255).
        # Теперь LLM анализирует зоны по графикам + fallback VP/ZigZag —
        # cap режет реалистичные зоны в сильных трендах (support на -30% обрезался до -10%).
        # Оставлена только валидация lower>price (2a-1b ниже).

        # 2a-1b: валидация для ВСЕХ ТФ — lower не выше цены, upper не ниже цены.
        # Если lower > price → вся зона выше цены (XAUT D1 lower=4367 > price=4058).
        #   Сдвигаем lower к цене: если upper тоже > price → зона [price*0.97, upper],
        #   если upper был сдвинут D1 cap и стал близко к price → [price*0.97, price*1.03].
        # Если upper < price → аналогично для upper.
        margin_pct = 0.03  # ±3% от цены для минимальной зоны
        for tf_key, z in tf_zones.items():
            if not isinstance(z, dict):
                continue
            z_lower = z.get("lower")
            z_upper = z.get("upper")
            if z_lower is not None and z_lower > price:
                z["lower"] = round(price * (1 - margin_pct), 2)
            if z_upper is not None and z_upper < price:
                z["upper"] = round(price * (1 + margin_pct), 2)
            # После сдвигов: lower >= upper → вырожденная зона
            if z.get("lower") is not None and z.get("upper") is not None and z["lower"] >= z["upper"]:
                z["upper"] = round(price * (1 + margin_pct), 2)
                z["lower"] = round(price * (1 - margin_pct), 2)

        # 2a-2: валидация вложенности (от старшего к младшему)
        for i in range(len(nesting_order) - 1):
            parent_tf = nesting_order[i]
            child_tf = nesting_order[i + 1]
            parent = tf_zones.get(parent_tf)
            child = tf_zones.get(child_tf)
            if not isinstance(parent, dict) or not isinstance(child, dict):
                continue

            p_upper = parent.get("upper")
            p_lower = parent.get("lower")
            c_upper = child.get("upper")
            c_lower = child.get("lower")

            if p_lower is None and p_upper is None:
                continue

            # Если child выходит ЗА parent — СУЗИТЬ child до parent.
            # Старший ТФ авторитетнее: D1 ⊃ 4H ⊃ 1H ⊃ 15M ⊃ 5M.
            # Раньше расширяли parent → все зоны сливались в одну максимальную.
            if c_lower is not None and p_lower is not None and c_lower < p_lower:
                child["lower"] = p_lower
            if c_upper is not None and p_upper is not None and c_upper > p_upper:
                child["upper"] = p_upper

        # Phase 2 sync: после сдвигов upper/lower синхронизируем range.
        # Если upper/lower были изменены валидатором, range должен следовать.
        for tf_key, z in tf_zones.items():
            if not isinstance(z, dict):
                continue
            if "range" in z:
                z_low = z.get("lower")
                z_high = z.get("upper")
                if z_low is not None and z_high is not None:
                    z["range"] = [z_low, z_high]
                else:
                    z["range"] = None

        return tf_zones

    # data["tf_zones"] = _validate_zone_nesting(data["tf_zones"], data.get("price"))  # TEMP disabled Variant E Phase 1 — Z: дубль parent clamp (structure.py уже clamp'ит)

    # -----------------------------
    # 2d) Fallback: если зона удалена (min-span/uniqueness) — подставить
    # ZigZag structure zone (реальные пивоты + BOS, не микроканал).
    # Это фиксит проблему когда LLM видит сжатие последних 5 свечей и ставит
    # зону = микроканал. ZigZag даёт структурный range после BOS.
    # -----------------------------
    zz_ctx = data.get("zigzag_context") or {}
    if isinstance(zz_ctx, dict):
        zz_tfs = zz_ctx.get("timeframes") or {}
        if isinstance(zz_tfs, dict):
            for tf_key in ("5M", "15M", "1H", "4H", "1D"):
                if tf_key in data.get("tf_zones", {}):
                    continue  # зона есть — не подставляем
                # Ищем зону в zigzag по разным вариантам ключа ТФ
                zz_tf_data = None
                for zz_key in (tf_key, tf_key.lower(), tf_key.replace("M", "m").replace("H", "h").replace("D", "d")):
                    if zz_key in zz_tfs:
                        zz_tf_data = zz_tfs[zz_key]
                        break
                if not isinstance(zz_tf_data, dict):
                    continue
                # Приоритет: TF-level upper/lower (полная зона с parent constraint)
                # > structure zone > raw upper/lower
                fb_upper = _safe_float(zz_tf_data.get("upper"))
                fb_lower = _safe_float(zz_tf_data.get("lower"))
                if fb_upper is not None and fb_lower is not None and fb_upper > fb_lower:
                    logging.info(
                        "FALLBACK: %s zone from ZigZag structure: [%.2f - %.2f]",
                        tf_key, fb_lower, fb_upper,
                    )
                    if "tf_zones" not in data or not isinstance(data["tf_zones"], dict):
                        data["tf_zones"] = {}
                    data["tf_zones"][tf_key] = {
                        "upper": fb_upper,
                        "lower": fb_lower,
                        "source": "zigzag_structure_fallback",
                    }

    # -----------------------------
    # 2d-bis) Zone nesting (SOFT, temporary): top-down D1 → 4H → 1H → 15M.
    # TODO(Z): заменить на nesting_status флаг в structure.py (nested |
    # parent_broken | no_parent). Пока — только warning лог, зоны НЕ
    # удаляем и НЕ clip (clip/delete уничтожает валидную ZigZag структуру,
    # что нарушает Variant E: ZigZag = authoritative source).
    # -----------------------------
    def _log_zone_nesting(tf_zones: dict) -> dict:
        tf_order = ["1D", "4H", "1H", "15M", "5M"]
        for i, child_tf in enumerate(tf_order[1:], start=1):
            if child_tf not in tf_zones:
                continue
            child = tf_zones.get(child_tf)
            if not isinstance(child, dict):
                continue
            c_upper = _safe_float(child.get("upper"))
            c_lower = _safe_float(child.get("lower"))
            if c_upper is None or c_lower is None or c_upper <= c_lower:
                continue
            parent_tf = None
            for ptf in tf_order[:i][::-1]:
                if ptf in tf_zones and isinstance(tf_zones[ptf], dict):
                    parent_tf = ptf
                    break
            if parent_tf is None:
                continue
            parent = tf_zones[parent_tf]
            p_upper = _safe_float(parent.get("upper"))
            p_lower = _safe_float(parent.get("lower"))
            if p_upper is None or p_lower is None or p_upper <= p_lower:
                continue
            # SOFT: только лог, не трогаем зону
            if c_lower < p_lower or c_upper > p_upper:
                logging.warning(
                    "ZONE NESTING (soft): %s [%.2f - %.2f] extends beyond %s [%.2f - %.2f] "
                    "— parent_broken (zone kept as-is, awaiting nesting_status flag)",
                    child_tf, c_lower, c_upper,
                    parent_tf, p_lower, p_upper,
                )
        return tf_zones

    data["tf_zones"] = _log_zone_nesting(data["tf_zones"])

    data["confluence_levels"] = _normalize_confluence_levels(data.get("confluence_levels") or [])
    data["tf_span_map"] = {}

    rm_obj = data.get("risk_management")
    rm = rm_obj if isinstance(rm_obj, dict) else _empty_bundle()

    if "primary" not in rm and any(k in rm for k in ("sl", "tp1", "tp2", "tp3", "rr")):
        rm = {
            "primary": {
                "sl": rm.get("sl"),
                "tp1": rm.get("tp1"),
                "tp2": rm.get("tp2"),
                "tp3": rm.get("tp3"),
                "rr": rm.get("rr"),
            },
            "alternative": _empty_risk(),
        }

    primary_obj = rm.get("primary")
    alternative_obj = rm.get("alternative")
    primary = _normalize_risk_block(primary_obj)
    alternative = _normalize_risk_block(alternative_obj)

    rm["primary"] = primary
    rm["alternative"] = alternative
    data["risk_management"] = rm

    for key in ("scenario_status", "scenario_status_comment", "risk_management_comment", "entry_conditions_comment"):
        if not isinstance(data.get(key), str):
            data[key] = ""

        # -----------------------------
    # 2.1) State / history hook
    # -----------------------------
    state_obj = data.get("state_diff")
    state_diff = state_obj if isinstance(state_obj, dict) else {}
    zone_status = str(state_diff.get("zone_status", "unknown")).lower()
    active_reference_tf = str(state_diff.get("active_reference_tf", "unknown")).upper()

    if zone_status in ("broken", "false_breakout"):
        if str(data.get("signal_status", "")).lower() == "aggressive_breakout":
            data["signal_status"] = "no_signal"
            data["signal_status_comment"] = (
                "Старая зона пробита или дала ложный пробой, агрессивный сигнал понижен"
            )

    if zone_status == "rebuilt":
        data["scenario_status"] = "alternative_active"
        if not data.get("scenario_status_comment"):
            data["scenario_status_comment"] = "Структура перестроена, активен новый ориентир"

    if zone_status == "retest":
        if str(data.get("signal_status", "")).lower() in ("no_signal", "accumulation"):
            data["signal_status"] = "retest"
            data["signal_status_comment"] = "Проверка пробитой зоны в режиме ретеста"

    if zone_status == "updated_inside_range":
        if not data.get("scenario_status_comment"):
            data["scenario_status_comment"] = "Зона обновлена внутри старого диапазона"

    if active_reference_tf != "UNKNOWN":
        data["active_reference_tf"] = active_reference_tf

    data.setdefault("prev_trend", "unknown")
    data.setdefault("current_substructure", "unknown")

        # -----------------------------
    # 2.2) State / history correction
    # -----------------------------
    state_obj = data.get("state_diff")
    state_diff = state_obj if isinstance(state_obj, dict) else {}

    zone_status = str(state_diff.get("zone_status", "unknown")).lower()
    broken_levels = state_diff.get("broken_levels")
    new_levels = state_diff.get("new_levels")
    active_reference_tf = str(state_diff.get("active_reference_tf", "unknown")).upper()
    
    # -----------------------------
    # 2.3) Add explicit state event text
    # -----------------------------
    if isinstance(state_diff, dict):
        zone_status = str(state_diff.get("zone_status", "unknown")).lower()
        broken_levels = state_diff.get("broken_levels") or []
        new_levels = state_diff.get("new_levels") or []

        if zone_status == "rebuilt":
            data["state_feedback"] = f"Перестройка структуры. Пробой: {broken_levels[:3]} | Новые уровни: {new_levels[:3]}"
        elif zone_status == "retest":
            data["state_feedback"] = f"Ретест зоны. Активный ориентир: {state_diff.get('active_reference_tf', 'unknown')}"
        elif zone_status == "broken":
            data["state_feedback"] = f"Пробой уровня: {broken_levels[:3]}"
        elif zone_status == "false_breakout":
            data["state_feedback"] = f"Ложный пробой с возвратом внутрь: {broken_levels[:3]}"
        elif zone_status == "updated_inside_range":
            data["state_feedback"] = f"Обновление диапазона без пробоя. Новые уровни: {new_levels[:3]}"

    # Мягкая нормализация сигналов по состоянию зоны
    if zone_status == "broken":
        if str(data.get("signal_status", "")).lower() == "aggressive_breakout":
            data["signal_status"] = "no_signal"
            data["signal_status_comment"] = (
                "Старая зона сломана, агрессивный сигнал понижен до no_signal"
            )
        if str(data.get("scenario_status", "")).lower() not in ("alternative_active", "primary_invalidated"):
            data["scenario_status"] = "primary_invalidated"
            data["scenario_status_comment"] = "Старая зона сломана, основной сценарий ослаблен"

    elif zone_status == "false_breakout":
        if str(data.get("signal_status", "")).lower() == "aggressive_breakout":
            data["signal_status"] = "false_breakout"
        if not data.get("signal_status_comment"):
            data["signal_status_comment"] = "Зафиксирован ложный пробой зоны"

    elif zone_status == "retest":
        if str(data.get("signal_status", "")).lower() in ("no_signal", "accumulation", "false_breakout"):
            data["signal_status"] = "retest"
            data["signal_status_comment"] = "Зона проходит ретест"

    elif zone_status == "rebuilt":
        data["scenario_status"] = "alternative_active"
        if not data.get("scenario_status_comment"):
            data["scenario_status_comment"] = "Структура перестроена, активен новый ориентир"

    elif zone_status == "updated_inside_range":
        if not data.get("scenario_status_comment"):
            data["scenario_status_comment"] = "Зона обновлена внутри старого диапазона"

    if active_reference_tf != "UNKNOWN":
        data["active_reference_tf"] = active_reference_tf

    if isinstance(broken_levels, list) and broken_levels and not data.get("state_feedback"):
        data["state_feedback"] = "Есть пробитые уровни"
    if isinstance(new_levels, list) and new_levels and not data.get("state_feedback"):
        data["state_feedback"] = "Есть новые уровни"

    # -----------------------------
    # 3) ABC синхронизация
    # -----------------------------
    _normalize_wave_abc(data)

    # -----------------------------
    # 4) Сбор уровней
    # -----------------------------
    candidates: list[float] = []

    for z in data["tf_zones"].values():
        if isinstance(z, dict):
            for k in ("upper", "lower"):
                v = _safe_float(z.get(k))
                if v is not None:
                    candidates.append(v)

    key_zones_obj = data.get("key_zones")
    key_zones = key_zones_obj if isinstance(key_zones_obj, dict) else {}
    for k in ("resistance", "support"):
        v = _safe_float(key_zones.get(k))
        if v is not None:
            candidates.append(v)

    for item in data["confluence_levels"]:
        if isinstance(item, dict):
            v = _safe_float(item.get("level"))
            if v is not None:
                candidates.append(v)

    candidates.extend(_extract_zigzag_levels_from_context(data))

    # Добавляем уровни из liquidity-magnet pools если есть
    liquidity_pools = data.get("liquidity_pools") or data.get("liquidity_context", {})
    if isinstance(liquidity_pools, dict):
        for pool_key in ("resistance_pools", "support_pools", "equal_highs", "equal_lows", "pools"):
            pools = liquidity_pools.get(pool_key) or []
            if isinstance(pools, list):
                for p in pools:
                    if isinstance(p, dict):
                        lvl = _safe_float(p.get("level") or p.get("price"))
                        if lvl is not None:
                            candidates.append(lvl)
                    elif isinstance(p, (int, float)):
                        candidates.append(float(p))
    elif isinstance(liquidity_pools, list):
        for p in liquidity_pools:
            if isinstance(p, dict):
                lvl = _safe_float(p.get("level") or p.get("price"))
                if lvl is not None:
                    candidates.append(lvl)

    candidates = sorted(set(round(x, 6) for x in candidates))

    # -----------------------------
    # 5) Подавление false_breakout внутри диапазона
    # -----------------------------
    signal_status = str(data.get("signal_status", "")).lower()
    current_price = _safe_float(data.get("price"))

    if signal_status == "false_breakout":
        support = _safe_float(key_zones.get("support"))
        resistance = _safe_float(key_zones.get("resistance"))

        inside_key_range = (
            current_price is not None
            and support is not None
            and resistance is not None
            and min(support, resistance) <= current_price <= max(support, resistance)
        )

        entry_obj = data.get("entry_conditions")
        entry_conditions = entry_obj if isinstance(entry_obj, dict) else {}
        current_status = str(entry_conditions.get("current_status", "")).lower()

        # Подтверждение для false_breakout:
        # - должен быть явный выход/возврат,
        # - либо подтверждённый ретест/пробой,
        # - либо явное условие входа.
        has_confirmed_break = current_status in ("aggressive_breakout", "retest", "breakout") or bool(
            entry_conditions.get("aggressive") or entry_conditions.get("conservative")
        )

        # Если модель поставила false_breakout, но подтверждения нет,
        # не превращаем это в accumulation — понижаем в no_signal.
        if inside_key_range and not has_confirmed_break:
            data["signal_status"] = "no_signal"
            data["signal_status_comment"] = (
                "Цена внутри ключевого диапазона без подтверждённого выноса и возврата, false_breakout понижен до no_signal"
            )
            signal_status = "no_signal"
    def _zone_label(tf_str: str) -> str:
        """Нормализация имени ТФ для компактных меток."""
        tf = str(tf_str).strip().upper().replace("MIN", "M")
        label_map = {"5M": "5M", "15M": "15M", "1H": "1H", "4H": "4H", "1D": "1D"}
        return label_map.get(tf, tf)

    # -----------------------------
    # 5.1) Merge close tf zones
    # -----------------------------
    if isinstance(data.get("tf_zones"), dict):
        merged_tf_zones = {}
        for tf, z in data["tf_zones"].items():
            if not isinstance(z, dict):
                continue
            tf_key = str(tf).strip().upper().replace("MIN", "M")
            upper = _safe_float(z.get("upper"))
            lower = _safe_float(z.get("lower"))
            if upper is not None and lower is not None and lower > upper:
                lower, upper = upper, lower
            entry = {"upper": upper, "lower": lower}
            # Phase 2: сохраняем bos поля и range при merge
            if "range" in z:
                entry["range"] = [lower, upper] if (lower is not None and upper is not None) else None
            for bos_key in ("bos_price", "bos_dir", "bos_age"):
                if bos_key in z:
                    entry[bos_key] = z[bos_key]
            # source тоже сохраняем
            if "source" in z:
                entry["source"] = z["source"]
            merged_tf_zones[tf_key] = entry

        # keep individual tf zones — visual grouping is done in format_json_for_tg
        data["tf_zones"] = merged_tf_zones

    # -----------------------------
    # 6) tf_span_map
    # -----------------------------
    sorted_zones = _sorted_tf_order(data["tf_zones"])
    tf_span_map = {}
    for tf, z in sorted_zones:
        span = _zone_span(z)
        if span is not None:
            tf_span_map[tf] = span
    data["tf_span_map"] = tf_span_map

    # -----------------------------
    # 7) Если no_signal / accumulation — не создаём риск-блоки
    # -----------------------------
    if signal_status in ("accumulation", "no_signal"):
        data["scenario_status"] = "no_alternative"
        if not isinstance(data.get("scenario_status_comment"), str) or not data["scenario_status_comment"].strip():
            data["scenario_status_comment"] = "Цена внутри диапазона без подтверждённого выхода"
        rm["primary"] = _empty_risk()
        rm["alternative"] = _empty_risk()

    # -----------------------------
    # 8) ABC по комментариям
    # -----------------------------
    wave_comment = str(data.get("wave_phase_comment", "")).lower()

    if "abc вниз" in wave_comment or "abc-коррекции вниз" in wave_comment:
        data["abc_risk"] = "abc_risk_down"
        if not data.get("abc_risk_comment"):
            data["abc_risk_comment"] = "Риск ABC вниз по волновой фазе"
    elif "abc вверх" in wave_comment or "abc-коррекции вверх" in wave_comment:
        data["abc_risk"] = "abc_risk_up"
        if not data.get("abc_risk_comment"):
            data["abc_risk_comment"] = "Риск ABC вверх по волновой фазе"

    abc_risk = str(data.get("abc_risk", "")).lower()
    if abc_risk not in ("abc_risk_up", "abc_risk_down", "none", "unknown"):
        if "abc вниз" in wave_comment:
            data["abc_risk"] = "abc_risk_down"
        elif "abc вверх" in wave_comment:
            data["abc_risk"] = "abc_risk_up"
        else:
            data["abc_risk"] = "unknown"


    # -----------------------------
    # 8.1) Иерархия сигналов (P0.1 из ТЗ)
    # Если current_substructure противоречит signal_status -
    # приоритет у substructure (более конкретное поле).
    # -----------------------------
    SIGNAL_PRIORITY = {
        "aggressive_breakout": 0,
        "retest": 1,
        "reversal": 2,
        "false_breakout": 3,
        "accumulation": 4,
        "no_signal": 5,
    }
    raw_sub = str(data.get("current_substructure", "")).lower()
    llm_signal = str(data.get("signal_status", "")).lower()

    sub_to_signal = {
        "breakout_up": "aggressive_breakout",
        "breakout_down": "aggressive_breakout",
        "false_breakout_up": "false_breakout",
        "false_breakout_down": "false_breakout",
        "reversal_attempt_up": "reversal",
        "reversal_attempt_down": "reversal",
    }
    if raw_sub in sub_to_signal and llm_signal in SIGNAL_PRIORITY:
        resolved = sub_to_signal[raw_sub]
        if SIGNAL_PRIORITY.get(resolved, 99) < SIGNAL_PRIORITY.get(llm_signal, 99):
            data["signal_status"] = resolved
            data["signal_status_comment"] = (
                f"Иерархия: substructure={raw_sub} приоритетнее signal={llm_signal}"
            )
            signal_status = resolved

    # -----------------------------
    # 9) Определение направления
    # -----------------------------
    direction_hint = _direction_from_data(data)

    # -----------------------------
    # 10) TP/SL для primary
    # -----------------------------
    primary = rm.get("primary")
    if not isinstance(primary, dict):
        primary = _empty_risk()
        rm["primary"] = primary

    if current_price is not None and candidates and data.get("signal_status") in ("aggressive_breakout", "retest", "reversal", "false_breakout"):
        tp1, tp2, tp3 = _pick_tp_levels(direction_hint, current_price, candidates)
        # DEBUG: TP fill trace
        if direction_hint == "long":
            if tp1 is not None and tp1 > current_price and primary.get("tp1") is None:
                primary["tp1"] = tp1
            if tp2 is not None and tp2 > current_price and tp2 != primary.get("tp1") and primary.get("tp2") is None:
                primary["tp2"] = tp2
            if tp3 is not None and tp3 > current_price and tp3 not in (primary.get("tp1"), primary.get("tp2")) and primary.get("tp3") is None:
                primary["tp3"] = tp3
        else:
            if tp1 is not None and tp1 < current_price and primary.get("tp1") is None:
                primary["tp1"] = tp1
            if tp2 is not None and tp2 < current_price and tp2 != primary.get("tp1") and primary.get("tp2") is None:
                primary["tp2"] = tp2
            if tp3 is not None and tp3 < current_price and tp3 not in (primary.get("tp1"), primary.get("tp2")) and primary.get("tp3") is None:
                primary["tp3"] = tp3

        if primary.get("sl") is None or (
            current_price is not None
            and _safe_float(primary.get("sl")) is not None
            and abs(current_price - _safe_float(primary.get("sl"))) / max(current_price, 1e-9) < 0.005  # SL < 0.5% = слишком близко
        ):
            # SWING-SL: берём ближайший zigzag structure swing high/low, не любой candidate
            # zigzag structure.high/low = разворотные пивоты с протарговкой
            swing_highs, swing_lows = _extract_swing_levels(data)

            # ── AGGRESSIVE_BREAKOUT: SL = противоположная граница зоны пробоя ──
            # По Возному: aggressive = пробой границы, SL = край зоны (противоположная граница)
            # zone_breakout_up=True → long, SL = zone_low (противоположная)
            # zone_breakout_down=True → short, SL = zone_high (противоположная)
            breakout_sl = None
            if signal_status == "aggressive_breakout":
                zz_ctx_sl = data.get("zigzag_context") or {}
                if isinstance(zz_ctx_sl, dict):
                    zz_tfs_sl = zz_ctx_sl.get("timeframes") or {}
                    # Ищем ТФ где есть breakout (от M15 вверх — младший = сигнал)
                    for tf_key in ("15m", "1h", "4h", "1D"):
                        tfd = zz_tfs_sl.get(tf_key)
                        if not isinstance(tfd, dict):
                            continue
                        struct = tfd.get("structure") or {}
                        if not isinstance(struct, dict):
                            continue
                        bu = struct.get("zone_breakout_up")
                        bd = struct.get("zone_breakout_down")
                        curr = struct.get("curr_structure") or {}
                        if not isinstance(curr, dict):
                            continue
                        z_low = _safe_float(curr.get("low"))
                        z_high = _safe_float(curr.get("high"))
                        if bu and z_low is not None and z_low < current_price:
                            breakout_sl = z_low  # long: SL = zone_low
                            logging.info(
                                "SL (breakout zone): %s long, TF=%s, zone=[%.2f-%.2f], SL=zone_low=%.2f",
                                "primary", tf_key, z_low, z_high, breakout_sl,
                            )
                            break
                        if bd and z_high is not None and z_high > current_price:
                            breakout_sl = z_high  # short: SL = zone_high
                            logging.info(
                                "SL (breakout zone): %s short, TF=%s, zone=[%.2f-%.2f], SL=zone_high=%.2f",
                                "primary", tf_key, z_low, z_high, breakout_sl,
                            )
                            break

            if breakout_sl is not None:
                # Для aggressive_breakout: SL = MAX(зона, ближайший swing старшего ТФ)
                # Зона = минимальный SL (край зоны пробоя по Возному)
                # Swing H1/H4 = структурный SL (если дальше от entry — реальный уровень)
                # ОГРАНИЧЕНИЕ: swing в пределах 8% от entry (1D swing = слишком далеко, 19%)
                max_sl_distance = abs(current_price) * 0.08 if current_price is not None else float('inf')
                if direction_hint == "long":
                    # long: SL ниже entry. Берём swing low ПОД entry который СТРОГО дальше от entry чем zone_low
                    below = [x for x in swing_lows if x < current_price] if current_price is not None else []
                    # zone_low = минимальный SL. Ищем swing low СТРОГО дальше zone_low (структурный H1/H4)
                    # но не дальше 8% от entry (1D swing слишком далеко → RR < 1.0)
                    farther_below = [x for x in below if x < breakout_sl and (current_price - x) <= max_sl_distance]
                    if farther_below:
                        # берём ближайший к entry из тех что СТРОГО дальше zone_low (но ниже = дальше от entry)
                        primary["sl"] = farther_below[-1]
                        logging.info(
                            "SL (breakout zone + swing): long, zone_low=%.2f, swing=%.2f (структурный дальше)",
                            breakout_sl, farther_below[-1],
                        )
                    else:
                        primary["sl"] = breakout_sl
                else:
                    # short: SL выше entry. Берём swing high НАД entry который СТРОГО дальше от entry чем zone_high
                    above = [x for x in swing_highs if x > current_price] if current_price is not None else []
                    # zone_high = минимальный SL. Ищем swing high СТРОГО дальше zone_high (структурный H1/H4)
                    # но не дальше 8% от entry (1D swing слишком далеко → RR < 1.0)
                    farther_above = [x for x in above if x > breakout_sl and (x - current_price) <= max_sl_distance]
                    if farther_above:
                        # берём ближайший к entry из тех что СТРОГО дальше zone_high
                        primary["sl"] = farther_above[0]
                        logging.info(
                            "SL (breakout zone + swing): short, zone_high=%.2f, swing=%.2f (структурный дальше)",
                            breakout_sl, farther_above[0],
                        )
                    else:
                        primary["sl"] = breakout_sl
            elif direction_hint == "long":
                # long: SL ниже entry — ближайший swing low под entry (H1/H4 приоритет, не M15)
                below = [x for x in swing_lows if x < current_price] if current_price is not None else []
                if below:
                    primary["sl"] = below[-1]  # ближайший к entry
                else:
                    # fallback: candidate swing low
                    cand_below = [x for x in candidates if x < current_price] if current_price is not None else []
                    primary["sl"] = cand_below[-1] if cand_below else (min(candidates) if candidates else None)
            else:
                # short: SL выше entry — ближайший swing high над entry
                above = [x for x in swing_highs if x > current_price] if current_price is not None else []
                if above:
                    primary["sl"] = above[0]  # ближайший к entry
                else:
                    # fallback: candidate swing high
                    cand_above = [x for x in candidates if x > current_price] if current_price is not None else []
                    primary["sl"] = cand_above[0] if cand_above else (max(candidates) if candidates else None)

        if direction_hint == "long":
            # LONG: SL must be BELOW entry, TP must be ABOVE entry
            for k in ("tp1", "tp2", "tp3"):
                tp_val = _safe_float(rm["primary"].get(k))
                if tp_val is not None and current_price is not None and tp_val <= current_price:
                    rm["primary"][k] = None

            sl_val = _safe_float(rm["primary"].get("sl"))
            if sl_val is not None and current_price is not None and sl_val >= current_price:
                # SL above entry (invalid for long) — recalculate from swing lows below price
                swing_highs_s, swing_lows_s = _extract_swing_levels(data)
                below = [x for x in swing_lows_s if x < current_price] if current_price is not None else []
                if below:
                    rm["primary"]["sl"] = below[-1]
                else:
                    cand_below = [x for x in candidates if x < current_price] if current_price is not None else []
                    rm["primary"]["sl"] = cand_below[-1] if cand_below else (min(candidates) if candidates else None)

    # -----------------------------
    # 11) TP/SL для alternative
    # -----------------------------
    alt_direction = "short" if direction_hint == "long" else "long"
    alternative = rm.get("alternative")
    if not isinstance(alternative, dict):
        alternative = _empty_risk()
        rm["alternative"] = alternative

    alt_status = str(data.get("scenario_status", "")).lower()
    if current_price is not None and candidates and (alt_status in ("alternative_active", "primary_invalidated") or signal_status == "false_breakout"):
        tp1, tp2, tp3 = _pick_tp_levels(alt_direction, current_price, candidates)

        if alt_direction == "long":
            if tp1 is not None and tp1 > current_price and alternative.get("tp1") is None:
                alternative["tp1"] = tp1
            if tp2 is not None and tp2 > current_price and tp2 != alternative.get("tp1") and alternative.get("tp2") is None:
                alternative["tp2"] = tp2
            if tp3 is not None and tp3 > current_price and tp3 not in (alternative.get("tp1"), alternative.get("tp2")) and alternative.get("tp3") is None:
                alternative["tp3"] = tp3
        else:
            if tp1 is not None and tp1 < current_price and alternative.get("tp1") is None:
                alternative["tp1"] = tp1
            if tp2 is not None and tp2 < current_price and tp2 != alternative.get("tp1") and alternative.get("tp2") is None:
                alternative["tp2"] = tp2
            if tp3 is not None and tp3 < current_price and tp3 not in (alternative.get("tp1"), alternative.get("tp2")) and alternative.get("tp3") is None:
                alternative["tp3"] = tp3

        if alternative.get("sl") is None:
            # SWING-SL для alternative (та же логика что primary)
            swing_highs_a, swing_lows_a = _extract_swing_levels(data)
            if alt_direction == "long":
                # long: SL ниже entry — ближайший swing low под entry
                below = [x for x in swing_lows_a if x < current_price] if current_price is not None else []
                if below:
                    alternative["sl"] = below[-1]
                else:
                    cand_below = [x for x in candidates if x < current_price] if current_price is not None else []
                    alternative["sl"] = cand_below[-1] if cand_below else (min(candidates) if candidates else None)
            else:
                # short: SL выше entry — ближайший swing high над entry
                above = [x for x in swing_highs_a if x > current_price] if current_price is not None else []
                if above:
                    alternative["sl"] = above[0]
                else:
                    cand_above = [x for x in candidates if x > current_price] if current_price is not None else []
                    alternative["sl"] = cand_above[0] if cand_above else (max(candidates) if candidates else None)

        if alt_direction == "long":
            for k in ("tp1", "tp2", "tp3"):
                tp_val = _safe_float(rm["alternative"].get(k))
                if tp_val is not None and current_price is not None and tp_val <= current_price:
                    rm["alternative"][k] = None

            sl_val = _safe_float(rm["alternative"].get("sl"))
            if sl_val is not None and current_price is not None and sl_val >= current_price:
                swing_highs_r, swing_lows_r = _extract_swing_levels(data)
                below = [x for x in swing_lows_r if x < current_price] if current_price is not None else []
                if below:
                    rm["alternative"]["sl"] = below[-1]
                else:
                    cand_below = [x for x in candidates if x < current_price] if current_price is not None else []
                    rm["alternative"]["sl"] = cand_below[-1] if cand_below else (min(candidates) if candidates else None)
        else:
            for k in ("tp1", "tp2", "tp3"):
                tp_val = _safe_float(rm["alternative"].get(k))
                if tp_val is not None and current_price is not None and tp_val >= current_price:
                    rm["alternative"][k] = None

            sl_val = _safe_float(rm["alternative"].get("sl"))
            if sl_val is not None and current_price is not None and sl_val <= current_price:
                above = [x for x in candidates if current_price is not None and x > current_price]
                rm["alternative"]["sl"] = above[0] if above else (max(candidates) if candidates else None)

    # -----------------------------
    # 12) RR
    # -----------------------------
    entry_price = current_price

    primary["rr"] = _calc_rr(
        entry_price,
        _safe_float(primary.get("sl")),
        _safe_float(primary.get("tp1")),
    )
    alternative["rr"] = _calc_rr(
        entry_price,
        _safe_float(alternative.get("sl")),
        _safe_float(alternative.get("tp1")),
    )

        # -----------------------------
    # 12.1) Volume-aware validation
    # -----------------------------
    volume_obj = data.get("volume_context")
    volume_ctx = volume_obj if isinstance(volume_obj, dict) else {}

    ad_trend = str(volume_ctx.get("ad_trend", "unknown")).lower()
    cmf_val = _safe_float(volume_ctx.get("cmf_20"))
    volume_confirmation = str(volume_ctx.get("volume_confirmation", "unknown")).lower()
    divergence = str(volume_ctx.get("divergence", "unknown")).lower()

    volume_bullish = (
        volume_confirmation == "bullish"
        or (ad_trend == "rising" and (cmf_val is not None and cmf_val > 0))
        or divergence == "bullish"
    )
    volume_bearish = (
        volume_confirmation == "bearish"
        or (ad_trend == "falling" and (cmf_val is not None and cmf_val < 0))
        or divergence == "bearish"
    )

        # -----------------------------
    # 12.2) Scenario / risk consistency
    # -----------------------------
    primary_has_risk = any(primary.get(k) is not None for k in ("sl", "tp1", "tp2", "tp3"))
    alt_has_risk = any(alternative.get(k) is not None for k in ("sl", "tp1", "tp2", "tp3"))

    # Если primary_valid, но primary пустой — это ошибка сценария
    if str(data.get("scenario_status", "")).lower() == "primary_valid" and not primary_has_risk:
        if alt_has_risk:
            data["scenario_status"] = "alternative_active"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Основной сценарий не собран, активен альтернативный."
            )
        else:
            data["scenario_status"] = "no_alternative"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Сценарий не подтверждён, риск-блоки пусты."
            )

    # Если false_breakout есть, но primary пустой — строим primary по текущему направлению
    if signal_status.startswith("false_breakout") and not primary_has_risk and current_price is not None and candidates:
        if direction_hint == "long":
            below = [x for x in candidates if x < current_price]
            above = [x for x in candidates if x > current_price]

            primary["sl"] = below[-1] if below else (min(candidates) if candidates else None)
            primary["tp1"] = above[0] if above else None
            primary["tp2"] = above[1] if len(above) > 1 else primary["tp1"]
            primary["tp3"] = above[2] if len(above) > 2 else primary["tp2"]
        else:
            above = [x for x in candidates if x > current_price]
            below = [x for x in candidates if x < current_price]

            primary["sl"] = above[0] if above else (max(candidates) if candidates else None)
            primary["tp1"] = below[-1] if below else None
            primary["tp2"] = below[-2] if len(below) > 1 else primary["tp1"]
            primary["tp3"] = below[-3] if len(below) > 2 else primary["tp2"]

        primary["rr"] = _calc_rr(
            current_price,
            _safe_float(primary.get("sl")),
            _safe_float(primary.get("tp1")),
        )
        rm["primary"] = primary

            # -----------------------------
    # 12.3) SL adjustment with volume context
    # -----------------------------
    def _widen_sl(sl_value: float | None, price_value: float | None, direction: str, factor: float = 0.35) -> float | None:
        if sl_value is None or price_value is None:
            return sl_value
        if direction == "long":
            return round(sl_value - abs(price_value - sl_value) * factor, 6)
        if direction == "short":
            return round(sl_value + abs(price_value - sl_value) * factor, 6)
        return sl_value

    # Если объём не подтверждает движение — SL чуть дальше, чтобы не выбило на шуме
    if current_price is not None:
        if signal_status.startswith("false_breakout"):
            if volume_bullish and direction_hint == "long":
                primary["sl"] = _widen_sl(_safe_float(primary.get("sl")), current_price, "long", factor=0.20)
            elif volume_bearish and direction_hint == "short":
                primary["sl"] = _widen_sl(_safe_float(primary.get("sl")), current_price, "short", factor=0.20)
            else:
                primary["sl"] = _widen_sl(_safe_float(primary.get("sl")), current_price, direction_hint, factor=0.45)

        elif signal_status in ("retest", "reversal"):
            primary["sl"] = _widen_sl(_safe_float(primary.get("sl")), current_price, direction_hint, factor=0.30)

    primary["rr"] = _calc_rr(
        current_price,
        _safe_float(primary.get("sl")),
        _safe_float(primary.get("tp1")),
    )

        # -----------------------------
    # 12.4) Volume invalidation
    # -----------------------------
    if signal_status.startswith("false_breakout") and current_price is not None:
        if direction_hint == "long" and volume_bearish:
            data["scenario_status"] = "primary_invalidated"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Объём не подтверждает длинный сценарий."
            )
        elif direction_hint == "short" and volume_bullish:
            data["scenario_status"] = "primary_invalidated"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Объём не подтверждает короткий сценарий."
            )
        
        # -----------------------------
    # 12.5) Reconcile scenario with risk blocks
    # -----------------------------
    primary_has_risk = any(primary.get(k) is not None for k in ("sl", "tp1", "tp2", "tp3"))
    alt_has_risk = any(alternative.get(k) is not None for k in ("sl", "tp1", "tp2", "tp3"))

    if str(data.get("scenario_status", "")).lower() == "primary_valid" and not primary_has_risk:
        if alt_has_risk:
            data["scenario_status"] = "alternative_active"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Основной сценарий не собран, активен альтернативный."
            )
        else:
            data["scenario_status"] = "no_alternative"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Сценарий не подтверждён, риск-блоки пусты."
            )

    if signal_status.startswith("false_breakout") and not primary_has_risk and current_price is not None and candidates:
        if direction_hint == "long":
            below = [x for x in candidates if x < current_price]
            above = [x for x in candidates if x > current_price]
            primary["sl"] = below[-1] if below else (min(candidates) if candidates else None)
            primary["tp1"] = above[0] if above else None
            primary["tp2"] = above[1] if len(above) > 1 else primary["tp1"]
            primary["tp3"] = above[2] if len(above) > 2 else primary["tp2"]
        else:
            above = [x for x in candidates if x > current_price]
            below = [x for x in candidates if x < current_price]
            primary["sl"] = above[0] if above else (max(candidates) if candidates else None)
            primary["tp1"] = below[-1] if below else None
            primary["tp2"] = below[-2] if len(below) > 1 else primary["tp1"]
            primary["tp3"] = below[-3] if len(below) > 2 else primary["tp2"]

        primary["rr"] = _calc_rr(
            current_price,
            _safe_float(primary.get("sl")),
            _safe_float(primary.get("tp1")),
        )
        rm["primary"] = primary

    # -----------------------------
    # 12.6) SL buffer from structural edge
    # -----------------------------
    atr_hint = _safe_float(data.get("atr")) or _safe_float(data.get("atr_last"))
    if atr_hint is None:
        atr_hint = abs(current_price or 0.0) * 0.002 if current_price is not None else 0.0
    sl_buffer = max(atr_hint * 0.2, (abs(current_price) * 0.0015 if current_price is not None else 0.0), 1e-9)

    def _apply_sl_buffer(sl_value: float | None, direction: str) -> float | None:
        if sl_value is None or current_price is None:
            return sl_value
        if direction == "long":
            return round(sl_value - sl_buffer, 6)
        if direction == "short":
            return round(sl_value + sl_buffer, 6)
        return sl_value

    if primary.get("sl") is not None:
        primary["sl"] = _apply_sl_buffer(_safe_float(primary.get("sl")), direction_hint)

    if alt_has_risk and alternative.get("sl") is not None:
        alt_direction = "short" if direction_hint == "long" else "long"
        alternative["sl"] = _apply_sl_buffer(_safe_float(alternative.get("sl")), alt_direction)

    # -----------------------------
    # 12.6b) P2-4: SL/TP logical validation
    # LONG  → SL must be < price, TP must be > price
    # SHORT → SL must be > price, TP must be < price
    # RR must be > 1.0, otherwise clear risk block (unrealistic)
    # -----------------------------
    active_signals = ("aggressive_breakout", "retest", "reversal", "false_breakout")
    if signal_status in active_signals and current_price is not None:
        for block_name, block_dir in (("primary", direction_hint), ("alternative", alt_direction)):
            block = rm.get(block_name)
            if not isinstance(block, dict):
                continue
            sl = _safe_float(block.get("sl"))
            tp1 = _safe_float(block.get("tp1"))
            rr = _safe_float(block.get("rr"))

            if block_dir == "long":
                if sl is not None and sl >= current_price:
                    block["sl"] = None
                    block["sl_invalid"] = True
                if tp1 is not None and tp1 <= current_price:
                    block["tp1"] = None
                    block["tp1_invalid"] = True
            else:  # short
                if sl is not None and sl <= current_price:
                    block["sl"] = None
                    block["sl_invalid"] = True
                if tp1 is not None and tp1 >= current_price:
                    block["tp1"] = None
                    block["tp1_invalid"] = True

            # Recalc RR after possible corrections
            block["rr"] = _calc_rr(current_price, _safe_float(block.get("sl")), _safe_float(block.get("tp1")))
            rr = _safe_float(block.get("rr"))

            # RR < 1.0 — risk > reward, signal is unrealistic
            # ИСКЛЮЧЕНИЕ: aggressive_breakout — агрессивный вход = высокий риск по определению,
            # SL = структурный уровень (край зоны / swing H1), TP1 = противоположная граница зоны.
            # RR может быть < 1.0 (короткий TP, далекий SL) — это нормально для агрессивного входа.
            # Не обнуляем SL — пусть user видит реальный структурный уровень.
            if rr is not None and rr < 1.0 and signal_status != "aggressive_breakout":
                block["sl"] = None
                block["tp1"] = None
                block["tp2"] = None
                block["tp3"] = None
                block["rr"] = None
                block["rr_invalid"] = True

        rm["primary"] = rm.get("primary", primary)
        rm["alternative"] = rm.get("alternative", alternative)
        data["risk_management"] = rm

    # -----------------------------
    # 12.7) Volume bias influence
    # -----------------------------
    if current_price is not None:
        if volume_bullish and direction_hint == "long":
            data["confidence"] = "high" if str(data.get("confidence", "")).lower() != "high" else data["confidence"]
            if not data.get("confidence_reason"):
                data["confidence_reason"] = "Объём подтверждает направление вверх."
        elif volume_bearish and direction_hint == "short":
            data["confidence"] = "high" if str(data.get("confidence", "")).lower() != "high" else data["confidence"]
            if not data.get("confidence_reason"):
                data["confidence_reason"] = "Объём подтверждает направление вниз."
        elif volume_confirmation == "neutral" or divergence == "none":
            if str(data.get("confidence", "")).lower() == "high":
                data["confidence"] = "medium"

    primary["rr"] = _calc_rr(
        current_price,
        _safe_float(primary.get("sl")),
        _safe_float(primary.get("tp1")),
    )
    alternative["rr"] = _calc_rr(
        current_price,
        _safe_float(alternative.get("sl")),
        _safe_float(alternative.get("tp1")),
    )

        # -----------------------------
    # 12.8) Final consistency normalizer
    # -----------------------------
    primary_has_risk = any(primary.get(k) is not None for k in ("sl", "tp1", "tp2", "tp3"))
    alt_has_risk = any(alternative.get(k) is not None for k in ("sl", "tp1", "tp2", "tp3"))

    if str(data.get("scenario_status", "")).lower() == "primary_valid" and not primary_has_risk:
        if alt_has_risk:
            data["scenario_status"] = "alternative_active"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Основной сценарий не собран, активен альтернативный."
            )
        else:
            data["scenario_status"] = "no_alternative"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Сценарий не подтверждён, риск-блоки пусты."
            )

    if data.get("signal_status") == "no_signal":
        rm["primary"] = _empty_risk()
        rm["alternative"] = _empty_risk()
        data["risk_management"] = rm
        data["scenario_status"] = "no_alternative"
        if not data.get("scenario_status_comment"):
            data["scenario_status_comment"] = "Нет сигнала, риск-блоки очищены."

    if data.get("signal_status") == "accumulation":
        rm["primary"] = _empty_risk()
        rm["alternative"] = _empty_risk()
        data["risk_management"] = rm
        data["scenario_status"] = "no_alternative"
        if not data.get("scenario_status_comment"):
            data["scenario_status_comment"] = "Накопление без подтверждённого пробоя."

    if signal_status.startswith("false_breakout") and not primary_has_risk and current_price is not None and candidates:
        if direction_hint == "long":
            below = [x for x in candidates if x < current_price]
            above = [x for x in candidates if x > current_price]
            primary["sl"] = round((below[-1] if below else min(candidates)) - sl_buffer, 6)
            primary["tp1"] = above[0] if above else None
            primary["tp2"] = above[1] if len(above) > 1 else primary["tp1"]
            primary["tp3"] = above[2] if len(above) > 2 else primary["tp2"]
        else:
            above = [x for x in candidates if x > current_price]
            below = [x for x in candidates if x < current_price]
            primary["sl"] = round((above[0] if above else max(candidates)) + sl_buffer, 6)
            primary["tp1"] = below[-1] if below else None
            primary["tp2"] = below[-2] if len(below) > 1 else primary["tp1"]
            primary["tp3"] = below[-3] if len(below) > 2 else primary["tp2"]

        primary["rr"] = _calc_rr(
            current_price,
            _safe_float(primary.get("sl")),
            _safe_float(primary.get("tp1")),
        )
        rm["primary"] = primary

    if signal_status.startswith("false_breakout"):
        if direction_hint == "long" and volume_bearish:
            data["scenario_status"] = "primary_invalidated"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Объём не подтверждает длинный сценарий."
            )
        elif direction_hint == "short" and volume_bullish:
            data["scenario_status"] = "primary_invalidated"
            data["scenario_status_comment"] = (
                data.get("scenario_status_comment") or "Объём не подтверждает короткий сценарий."
            )

    primary["rr"] = _calc_rr(
        current_price,
        _safe_float(primary.get("sl")),
        _safe_float(primary.get("tp1")),
    )
    alternative["rr"] = _calc_rr(
        current_price,
        _safe_float(alternative.get("sl")),
        _safe_float(alternative.get("tp1")),
    )

    rm["primary"] = primary
    rm["alternative"] = alternative
    data["risk_management"] = rm

    # -----------------------------
    # 13) Финальная нормализация комментариев и статусов
    # -----------------------------
    if data.get("scenario_status") == "alternative_active":
        if not isinstance(data.get("scenario_status_comment"), str) or not data["scenario_status_comment"].strip():
            data["scenario_status_comment"] = "Альтернативный сценарий активен"

    if data.get("scenario_status") not in (
        "primary_valid",
        "primary_invalidated",
        "alternative_active",
        "no_alternative",
    ):
        data["scenario_status"] = "no_alternative"

    for key in ("risk_management_comment", "scenario_status_comment", "entry_conditions_comment"):
        val = data.get(key)
        if not isinstance(val, str):
            data[key] = ""

    # -----------------------------
    # 14) Финальный нормализатор блока риска
    # -----------------------------
    rm_final = data.get("risk_management")
    if not isinstance(rm_final, dict):
        rm_final = _empty_bundle()

    primary_final = rm_final.get("primary")
    alternative_final = rm_final.get("alternative")

    if not isinstance(primary_final, dict):
        primary_final = _empty_risk()
        rm_final["primary"] = primary_final
    if not isinstance(alternative_final, dict):
        alternative_final = _empty_risk()
        rm_final["alternative"] = alternative_final

    rm_final["primary"] = _normalize_risk_block(primary_final)
    rm_final["alternative"] = _normalize_risk_block(alternative_final)
    data["risk_management"] = rm_final

    # -----------------------------
    # 14.1) Remove noisy placeholders
    # -----------------------------
    for key in ("fact_feedback", "signal_status_comment", "scenario_status_comment", "risk_management_comment", "entry_conditions_comment"):
        if isinstance(data.get(key), str):
            data[key] = _normalize_zone_text(data[key])

    # remove duplicate spaces / empty lines in text fields
    for key in ("fact_feedback", "signal_status_comment", "scenario_status_comment", "risk_management_comment", "entry_conditions_comment", "confidence_reason"):
        if isinstance(data.get(key), str):
            data[key] = _cleanup_empty_lines(data[key])

    # if missing facts, make them explicit only when needed
    if not str(data.get("fact_feedback", "")).strip():
        data["fact_feedback"] = "Нет подтверждённых фактов для сигнала."

    return data

def _normalize_zone_text(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    text = text.replace("—", "-").replace("–", "-")
    text = text.replace("  ", " ")
    return text


def _format_num(v):
    try:
        if v is None:
            return "Н/Д"
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN or Inf
            return "Н/Д"
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.2f}"
    except Exception:
        return "Н/Д"


def _merge_close_levels(levels: list[float], tolerance_ratio: float = 0.0015) -> list[float]:
    if not levels:
        return []
    levels = sorted(float(x) for x in levels if x is not None)
    merged = [levels[0]]
    for lvl in levels[1:]:
        last = merged[-1]
        tol = max(abs(last) * tolerance_ratio, 0.5)
        if abs(lvl - last) <= tol:
            merged[-1] = round((last + lvl) / 2.0, 6)
        else:
            merged.append(lvl)
    return merged


def _zone_label(tf: str) -> str:
    tf = str(tf or "").strip().upper()
    if tf in ("1H", "H1", "1"):
        return "H1"
    if tf in ("4H", "H4", "4"):
        return "H4"
    if tf in ("15M", "M15", "15"):
        return "M15"
    if tf in ("1D", "D1"):
        return "D1"
    return tf or "TF"


def _cleanup_empty_lines(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    out = []
    prev_empty = False
    for line in lines:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        out.append(line)
        prev_empty = is_empty
    return "\n".join(out).strip()

def format_json_for_tg(data: dict) -> str:
    if not isinstance(data, dict):
        return "⚠️ Неверный формат данных анализа."
    if data.get("error"):
        return f"⚠️ {data.get('message', 'Ошибка анализа.')}"

    def fmt(v):
        if v is None or v == "None" or v == "":
            return "Н/Д"
        return str(v)

    def clean(v):
        s = fmt(v)
        return "" if s in ("Н/Д", "[]", "{}") else s

    price = data.get("price")
    signal_status = str(data.get("signal_status", "no_signal"))
    current_substructure = str(data.get("current_substructure", "unknown"))
    prev_trend = str(data.get("prev_trend", "unknown"))

    key_zones = data.get("key_zones") or {}
    risk = data.get("risk_management") or {}
    entry = data.get("entry_conditions") or {}
    tf_zones = data.get("tf_zones") or {}
    confluence_levels = data.get("confluence_levels") or []
    tf_span_map = data.get("tf_span_map") or {}

    primary = risk.get("primary") or {}
    alternative = risk.get("alternative") or {}

    psl = primary.get("sl")
    ptp1 = primary.get("tp1")
    ptp2 = primary.get("tp2")
    ptp3 = primary.get("tp3")
    prr = primary.get("rr")

    asl = alternative.get("sl")
    atp1 = alternative.get("tp1")
    atp2 = alternative.get("tp2")
    atp3 = alternative.get("tp3")
    arr = alternative.get("rr")

    ordered = []
    for tf, z in tf_zones.items():
        if isinstance(z, dict):
            upper = z.get("upper")
            lower = z.get("lower")
            if upper is not None or lower is not None:
                ordered.append((_zone_label(tf), z))
    tf_block = []
    if ordered:
        # Каждый ТФ на отдельной строке — без группировки через "/".
        # Группировка создавала путаницу: "D1/H4" выглядело как баг.
        # Phase 2: показываем оба уровня зоны + BOS-уровень (Z's request):
        #   • 1D: [55000 - 64680] | BOS↑ 64680 (age=20)
        # Zone = [prev swing low - prev swing high] до BOS.
        # bos_price = broken_level = пробитый уровень (где был BOS).
        for tf, z in ordered:
            upper = z.get("upper")
            lower = z.get("lower")
            zone_str = f"• {tf}: [{_format_num(lower)} - {_format_num(upper)}]"
            # Phase 2: добавляем BOS-уровень если есть
            bos_price = z.get("bos_price")
            bos_dir = z.get("bos_dir")
            bos_age = z.get("bos_age")
            if bos_price is not None:
                dir_arrow = "↑" if bos_dir == "up" else "↓" if bos_dir == "down" else "•"
                age_str = f" age={bos_age}" if bos_age is not None else ""
                zone_str += f" | BOS{dir_arrow} {_format_num(bos_price)}{age_str}"
            tf_block.append(zone_str)
    else:
        tf_block.append("• Нет")

    confluence_text = []
    if isinstance(confluence_levels, list) and confluence_levels:
        for item in confluence_levels[:8]:
            if isinstance(item, dict):
                lvl = item.get("level")
                tfs = item.get("timeframes", [])
                pr = item.get("priority", "low")
                sp = item.get("spread")
                kind = item.get("kind", "mixed")
                spread_str = f"spread={_format_num(sp)}" if sp and float(sp) != 0 else f"count={item.get('count', '?')}"
                # FEELS: показать log_distance и proximity если есть
                ld = item.get("log_distance")
                prox = item.get("proximity_score")
                ld_str = f" ld={ld:.3f}" if ld is not None else ""
                prox_str = f" prox={prox:.2f}" if prox is not None else ""
                confluence_text.append(
                    f"• {_format_num(lvl)} | TF: {fmt(tfs)} | {pr} | {spread_str}{ld_str}{prox_str} | {kind}"
                )

    tf_span_text = []
    if isinstance(tf_span_map, dict) and tf_span_map:
        for tf, span in tf_span_map.items():
            tf_span_text.append(f"• {_zone_label(tf)}: {_format_num(span)}")

    state_diff = data.get("state_diff") if isinstance(data.get("state_diff"), dict) else {}
    state_line = ""
    if state_diff:
        state_line = f"🧠 State: {fmt(state_diff.get('zone_status', 'unknown'))} | Ref TF: {fmt(state_diff.get('active_reference_tf', 'unknown'))}"

    lines = []

    # Compact header: price + prev + sub + HTF in one line
    htf_str = clean(data.get('htf_structure', 'unknown')).capitalize()
    lines.append(f"💰 {_format_num(price)} | 🧭 Prev: {prev_trend.capitalize()} | Sub: {clean(current_substructure)} | 🌍 {htf_str}")
    for c in [clean(data.get("htf_structure_comment", ""))]:
        if c:
            lines.append(c)

    # Trend + LTF in one line
    trend_str = clean(data.get('trend_structure', 'unknown')).capitalize()
    ltf_str = clean(data.get('ltf_structure', 'unknown')).capitalize()
    lines.append(f"📈 {trend_str} | 🧩 LTF: {ltf_str}")
    for c in [clean(data.get("trend_structure_comment", "")), clean(data.get("ltf_structure_comment", ""))]:
        if c:
            lines.append(c)

    # Accum + Wave + ABC in one line
    accum_str = clean(data.get("accumulation_state", "unknown")).capitalize()
    wave_str = clean(data.get("wave_phase", "unclear")).replace('_', ' ').capitalize()
    abc_str = clean(data.get("abc_risk", "unknown")).replace('_', ' ').capitalize()
    lines.append(f"🧩 Accum: {accum_str} | 🌊 {wave_str} | ⚠️ ABC: {abc_str}")
    for c in [clean(data.get("accumulation_state_comment", "")), clean(data.get("wave_phase_comment", "")), clean(data.get("abc_risk_comment", ""))]:
        if c:
            lines.append(c)

    # Key zones
    lines.append(f"📏 R={_format_num(key_zones.get('resistance'))} | S={_format_num(key_zones.get('support'))}")
    kz_comment = clean(data.get("key_zones_comment", ""))
    if kz_comment:
        lines.append(kz_comment)

    # TF Zones (keep — SMC nesting visibility)
    lines.append("📦 Зоны:")
    if tf_block:
        for x in tf_block:
            lines.append("  " + x)
    else:
        lines.append("  • Нет")

    # T15: FVG / Imbalance zones — compact, only unfilled or in current price zone
    # TF-приоритет (user 17.07.2026): H4+D1=primary, H1=info, M15=excluded
    fvg_primary: list[str] = []
    fvg_info: list[str] = []
    tfs_ctx = {}
    zctx = data.get("zigzag_context")
    if isinstance(zctx, dict):
        tfs_ctx = zctx.get("timeframes") or {}
    for tf, tf_data in tfs_ctx.items():
        if not isinstance(tf_data, dict):
            continue
        # M15 исключён (микро-гэпы, не структурные). 5M не используется (шумный, вне дефолтных TF).
        tf_l = tf.lower()
        if tf_l == "15m":
            continue
        imb = tf_data.get("imbalances")
        if not isinstance(imb, dict):
            continue
        for fvg in (imb.get("fvgs") or []):
            if not isinstance(fvg, dict):
                continue
            # Только незаполненные или в зоне текущей цены
            if not fvg.get("filled") or fvg.get("current_price_in_zone"):
                status = "✅" if fvg.get("filled") else f"fill={int((fvg.get('fill_pct') or 0) * 100)}%"
                in_zone = " ⚡" if fvg.get("current_price_in_zone") else ""
                fvg_entry = (
                    f"  • {_zone_label(tf)}: FVG {fvg.get('type','?')} "
                    f"[{_format_num(fvg.get('low'))}-{_format_num(fvg.get('high'))}] "
                    f"atr={fvg.get('gap_size_atr','?')} {status}{in_zone}"
                )
                if tf_l in ("1d", "4h"):
                    fvg_primary.append(fvg_entry)
                else:  # 1h — info
                    fvg_info.append(fvg_entry)
    if fvg_primary:
        lines.append("⚡ FVG [H4/D1]:")
        lines.extend(fvg_primary)
    if fvg_info:
        lines.append("ℹ️ FVG [H1 info]:")
        lines.extend(fvg_info)

    # State
    if state_line:
        lines.append(state_line)

    # Signal: compact when no signal, full risk mgmt + BE when signal
    has_signal = psl is not None or asl is not None
    lines.append(f"🚦 {clean(signal_status).replace('_', ' ').capitalize()}")
    sig_comment = clean(data.get("signal_status_comment", ""))
    if sig_comment:
        lines.append(sig_comment)

    # Normalize entry to dict (LLM sometimes returns string)
    if not isinstance(entry, dict):
        entry = {}

    if has_signal:
        # Full risk management
        lines.append(f"⚡ {fmt(entry.get('aggressive'))} | 🛡️ {fmt(entry.get('conservative'))}")
        status_val = fmt(entry.get("current_status"))
        if status_val != "Н/Д":
            lines.append(f"📊 {status_val}")
        ec = clean(data.get("entry_conditions_comment", ""))
        if ec:
            lines.append(ec)
        # Primary risk (only if SL exists)
        if psl is not None:
            lines.append(f"⚖️ SL={_format_num(psl)} | TP1={_format_num(ptp1)} | TP2={_format_num(ptp2)} | TP3={_format_num(ptp3)} | R:R={_format_num(prr)}")
            rc = clean(data.get("risk_management_comment", ""))
            if rc:
                lines.append(rc)
        # Alternative (only if SL exists)
        if asl is not None:
            lines.append(f"🔄 Alt: SL={_format_num(asl)} | TP1={_format_num(atp1)} | TP2={_format_num(atp2)} | R:R={_format_num(arr)}")
            sc = clean(data.get("scenario_status_comment", ""))
            if sc:
                lines.append(sc)
        # Trade management: BE at TP1
        if ptp1 is not None and psl is not None:
            lines.append(f"🔄 BE @ TP1 {_format_num(ptp1)} → SL to entry")
    else:
        # No signal — status only, no Н/Д spam
        status_val = fmt(entry.get("current_status")) if isinstance(entry, dict) else "Н/Д"
        if status_val != "Н/Д":
            lines.append(f"📊 {status_val}")
        ec = clean(data.get("entry_conditions_comment", ""))
        if ec:
            lines.append(ec)

    # Facts + Confidence (always)
    lines.append(f"📝 {clean(data.get('fact_feedback', ''))}")
    lines.append(f"🎯 {clean(data.get('confidence', 'low')).capitalize()} | {clean(data.get('confidence_reason', ''))}")

    # убрать пустые строки и подряд идущие пустые строки
    filtered = []
    for line in lines:
        if line is None:
            continue
        s = str(line).rstrip()
        if not s.strip():
            continue
        filtered.append(s)

    return _cleanup_empty_lines("\n".join(filtered))


def _format_zigzag_context_compact(ctx: dict) -> str:
    """
    Сериализатор ZigZag контекста для промпта LLM.

    При наличии structure.narrative (реальные пивоты + BOS) — использует
    structure narrative вместо абстрактных mode/swing/pos метрик.
    Фоллбэк на старый формат если structure данных нет.
    """
    if not ctx or not isinstance(ctx, dict) or ctx.get("error"):
        return ctx.get("message", "ZigZag недоступен.") if isinstance(ctx, dict) else "{}"

    lines = []

    # Stack summary
    stack = ctx.get("stack") or {}
    if stack:
        lines.append(
            f"Stack: bias={stack.get('stack_bias','?')} align={stack.get('alignment','?')} "
            f"dom={stack.get('dominant_tf','?')} | {' '.join(f'{k}:{v}' for k,v in (stack.get('directions') or {}).items())}"
        )

    # Per-TF: structure narrative (если есть) или legacy формат
    tfs = ctx.get("timeframes") or {}
    has_structure = False
    for tf, data in tfs.items():
        if not isinstance(data, dict):
            continue
        struct = data.get("structure")
        narrative = struct.get("narrative") if isinstance(struct, dict) else None
        if narrative:
            lines.append(narrative)
            has_structure = True
        else:
            # Legacy fallback: mode/swing/pos
            lines.append(
                f"{tf}: mode={data.get('market_mode','?')} swing={data.get('swing_direction','?')} "
                f"pivots={data.get('pivot_count','?')} pos={data.get('price_position','?')}"
            )

    # Если был structure narrative — добавить подсказку для LLM
    if has_structure:
        lines.append(
            "ВАЖНО: Zone выше = структурный range после BOS, НЕ последние 5 свечей."
        )

    # T15: FVG / Imbalance zones (liquidity концепт, не zone_structure)
    # TF-приоритет (user 17.07.2026): H4+D1=primary, H1=info, M15=excluded
    fvg_lines: list[str] = []
    fvg_primary: list[str] = []
    fvg_info: list[str] = []
    for tf, data in tfs.items():
        if not isinstance(data, dict):
            continue
        imb = data.get("imbalances")
        if not isinstance(imb, dict):
            continue
        # M15 исключён (микро-гэпы, не структурные). 5M не используется (шумный, вне дефолтных TF).
        tf_l = tf.lower()
        if tf_l == "15m":
            continue
        fvgs = imb.get("fvgs") or []
        for fvg in fvgs:
            if not isinstance(fvg, dict):
                continue
            # Показываем только незаполненные или в зоне текущей цены
            if not fvg.get("filled") or fvg.get("current_price_in_zone"):
                status = "FILLED" if fvg.get("filled") else f"fill={int((fvg.get('fill_pct') or 0) * 100)}%"
                in_zone = "⚡IN_ZONE" if fvg.get("current_price_in_zone") else ""
                fvg_entry = (
                    f"  • {tf}: FVG {fvg.get('type','?')} [{fvg.get('low','?')}-{fvg.get('high','?')}] "
                    f"age={fvg.get('age_bars','?')} atr={fvg.get('gap_size_atr','?')} {status} {in_zone}".strip()
                )
                if tf_l in ("1d", "4h"):
                    fvg_primary.append(fvg_entry)
                else:  # 1h — info
                    fvg_info.append(fvg_entry)
    if fvg_primary:
        lines.append("FVG (Fair Value Gaps) — PRIMARY [H4, D1]:")
        lines.extend(fvg_primary)
    if fvg_info:
        lines.append("FVG — INFO [H1] (общая информация, НЕ основа для прогноза):")
        lines.extend(fvg_info)
    if fvg_primary or fvg_info:
        lines.append("ВАЖНО: FVG = liquidity зона (vacuum). H4/D1 — серьёзные уровни притяжения. H1 — контекст, не торговый сигнал. M15 исключён.")


    confluence = ctx.get("confluence_levels") or []
    if confluence:
        cf_str = ", ".join(
            f"{c.get('level','?')}({'+'.join(c.get('timeframes',[]))},{c.get('priority','?')})"
            for c in confluence[:6]
        )
        lines.append(f"Confluence: {cf_str}")

    return "\n".join(lines) if lines else "ZigZag недоступен."


async def analyze_multi_images(
    images: List[bytes],
    market_type: str = "crypto",
    prev_analysis: Optional[Dict[str, Any]] = None,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
) -> Dict[str, Any]:
    b64_images = [prepare_image_for_llm(img) for img in images]
    b64_images = [img for img in b64_images if img]

    if not b64_images:
        return {"error": True, "message": "Не удалось обработать изображение."}

    if isinstance(prev_analysis, dict):
        metrics_str = str(prev_analysis.get("metrics") or "Данные недоступны.")
        tf_ctx_str = str(prev_analysis.get("tf_context") or "Один таймфрейм.")
        bt_str = str(prev_analysis.get("backtest") or "Статистика ещё формируется.")
        zigzag_str = _format_zigzag_context_compact(prev_analysis.get("zigzag_context") or {})
        volume_str = json.dumps(prev_analysis.get("volume_context") or {}, ensure_ascii=False, indent=2)
        state_str = json.dumps(prev_analysis.get("state_context") or {}, ensure_ascii=False, indent=2)
        liquidity_str = str(prev_analysis.get("heatmap_context") or "Liquidity heatmap недоступна.")
        multi_str = str(prev_analysis.get("multi_symbol") or "Мульти-символьный контекст: данные недоступны.")
    else:
        metrics_str = str(prev_analysis) or "Данные недоступны."
        tf_ctx_str = "Один таймфрейм."
        bt_str = "Статистика ещё формируется."
        zigzag_str = "{}"
        volume_str = "{}"
        state_str = "{}"
        liquidity_str = "Liquidity heatmap недоступна."
        multi_str = "Мульти-символьный контекст: данные недоступны."

    user_text = PRO_TA_USER_PROMPT.format(
        market_type=market_type,
        metrics=metrics_str,
        tf_context=tf_ctx_str,
        zigzag_context=zigzag_str,
        volume_context=volume_str,
        liquidity_context=liquidity_str,
        state_context=state_str,
        backtest=bt_str,
        multi_symbol=multi_str,
    )

    content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    # Передать ВСЕ графики в LLM (не только b64_images[0]).
    # Раньше LLM видел только 1 картинку (младший ТФ) → не мог вернуть
    # tf_zones для остальных ТФ → fallback подставлял сырые экстремумы.
    for img_b64 in b64_images:
        content_parts.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
        )

    # NOTE: payload больше не формируется здесь — ollama_service.generate()
    # собирает его сам из messages + model + temperature + max_tokens.

    # -----------------------------
    # P2-3: Self-consistency — 2 прогона с голосованием по signal_status
    # -----------------------------
    messages = [
        {"role": "system", "content": _get_system_prompt()},
        {"role": "user", "content": content_parts},
    ]

    # Конфигурация прогонов: небольшая вариация temperature для разнообразия
    RUN_TEMPERATURES = [0.15, 0.25]
    RUN_TIMEOUT_LIMIT = 40  # сек — если первый прогон >40с, второй пропускаем
    RUN_TOTAL = len(RUN_TEMPERATURES)

    async def _single_run(run_idx: int, temp: float) -> dict[str, Any] | None:
        """Один прогон LLM. Возвращает parsed dict или None."""
        run_start = time.monotonic()
        try:
            result = await llm_generate(
                messages=messages,
                model=llm_model or MODEL_NAME,
                temperature=temp,
                max_tokens=2000,
                timeout=45,
                api_key=llm_api_key,
                base_url=llm_base_url,
            )

            raw = result["content"]
            logger.warning(f"RAW LLM OUTPUT (run {run_idx + 1}/{RUN_TOTAL}, temp={temp}):\n{raw}")
            parsed = parse_llm_json(raw)

            if parsed.get("error"):
                logger.warning(
                    "Self-consistency: run %d/%d parse failed: %s",
                    run_idx + 1, RUN_TOTAL, parsed.get("message"),
                )
                return None

            signal = str(parsed.get("signal_status", "no_signal"))
            elapsed = time.monotonic() - run_start
            logger.info(
                "Self-consistency: run %d/%d, signal=%s, temp=%.2f, took=%.1fs",
                run_idx + 1, RUN_TOTAL, signal, temp, elapsed,
            )
            parsed["_run_time"] = elapsed
            return parsed

        except LLMError as e:
            mode_hint = "cloud" if LLM_MODE == "cloud" else "local (LM Studio)"
            logger.warning(
                "Self-consistency: run %d/%d LLM error: %s",
                run_idx + 1, RUN_TOTAL, e,
            )
            return None
        except Exception as e:
            logger.exception("Self-consistency: run %d/%d unexpected error", run_idx + 1, RUN_TOTAL)
            return None

    # P6: Параллельные прогоны через asyncio.gather (~30 сек вместо ~60)
    # Lock НЕ нужен — каждый прогон независимый HTTP запрос к cloud API.
    run_coros = [_single_run(i, t) for i, t in enumerate(RUN_TEMPERATURES)]
    run_results = await asyncio.gather(*run_coros)

    results: list[dict[str, Any]] = [r for r in run_results if r is not None]
    run_times: list[float] = [r.get("_run_time", 0) for r in results]

    # -----------------------------
    # Голосование по signal_status
    # -----------------------------
    if not results:
        return {"error": True, "message": "Both runs failed (no valid LLM output)"}

    signals = [r.get("signal_status", "no_signal") for r in results]
    winner_signal = Counter(signals).most_common(1)[0][0]
    agreed = len(set(signals)) == 1

    if not agreed:
        logger.warning(
            "Self-consistency disagreement: %s vs %s — taking first (temp=%.2f, more deterministic)",
            signals[0] if len(signals) > 0 else "N/A",
            signals[1] if len(signals) > 1 else "N/A",
            RUN_TEMPERATURES[0],
        )
        # Берём первый (более детерминированный, temperature=0.15)
        final = results[0]
    else:
        # Консенсус — берём первый (или любой, они одинаковы по signal)
        final = results[0]

    # Пробрасываем liquidity_pools из prev_analysis в data для enforce_risk_rules
    if isinstance(prev_analysis, dict) and prev_analysis.get("liquidity_pools"):
        final["liquidity_pools"] = prev_analysis["liquidity_pools"]

    # Пробрасываем zigzag_context из prev_analysis в data для enforce_risk_rules.
    # LLM output не содержит zigzag_context — он был в промпте (prev_analysis).
    # Без этого _detect_contamination не видит per-TF ZigZag zones и не фиксит контаминацию.
    if isinstance(prev_analysis, dict) and prev_analysis.get("zigzag_context"):
        final["zigzag_context"] = prev_analysis["zigzag_context"]

    final = enforce_risk_rules(final)
    final["error"] = False

    # Метаданные self-consistency
    final["_consistency"] = {
        "runs": len(results),
        "signals": signals,
        "agreed": agreed,
        "temperatures": RUN_TEMPERATURES[:len(results)],
        "run_times_sec": [round(t, 2) for t in run_times],
    }

    return final