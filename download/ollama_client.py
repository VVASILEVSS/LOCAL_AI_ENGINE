import io
import base64
import json
import re
import asyncio
import httpx
from PIL import Image
import logging
from typing import List, Optional, Dict, Any, Tuple

from core.config import LOCAL_AI_ENDPOINT, MODEL_NAME
from core.divergence_context import get_multi_context, get_multi_symbol_context

import os
import pickle
import numpy as np
import pandas as pd

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

State / history context:
{state_context}

Исторические дивергенции A/D:
{divergence_context}

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

    # -----------------------------
    # 15) ML FILTER — Phase 1 LOG-ONLY
    # -----------------------------
    signal_status_ml = str(data.get("signal_status", "")).lower()
    ml_should_run = (
        signal_status_ml.startswith(("false_breakout", "aggressive_breakout", "retest", "reversal"))
        and signal_status_ml not in ("no_signal", "accumulation", "unknown", "")
    )

    if ml_should_run:
        ml_log_lines = []
        ml_log_lines.append(f"ML FILTER [PHASE1 LOG-ONLY] signal_status={signal_status_ml}")

        try:
            model_path = os.path.join(os.path.dirname(__file__), "..", "results", "model.pkl")
            if os.path.isfile(model_path):
                with open(model_path, "rb") as f:
                    ml_pipeline = pickle.load(f)

                # Pipeline stores feature_names_in_ after .fit() with DataFrame
                if hasattr(ml_pipeline, 'feature_names_in_'):
                    expected_features = list(ml_pipeline.feature_names_in_)
                else:
                    clf_step = ml_pipeline.steps[-1][1]
                    if hasattr(clf_step, 'feature_names_in_'):
                        expected_features = list(clf_step.feature_names_in_)
                    elif hasattr(clf_step, 'n_features_in_'):
                        expected_features = list(range(clf_step.n_features_in_))
                    else:
                        expected_features = []

                clf_step = ml_pipeline.steps[-1][1]
                ml_log_lines.append(f"  model loaded: {type(clf_step).__name__}, features={len(expected_features)}")

                # --- Build context JSON from divergence_context_raw or OHLCV fallback ---
                context_dict = {}
                raw_ctx = data.get("divergence_context_raw")

                if raw_ctx and isinstance(raw_ctx, (dict, str)):
                    if isinstance(raw_ctx, str):
                        try:
                            context_dict = json.loads(raw_ctx)
                        except (json.JSONDecodeError, TypeError):
                            context_dict = {}
                    else:
                        context_dict = raw_ctx
                    ml_log_lines.append(f"  context from divergence_context_raw ({len(context_dict)} keys)")
                else:
                    # OHLCV fallback: build context from last 21 bars
                    ml_log_lines.append("  divergence_context_raw empty, trying OHLCV fallback...")
                    symbol_ml = str(data.get("symbol") or data.get("symbol_id") or "").strip()
                    ref_tf = str(data.get("active_reference_tf") or "4h").strip()

                    if symbol_ml:
                        try:
                            from core.data_provider import OhlcvDataProvider
                            dp = OhlcvDataProvider()
                            csv_paths = dp.refresh_many([symbol_ml], [ref_tf], limit=21, force_refresh=False)
                            ml_log_lines.append(f"  OHLCV paths returned: {csv_paths}")

                            if csv_paths:
                                import csv as csv_mod
                                csv_file = csv_paths[0]
                                if os.path.isfile(csv_file):
                                    bars_df = pd.read_csv(csv_file, nrows=21)
                                    if not bars_df.empty:
                                        # Build context dict with same keys as divergence context
                                        close_col = [c for c in bars_df.columns if "close" in c.lower()]
                                        high_col = [c for c in bars_df.columns if "high" in c.lower()]
                                        low_col = [c for c in bars_df.columns if "low" in c.lower()]
                                        vol_col = [c for c in bars_df.columns if "volume" in c.lower()]
                                        open_col = [c for c in bars_df.columns if "open" in c.lower()]

                                        def _col(columns, fallback="close"):
                                            return columns[0] if columns else fallback

                                        closes = bars_df[_col(close_col)].values
                                        highs = bars_df[_col(high_col)].values
                                        lows = bars_df[_col(low_col)].values
                                        volumes = bars_df[_col(vol_col, "volume")].values
                                        opens = bars_df[_col(open_col, "open")].values

                                        if len(closes) >= 2:
                                            pct_changes = pd.Series(closes).pct_change().dropna().values
                                            high_low_ranges = highs - lows
                                            atr_proxy = np.mean(high_low_ranges[-14:]) if len(high_low_ranges) >= 14 else np.mean(high_low_ranges)

                                            # Momentum features
                                            roc_5 = ((closes[-1] / closes[-6]) - 1) * 100 if len(closes) >= 6 else 0
                                            roc_10 = ((closes[-1] / closes[-11]) - 1) * 100 if len(closes) >= 11 else 0

                                            # Volatility features
                                            volatility_ratio = (np.std(pct_changes[-5:]) / (np.std(pct_changes[-14:]) + 1e-9)) if len(pct_changes) >= 14 else 1.0
                                            bb_width = (np.mean(highs[-20:]) - np.mean(lows[-20:])) / (np.mean(closes[-20:]) + 1e-9) if len(closes) >= 20 else 0

                                            # Volume features
                                            vol_mean = np.mean(volumes[-14:]) if len(volumes) >= 14 else np.mean(volumes)
                                            vol_latest = volumes[-1]
                                            vol_ratio = vol_latest / (vol_mean + 1e-9)
                                            vol_trend = np.mean(volumes[-5:]) / (np.mean(volumes[-14:]) + 1e-9) if len(volumes) >= 14 else 1.0

                                            # Price action features
                                            body_ratio = abs(closes[-1] - opens[-1]) / (highs[-1] - lows[-1] + 1e-9)
                                            upper_wick = highs[-1] - max(opens[-1], closes[-1])
                                            lower_wick = min(opens[-1], closes[-1]) - lows[-1]
                                            candle_range = highs[-1] - lows[-1]

                                            # Trend features
                                            sma_5 = np.mean(closes[-5:]) if len(closes) >= 5 else closes[-1]
                                            sma_10 = np.mean(closes[-10:]) if len(closes) >= 10 else closes[-1]
                                            sma_20 = np.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
                                            price_vs_sma5 = ((closes[-1] / sma_5) - 1) * 100
                                            price_vs_sma20 = ((closes[-1] / sma_20) - 1) * 100

                                            # Build FLAT feature dict matching model feature names
                                            vol_std_val = float(np.std(volumes[-14:])) if len(volumes) >= 14 else float(np.std(volumes))
                                            returns_arr = pct_changes if len(pct_changes) > 0 else np.array([0.0])

                                            # TF one-hot encoding
                                            tf_map = {"15m": 1, "1h": 2, "4h": 3, "1d": 4}
                                            tf_ord = tf_map.get(ref_tf.lower(), 3)

                                            context_dict = {
                                                "atr": float(atr_proxy),
                                                "atr_pct": float(atr_proxy / (closes[-1] + 1e-9) * 100),
                                                "bb_width": float(bb_width * 100),
                                                "body_ratio": float(body_ratio),
                                                "roc_5": float(roc_5),
                                                "row_atr_pct": float(atr_proxy / (closes[-1] + 1e-9) * 100),
                                                "vol_avg": float(vol_mean),
                                                "vol_std": vol_std_val,
                                                "vol_std_pct": float(vol_std_val / (closes[-1] + 1e-9) * 100),
                                                "vol_trend": float(vol_trend),
                                                "vol_ratio_row": float(vol_ratio),
                                                "volatility_pct": float(bb_width * 100),
                                                "return_last": float(pct_changes[-1]) if len(pct_changes) > 0 else 0.0,
                                                "return_mean": float(np.mean(returns_arr)),
                                                "rsi": float(50 + np.clip(np.mean(pct_changes[-14:]) * 1000, -50, 50)) if len(pct_changes) >= 3 else 50.0,
                                                "wick_lower": float(lower_wick / (candle_range + 1e-9) * 100),
                                                "wick_upper": float(upper_wick / (candle_range + 1e-9) * 100),
                                                "close_position": float((closes[-1] - lows[-1]) / (highs[-1] - lows[-1] + 1e-9)),
                                                "price_range_pct": float(candle_range / (closes[-1] + 1e-9) * 100),
                                                "ema_slope_pct": float(price_vs_sma5),
                                                "tf_15m": 1.0 if ref_tf.lower() == "15m" else 0.0,
                                                "tf_1h": 1.0 if ref_tf.lower() == "1h" else 0.0,
                                                "tf_4h": 1.0 if ref_tf.lower() == "4h" else 0.0,
                                                "tf_1d": 1.0 if ref_tf.lower() == "1d" else 0.0,
                                                "tf_ordinal": float(tf_ord),
                                                "vol_median_ratio": float(vol_latest / (np.median(volumes[-14:]) + 1e-9)) if len(volumes) >= 14 else 1.0,
                                                "vol_ratio_entry": float(vol_latest / (vol_mean + 1e-9)),
                                                "is_bear": 1.0 if closes[-1] < opens[-1] else 0.0,
                                            }
                                            ml_log_lines.append(f"  OHLCV fallback built {len(context_dict)} features matching model")
                        except Exception as e:
                            ml_log_lines.append(f"  OHLCV fallback FAILED: {type(e).__name__}: {e}")
                    else:
                        ml_log_lines.append("  OHLCV fallback SKIPPED: symbol empty in data")

                # --- Flatten context dict to feature vector ---
                def _flatten(d, prefix="", sep="_"):
                    items = {}
                    for k, v in (d.items() if isinstance(d, dict) else []):
                        new_key = f"{prefix}{sep}{k}" if prefix else str(k)
                        if isinstance(v, dict):
                            items.update(_flatten(v, new_key, sep))
                        elif isinstance(v, (int, float)):
                            items[new_key] = float(v)
                    return items

                flat = _flatten(context_dict)

                if expected_features and len(flat) > 0:
                    # Build feature row matching expected feature order
                    feature_row = {}
                    for feat in expected_features:
                        if feat in flat:
                            feature_row[feat] = flat[feat]
                        elif feat in flat:
                            feature_row[feat] = flat[feat]
                        else:
                            feature_row[feat] = 0.0

                    X_ml = pd.DataFrame([feature_row])[expected_features]
                    proba = ml_pipeline.predict_proba(X_ml)[0]
                    confidence = float(proba[1])  # P(class=1 = good signal)

                    ml_log_lines.append(f"  P(good)={confidence:.3f} | threshold=0.75")

                    # Store ML results in data dict for TG formatting
                    data["ml_confidence"] = round(confidence, 4)
                    data["ml_prediction"] = int(1 if confidence >= 0.75 else 0)
                    data["ml_model_type"] = type(clf_step).__name__
                    data["ml_features_used"] = len(expected_features)
                    data["ml_features_matched"] = sum(1 for f in expected_features if f in flat)
                    data["ml_phase"] = "PHASE1_LOG_ONLY"

                    # Phase 1: LOG ONLY — do NOT block signal
                    # To enable active filtering in Phase 2, uncomment:
                    # if confidence < 0.75:
                    #     data["signal_status"] = "no_signal"
                    #     data["ml_blocked"] = True

                elif not expected_features:
                    ml_log_lines.append("  model has no feature_names_in_ — cannot predict")
                else:
                    ml_log_lines.append(f"  no context features extracted (flat={len(flat)} keys)")
            else:
                ml_log_lines.append(f"  model.pkl NOT FOUND at {model_path}")
        except Exception as e:
            ml_log_lines.append(f"  ML filter ERROR: {type(e).__name__}: {e}")
            import traceback
            ml_log_lines.append(traceback.format_exc()[-500:])

        for ml_line in ml_log_lines:
            logger.info(ml_line)

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

    # ML Filter block (Phase 1 LOG-ONLY)
    ml_confidence = data.get("ml_confidence")
    if ml_confidence is not None:
        ml_pred = data.get("ml_prediction", 0)
        ml_model = data.get("ml_model_type", "unknown")
        ml_matched = data.get("ml_features_matched", 0)
        ml_used = data.get("ml_features_used", 0)
        verdict = "PASS" if ml_pred == 1 else "FILTERED"
        lines.append(f"🤖 ML Filter [{data.get('ml_phase', 'PHASE1')}]: {verdict} | confidence={ml_confidence:.1%} | model={ml_model} | features={ml_matched}/{ml_used}")

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
        zigzag_str = json.dumps(prev_analysis.get("zigzag_context") or {}, ensure_ascii=False, indent=2)
        volume_str = json.dumps(prev_analysis.get("volume_context") or {}, ensure_ascii=False, indent=2)
        state_str = json.dumps(prev_analysis.get("state_context") or {}, ensure_ascii=False, indent=2)

        # Divergence context: load from pre-computed candidates
        # Supports both single symbol (str) and multi-symbol (list)
        div_symbols = prev_analysis.get("divergence_symbols") or prev_analysis.get("symbol", "BTCUSDT")
        if isinstance(div_symbols, str):
            div_symbols = [div_symbols]
        if not isinstance(div_symbols, list) or not div_symbols:
            div_symbols = ["BTCUSDT"]

        div_tfs = prev_analysis.get("divergence_timeframes", ["1h", "4h"])
        div_lookback = prev_analysis.get("divergence_lookback_hours", 168)
        div_max_per_tf = prev_analysis.get("divergence_max_per_tf", 3)
        div_max_total = prev_analysis.get("divergence_max_total_signals", 30)
        div_max_chars = prev_analysis.get("divergence_max_prompt_chars", 4000)

        try:
            if len(div_symbols) == 1:
                divergence_str = get_multi_context(
                    symbol=div_symbols[0],
                    tfs=div_tfs,
                    lookback_hours=div_lookback,
                    max_per_tf=div_max_per_tf,
                    max_total_signals=div_max_total,
                    max_prompt_chars=div_max_chars,
                )
            else:
                divergence_str = get_multi_symbol_context(
                    symbols=div_symbols,
                    tfs=div_tfs,
                    lookback_hours=div_lookback,
                    max_per_tf=div_max_per_tf,
                    max_total_signals=div_max_total,
                    max_prompt_chars=div_max_chars,
                )
        except Exception:
            divergence_str = "Нет данных A/D дивергенций."

    else:
        metrics_str = str(prev_analysis) or "Данные недоступны."
        tf_ctx_str = "Один таймфрейм."
        bt_str = "Статистика ещё формируется."
        zigzag_str = "{}"
        volume_str = "{}"
        state_str = "{}"
        divergence_str = "Нет данных A/D дивергенций."

    user_text = PRO_TA_USER_PROMPT.format(
        market_type=market_type,
        metrics=metrics_str,
        tf_context=tf_ctx_str,
        zigzag_context=zigzag_str,
        volume_context=volume_str,
        state_context=state_str,
        divergence_context=divergence_str,
        backtest=bt_str,
    )

    content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    content_parts.append(
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_images[0]}"}}
    )

    payload = {
        "model": MODEL_NAME,
        "temperature": 0.02,
        "stream": False,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": PRO_TA_SYSTEM_PROMPT},
            {"role": "user", "content": content_parts}
        ]
    }

    try:
        async with LLM_QUEUE_LOCK:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=10.0)) as client:
                resp = await client.post(LOCAL_AI_ENDPOINT, json=payload)

        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                err = resp.text[:200]
            return {"error": True, "message": f"LM Studio (HTTP {resp.status_code}): {err}"}

        raw = resp.json()["choices"][0]["message"]["content"]
        logger.warning(f"RAW LLM OUTPUT:\n{raw}")
        parsed = parse_llm_json(raw)

        if parsed.get("error"):
            logger.warning(f"LLM parse fallback: {parsed.get('message')}")
            return {"error": True, "message": "Не удалось распарсить JSON от модели.", "raw": parsed.get("raw", raw)}

        # Inject symbol from prev_analysis into parsed for ML filter
        if isinstance(prev_analysis, dict):
            _sym = (prev_analysis.get("symbol") or prev_analysis.get("symbol_id")
                     or prev_analysis.get("current_symbol")
                     or prev_analysis.get("divergence_symbols") or "")
            if isinstance(_sym, list):
                _sym = _sym[0] if _sym else ""
            _sym = str(_sym).strip()
            if _sym:
                parsed["symbol"] = _sym
                parsed["symbol_id"] = _sym
            else:
                # Log prev_analysis keys for debugging
                logger.info(f"ML INJECT: symbol not found in prev_analysis keys: {list(prev_analysis.keys())[:15]}")

            # Inject active_reference_tf from state_context if available
            _st = prev_analysis.get("state_context") or {}
            if isinstance(_st, dict):
                _ref_tf = _st.get("active_reference_tf") or ""
                if _ref_tf:
                    parsed["active_reference_tf"] = _ref_tf

        parsed = enforce_risk_rules(parsed)
        parsed["error"] = False
        return parsed

    except httpx.ReadTimeout:
        return {"error": True, "message": "LM Studio не ответил за 90 сек."}
    except httpx.ConnectError:
        return {"error": True, "message": "LM Studio выключен или порт 1234 недоступен."}
    except Exception as e:
        logger.exception("Ошибка запроса")
        return {"error": True, "message": f"Ошибка: {type(e).__name__}"}