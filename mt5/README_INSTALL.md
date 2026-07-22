# MT5 индикатор SMC Zones — установка

## Что это

Индикатор для MetaTrader 5, рисует зоны SMC (support/resistance) по всем таймфреймам + стрелки пробоев. Данные берёт из LOCAL_AI_ENGINE бота через HTTP API.

## Установка (5 шагов)

### Шаг 1. Скопировать файл индикатора

Файл: `LOCAL_AI_ENGINE/mt5/SMC_Zones_Indicator.mq5`

Скопировать в папку MT5:
```
C:\Users\<user>\AppData\Roaming\Meta\Terminal\<hash>\MQL5\Indicators\
```

Найти точный путь: в MT5 → **File → Open Data Folder** → `MQL5/Indicators/`

### Шаг 2. Скомпилировать

Открыть `SMC_Zones_Indicator.mq5` в MetaEditor → нажать **F7** (Compile). Должно быть 0 errors.

### Шаг 3. Разрешить WebRequest (КРИТИЧЕСКИ ВАЖНО)

MT5 → **Tools → Options → Expert Advisors**:
- ✅ **Allow WebRequest for the following URL**
- Добавить: `http://localhost:5000`
- Нажать Add, убедиться что URL в списке

Без этого индикатор не сможет получать данные.

### Шаг 4. Запустить бота

Бот должен работать (Flask на `localhost:5000`). Команда:
```bash
cd /c/Users/Asus-pc/LOCAL_AI_ENGINE
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe web_dashboard.py
```

Проверка: `curl http://localhost:5000/api/signals` — должен вернуть JSON.

### Шаг 5. Установить индикатор на график

В MT5: **Navigator → Indicators → SMC_Zones_Indicator** → перетащить на график BTCUSDT (или любой символ).

Параметры индикатора:
| Параметр | Значение по умолчанию | Описание |
|---|---|---|
| `ServerURL` | `http://localhost:5000/api/signals` | URL API бота |
| `TargetSymbol` | `BTCUSDT` | Символ (как в боте) |
| `PollSeconds` | `30` | Частота опроса (сек) |
| `ShowTFs` | `15m,1h,4h,1D` | Какие ТФ рисовать |
| `ColorUpper` | Red | Цвет resistance |
| `ColorLower` | Green | Цвет support |
| `ColorBreak` | Gold | Цвет стрелок пробоя |
| `LineWidth` | 1 | Толщина линий |

## Что рисует индикатор

- 🔴 **Красные линии** — resistance (upper) по каждому ТФ
- 🟢 **Зелёные линии** — support (lower) по каждому ТФ
- 🔵 **Голубая пунктир** — текущая цена
- 🟡 **Золотые стрелки** — пробои уровней
- **Бейдж в углу** — символ, цена, статус сигнала, фаза

## Траблшутинг

**"WebRequest failed"** — не разрешён URL в Step 3.
**"symbols": {}** — бот не сделал ещё скан. Подождать 15 мин (autoscan цикл) или запустить `/scan BTC` в TG боте.
**Старые данные** — проверь `timestamp` в JSON, если старый — перезапусти бота.
**Индикатор не рисует** — проверить что TargetSymbol совпадает с тем что бот сканирует.
