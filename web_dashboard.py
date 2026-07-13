"""
LOCAL_AI_ENGINE — Dashboard Bot + Web Dashboard
Бот: @my_hermes_lokal_ai_bot (cloud LLM — Alibaba GLM)
Основной бот: @KXROBObot (local LLM — LM Studio)
Запуск: python web_dashboard.py → http://localhost:5000
"""
from __future__ import annotations

import os
import sys
import threading
import asyncio
import sqlite3
import subprocess
import logging
import time

# Загрузка .env (DASHBOARD_LLM_* для облака)
from dotenv import load_dotenv
load_dotenv()

# Фикс DNS для aiohttp на Windows
import aiohttp
from aiohttp.resolver import ThreadedResolver
_orig_init = aiohttp.TCPConnector.__init__
def _patched_init(self, *a, **kw):
    if 'resolver' not in kw or kw['resolver'] is None:
        kw['resolver'] = ThreadedResolver()
    return _orig_init(self, *a, **kw)
aiohttp.TCPConnector.__init__ = _patched_init

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BotCommand
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from flask import Flask, jsonify, render_template_string

# ── Config ─────────────────────────────────────────────────────────────────

DASH_TOKEN = os.getenv("DASH_TOKEN", "8823603938:AAEQ8IPYIRIXPIXlz0YlCL4nMv82ITTv24w")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "1943427656"))
WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, "forecasts.db")
PYTHON_EXE = os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe")

# ── Cloud LLM config — дашборд-бот использует облако (DASHBOARD_LLM_* из .env) ─
DASHBOARD_LLM_API_KEY = os.getenv("DASHBOARD_LLM_API_KEY", "")
DASHBOARD_LLM_BASE_URL = os.getenv("DASHBOARD_LLM_BASE_URL", "")
DASHBOARD_MODEL_NAME = os.getenv("DASHBOARD_MODEL_NAME", "glm-5.2-fast-preview")

# ── Main bot process management ─────────────────────────────────────────────

main_bot_process: subprocess.Popen | None = None
main_bot_started_at: float | None = None

def start_main_bot() -> bool:
    global main_bot_process, main_bot_started_at
    if main_bot_process and main_bot_process.poll() is None:
        return False
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    main_bot_process = subprocess.Popen(
        [PYTHON_EXE, "main.py"],
        cwd=PROJECT_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    main_bot_started_at = time.time()
    return True

def stop_main_bot() -> bool:
    global main_bot_process
    if not main_bot_process or main_bot_process.poll() is not None:
        return False
    main_bot_process.terminate()
    try:
        main_bot_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        main_bot_process.kill()
    return True

def is_main_bot_running() -> bool:
    return main_bot_process is not None and main_bot_process.poll() is None

# ── DB helpers ──────────────────────────────────────────────────────────────

def _query_db(query, args=()):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(query, args)
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def get_setting(key, default=None):
    rows = _query_db("SELECT value FROM settings WHERE key=?", (key,))
    if rows:
        v = rows[0]["value"]
        if v in ("true", "false"): return v == "true"
        return v
    return default

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit(); conn.close()

def get_stats():
    stats = {
        "bot_status": "🟢 Running" if is_main_bot_running() else "🔴 Stopped",
        "uptime": int(time.time() - main_bot_started_at) if main_bot_started_at else 0,
        "model": DASHBOARD_MODEL_NAME,
        "llm_mode": "cloud" if DASHBOARD_LLM_API_KEY else "local",
        "prompt_variant": os.getenv("PROMPT_VARIANT", "A"),
        "interval": get_setting("interval_minutes", 60),
        "auto_mode": get_setting("auto_mode", False),
    }
    rows = _query_db("""
        SELECT COUNT(*) as total,
            SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'sl_hit' THEN 1 ELSE 0 END) as sl_hits
        FROM signal_log WHERE checked_at IS NOT NULL
    """)
    if rows and rows[0]["total"] and rows[0]["total"] > 0:
        r = rows[0]; total = r["total"]
        stats.update(checked=total, wins=int(r["wins"] or 0),
                      accuracy=round((r["wins"] or 0) / total * 100, 1),
                      sl_rate=round((r["sl_hits"] or 0) / total * 100, 1))
    else:
        stats.update(checked=0, wins=0, accuracy=0, sl_rate=0)
    pending = _query_db("SELECT COUNT(*) as cnt FROM signal_log WHERE checked_at IS NULL")
    stats["pending"] = pending[0]["cnt"] if pending else 0
    last = _query_db("SELECT timestamp, symbol, signal_status, direction, entry_price, sl, tp1, outcome FROM signal_log ORDER BY timestamp DESC LIMIT 10")
    stats["last_signals"] = last
    ab = _query_db("SELECT prompt_variant, COUNT(*) as cnt, SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins FROM signal_log WHERE checked_at IS NOT NULL AND prompt_variant IS NOT NULL GROUP BY prompt_variant")
    stats["ab_variants"] = [{"variant": r["prompt_variant"] or "A", "total": r["cnt"], "wins": r["wins"] or 0} for r in ab]
    return stats

# ── Bot handlers ────────────────────────────────────────────────────────────

dp = Dispatcher(storage=MemoryStorage())

# ── Inline Keyboard ──────────────────────────────────────────────────────────

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

SYMBOLS_SCAN = ["BTCUSDT", "XAUTUSDT"]  # из _get_symbols()

def _main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура /start."""
    scan_btns = []
    for sym in SYMBOLS_SCAN:
        label = sym.replace("USDT", "")
        if label == "XAUT":
            label = "XAUT (золото)"
        scan_btns.append([InlineKeyboardButton(text=f"📊 Анализ {label}", callback_data=f"scan_{sym}")])

    return InlineKeyboardMarkup(inline_keyboard=[
        *scan_btns,
        [InlineKeyboardButton(text="📈 Статистика", callback_data="cmd_stats"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data="cmd_settings")],
        [InlineKeyboardButton(text="🔇 Авто-режим", callback_data="cmd_auto"),
         InlineKeyboardButton(text="🔄 Статус ботов", callback_data="cmd_status")],
        [InlineKeyboardButton(text=_autoscan_button_label(), callback_data="toggle_autoscan")],
    ])


def _autoscan_button_label() -> str:
    interval = get_setting("autoscan_interval", 30)
    active = get_setting("autoscan_active", False)
    if active:
        return f"⏹ Остановить автоскан ({interval} мин)"
    return f"▶ Запустить автоскан ({interval} мин)"


# ── /start с inline keyboard ────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await message.answer(
        "🤖 *Dashboard Bot* — облако (Alibaba GLM)\n\n"
        "Нажми кнопку или введи команду:\n"
        "/scan BTC | ETH | XAUT — анализ через облако",
        reply_markup=_main_keyboard(),
        parse_mode="Markdown",
    )


# ── Callback handlers ────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("scan_"))
async def cb_scan(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    symbol = callback.data.removeprefix("scan_")
    symbol_map = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "XAUT": "XAUTUSDT"}
    full = symbol_map.get(symbol, symbol)
    label = full.replace("USDT", "")
    if label == "XAUT":
        label = "XAUT (золото)"

    await callback.message.edit_text(f"🔍 Анализ {label} через облако ({DASHBOARD_MODEL_NAME})...")
    await _do_scan(callback.bot, full, callback.message.chat.id)
    # Обновить клавиатуру после анализа
    await callback.message.answer("Выбери действие:", reply_markup=_main_keyboard())


@dp.callback_query(F.data == "cmd_stats")
async def cb_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID: return
    s = get_stats()
    text = (f"📈 *Статистика*\n\nПроверено: {s['checked']}\n"
            f"Accuracy: {s['accuracy']}%\nWins: {s['wins']}\n"
            f"SL: {s['sl_rate']}%\nPending: {s['pending']}\n\n")
    if s.get("ab_variants"):
        text += "🧪 *A/B:*\n"
        for v in s["ab_variants"]:
            text += f"  {v['variant']}: {v['wins']}/{v['total']}\n"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_main_keyboard())


@dp.callback_query(F.data == "cmd_settings")
async def cb_settings(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID: return
    rows = _query_db("SELECT key, value FROM settings ORDER BY key")
    text = "⚙️ *Настройки:*\n"
    for r in rows:
        text += f"  {r['key']} = {r['value']}\n"
    if not rows:
        text += "  _(пусто)_\n"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_main_keyboard())


@dp.callback_query(F.data == "cmd_auto")
async def cb_auto(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID: return
    state = not get_setting("auto_mode", False)
    set_setting("auto_mode", state)
    label = "🔇 ON (только сигналы)" if state else "📢 OFF (все анализы)"
    await callback.message.edit_text(f"Авто-режим: {label}", reply_markup=_main_keyboard())


@dp.callback_query(F.data == "cmd_status")
async def cb_status(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID: return
    running = is_main_bot_running()
    uptime = int(time.time() - main_bot_started_at) if main_bot_started_at and running else 0
    pid = main_bot_process.pid if running else "—"
    status = "🟢 Running" if running else "🔴 Stopped"
    cloud = "✅ cloud" if DASHBOARD_LLM_API_KEY else "❌ no key"
    autoscan = get_setting("autoscan_active", False)
    autoscan_iv = get_setting("autoscan_interval", 30)
    text = (
        f"📊 *Статус*\n\n"
        f"Основной бот: {status} (PID {pid}, {uptime//60} мин)\n"
        f"Дашборд: 🟢 Active\n"
        f"  LLM: {DASHBOARD_MODEL_NAME} ({cloud})\n"
        f"  Автоскан: {'🟢 ON' if autoscan else '🔴 OFF'} ({autoscan_iv} мин)\n"
        f"  Авто-режим: {'ON' if get_setting('auto_mode', False) else 'OFF'}"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_main_keyboard())


# ── Автоскан ─────────────────────────────────────────────────────────────────

from apscheduler.schedulers.asyncio import AsyncIOScheduler
autoscan_scheduler = AsyncIOScheduler()


async def _autoscan_cycle(bot: Bot):
    """Циклический анализ всех символов через облако."""
    if not DASHBOARD_LLM_API_KEY:
        logging.warning("autoscan: DASHBOARD_LLM_API_KEY not set, skipping")
        return
    import core.config as cfg
    import core.scheduler as sched_mod
    old_auto = cfg.AUTO_SIGNAL_ONLY
    old_my_chat = cfg.MY_CHAT_ID
    try:
        # Автоскан: AUTO_SIGNAL_ONLY — только сигналы
        cfg.MY_CHAT_ID = ADMIN_CHAT_ID
        sched_mod.AUTO_SIGNAL_ONLY = True
        sched_mod.ACTIONABLE_SIGNALS = ("aggressive_breakout", "retest", "reversal")
        await run_hourly_analysis(
            bot=bot,
            llm_api_key=DASHBOARD_LLM_API_KEY,
            llm_base_url=DASHBOARD_LLM_BASE_URL,
            llm_model=DASHBOARD_MODEL_NAME,
        )
    except Exception as e:
        logging.error(f"autoscan cycle error: {e}")
    finally:
        cfg.AUTO_SIGNAL_ONLY = old_auto
        cfg.MY_CHAT_ID = old_my_chat


def _start_autoscan(bot: Bot) -> bool:
    interval = get_setting("autoscan_interval", 30)
    autoscan_scheduler.add_job(
        _autoscan_cycle, "interval", minutes=interval,
        args=[bot], id="autoscan_job", replace_existing=True,
        max_instances=1, coalesce=True,
    )
    if not autoscan_scheduler.running:
        autoscan_scheduler.start()
    set_setting("autoscan_active", True)
    logging.info(f"Autoscan started: every {interval} min, symbols: {SYMBOLS_SCAN}")
    return True


def _stop_autoscan() -> bool:
    try:
        autoscan_scheduler.remove_job("autoscan_job")
    except Exception:
        pass
    set_setting("autoscan_active", False)
    logging.info("Autoscan stopped")
    return True


@dp.callback_query(F.data == "toggle_autoscan")
async def cb_toggle_autoscan(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID: return
    active = get_setting("autoscan_active", False)
    if active:
        _stop_autoscan()
        text = "⏹ Автоскан остановлен"
    else:
        if not DASHBOARD_LLM_API_KEY:
            await callback.answer("❌ DASHBOARD_LLM_API_KEY не задан", show_alert=True)
            return
        _start_autoscan(callback.bot)
        iv = get_setting("autoscan_interval", 30)
        text = f"▶ Автоскан запущен (каждые {iv} мин)"
    await callback.message.edit_text(text, reply_markup=_main_keyboard())


# ── Настройка интервала автоскана ────────────────────────────────────────────

@dp.message(Command("autoscan"))
async def cmd_autoscan(message: Message):
    """Ручное управление: /autoscan, /autoscan 45, /autoscan off"""
    if message.from_user.id != ADMIN_CHAT_ID: return
    parts = message.text.strip().split()
    if len(parts) >= 2:
        arg = parts[1].lower()
        if arg in ("off", "stop", "0"):
            _stop_autoscan()
            await message.answer("⏹ Автоскан остановлен")
            return
        try:
            minutes = int(arg)
            if minutes < 5:
                await message.answer("❌ Минимум 5 минут")
                return
            set_setting("autoscan_interval", minutes)
            active = get_setting("autoscan_active", False)
            if active:
                _stop_autoscan()
                _start_autoscan(message.bot)
            await message.answer(f"✅ Интервал: {minutes} мин" + (" (автоскан перезапущен)" if active else ""))
            return
        except ValueError:
            pass
    # Без аргументов — текущий статус
    active = get_setting("autoscan_active", False)
    iv = get_setting("autoscan_interval", 30)
    text = f"Автоскан: {'🟢 ON' if active else '🔴 OFF'} ({iv} мин)\n\nУправление:\n/autoscan 30 — интервал\n/autoscan off — стоп"
    await message.answer(text)

@dp.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    running = is_main_bot_running()
    uptime = int(time.time() - main_bot_started_at) if main_bot_started_at and running else 0
    pid = main_bot_process.pid if running else "—"
    status = "🟢 Running" if running else "🔴 Stopped"
    cloud_status = "✅ cloud" if DASHBOARD_LLM_API_KEY else "❌ no key"
    text = (
        f"📊 *Статус*\n\n"
        f"Основной бот (@KXROBObot): {status}\n"
        f"  PID: {pid}\n"
        f"  Uptime: {uptime//60} мин {uptime%60} сек\n"
        f"  Модель: LM Studio qwen2.5-vl-7b-instruct (локальная)\n\n"
        f"Дашборд-бот (@my_hermes_lokal_ai_bot): 🟢 Active\n"
        f"  Модель: {DASHBOARD_MODEL_NAME} ({cloud_status})\n"
        f"  Prompt: variant {os.getenv('PROMPT_VARIANT', 'A')}\n"
        f"  Auto: {'🔇 ON' if get_setting('auto_mode', False) else '📢 OFF'}\n"
        f"  Интервал: {get_setting('interval_minutes', 60)} мин"
    )
    await message.answer(text)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    s = get_stats()
    text = f"📈 *Статистика*\n\nПроверено: {s['checked']}\nAccuracy: {s['accuracy']}%\nWins: {s['wins']}\nSL: {s['sl_rate']}%\nPending: {s['pending']}\n\n"
    if s.get("ab_variants"):
        text += "🧪 *A/B:*\n"
        for v in s["ab_variants"]:
            text += f"  {v['variant']}: {v['wins']}/{v['total']}\n"
    if s.get("last_signals"):
        text += "\n📋 *Сигналы:*\n"
        for sig in s["last_signals"][:5]:
            text += f"  {sig['timestamp'][:16]} | {sig['symbol']} | {sig['signal_status']} | {sig.get('outcome','pending')}\n"
    else:
        text += "\n_(нет данных)_"
    await message.answer(text)

@dp.message(Command("startbot"))
async def cmd_startbot(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    if is_main_bot_running():
        await message.answer("⚠️ Уже запущен")
        return
    ok = start_main_bot()
    await message.answer(f"✅ Запущен (PID {main_bot_process.pid})" if ok else "❌ Ошибка")

@dp.message(Command("stopbot"))
async def cmd_stopbot(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    if not is_main_bot_running():
        await message.answer("⚠️ Уже остановлен")
        return
    ok = stop_main_bot()
    await message.answer("🛑 Остановлен" if ok else "❌ Ошибка")

@dp.message(Command("auto"))
async def cmd_auto(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    state = not get_setting("auto_mode", False)
    set_setting("auto_mode", state)
    text = "🔇 ON (только сигналы)" if state else "📢 OFF (все анализы)"
    await message.answer(f"Авто-режим: {text}")

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    rows = _query_db("SELECT key, value FROM settings ORDER BY key")
    text = "⚙️ *Настройки:*\n"
    for r in rows:
        text += f"  {r['key']} = {r['value']}\n"
    await message.answer(text)

@dp.message(Command("version"))
async def cmd_version(message: Message):
    if message.from_user.id != ADMIN_CHAT_ID: return
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=PROJECT_DIR)
        head = r.stdout.strip()
        r2 = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True, text=True, cwd=PROJECT_DIR)
        await message.answer(f"📌 HEAD: `{head}`\n{r2.stdout.strip()}")
    except Exception as e:
        await message.answer(f"❌ {e}")

@dp.message(Command("scan"))
@dp.message(F.text.lower().startswith("/scan"))
async def cmd_scan(message: Message):
    """Анализ через облако — полный контекст как в scheduler."""
    if message.from_user.id != ADMIN_CHAT_ID: return
    if not DASHBOARD_LLM_API_KEY:
        await message.answer("❌ DASHBOARD_LLM_API_KEY не задан в .env")
        return
    text = message.text.strip()
    for prefix in ("/scan", "/SCAN", "/Scan"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        await message.answer("Использование: /scan BTC | ETH | XAUT", reply_markup=_main_keyboard())
        return
    symbol_map = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "XAUT": "XAUTUSDT"}
    symbol = text.upper().split()[0]
    full = symbol_map.get(symbol, symbol + "USDT" if not symbol.endswith("USDT") else symbol)
    await message.answer(f"🔍 Анализ {full} через облако ({DASHBOARD_MODEL_NAME})...")
    await _do_scan(message.bot, full, message.chat.id)


async def _do_scan(bot: Bot, symbol: str, chat_id: int):
    """Общий код сканирования через облако (для /scan и кнопок)."""
    try:
        from core.scheduler import run_hourly_analysis
        import core.config as cfg
        import core.scheduler as sched_mod
        # Сохраняем оригинальные значения
        old_chat = cfg.MY_CHAT_ID
        old_auto = cfg.AUTO_SIGNAL_ONLY
        try:
            cfg.MY_CHAT_ID = chat_id
            # Ручной /scan ВСЕГДА отправляет результат
            cfg.AUTO_SIGNAL_ONLY = False
            sched_mod.AUTO_SIGNAL_ONLY = False
            await run_hourly_analysis(
                bot=bot,
                symbol_filter=symbol,
                llm_api_key=DASHBOARD_LLM_API_KEY,
                llm_base_url=DASHBOARD_LLM_BASE_URL,
                llm_model=DASHBOARD_MODEL_NAME,
            )
        finally:
            cfg.MY_CHAT_ID = old_chat
            cfg.AUTO_SIGNAL_ONLY = old_auto
            sched_mod.AUTO_SIGNAL_ONLY = old_auto
    except Exception as e:
        await bot.send_message(chat_id, f"❌ {type(e).__name__}: {e}")

# ── Flask web ──────────────────────────────────────────────────────────────

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LOCAL_AI_ENGINE Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#1a1a2e;color:#e0e0e0;padding:20px}
h1{color:#e94560;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:15px;margin-bottom:20px}
.card{background:#16213e;border-radius:10px;padding:15px;border:1px solid #0f3460}
.card h2{color:#e94560;font-size:1.1em;margin-bottom:10px}
.stat{display:flex;justify-content:space-between;padding:4px 0;font-family:'Courier New',monospace}
.stat .val{color:#53d769;font-weight:bold}
.stat .val.red{color:#e94560}
table{width:100%;border-collapse:collapse;margin-top:10px}
th{text-align:left;color:#e94560;padding:6px;font-size:0.85em}
td{padding:6px;font-family:'Courier New',monospace;font-size:0.85em;border-top:1px solid #0f3460}
.controls{margin:15px 0}
.btn{padding:10px 20px;border:none;border-radius:5px;cursor:pointer;font-size:1em;margin-right:10px}
.btn-start{background:#53d769;color:#1a1a2e}.btn-stop{background:#e94560;color:#fff}
</style></head><body>
<h1>📊 LOCAL_AI_ENGINE Dashboard</h1>
<div class="controls">
<button class="btn btn-start" onclick="ctrl('start')">▶ Start</button>
<button class="btn btn-stop" onclick="ctrl('stop')">⏹ Stop</button>
</div>
<div class="grid" id="dashboard"></div>
<script>
async function fetchStats(){const r=await fetch('/api/stats');const d=await r.json();let h='';
h+='<div class="card"><h2>⚙️ Статус</h2>';
h+='<div class="stat"><span>Основной бот:</span><span class="val">'+d.bot_status+'</span></div>';
h+='<div class="stat"><span>Uptime:</span><span>'+Math.floor(d.uptime/60)+' мин</span></div>';
h+='<div class="stat"><span>Дашборд LLM:</span><span>'+d.model+' ('+d.llm_mode+')</span></div>';
h+='<div class="stat"><span>Variant:</span><span>'+d.prompt_variant+'</span></div>';
h+='<div class="stat"><span>Auto:</span><span>'+(d.auto_mode?'ON':'OFF')+'</span></div>';
h+='</div>';
h+='<div class="card"><h2>🎯 Точность</h2>';
h+='<div class="stat"><span>Проверено:</span><span class="val">'+d.checked+'</span></div>';
h+='<div class="stat"><span>Accuracy:</span><span class="val">'+d.accuracy+'%</span></div>';
h+='<div class="stat"><span>SL rate:</span><span class="val red">'+d.sl_rate+'%</span></div>';
h+='<div class="stat"><span>Pending:</span><span>'+d.pending+'</span></div>';
h+='</div>';
if(d.last_signals&&d.last_signals.length>0){h+='<div class="card" style="grid-column:1/-1"><h2>📈 Сигналы</h2><table><tr><th>Время</th><th>Символ</th><th>Signal</th><th>Dir</th><th>Outcome</th></tr>';
d.last_signals.forEach(s=>{h+='<tr><td>'+(s.timestamp||'').slice(11,19)+'</td><td>'+s.symbol+'</td><td>'+s.signal_status+'</td><td>'+(s.direction||'—')+'</td><td>'+(s.outcome||'pending')+'</td></tr>'});
h+='</table></div>';}
document.getElementById('dashboard').innerHTML=h;}
async function ctrl(a){await fetch('/api/'+a,{method:'POST'});setTimeout(fetchStats,500);}
fetchStats();setInterval(fetchStats,60000);
</script></body></html>"""

@app.route("/")
def dashboard():
    return render_template_string(HTML)

@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())

@app.route("/api/start", methods=["POST"])
def api_start():
    return jsonify({"status": "started" if start_main_bot() else "already running"})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    return jsonify({"status": "stopped" if stop_main_bot() else "not running"})

# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    if not is_main_bot_running():
        start_main_bot()
        logging.info(f"Main bot started (PID {main_bot_process.pid})")

    def run_flask():
        app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info(f"Web dashboard: http://localhost:{WEB_PORT}")

    session = AiohttpSession()
    bot = Bot(token=DASH_TOKEN, session=session)
    await bot.set_my_commands([
        BotCommand(command="start", description="Меню с кнопками"),
        BotCommand(command="scan", description="Анализ: /scan BTC"),
        BotCommand(command="autoscan", description="Автоскан: /autoscan 30"),
        BotCommand(command="status", description="Статус ботов"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="startbot", description="Запустить основной бот"),
        BotCommand(command="stopbot", description="Остановить основной бот"),
        BotCommand(command="auto", description="Тогл авто-режима"),
        BotCommand(command="settings", description="Настройки"),
        BotCommand(command="version", description="Git HEAD"),
    ])
    me = await bot.get_me()
    logging.info(f"Dashboard bot: @{me.username} (id={me.id})")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print(f"📊 Dashboard Bot + Web: http://localhost:{WEB_PORT}")
    asyncio.run(main())
