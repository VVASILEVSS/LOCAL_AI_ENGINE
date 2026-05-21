import io
import base64
import json
import re
import httpx
from PIL import Image
import logging
from typing import List, Optional, Dict, Any, Tuple

from core.config import LOCAL_AI_ENDPOINT, MODEL_NAME

logger = logging.getLogger(__name__)

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
"""

PRO_TA_USER_PROMPT = """Тип рынка: {market_type}

Текущие данные:
{metrics}

Контекст таймфреймов:
{tf_context}

ZigZag контекст:
{zigzag_context}

Историческая точность:
{backtest}

Верни ТОЛЬКО валидный JSON без markdown, пояснений или комментариев. Строгая схема:
{{
  "price": <number|null>,

  "htf_structure": "trend|balance|correction|unknown",
  "htf_structure_comment": "краткий комментарий по старшим ТФ 1D/4H на русском",

  "trend_structure": "up|down|balance|unknown",
  "trend_structure_comment": "краткий комментарий о направлении структуры на русском",

  "ltf_structure": "up|down|balance|correction_up|correction_down|unknown",
  "ltf_structure_comment": "краткий комментарий о младшем ТФ на русском",

  "accumulation_state": "accumulation|distribution|none|unknown",
  "accumulation_state_comment": "краткий комментарий на русском",

  "wave_phase": "impulse_up|impulse_down|correction_up|correction_down|impulse_end_risk_up|impulse_end_risk_down|unclear",
  "wave_phase_comment": "краткий комментарий на русском, обязательно с направлением импульса/коррекции и риском конца 5-й волны или ABC, если актуально",

  "abc_risk": "abc_risk_up|abc_risk_down|none|unknown",
  "abc_risk_comment": "краткий комментарий на русском, указывающий направление риска ABC или его отсутствие",

  "global_structure": "trend|balance|correction|unknown",
  "global_structure_comment": "краткий комментарий на русском о текущей фазе рынка",

  "key_zones": {{ "resistance": <number|null>, "support": <number|null> }},
  "key_zones_comment": "краткий комментарий на русском",

  "tf_zones": {{
    "<фактический_ТФ_из_контекста>": {{ "upper": <number|null>, "lower": <number|null> }}
  }},
  "tf_zones_comment": "краткий комментарий на русском",

  "signal_status": "aggressive_breakout|retest|false_breakout|accumulation|no_signal",
  "signal_status_comment": "краткий комментарий на русском",

  "entry_conditions": {{
    "aggressive": "<уровень + объём или null>",
    "conservative": "<уровень ретеста + условие or null>",
    "current_status": "<статус пробоя or null>"
  }},
  "entry_conditions_comment": "краткий комментарий на русском",

  "risk_management": {{ "sl": <number|null>, "tp1": <number|null>, "tp2": <number|null>, "rr": <number|null> }},
  "risk_management_comment": "краткий комментарий на русском",

  "fact_feedback": "<кратко подтверждённые факты на русском>",
  "confidence": "high|medium|low",
  "confidence_reason": "<структура, объём, сессия, пробой, волновая фаза на русском>",
  "missing_data": ["<что отсутствует или не подтверждено на русском>"]
}}

ЖЁСТКИЕ ПРАВИЛА ЗАПОЛНЕНИЯ:
1. htf_structure — только по старшим ТФ 1D/4H. Это не то же самое, что trend_structure.
2. trend_structure — только направление структуры. Не путай с фазой рынка.
3. ltf_structure — только по младшему ТФ. Если младший ТФ в коррекции, так и укажи.
4. wave_phase всегда должна содержать направление:
   - impulse_up = импульс вверх
   - impulse_down = импульс вниз
   - correction_up = коррекция вверх
   - correction_down = коррекция вниз
   - impulse_end_risk_up = импульс вверх с риском завершения и ABC вниз
   - impulse_end_risk_down = импульс вниз с риском завершения и ABC вверх
5. abc_risk всегда должна содержать направление:
   - abc_risk_up = риск ABC вверх после снижения
   - abc_risk_down = риск ABC вниз после роста
   - none = риска ABC нет
6. Если wave_phase_comment явно указывает риск ABC вверх, то abc_risk обязателен = abc_risk_up.
   Если wave_phase_comment явно указывает риск ABC вниз, то abc_risk обязателен = abc_risk_down.
   Не ставь abc_risk = none, если в wave_phase_comment уже указан риск ABC.
7. global_structure:
   - trend = выраженное направленное движение
   - balance = боковик / сжатие / отсутствие явного направления
   - correction = коррекция после импульса
   - unknown = недостаточно данных
8. Если по 1D/4H видно направленное восстановление или падение, не обобщай это в balance без необходимости.
9. Если цена находится внутри диапазона без подтверждённого пробоя, не ставь false_breakout. Используй balance, accumulation или no_signal.
10. tf_zones: использ��й предвычисленные данные из контекста. Запрещено пересчитывать, усреднять или выдумывать upper/lower.
11. risk_management: если signal_status в ["false_breakout", "accumulation", "no_signal"], то sl, tp1, tp2, rr = null.
12. Фибо используй только как контекст глубины коррекции. Не ставь по нему SL/TP и не делай по нему сигнал.
13. Если признаки ослабления импульса видны, отмечай это в wave_phase_comment и abc_risk_comment.
14. Если данных нет — null.
15. Все комментарии на русском.
16. Не добавляй markdown или лишний текст. Только JSON.
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

    # Fix missing commas between fields
    candidate = re.sub(r'("\s*)(\r?\n\s*")', r'\1,\2', candidate)
    candidate = re.sub(r'([}\]])(\r?\n\s*")', r'\1,\2', candidate)
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

    try:
        data = json.loads(candidate)
        if "tf_zones" in data and isinstance(data["tf_zones"], dict):
            normalized = {}
            for k, v in data["tf_zones"].items():
                normalized[str(k).strip().upper()] = v
            data["tf_zones"] = normalized
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"LLM parse failed after cleanup: {e}")
        return {
            "error": True,
            "message": f"Parse failed: {str(e)}",
            "raw": raw,
            "candidate": candidate[:4000],
        }


def enforce_risk_rules(data: dict) -> dict:
    invalid_signals = ["false_breakout", "accumulation", "no_signal"]
    if str(data.get("signal_status", "")).lower() in invalid_signals:
        data["risk_management"] = {"sl": None, "tp1": None, "tp2": None, "rr": None}
        data["risk_management_comment"] = "Не применяется (сигнал не подтверждён)"

    wave_comment = str(data.get("wave_phase_comment", "")).lower()
    wave_phase = str(data.get("wave_phase", "")).lower()
    abc_risk = str(data.get("abc_risk", "")).lower()

    if "abc вверх" in wave_comment or "abc-коррекции вверх" in wave_comment:
        data["abc_risk"] = "abc_risk_up"
        if not data.get("abc_risk_comment"):
            data["abc_risk_comment"] = "Риск ABC вверх по волновой фазе"
    elif "abc вниз" in wave_comment or "abc-коррекции вниз" in wave_comment:
        data["abc_risk"] = "abc_risk_down"
        if not data.get("abc_risk_comment"):
            data["abc_risk_comment"] = "Риск ABC вниз по волновой фазе"
    elif wave_phase == "correction_down" and abc_risk == "none":
        data["abc_risk"] = "abc_risk_up"
        data["abc_risk_comment"] = "Риск ABC вверх после коррекции вниз"
    elif wave_phase == "correction_up" and abc_risk == "none":
        data["abc_risk"] = "abc_risk_down"
        data["abc_risk_comment"] = "Риск ABC вниз после коррекции вверх"

    return data


def format_json_for_tg(data: dict) -> str:
    if not isinstance(data, dict):
        return "⚠️ Неверный формат данных анализа."
    if data.get("error"):
        return f"⚠️ {data.get('message', 'Ошибка анализа.')}"

    def fmt(v):
        if v is None or v == "None" or v == "":
            return "Н/Д"
        return str(v)

    def pretty(v):
        return fmt(v).replace("_", " ").capitalize()

    def format_wave(v):
        mapping = {
            "impulse_up": "Импульс вверх",
            "impulse_down": "Импульс вниз",
            "correction_up": "Коррекция вверх",
            "correction_down": "Коррекция вниз",
            "impulse_end_risk_up": "Риск конца импульса вверх",
            "impulse_end_risk_down": "Риск конца импульса вниз",
            "unclear": "Н/Д",
        }
        return mapping.get(str(v), pretty(v))

    def format_abc(v):
        mapping = {
            "abc_risk_up": "Риск ABC вверх",
            "abc_risk_down": "Риск ABC вниз",
            "none": "Нет",
            "unknown": "Н/Д",
        }
        return mapping.get(str(v), pretty(v))

    def ordered_tf_zones(tf_zones: dict) -> list[Tuple[str, dict]]:
        order = {"1D": 0, "4H": 1, "1H": 2, "15M": 3, "5M": 4}
        items = []
        for tf, z in tf_zones.items():
            if isinstance(z, dict):
                items.append((str(tf).upper(), z))
        items.sort(key=lambda x: order.get(x[0], 99))
        return items

    price = data.get("price")
    signal_status = str(data.get("signal_status", "no_signal"))

    key_zones = data.get("key_zones") or {}
    risk = data.get("risk_management") or {}
    entry = data.get("entry_conditions") or {}
    tf_zones = data.get("tf_zones") or {}

    sl = risk.get("sl")
    tp1 = risk.get("tp1")
    tp2 = risk.get("tp2")
    rr = risk.get("rr")

    zones_block = "📦 ЗОНЫ ПО ТФ:\n"
    ordered = ordered_tf_zones(tf_zones)
    if ordered:
        for tf, z in ordered:
            upper = z.get("upper")
            lower = z.get("lower")
            if upper is not None or lower is not None:
                zones_block += f"  • {tf}: [{fmt(lower)} — {fmt(upper)}]\n"
    else:
        zones_block += "  • Зоны не рассчитаны\n"

    signal_text = pretty(signal_status)
    if signal_status == "no_signal":
        signal_text = "Нет сигнала"

    return (
        f"💰 Цена: {fmt(price)}\n"
        f"🌍 HTF структура: {pretty(data.get('htf_structure', 'unknown'))}\n"
        f"📝 {data.get('htf_structure_comment', '')}\n"
        f"📈 Тренд: {pretty(data.get('trend_structure', 'unknown'))}\n"
        f"📝 {data.get('trend_structure_comment', '')}\n"
        f"🧩 LTF структура: {pretty(data.get('ltf_structure', 'unknown'))}\n"
        f"📝 {data.get('ltf_structure_comment', '')}\n"
        f"🧩 Накопление/распределение: {pretty(data.get('accumulation_state', 'unknown'))}\n"
        f"📝 {data.get('accumulation_state_comment', '')}\n"
        f"🌊 Волновая фаза: {format_wave(data.get('wave_phase', 'unclear'))}\n"
        f"📝 {data.get('wave_phase_comment', '')}\n"
        f"⚠️ ABC риск: {format_abc(data.get('abc_risk', 'unknown'))}\n"
        f"📝 {data.get('abc_risk_comment', '')}\n"
        f"📏 Зоны: R={fmt(key_zones.get('resistance'))} | S={fmt(key_zones.get('support'))}\n"
        f"📝 {data.get('key_zones_comment', '')}\n"
        f"{zones_block}"
        f"🚦 Сигнал: {signal_text}\n"
        f"📝 {data.get('signal_status_comment', '')}\n"
        f"⚡ Агрессивный: {fmt(entry.get('aggressive'))}\n"
        f"🛡️ Консервативный: {fmt(entry.get('conservative'))}\n"
        f"📊 Статус: {fmt(entry.get('current_status'))}\n"
        f"📝 {data.get('entry_conditions_comment', '')}\n"
        f"⚖️ Риск: SL={fmt(sl)} | TP1={fmt(tp1)} | TP2={fmt(tp2)} | R:R={fmt(rr)}\n"
        f"📝 {data.get('risk_management_comment', '')}\n"
        f"📝 Факты: {data.get('fact_feedback', '')}\n"
        f"🎯 Уверенность: {pretty(data.get('confidence', 'low'))} | {data.get('confidence_reason', '')}"
    )


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
    else:
        metrics_str = str(prev_analysis) or "Данные недоступны."
        tf_ctx_str = "Один таймфрейм."
        bt_str = "Статистика ещё формируется."
        zigzag_str = "{}"

    user_text = PRO_TA_USER_PROMPT.format(
        market_type=market_type,
        metrics=metrics_str,
        tf_context=tf_ctx_str,
        backtest=bt_str,
        zigzag_context=zigzag_str,
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=90.0, write=60.0, pool=10.0)) as client:
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

        parsed = enforce_risk_rules(parsed)
        parsed["error"] = False
        return parsed

    except httpx.ReadTimeout:
        return {"error": True, "message": "LM Studio не ответил за 90 сек."}
    except httpx.ConnectError:
        return {"error": True, "message": "LM Studio вы��лючен или порт 1234 недоступен."}
    except Exception as e:
        logger.exception("Ошибка запроса")
        return {"error": True, "message": f"Ошибка: {type(e).__name__}"}