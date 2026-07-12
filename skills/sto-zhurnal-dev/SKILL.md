---
name: sto-zhurnal-dev
description: |
  Skill для разработки десктопного приложения «СТО · Журнал заказ-нарядов»
  на Python + tkinter + SQLite для рынка Казахстана (коммерческий продукт).
  Использовать при любой разработке, багфиксах, улучшениях UI/UX, добавлении
  функций в проект STO Zhurnal. Включает готовые решения для типичных проблем
  tkinter, SQLite, криптографии, лицензирования, 1С-интеграции.
  Триггеры: «СТО Журнал», «STO Zhurnal», «заказ-наряд», «tkinter тёмная тема»,
  «SQLite кириллица», «RSA лицензия», «1С экспорт», «AutocompleteEntry»,
  «автозаполнение tkinter», «печать HTML из tkinter», «PIL логотип».
---

# Скиллы для разработки STO Zhurnal

## Контекст проекта

Коммерческое десктопное ПО для СТО (1-5 рабочих мест), Python 3.10+, tkinter, SQLite.
Монетизация: подписка (5000-15000 тг/мес) или бессрочная лицензия (30000-50000 тг).
Репозиторий: `github.com/VVASILEVSS/STO_Zhurnal` (приватный).
Текущая версия: `v1.0.0-beta` (тег в Git).

---

## 1. КРИТИЧЕСКИЕ ПРАВИЛА (выучить наизусть)

### 1.1. НИКОГДА не использовать SQL `LOWER()` для кириллицы

**Проблема:** SQLite `LOWER()` работает ТОЛЬКО с латиницей (A-Z).
`LOWER('Иванов')` → `'Иванов'` (без изменений). Поиск по `'иванов'` не находит `'Иванов'`.

**Решение:** Вся регистронезависимая фильтрация — в Python, не в SQL:
```python
# ПРАВИЛЬНО:
cur = conn.execute("SELECT id, fio, phone FROM clients LIMIT 500")
for row in cur.fetchall():
    if search_lower in (row[1] or '').lower():
        results.append(row)

# НЕПРАВИЛЬНО (не работает с кириллицей):
cur = conn.execute("SELECT ... WHERE LOWER(fio) LIKE ?", (f'%{search.lower()}%',))
```

### 1.2. НИКОГДА не смешивать `pack()` и `grid()` в одном контейнере

**Проблема:** TclError: cannot use geometry manager grid inside which already has slaves managed by pack.

**Решение:** В каждом `Frame`/`LabelFrame`/`Toplevel` использовать ТОЛЬКО один менеджер:
- `pack()` — для простых布局 (вертикальный/горизонтальный стек)
- `grid()` — для табличных布局
- Если нужны оба — создать вложенный `Frame` для второго менеджера

### 1.3. НИКОГДА не использовать `file://` (2 слеша) для Windows

**Проблема:** `webbrowser.open(f'file://{path}')` не работает на Windows.

**Решение:** Всегда 3 слеша:
```python
webbrowser.open(f'file:///{tmp.name}')
```

### 1.4. Тёмная тема — `option_add()` для ВСЕХ tk.* виджетов

**Проблема:** `ttk.Style` настраивает только ttk.* виджеты. Обычные `tk.Frame`, `tk.Label`,
`tk.Button`, `tk.Toplevel` остаются белыми на Windows.

**Решение:** В `setup_dark_theme()` — 30+ `option_add()` вызовов:
```python
root.option_add('*Background', Colors.BG)
root.option_add('*Foreground', Colors.TEXT)
root.option_add('*Entry.Background', Colors.PANEL)
root.option_add('*Menu.Background', Colors.PANEL)
root.option_add('*Menu.activeBackground', Colors.ACCENT)
# ... и т.д. для всех типов виджетов
```

### 1.5. `try/except` в КАЖДОМ обработчике кнопок

**Проблема:** Ошибка в одном обработчике → крах всего приложения.

**Решение:**
```python
def _on_new_order(self):
    try:
        from sto_app.ui.order_card import OrderCard
        card = OrderCard(self, self.conn, order=None, on_save=self._refresh_orders)
        self.wait_window(card)
    except Exception as e:
        logger.error('New order error: %s', e, exc_info=True)
        show_error(self, 'Ошибка', f'Не удалось открыть карточку заказа:\n{e}')
```

### 1.6. Логи рядом с программой (НЕ в %APPDATA%)

```python
log_dir = BASE / 'logs'  # D:\STO_Zhurnal\logs\app.log
```

---

## 2. ГОТОВЫЕ РЕШЕНИЯ (паттерны)

### 2.1. AutocompleteEntry — автозаполнение

Готовый виджет в `sto_app/ui/widgets.py`. Особенности:
- `ButtonRelease-1` (НЕ `Button-1`) — клик сначала выделяет, потом срабатывает
- `nearest(event.y)` — fallback если `curselection()` пуст
- `on_select` callback — вызывается при выборе, заполняет другие поля
- Задержка `_on_focus_out` — 250мс (клик по listbox успевает сработать)
- Порог — 1 символ (не 2)

```python
entry = AutocompleteEntry(parent,
                          get_suggestions=self._search_clients,
                          on_select=self._on_client_selected,
                          width=40)
```

### 2.2. Поиск клиентов (ФИО + телефон без пробелов)

```python
# Телефон нормализуется — убираем пробелы/дефисы/скобки
prefix_clean = prefix.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')

# Загружаем клиентов, фильтруем в Python (LOWER не работает с кириллицей)
cur = conn.execute("SELECT id, fio, phone, iin FROM clients ORDER BY fio LIMIT 500")
for row in cur.fetchall():
    fio_lower = (row[1] or '').lower()
    phone_clean = ''.join(c for c in (row[2] or '') if c.isdigit() or c == '+').lower()
    if prefix_lower in fio_lower or prefix_clean in phone_clean:
        results.append(...)
```

### 2.3. ScrollableWindow — прокручиваемые окна

Базовый класс для ВСЕХ модальных окон. Кнопки внизу ВСЕГДА видны:
```python
class OrderCard(ScrollableWindow):
    def __init__(self, parent, conn, order=None, on_save=None):
        super().__init__(parent, title='...', width=950, height=780)
        # Виджеты в self.content_frame
        # Кнопки: self.add_button('Сохранить', self._on_save)
```

### 2.4. Печать заказ-наряда (HTML из шаблона)

```python
from sto_app.reports import print_order
print_order(conn, order, is_trial=False)
# Рендерит sto_order.html с плейсхолдерами {{STO_NAME}}, {{CLIENT_FIO}}, ...
# file:/// (3 слеша для Windows)
```

### 2.5. Логотип СТО в шапке (Base64 → PIL → tkinter)

```python
import base64
from PIL import Image, ImageTk
from io import BytesIO

logo_b64 = db.get_setting(conn, 'sto_logo_b64', '')
if logo_b64:
    img_data = base64.b64decode(logo_b64)
    img = Image.open(BytesIO(img_data))
    img = img.resize((48, 48), Image.Resampling.LANCZOS)
    self._logo_photo = ImageTk.PhotoImage(img)
    ttk.Label(header, image=self._logo_photo).pack(side='left', padx=5)
```

### 2.6. 1С-экспорт CSV (Windows-1251, разделитель ;, НДС 16%)

```python
# НДС настраиваемый: 16 (по умолчанию), 12 (старая), 0, None (без НДС)
def export_1c_csv(conn, orders, output_path, nds_rate=16):
    with open(output_path, 'w', encoding='windows-1251', newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['НомерДокумента', 'Дата', 'Контрагент', 'ИИН', ...])
```

### 2.7. Export_path — настраиваемая папка выгрузки

```python
export_dir = db.get_setting(conn, 'export_path', '')
if export_dir:
    path = os.path.join(export_dir, f'1c_export_{timestamp}.csv')
else:
    path = filedialog.asksaveasfilename(...)
```

### 2.8. Перевод латинских enum'ов на русский в UI

```python
# В combobox'ах используем русские названия
status_map = {'Принят': 'accepted', 'В работе': 'in_progress', 'Готов': 'ready', ...}
# При сохранении — конвертируем обратно
order.status = self._status_map.get(self._status_var.get(), 'accepted')
# При загрузке — конвертируем enum в русское
self._status_var.set(self._status_reverse.get(order.status, 'Принят'))
```

---

## 3. СТРУКТУРА ПРОЕКТА

```
sto_app/
├── __init__.py          — версия, PUBLIC_KEY_PEM, BUILD_SECRET
├── main.py              — точка входа (CLI, smoke-test, init_demo)
├── models.py            — 16 дата-классов
├── db.py                — SQLite: 20 таблиц, CRUD, backup, merge, search_*
├── security.py          — пароли (Argon2id), права (4 роли), маскировка, аудит
├── license.py           — HW ID (3-факторный), RSA-2048 JWT, счётчик, blacklist
├── sync.py              — Drive/WebDAV/SMB, push/pull/merge
├── reports.py           — статистика, CSV/JSON/1С/CommerceML/HTML, print_order
├── notify.py            — WhatsApp (wa.me), Kaspi QR, обновления
├── logs.py              — RotatingFileHandler, audit, integrity, SMTP
├── ui/
│   ├── widgets.py       — Colors, Fonts, ScrollableFrame/Window, AutocompleteEntry, ...
│   ├── main_window.py   — главное окно
│   ├── order_card.py    — карточка заказ-наряда (4 вкладки)
│   ├── settings.py      — настройки (8 вкладок)
│   ├── license_dialog.py — активация лицензии
│   ├── stats_window.py  — статистика
│   └── login_window.py  — экран входа
└── templates/
    ├── sto_order.html   — A4 шаблон заказ-наряда
    ├── online_sto.html  — веб-версия
    ├── oferta.txt       — оферта
    ├── policy.txt       — политика конф.
    ├── consent_pd.txt   — согласие на ПД
    ├── consent_telemetry.txt — согласие на телеметрию
    ├── nda_template.txt — NDA
    └── changelog.txt    — история версий

tests/                   — 325 unit-тестов
tools/                   — blacklist_publisher.py
build_bundle.py          — конкатенация модулей + BUILD_SECRET
license_gen.py           — генератор лицензий (для продавца)
ZAPUSTIT.bat / SOBRAT_EXE.bat / INSTALL.iss / CHITAJ.txt / README.txt
```

---

## 4. ЦВЕТОВАЯ СХЕМА (тёмная тема)

```python
BG = '#0d1520'       # Основной фон (тёмно-синий)
PANEL = '#152030'    # Фон панелей
BORDER = '#243447'   # Границы
TEXT = '#e0e0e0'     # Основной текст (светлый)
ACCENT = '#00d1b2'   # Акцент (бирюза) — кнопки, заголовки
MUTED = '#8a9aae'    # Приглушённый текст
GREEN = '#4caf50'    # Готов / успех
YELLOW = '#ffc107'   # В работе / предупреждение
RED = '#f44336'      # Ошибка / отменён / долг
BLUE = '#2196f3'     # Выдан
```

---

## 5. КОМАНДЫ ДЛЯ ЗАПУСКА

### Обновить и запустить (D:\STO_Zhurnal):
```bash
cd /d/STO_Zhurnal && git pull origin fix/ui-improvements && del sto_app\sto_data.db && del sto_app\sto_data.db-wal && del sto_app\sto_data.db-shm && python -m sto_app.main --init-demo
```

### Обновить и запустить (D:\Projects\STO_Zhurnal):
```bash
cd /d/Projects/STO_Zhurnal && git pull origin fix/ui-improvements && del sto_app\sto_data.db && del sto_app\sto_data.db-wal && del sto_app\sto_data.db-shm && python -m sto_app.main --init-demo
```

### Без демо-данных (сохранить существующую БД):
```bash
cd /d/STO_Zhurnal && git pull origin fix/ui-improvements && python -m sto_app.main
```

### Запуск тестов:
```bash
python -m pytest tests/ -q
```

### Smoke-тест:
```bash
python -m sto_app.main --smoke-test
```

---

## 6. ИЗВЕСТНЫЕ ОШИБКИ И ИХ РЕШЕНИЯ

| Ошибка | Причина | Решение |
|--------|---------|---------|
| TclError: grid inside pack | Смешивание менеджеров | Использовать один менеджер в контейнере |
| Белый фон на Windows | ttk.Style не действует на tk.* | `option_add()` для всех виджетов |
| Поиск не находит кириллицу | SQLite LOWER() не работает с русскими | Фильтрация в Python через `.lower()` |
| Кнопки не видны в окне | Нет прокрутки | Наследовать от `ScrollableWindow` |
| Печать показывает отчёт | `_print_order` вызывал `print_orders_summary` | Использовать `print_order()` из reports.py |
| Логотип не отображается | Нет декодирования Base64 → PIL | `base64.b64decode` → `Image.open` → `ImageTk.PhotoImage` |
| Автозаполнение закрывается при клике | `Button-1` срабатывает до выделения | `ButtonRelease-1` + `nearest(event.y)` |
| Телефон не ищется | Пробелы в номере (`+7 777 123 45 67`) | `REPLACE(phone, ' ', '')` или Python-фильтр |
| Латынь в UI (accepted, cash) | Combobox с enum значениями | Словари `_map` / `_reverse` для перевода |
| `file://` не открывает браузер | 2 слеша вместо 3 | `file:///` (3 слеша для Windows) |
| Крах программы при ошибке | Нет try/except | `try/except` в каждом обработчике |
| `ModuleNotFoundError: sto_app.logs` | Запуск не из папки проекта | `cd` в папку с `sto_app/` |

---

## 7. ГОРЯЧИЕ КЛАВИШИ

| Сочетание | Действие |
|-----------|----------|
| Ctrl+N | Новый заказ |
| Ctrl+S | Сохранить (в карточке) |
| Ctrl+P | Печать |
| Ctrl+F | Поиск |
| F5 | Синхронизация |
| Ctrl+H | История клиента |
| Ctrl+Shift+L | Сменить пользователя |
| ESC | Закрыть модальное окно |
| Del | Удалить выбранный заказ |

---

## 8. ДОКУМЕНТАЦИЯ ПРОЕКТА

- `docs/TZ.md` — ТЗ v1.3 (2560 строк, 24 функциональных блока, 152 тест-кейса)
- `docs/ARCHITECTURE.md` — архитектура проекта (750 строк)
- `docs/fixes/2026-07-07.md` — журнал дня 1 (этапы 1-6)
- `docs/fixes/2026-07-08.md` — журнал дня 2 (этапы 7-10)
- `docs/DEMO_SCREENSHOT.txt` — ASCII-арт главного окна
- `PROMPT_STO_FULL.txt` — сводный промт (источник истины, 1580 строк)
- `CHITAJ.txt` — инструкция пользователя
- `README.txt` — для разработчика

---

## 9. АРХИТЕКТУРНЫЕ РЕШЕНИЯ ДЛЯ НОВЫХ МОДУЛЕЙ

### 9.1. Модуль «Мойка» — гибридный подход

Мойка НЕ требует отдельной таблицы сессий для базовой работы:
- Услуги мойки добавляются в `services` с `category = 'Мойка'`
- В заказ-наряде — те же работы, что и ремонт (через `works`)
- Поле `wash_operator_id` в `orders` — для контроля оператора мойки (≠ master_id)
- Таблица `wash_shifts` — только для смен и выручки за смену
- Статистика: разбивка по `services.category` (Ремонт/Мойка/ТО)
- 1С: колонка «КатегорияУслуги» вместо фиксированного «Автосервисные работы»
- UI: кнопка «🚿 Мойка» → быстрое создание заказа с типом «Мойка»

### 9.2. Модуль «Завгар» (Автопарк) — 4 новые таблицы

```sql
fleet_vehicles — машины автопарка (client_id + auto_id + fleet_number + ТО по пробегу)
maintenance_schedule — шаблоны ТО (интервал в км + месяцах + works_template JSON)
inventory — склад запчастей (name, part_number, quantity, min_quantity, avg_cost)
inventory_movements — движение (in/out/adjust, quantity, price, order_id)
```

### 9.3. Склад запчастей — списание

```python
# При сохранении заказа:
if part.source == 'sto':
    # Проверить остаток на складе
    inv = db.get_inventory_by_name(conn, part.name)
    if inv and inv.quantity < part.qty:
        show_warning("Недостаточно на складе!")
    else:
        # Списать
        db.add_inventory_movement(conn, inv.id, 'out', part.qty, order.id)
# part.source == 'client' → НЕ трогать склад
```

### 9.4. Ролевой тулбар (предотвращение перегрузки UI)

```python
# master/junior: Новый, Печать, WhatsApp
# admin: + Экспорт, Статистика, 1С
# owner: + Автопарк, Склад, Настройки
```

### 9.5. График ТО для завгара — таблица с алертами

```
⚠ Красным: "Просрочено по пробегу" / "Просрочено по дате" / "Мало на складе"
🟢 Зелёным: OK (остаток до ТО > 0)
```

### 9.6. План реализации новых модулей

| Этап | Что | Время |
|------|-----|-------|
| 1 | Мойка: категория в services + кнопка | 1 день |
| 2 | Статистика: разбивка по категориям | 0.5 дня |
| 3 | 1С: колонка «КатегорияУслуги» | 0.5 дня |
| 4 | Завгар: таблицы fleet_vehicles + schedule | 2 дня |
| 5 | Завгар: UI «Автопарк» | 2 дня |
| 6 | Склад: таблицы inventory + movements | 2 дня |
| 7 | Склад: списание при source='sto' | 1 день |
| 8 | Склад: UI «Склад» | 2 дня |

---

## 10. ПРАВИЛО ОБНОВЛЕНИЯ СКИЛЛА

При каждой новой правке, багфиксе или архитектурном решении — добавлять запись в этот скилл:
- Раздел 6 «Известные ошибки» — если найден новый баг
- Раздел 2 «Готовые решения» — если создан новый паттерн
- Раздел 9 «Архитектурные решения» — если добавлен новый модуль
- Раздел 5 «Команды» — если изменились пути запуска
