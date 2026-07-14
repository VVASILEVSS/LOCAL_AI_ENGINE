# Задание: XAUT зона D1 ВЫШЕ цены + BTC D1=H4=H1 из одинакового ZigZag

## Контекст

После коммита 449e7e0 (вариант D: матрёшка + ZigZag fallback) Zones немного улучшились у ETH (D1≠H4 ✅), но появились новые баги.

## Баг 1: XAUT — все зоны anchored на один ZigZag pivot, D1 вся ВЫШЕ цены

**Факты** (цена XAUT = 4058.27):

| ТФ | Зона | Проблема |
|----|------|----------|
| D1 | [**4367** - 4464.10] | lower=4367 > price=4058 (+7.6%), вся зона ВЫШЕ цены |
| H4 | [4359.20 - **4367**] | span=7.80, вырожденная зона |
| H1 | [4189.40 - **4367**] | upper=D1 lower (матрёшка) |
| M15 | [4112.40 - **4367**] | upper=D1 lower (матрёшка) |

**Что произошло**: ZigZag benchmark для D1 вернул zone с lower=4367. Матрёшка видит D1 как parent с lower=4367 → сужает все child чтобы child.upper <= 4367. Все child получили upper=4367.

**Почему D1 cap не сработал**: 4367 = +7.6% от цены < 10% cap. Cap не видит что ВСЯ ЗОНА выше цены.

**Решение**: в `_validate_zone_nesting()` или после fallback — валидация:
- Если `zone.lower > price` → зона невалидна (support выше цены = нонсенс)
- Действие: сдвинуть lower вниз до `price * (1 - some_pct)`, например `price - 1*ATR` или `price * 0.97`
- Или проще: если lower > price, установить lower = min(existing_lower, price - 1%)

## Баг 2: BTC D1 = H4 = H1 — ZigZag возвращает одинаковые zones

**Факты** (цена BTC = 62580):

| ТФ | Зона | Источник |
|----|------|----------|
| D1 | [61544.56 - 64692.83] | ZigZag fallback |
| H4 | [61544.56 - 64692.83] | ZigZag fallback (= D1) |
| H1 | [61544.56 - 64691.90] | ZigZag fallback (≈ D1) |
| M15 | [62434.90 - 64497.40] | LLM ✅ |

**Причина**: ZigZag benchmark для 1D, 4H, 1H вернул одинаковые upper/lower (61544/64692). Это значит ZigZag находит одни и те же pivots для разных ТФ когда свечи покрывают один и тот же ценовой диапазон.

**Решение**: это не баг матрёшки и не баг fallback — это ограничение ZigZag. Когда LLM вернёт zones (после передачи всех картинок), проблема уйдёт. Но для fallback: если ZigZag D1 = ZigZag H4, оставить как есть. Матрёшка уже не слипает их (H1 не шире D1).

**Приоритет**: низкий — исправится когда LLM начнёт возвращать zones для всех ТФ.

## Что делать

### Приоритет 1: Баг 1 — валидация lower > price

В `_validate_zone_nesting()` (ollama_client.py) после D1 cap:

```python
# После D1 cap (строки ~890-898):
# Валидация: lower не должен быть выше цены
if d1_lower is not None and price is not None and d1_lower > price:
    d1["lower"] = round(price * 0.99, 2)  # или price - 1*ATR если есть
```

Или более общий вариант — для ЛЮБОГО ТФ, не только D1:
```python
for tf_key, z in tf_zones.items():
    if z.get("lower") is not None and z["lower"] > price:
        z["lower"] = round(price * 0.99, 2)
```

### Приоритет 2 (низкий): BTC D1=H4=H1

Не трогать. Исправится когда LLM начнёт возвращать zones. Если нужно быстрое улучшение — расширить ZigZag depth для 1D (чтобы находил более широкие pivots). Но это задача roadmap, не хотфикс.

## Файл

`core/ollama_client.py` — `_validate_zone_nesting()` после строки ~898 (после D1 cap)