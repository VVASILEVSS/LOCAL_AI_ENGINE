"""core/structure.py — BOS detection + structure split для REAL pivot data.

Принимает список пивотов от _find_real_pivots() и цены закрытия.
Определяет:
  - Последний Break of Structure (BOS)
  - Разделение на prev_structure и curr_structure
  - Формирует narrative текст для промпта LLM

BOS = момент когда цена пробивает значимый swing high (bullish BOS)
или swing low (bearish BOS), подтверждая смену направления структуры.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StructureRange:
    """Описывает одну структурную единицу (движение между двумя BOS)."""
    direction: str  # "bullish" | "bearish" | "sideways"
    high: float
    low: float
    start_index: int
    end_index: int  # inclusive; для текущей = последний индекс в данных
    pivot_count: int = 0
    candle_count: int = 0

    @property
    def span(self) -> float:
        return self.high - self.low

    def price_position(self, price: float) -> float:
        w = max(self.span, 1e-9)
        return round((price - self.low) / w, 4)


@dataclass
class BOSPoint:
    """Точка Break of Structure."""
    index: int
    price: float
    direction: str  # "bullish" | "bearish"
    # Какой level был пробит
    broken_level: float
    broken_type: str  # "swing_high" | "swing_low"


@dataclass
class StructureAnalysis:
    """Результат полного структурного анализа одного ТФ."""
    tf: str
    bos: Optional[BOSPoint] = None
    prev_structure: Optional[StructureRange] = None
    curr_structure: Optional[StructureRange] = None
    # Последние значимые уровни для зоны
    zone_high: Optional[float] = None
    zone_low: Optional[float] = None
    # Свинг-направление (для совместимости)
    swing_direction: str = "sideways"
    # Количество пивотов в текущей структуре
    active_pivot_count: int = 0


def detect_bos(
    swing_points: List[Dict[str, Any]],
    closes: Optional[List[float]] = None,
    current_price: Optional[float] = None,
) -> Optional[BOSPoint]:
    """Найти последний BOS в последовательности пивотов.

    BOS bullish: цена (close) пробивает последний значимый swing high.
    BOS bearish: цена пробивает последний значимый swing low.

    Ищем ПОСЛЕДНИЙ BOS — он определяет текущую структуру.

    Args:
        swing_points: отсортированные по index пивоты от _find_real_pivots()
        closes: массив цен закрытия (для точного определения момента пробоя)
        current_price: текущая цена (если closes нет)

    Returns:
        BOSPoint или None (если BOS не обнаружен — структура не сломана)
    """
    if not swing_points or len(swing_points) < 3:
        return None

    # Нужно минимум 2 пивота одного типа, чтобы был level для пробоя
    # Ищем последний BOS с конца
    last_bos: Optional[BOSPoint] = None

    # Строим последовательность swing highs и swing lows
    swing_highs = [(p["index"], p["price"]) for p in swing_points if p["type"] == "high"]
    swing_lows = [(p["index"], p["price"]) for p in swing_points if p["type"] == "low"]

    if not swing_highs or not swing_lows:
        return None

    # Для каждого потенциального BOS проверяем: пробит ли уровень
    # BOS bullish: close > предыдущий swing high после того как был swing low
    # BOS bearish: close < предыдущий swing low после того как был swing high

    # Сливаем все пивоты в хронологический порядок (уже отсортированы)
    # Ищем паттерн: swing_low → swing_high(пробитый) = bullish BOS
    #               swing_high → swing_low(пробитый) = bearish BOS

    for i in range(2, len(swing_points)):
        prev = swing_points[i - 1]
        curr = swing_points[i]

        if prev["type"] == curr["type"]:
            continue  # два пивота одного типа подряд — не BOS паттерн

        # Определяем направление пробоя
        if prev["type"] == "low" and curr["type"] == "high":
            # ...low → high... — если после этого high цена ушла выше — bullish BOS?
            # Нет, нужно: price > prev significant high (не текущий)
            # Классический BOS: low → higher high → price breaks above previous high
            # Упрощённый: если curr high > prev-prev high (если есть) и цена > curr high
            pass

    # Более простой и надёжный подход:
    # Идём с конца, ищем последний момент где price пробила swing level

    # Находим последний swing high и последний swing low перед текущей ценой
    last_sh_idx, last_sh_price = swing_highs[-1]
    last_sl_idx, last_sl_price = swing_lows[-1]

    # Предпоследние уровни
    prev_sh_price = swing_highs[-2][1] if len(swing_highs) >= 2 else None
    prev_sl_price = swing_lows[-2][1] if len(swing_lows) >= 2 else None

    price = current_price if current_price else (closes[-1] if closes else None)
    if not price or price <= 0:
        return None

    # BOS bullish: последний пивот = high, и цена выше предпоследнего swing high
    # (т.е. структура сменилась с нисходящей на восходящую)
    # Или: последний пивот = low, и цена выше последнего swing high
    if last_sl_idx > last_sh_idx:
        # Последний пивот = low. Если цена > последний swing high → bullish BOS
        if price > last_sh_price:
            last_bos = BOSPoint(
                index=max(last_sl_idx, last_sh_idx),
                price=last_sh_price,
                direction="bullish",
                broken_level=last_sh_price,
                broken_type="swing_high",
            )
    elif last_sh_idx > last_sl_idx:
        # Последний пивот = high. Если цена < последний swing low → bearish BOS
        if price < last_sl_price:
            last_bos = BOSPoint(
                index=max(last_sl_idx, last_sh_idx),
                price=last_sl_price,
                direction="bearish",
                broken_level=last_sl_price,
                broken_type="swing_low",
            )

    return last_bos


def split_structure(
    swing_points: List[Dict[str, Any]],
    bos: Optional[BOSPoint],
    total_candles: int,
    current_price: float,
) -> Tuple[Optional[StructureRange], StructureRange]:
    """Разделить пивоты на предыдущую и текущую структуру по BOS.

    Args:
        swing_points: все пивоты
        bos: обнаруженный BOS (может быть None)
        total_candles: общее количество свечей в данных
        current_price: текущая цена

    Returns:
        (prev_structure, curr_structure)
        Если BOS нет — prev=None, curr = вся выборка.
    """
    if not swing_points:
        # Нет пивотов — вся выборка = одна структура
        curr = StructureRange(
            direction="sideways",
            high=current_price,
            low=current_price,
            start_index=0,
            end_index=total_candles - 1,
            pivot_count=0,
            candle_count=total_candles,
        )
        return None, curr

    if bos is None:
        # BOS не обнаружен — вся выборка = текущая структура
        all_highs = [p["price"] for p in swing_points if p["type"] == "high"]
        all_lows = [p["price"] for p in swing_points if p["type"] == "low"]
        h = max(all_highs) if all_highs else current_price
        l = min(all_lows) if all_lows else current_price

        # Определяем направление по первому и последнему пивоту
        if len(swing_points) >= 2:
            d = _structure_direction(swing_points[0], swing_points[-1], current_price)
        else:
            d = "sideways"

        curr = StructureRange(
            direction=d,
            high=h,
            low=l,
            start_index=swing_points[0]["index"],
            end_index=total_candles - 1,
            pivot_count=len(swing_points),
            candle_count=total_candles - swing_points[0]["index"],
        )
        return None, curr

    # Разделяем по BOS
    bos_idx = bos.index

    # Предыдущая структура: от начала до BOS
    prev_pivots = [p for p in swing_points if p["index"] <= bos_idx]
    # Текущая структура: от BOS до конца
    curr_pivots = [p for p in swing_points if p["index"] >= bos_idx]

    # Prev structure
    prev = None
    if prev_pivots:
        prev_h = max(p["price"] for p in prev_pivots if p["type"] == "high") or current_price
        prev_l = min(p["price"] for p in prev_pivots if p["type"] == "low") or current_price
        start = prev_pivots[0]["index"]
        if len(prev_pivots) >= 2:
            prev_dir = _structure_direction(prev_pivots[0], prev_pivots[-1], bos.price)
        else:
            prev_dir = "sideways"
        prev = StructureRange(
            direction=prev_dir,
            high=prev_h,
            low=prev_l,
            start_index=start,
            end_index=bos_idx,
            pivot_count=len(prev_pivots),
            candle_count=bos_idx - start,
        )

    # Curr structure
    curr_h = [p["price"] for p in curr_pivots if p["type"] == "high"]
    curr_l = [p["price"] for p in curr_pivots if p["type"] == "low"]
    h = max(curr_h) if curr_h else current_price
    l = min(curr_l) if curr_l else current_price

    # Включаем current_price в range текущей структуры
    h = max(h, current_price)
    l = min(l, current_price)

    if len(curr_pivots) >= 2:
        d = _structure_direction(curr_pivots[0], curr_pivots[-1], current_price)
    elif curr_pivots:
        d = "bullish" if current_price > curr_pivots[0]["price"] else "bearish"
    else:
        d = bos.direction  # направление BOS = направление текущей структуры

    curr = StructureRange(
        direction=d,
        high=h,
        low=l,
        start_index=bos_idx,
        end_index=total_candles - 1,
        pivot_count=len(curr_pivots),
        candle_count=total_candles - bos_idx,
    )

    return prev, curr


def _structure_direction(
    first_pivot: Dict[str, Any],
    last_pivot: Dict[str, Any],
    current_price: float,
) -> str:
    """Определить направление структуры по первому и последнему пивоту."""
    if last_pivot["type"] == "high" and current_price < last_pivot["price"]:
        return "bearish"
    if last_pivot["type"] == "low" and current_price > last_pivot["price"]:
        return "bullish"
    if first_pivot["price"] < last_pivot["price"]:
        return "bullish"
    if first_pivot["price"] > last_pivot["price"]:
        return "bearish"
    return "sideways"


def analyze_tf_structure(
    swing_points: List[Dict[str, Any]],
    tf: str,
    current_price: float,
    total_candles: int = 200,
    closes: Optional[List[float]] = None,
) -> StructureAnalysis:
    """Полный структурный анализ одного ТФ.

    Вызывается после _find_real_pivots() в benchmark_zigzag.

    Args:
        swing_points: пивоты от _find_real_pivots()
        tf: таймфрейм ("15m", "1h", etc.)
        current_price: текущая цена
        total_candles: количество свечей в данных
        closes: массив close (для точного BOS)

    Returns:
        StructureAnalysis с BOS, prev/curr structures, zone
    """
    bos = detect_bos(swing_points, closes, current_price)
    prev_struct, curr_struct = split_structure(
        swing_points, bos, total_candles, current_price
    )

    # Зона = range текущей структуры (или всей выборки если BOS нет)
    if curr_struct:
        zone_high = curr_struct.high
        zone_low = curr_struct.low
        swing_dir = curr_struct.direction
    else:
        zone_high = current_price
        zone_low = current_price
        swing_dir = "sideways"

    active_pivots = 0
    if bos and swing_points:
        active_pivots = sum(1 for p in swing_points if p["index"] >= bos.index)
    elif swing_points:
        active_pivots = len(swing_points)

    return StructureAnalysis(
        tf=tf,
        bos=bos,
        prev_structure=prev_struct,
        curr_structure=curr_struct,
        zone_high=zone_high,
        zone_low=zone_low,
        swing_direction=swing_dir,
        active_pivot_count=active_pivots,
    )


def format_structure_narrative(analysis: StructureAnalysis, price: float) -> str:
    """Форматировать structure analysis как текстовый narrative для промпта LLM.

    Заменяет _format_zigzag_context_compact.

    Пример вывода:
        15m: BOS bullish на 1793 (8 свечей назад).
             Пред. структура: нисходящая 1793→1748 (5 пивотов, 18 св).
             Тек. структура: восходящая 1748→1789 (3 пивота, 12 св).
             Zone = [1748.0 - 1793.0] (структурный range после BOS).
    """
    lines = [analysis.tf.upper() + ":"]

    if analysis.bos:
        bos = analysis.bos
        candles_ago = "недавно"
        if analysis.curr_structure:
            candles_ago = f"{analysis.curr_structure.candle_count} св. назад"
        lines.append(
            f"  BOS {bos.direction} на {bos.broken_level:.1f} ({candles_ago})."
        )
    else:
        lines.append("  BOS не обнаружен (структура не сломана).")

    if analysis.prev_structure:
        ps = analysis.prev_structure
        dir_ru = _direction_ru(ps.direction)
        lines.append(
            f"  Пред. структура: {dir_ru} {ps.low:.1f}→{ps.high:.1f} "
            f"({ps.pivot_count} пивотов, {ps.candle_count} св)."
        )

    if analysis.curr_structure:
        cs = analysis.curr_structure
        dir_ru = _direction_ru(cs.direction)
        pos = cs.price_position(price)
        pos_desc = "верх" if pos > 0.7 else "низ" if pos < 0.3 else "середина"
        lines.append(
            f"  Тек. структура: {dir_ru} {cs.low:.1f}→{cs.high:.1f} "
            f"({cs.pivot_count} пивотов, {cs.candle_count} св, цена в {pos_desc})."
        )

    if analysis.zone_high and analysis.zone_low:
        span_pct = (analysis.zone_high - analysis.zone_low) / price * 100
        lines.append(
            f"  Zone = [{analysis.zone_low:.1f} - {analysis.zone_high:.1f}] "
            f"(span {span_pct:.1f}%)."
        )

    return "\n".join(lines)


def _direction_ru(direction: str) -> str:
    """Перевод направления на русский для narrative."""
    return {
        "bullish": "восходящая",
        "bearish": "нисходящая",
        "sideways": "боковая",
    }.get(direction, direction)