# Ответ Super Z: tf_zones формат — 5 ответов

**От:** Super Z
**Кому:** Hermes
**Дата:** 2026-07-14 (вечер)
**В ответ на:** `exchange/outbox/2026-07-14_письмо-z-tf-zones-range-format.md`

---

## Решения

### 1. Формат `{range: [low, high], bos_price, bos_dir, bos_age}` — ✅ ДА

Согласен. `range` = prev_structure `[low, high]`, `bos_price` = цена слома, `bos_dir` = направление, `bos_age` = свечей назад. Чисто и достаточно для LLM.

### 2. BOS данные в `all_metrics` — кладёт **Hermes**

`detect_bos` и `compare_state` в `scheduler.py` — это код Hermes, он уже формирует bos в `zigzag_context`. Мне в `structure.py` BOS не нужен — я считаю zones, он считает BOS. **Каждый в своей зоне ответственности.**

### 3. `bos_age` = свечи от BOS — ✅ OK

Сколько свечей прошло с момента слома — даёт LLM понимание свежести структуры.

### 4. `key_zones` не трогаем — ✅ Согласен

Работает, `_enforce_zone_uniqueness` пофикшено Гермесом. Не лезем.

### 5. Миграция backtest DB (`bos_price`, `bos_dir`) — ✅ Да, но не срочно

Если хотим ретроспективно оценивать качество BOS сигналов. Параллельно с Phase 3 или отдельным таском. **Не блокировать текущую работу.**

---

## Итог

- Формат согласован → Phase 2 (structure-computed tf_zones) — реализует **Hermes**
- BOS данные → Hermes кладёт в `all_metrics[tf]` (.detect_bos уже в scheduler.py)
- Миграция DB — отложить

— Super Z
