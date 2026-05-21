# Назначение: хранение и сравнение рыночного состояния между анализами.
# Отвечает за: историю уровней, фиксацию слома/перестройки, ретесты и обновление диапазонов.
# Связано с: scheduler.py, handlers.py, ollama_client.py.

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "state")


ZONE_STATUS_VALUES = {
    "saved",
    "broken",
    "rebuilt",
    "false_breakout",
    "retest",
    "updated_inside_range",
    "unknown",
}

ACTIVE_REFERENCE_TF_ORDER = ["M15", "H1", "H4", "D1"]

def _normalize_tf_key(tf: Any) -> str:
    s = str(tf).strip().upper()

    # убираем угловые скобки и лишние символы
    s = s.replace("<", "").replace(">", "").replace("[", "").replace("]", "").replace("(", "").replace(")", "")

    # унификация популярных форматов
    if s in ("1H", "H1", "60M"):
        return "H1"
    if s in ("4H", "H4", "240M"):
        return "H4"
    if s in ("15M", "M15", "15"):
        return "M15"
    if s in ("1D", "D1", "D"):
        return "D1"

    # если пришло что-то вроде "1H " или " 4h"
    if "1H" in s:
        return "H1"
    if "4H" in s:
        return "H4"
    if "15M" in s:
        return "M15"
    if "1D" in s:
        return "D1"

    return s

def _ensure_state_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def _state_path(symbol: str, timeframe: str) -> str:
    _ensure_state_dir()
    safe_symbol = str(symbol).replace("/", "").upper().strip()
    safe_tf = str(timeframe).replace("/", "").upper().strip()
    return os.path.join(STATE_DIR, f"{safe_symbol}_{safe_tf}.json")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            if not value:
                return None
            return float(value)
        return None
    except (TypeError, ValueError):
        return None


def _to_float_list(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for v in values:
        fv = _safe_float(v)
        if fv is not None:
            out.append(fv)
    return out


def _normalize_zone_pair(zone: Any) -> dict[str, Optional[float]]:
    if not isinstance(zone, dict):
        return {"upper": None, "lower": None}

    upper = _safe_float(zone.get("upper"))
    lower = _safe_float(zone.get("lower"))

    if upper is not None and lower is not None and lower > upper:
        lower, upper = upper, lower

    return {"upper": upper, "lower": lower}


def normalize_zones(tf_zones: dict[str, Any]) -> dict[str, dict[str, Optional[float]]]:
    normalized: dict[str, dict[str, Optional[float]]] = {}
    if not isinstance(tf_zones, dict):
        return normalized

    for tf, zone in tf_zones.items():
        tf_key = _normalize_tf_key(tf)
        normalized[tf_key] = _normalize_zone_pair(zone)

    order_map = {"M15": 0, "H1": 1, "H4": 2, "D1": 3}
    return dict(sorted(normalized.items(), key=lambda item: order_map.get(item[0], 99)))


def load_state(symbol: str, timeframe: str) -> dict | None:
    path = _state_path(symbol, timeframe)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def save_state(symbol: str, timeframe: str, state: dict) -> None:
    path = _state_path(symbol, timeframe)

    payload = dict(state or {})
    payload["symbol"] = str(symbol).replace("/", "").upper().strip()
    payload["timeframe"] = str(timeframe).upper().strip()
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _levels_from_zones(tf_zones: dict[str, Any]) -> list[float]:
    levels: list[float] = []
    if not isinstance(tf_zones, dict):
        return levels

    for zone in tf_zones.values():
        if not isinstance(zone, dict):
            continue
        upper = _safe_float(zone.get("upper"))
        lower = _safe_float(zone.get("lower"))
        if upper is not None:
            levels.append(upper)
        if lower is not None:
            levels.append(lower)

    return sorted(set(round(x, 6) for x in levels))


def extract_broken_levels(previous: dict | None, current: dict) -> list[float]:
    broken: list[float] = []

    prev_zones = normalize_zones((previous or {}).get("tf_zones") or {})
    curr_price = _safe_float(current.get("price") or current.get("current_price"))
    if curr_price is None:
        return broken

    for tf, zone in prev_zones.items():
        upper = zone.get("upper")
        lower = zone.get("lower")
        if upper is None or lower is None:
            continue

        if curr_price > upper:
            broken.append(float(upper))
        elif curr_price < lower:
            broken.append(float(lower))

    return sorted(set(round(x, 6) for x in broken))


def extract_new_levels(current: dict, previous: dict | None) -> list[float]:
    curr_zones = normalize_zones((current or {}).get("tf_zones") or {})
    prev_zones = normalize_zones((previous or {}).get("tf_zones") or {})
    curr_levels = _levels_from_zones(curr_zones)
    prev_levels = set(_levels_from_zones(prev_zones))

    new_levels = [lvl for lvl in curr_levels if round(lvl, 6) not in {round(x, 6) for x in prev_levels}]
    return sorted(set(round(x, 6) for x in new_levels))


def is_false_breakout(previous: dict | None, current: dict, price: float) -> bool:
    prev_zones = normalize_zones((previous or {}).get("tf_zones") or {})
    curr_zones = normalize_zones((current or {}).get("tf_zones") or {})

    curr_price = _safe_float(price)
    if curr_price is None or not prev_zones:
        return False

    prev_price = _safe_float((previous or {}).get("price") or (previous or {}).get("current_price"))
    if prev_price is None:
        return False

    # False breakout засчитываем только если:
    # 1) раньше цена была за пределами старой зоны;
    # 2) сейчас цена вернулась внутрь той же зоны;
    # 3) текущая зона не пустая.
    for zone in prev_zones.values():
        upper = zone.get("upper")
        lower = zone.get("lower")
        if upper is None or lower is None:
            continue

        lo = min(lower, upper)
        hi = max(lower, upper)

        # Текущая цена должна быть внутри хотя бы одной текущей зоны
        inside_current_zone = False
        for czone in curr_zones.values():
            cupper = czone.get("upper")
            clower = czone.get("lower")
            if cupper is None or clower is None:
                continue

            clo = min(clower, cupper)
            chi = max(clower, cupper)
            if clo <= curr_price <= chi:
                inside_current_zone = True
                break

        if not inside_current_zone:
            continue

        broke_up = prev_price > hi and curr_price <= hi
        broke_down = prev_price < lo and curr_price >= lo

        if broke_up or broke_down:
            return True

    return False


def is_retest(previous: dict | None, current: dict, price: float) -> bool:
    prev_state = previous or {}
    prev_zones = normalize_zones(prev_state.get("tf_zones") or {})
    curr_price = _safe_float(price)
    if curr_price is None or not prev_zones:
        return False

    prev_price = _safe_float(prev_state.get("price") or prev_state.get("current_price"))
    if prev_price is None:
        return False

    for zone in prev_zones.values():
        upper = zone.get("upper")
        lower = zone.get("lower")
        if upper is None or lower is None:
            continue

        lo = min(lower, upper)
        hi = max(lower, upper)

        # Ретест: раньше цена была с одной стороны, потом вернулась к зоне.
        near_zone = lo * 0.997 <= curr_price <= hi * 1.003
        crossed = (prev_price > hi and curr_price <= hi) or (prev_price < lo and curr_price >= lo)
        if near_zone and crossed:
            return True

    return False


def get_active_reference_tf(tf_zones: dict[str, Any], price: float | None) -> str:
    zones = normalize_zones(tf_zones or {})
    curr_price = _safe_float(price)
    if curr_price is None or not zones:
        return "unknown"

    for tf in ("M15", "H1", "H4", "D1"):
        zone = zones.get(tf)
        if not zone:
            continue
        upper = zone.get("upper")
        lower = zone.get("lower")
        if upper is None or lower is None:
            continue

        lo = min(lower, upper)
        hi = max(lower, upper)
        if lo <= curr_price <= hi:
            return tf

    # Если цена вне всех диапазонов — возвращаем самый старший доступный ТФ
    for tf in ("D1", "H4", "H1", "M15"):
        if tf in zones:
            return tf

    return "unknown"

def detect_zone_event(previous: dict | None, current: dict) -> str:
    curr_price = _safe_float(current.get("price") or current.get("current_price"))
    if curr_price is None:
        return "unknown"

    prev_zones = normalize_zones((previous or {}).get("tf_zones") or {})
    curr_zones = normalize_zones((current or {}).get("tf_zones") or {})

    if not prev_zones:
        return "saved"

    broken = extract_broken_levels(previous, current)
    new_levels = extract_new_levels(current, previous)

    # false_breakout проверяем первым, но только при реальном возврате внутрь текущей зоны
    if is_false_breakout(previous, current, curr_price):
        return "false_breakout"

    if is_retest(previous, current, curr_price):
        return "retest"

    if broken and new_levels:
        return "rebuilt"

    if broken:
        return "broken"

    if new_levels and not broken:
        return "updated_inside_range"

    prev_levels = _levels_from_zones(prev_zones)
    curr_levels = _levels_from_zones(curr_zones)

    if prev_levels == curr_levels:
        prev_price = _safe_float((previous or {}).get("price") or (previous or {}).get("current_price"))
        if prev_price is not None:
            move_pct = abs(curr_price - prev_price) / max(prev_price, 1e-9)
            if move_pct > 0.001:
                return "updated_inside_range"
        return "saved"

    prev_span = sum(
        abs((z.get("upper") or 0) - (z.get("lower") or 0))
        for z in prev_zones.values()
        if isinstance(z, dict) and z.get("upper") is not None and z.get("lower") is not None
    )
    curr_span = sum(
        abs((z.get("upper") or 0) - (z.get("lower") or 0))
        for z in curr_zones.values()
        if isinstance(z, dict) and z.get("upper") is not None and z.get("lower") is not None
    )

    if abs(curr_span - prev_span) > max(prev_span * 0.05, 1e-9):
        return "updated_inside_range"

    return "saved"

def compare_state(previous: dict | None, current: dict) -> dict:
    curr_price = _safe_float(current.get("price") or current.get("current_price"))
    curr_zones = normalize_zones((current or {}).get("tf_zones") or {})
    prev_zones = normalize_zones((previous or {}).get("tf_zones") or {})

    zone_status = detect_zone_event(previous, current)
    broken_levels = extract_broken_levels(previous, current)
    new_levels = extract_new_levels(current, previous)

    active_reference_tf = get_active_reference_tf(curr_zones, curr_price)
    structure_shifted = zone_status in {"broken", "rebuilt", "false_breakout"}
    needs_rebuild = zone_status in {"broken", "false_breakout"}

    comment_parts: list[str] = []

    if zone_status == "saved":
        prev_price = _safe_float((previous or {}).get("price") or (previous or {}).get("current_price"))
        if prev_price is not None and curr_price is not None:
            if abs(curr_price - prev_price) / max(prev_price, 1e-9) > 0.001:
                comment_parts.append("Зоны сохранены, но цена заметно сместилась внутри диапазона.")
            else:
                comment_parts.append("Зоны сохранены без значимых изменений.")
        else:
            comment_parts.append("Зоны сохранены без значимых изменений.")
    elif zone_status == "broken":
        comment_parts.append("Одна или несколько зон пробиты.")
    elif zone_status == "rebuilt":
        comment_parts.append("Структура перестроена после пробоя.")
    elif zone_status == "false_breakout":
        comment_parts.append("Обнаружен ложный пробой с возвратом внутрь диапазона.")
    elif zone_status == "retest":
        comment_parts.append("Возможен ретест пробитой зоны.")
    elif zone_status == "updated_inside_range":
        comment_parts.append("Зоны обновились внутри старого диапазона.")
    else:
        comment_parts.append("Недостаточно данных для точного определения события.")

    if active_reference_tf != "unknown":
        comment_parts.append(f"Активный ориентир: {active_reference_tf}.")

    if broken_levels:
        comment_parts.append(f"Пробитые уровни: {', '.join(str(x) for x in broken_levels[:6])}.")
    if new_levels:
        comment_parts.append(f"Новые уровни: {', '.join(str(x) for x in new_levels[:6])}.")

    comment = " ".join(comment_parts).strip()

    return {
        "zone_status": zone_status if zone_status in ZONE_STATUS_VALUES else "unknown",
        "active_reference_tf": active_reference_tf,
        "broken_levels": broken_levels,
        "new_levels": new_levels,
        "structure_shifted": structure_shifted,
        "needs_rebuild": needs_rebuild,
        "comment": comment,
    }


def build_state_context(state_diff: dict, current: dict, previous: dict | None = None) -> dict:
    curr_price = _safe_float(current.get("price") or current.get("current_price"))
    current_substructure = str(current.get("current_substructure", "unknown"))
    signal_status = str(current.get("signal_status", "unknown"))
    scenario_status = str(current.get("scenario_status", "unknown"))

    ctx = {
        "zone_status": str(state_diff.get("zone_status", "unknown")),
        "active_reference_tf": str(state_diff.get("active_reference_tf", "unknown")),
        "broken_levels": state_diff.get("broken_levels", []),
        "new_levels": state_diff.get("new_levels", []),
        "structure_shifted": bool(state_diff.get("structure_shifted", False)),
        "needs_rebuild": bool(state_diff.get("needs_rebuild", False)),
        "comment": str(state_diff.get("comment", "")),
        "price": curr_price,
        "current_substructure": current_substructure,
        "signal_status": signal_status,
        "scenario_status": scenario_status,
    }

    if previous:
        prev_price = _safe_float(previous.get("price") or previous.get("current_price"))
        if prev_price is not None:
            ctx["previous_price"] = prev_price

    return ctx


def update_and_save_state(symbol: str, timeframe: str, current: dict) -> dict:
    previous = load_state(symbol, timeframe)
    state_diff = compare_state(previous, current)
    payload = dict(current or {})
    payload["state_diff"] = state_diff
    payload["state_context"] = build_state_context(state_diff, payload, previous)
    save_state(symbol, timeframe, payload)
    return payload