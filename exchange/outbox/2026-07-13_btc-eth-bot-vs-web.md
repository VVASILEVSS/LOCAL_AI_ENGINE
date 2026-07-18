# Отчёт: BTC + ETH через бота и веб-дашборд — сравнение качества

**Дата:** 2026-07-13
**Автор:** Hermes
**HEAD:** `c0f8228` (D:\LOCAL_AI_ENGINE)

---

## 1. Запуск

| Параметр | Значение |
|----------|----------|
| Бот | `@KXROBObot` (pid 31252) |
| LM Studio | qwen2.5-vl-7b-instruct, localhost:1234 |
| ТФ | 1D, 4h, 1h, 15m |
| Планировщик | 60 мин |
| auto_mode | false |
| DNS фикс | ThreadedResolver monkey-patch |

## 2. BTC/USDT — результат через бота

**Команда:** `/scan BTC` → `2026-07-12 23:33:25`

| Параметр | Значение |
|----------|----------|
| Run 1 (temp=0.15) | no_signal, 6481 tokens |
| Run 2 (temp=0.25) | no_signal, 6513 tokens |
| Self-consistency | 2/2 agreed ✅ |
| LLM price | (в raw JSON) |
| Binance live | $64,129.44 |
| signal_log | не сохранён (no_signal → нет active signal) |

## 3. ETH/USDT — результат через бота

**Команда:** `/scan ETH` → `2026-07-12 23:36:06`

| Параметр | Значение |
|----------|----------|
| Run 1 (temp=0.15) | no_signal, 6435 tokens |
| Run 2 (temp=0.25) | no_signal, 6495 tokens |
| Self-consistency | 2/2 agreed ✅ |
| LLM price | $1,819.02 |
| Binance live | $1,821.50 |
| Расхождение | $2.48 (0.14%) — норма |
| signal_log | не сохранён (no_signal) |

## 4. Веб-дашборд

**Статус:** Запущен, но **конфликт с ботом** — web_dashboard.py запускает второй экземпляр бота в фоновом потоке → `TelegramConflictError`. Решение: веб-дашборд должен запускаться **без** автозапуска бота, только для чтения статистики.

**Данные из БД (через веб-дашборд /api/stats):**
- signal_log: 0 записей (no_signal не сохраняется)
- forecasts: 0 записей
- pending: 0
- checked: 0
- accuracy: N/A (нет проверенных прогнозов)

## 5. Сравнение качества: Bot vs Web

| Критерий | Bot (TG) | Web Dashboard |
|----------|----------|----------------|
| Анализ BTC | ✅ no_signal 2/2 | ❌ конфликт (двойной бот) |
| Анализ ETH | ✅ no_signal 2/2 | ❌ конфликт |
| Цена BTC | из Binance API | из SQLite (0 записей) |
| Цена ETH | $1,819.02 (LLM) | нет данных |
| Зоны TF | LLM + metrics | нет данных |
| signal_log | 0 (no_signal) | 0 |
| Self-consistency | 2/2 agreed | N/A |
| LLM tokens | 12,994 (BTC) + 12,930 (ETH) | N/A |

**Проблема:** Веб-дашборд не может работать одновременно с ботом — два процесса делают `getUpdates`. Нужно разделить: веб-дашборд только читает БД, не запускает бота.

## 6. Актуальность данных

| Параметр | Значение | Статус |
|----------|----------|--------|
| BTC 1h last candle | 2026-07-12 18:00 UTC | ✅ свежая |
| ETH 1h last candle | 2026-07-12 18:00 UTC | ✅ свежая |
| BTC live price | $64,129.44 (Binance) | ✅ |
| ETH live price | $1,821.50 (Binance) | ✅ |
| ETH LLM price | $1,819.02 | ✅ ($2.48 расхождение — норма) |
| CSV cache TTL | обновляется при каждом `/scan` | ✅ |
| State files | BTCUSDT_15M.json, ETHUSDT_15M.json | ✅ |
| LM Studio | HTTP 200, модель загружена | ✅ |

## 7. Итог

**Бот работает корректно:**
- ✅ BTC + ETH проанализированы через `/scan`
- ✅ Self-consistency 2/2 agreed на `no_signal` для обоих
- ✅ LLM цена ETH ($1,819) близка к live ($1,821)
- ✅ Данные актуальны (свечи 18:00 UTC, цены live)
- ✅ State files созданы

**Веб-дашборд требует доработки:**
- ❌ Конфликт double-bot — нужно убрать автозапуск бота из web_dashboard.py
- ❌ Нет данных для отображения (signal_log пуст, т.к. no_signal не сохраняется)

**Рекомендации:**
1. web_dashboard.py: убрать `start_bot_thread()` из `__main__` — только чтение БД
2. Рассмотреть сохранение ВСЕХ прогнозов в signal_log (не только active signals) для статистики
3. Добавить `last_analysis_time` трекинг в БД для веб-дашборда
