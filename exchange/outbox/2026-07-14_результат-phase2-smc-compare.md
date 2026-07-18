# Результат Phase 2: Сравнение SMC библиотек

**Статус:** DONE

## 2.2 smartmoneyconcepts (joshyattridge, 1855⭐)

**Установлен:** v0.0.27

### Результат сравнения
Синтетические 500 свечей 4H. Одни и те же swing points.

| Метрика | smartmoneyconcepts | Наш detect_bos |
|---|---|---|
| BOS событий | 9 (все) | 1 (последний) |
| CHOCH | 2 события | ❌ нет |
| Последний BOS | bullish @ 80369.0 | bullish @ 80369.0 |
| Совпадение | ✅ | ✅ |

### Вывод
BOS точки **совпадают**. SMC детектирует все сломы + CHOCH, мы только последний.
Разная философия: SMC = индикатор (каждый слом), наш = структурный анализ (prev/curr, zones, top-down).

**РЕКОМЕНДАЦИЯ: НЕ интегрировать как основной BOS.** Наш structure.py лучше для зон и top-down.
CHOCH можно добавить как дополнительный сигнал в P3.

## 2.3 pymarket-structure (fortunato/pymarket-structure)

**Установлен** из vendor/ (Гермес положил, обход Git бана).

### Результат: НЕ подходит для интеграции

Библиотека жестко привязана к freqtrade:
- Требует колонку `open_time` (freqtrade формат)
- Требует `tsi_hist` (гистограмма TSI индикатора)
- API: `attach_market_structure(df, metadata, store)` — не standalone
- MTF функция требует `date` колонку + DatetimeIndex

Попытка запуска на нашем OHLCV кеше (BTC 1D, 500 свечей):
```
KeyError: 'tsi_hist' → добавили → KeyError: 'open_time'
```

Нужна обёртка для конвертации данных. Но даже с ней — 67 ms_* колонок рассчитаны для freqtrade бэктестов (zone quality score, retest mode), не для LLM narrative.

**РЕКОМЕНДАЦИЯ: НЕ интегрировать.** Слишком freqtrade-специфичная.
Единственная полезная фича — `lookahead_bias=False` в MTF (предотвращение подглядывания). У нас это решается архитектурно (top-down от старшего ТФ).

## Общая рекомендация по Phase 2

**Ничего не меняем в основном коде.** Наш ZigZag + structure.py справляется.
SMC библиотеки не дают преимущества для нашей архитектуры (top-down + zones + LLM narrative).
CHOCH из smartmoneyconcepts можно рассмотреть в P3 как фича-дополнение.