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

from core.config import LOCAL_AI_ENDPOINT, MODEL_NAME, LLM_MODE, LLM_API_KEY
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
  "tf_zones": {
    "15m": { "upper": 63950.0, "lower": 63820.0 },
    "1h": { "upper": 64100.0, "lower": 63750.0 },
    "4h": { "upper": 64245.0, "lower": 63640.0 },
    "1D": { "upper": 64680.0, "lower": 61929.0 }
  },
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
  "tf_zones": {
    "15m": { "upper": 64400.0, "lower": 64200.0 },
    "1h": { "upper": 64500.0, "lower": 64100.0 },
    "4h": { "upper": 65000.0, "lower": 64245.0 },
    "1D": { "upper": 66000.0, "lower": 63640.0 }
  },
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

  "tf_zones": {{
    "<ТФ>": {{ "upper": <number|null>, "lower": <number|null> }}
  }},
  "tf_zones_comment": "краткий комментарий",

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
8. tf_zones и key_zones не пересчитывай — бери из контекста.
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
20. false_breakout только при выходе за границу с возвратом внутрь и подтверждением.
21. Если есть реакция у уровня без явного выхода и возврата, не ставь false_breakout — используй retest, reversal или no_signal.
22. При споре между false_breakout и retest выбирай retest.
23. Диапазоны ТФ иерархические: 15m → 1H → 4H → 1D.
24. Младший пробой не означает старший пробой.
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

        # Нормализация tf_zones
        if isinstance(data.get("tf_zones"), dict):
            normalized = {}
            for k, v in data["tf_zones"].items():
                tf_key = str(k).strip().upper().replace("MIN", "M")
                if isinstance(v, dict):
                    normalized[tf_key] = {
                        "upper": _safe_float(v.get("upper")),
                        "lower": _safe_float(v.get("lower")),
                    }
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


# TF hierarchy for TP1 validation: next TF up from entry TF
_TF_HIERARCHY = ["15m", "1h", "4h", "1d"]


def _next_tf_boundary(data: dict, direction: str, current_price: float, side: str) -> float | None:
    """Find nearest HTF zone boundary in trade direction.
    side='upper' for long TP, 'lower' for short TP.
    HTF = next TF up from entry TF in _TF_HIERARCHY.
    Returns None if no boundary found.
    """
    tf_zones = data.get("tf_zones") or {}
    if not isinstance(tf_zones, dict):
        return None

    # Determine entry TF from tf_span_map or default to 15m
    tf_span_map = data.get("tf_span_map") or {}
    entry_tf = "15m"
    if isinstance(tf_span_map, dict) and tf_span_map:
        # smallest TF in span = entry TF
        for tf in _TF_HIERARCHY:
            if tf in tf_span_map or tf.upper() in tf_span_map:
                entry_tf = tf
                break

    # Next TF up
    try:
        idx = _TF_HIERARCHY.index(entry_tf.lower())
    except ValueError:
        idx = 0
    htf = _TF_HIERARCHY[idx + 1] if idx + 1 < len(_TF_HIERARCHY) else None
    if htf is None:
        return None

    # Try HTF zone, then fall back to progressively higher TFs
    for i in range(idx + 1, len(_TF_HIERARCHY)):
        tf_key = _TF_HIERARCHY[i]
        # tf_zones keys may be upper or lower
        zone = tf_zones.get(tf_key) or tf_zones.get(tf_key.upper())
        if not isinstance(zone, dict):
            continue
        boundary = None
        if side == "upper":
            boundary = zone.get("upper") or zone.get("resistance")
        else:  # lower
            boundary = zone.get("lower") or zone.get("support")
        if boundary is not None:
            try:
                b = float(boundary)
                # Long: boundary must be > current_price; Short: boundary < current_price
                if direction == "long" and b > current_price:
                    return b
                elif direction == "short" and b < current_price:
                    return b
            except (TypeError, ValueError):
                continue

    return None


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
            upper = _safe_float(z.get("upper"))
            lower = _safe_float(z.get("lower"))

            if upper is None and lower is None:
                continue
            if upper is not None and lower is not None and lower > upper:
                lower, upper = upper, lower

            normalized[tf_key] = {
                "upper": upper,
                "lower": lower,
            }

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
            out.append({
                "level": _safe_float(item.get("level")),
                "timeframes": [str(tf).upper() for tf in timeframes if str(tf).strip()],
                "priority": str(item.get("priority", "low")).lower(),
                "count": int(item.get("count") or 0),
                "spread": _safe_float(item.get("spread")),
                "kind": str(item.get("kind", "mixed")).lower(),
            })
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
        if entry_price is None or not candidates:
            return None, None, None

        direction = (direction or "").lower().strip()
        if direction not in ("long", "short"):
            return None, None, None

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

        if "abc вниз" in wave_comment or "abc-коррекции вниз" in wave_comment:
            src["abc_risk"] = "abc_risk_down"
            if not src.get("abc_risk_comment"):
                src["abc_risk_comment"] = "Риск ABC вниз по волновой фазе"
        elif "abc вверх" in wave_comment or "abc-коррекции вверх" in wave_comment:
            src["abc_risk"] = "abc_risk_up"
            if not src.get("abc_risk_comment"):
                src["abc_risk_comment"] = "Риск ABC вверх по волновой фазе"

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
            merged_tf_zones[tf_key] = {"upper": upper, "lower": lower}

        # merge only exact-close ranges, keep tf names
        zones_by_span = {}
        for tf, z in merged_tf_zones.items():
            key = (round(z["lower"] or 0, 2), round(z["upper"] or 0, 2))
            zones_by_span.setdefault(key, []).append(tf)

        compact_tf_zones = {}
        for (lo, hi), tfs in zones_by_span.items():
            label = "/".join(sorted({_zone_label(tf) for tf in tfs}))
            compact_tf_zones[label] = {"upper": hi, "lower": lo}

        data["tf_zones"] = compact_tf_zones

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

    # CRITICAL: V4 direction propagation.
    # enforce_risk_rules вычисляет direction_hint через _direction_from_data,
    # но без этой записи position_tracker берёт LLM signal_direction (часто пустое)
    # → открывает позицию в wrong direction → SL/TP инвертированы.
    data["signal_direction"] = direction_hint

    # -----------------------------
    # 10) TP/SL для primary
    # -----------------------------
    primary = rm.get("primary")
    if not isinstance(primary, dict):
        primary = _empty_risk()
        rm["primary"] = primary

    if current_price is not None and candidates and data.get("signal_status") in ("aggressive_breakout", "retest", "reversal", "false_breakout"):
        tp1, tp2, tp3 = _pick_tp_levels(direction_hint, current_price, candidates)

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

        if primary.get("sl") is None:
            if direction_hint == "long":
                below = [x for x in candidates if x < current_price]
                primary["sl"] = below[-1] if below else (min(candidates) if candidates else None)
            else:
                above = [x for x in candidates if x > current_price]
                primary["sl"] = above[0] if above else (max(candidates) if candidates else None)

        if direction_hint == "long":
            for k in ("tp1", "tp2", "tp3"):
                tp_val = _safe_float(rm["primary"].get(k))
                if tp_val is not None and current_price is not None and tp_val <= current_price:
                    rm["primary"][k] = None

            sl_val = _safe_float(rm["primary"].get("sl"))
            if sl_val is not None and current_price is not None and sl_val >= current_price:
                below = [x for x in candidates if current_price is not None and x < current_price]
                rm["primary"]["sl"] = below[-1] if below else (min(candidates) if candidates else None)
            else:
                for k in ("tp1", "tp2", "tp3"):
                    tp_val = _safe_float(rm["primary"].get(k))
                    if tp_val is not None and current_price is not None and tp_val >= current_price:
                        rm["primary"][k] = None

                sl_val = _safe_float(rm["primary"].get("sl"))
                if sl_val is not None and current_price is not None and sl_val <= current_price:
                    above = [x for x in candidates if current_price is not None and x > current_price]
                    rm["primary"]["sl"] = above[0] if above else (max(candidates) if candidates else None)

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
            if alt_direction == "long":
                below = [x for x in candidates if x < current_price]
                alternative["sl"] = below[-1] if below else (min(candidates) if candidates else None)
            else:
                above = [x for x in candidates if x > current_price]
                alternative["sl"] = above[0] if above else (max(candidates) if candidates else None)

        if alt_direction == "long":
            for k in ("tp1", "tp2", "tp3"):
                tp_val = _safe_float(rm["alternative"].get(k))
                if tp_val is not None and current_price is not None and tp_val <= current_price:
                    rm["alternative"][k] = None

            sl_val = _safe_float(rm["alternative"].get("sl"))
            if sl_val is not None and current_price is not None and sl_val >= current_price:
                below = [x for x in candidates if current_price is not None and x < current_price]
                rm["alternative"]["sl"] = below[-1] if below else (min(candidates) if candidates else None)
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
    # 12.3b) ATR-based SL floor
    # Z proposal: SL = max(structural_SL, entry ± 1.5×ATR15m)
    # Защита от tight BOS stops (7 пунктов = noise)
    # -----------------------------
    atr_15m = _safe_float(data.get("atr_15m"))
    sl_current = _safe_float(primary.get("sl"))
    if atr_15m is not None and atr_15m > 0 and sl_current is not None and current_price is not None:
        sl_floor_dist = 1.5 * atr_15m
        if direction_hint == "long":
            sl_floor = current_price - sl_floor_dist
            if sl_floor < sl_current:  # floor дальше от entry → забирает
                primary["sl"] = round(sl_floor, 6)
                primary["sl_source"] = "atr_floor"
                logger.info("SL ATR floor (long): BOS sl=%.4f → ATR floor=%.4f (atr=%.4f, 1.5×atr=%.4f)",
                            sl_current, sl_floor, atr_15m, sl_floor_dist)
            else:
                primary["sl_source"] = "structural"
        elif direction_hint == "short":
            sl_floor = current_price + sl_floor_dist
            if sl_floor > sl_current:  # floor дальше от entry → забирает
                primary["sl"] = round(sl_floor, 6)
                primary["sl_source"] = "atr_floor"
                logger.info("SL ATR floor (short): BOS sl=%.4f → ATR floor=%.4f (atr=%.4f, 1.5×atr=%.4f)",
                            sl_current, sl_floor, atr_15m, sl_floor_dist)
            else:
                primary["sl_source"] = "structural"

    # Recalc RR after SL floor
    primary["rr"] = _calc_rr(
        current_price,
        _safe_float(primary.get("sl")),
        _safe_float(primary.get("tp1")),
    )

    # -----------------------------
    # 12.3c) TP1 validation: min(2×risk, next-TF zone boundary)
    # Z proposal: TP1 = min(forced_2R, HTF boundary) — не улетает в космос
    # HTF = следующий TF по иерархии от entry TF
    # -----------------------------
    sl_final = _safe_float(primary.get("sl"))
    tp1_current = _safe_float(primary.get("tp1"))
    if sl_final is not None and tp1_current is not None and current_price is not None:
        risk = abs(current_price - sl_final)
        if risk > 0:
            # forced TP1 = entry + 2×risk
            if direction_hint == "long":
                tp1_forced = current_price + 2.0 * risk
                # Если forced TP1 дальше HTF boundary → урезать
                tp1_htf = _next_tf_boundary(data, direction_hint, current_price, "upper")
                if tp1_htf is not None and tp1_forced > tp1_htf:
                    primary["tp1"] = round(tp1_htf, 6)
                    primary["tp1_source"] = "htf_boundary"
                    logger.info("TP1 HTF boundary (long): forced=%.4f → htf=%.4f (risk=%.4f)",
                                tp1_forced, tp1_htf, risk)
                else:
                    primary["tp1"] = round(tp1_forced, 6)
                    primary["tp1_source"] = "forced_rr"
            elif direction_hint == "short":
                tp1_forced = current_price - 2.0 * risk
                tp1_htf = _next_tf_boundary(data, direction_hint, current_price, "lower")
                if tp1_htf is not None and tp1_forced < tp1_htf:
                    primary["tp1"] = round(tp1_htf, 6)
                    primary["tp1_source"] = "htf_boundary"
                    logger.info("TP1 HTF boundary (short): forced=%.4f → htf=%.4f (risk=%.4f)",
                                tp1_forced, tp1_htf, risk)
                else:
                    primary["tp1"] = round(tp1_forced, 6)
                    primary["tp1_source"] = "forced_rr"

    # Recalc RR after TP1 validation
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
            if rr is not None and rr < 1.0:
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
        seen = set()
        for tf, z in ordered:
            upper = z.get("upper")
            lower = z.get("lower")
            key = (round(float(lower), 4) if lower is not None else None, round(float(upper), 4) if upper is not None else None)
            if key in seen:
                continue
            seen.add(key)
            tf_block.append(f"• {tf}: [{_format_num(lower)} - {_format_num(upper)}]")
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
                confluence_text.append(
                    f"• {_format_num(lvl)} | TF: {fmt(tfs)} | {pr} | spread={_format_num(sp)} | {kind}"
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

    lines.append(f"💰 Цена: {_format_num(price)}")
    lines.append(f"🧭 Prev trend: {prev_trend.capitalize()}")
    lines.append(f"🧭 Current substructure: {clean(current_substructure)}")
    lines.append(f"🌍 HTF структура: {clean(data.get('htf_structure', 'unknown')).capitalize()}")
    lines.append(clean(data.get("htf_structure_comment", "")))
    lines.append(f"📈 Тренд: {clean(data.get('trend_structure', 'unknown')).capitalize()}")
    lines.append(clean(data.get("trend_structure_comment", "")))
    lines.append(f"🧩 LTF структура: {clean(data.get('ltf_structure', 'unknown')).capitalize()}")
    lines.append(clean(data.get("ltf_structure_comment", "")))
    lines.append(f"🧩 Накопление/распределение: {clean(data.get('accumulation_state', 'unknown')).capitalize()}")
    lines.append(clean(data.get("accumulation_state_comment", "")))
    lines.append(f"🌊 Волновая фаза: {clean(data.get('wave_phase', 'unclear')).replace('_', ' ').capitalize()}")
    lines.append(clean(data.get("wave_phase_comment", "")))
    lines.append(f"⚠️ ABC риск: {clean(data.get('abc_risk', 'unknown')).replace('_', ' ').capitalize()}")
    lines.append(clean(data.get("abc_risk_comment", "")))
    lines.append(f"📏 Зоны: R={_format_num(key_zones.get('resistance'))} | S={_format_num(key_zones.get('support'))}")
    lines.append(clean(data.get("key_zones_comment", "")))

    lines.append("📦 ЗОНЫ ПО ТФ:")
    if tf_block:
        for x in tf_block:
            lines.append("  " + x)
    else:
        lines.append("  • Нет")

    lines.append("📐 Confluence:")
    if confluence_text:
        for x in confluence_text:
            lines.append("  " + x)
    else:
        lines.append("  • Нет")

    lines.append("📏 TF span map:")
    if tf_span_text:
        for x in tf_span_text:
            lines.append("  " + x)
    else:
        lines.append("  • Нет")

    if state_line:
        lines.append(state_line)

    lines.append(f"🚦 Сигнал: {clean(signal_status).replace('_', ' ').capitalize()}")
    lines.append(clean(data.get("signal_status_comment", "")))
    lines.append(f"⚡ Агрессивный: {fmt(entry.get('aggressive'))}")
    lines.append(f"🛡️ Консервативный: {fmt(entry.get('conservative'))}")
    lines.append(f"📊 Статус: {fmt(entry.get('current_status'))}")
    lines.append(clean(data.get("entry_conditions_comment", "")))
    lines.append(f"⚖️ Основной риск: SL={_format_num(psl)} | TP1={_format_num(ptp1)} | TP2={_format_num(ptp2)} | TP3={_format_num(ptp3)} | R:R={_format_num(prr)}")
    lines.append(clean(data.get("risk_management_comment", "")))
    lines.append(f"⚖️ Альтернатива: SL={_format_num(asl)} | TP1={_format_num(atp1)} | TP2={_format_num(atp2)} | TP3={_format_num(atp3)} | R:R={_format_num(arr)}")
    lines.append(clean(data.get("scenario_status_comment", "")))
    lines.append(f"📝 Факты: {clean(data.get('fact_feedback', ''))}")
    lines.append(f"🎯 Уверенность: {clean(data.get('confidence', 'low')).capitalize()} | {clean(data.get('confidence_reason', ''))}")

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
    Компактный текстовый сериализатор ZigZag контекста для промпта LLM.
    ~700 chars вместо 3584 chars сырого JSON — меньше токенов, больше сигнала.
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

    # Per-TF zones
    tfs = ctx.get("timeframes") or {}
    for tf, data in tfs.items():
        if not isinstance(data, dict):
            continue
        lines.append(
            f"{tf}: [{data.get('lower','?')}–{data.get('upper','?')}] "
            f"mode={data.get('market_mode','?')} swing={data.get('swing_direction','?')} "
            f"pivots={data.get('pivot_count','?')} pos={data.get('price_position','?')}"
        )

    # Confluence levels
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
    prev_analysis: Optional[Dict[str, Any]] = None
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
    else:
        metrics_str = str(prev_analysis) or "Данные недоступны."
        tf_ctx_str = "Один таймфрейм."
        bt_str = "Статистика ещё формируется."
        zigzag_str = "{}"
        volume_str = "{}"
        state_str = "{}"
        liquidity_str = "Liquidity heatmap недоступна."

    user_text = PRO_TA_USER_PROMPT.format(
        market_type=market_type,
        metrics=metrics_str,
        tf_context=tf_ctx_str,
        zigzag_context=zigzag_str,
        volume_context=volume_str,
        liquidity_context=liquidity_str,
        state_context=state_str,
        backtest=bt_str,
    )

    content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    content_parts.append(
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_images[0]}"}}
    )

    # NOTE: payload больше не формируется здесь — ollama_service.generate()
    # собирает его сам из messages + model + temperature + max_tokens.

    # -----------------------------
    # P2-3: Self-consistency — 2 прогона с голосованием по signal_status
    # -----------------------------
    messages = [
        {"role": "system", "content": PRO_TA_SYSTEM_PROMPT},
        {"role": "user", "content": content_parts},
    ]

    # Конфигурация прогонов: небольшая вариация temperature для разнообразия
    RUN_TEMPERATURES = [0.15, 0.25]
    RUN_TIMEOUT_LIMIT = 40  # сек — если первый прогон >40с, второй пропускаем
    RUN_TOTAL = len(RUN_TEMPERATURES)

    results: list[dict[str, Any]] = []
    run_signals: list[str] = []
    run_times: list[float] = []

    for run_idx, temp in enumerate(RUN_TEMPERATURES):
        run_start = time.monotonic()

        # Защита от таймаута: если первый прогон занял >40с — второй пропускаем
        if run_idx > 0 and run_times[-1] > RUN_TIMEOUT_LIMIT:
            logger.info(
                "Self-consistency: skip run %d/%d (prev run took %.1fs > %ds limit)",
                run_idx + 1, RUN_TOTAL, run_times[-1], RUN_TIMEOUT_LIMIT,
            )
            break

        try:
            async with LLM_QUEUE_LOCK:
                result = await llm_generate(
                    messages=messages,
                    model=MODEL_NAME,
                    temperature=temp,
                    max_tokens=2000,
                    timeout=45,
                )

            raw = result["content"]
            logger.warning(f"RAW LLM OUTPUT (run {run_idx + 1}/{RUN_TOTAL}, temp={temp}):\n{raw}")
            parsed = parse_llm_json(raw)

            if parsed.get("error"):
                logger.warning(
                    "Self-consistency: run %d/%d parse failed: %s",
                    run_idx + 1, RUN_TOTAL, parsed.get("message"),
                )
                run_times.append(time.monotonic() - run_start)
                continue

            signal = str(parsed.get("signal_status", "no_signal"))
            logger.info(
                "Self-consistency: run %d/%d, signal=%s, temp=%.2f",
                run_idx + 1, RUN_TOTAL, signal, temp,
            )

            results.append(parsed)
            run_signals.append(signal)
            run_times.append(time.monotonic() - run_start)

        except LLMError as e:
            mode_hint = "cloud" if LLM_MODE == "cloud" else "local (LM Studio)"
            logger.warning(
                "Self-consistency: run %d/%d LLM error: %s",
                run_idx + 1, RUN_TOTAL, e,
            )
            run_times.append(time.monotonic() - run_start)
            # Продолжаем к следующему прогону — второй может сработать
            continue
        except Exception as e:
            logger.exception("Self-consistency: run %d/%d unexpected error", run_idx + 1, RUN_TOTAL)
            run_times.append(time.monotonic() - run_start)
            continue

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