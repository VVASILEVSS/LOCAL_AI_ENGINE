# Сессия 2026-07-13 — Отчёт для новой машины

## Что было сделано сегодня

### Вариант D (`449e7e0`) — матрёшка + ZigZag fallback

**Проблема:** Зоны по ТФ слипались (D1=H4=H1). Три root cause:
1. Матрёшка в `_validate_zone_nesting` **расширяла parent** до child (`parent["upper"] = c_upper`)
2. LLM дублировал зоны (1D=4H)
3. Fallback = сырые экстремумы (`get_structural_extremums`, raw max/min за 120 свечей)

**Фикс:**
- `core/ollama_client.py:918-923` — матрёшка **сужает child до parent** (`child["upper"] = p_upper`). Старший ТФ авторитетнее.
- `web_dashboard.py:315` — `_fill_missing_tf_zones(result, prev_tf_zones, timeframes, zigzag_timeframes=None)`. Fallback-1: ZigZag benchmark. Fallback-2: prev_tf_zones. Нет нигде → N/A.
- `web_dashboard.py:519` — caller передаёт `zigzag_context.get("timeframes", {})`.

**Verification:** 14/14 PASS. Матрёшка сужает child ✅, ZigZag fallback ✅, no collapse ✅.

**Результат пользователя (vs TradingView):**
- ✅ XAUT D1: было [4367-4464] (выше цены) → стало [3936-4464] (lower ниже цены)
- ✅ ETH: D1 [1594-1848], H4 [1713-1830], M15 [1766-1830] — матрёшка не слипает
- ⚠️ BTC D1=H4=H1 [61544-64692] — ZigZag даёт одинаковые зоны (limitation, не баг)

---

### Volume Profile POC (`a1ce685`) — P1 roadmap

**Зачем:** "ZigZag benchmark" оказался не ZigZag — это raw `np.max(highs)` / `np.min(lows)` за 200 свечей (`benchmark_zigzag.py:214-215`). `swing_points` искусственные (каждый 5-й бар). Поэтому BTC D1=H4=H1 — если absolute max/min попали в одно окно, все ТФ идентичны.

**Новый модуль `core/volume_profile.py`:**
- `build_volume_profile(timeframe, symbol, limit=200, bins=50)` — распределяет объём свечей по ценовым бинам. POC = бин с макс. объёмом. VAH/VAL = границы Value Area (70% объёма вокруг POC).
- TPO fallback когда volume=0 для всех свечей.
- `run_volume_profile(symbol, timeframes, ...)` — для всех ТФ, аналогично `run_benchmark`.

**Интеграция в `web_dashboard.py` `_do_full_scan`:**
- VP вызывается рядом с ZigZag: `run_volume_profile(symbol, timeframes, limit=200, bins=50, value_area_pct=0.70, market_type="future")`
- `vp_context` передаётся в `_fill_missing_tf_zones`.

**Новый fallback приоритет в `_fill_missing_tf_zones`:**
0. **Volume Profile POC** (настоящие зоны консолидации)
1. ZigZag benchmark (старый fallback)
2. prev_tf_zones (для ТФ без VP/ZigZag)
3. N/A если нет нигде

**Case-insensitive lookup:** `_find_zone()` ищет зону по `tf` / `norm_key` / `tf_lower`. VP возвращает lowercase `"1d"`, forecasts.db хранит `"1D"`.

**Verification:** 12/12 PASS. VP иерархия 15M ⊂ 1H ⊂ 4H ⊂ 1D. VP 4H span=4558 vs ZigZag span=9497 (raw max/min).

---

### 1D fetch fix (`0c86a09`)

**Root cause:** `forecasts.db` хранит `timeframes=["15m","1h","4h","1D"]` (uppercase D). Binance API ожидает `"1d"` (lowercase). `fetch_ohlcv(symbol, "1D")` → `BadRequest {"code":-1120,"msg":"Invalid interval."}`.

Поэтому **ни VP, ни ZigZag не могли загрузить 1D данные**. В выводе BTC D1 зона — выдумка LLM (без реальных 1D данных), а 4H=ZigZag raw max/min. Матрёшка сужала 4H до выдуманного D1 → D1=H4=H1.

**Фикс:**
- `core/volume_profile.py:54` — `tf_normalized = timeframe.lower()`
- `core/zigzag/benchmark_zigzag.py:199` — `tf_norm = tf.lower()`

**Verification:** 8/8 PASS. VP 1D для BTCUSDT: POC=67801, VAH=77845, VAL=60972. ZigZag 1D: [57758-97932] (raw max/min за полгода). VP 1D span=16873 < ZigZag span=40174.

**⚠️ Не фиксировано:** `core/auto_chart.py:600` и `core/data_provider.py:88` тоже вызывают `fetch_ohlcv(symbol, tf, ...)` без `.lower()`. Но они вне scope VP POC — возможно тоже падают на 1D.

---

### Super Z коммиты (во время сессии)

| Commit | Что |
|--------|-----|
| `f7844cc` | Задание Гермесу: XAUT lower>price + BTC D1=H4 |
| `6227f87` | Фикс: lower>price validation ±3% в `enforce_risk_rules` (ollama_client.py:901) |

Super Z фикс не конфликтует с вариантом D — в другом блоке (`2a-1b`), до матрёшки (`2a-2`).

---

## Commit stack (новый → старый, на конец сессии)

```
0c86a09  fix: 1D fetch failed — Binance expects lowercase '1d', not '1D'
a1ce685  feat: Volume Profile POC — настоящие зоны консолидации (P1)
6227f87  [Super Z] fix: lower>price validation margin 3% instead of 1%
f7844cc  [Super Z] задание Гермесу: XAUT lower>price + BTC D1=H4
449e7e0  fix: matryoshka narrows child (not expand parent) + ZigZag fallback (variant D)
a7a12d0  [Super Z] задание Гермесу: fallback из ZigZag + матрёшка (вариант D)
```

Предыдущий stack (до сессии, см. `docs/hermes/CONTEXT.md`):
`a112f19` → `ca6936b` → `97dffe9` → `d31d633` → `7718a51` → `64aeb6e` → `5db4ed3` → `46a0012` → `7d35c6a` → `c888d8e` → `f637601`

---

## Roadmap (подтверждён пользователем)

| Приоритет | Метод | Статус |
|-----------|-------|--------|
| ✅ P1 | **Volume Profile POC** | ГОТОВО (`a1ce685`) |
| 📅 P2 | **Order Block SMC** | Следующий — алгоритм из свечей, confluence с POC |
| 📅 P3 | **FVG** | 3-свеч imbalance, дёшевый, дополнительный уровень |

**Отклонено:** Order book depth (запаздывает на D1/4H, rate limits), Camarilla (примитивно), Funding rate (не для зон), Liquidation heatmap (уже есть).

---

## Текущее состояние

- **HEAD:** `0c86a09` (main)
- **Бот:** `proc_5a86a4a1d9e5` (PID 55856), код `0c86a09`, активен
- **@my_hermes_lokal_ai_bot:** cloud Alibaba GLM (`glm-5.2-fast-preview`), variant A
- **@KXROBObot** (main.py): local LM Studio `qwen2.5-vl-7b-instruct`, отдельный процесс
- **forecasts.db** (not in git): `settings.timeframes` = `["15m","1h","4h","1D"]`
- **.env** (not in git): `LLM_API_KEY=***`, `LLM_BASE_URL=...`, `MODEL_NAME=glm-5.2-fast-preview`

## Что проверить на новой машине

1. **`git pull`** — должен получить `0c86a09` (HEAD)
2. **`.env`** — создать вручную (не в git), см. `docs/hermes/CONTEXT.md`
3. **`.venv`** — Python 3.13, `PYTHONPATH=""` перед запуском
4. **Запуск бота:** `cd LOCAL_AI_ENGINE && source .venv/Scripts/activate && PYTHONPATH="" python web_dashboard.py`
5. **Тест VP:** `PYTHONPATH="" python -c "from core.volume_profile import run_volume_profile; print(run_volume_profile(symbol='BTCUSDT', timeframes=['4h','1D'], limit=200, bins=50)['timeframes'])"`
6. **Заказать `/scan BTC ETH XAUT`** — в логах для 1D должно быть `VP: BTCUSDT 1d — POC=...` (не `fetch failed`)

## Known issues (не фиксировано сегодня)

- `core/auto_chart.py:600`, `core/data_provider.py:88` — тот же 1D uppercase баг, вне scope
- `core/binance_metrics.py:28` — OI = N/A (парсинг не извлекает значение)
- Volume 0.07x/0.92x — аномально низкий, нужна проверка rolling mean
- `PYTHONPATH` contamination от Hermes venv → `PYTHONPATH=""` prefix
