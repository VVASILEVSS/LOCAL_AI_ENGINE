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
    """Найти последний BOS (Break of Structure) в последовательности пивотов.

    Классический SMC BOS: close пробивает **предыдущий** (не последний!)
    значимый swing level, подтверждая смену структурного направления.

    - Bullish BOS: после формирования lower high, close пробивает
      один из предыдущих swing highs → структура сменилась на бычью.
    - Bearish BOS: после формирования higher low, close пробивает
      один из предыдущих swing lows → структура сменилась на медвежью.

    Ищем ПОСЛЕДНИЙ BOS (самый свежий).

    Args:
        swing_points: отсортированные по index пивоты от _find_real_pivots()
        closes: массив цен закрытия (для определения момента пробоя по close)
        current_price: текущая цена (fallback если closes нет)

    Returns:
        BOSPoint или None
    """
    if not swing_points or len(swing_points) < 3:
        return None

    swing_highs = [(p["index"], p["price"]) for p in swing_points if p["type"] == "high"]
    swing_lows = [(p["index"], p["price"]) for p in swing_points if p["type"] == "low"]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None

    price = current_price if current_price else (closes[-1] if closes else None)
    if not price or price <= 0:
        return None

    last_bos: Optional[BOSPoint] = None

    # ── Bullish BOS: close пробил предыдущий swing high ──
    # Идём с конца по swing_highs (кроме последнего — он текущий уровень).
    # Если после какого-то swing_high[i] был close > этого high
    # и между ними был хотя бы один swing_low → это BOS bullish.
    for i in range(len(swing_highs) - 2, -1, -1):
        sh_idx, sh_price = swing_highs[i]
        if closes:
            # Ищем первый close после sh_idx который пробивает уровень
            broken = False
            for j in range(sh_idx + 1, len(closes)):
                if closes[j] > sh_price:
                    broken = True
                    break
            if not broken:
                continue
        else:
            # Нет closes — проверяем только текущую цену
            # (цена между sh и следующего high была ниже, а теперь выше → пробой)
            if price <= sh_price:
                continue

        # Убеждаемся что после этого high был хотя бы один swing_low
        # (иначе это не слом структуры, а продолжение бычьего тренда)
        has_low_after = any(sl[0] > sh_idx for sl in swing_lows)
        if has_low_after:
            # Нашли BOS — записываем и прерываем (ищем последний = самый свежий)
            bos_idx = sh_idx
            if closes:
                # Точный момент пробоя
                for j in range(sh_idx + 1, len(closes)):
                    if closes[j] > sh_price:
                        bos_idx = j
                        break
            last_bos = BOSPoint(
                index=bos_idx,
                price=sh_price,
                direction="bullish",
                broken_level=sh_price,
                broken_type="swing_high",
            )
            break  # Последний bullish BOS найден

    # ── Bearish BOS: close пробил предыдущий swing low ──
    for i in range(len(swing_lows) - 2, -1, -1):
        sl_idx, sl_price = swing_lows[i]
        if closes:
            broken = False
            for j in range(sl_idx + 1, len(closes)):
                if closes[j] < sl_price:
                    broken = True
                    break
            if not broken:
                continue
        else:
            if price >= sl_price:
                continue

        has_high_after = any(sh[0] > sl_idx for sh in swing_highs)
        if has_high_after:
            bos_idx = sl_idx
            if closes:
                for j in range(sl_idx + 1, len(closes)):
                    if closes[j] < sl_price:
                        bos_idx = j
                        break
            # Берём более свежий BOS из bullish и bearish
            if last_bos is None or bos_idx > last_bos.index:
                last_bos = BOSPoint(
                    index=bos_idx,
                    price=sl_price,
                    direction="bearish",
                    broken_level=sl_price,
                    broken_type="swing_low",
                )
            break  # Последний bearish BOS найден

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
    curr_pivots = [p for p in swing_points if p["index"] >= bos_idx]

    # Если после BOS слишком мало пивотов (< 1 каждого типа) —
    # расширяем до включения 1-2 пивотов ДО BOS для осмысленной зоны
    curr_h_list = [p["price"] for p in curr_pivots if p["type"] == "high"]
    curr_l_list = [p["price"] for p in curr_pivots if p["type"] == "low"]
    if (not curr_h_list or not curr_l_list) and swing_points:
        # Добавляем пивоты перед BOS пока не получим оба типа
        expanded = list(curr_pivots)
        for p in reversed(swing_points):
            if p["index"] < bos_idx:
                expanded.insert(0, p)
                if p["type"] == "high":
                    curr_h_list.append(p["price"])
                elif p["type"] == "low":
                    curr_l_list.append(p["price"])
                if curr_h_list and curr_l_list:
                    break
        curr_pivots = expanded

    curr_h = max(curr_h_list) if curr_h_list else current_price
    curr_l = min(curr_l_list) if curr_l_list else current_price

    # Включаем current_price в range текущей структуры
    h = max(curr_h, current_price)
    l = min(curr_l, current_price)

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