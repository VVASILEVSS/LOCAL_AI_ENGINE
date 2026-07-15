"""core/structure.py — BOS detection + structure split + top-down chain.

Принимает список пивотов от _find_real_pivots() и цены закрытия.
Определяет:
  - Последний Break of Structure (BOS)
  - Разделение на prev_structure и curr_structure
  - Top-down structural chain (D1→H4→H1→M15→5M)
  - Накопление (accumulation detection)
  - Цели (targets) из parent boundaries
  - Narrative текст для промпта LLM

BOS = момент когда цена пробивает значимый swing high (bullish BOS)
или swing low (bearish BOS), подтверждая смену направления структуры.
"""
from __future__ import annotations

import logging
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
    # T3: Accumulation
    is_accumulation: bool = False
    accumulation_pivot_count: int = 0
    # T4: Targets (parent boundaries + swing levels)
    targets: List[Dict[str, Any]] = field(default_factory=list)
    # T1: Top-down metadata
    parent_tf: Optional[str] = None
    chain_broken: bool = False  # Reserved: всегда False (жёсткий parent constraint)


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

    # Prev structure: АБСОЛЮТНЫЕ экстремумы за весь период до BOS.
    # Зона = полный видимый structural range (все пивоты до BOS).
    # Старший ТФ задаёт границы, младшие наследуют через parent constraint.
    # Пример: BTC D1 → prev_high = max всех highs до BOS, prev_low = min всех lows.
    prev = None
    if prev_pivots:
        # АБСОЛЮТНЫЕ экстремумы за весь период до BOS — зона = полная структурная range.
        # НЕ зацикливаемся на ТФ, работаем по структуре, старший ТФ в приоритете.
        prev_highs = [p for p in prev_pivots if p["type"] == "high"]
        prev_lows = [p for p in prev_pivots if p["type"] == "low"]
        prev_h = max(p["price"] for p in prev_highs) if prev_highs else current_price
        prev_l = min(p["price"] for p in prev_lows) if prev_lows else current_price
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

    # BUG 1 FIX: Если BOS bullish и curr не обновила high —
    # подтянуть swing high пробитый BOS + max swing high из последнего цикла до BOS.
    # BOS price = пробитый swing high. Но часто последний реальный swing high
    # был ДО пробитого (lower high pattern). Берём max из последних N=5 swing highs до BOS.
    # НЕ берём max всех prev_pivots — там древние экстремумы (78080).
    if bos:
        if bos.direction == "bullish":
            broken_high = bos.price
            prev_highs_before_bos = sorted(
                [p for p in prev_pivots if p["type"] == "high"],
                key=lambda p: p["index"],
            )
            # Берём max из последних 5 swing highs до BOS (свежий рыночный цикл)
            recent_highs = prev_highs_before_bos[-5:] if len(prev_highs_before_bos) >= 5 else prev_highs_before_bos
            max_recent_high = max(p["price"] for p in recent_highs) if recent_highs else broken_high
            curr_h = max(curr_h, broken_high, max_recent_high)
        elif bos.direction == "bearish":
            broken_low = bos.price
            prev_lows_before_bos = sorted(
                [p for p in prev_pivots if p["type"] == "low"],
                key=lambda p: p["index"],
            )
            recent_lows = prev_lows_before_bos[-5:] if len(prev_lows_before_bos) >= 5 else prev_lows_before_bos
            min_recent_low = min(p["price"] for p in recent_lows) if recent_lows else broken_low
            curr_l = min(curr_l, broken_low, min_recent_low)

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
    parent_zone: Optional[Tuple[float, float]] = None,
    parent_tf: Optional[str] = None,
) -> StructureAnalysis:
    """Полный структурный анализ одного ТФ.

    Вызывается после _find_real_pivots() в benchmark_zigzag.

    Args:
        swing_points: пивоты от _find_real_pivots()
        tf: таймфрейм ("15m", "1h", etc.)
        current_price: текущая цена
        total_candles: количество свечей в данных
        closes: массив close (для точного BOS)
        parent_zone: (low, high) рамка от старшего ТФ (T1: top-down)
        parent_tf: имя родительского ТФ (для логирования)

    Returns:
        StructureAnalysis с BOS, prev/curr structures, zone,
        accumulation, targets, top-down metadata.
    """
    bos = detect_bos(swing_points, closes, current_price)
    prev_struct, curr_struct = split_structure(
        swing_points, bos, total_candles, current_price
    )

    # Зона = range текущей И предыдущей структуры.
    # prev_struct.high часто содержит значимый LH/HH который формирует
    # верхнюю границу видимого range (например D1 high = 82850 из мая).
    if curr_struct:
        zone_high = curr_struct.high
        zone_low = curr_struct.low
        swing_dir = curr_struct.direction
        if prev_struct:
            zone_high = max(zone_high, prev_struct.high)
            zone_low = min(zone_low, prev_struct.low)
    else:
        zone_high = current_price
        zone_low = current_price
        swing_dir = "sideways"

    active_pivots = 0
    if bos and swing_points:
        active_pivots = sum(1 for p in swing_points if p["index"] >= bos.index)
    elif swing_points:
        active_pivots = len(swing_points)

    # ── T1: Parent constraint — ребёнок ВНУТРИ parent, но сохраняет свои границы ──
    # При Variant E (ZigZag authoritative) каждый TF должен иметь СВОИ zone boundaries.
    # Старший ТФ задаёт абсолютные рамки, но НЕ перезаписывает child boundaries.
    chain_broken = False
    if parent_zone is not None:
        p_low, p_high = parent_zone
        if zone_high > p_high:
            logging.info(
                "TOPDOWN: %s zone_high %.1f clamped to parent %s %.1f",
                tf, zone_high, parent_tf or "?", p_high,
            )
        if zone_low < p_low:
            logging.info(
                "TOPDOWN: %s zone_low %.1f raised to parent %s %.1f",
                tf, zone_low, parent_tf or "?", p_low,
            )
        # Clamp: ребёнок внутри parent, но сохраняет СВОИ значения если внутри.
        zone_high = min(zone_high, p_high)
        zone_low = max(zone_low, p_low)

    # ── T3: Accumulation detection ──
    is_acc, acc_count = detect_accumulation(swing_points, zone_high, zone_low, tf=tf)

    result = StructureAnalysis(
        tf=tf,
        bos=bos,
        prev_structure=prev_struct,
        curr_structure=curr_struct,
        zone_high=zone_high,
        zone_low=zone_low,
        swing_direction=swing_dir,
        active_pivot_count=active_pivots,
        is_accumulation=is_acc,
        accumulation_pivot_count=acc_count,
        parent_tf=parent_tf,
        chain_broken=chain_broken,
    )

    return result


# ── T3: Accumulation detection ──

_ACCUM_MIN_PIVOTS: Dict[str, int] = {
    "1d": 2, "4h": 3, "1h": 3, "15m": 4, "5m": 4,
}


def detect_accumulation(
    swing_points: List[Dict[str, Any]],
    zone_high: float,
    zone_low: float,
    tf: str = "",
) -> Tuple[bool, int]:
    """Накопление = последние min_pivots пивотов не обновляют zone bounds.

    Если N последних пивотов не пробивают zone_high (для high-пивотов)
    и не пробивают zone_low (для low-пивотов) → накопление.

    Returns:
        (is_accumulation, count_of_consecutive_non_updating_pivots)
    """
    if not swing_points:
        return False, 0

    min_piv = _ACCUM_MIN_PIVOTS.get(tf.lower(), 3)

    # Считаем сколько последних пивотов подряд не обновляют zone
    count = 0
    for p in reversed(swing_points):
        if p["type"] == "high" and p["price"] > zone_high:
            break  # Новый HH — не накопление
        if p["type"] == "low" and p["price"] < zone_low:
            break  # Новый LL — не накопление
        count += 1

    return count >= min_piv, count


# ── T2: Top-down orchestrator ──

# Стандартный порядок анализа (старший → младший)
_TF_ORDER: List[str] = ["1d", "4h", "1h", "15m", "5m"]


def analyze_topdown(
    tf_data: Dict[str, Dict[str, Any]],
    tf_order: Optional[List[str]] = None,
) -> Dict[str, StructureAnalysis]:
    """Top-down structural analysis chain.

    Анализирует ТФ по порядку от старшего к младшему.
    Каждый младший ТФ получает parent_zone от старшего.
    Старший ТФ задаёт жёсткие рамки (low = пол, high = потолок)
    для всех младших ТФ. Chain break убран — структура едина.

    Args:
        tf_data: словарь {tf: {"swing_points": [...], "current_price": float,
                    "closes": [...], "total_candles": int}}
        tf_order: порядок анализа (по умолчанию D1→H4→H1→15M→5M)

    Returns:
        {tf: StructureAnalysis} для каждого ТФ.
    """
    if tf_order is None:
        tf_order = list(_TF_ORDER)

    results: Dict[str, StructureAnalysis] = {}
    parent_zone: Optional[Tuple[float, float]] = None
    parent_tf_name: Optional[str] = None

    for tf in tf_order:
        tf_lower = tf.lower()
        # Ищем данные по разным вариантам ключа
        data = None
        for key in (tf, tf_lower, tf.upper()):
            if key in tf_data:
                data = tf_data[key]
                break
        if not isinstance(data, dict) or not data.get("swing_points"):
            logging.debug("TOPDOWN: %s — no data, skipping", tf)
            continue

        analysis = analyze_tf_structure(
            swing_points=data["swing_points"],
            tf=tf,
            current_price=data["current_price"],
            total_candles=data.get("total_candles", 200),
            closes=data.get("closes"),
            parent_zone=parent_zone,
            parent_tf=parent_tf_name,
        )

        # T4: Собираем targets из parent boundaries
        targets = []
        if parent_zone is not None and parent_tf_name:
            p_low, p_high = parent_zone
            if analysis.zone_high is not None and p_high > analysis.zone_high:
                targets.append({
                    "level": round(p_high, 1),
                    "type": "parent_boundary",
                    "tf": parent_tf_name.upper(),
                    "side": "above",
                })
            if analysis.zone_low is not None and p_low < analysis.zone_low:
                targets.append({
                    "level": round(p_low, 1),
                    "type": "parent_boundary",
                    "tf": parent_tf_name.upper(),
                    "side": "below",
                })
        # Также добавляем значимые swing levels из prev_structure
        if analysis.prev_structure:
            ps = analysis.prev_structure
            if ps.high > (analysis.zone_high or 0):
                targets.append({
                    "level": round(ps.high, 1),
                    "type": "swing_level",
                    "tf": tf.upper(),
                    "side": "above",
                })
            if ps.low < (analysis.zone_low or float("inf")):
                targets.append({
                    "level": round(ps.low, 1),
                    "type": "swing_level",
                    "tf": tf.upper(),
                    "side": "below",
                })
        analysis.targets = targets

        results[tf_lower] = analysis

        # Передаём zone как parent для следующего (младшего) ТФ.
        # Chain break убран — senior TF ВСЕГДА propagate вниз.
        # Это обеспечивает общий low для всех ТФ (как в ручной разметке).
        if analysis.zone_high is not None and analysis.zone_low is not None:
            parent_zone = (analysis.zone_low, analysis.zone_high)
            parent_tf_name = tf_lower
        else:
            parent_zone = None
            parent_tf_name = None

    return results


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
        zone_line = (
            f"  Zone = [{analysis.zone_low:.1f} - {analysis.zone_high:.1f}] "
            f"(span {span_pct:.1f}%, полный структурный range)."
        )
        if analysis.parent_tf:
            zone_line += f" (parent {analysis.parent_tf.upper()} задал рамки)"
        lines.append(zone_line)

    if analysis.is_accumulation:
        lines.append(
            f"  Накопление: {analysis.accumulation_pivot_count} пивотов без обновления zone."
        )

    if analysis.targets:
        tgt_str = ", ".join(
            f"{t['level']:.0f} ({t['type']}, {t['tf']})" for t in analysis.targets[:4]
        )
        lines.append(f"  Цели: {tgt_str}.")

    return "\n".join(lines)


def _direction_ru(direction: str) -> str:
    """Перевод направления на русский для narrative."""
    return {
        "bullish": "восходящая",
        "bearish": "нисходящая",
        "sideways": "боковая",
    }.get(direction, direction)