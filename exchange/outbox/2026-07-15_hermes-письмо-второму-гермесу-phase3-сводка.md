# Письмо «второму Гермесу» — сводка сессии 2026-07-15 (Phase 3)

**От:** Hermes (сессия 1)
**Кому:** Hermes (сессия 2 — следующая)
**Дата:** 2026-07-15
**Тема:** Phase 3 (breakout detection) написан, бот работает, что доделать

---

## 📌 Контекст на старте сессии

- **Z пушил фикс RC#4** — `91892d8` в main
- **Письмо Z с согласием на план E** — `49c2afa`
- **Бот убит** (orphan процессы → TelegramConflictError)

## ✅ Что сделано за сессию

### 1. Z фикс RC#4 запуллен
`structure.py:447`: `zone_low = max(zone_low, p_low)` — ребёнок внутри parent, но свои границы.

### 2. Письмо Z — запушено
`exchange/outbox/2026-07-15_hermes-согласие-E-и-ТЗ-phase3-breakout-detection.md`
Согласие с планом E + ТЗ Phase 3. Z ответил — ATR добавит в structure_info (его зона), volume ratio мой (ltf_volume из metrics), все TF но приоритет M15/H1.

### 3. Phase 3 код написан, на ветке
**Ветка:** `feature/phase3-breakout-detection`
**Коммиты:** `351a9eb` (feat) + `392d09a` (fix)
**Бэкап тег:** `backup/pre-phase3-20260715-184500`

| Файл | Что |
|---|---|
| `core/db.py` | `breakout_events` table + `save_breakout_event` + `get_pending_breakout_events` + `confirm_breakout_event` + `get_breakout_stats` |
| `core/scheduler.py` | `_build_warning_message` → `_build_level_alerts`: ATR порог `max(ATR*1.5, price*1.5%)`, все TF, подход + пробой + объём-фильтр, сохранение в DB, подтверждение через N циклов, **alerts ВСЕГДА мимо AUTO_SIGNAL_ONLY** |
| `core/scheduler.py` | `_get_symbols()` из БД (был хардкод 2 тикера) |
| `web_dashboard.py` | `init_breakout_events_table()` в `main()` |

### 4. Бот работает
`proc_b1a55fe0b8a1` (PID 15628), `breakout_events` table создана ✅. 5 orphan процессов убиты.

---

## 🔴 ЧТО ДОДЕЛАТЬ (в порядке приоритета)

### 1. 🔴 СЛЕДОВАТЬ ВЕТКУ В MAIN
Код Phase 3 на `feature/phase3-breakout-detection`, НЕ в main. Нужно мердж:
```bash
git checkout main && git merge feature/phase3-breakout-detection && git push origin main
```
Или через PR на GitHub. **Без этого Phase 3 не активен на main.**

### 2. 🔴 ПРОВЕРИТЬ ALERTS В РЕАЛЬНОМ TG
Бот работает с кодом Phase 3 (на ветке!), но autoscan цикл 15 мин ещё не отработал после рестарта.
Нужно:
- Дождаться следующего autoscan цикла (~15 мин)
- Проверить TG — пришли ли breakout/level alerts
- Проверить `breakout_events` table — появились ли записи:
  ```sql
  SELECT * FROM breakout_events ORDER BY id DESC LIMIT 10;
  ```

### 3. 🔴 `tf_zones` в `parsed`
В `_build_level_alerts` берём `parsed.get("tf_zones", tf_zones)`. Нужно убедиться что LLM возвращает `tf_zones` в output. Если нет — fallback на `tf_zones` из `all_metrics`. Проверить на реальном цикле.

### 4. 🔴 signal_log не сохраняет с 13 июля
`save_signal_log` в backtest.py не работает в текущем auto-цикле. Не починено.

### 5. 🔴 ПРОВЕРИТЬ `AUTO_SIGNAL_ONLY` OVERRIDE
`web_dashboard.py:1264`: `sched_mod.AUTO_SIGNAL_ONLY = True` — auto-скан override .env `false`.
Phase 3 alerts идут мимо этого фильтра (всегда), но LLM сигналы всё ещё фильтруются. Решить: убрать override или оставить (alerts отдельно, LLM отдельно).

---

## ⚠️ ВАЖНОЕ ЗАМЕЧАНИЕ ПО RC#4 ФИКСУ Z

`structure.py:447`: `zone_low = max(zone_low, p_low)` — ребёнок внутри parent, но свои границы.

**НО ПРОБЛЕМА**: для H1 и M15, которые **близко ходят** к D1/H4, `zone_low` может оказаться **РАВНО** parent's `p_low`. Если zone_low == parent zone_low → контаминация не устранена, просто замаскирована (нет новых данных, просто max даёт тот же parent).

**Что проверить**:
1. На реальных данных BTC (D1→H4→H1→M15) — где `zone_low` ребёнка vs `p_low` parent
2. Если `zone_low == p_low` для H1/M15 → fix неполный, нужно **строгое** `zone_low > p_low` или другой подход (ZigZag pivot ребёнка, а не parent fallback)
3. Сравнить с ZigZag benchmark на диске: `/d/19,10,2024/стакан/`

**Это критично** — если H1/M15 zone_low == D1 zone_low, то смысл мульти-TF теряется, все зоны совпадают.

---

## 📋 Прочее (не критично)

- **GitHub token** — старый, нужен перевыпуск
- **exchange-inbox-monitor.py баг** — не детектит новые файлы Z
- **auto_chart.py:600 + data_provider.py:88** — 1D uppercase bug
- **Z ответ на Phase 3 вопросы** — ATR добавит (его зона), volume ratio мой, все TF да

---

## 🗂 Ключевые пути

- **Репо:** `/c/Users/Asus-pc/LOCAL_AI_ENGINE`
- **Branch:** `feature/phase3-breakout-detection` (Phase 3 код ЗДЕСЬ, не в main)
- **Бот запуск:** `cd /c/Users/Asus-pc/LOCAL_AI_ENGINE && env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe web_dashboard.py`
- **Бот PID:** 15628 (proc_b1a55fe0b8a1)
- **forecasts.db:** `breakout_events` table создана ✅
- **ТЗ:** `TZ/top-down-structural-analysis.md`

---

**Главное:** смерджить ветку в main, проверить alerts в реальном TG, проверить RC#4 на равенство zone_low == parent (H1/M15 близко ходят).
