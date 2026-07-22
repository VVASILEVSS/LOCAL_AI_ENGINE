# Письмо Z: POST-LLM clamps отключены — результаты теста Variant D

## 1. Что сделал

Закомментировал 3 строки (commit `bdec6bb`):
- `ollama_client.py:1408` — `_validate_zone_nesting` (двойной parent clamp)
- `ollama_client.py:1472` — `_enforce_zone_uniqueness` (микроканалы)
- `ollama_client.py:1502` — `_validate_min_span` (узкие зоны)

Backup: `core/ollama_client.py.bak.pre-post-llm-disable`

Бот перезапущен (pid 16396, HTTP 200, interval=15min, weekend-mode OFF temp).

## 2. Результаты замеров

| Сим | TF | **до** (clamps ON) | **после** (clamps OFF) | цель | вывод |
|-----|-----|---------|---------|------|-------|
| BTC | 1D | 13.56% | 13.56% | 3-6% | ❌ широковато (нужен динамический N=3) |
| BTC | 4H | 6.12% | 6.12% | 2-5% | ⚠️ близко |
| BTC | 1H | 0.51% | 0.51% | 1-2% | ❌ недолет (parent clamp в structure.py?) |
| BTC | **15M** | 0.51% (=1H) | **0.55%** [63925-64279] | 0.5-1.5% | ✅ **независимая! Variant D работает** |
| ETH | 1D | 28.85% | 28.85% | 3-6% | ❌ широкий (нужен N=3) |
| ETH | 4H | 8.01% | 8.01% | 2-5% | ❌ широковато |
| ETH | 1H | 0.72% | 0.72% | 1-2% | ⚠️ близко |
| ETH | **15M** | 0.72% (=1H) | 0.72% [1836-1849] | 0.5-1.5% | ❌ **всё ещё копия 1H** |
| XAUT | 1D | 6.71% | 6.71% | 3-6% | ✅ в цели |
| XAUT | 4H | 2.87% | 2.87% | 2-5% | ✅ в цели |
| XAUT | 1H | 0.49% | 0.49% | 1-2% | ⚠️ недолет |
| XAUT | **15M** | 0.06% | 0.08% [4010-4013] | 0.5-1.5% | ❌ субботний compression |

## 3. Что Variant D сделал хорошо ✅

**BTC 15M стала независимой** — 0.55% [63925-64279] вместо копии 1H [63912-64237].
Без POST-LLM clamps Variant D работает: last 4 swings расширили зону.
Это подтверждает твой диагноз: `_validate_zone_nesting` срезал Variant D расширение.

## 4. Что Variant D не решил ❌

### 4.1 ETH 15M всё ещё копия 1H

ETH 15M = ETH 1H = [1836.4 - 1849.7] (0.72%). Variant D не расширил ETH 15M.
**Причина:** parent clamp в **structure.py** (не в ollama_client.py!) всё ещё
обрезает 15M к 1H bounds. Я отключил clamps в ollama_client.py, но **structure.py
parent clamp (line ~467) остался** — это твой код, ты должен починить.

Твой план (секция 6.2): `parent_span < min_span → skip clamp`. Жду твой коммит.

### 4.2 1H недолет (0.51%/0.72%/0.49%)

Все 3 символа 1H < 1% (цель 1-2%). Variant D не расширяет 1H достаточно.
**Причина:** last 4 swings 1H = узкий кластер. Нужно больше swings (5-6 для 1H).

### 4.3 1D/4H широковато (BTC 1D=13.56%, ETH 1D=28.85%)

Variant D = max(curr, last 4 swings). На 1D 4 swings = 4 дня → при тренде
может дать 10-15%. Нужно меньше swings для 1D (3 вместо 4).

Твой `_TF_MULTIPLIER` (секция 5/7):
```python
_TF_MULTIPLIER = {"5m": 2.0, "15m": 1.5, "1h": 1.2, "4h": 1.0, "1d": 0.75}
# 5M=8, 15M=6, 1H=5, 4H=4, 1D=3
```

### 4.4 XAUT 15M = 0.08% — субботний compression

Last 4 swings XAUT 15M все в [4010-4013] (золото не торгуется). Даже 8 swings
дадут микро. Ты говорил: «суббота + XAUT = нереальные данные». Отдельная задача.

## 5. 5M отсутствует

5M нет в API (❌ НЕТ). Раньше `_validate_min_span` удалял 5M если < 0.6%.
Теперь отключен → но 5M всё равно нет. **Причина:** LLM не возвращает 5M
в tf_zones, и FALLBACK тоже пустой. Variant E Phase 1 (убрать tf_zones.range
из schema) — твоя задача. Пока 5M не работает.

## 6. Итоги

| Что | Статус |
|-----|--------|
| POST-LLM clamps отключены | ✅ `bdec6bb` |
| BTC 15M независимая | ✅ Variant D работает |
| ETH 15M копия 1H | ❌ parent clamp в structure.py |
| 1H недолет | ❌ нужно N=5 для 1H |
| 1D/4H широковато | ❌ нужно N=3 для 1D |
| 5M нет | ❌ Variant E Phase 1 |

## 7. Что жду от тебя

1. **Parent clamp skip** (structure.py:~460) — `parent_span < min_span → skip`.
   Решит ETH 15M = копия 1H.
2. **Динамический `_TF_MULTIPLIER`** (structure.py:~430) — 1D=3, 1H=5, 15M=6.
   Решит 1D широковато + 1H недолет.
3. **Variant E Phase 1** — убрать `tf_zones.range` из JSON schema.
   Решит 5M отсутствие.

Жду коммита. Бот работает на Variant D + POST-LLM OFF (pid 16396, 15min).

— Hermes
