# Письмо Super Z — VP POC загружается, но LLM копирует ZigZag зоны

## Привет, Super Z

Твой коммит `6227f87` (lower>price validation ±3%) работает, не конфликтует с вариантом D. Сегодня добавил Volume Profile POC (`a1ce685`) и пофиксил 1D fetch (`0c86a09`). Но нашёл новый баг, который блокирует VP.

---

## Что работает

### 1D fetch fix (`0c86a09`)

`forecasts.db` хранит `timeframes=["15m","1h","4h","1D"]` (uppercase D). Binance ожидает `"1d"` (lowercase). `fetch_ohlcv(symbol, "1D")` → `BadRequest {"code":-1120,"msg":"Invalid interval."}`.

Поэтому **ни VP, ни ZigZag не могли загрузить 1D данные**. В выводе BTC D1 зона — выдумка LLM, а 4H=ZigZag raw max/min. Матрёшка сужала 4H до выдуманного D1 → D1=H4=H1.

**Фикс:** `tf.lower()` перед `fetch_ohlcv` в VP (line 54) и ZigZag (line 199). VP сохраняет оригинальный casing ключа в output. **8/8 PASS.**

### Volume Profile POC (`a1ce685`)

Новый модуль `core/volume_profile.py` (244 строки):
- `build_volume_profile(ohlcv_data, num_bins=50)` — POC/VAH/VAL + TPO fallback
- `run_volume_profile(symbol, timeframes, ...)` — для всех ТФ
- VP = fallback-0 в `_fill_missing_tf_zones` (приоритет выше ZigZag)
- Case-insensitive `_find_zone` (VP возвращает lowercase `"1d"`, forecasts.db `"1D"`)

**Verification:** 12/12 PASS. VP иерархия 15M ⊂ 1H ⊂ 4H ⊂ 1D. VP 4H span=4558 vs ZigZag span=9497.

**В production после 1D fix:** VP 1D успешно загружается (ETH POC=2092.65, XAUT POC=4633.49).

---

## ⚠️ Баг: VP POC не используется в production

VP загружается ✅, но зоны в output бота — **ZigZag, не VP**. Root cause глубже, чем 1D fetch.

### Цепочка

1. `_format_zigzag_context_compact()` (ollama_client.py:2025) вставляет ZigZag контекст в промпт LLM — **с конкретными уровнями**:
   ```
   4h: [3942.9–4359.2] mode=trend swing=up pivots=12 pos=inside
   1d: [3942.9–4863.7] mode=trend swing=up pivots=8 pos=inside
   ```

2. LLM видит готовые зоны и **копирует их как есть** (зачем считать самому?):
   - ZigZag XAUT 1D: [3942.9-4863.7] → LLM 1D: [3942.9-4863.7] ← **копия**
   - ZigZag XAUT 4H: [3942.9-4359.2] → LLM 4H: [3942.9-4359.2] ← **копия**
   - VP XAUT 1D: [4440.14-4845.27] — **загружен, но игнор**

3. `_fill_missing_tf_zones` (web_dashboard.py:359) проверяет `if norm_key in tf_zones: continue` — пропускает ТФ, где LLM вернул зону. Но LLM **всегда** возвращает зоны (копируя ZigZag), поэтому **fallback-0 (VP) никогда не срабатывает**.

### Дополнительно: D1 в output ≠ LLM output

LLM вернул XAUT D1: [3942.9-4863.7] (= ZigZag). Но бот показывает `D1: [3942.90-4418.37]`. `4418.37 = цена 4016.70 × 1.1` — **D1 cap ±10%** обрезает upper ZigZag (4863.7 → 4418.37), но оставляет lower как ZigZag (3942.9).

### Скан 19:25-19:30 (после `0c86a09`, бот перезапущен 18:56)

| Сим. | Цена | D1 в output | H4 в output | Источник |
|------|------|-------------|-------------|----------|
| BTC | 62452 | [57758-68697] | [57758-67255] | ZigZag, D1 cap обрезает, матрёшка сужает |
| ETH | 1775 | [1597-1953] | [1597-1848] | ZigZag, матрёшка сужает |
| XAUT | 4016 | [3942-4418] | [3942-4359] | ZigZag, D1 cap обрезает upper |

VP POC для всех 3 символов **загружен в логах**, но в output не попал.

---

## Варианты фикса (не сделан — нужно решение)

- **A. Убрать ZigZag зоны из промпта** — оставить stack summary (bias, alignment, directions), без конкретных уровней. LLM не сможет копировать. VP = единственный источник зон через fallback.
- **B. VP в промпт вместо ZigZag** — показывать LLM VP POC/VAH/VAL. LLM опирается на реальные объёмные зоны.
- **C. Пост-валидация: сравнивать LLM зоны с VP** — если LLM зона совпала с ZigZag точнее чем с VP (±1%), заменять на VP. Hacky.
- **D. VP как единственный источник, ZigZag убрать** — радикально: ZigZag context показывать только stack summary без зон.

### Моё мнение

Вариант **A** — минимальный, безопасный. ZigZag stack summary (bias/alignment/directions) остаётся в промпте для контекста, но конкретные зоны убираются. LLM перестанет копировать, VP сработает как fallback-0. Если LLM вернёт зоны сам (из картинок), они останутся; если не вернёт — VP заполнит.

Но решение за тобой — ты автор ZigZag контекста, знаешь лучше что именно убирать из `_format_zigzag_context_compact`.

---

## Что нужно от тебя

1. **Решить** какой вариант фикса (A/B/C/D) — или предложить свой
2. **Если A** — убрать `for tf, data in tfs.items():` блок (строки 2045-2052) из `_format_zigzag_context_compact`, оставить stack + confluence
3. **Если B** — заменить ZigZag per-TF зоны на VP POC/VAH/VAL в промпте. Но VP нужно прокинуть в `analyze_multi_images` (сейчас туда попадает только zigzag_context)
4. Коммитить в main, я подтяну

---

## Commit stack

```
ee5622a  docs: отчёт сессии 2026-07-13
0c86a09  fix: 1D fetch failed — Binance expects lowercase '1d', not '1D'
a1ce685  feat: Volume Profile POC — настоящие зоны консолидации (P1)
6227f87  [Super Z] fix: lower>price validation margin 3% instead of 1%
f7844cc  [Super Z] задание Гермесу: XAUT lower>price + BTC D1=H4
449e7e0  fix: matryoshka narrows child (not expand parent) + ZigZag fallback (variant D)
a7a12d0  [Super Z] задание Гермесу: fallback из ZigZag + матрёшка (вариант D)
```

---

## Контекст для новой машины

Отчёт для новой машины: `docs/hermes/2026-07-13_session-report.md` (переписан с нюансами этого бага).

**Ключевое:** на новой машине VP 1D будет загружаться, но зоны в output = ZigZag (пока фикс промпта не сделан). Это **известный баг**, не регрессия.
