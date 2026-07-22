# HANDOFF: LOCAL_AI_ENGINE → Hermes #2

**От:** Hermes (instance #1)
**Кому:** Hermes (instance #2)
**Дата:** 2026-07-17 ~20:20 MSK (обновлено)
**Тема:** Передача проекта LOCAL_AI_ENGINE — текущее состояние, нюансы, пути, правила

---

## 0. Главное

Ты подхватываешь проект **LOCAL_AI_ENGINE** — SMC trading bot (BTC/ETH/XAUT). Проект в `/c/Users/Asus-pc/LOCAL_AI_ENGINE`, ветка `main`, HEAD=`dae3e05`. Всё запушено на GitHub (`VVASILEVSS/LOCAL_AI_ENGINE`), working tree clean.

Пользователь: **Василий** (Vasily). Рабочий язык — **только русский**. Автономный режим («САМ РЕШИ»): не задавать A/B/C/D вопросы, выбирать вариант и выполнять. Backup tag перед любой правкой = обязательно.

Бот **запущен**: PID 14128. Session в Hermes: `proc_e75f4947121f`. Проверять через `process(action='poll', session_id='proc_e75f4947121f')`.

---

## 1. РЕПО И ОКРУЖЕНИЕ

### Пути
- **Репо:** `C:\Users\Asus-pc\LOCAL_AI_ENGINE` (= `/c/Users/Asus-pc/LOCAL_AI_ENGINE` в MSYS)
- **Python venv:** `.venv/Scripts/python.exe` (Python 3.13)
- **БД:** `forecasts.db` (signal_log, forecasts, breakout_events), `data/local_engine.db` (пустая)
- **LLM:** ollama_client.py, модель через локальный ollama + визуальный анализ через Alibaba endpoint (DASHBOARD_LLM_BASE_URL/DASHBOARD_LLM_API_KEY в .env)

### Запуск бота
```bash
cd /c/Users/Asus-pc/LOCAL_AI_ENGINE
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe web_dashboard.py
```
Фласк на `localhost:5000`. MT5-индикатор дёргает `/api/signals` + читает файл `signals_{SYM}.json` в `C:\Users\Asus-pc\AppData\Roaming\MetaQuotes\Terminal\Common\Files\`.

### Критично при запуске
- **`env -u PYTHONPATH -u PYTHONHOME`** — ОБЯЗАТЕЛЬНО. Без этого Python подхватывает чужой PYTHONPATH (системный Python 3.13) и модули ломаются.
- Бот спавнит 2 процесса (parent+child). Убивать через `powershell.exe Get-CimInstance Win32_Process | Where {Name='python.exe' -and CommandLine -like '*web_dashboard*'} | Stop-Process -Force`.

---

## 2. ARHИТЕКТУРА / ЗОНИРОВАНИЕ КОДА

**КРИТИЧНО: два разработчика — Hermes и Super Z. Чужой код не трогать без спроса.**

| Файл | Владелец | Что делает |
|---|---|---|
| `core/structure.py` | **Z** | Zone union, nesting_status, is_accumulation, _STRUCT_WINDOW |
| `core/trend_lines.py` | **Z** | Трендовые линии |
| `core/zigzag/benchmark_zigzag.py` | **Z** (+ Hermes FVG) | ZigZag пивоты, structure detection, FVG интегрирован Hermes |
| `core/imbalance_detector.py` | **Hermes** | FVG (Fair Value Gap) детекция: detect_fvg(), check_fill(), detect_imbalance_zones(), get_active_imbalances() |
| `core/ollama_client.py` | **Hermes** | LLM промпт, парсинг, TG формат, enforce_risk_rules, _format_zigzag_context_compact |
| `core/scheduler.py` | **Hermes** | _build_zigzag_context(), _last_analysis_cache, autoscan цикл |
| `core/handlers.py` | **Hermes** | Обработка, sanitize |
| `web_dashboard.py` | **Hermes** | Flask API, /api/signals, файловый fallback для MT5 |

---

## 3. COMMIT CHAIN (актуальный main)

```
...→73bc323 (FVG в LLM+TG)
    →9884874 (Z: STRUCT_WINDOW fix D1=100, 4H=150)
    →0b678e6 (FVG TF-priority: M15 excluded, H1=info, H4+D1=primary)
    →8b6bf35 (merge Z)
    →0c54738 (entry normalize →dict + R:R>1.0 rule #20)
    →b4f7f25 (письмо Z #1)
    →a65a3b5 (CRITICAL: entry→fvg_entry rename — Z's shadowing bug)
    →3f1d2d2 (письмо Z #2 — ответ на shadowing)
    →1556b09 (HANDOFF Hermes #2 v1)
    →8d76829 (fix: TF case-insensitive tf_l=tf.lower() — D1 FVG падал в H1 info)
    →11879ee (cleanup: remove duplicate case-check)
    →dae3e05 (feat: rule 28 — FVG как усилитель сигнала, не standalone) ← HEAD
```

### Что делает каждый коммит

- **`9884874` (Z):** `_STRUCT_WINDOW = {"4h": 150, "1d": 100}` в benchmark_zigzag.py. До этого был `None` → ZigZag брал пивоты из 500 свечей → древние экстремумы (BTC 126199 мая 2025, ETH 4956). После: BTC D1 upper 97932→82828 (хай 06.05.2026), ETH D1 [1503-2463].

- **`0b678e6`:** FVG TF-приоритет в LLM промпте + TG формате. M15 исключён (микро-гэпы путают), H1=info, H4+D1=primary. **Директива user 17.07.2026.** Фильтр на уровне вывода, не детектора.

- **`0c54738`:** (1) `if not isinstance(entry, dict): entry = {}` — защита от LLM возвращающего entry_conditions строкой. (2) Правило 20 в промпте: «R:R must be > 1.0 for aggressive. If <1.0 → no_signal. FORBIDDEN.» (3) Нумерация правил 1-27 без дубликатов.

- **`a65a3b5` (CRITICAL):** `entry` → `fvg_entry` в обоих FVG блоках (`format_json_for_tg()` L2853 + `_format_zigzag_context_compact()` L3001). **Баг нашёл Z**: FVG-цикл `entry = (f"...")` переписывал signal dict `entry = data.get("entry_conditions")` → normalize делал `{}` → `entry.get('aggressive')` = None → сигнал терял aggressive/conservative/current_status → TG показывал «Н/Д». После фикса — не затирается.

- **`8d76829` (BUG FIX):** TF case-insensitive matching. `zigzag_context.timeframes` отдаёт TF-ключи **ЗАГЛАВНЫМИ** (`"1D"`, `"4H"`, `"1H"`, `"15M"`), а FVG-блоки проверяли `tf in ("1d","4h")` — строчные. `"1D" != "1d"` → D1 FVG падал в `else` (info-ветку) вместо primary. 15M исключение тоже не срабатывало для `"15M"`. Фикс: `tf_l = tf.lower()` + `if tf_l in (...)` в обоих блоках. User указал на баг по логу XAUT (D1 FVG в блоке «H1 info» вместо «H4/D1»).

- **`11879ee` (cleanup):** Убран дубль — после патча остался orphan `if tf in ("15m", "5m")` (case-sensitive) перед новым `tf_l` блоком. Удалён.

- **`dae3e05` (RULE 28):** FVG попадал в LLM контекст (zigzag_context), но промпт **не имел правила как модели учитывать FVG в решении**. Только информационная строка «FVG = liquidity зона». Без правила модель могла игнорировать FVG. Добавлено правило 28 в `PRO_TA_USER_PROMPT` (отправляется с каждым запросом): FVG = усилитель сигнала, не standalone trigger. H4/D1 FVG с `current_price_in_zone=true` → усиливает aggressive_breakout/retest. Незаполненный H4/D1 FVG ≤1 ATR от entry → корректирует TP (magnet) или SL. H1 FVG → context, НЕ влияет на signal_status. M15 FVG игнор. FVG сам по себе НЕ переводит no_signal → aggressive (нужно структурное подтверждение).

### Backup tags (откат)
```
backup/pre-fvg-rule28-20260717-202000        → 11879ee (перед rule 28)
backup/pre-tf-case-fix-20260717-195500        → 3f1d2d2 (перед tf.lower() fix)
backup/pre-fvg-tf-priority-20260717-180000   → 73bc323  (перед TF-priority)
backup/pre-fvg-impl-20260717-170000          → 7f6aae4  (перед FVG в LLM+TG)
backup/pre-imbalance-detection-20260717-164000 → 604c924
backup/pre-union-revert-20260717-170000        → 8e993a9
backup/pre-zone-nesting-20260717-133000       → c8a2f9b
backup/pre-zone-curr-structure-2026016-160000 → 91892d8
backup/pre-trade-levels-20260717-110000       → (MT5 render)
backup/pre-sl-fix-4fixes-20260717-101500
backup/pre-phase3-20260715-184500
```
Правило: **перед любой правкой кода — `git tag backup/<timestamp>`**. User доверяет автономии («пробуй, с бэкапа вернём») — backup = safety net.

---

## 4. FVG (Fair Value Gap) — ТЕКУЩЕЕ СОСТОЯНИЕ

### Что такое FVG
3-свечной паттерн имбаланса:
- **Bullish:** `candle[i-1].high < candle[i+1].low` (gap вверх)
- **Bearish:** `candle[i-1].low > candle[i+1].high` (gap вниз)
- Параметры: `min_gap_atr=0.3`, `lookback=50`, `max_fvg=5`, `max_body=3`

### Pipeline
```
imbalance_detector.py
  ├── detect_fvg(candles, tf) → list[FVG dicts]
  ├── check_fill(fvg, candles) → bool + fill_pct
  ├── detect_imbalance_zones(candles, tf) → список зон
  └── get_active_imbalances(candles_by_tf, tf) → dict[tf]→{fvgs, zones}

benchmark_zigzag.py → вызывает get_active_imbalances() → imbalances в compact_timeframes
scheduler.py _build_zigzag_context() → imbalances в zigzag_context (LLM видит)
ollama_client.py:
  ├── _format_zigzag_context_compact() L~2985 → FVG блок в LLM промпт
  └── format_json_for_tg() L~2840 → FVG блок в TG сообщение
```

### TF-приоритеты (user 17.07.2026)
| TF | Статус | В TG/LLM |
|---|---|---|
| **M15/5m** | ❌ FVG ИСКЛЮЧЁН | Зоны+BOS **остаются** (M15 zone рендерится в `tf_block` L2736-2756). FVG исключён (L2840-2841, L2991-2992). tf.lower() — case-insensitive. |
| **H1** | ℹ️ INFO | «общая информация, НЕ основа для прогноза» |
| **H4 + D1** | ⚡ PRIMARY | «серьёзные уровни притяжения» |

**Важно:** M15 исключён **только для FVG/имбаланса**, НЕ полностью. Зоны (range, upper/lower, BOS) M15 рендерятся в `tf_block`. Верифицировано mock-тестом: `M15: [62611 - 63693] | BOS↑ 63693 age=2` — zone present ✅, M15 FVG absent ✅, D1+H4+H1 FVG present ✅.

**Правило 28 (rule 28, `dae3e05`):** FVG как **усилитель сигнала**, не standalone trigger. Живёт в `PRO_TA_USER_PROMPT` (отправляется с каждым LLM запросом):
- H4/D1 FVG + `current_price_in_zone=true` → подтверждение ликвидности, усиливает aggressive_breakout/retest
- Незаполненный H4/D1 FVG ≤1 ATR от entry → корректирует TP (magnet) или SL
- H1 FVG → context, **НЕ влияет** на signal_status
- M15 FVG не показан → игнорируй
- FVG **сам по себе НЕ переводит** no_signal → aggressive_breakout (нужно структурное подтверждение)

Фильтр на уровне вывода (ollama_client.py), НЕ детектора. imbalance_detector.py детектит FVG на всех ТФ. Если Z захочет чинить 15M offset в детекторе — его право, но в вывод не возвращать без user-директивы.

### Z одобрил FVG
Z ответил на 6 вопросов (exchange/inbox/2026-07-17_ответ-z-fvg-принято-делаю-nesting-accumulation.md) — всё одобрано. FVG = liquidity зона, не structure zone.

---

## 5. ЗАПУЩЕННЫЕ ФИКСЫ (РАБОТАЮТ)

| Коммит | Что | Статус |
|---|---|---|
| `438453c` | zone = union(curr, prev) — nesting D1⊇4H⊇1H⊇15M | ✅ работает |
| `0d9954f` | soft nesting (parent_broken флаг, не обрезает) | ✅ оставлен |
| `c8a2f9b` | false-breakout фильтр | ✅ |
| `c7c4ed0` | zone-drift фикс | ✅ |
| `9b64b13` | TF-ladder | ✅ |
| `fde7bb5` | FVG модуль + benchmark integration | ✅ |
| `73bc323` | FVG в LLM промпт + TG | ✅ |
| `0b678e6` | FVG TF-приоритет | ✅ |
| `9884874` | Z STRUCT_WINDOW | ✅ |
| `0c54738` | entry normalize + R:R rule | ✅ |
| `a65a3b5` | entry→fvg_entry (Z's bug) | ✅ |
| `8d76829` | TF case-insensitive (tf_l=tf.lower()) | ✅ |
| `11879ee` | cleanup: remove duplicate case-check | ✅ |
| `dae3e05` | rule 28 — FVG как усилитель сигнала | ✅ |

### ПРАВИЛА КОДИРОВАНИЯ (выработанные)
1. **ПОСЛЕ ЛЮБОЙ ПРАВКИ КОДА — ПРОВЕРЯТЬ ПОЛОЖЕНИЕ ЗОН.** Бэкенд сломал zone-логику → MT5 рисует чушь.
2. **Backup tag ПЕРЕД правкой = обязательно.**
3. **НЕ интерпретировать disjoint зоны как «реальную структуру».** Если 4H [64411-64691] выше 1H [62907-63833] — nesting сломан, это не валидная структура.
4. **scheduler.py `_last_analysis_cache`** обновляется ПОСЛЕ `enforce_risk_rules` с parsed['tf_zones'] (post-nesting зоны). Не с raw zones.
5. **MT5 v1.18 DrawTradeLevels** парсит risk_management+entry_price+tf_zones из /api/signals. Коммит `92fa4e0`.
6. **ПИСЬМО Z:** НЕ пушить в outbox/ пока root cause не подтверждён + user не одобрил. DRAFT хранить в `outbox/DRAFT-*.md`.

---

## 6. ОБМЕН С Z (exchange/)

### Структура exchange/
```
exchange/
  inbox/      ← Z пушит сюда, Hermes читает (НЕ редактировать)
  outbox/     ← Hermes пишет + коммитит + пушит в main (к Z)
  archive/    ← авто-архив старше 14 дней (Hermes коммитит)
```

### Cron (каждые 5 мин)
Проверяет `git fetch` + inbox/ на новые письма Z. **При старте сессии — проверять inbox + outbox.**

### Текущие письма
**inbox (последние, от Z):**
- `2026-07-17_ответ-z-сверка-реальных-графиков-3-бага.md` — Z нашёл 3 бага: (1) STRUCT_WINDOW=None→древние пивоты [FIXED 9884874], (2) бот не обновлён [FIXED restart], (3) контаминация H4 lower=D1 lower [виден в логах CONTAMINATION FIX].
- `2026-07-17_ответ-z-fvg-принято-делаю-nesting-accumulation.md` — Z одобрил FVG.
- `2026-07-17_ответ-z-zone-nesting-флаг-вместо-clip.md` — Z сделал nesting_status + is_accumulation.
- `2026-07-16_ответ-z-zone-curr-structure-analysis.md` — анализ узких зон.
- `2026-07-15_ответ-z-контаминация-вариант-bd-потом-e.md` — контаминация.

**outbox (последние, от Hermes → Z):**
- `2026-07-17_ответ-z-entry-shadowing-fixed-откат-обсуждение.md` (HEAD) — ответ на Z's bug report: entry shadowing FIXED + обсуждение отката `438453c` (Hermes против без кейса).
- `2026-07-17_письмо-z-fvg-tf-priority-ответ-на-3-бага.md` — FVG TF-приоритет + ответ на 3 бага + 3 вопроса Z.
- `DRAFT-2026-07-17_письмо-z-zone-curr-structure-узкие-зоны.md` — DRAFT про узкие зоны (не отправлен).

### Чему Z ответил (ключевые вопросы)
- **Q1 (STRUCT_WINDOW D1=100 vs 50):** Z сказал 100 — норм. 82828 (хай 06.05) — значимый уровень.
- **Q2 (FVG 15M offset):** Z сказал: исключить из вывода — правильное решение. Детектор чинить не надо пока.
- **Q3 (дальнейшие шаги):** Z сказал: приоритет — откатить `438453c` + пофиксить entry shadowing. Потом 24h стабилизация. → Hermes: entry пофиксен, откат 438453c — обсуждаем (я против без кейса).

---

## 7. ОТКРЫТЫЕ ВОПРОСЫ / СПОРНЫЕ МОМЕНТЫ

### 7.1. Откат `438453c` (union→curr_structure ONLY) — АКТИВНЫЙ СПОР

**Позиция Z:** откатить union, вернуть curr_structure ONLY. Логика: curr_struct — «реальная структура после BOS», union с prev — искусственное растягивание. parent_broken флаг покажет пробой.

**Позиция Hermes (текущая):** НЕ откатывать без конкретного кейса. Аргументы:
1. До union зоны были слишком узкие (4H [64411-64691], span 0.4%) и 4H выше 1H → nesting сломан.
2. Union восстановил nesting (4H⊇1H⊇15M). User одобрил.
3. parent_broken флаг уже работает поверх union (Z's код `7f6aae4`).
4. На реальных данных BTC/ETH/XAUT union работает (17.07.2026).

**Что делать:** Z ещё не ответил на контр-аргумент. Ждём. Если Z приведёт кейс — откатить. Если user скажет «откатить» — откатить.

### 7.2. Контаминация (BUG 3) — ЛОЖНАЯ ТРЕВОГА ✅ РАЗРЕШЕНО

Z сообщил о контаминации (4H lower = 1D lower). Проверено по OHLCV-кэшу + live данным 17.07 ~20:00:

| Символ | Зоны | Диагноз |
|---|---|---|
| **BTC** | D1 lower=H4 lower=57758 | ✅ **Не контаминация** — реальный swing low 57800.19 @ 01.07.2026 в обоих окнах (D1 100св / H4 150св) |
| **ETH** | D1=1503.60 vs H4=1510.90 | ✅ Разные → баг контаминации **не подтвердился** |
| **XAUT** | D1=H4=H1 одинаковы | ⚠️ **Не контаминация** — ZigZag недоступен, зоны от LLM, не от структуры. Проблема данных. |

Live цена BTC ~$63,000-63,900 совпадает с ботом (63320). **Бэкенд работает корректно, откат `438453c` не требуется по этому багу.**

### 7.3. MT5 v1.18 кэширование Trade Levels — ИНЦИДЕНТ ЗАКРЫТ ✅

**Наблюдено 17.07 ~19:29:** MT5 показывал старый aggressive_breakout сигнал (entry=3968.33, SL=4027.10, TP1=3942.90, R:R=0.43, id=267) ~30-60 мин после того, как API уже отдаёт `no_signal`. User подтвердил: «этот сигнал уже давно висит, полчаса или час назад, сейчас пропад». MT5 синхронизировался сам.

**Бэкенд работает правильно** (API + файл `signals_XAUTUSDT.json` актуальны). Проблема в MT5-индикаторе (DrawTradeLevels) — кэширует entry/SL/TP и не очищает при `no_signal`. Если повторится — чинить MT5 сторону (код MQ4/MQ5, у user). Рекомендация: DrawTradeLevels должен стирать Trade Levels при `signal_status=no_signal`.

### 7.4. Pending (не срочно)
- Ротация GitHub токена user (VVASILEVSS).
- Обрезка `signal_log.raw_json` на 8000 символов (DB разрастается).
- Обход переопределения `AUTO_SIGNAL_ONLY` в `web_dashboard.py:1264`.
- MT5 v1.18 — проверить отображение зон после union-фикса + STRUCT_WINDOW фикса (визуально на графике).

---

## 8. БЫТОВЫЕ НЮАНСЫ

- **User hardware:** GPU 4 GB VRAM (не 8). STT=faster-whisper small (2.5GB VRAM). Voice TTS=Edge ru-RU-SvetlanaNeural. BT наушники YYK-530.
- **Vision:** glm-5.2-fast-preview БЕЗ нативного зрения, `vision_analyze` таймаутит. Рабочий способ — Qwen-VL через Alibaba endpoint в execute_code (base64→POST). Проверен 07-16: читает TradingView ETH уровни. См. memory.
- **Bot restart на Windows:** terminal(background=true) spawns python, `process(action='kill')` убивает bash wrapper, python orphan выживает → multi-instance TG conflict. FIX: `powershell.exe Get-CimInstance Win32_Process (Name=python.exe, CommandLine=*web_dashboard*) | Stop-Process -Force`. Verify: count=2 (parent+child), no TelegramConflictError. Skills: `references/orphan-process-kill.md` + `references/bot-process-management.md`.
- **Рабочая сфера user:** ремонт ТНВД (топливные насосы). STO Zhurnal — параллельный проект (не активен в этой сессии).
- **1С Бух КЗ** — параллельный проект, не активен.
- **LLM:** Working GLM via alibaba (glm-5.1, glm-5.2, glm-5.2-fast-preview). Z.ai (bigmodel.cn) — balance 0, dead. User prefers free/affordable models.
- **Alibaba Cloud manager:** Larry Lin (larrylin@alibaba-inc.com) — verified real, can bump rate limits.
- ** autoscan_interval** = 15 минут.

---

## 9. ВЕРИФИКАЦИЯ

В репо **нет canonical test-раннера**. Верификация = ad-hoc скрипты под `C:\Users\Asus-pc\AppData\Local\Temp\hermes-verify-*.py`, прогон против изменённого поведения, удаление после.

### Что верифицировано (на 20:15 MSK 17.07, HEAD=`dae3e05`)
**55/55 PASS** ad-hoc:
- Синтаксис ollama_client+scheduler+imbalance_detector
- **Rule 28** (presence + 5 clauses: IN_ZONE, magnet, no-standalone, H1 info)
- **Rules 1-28 unique** (regex start-of-line match, без substring false positives)
- **TF case-insensitive** (tf_l=tf.lower() ×2, no bare `tf in`, uses tf_l for primary+15m exclude)
- **entry shadowing** (fvg_entry ×2, no Chinese 不结构, entry normalize guard, R:R>1.0 rule)
- **TG UPPER routing** (D1/4H→PRIMARY, H1→INFO, 15M→excluded, aggressive preserved, no Н/Д)
- **LLM UPPER routing** (D1→PRIMARY, H1→INFO, 15M→excluded)
- **Edge cases** (entry=str, entry=missing)
- **Bot alive** (/api/signals reachable, server_time актуальный)

### Что проверено на боте
- После restart (id>=270): 8 сигналов, все `no_signal`, R:R=None. **Правило R:R>1.0 работает** (до правила были 6 aggressive с R:R=0.01-0.67).
- `signal_log` сохраняется без AttributeError (до фикса падал).
- **MT5 stale signal инцидент**: API/файл актуальны (no_signal), MT5 кэшировал id=267 (aggressive_breakout R:R=0.43) ~30-60 мин, сам синхронизировался. Бэкенд НЕ виноват.

### R:R < 1.0 в DB (pre-rule vs post-rule)
| signal_log id | signal | R:R | Era |
|---|---|---|---|
| 260-269 | aggressive_breakout | 0.01-0.67 | pre-rule |
| >=270 | no_signal | None | post-rule ✅ |

---

## 10. ЧЕКЛИСТ ПРИ СТАРТЕ СЕССИИ

1. `cd /c/Users/Asus-pc/LOCAL_AI_ENGINE && git status && git log --oneline -5` — проверить HEAD.
2. `git fetch && git log origin/main..HEAD` — проверить непушенные коммиты.
3. Проверить `exchange/inbox/` на новые письма Z.
4. Проверить `exchange/outbox/` — не отправленные DRAFT-письма.
5. Проверить что бот запущен: `powershell.exe -Command "Get-CimInstance Win32_Process | Where {Name='python.exe' -and CommandLine -like '*web_dashboard*'} | Select ProcessId"`.
6. Если бот не запущен — стартовать (см. раздел 1).
7. Проверить last signal_log: `sqlite3 forecasts.db "SELECT id,symbol,signal_status,rr_planned,timestamp FROM signal_log ORDER BY id DESC LIMIT 5"`.

---

## 11. ССЫЛКИ НА SKILLS

Документация и процедуры в репо:
- `references/orphan-process-kill.md` — убийство orphan python на Windows
- `references/bot-process-management.md` — управление ботом
- `references/mt5-sl-tp-rendering.md` — MT5 v1.18 DrawTradeLevels рендеринг
- `references/enforce-risk-rules-overrides.md` — enforce_risk_rules overrides

**Living ТЗ:** `TZ/top-down-structural-analysis.md` — top-down SMC анализ, user координируется с Z через этот док.

**Skill в Hermes:** `1c-buhgalteriya-kazakhstan-tnvd` (параллельный проект 1С, не активен).

---

## 12. ИТОГ ДЛЯ ТЕБЯ

1. Бот работает, HEAD=`dae3e05`, всё запушено, backup tags на месте.
2. entry shadowing FIXED (`a65a3b5`). R:R>1.0 правило работает (id>=270 все no_signal). FVG TF-priority работает.
3. **TF case-insensitive FIXED** (`8d76829`+`11879ee`): D1 FVG теперь в PRIMARY блоке, не H1 info. `tf_l = tf.lower()` в обоих FVG блоках.
4. **Rule 28 добавлена** (`dae3e05`): FVG = усилитель сигнала, не standalone. Живёт в `PRO_TA_USER_PROMPT`. Модель теперь знает как учитывать FVG в решениях.
5. **M15 exclusion верифицирован**: только FVG, не ТФ. M15 zones+BOS рендерятся, FVG исключён.
6. **Контаминация BUG 3 — ЛОЖНАЯ ТРЕВОГА**: BTC общий swing (реальный), ETH разные, XAUT LLM-зоны (ZigZag недоступен). Откат `438453c` не требуется по этому багу.
7. **MT5 stale signal**: не баг бэкенда, MT5 сам обновился. Рекомендация: DrawTradeLevels стирать Trade Levels при no_signal.
8. **Ждём ответ Z** по откату `438453c` (Hermes против, Z за). Контаминация ложна, но Z может иметь другие аргументы.
9. 24h стабилизация (Z предложил) — пусть бот поработает. Ждём первый autoscan цикл с rule 28.
10. Дальше: MT5 v1.18 визуальная проверка, trend lines, угол наклона (user хочет 3-ю размерность).

Удачи. Если сомневаешься — пиши Z (через outbox/), user одобряет важное.

— Hermes #1 (обновлено 17.07.2026 ~20:20 MSK)
