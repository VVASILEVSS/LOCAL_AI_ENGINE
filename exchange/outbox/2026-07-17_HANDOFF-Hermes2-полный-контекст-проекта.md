# HANDOFF: LOCAL_AI_ENGINE → Hermes #2

**От:** Hermes (instance #1)
**Кому:** Hermes (instance #2)
**Дата:** 2026-07-17 ~19:45 MSK
**Тема:** Передача проекта LOCAL_AI_ENGINE — текущее состояние, нюансы, пути, правила

---

## 0. Главное

Ты подхватываешь проект **LOCAL_AI_ENGINE** — SMC trading bot (BTC/ETH/XAUT). Проект в `/c/Users/Asus-pc/LOCAL_AI_ENGINE`, ветка `main`, HEAD=`3f1d2d2`. Всё запушено на GitHub (`VVASILEVSS/LOCAL_AI_ENGINE`), working tree clean.

Пользователь: **Василий** (Vasily). Рабочий язык — **только русский**. Автономный режим («САМ РЕШИ»): не задавать A/B/C/D вопросы, выбирать вариант и выполнять. Backup tag перед любой правкой = обязательно.

Бот **запущен**: PID 22756/4940 (parent+child). Session в Hermes: `proc_7e1edd81800c`. Проверять через `process(action='poll', session_id='proc_7e1edd81800c')`.

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

## 3. COMMИT CHAIN (актуальный main)

```
...→73bc323 (FVG в LLM+TG)
    →9884874 (Z: STRUCT_WINDOW fix D1=100, 4H=150)
    →0b678e6 (FVG TF-priority: M15 excluded, H1=info, H4+D1=primary)
    →8b6bf35 (merge Z)
    →0c54738 (entry normalize →dict + R:R>1.0 rule #20)
    →b4f7f25 (письмо Z #1)
    →a65a3b5 (CRITICAL: entry→fvg_entry rename — Z's shadowing bug)
    →3f1d2d2 (письмо Z #2 — ответ на shadowing) ← HEAD
```

### Что делает каждый коммит

- **`9884874` (Z):** `_STRUCT_WINDOW = {"4h": 150, "1d": 100}` в benchmark_zigzag.py. До этого был `None` → ZigZag брал пивоты из 500 свечей → древние экстремумы (BTC 126199 мая 2025, ETH 4956). После: BTC D1 upper 97932→82828 (хай 06.05.2026), ETH D1 [1503-2463].

- **`0b678e6`:** FVG TF-приоритет в LLM промпте + TG формате. M15 исключён (микро-гэпы путают), H1=info, H4+D1=primary. **Директива user 17.07.2026.** Фильтр на уровне вывода, не детектора.

- **`0c54738`:** (1) `if not isinstance(entry, dict): entry = {}` — защита от LLM возвращающего entry_conditions строкой. (2) Правило 20 в промпте: «R:R must be > 1.0 for aggressive. If <1.0 → no_signal. FORBIDDEN.» (3) Нумерация правил 1-27 без дубликатов.

- **`a65a3b5` (CRITICAL):** `entry` → `fvg_entry` в обоих FVG блоках (`format_json_for_tg()` L2853 + `_format_zigzag_context_compact()` L3001). **Баг нашёл Z**: FVG-цикл `entry = (f"...")` переписывал signal dict `entry = data.get("entry_conditions")` → normalize делал `{}` → `entry.get('aggressive')` = None → сигнал терял aggressive/conservative/current_status → TG показывал «Н/Д». После фикса — не затирается.

### Backup tags (откат)
```
backup/pre-fvg-tf-priority-20260717-180000  → 73bc323  (перед TF-priority)
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
| **M15/5m** | ❌ ИСКЛЮЧЁН | Не показывается (микро-гэпы путают) |
| **H1** | ℹ️ INFO | «общая информация, НЕ основа для прогноза» |
| **H4 + D1** | ⚡ PRIMARY | «серьёзные уровни притяжения» |

**Важно:** фильтр на уровне вывода (ollama_client.py), НЕ детектора. imbalance_detector.py детектит FVG на всех ТФ. Если Z захочет чинить 15M offset в детекторе — его право, но в вывод не возвращать без user-директивы.

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

### 7.2. Контаминация (BUG 3)

Z сказал: «после STRUCT_WINDOW фикса разрешится». В логах бота виден `CONTAMINATION FIX` на XAUT (1H lower=4H lower → ZigZag fix). Но в текущих зонах XAUT:
```
1D: R=4863.7 S=3942.9
4H: R=4189.4 S=3942.9   ← lower совпадает с 1D
1H: R=4097.1 S=3971.4   ← НЕ совпадает (норм)
15M: R=4008.5 S=3971.4
```
4H lower = 1D lower = 3942.9 — **контаминация осталась** на 4H. Z BUG 3 не полностью закрыт. Это к Z (его structure.py).

### 7.3. MT5 v1.18 кэширование Trade Levels

**Наблюдено 17.07 ~19:29:** MT5 показывал старый aggressive_breakout сигнал (entry=3968.33, SL=4027.10, TP1=3942.90, R:R=0.43, id=267) полчаса-час после того, как API уже отдаёт `no_signal`. User подтвердил: «этот сигнал уже давно висит, полчаса или час назад, сейчас пропад». MT5 синхронизировался.

**Бэкенд работает правильно** (API + файл `signals_XAUTUSDT.json` актуальны). Проблема в MT5-индикаторе (DrawTradeLevels) — кэширует entry/SL/TP и не очищает при `no_signal`. Если повторится — чинить MT5 сторону (код MQ4/MQ5, у user).

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

### Что верифицировано (на 19:40 MSK 17.07)
40/40 PASS ad-hoc:
- Синтаксис 3 модулей
- fvg_entry в обоих местах (нет shadowing)
- нет китайского артефакта 不结构性
- entry normalize guard + R:R>1.0 rule + rules 20-27 уникальны
- signal preserved with FVG (aggressive/conservative/current_status не теряются)
- FVG TF-priority (M15 absent, D1+4H ⚡, H1 ℹ️, labels) в TG и LLM
- Edge cases: entry string, entry missing, no_signal path

### Что проверено на боте
- После restart (id>=270): 8 сигналов, все `no_signal`, R:R=None. **Правило R:R>1.0 работает** (до правила были 6 aggressive с R:R=0.01-0.67).
- `signal_log` сохраняется без AttributeError (до фикса падал).
- `CONTAMINATION FIX` работает на XAUT.

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

1. Бот работает, HEAD=`3f1d2d2`, всё запушено, backup tags на месте.
2. entry shadowing FIXED (`a65a3b5`). R:R>1.0 правило работает. FVG TF-priority работает.
3. **Ждём ответ Z** по откату `438453c` (Hermes против, Z за, user сказал «обсудить в письме»).
4. **Контаминация 4H lower=1D lower** на XAUT осталась — к Z (structure.py).
5. **MT5 кэширование** старого сигнала — не баг бота, MT5 сам обновился.
6. 24h стабилизация (Z предложил) — пусть бот поработает.
7. Дальше: MT5 v1.18 визуальная проверка, trend lines, угол наклона (user хочет 3-ю размерность).

Удачи. Если сомневаешься — пиши Z (через outbox/), user одобряет важное.

— Hermes #1
