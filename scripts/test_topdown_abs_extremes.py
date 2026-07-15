"""Тест 1.2: Проверка analyze_topdown() с абсолютными экстремумами.

Синтетические данные имитируют BTC-подобную структуру:
- D1: сильный нисходящий тренд, BOS bearish, потом отскок
- H4, H1, 15M: более мелкие структуры внутри D1 зоны

Проверяем:
1. prev_structure берёт АБСОЛЮТНЫЙ max/min (не последний swing)
2. Zone = prev_structure range объединённый с curr
3. Иерархия D1→H4→H1→15M: дети не выходят за родителя
"""
import sys
sys.path.insert(0, "/home/z/my-project/LOCAL_AI_ENGINE")

from core.structure import analyze_topdown, format_structure_narrative

# ── Синтетические данные ──

def make_bearish_structure(base=70000, drop=15000, recovery=3000, n_swings=8):
    """Нисходящая структура с BOS, потом частичный отскок.
    Возвращает (swing_points, closes, current_price, total_candles).
    """
    import random
    random.seed(42)
    swing_points = []
    closes = []
    price = base
    total_candles = 200

    # Генерируем свечи
    for i in range(total_candles):
        noise = random.uniform(-200, 200)
        # Тренд: сначала вниз, потом отскок
        if i < 140:
            trend = -drop / 140
        else:
            trend = recovery / 60
        price = max(price + trend + noise, base - drop - 5000)
        closes.append(round(price, 2))

    # Генерируем пивоты (чередуем high/low)
    # Первые пивоты — нисходящие (LH, LL)
    for i in range(n_swings):
        idx = 15 + i * 20
        if idx >= total_candles - 10:
            break
        if i % 2 == 0:  # high
            swing_points.append({
                "index": idx,
                "price": closes[idx] + random.uniform(500, 1500),
                "type": "high"
            })
        else:  # low
            swing_points.append({
                "index": idx,
                "price": closes[idx] - random.uniform(500, 1500),
                "type": "low"
            })

    # Убеждаемся что BOS есть: последний low ниже предыдущего low
    if len(swing_points) >= 4:
        # Делаем явный bearish BOS: последние 2 low ниже всех предыдущих
        swing_points[-1]["price"] = min(p["price"] for p in swing_points if p["type"] == "low") - 1000
        swing_points[-1]["type"] = "low"

    current_price = closes[-1]
    return swing_points, closes, current_price, total_candles


def make_bullish_child(parent_low, parent_high, tf_name="4h"):
    """Младший ТФ: восходящая структура внутри parent zone."""
    import random
    random.seed(hash(tf_name) % 2**31)
    swing_points = []
    closes = []
    total_candles = 200

    mid = (parent_low + parent_high) / 2
    price = parent_low + (parent_high - parent_low) * 0.3

    for i in range(total_candles):
        noise = random.uniform(-100, 100)
        trend = (parent_high - price) / (total_candles - i + 1) * 0.3
        price = max(parent_low - 500, min(parent_high + 500, price + trend + noise))
        closes.append(round(price, 2))

    for i in range(6):
        idx = 10 + i * 25
        if idx >= total_candles - 5:
            break
        if i % 2 == 0:
            swing_points.append({
                "index": idx,
                "price": min(closes[idx] + random.uniform(200, 800), parent_high + 200),
                "type": "high"
            })
        else:
            swing_points.append({
                "index": idx,
                "price": max(closes[idx] - random.uniform(200, 800), parent_low - 200),
                "type": "low"
            })

    return swing_points, closes, closes[-1], total_candles


# ── D1 ──
d1_swings, d1_closes, d1_price, d1_total = make_bearish_structure(
    base=89000, drop=25000, recovery=4000, n_swings=10
)

# ── H4, H1, 15M — дети внутри D1 зоны ──
# Сначала считаем D1 чтобы узнать зону (без parent)
from core.structure import analyze_tf_structure
d1_analysis = analyze_tf_structure(d1_swings, "1d", d1_price, d1_total, d1_closes)
d1_zone = (d1_analysis.zone_low, d1_analysis.zone_high)

h4_swings, h4_closes, h4_price, h4_total = make_bullish_child(d1_zone[0], d1_zone[1], "4h")
h1_swings, h1_closes, h1_price, h1_total = make_bullish_child(d1_zone[0], d1_zone[1], "1h")
m15_swings, m15_closes, m15_price, m15_total = make_bullish_child(d1_zone[0], d1_zone[1], "15m")

# ── Тестируем analyze_topdown ──
tf_data = {
    "1d": {"swing_points": d1_swings, "current_price": d1_price, "closes": d1_closes, "total_candles": d1_total},
    "4h": {"swing_points": h4_swings, "current_price": h4_price, "closes": h4_closes, "total_candles": h4_total},
    "1h": {"swing_points": h1_swings, "current_price": h1_price, "closes": h1_closes, "total_candles": h1_total},
    "15m": {"swing_points": m15_swings, "current_price": m15_price, "closes": m15_closes, "total_candles": m15_total},
}

results = analyze_topdown(tf_data)

# ── Вывод результатов ──
print("=" * 70)
print("ТЕСТ 1.2: analyze_topdown() — абсолютные экстремумы")
print("=" * 70)

all_ok = True

for tf in ["1d", "4h", "1h", "15m"]:
    if tf not in results:
        print(f"\n{tf.upper()}: НЕТ ДАННЫХ")
        continue
    a = results[tf]
    narrative = format_structure_narrative(a, a.zone_low or 0)
    print(f"\n{narrative}")

    # Проверки
    if a.prev_structure:
        ps = a.prev_structure
        # Проверяем что prev берёт абсолютные экстремумы
        highs = [p["price"] for p in d1_swings if p["type"] == "high"] if tf == "1d" else \
                [p["price"] for p in h4_swings if p["type"] == "high"] if tf == "4h" else \
                [p["price"] for p in h1_swings if p["type"] == "high"] if tf == "1h" else \
                [p["price"] for p in m15_swings if p["type"] == "high"]
        lows = [p["price"] for p in d1_swings if p["type"] == "low"] if tf == "1d" else \
               [p["price"] for p in h4_swings if p["type"] == "low"] if tf == "4h" else \
               [p["price"] for p in h1_swings if p["type"] == "low"] if tf == "1h" else \
               [p["price"] for p in m15_swings if p["type"] == "low"]

        abs_high = max(highs) if highs else 0
        abs_low = min(lows) if lows else 0

        # Только для пивотов ДО BOS
        if a.bos:
            bos_idx = a.bos.index
            swings = d1_swings if tf == "1d" else h4_swings if tf == "4h" else h1_swings if tf == "1h" else m15_swings
            prev_pivs = [p for p in swings if p["index"] <= bos_idx]
            prev_h = [p["price"] for p in prev_pivs if p["type"] == "high"]
            prev_l = [p["price"] for p in prev_pivs if p["type"] == "low"]
            expected_h = max(prev_h) if prev_h else 0
            expected_l = min(prev_l) if prev_l else 0

            if abs(ps.high - expected_h) > 0.01:
                print(f"  ❌ FAIL: prev.high={ps.high} != expected abs max={expected_h}")
                all_ok = False
            else:
                print(f"  ✅ prev.high = {ps.high:.1f} = abs max (OK)")

            if abs(ps.low - expected_l) > 0.01:
                print(f"  ❌ FAIL: prev.low={ps.low} != expected abs min={expected_l}")
                all_ok = False
            else:
                print(f"  ✅ prev.low = {ps.low:.1f} = abs min (OK)")

# ── Иерархия ──
print("\n" + "=" * 70)
print("ИЕРАРХИЯ ЗОН (D1 → 15M)")
print("=" * 70)
print(f"{'TF':<6} {'Zone Low':>12} {'Zone High':>12} {'Span':>10} {'Parent':>8} {'Chain OK':>10}")
print("-" * 60)

prev_high = float("inf")
prev_low = float("-inf")
for tf in ["1d", "4h", "1h", "15m"]:
    if tf not in results:
        continue
    a = results[tf]
    zl = a.zone_low or 0
    zh = a.zone_high or 0
    span = zh - zl
    parent = a.parent_tf or "-"
    # Проверяем что дети внутри родителя
    ok = True
    if parent != "-":
        if zl < prev_low - 0.01 or zh > prev_high + 0.01:
            ok = False
            all_ok = False
    print(f"{tf.upper():<6} {zl:>12.1f} {zh:>12.1f} {span:>10.1f} {parent:>8} {'✅' if ok else '❌':>10}")
    prev_high = zh
    prev_low = zl

print("\n" + "=" * 70)
if all_ok:
    print("РЕЗУЛЬТАТ: ✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
else:
    print("РЕЗУЛЬТАТ: ❌ ЕСТЬ ОШИБКИ")
print("=" * 70)