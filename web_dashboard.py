"""
LOCAL_AI_ENGINE — Dashboard Bot + Web Dashboard
Бот: @my_hermes_lokal_ai_bot (cloud LLM — Alibaba GLM)
Основной бот: @KXROBObot (local LLM — LM Studio)
Запуск: python web_dashboard.py → http://localhost:5000

Функционал — полная копия KXROBO (core/handlers.py) через cloud LLM + автоскан.
"""
from __future__ import annotations

import io
import re
import os
import sys
import threading
import asyncio
import sqlite3
import subprocess
import logging
import time
from datetime import datetime, timedelta

# Подключаем централизованное логирование (RotatingFileHandler → logs/bot.log)
from core.logging_setup import setup_logging
setup_logging()

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
from aiogram.types import (
    Message, BotCommand,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, InaccessibleMessage, BufferedInputFile,
)
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
DASHBOARD_LLM_BASE_URL = os.getenv("DASHBOARD_LLM_BASE_URL", "").rstrip("/").removesuffix("/v1")
DASHBOARD_MODEL_NAME = os.getenv("DASHBOARD_MODEL_NAME", "glm-5.2-fast-preview")

# ── Импорты из core (общий код с KXROBO) ────────────────────────────────────

from core.ollama_client import analyze_multi_images, enforce_risk_rules, format_json_for_tg
from core.config import USER_ANALYSIS_CACHE
from core.auto_chart import fetch_and_plot
from core.state_tracker import update_and_save_state
from core.db import get_backtest_stats, get_history_df, get_setting, set_setting, init_breakout_events_table
from core.scheduler import update_timer
from core.utils import validate_symbol, fetch_ticker_safe, format_symbol, is_futures, sort_timeframes

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

# ── DB helpers (локальные, не трогаем core.db — используем его напрямую) ──

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

def _dash_get_setting(key, default=None):
    rows = _query_db("SELECT value FROM settings WHERE key=?", (key,))
    if rows:
        v = rows[0]["value"]
        if v in ("true", "false"): return v == "true"
        return v
    return default

def _dash_set_setting(key, value):
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

    # Win/Loss по символам и направлениям (для swing-SL статистики)
    by_symbol = _query_db("""
        SELECT symbol,
            COUNT(*) as total,
            SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'sl_hit' THEN 1 ELSE 0 END) as sl_hits
        FROM signal_log WHERE checked_at IS NOT NULL AND signal_status = 'aggressive_breakout'
        GROUP BY symbol
    """)
    stats["by_symbol"] = [{"symbol": r["symbol"], "total": r["total"], "wins": int(r["wins"] or 0),
                            "sl_hits": int(r["sl_hits"] or 0)} for r in by_symbol]

    by_direction = _query_db("""
        SELECT direction,
            COUNT(*) as total,
            SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'sl_hit' THEN 1 ELSE 0 END) as sl_hits
        FROM signal_log WHERE checked_at IS NOT NULL AND signal_status = 'aggressive_breakout'
        GROUP BY direction
    """)
    stats["by_direction"] = [{"direction": r["direction"], "total": r["total"], "wins": int(r["wins"] or 0),
                               "sl_hits": int(r["sl_hits"] or 0)} for r in by_direction]

    return stats

# ═══════════════════════════════════════════════════════════════════════════
# КОПИЯ KXROBO HANDLERS — тот же функционал, но через cloud LLM
# ═══════════════════════════════════════════════════════════════════════════

USER_PHOTO_BUFFER: dict[int, list[bytes]] = {}
SCAN_LOCK = asyncio.Lock()  # Последовательная загрузка графиков


def clean_analysis_report(text: str) -> str:
    """Исправляет логические противоречия в ТП/СЛ и форматирует вывод."""
    if "НАПРАВЛЕНИЕ:" in text:
        direction = "Long" if "Long" in text.split("НАПРАВЛЕНИЕ:")[1].split("|")[0] else "Short"
        price_match = re.search(r"Текущая цена:\s*([\d.]+)", text)
        if price_match:
            current = float(price_match.group(1))
            tp1_match = re.search(r"TP1:\s*([\d.]+)", text)
            if tp1_match:
                tp1 = float(tp1_match.group(1))
                if direction == "Long" and tp1 <= current:
                    text = text.replace(f"TP1: {tp1}", "TP1: Уже отработан")
                elif direction == "Short" and tp1 >= current:
                    text = text.replace(f"TP1: {tp1}", "TP1: Уже отработан")
    return text


def _get_timeframes() -> list[str]:
    val = get_setting("timeframes", ["15m", "1h", "4h", "1D"])
    return val if isinstance(val, list) else ["15m", "1h", "4h", "1D"]


def _get_symbols() -> list[str]:
    val = get_setting("symbols", ["BTCUSDT", "XAUTUSDT"])
    return val if isinstance(val, list) else ["BTCUSDT", "XAUTUSDT"]


def _format_symbol(symbol_id: str) -> str:
    if "/" in symbol_id:
        return symbol_id
    for quote in ["USDT", "BUSD", "USDC", "EUR", "TRY", "BTC", "ETH", "BNB", "DAI", "GBP", "AUD"]:
        if symbol_id.endswith(quote):
            return f"{symbol_id[:-len(quote)]}/{quote}"
    return symbol_id


# ── Автоскан: символы и интервал ───────────────────────────────────────────

ALL_AUTOSCAN_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUTUSDT", "SOLUSDT"]
SYMBOL_LABELS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "XAUTUSDT": "XAUT", "SOLUSDT": "SOL"}
DEFAULT_AUTOSCAN_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUTUSDT"]


def _get_autoscan_symbols() -> list[str]:
    raw = _dash_get_setting("autoscan_symbols", "")
    if raw and isinstance(raw, str) and raw.strip():
        syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
        return [s for s in syms if s in ALL_AUTOSCAN_SYMBOLS]
    return list(DEFAULT_AUTOSCAN_SYMBOLS)


def _set_autoscan_symbols(symbols: list[str]):
    _dash_set_setting("autoscan_symbols", ",".join(symbols))


# ── Inline Keyboards (копия KXROBO + автоскан) ─────────────────────────────

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура — как у KXROBO + строка автоскана."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Быстрый анализ", callback_data="menu_scan"),
         InlineKeyboardButton(text="📷 Анализ скриншотов", callback_data="menu_screenshots")],
        [InlineKeyboardButton(text="⚙️ Инструменты", callback_data="menu_instruments"),
         InlineKeyboardButton(text="⏱ Таймер", callback_data="menu_timer")],
        [InlineKeyboardButton(text="📈 Таймфреймы", callback_data="menu_timeframes"),
         InlineKeyboardButton(text="📊 Экспорт + Бэктест", callback_data="menu_export")],
        [InlineKeyboardButton(text="📋 Настройки", callback_data="menu_settings"),
         InlineKeyboardButton(text="ℹ️ О боте", callback_data="menu_about")],
        [InlineKeyboardButton(text=_autoscan_button_label(), callback_data="toggle_autoscan"),
         InlineKeyboardButton(text="📊 Автоскан: тикеры", callback_data="multi_monitor")],
    ])


def get_tf_keyboard() -> InlineKeyboardMarkup:
    selected = _get_timeframes()
    keyboard = []
    row = []
    for tf in ["15m", "1h", "4h", "1D"]:
        icon = "✅" if tf in selected else "⬜"
        row.append(InlineKeyboardButton(text=f"{icon} {tf}", callback_data=f"tf_toggle_{tf}"))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_tf")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _multi_monitor_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора тикеров для автоскана."""
    selected = _get_autoscan_symbols()
    rows = []
    row = []
    for sym in ALL_AUTOSCAN_SYMBOLS:
        icon = "✅" if sym in selected else "⬜"
        label = SYMBOL_LABELS.get(sym, sym.replace("USDT", ""))
        row.append(InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"sym_toggle_{sym}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    interval = _effective_autoscan_interval()
    rows.append([InlineKeyboardButton(text=f"⏱ Интервал: {interval} мин", callback_data="autoscan_interval_info")])
    rows.append([
        InlineKeyboardButton(text="◀ −5 мин", callback_data="iv_minus"),
        InlineKeyboardButton(text="+5 мин ▶", callback_data="iv_plus"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _autoscan_button_label() -> str:
    interval = _effective_autoscan_interval()
    active = _dash_get_setting("autoscan_active", False)
    if active:
        return f"⏹ Стоп автоскан ({interval} мин)"
    syms = _get_autoscan_symbols()
    sym_labels = "+".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in syms)
    return f"▶ Автоскан ({sym_labels}, {interval} мин)"


def _effective_autoscan_interval() -> int:
    """Эффективный autoscan интервал с учётом рыночных часов.
    Рабочее окно: пн 08:00 – пт 23:59 (крипто 24/7, но движения живые).
    Вне окна (сб/вс + пн до 08:00): 60 мин фикс — рынок спящий.
    В окне: базовый autoscan_interval из settings (10–15 мин, дефолт 15)."""
    raw = _dash_get_setting("autoscan_interval", 15)
    try:
        base = int(raw)
    except (ValueError, TypeError):
        base = 15
    now = datetime.now()
    wd = now.weekday()  # 0=пн, 5=сб, 6=вс
    # Сб/вс → вне окна
    if wd >= 5:
        return 60
    # Пн до 08:00 → вне окна
    if wd == 0 and now.hour < 8:
        return 60
    # Пн 08:00+ … Пт 23:59 → рабочее окно
    return base


# ═══════════════════════════════════════════════════════════════════════════
# BOT DISPATCHER + HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

dp = Dispatcher(storage=MemoryStorage())


# ── /start ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
@dp.message(Command("menu"))
async def show_main_menu(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    await message.answer(
        "🤖 *Dashboard Bot* — облако (Alibaba GLM)\n\n"
        "Полная копия KXROBO + автоскан.\n"
        "Выберите действие ниже или используйте команды:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown",
    )


# ── /scan (полная копия из handlers.py, но через cloud LLM) ────────────────

def _fill_missing_tf_zones(
    result: dict, prev_tf_zones: dict, timeframes: list[str],
    zigzag_timeframes: dict | None = None,
    vp_timeframes: dict | None = None,
) -> None:
    """
    Если LLM не вернул зону для какого-то ТФ — берёт из fallback источников.

    Приоритет fallback:
    0. Volume Profile POC (настоящие зоны консолидации по объёму)
    1. ZigZag benchmark
    2. prev_tf_zones (для ТФ которых ZigZag/VP не загрузили)

    Если нет нигде — зона не вставляется (N/A).
    Если нет нигде — зона не вставляется (N/A).

    НЕ используем all_metrics[tf]["zone"] = get_structural_extremums()
    (сырые max/min за 120 свечей — не зоны консолидации).

    Матрёшка + D1 cap применяются ВНЕ функции — через re-run enforce_risk_rules()
    в caller (_do_full_scan) после fallback.
    """
    tf_key_map = {
        "1d": "1D", "4h": "4H", "1h": "1H", "15m": "15M", "5m": "5M",
        "1D": "1D", "4H": "4H", "1H": "1H", "15M": "15M", "5M": "5M",
    }
    # Канонический порядок от старшего к младшему
    canonical_order = ["1D", "4H", "1H", "15M", "5M"]

    if not isinstance(result, dict):
        return
    tf_zones = result.get("tf_zones")
    if not isinstance(tf_zones, dict):
        tf_zones = {}
        result["tf_zones"] = tf_zones

    if not isinstance(zigzag_timeframes, dict):
        zigzag_timeframes = {}
    if not isinstance(vp_timeframes, dict):
        vp_timeframes = {}

    filled = False
    price = result.get("price") or result.get("current_price")
    price_safe = price if isinstance(price, (int, float)) and price > 0 else None
    stick_tol = 0.005  # 0.5% — если parent и child зоны совпадают в пределах tolerance

    for tf in timeframes:
        norm_key = tf_key_map.get(tf, tf.upper().replace("MIN", "M"))
        if norm_key in tf_zones and isinstance(tf_zones[norm_key], dict):
            # Проверка «прилипания»: если эта зона = parent зоне (D1=H4=H1)
            # → LLM скопировал, заменяем на fallback (VP/ZigZag)
            if price_safe and norm_key != "1D":
                parent_idx = canonical_order.index(norm_key) - 1 if norm_key in canonical_order else -1
                if parent_idx >= 0:
                    parent_tf = canonical_order[parent_idx]
                    parent_z = tf_zones.get(parent_tf)
                    child_z = tf_zones[norm_key]
                    if (isinstance(parent_z, dict) and isinstance(child_z, dict)
                            and parent_z.get("lower") is not None and child_z.get("lower") is not None
                            and parent_z.get("upper") is not None and child_z.get("upper") is not None):
                        ldiff = abs(parent_z["lower"] - child_z["lower"]) / price_safe
                        udiff = abs(parent_z["upper"] - child_z["upper"]) / price_safe
                        if ldiff < stick_tol and udiff < stick_tol:
                            logging.info(
                                "DASHBOARD: %s zone sticks to %s (diff l=%.3f%% u=%.3f%%), replacing with fallback for %s",
                                norm_key, parent_tf, ldiff * 100, udiff * 100, result.get("symbol", "?"),
                            )
                            # Удаляем прилипшую зону — fallback ниже подставит реальную
                            del tf_zones[norm_key]
            # После проверки — если зона всё ещё на месте, пропускаем
            if norm_key in tf_zones and isinstance(tf_zones[norm_key], dict):
                # Доп. проверка: min-span — если зона слишком узкая (микроканал), тоже удаляем
                if price_safe:
                    z = tf_zones[norm_key]
                    z_upper = z.get("upper")
                    z_lower = z.get("lower")
                    if z_upper is not None and z_lower is not None:
                        span_pct = abs(z_upper - z_lower) / price_safe
                        # Минимальный span по ТФ: ниже = микроканал, не структурная зона
                        # Пороги основаны на реальных структурных ranges (20 свечей Binance)
                        # LLM часто ставит зону = последние 5 свечей в сжатии (0.3-0.8%)
                        # Реальный 15m structural range ≈ 1-3% (D1≈5-10%, H4≈3-5%, H1≈1.5-3%)
                        min_span = {"1D": 0.025, "4H": 0.020, "1H": 0.012, "15M": 0.008, "5M": 0.004}
                        min_pct = min_span.get(norm_key, 0.002)
                        if span_pct < min_pct:
                            # ВАРИАНТ C: зона с валидным BOS — структурная, не микроканал.
                            # BOS (bos_price + bos_dir up/down) доказывает что зона после пробоя,
                            # а не сжатие из 5 свечей. Узкая зона после свежего BOS — нормально.
                            has_valid_bos = (
                                z.get("bos_price") is not None
                                and z.get("bos_dir") in ("up", "down")
                            )
                            if has_valid_bos:
                                logging.info(
                                    "DASHBOARD: %s zone narrow (%.4f%% < %.4f%%) but has valid BOS %s %s age=%s — keeping",
                                    norm_key, span_pct * 100, min_pct * 100,
                                    z.get("bos_dir"), z.get("bos_price"), z.get("bos_age"),
                                )
                            else:
                                logging.info(
                                    "DASHBOARD: %s zone too narrow: %.4f%% < min %.4f%%, replacing with fallback for %s",
                                    norm_key, span_pct * 100, min_pct * 100, result.get("symbol", "?"),
                                )
                                del tf_zones[norm_key]
                if norm_key in tf_zones and isinstance(tf_zones[norm_key], dict):
                    continue

        zone = None
        source = None

        # Поиск ключа: tf (raw), norm_key (canonical), tf_lower (VP/ZigZag return lowercase)
        tf_lower = tf.lower()
        lookup_keys = [tf, norm_key, tf_lower]

        def _find_zone(d):
            """Ищем зону по любому варианту ключа (case-insensitive)."""
            if not isinstance(d, dict):
                return None
            for k in lookup_keys:
                v = d.get(k)
                if isinstance(v, dict) and (v.get("upper") is not None or v.get("lower") is not None):
                    return v
            return None

        # Fallback-0: Volume Profile POC (настоящие зоны консолидации по объёму)
        vp = _find_zone(vp_timeframes)
        if vp is not None:
            zone = {"upper": vp.get("upper"), "lower": vp.get("lower")}
            source = "volume_profile"

        # Fallback-1: ZigZag benchmark
        if zone is None:
            zz = _find_zone(zigzag_timeframes)
            if zz is not None:
                zone = {"upper": zz.get("upper"), "lower": zz.get("lower")}
                source = "zigzag"

        # Fallback-2: prev_tf_zones (для ТФ которых ZigZag/VP не загрузили, напр. 1D fetch failed)
        if zone is None:
            prev_zone = _find_zone(prev_tf_zones)
            if prev_zone is not None:
                zone = {"upper": prev_zone.get("upper"), "lower": prev_zone.get("lower")}
                source = "prev_analysis"

        if zone is not None:
            tf_zones[norm_key] = zone
            filled = True
            logging.info(
                "DASHBOARD: filled missing %s zone from %s for %s",
                norm_key, source, result.get("symbol", "?"),
            )

    # Пересобрать tf_zones в каноническом порядке (D1→H4→H1→M15→M5).
    # Python 3.7+ dict сохраняет порядок вставки — без этого D1 будет в конце.
    if filled:
        ordered = {}
        for canon_tf in canonical_order:
            z = tf_zones.get(canon_tf)
            if isinstance(z, dict):
                ordered[canon_tf] = z
        # Добавить любые другие ключи, которых нет в canonical_order
        for k, v in tf_zones.items():
            if k not in ordered:
                ordered[k] = v
        result["tf_zones"] = ordered

    # Пересчитать tf_span_map
    if filled:
        span_map = {}
        for tf in timeframes:
            norm_key = tf_key_map.get(tf, tf.upper().replace("MIN", "M"))
            z = ordered.get(norm_key) if filled else tf_zones.get(norm_key)
            if isinstance(z, dict):
                upper = z.get("upper")
                lower = z.get("lower")
                if upper is not None and lower is not None:
                    span_map[norm_key] = abs(upper - lower)
        result["tf_span_map"] = span_map


async def _do_full_scan(symbol: str, timeframes: list[str], chat_id: int, bot: Bot) -> None:
    """Полный анализ символ/ТФ через cloud LLM — графики последовательно."""
    async with SCAN_LOCK:
        await bot.send_message(chat_id, f"📡 Загружаю {_format_symbol(symbol)} по ТФ: {', '.join(timeframes)}...")
        try:
            chart_bytes_list: list[bytes] = []
            all_metrics: dict[str, dict] = {}

            # Динамический limit по ТФ: старшим нужно больше свечей,
            # чтобы видеть предыдущую + текущую структуру.
            # H4: 300 свечей = 50 дней — виден предыдущий HH/HL
            # D1: 250 свечей = 250 дней — полный цикл
            TF_LIMITS = {
                "1d": 250, "4h": 300, "1h": 200,
                "15m": 150, "5m": 120,
            }

            # Графики ПОСЛЕДОВАТЕЛЬНО (один за другим)
            for tf in timeframes:
                limit = TF_LIMITS.get(tf.lower(), 120)
                chart_bytes, metrics = fetch_and_plot(symbol=symbol, timeframe=tf, limit=limit)
                chart_bytes_list.append(chart_bytes)
                all_metrics[tf] = metrics

            m_htf = all_metrics[timeframes[0]]
            m_ltf = all_metrics[timeframes[-1]]

            fib = m_ltf.get("fib_context", {"50%": "N/A", "61.8%": "N/A", "38.2%": "N/A", "rule": ""})
            tf_zones = {tf: all_metrics[tf]["zone"] for tf in timeframes}

            live_price = m_ltf.get("current_price", m_ltf.get("last_closed_price", 0))

            tf_context = (
                f"[HTF] {m_htf.get('phase', 'N/A')} | Упор: {m_htf.get('resistance', 'N/A')} | Поддержка: {m_htf.get('support', 'N/A')} | "
                f"[{timeframes[-1]}] {m_ltf.get('phase', 'N/A')} | Текущая цена: {live_price} | "
                f"Объём: {m_ltf.get('vol_ratio', 1.0)}x ({m_ltf.get('vol_trend', 'N/A')}) | "
                f"Фибо: 50%={fib['50%']} | 61.8%={fib['61.8%']} | 38.2%={fib['38.2%']}"
            )

            stats = get_backtest_stats()
            metrics_str = (
                f"Текущая цена: {live_price} | Последняя закрытая: {m_ltf.get('last_closed_price', 'N/A')} | "
                f"ATR: {m_ltf.get('atr', 'N/A')} | RSI: {m_ltf.get('rsi', 'N/A')} | Сессия: {m_ltf.get('session', 'N/A')}"
            )

            # ZigZag compact context
            try:
                from core.zigzag.benchmark_zigzag import run_benchmark
                zigzag_benchmark = run_benchmark(
                    symbol=symbol, market_type="future",
                    timeframes=timeframes, limit=200,
                    mode="hybrid_atr", confirmation_mode="close",
                    debug=False, output=None, output_mode="compact",
                )
                zigzag_context = {
                    "symbol": zigzag_benchmark.get("symbol", symbol),
                    "normalized_symbol": zigzag_benchmark.get("normalized_symbol", symbol),
                    "stack": zigzag_benchmark.get("stack", {}),
                    "timeframes": zigzag_benchmark.get("timeframes", {}),
                    "confluence_levels": zigzag_benchmark.get("confluence_levels", []),
                }
            except Exception as e:
                zigzag_context = {
                    "error": True, "message": f"ZigZag: {type(e).__name__}",
                    "symbol": symbol, "stack": {}, "timeframes": {}, "confluence_levels": [],
                }

            # Volume Profile POC — настоящие зоны консолидации по объёму
            try:
                from core.volume_profile import run_volume_profile
                vp_result = run_volume_profile(
                    symbol=symbol, timeframes=timeframes,
                    limit=200, bins=50, value_area_pct=0.70,
                    market_type="future",
                )
                vp_context = {
                    "symbol": vp_result.get("symbol", symbol),
                    "timeframes": vp_result.get("timeframes", {}),
                }
            except Exception as e:
                vp_context = {
                    "error": True, "message": f"VP: {type(e).__name__}",
                    "symbol": symbol, "timeframes": {},
                }

            ltf_volume = all_metrics[timeframes[-1]].get("volume_context", {})
            if not isinstance(ltf_volume, dict):
                ltf_volume = {}

            # Liquidity heatmap
            try:
                from core.liquidity_heatmap import build_liquidity_context_text, build_liquidity_heatmap
                from core.data_provider import OhlcvDataProvider
                provider = OhlcvDataProvider()
                ltf_tf = timeframes[-1]
                try:
                    ltf_df = provider.read_current_csv(symbol, ltf_tf)
                    hm = build_liquidity_heatmap(ltf_df, symbol=symbol, timeframe=ltf_tf)
                    heatmap_text = build_liquidity_context_text(hm)
                except FileNotFoundError:
                    heatmap_text = "Liquidity heatmap: CSV недоступен."
            except Exception:
                heatmap_text = "Liquidity heatmap: ошибка."

            prev_ctx = {
                "metrics": metrics_str,
                "tf_context": tf_context,
                "backtest": f"Win Rate: {stats['win_rate']}%, MAE: {stats['mae_pct']}%",
                "tf_zones": tf_zones,
                "zigzag_context": zigzag_context,
                "volume_context": ltf_volume,
                "heatmap_context": heatmap_text,
            }

            # LLM через CLOUD
            raw_result = await analyze_multi_images(
                chart_bytes_list,
                prev_analysis=prev_ctx,
                llm_api_key=DASHBOARD_LLM_API_KEY,
                llm_base_url=DASHBOARD_LLM_BASE_URL,
                llm_model=DASHBOARD_MODEL_NAME,
            )

            parsed_result = raw_result
            if isinstance(parsed_result, dict):
                parsed_result = update_and_save_state(symbol, timeframes[-1], parsed_result)

            # enforce_risk_rules уже вызван внутри analyze_multi_images —
            # tf_zones, D1 cap, nesting, confluence — всё валидировано.
            # Но LLM может не вернуть зону для какого-то ТФ (часто 1D) —
            # заполняем отсутствующие из prev_analysis.tf_zones как fallback.
            # ВАЖНО: после fallback перезапускаем enforce_risk_rules,
            # чтобы матрёшка и D1 cap применились к обновлённым зонам.
            if isinstance(parsed_result, dict):
                # Гарантировать symbol в result для логирования fallback
                if not parsed_result.get("symbol"):
                    parsed_result["symbol"] = symbol
                _fill_missing_tf_zones(
                    parsed_result, tf_zones, timeframes,
                    zigzag_timeframes=zigzag_context.get("timeframes", {}),
                    vp_timeframes=vp_context.get("timeframes", {}),
                )
                # Проброс zigzag_context для _detect_contamination в enforce_risk_rules.
                # parsed_result — это LLM output, в нём нет zigzag_context.
                if not parsed_result.get("zigzag_context"):
                    parsed_result["zigzag_context"] = zigzag_context
                # Re-run enforce_risk_rules: применит матрёшку + D1 cap + anti-contamination
                # к fallback-зонам, добавленным из prev_analysis.
                parsed_result = enforce_risk_rules(parsed_result)

            if isinstance(parsed_result, dict) and parsed_result.get("error"):
                await bot.send_message(chat_id, f"⚠️ Ошибка анализа: {parsed_result.get('message')}")
                return

            if isinstance(parsed_result, dict):
                final_text = format_json_for_tg(parsed_result)
            else:
                final_text = str(parsed_result)

            USER_ANALYSIS_CACHE[chat_id] = final_text
            await bot.send_message(chat_id, f"📊 Анализ {_format_symbol(symbol)}:\n\n{final_text}")

        except Exception as e:
            import traceback as _tb
            _tb_str = _tb.format_exc()
            logging.error("Dashboard analysis error: %s\n%s", e, _tb_str)
            await bot.send_message(chat_id, f"⚠️ Ошибка: {e}\n\n```\n{_tb_str[-500:]}\n```")


@dp.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if not DASHBOARD_LLM_API_KEY:
        await message.answer("❌ DASHBOARD_LLM_API_KEY не задан в .env")
        return
    if message.text is None:
        return
    # Support multi-symbol: /scan BTC | ETH | XAUT  OR  /scan BTC,ETH,XAUT  OR  /scan BTC ETH XAUT  OR  /scan BTC
    raw_text = message.text.split(maxsplit=1)
    raw_syms_str = raw_text[1] if len(raw_text) > 1 else "BTC"
    # Normalize separators: '|' and ',' and whitespace (multiple spaces) → split
    # /scan BTC | ETH | XAUT  →  ["BTC", "ETH", "XAUT"]
    # /scan BTC,ETH,XAUT     →  ["BTC", "ETH", "XAUT"]
    # /scan BTC ETH XAUT     →  ["BTC", "ETH", "XAUT"]
    raw_syms_str_norm = raw_syms_str.replace("|", " ").replace(",", " ")
    raw_syms = [s.strip().upper().replace("/", "").replace("USDT", "") for s in raw_syms_str_norm.split()]
    raw_syms = [s for s in raw_syms if s]  # filter empty
    if not raw_syms:
        raw_syms = ["BTC"]
    timeframes = sort_timeframes(_get_timeframes())
    for i, raw_sym in enumerate(raw_syms):
        symbol = f"{raw_sym}USDT"
        if i > 0:
            await asyncio.sleep(3)  # pause between tickers to avoid rate limit
        await _do_full_scan(symbol, timeframes, message.chat.id, message.bot)


# ── /add, /remove, /settings, /timer, /filter, /auto, /export ──────────────

@dp.message(Command("add"))
async def cmd_add(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if message.text is None: return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("❌ Укажите ID тикера: `/add XAGUSDT`")
    raw_sym = parts[1]
    result = await validate_symbol(raw_sym)
    if not result["valid"]:
        return await message.answer(result["error"])
    current = _get_symbols()
    if result["id"] in current:
        return await message.answer(f"⚠️ `{_format_symbol(result['id'])}` уже в списке.")
    current.append(result["id"])
    set_setting("symbols", current)
    display = [f"`{_format_symbol(s)}`" for s in current]
    await message.answer(f"✅ `{_format_symbol(result['id'])}` добавлен ({result['type']}).\n📋 Список: {', '.join(display)}")


@dp.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if message.text is None: return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("❌ Укажите ID тикера: `/remove XAGUSDT`")
    raw_sym = parts[1].strip().upper()
    current = _get_symbols()
    found_idx = next((i for i, s in enumerate(current) if s == raw_sym or s.replace("/", "") == raw_sym), None)
    if found_idx is None:
        return await message.answer(f"⚠️ `{parts[1]}` не найден в списке.")
    current.pop(found_idx)
    set_setting("symbols", current)
    display = [f"`{_format_symbol(s)}`" for s in current]
    await message.answer(f"✅ Удалён. Осталось: {', '.join(display) if current else 'нет'}")


@dp.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    symbols = _get_symbols()
    timer = get_setting("interval_minutes", 60)
    timeframes = _get_timeframes()
    spot = [_format_symbol(s) for s in symbols if not is_futures(s)]
    futures = [_format_symbol(s) for s in symbols if is_futures(s)]
    txt = "⚙️ ТЕКУЩАЯ КОНФИГУРАЦИЯ:\n\n"
    txt += f" СПОТ: {', '.join(f'`{s}`' for s in spot) if spot else 'Нет'}\n"
    txt += f"🔴 ФЬЮЧЕРСЫ: {', '.join(f'`{s}`' for s in futures) if futures else 'Нет'}\n\n"
    txt += f"⏱ Интервал отчётов: {timer} минут\n"
    txt += f"📈 Таймфреймы: {', '.join(timeframes) if timeframes else 'Нет'}\n\n"
    txt += "🔹 `/add XAGUSDT` — добавить инструмент\n"
    txt += "🔹 `/remove XAGUSDT` — удалить инструмент\n"
    txt += "🔹 `/timer 30` — изменить интервал (мин 5)\n"
    txt += "🔹 `/timeframes` — выбрать ТФ"
    await message.answer(txt)


@dp.message(Command("timer"))
async def cmd_timer(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if message.text is None: return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("❌ Укажите интервал в минутах: `/timer 30`")
    try:
        mins = int(parts[1])
        if mins < 5:
            return await message.answer("⚠️ Минимальный интервал — 5 минут.")
    except ValueError:
        return await message.answer("❌ Введите число.")
    success, resp = update_timer(mins)
    await message.answer(resp)


@dp.message(Command("filter"))
async def cmd_filter(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if message.text is None: return
    args = message.text.split()
    current = get_setting("filter_mode", True)
    if len(args) > 1:
        state = args[1].lower() in ("on", "вкл", "true", "1")
        set_setting("filter_mode", state)
        status = "✅ ВКЛЮЧЁН" if state else "❌ ВЫКЛЮЧЁН"
        await message.answer(
            f"⚙️ Фильтр сигналов {status}.\n\n"
            f"🔹 ВКЛ: только подтверждённые пробои/ретесты + предупреждения о подходе к уровням.\n"
            f"🔹 ВЫКЛ: все отчёты без фильтрации."
        )
    else:
        status = "✅ ВКЛЮЧЁН" if current else "❌ ВЫКЛЮЧЁН"
        await message.answer(f"⚙️ Фильтр сигналов: {status}\nИспользуйте: `/filter on` или `/filter off`")


@dp.message(Command("auto"))
async def cmd_auto(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    state = not get_setting("auto_mode", False)
    set_setting("auto_mode", state)
    status_text = "🔇 ON (только сигналы)" if state else "📢 OFF (все анализы)"
    await message.answer(f"Авто-режим: {status_text}")


@dp.message(Command("export"))
async def cmd_export(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    stats = get_backtest_stats()
    csv_data = get_history_df()
    filename = f"forecasts_{stats['total']}_win{stats['win_rate']}%.csv"
    await message.answer_document(
        BufferedInputFile(csv_data.encode("utf-8-sig"), filename=filename),
        caption=f"📈 Бэктест: Всего {stats['total']}, Win: {stats['win_rate']}%, MAE: {stats['mae_pct']}%"
    )


# ── /timeframes ─────────────────────────────────────────────────────────────

@dp.message(Command("timeframes"))
async def cmd_timeframes(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    selected = _get_timeframes()
    txt = f"📊 ТЕКУЩИЕ ТАЙМФРЕЙМЫ:\n{', '.join(selected)}\n\n"
    txt += "Нажмите на ТФ, чтобы добавить/удалить.\n"
    txt += "Все выбранные ТФ применяются к `/scan` и авто-отчётам."
    await message.answer(txt, reply_markup=get_tf_keyboard())


# ── Скриншоты (фото) ───────────────────────────────────────────────────────

@dp.message(lambda msg: msg.photo)
async def collect_photos(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    assert message.from_user is not None
    assert message.bot is not None
    user_id = message.from_user.id
    if user_id not in USER_PHOTO_BUFFER:
        USER_PHOTO_BUFFER[user_id] = []
    if len(USER_PHOTO_BUFFER[user_id]) >= 5:
        await message.answer("⚠️ Максимум 5 фото. Введите `/analyze_all`.")
        return
    if not message.photo:
        return
    bio = io.BytesIO()
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    if file_info.file_path:
        await message.bot.download_file(file_info.file_path, destination=bio)
        USER_PHOTO_BUFFER[user_id].append(bio.getvalue())
        await message.answer(f"✅ Фото сохранено ({len(USER_PHOTO_BUFFER[user_id])}/5).")
    else:
        await message.answer("❌ Не удалось скачать фото.")


@dp.message(Command("analyze_all"))
async def cmd_analyze_all(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    assert message.from_user is not None
    user_id = message.from_user.id
    if user_id not in USER_PHOTO_BUFFER or not USER_PHOTO_BUFFER[user_id]:
        await message.answer("❌ Сначала отправьте фото.")
        return
    if not DASHBOARD_LLM_API_KEY:
        await message.answer("❌ DASHBOARD_LLM_API_KEY не задан в .env")
        return
    images = USER_PHOTO_BUFFER.pop(user_id)
    prev = USER_ANALYSIS_CACHE.get(user_id)
    await message.answer("🧠 Анализирую через облако...")
    try:
        result = await analyze_multi_images(
            images, prev_analysis=prev,
            llm_api_key=DASHBOARD_LLM_API_KEY,
            llm_base_url=DASHBOARD_LLM_BASE_URL,
            llm_model=DASHBOARD_MODEL_NAME,
        )
        final_text = format_json_for_tg(result)
        USER_ANALYSIS_CACHE[user_id] = final_text
        await message.answer(final_text)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")


# ── Callback handler (копия KXROBO + автоскан) ─────────────────────────────

@dp.callback_query()
async def callbacks_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message or isinstance(callback.message, InaccessibleMessage):
        return
    if not callback.data:
        return
    msg = callback.message

    # ── KXROBO кнопки ─────────────────────────────────────────────────────
    if callback.data == "menu_scan":
        await msg.answer("📊 Введите пару для анализа:\n`/scan BTC` или `/scan ETH/USDT` или `/scan SOL`")
    elif callback.data == "menu_screenshots":
        await msg.answer("📷 Отправьте 1–5 скриншотов графиков, затем введите `/analyze_all`")
    elif callback.data == "menu_instruments":
        await msg.answer("⚙️ Управление портфелем:\n`/add SOL/USDT` — добавить\n`/remove ETH/USDT` — удалить\n`/settings` — текущий список")
    elif callback.data == "menu_timer":
        await msg.answer("⏱ Введите интервал авто-отчётов в минутах:\n`/timer 15` (минимум 5)")
    elif callback.data == "menu_timeframes":
        await cmd_timeframes(msg)
    elif callback.data == "menu_export":
        await cmd_export(msg)
    elif callback.data == "menu_settings":
        await cmd_settings(msg)
    elif callback.data == "menu_about":
        cloud = "✅ cloud" if DASHBOARD_LLM_API_KEY else "❌ no key"
        await msg.edit_text(
            "🤖 *Dashboard Bot* — @my_hermes_lokal_ai_bot\n"
            "  LLM: {model} ({cloud})\n"
            "  Функции: полный анализ + автоскан\n\n"
            "🔧 *Основной бот* — @KXROBObot\n"
            "  LLM: LM Studio qwen2.5-vl-7b (локальная)\n\n"
            "📁 github.com/VVASILEVSS/LOCAL_AI_ENGINE".format(model=DASHBOARD_MODEL_NAME, cloud=cloud),
            parse_mode="Markdown",
        )
    elif callback.data == "analyze_all_btn":
        if not callback.from_user: return
        user_id = callback.from_user.id
        if user_id in USER_PHOTO_BUFFER and USER_PHOTO_BUFFER[user_id]:
            images = USER_PHOTO_BUFFER.pop(user_id)
            await msg.edit_text("🧠 Анализирую через облако...")
            try:
                result = await analyze_multi_images(
                    images, prev_analysis=USER_ANALYSIS_CACHE.get(user_id),
                    llm_api_key=DASHBOARD_LLM_API_KEY,
                    llm_base_url=DASHBOARD_LLM_BASE_URL,
                    llm_model=DASHBOARD_MODEL_NAME,
                )
                final_text = format_json_for_tg(result)
                USER_ANALYSIS_CACHE[user_id] = final_text
                await msg.answer(final_text)
            except Exception as e:
                await msg.answer(f"⚠️ Ошибка: {e}")
        else:
            await msg.answer("❌ Сначала отправьте скриншоты.")
    elif callback.data == "export_history":
        stats = get_backtest_stats()
        csv_data = get_history_df()
        await msg.answer_document(
            BufferedInputFile(csv_data.encode("utf-8-sig"), filename="forecasts.csv"),
            caption=f"📈 Бэктест: Win {stats['win_rate']}%, MAE {stats['mae_pct']}%"
        )
    elif callback.data == "settings_menu":
        await msg.edit_text("⚙️ Используйте команды: /settings, /add, /remove, /timer, /timeframes")

    # ── TF toggle ─────────────────────────────────────────────────────────
    elif callback.data and (callback.data.startswith("tf_toggle_") or callback.data == "close_tf"):
        if callback.data == "close_tf":
            await msg.edit_text("⚙️ Меню таймфреймов закрыто.")
            return
        tf = callback.data.replace("tf_toggle_", "")
        current = _get_timeframes()
        if tf in current:
            current.remove(tf)
        else:
            current.append(tf)
        set_setting("timeframes", current)
        await msg.edit_text(
            f"📊 ТЕКУЩИЕ ТАЙМФРЕЙМЫ:\n{', '.join(current)}\n\n"
            "Нажмите на ТФ, чтобы добавить/удалить.\n"
            "Все выбранные ТФ применяются к `/scan` и авто-отчётам.",
            reply_markup=get_tf_keyboard()
        )

    # ── Автоскан toggle ───────────────────────────────────────────────────
    elif callback.data == "toggle_autoscan":
        if callback.from_user and callback.from_user.id != ADMIN_CHAT_ID: return
        active = _dash_get_setting("autoscan_active", False)
        if active:
            _stop_autoscan()
            text = "⏹ Автоскан остановлен"
        else:
            if not DASHBOARD_LLM_API_KEY:
                await callback.answer("❌ DASHBOARD_LLM_API_KEY не задан", show_alert=True)
                return
            syms = _get_autoscan_symbols()
            if not syms:
                await callback.answer("❌ Нет выбранных тикеров", show_alert=True)
                return
            _start_autoscan(callback.bot)
            iv = _dash_get_setting("autoscan_interval", 15)
            labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in syms)
            text = (
                f"▶ Автоскан запущен\n\n"
                f"Тикеры: {labels}\n"
                f"Интервал: {iv} мин\n"
                f"Цикл: {' → '.join(SYMBOL_LABELS.get(s, s.replace('USDT', '')) for s in syms)} → пауза → повтор"
            )
        await msg.edit_text(text, reply_markup=get_main_menu_keyboard())

    # ── Мультивалютный монитор ────────────────────────────────────────────
    elif callback.data == "multi_monitor":
        if callback.from_user and callback.from_user.id != ADMIN_CHAT_ID: return
        selected = _get_autoscan_symbols()
        labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in selected)
        text = (
            f"📊 *Мультивалютный монитор*\n\n"
            f"Выбранные тикеры для автоскана:\n{labels}\n\n"
            f"Нажми на тикер, чтобы добавить/убрать.\n"
            f"Минимум 1 тикер."
        )
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=_multi_monitor_keyboard())

    elif callback.data and callback.data.startswith("sym_toggle_"):
        if callback.from_user and callback.from_user.id != ADMIN_CHAT_ID: return
        sym = callback.data.removeprefix("sym_toggle_")
        if sym not in ALL_AUTOSCAN_SYMBOLS:
            await callback.answer("Неизвестный символ", show_alert=True)
            return
        selected = _get_autoscan_symbols()
        if sym in selected:
            if len(selected) <= 1:
                await callback.answer("❌ Минимум 1 тикер!", show_alert=True)
                return
            selected.remove(sym)
        else:
            selected.append(sym)
        _set_autoscan_symbols(selected)
        labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in selected)
        text = (
            f"📊 *Мультивалютный монитор*\n\n"
            f"Выбранные тикеры для автоскана:\n{labels}\n\n"
            f"Нажми на тикер, чтобы добавить/убратить."
        )
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=_multi_monitor_keyboard())
        if _dash_get_setting("autoscan_active", False):
            _stop_autoscan()
            _start_autoscan(callback.bot)
            await callback.answer(f"Автоскан перезапущен: {labels}", show_alert=False)

    elif callback.data == "iv_minus":
        if callback.from_user and callback.from_user.id != ADMIN_CHAT_ID: return
        iv = _dash_get_setting("autoscan_interval", 15)
        iv = max(5, iv - 5)
        _dash_set_setting("autoscan_interval", iv)
        selected = _get_autoscan_symbols()
        labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in selected)
        text = f"📊 *Мультивалютный монитор*\n\nВыбранные тикеры: {labels}\n\n◀ / ▶ для интервала"
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=_multi_monitor_keyboard())
        if _dash_get_setting("autoscan_active", False):
            _stop_autoscan()
            _start_autoscan(callback.bot)

    elif callback.data == "iv_plus":
        if callback.from_user and callback.from_user.id != ADMIN_CHAT_ID: return
        iv = _dash_get_setting("autoscan_interval", 15)
        iv = min(240, iv + 5)
        _dash_set_setting("autoscan_interval", iv)
        selected = _get_autoscan_symbols()
        labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in selected)
        text = f"📊 *Мультивалютный монитор*\n\nВыбранные тикеры: {labels}\n\n◀ / ▶ для интервала"
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=_multi_monitor_keyboard())
        if _dash_get_setting("autoscan_active", False):
            _stop_autoscan()
            _start_autoscan(callback.bot)

    elif callback.data == "autoscan_interval_info":
        if callback.from_user and callback.from_user.id != ADMIN_CHAT_ID: return
        selected = _get_autoscan_symbols()
        labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in selected)
        base_iv = _dash_get_setting("autoscan_interval", 15)
        eff_iv = _effective_autoscan_interval()
        now = datetime.now()
        wd = now.weekday()
        in_window = not (wd >= 5 or (wd == 0 and now.hour < 8))
        mode_note = (
            f"\n\n🟢 *Рабочее окно* (пн 08:00–пт 23:59): интервал = {base_iv} мин (базовый)"
            if in_window else
            f"\n\n💤 *Вне окна* (сб/вс + пн до 08:00): интервал = 60 мин фикс (рынок спящий)"
        )
        text = f"📊 *Мультивалютный монитор*\n\nВыбранные тикеры: {labels}\n\nТекущий интервал: {eff_iv} мин.{mode_note}\n\n◀ / ▶ для изменения базового (10–15 мин в рабочем окне)"
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=_multi_monitor_keyboard())

    elif callback.data == "back_to_main":
        await msg.edit_text(
            "🤖 *Dashboard Bot* — облако (Alibaba GLM)\n\nВыберите действие:",
            parse_mode="Markdown",
            reply_markup=get_main_menu_keyboard(),
        )


# ── /autoscan (команда) ────────────────────────────────────────────────────

@dp.message(Command("autoscan"))
async def cmd_autoscan(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
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
            _dash_set_setting("autoscan_interval", minutes)
            active = _dash_get_setting("autoscan_active", False)
            if active:
                _stop_autoscan()
                _start_autoscan(message.bot)
            await message.answer(f"✅ Интервал: {minutes} мин" + (" (перезапущен)" if active else ""))
            return
        except ValueError:
            pass
    active = _dash_get_setting("autoscan_active", False)
    iv = _dash_get_setting("autoscan_interval", 15)
    syms = _get_autoscan_symbols()
    labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in syms)
    await message.answer(
        f"Автоскан: {'🟢 ON' if active else '🔴 OFF'} ({iv} мин)\n"
        f"Тикеры: {labels}\n\n"
        f"/autoscan 30 — интервал\n/autoscan off — стоп"
    )


# ── /status, /stats, /startbot, /stopbot, /version ─────────────────────────

@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    running = is_main_bot_running()
    uptime = int(time.time() - main_bot_started_at) if main_bot_started_at and running else 0
    pid = main_bot_process.pid if running else "—"
    status = "🟢 Running" if running else "🔴 Stopped"
    cloud_status = "✅ cloud" if DASHBOARD_LLM_API_KEY else "❌ no key"
    autoscan = _dash_get_setting("autoscan_active", False)
    autoscan_iv = _dash_get_setting("autoscan_interval", 15)
    autoscan_syms = _get_autoscan_symbols()
    autoscan_labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in autoscan_syms)
    text = (
        f"📊 *Статус*\n\n"
        f"Основной бот (@KXROBObot): {status}\n"
        f"  PID: {pid}\n"
        f"  Uptime: {uptime//60} мин {uptime%60} сек\n"
        f"  Модель: LM Studio qwen2.5-vl-7b (локальная)\n\n"
        f"Дашборд-бот (@my_hermes_lokal_ai_bot): 🟢 Active\n"
        f"  Модель: {DASHBOARD_MODEL_NAME} ({cloud_status})\n"
        f"  Prompt: variant {os.getenv('PROMPT_VARIANT', 'A')}\n"
        f"  Auto: {'🔇 ON' if get_setting('auto_mode', False) else '📢 OFF'}\n"
        f"  Автоскан: {'🟢 ON' if autoscan else '🔴 OFF'} ({autoscan_iv} мин)\n"
        f"    Тикеры: {autoscan_labels}"
    )
    await message.answer(text)


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    s = get_stats()
    text = f"📈 *Статистика*\n\nПроверено: {s['checked']}\nAccuracy: {s['accuracy']}%\nWins: {s['wins']}\nSL: {s['sl_rate']}%\nPending: {s['pending']}\n\n"
    if s.get("ab_variants"):
        text += "🧪 *A/B:*\n"
        for v in s["ab_variants"]:
            text += f"  {v['variant']}: {v['wins']}/{v['total']}\n"
    if s.get("by_symbol"):
        text += "\n📊 *По символам (aggressive_breakout):*\n"
        for r in s["by_symbol"]:
            wr = round(r["wins"] / r["total"] * 100, 1) if r["total"] else 0
            text += f"  {r['symbol']}: ✅{r['wins']} ❌{r['sl_hits']} / {r['total']} (WR {wr}%)\n"
    if s.get("by_direction"):
        text += "\n📈 *По направлению:*\n"
        for r in s["by_direction"]:
            wr = round(r["wins"] / r["total"] * 100, 1) if r["total"] else 0
            text += f"  {r['direction']}: ✅{r['wins']} ❌{r['sl_hits']} / {r['total']} (WR {wr}%)\n"
    if s.get("last_signals"):
        text += "\n📋 *Сигналы:*\n"
        for sig in s["last_signals"][:5]:
            text += f"  {sig['timestamp'][:16]} | {sig['symbol']} | {sig['signal_status']} | {sig.get('outcome','pending')}\n"
    else:
        text += "\n_(нет данных)_"
    await message.answer(text)


@dp.message(Command("startbot"))
async def cmd_startbot(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if is_main_bot_running():
        await message.answer("⚠️ Уже запущен")
        return
    ok = start_main_bot()
    await message.answer(f"✅ Запущен (PID {main_bot_process.pid})" if ok else "❌ Ошибка")


@dp.message(Command("stopbot"))
async def cmd_stopbot(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    if not is_main_bot_running():
        await message.answer("⚠️ Уже остановлен")
        return
    ok = stop_main_bot()
    await message.answer("🛑 Остановлен" if ok else "❌ Ошибка")


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    text = """📖 *Справка*

*Анализ:*
/scan BTC — анализ любой пары (SOL, XAG, DOGE...)
/analyze_all — анализ загруженных скриншотов
📎 Отправь 1–5 фото графиков → /analyze_all

*Портфель:*
/add XAGUSDT — добавить тикер в список
/remove XAGUSDT — удалить тикер
/settings — текущая конфигурация

*Таймфреймы и таймер:*
/timeframes — выбрать ТФ (15m, 1h, 4h, 1D)
/timer 30 — интервал авто-отчётов (мин 5)

*Автоскан (облако):*
/autoscan — статус автоскана
/autoscan 30 — установить интервал
/autoscan off — остановить
Кнопка «Автоскан: тикеры» — выбор монет для цикла

*Режимы:*
/auto — тогл авто-режима (только сигналы / все)
/filter on/off — фильтр сигналов

*Прочее:*
/export — скачать CSV бэктеста
/status — статус обоих ботов
/stats — статистика точности
/version — git HEAD
/startbot / stopbot — управл. основным ботом"""
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("version"))
async def cmd_version(message: Message) -> None:
    if message.from_user and message.from_user.id != ADMIN_CHAT_ID: return
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=PROJECT_DIR)
        head = r.stdout.strip()
        r2 = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True, text=True, cwd=PROJECT_DIR)
        await message.answer(f"📌 HEAD: `{head}`\n{r2.stdout.strip()}")
    except Exception as e:
        await message.answer(f"❌ {e}")


# ═══════════════════════════════════════════════════════════════════════════
# АВТОСКАН — последовательный цикл (BTC → 2мин → XAUT → пауза → повтор)
# ═══════════════════════════════════════════════════════════════════════════

_autoscan_running = False
# Phase 3 MT5: кэш последних результатов скана для /api/signals
_last_scan_results: dict[str, dict] = {}
_last_breakout_events: list[dict] = []


async def _autoscan_sequential_cycle(bot: Bot):
    """Последовательный цикл: символы по очереди, 2 мин пауза между, затем пауза до интервала."""
    global _autoscan_running
    global _last_scan_results
    global _last_breakout_events
    if not DASHBOARD_LLM_API_KEY:
        logging.warning("autoscan: DASHBOARD_LLM_API_KEY not set")
        return
    if _autoscan_running:
        return
    _autoscan_running = True

    symbols = _get_autoscan_symbols()
    interval_raw = _dash_get_setting("autoscan_interval", 15)
    try:
        interval = int(interval_raw)
    except (ValueError, TypeError):
        interval = 15
    inter_symbol_pause = 120  # 2 минуты между символами

    import core.config as cfg
    import core.scheduler as sched_mod
    from core.backtest import cleanup_old_signal_logs

    # Очистка старых записей при старте autoscan (retain 14 дней)
    try:
        deleted = cleanup_old_signal_logs(retain_days=14)
        if deleted > 0:
            logging.info("Autoscan startup: cleaned %d old signal_log records", deleted)
    except Exception as e:
        logging.warning("Autoscan startup: signal_log cleanup failed: %s", e)

    try:
        while _dash_get_setting("autoscan_active", False) and _autoscan_running:
            cycle_start = time.time()
            timeframes = sort_timeframes(_get_timeframes())

            for i, symbol in enumerate(symbols):
                if not _dash_get_setting("autoscan_active", False) or not _autoscan_running:
                    break
                label = SYMBOL_LABELS.get(symbol, symbol.replace("USDT", ""))
                logging.info(f"Autoscan: analyzing {label}...")

                # Автоскан: AUTO_SIGNAL_ONLY = True (только сигналы в TG)
                old_auto = cfg.AUTO_SIGNAL_ONLY
                old_my_chat = cfg.MY_CHAT_ID
                try:
                    cfg.MY_CHAT_ID = ADMIN_CHAT_ID
                    sched_mod.AUTO_SIGNAL_ONLY = True
                    sched_mod.ACTIONABLE_SIGNALS = ("aggressive_breakout", "retest", "reversal")
                    from core.scheduler import run_hourly_analysis
                    await run_hourly_analysis(
                        bot=bot,
                        symbol_filter=symbol,
                        llm_api_key=DASHBOARD_LLM_API_KEY,
                        llm_base_url=DASHBOARD_LLM_BASE_URL,
                        llm_model=DASHBOARD_MODEL_NAME,
                    )
                except Exception as e:
                    logging.error(f"autoscan {symbol} error: {e}")
                finally:
                    cfg.AUTO_SIGNAL_ONLY = old_auto
                    cfg.MY_CHAT_ID = old_my_chat

                # Phase 3 MT5: кэшируем результат для /api/signals
                try:
                    from core.scheduler import _last_analysis_cache
                    if _last_analysis_cache.get(symbol):
                        _last_scan_results[symbol] = _last_analysis_cache[symbol]
                except Exception:
                    pass

                # Пауза между символами
                if i < len(symbols) - 1 and _dash_get_setting("autoscan_active", False):
                    logging.info(f"Autoscan: pause {inter_symbol_pause}s before next symbol")
                    sleep_end = time.time() + inter_symbol_pause
                    while time.time() < sleep_end and _autoscan_running:
                        if not _dash_get_setting("autoscan_active", False):
                            break
                        await asyncio.sleep(min(10, sleep_end - time.time()))

            # Проверка исходов старых сигналов (старше CHECK_HORIZON_HOURS=4)
            if _dash_get_setting("autoscan_active", False) and _autoscan_running:
                try:
                    from core.backtest import check_pending_forecasts
                    # Собираем текущие цены для всех символов
                    current_prices = {}
                    for sym in symbols:
                        try:
                            tk = await fetch_ticker_safe(sym)
                            if tk and tk.get("last"):
                                current_prices[sym] = float(tk["last"])
                        except Exception as e:
                            logging.warning("Autoscan: fetch_ticker for backtest %s failed: %s", sym, e)
                    if current_prices:
                        checked = check_pending_forecasts(current_prices)
                        if checked > 0:
                            logging.info("Autoscan: checked %d pending forecasts (outcome assigned)", checked)
                except Exception as e:
                    logging.warning("Autoscan: check_pending_forecasts failed: %s", e)

            # Пауза после полного цикла
            if _dash_get_setting("autoscan_active", False) and _autoscan_running:
                elapsed = time.time() - cycle_start
                # Market-hours: рабочее окно (пн 08:00–пт 23:59) = base; вне окна = 60 фикс
                effective_interval = _effective_autoscan_interval()
                remaining = (effective_interval * 60) - elapsed
                if remaining > 0:
                    logging.info(f"Autoscan: cycle done, waiting {remaining:.0f}s (effective={effective_interval}min)")
                    sleep_end = time.time() + remaining
                    while time.time() < sleep_end and _autoscan_running:
                        if not _dash_get_setting("autoscan_active", False):
                            break
                        await asyncio.sleep(min(10, sleep_end - time.time()))
    except asyncio.CancelledError:
        logging.info("Autoscan: cancelled")
    finally:
        _autoscan_running = False


def _start_autoscan(bot: Bot) -> bool:
    global _autoscan_running
    if _autoscan_running:
        return False
    _dash_set_setting("autoscan_active", True)
    symbols = _get_autoscan_symbols()
    interval = _effective_autoscan_interval()
    labels = ", ".join(SYMBOL_LABELS.get(s, s.replace("USDT", "")) for s in symbols)
    logging.info(f"Autoscan started: interval={interval}min (effective, weekend-aware), symbols=[{labels}]")
    asyncio.create_task(_autoscan_sequential_cycle(bot))
    return True


def _stop_autoscan() -> bool:
    global _autoscan_running
    _autoscan_running = False
    _dash_set_setting("autoscan_active", False)
    logging.info("Autoscan stopped")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# FLASK WEB DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 MT5: /api/signals — зоны + пробои для MT5 индикатора/советника
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/signals")
def api_signals():
    """Отдаёт последние кэшированные зоны + пробои для MT5 индикатора.
    MT5 индикатор вызывает WebRequest("GET", "http://localhost:5000/api/signals")
    каждые N секунд и рисует horizontal lines зон + стрелки пробоев."""
    import core.scheduler as sched
    from core.db import get_pending_breakout_events

    symbols = _get_autoscan_symbols()
    result = {
        "server_time": datetime.now().isoformat() + "Z",
        "symbols": {},
    }

    for sym in symbols:
        cached = _last_scan_results.get(sym) or sched._last_analysis_cache.get(sym, {})
        if not cached:
            continue
        tf_zones = cached.get("tf_zones", {})
        zones_out = {}
        for tf, z in tf_zones.items():
            if isinstance(z, dict):
                zones_out[tf] = {
                    "upper": z.get("upper"),
                    "lower": z.get("lower"),
                }
        result["symbols"][sym] = {
            "price": cached.get("live_price", 0),
            "signal_status": cached.get("signal_status", "unknown"),
            "signal_direction": cached.get("signal_direction", ""),
            "phase": cached.get("phase", ""),
            "zones": zones_out,
            "risk_management": cached.get("risk_management", {}),
            "entry_price": cached.get("entry_price"),
            "timestamp": cached.get("timestamp", ""),
        }

    # Pending breakout events (для стрелок пробоев на индикаторе)
    try:
        events = []
        for sym in symbols:
            for ev in get_pending_breakout_events(sym, max_age_minutes=60):
                events.append(ev)
        result["breakout_events"] = events
    except Exception:
        result["breakout_events"] = []

    # Файловый fallback для MT5 (если WebRequest не работает — err=4006)
    # Пишет в MT5 Common\Files\ (доступно индикатору без WebRequest)
    try:
        import os, json as _json
        # MT5 Common\Files path: C:\Users\<user>\AppData\Roaming\MetaQuotes\Terminal\Common\Files
        common_dir = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files")
        os.makedirs(common_dir, exist_ok=True)
        for sym, data in result["symbols"].items():
            fn = os.path.join(common_dir, f"signals_{sym}.json")
            with open(fn, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

    return jsonify(result)


@app.route("/api/breakout_stats")
def api_breakout_stats():
    """Статистика пробоев по символам (для обучения volume thresholds)."""
    from core.db import get_breakout_stats
    symbols = _get_autoscan_symbols()
    return jsonify({sym: get_breakout_stats(sym) for sym in symbols})


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    # Phase 3: init breakout_events table
    init_breakout_events_table()
    # DISABLED: auto-start main.py (@KXROBObot) to avoid TelegramConflictError.
    # Two bots polling same token caused "AUTO ЗАПУСК 15М НЕ СРАБОТАЛ" — only one
    # bot (dashboard, @my_hermes_lokal_ai_bot) should run. Enable manually via /startbot.
    # if not is_main_bot_running():
    #     start_main_bot()
    #     logging.info(f"Main bot started (PID {main_bot_process.pid})")
    logging.info("Auto-start main.py DISABLED — only dashboard bot will run")

    def run_flask():
        app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info(f"Web dashboard: http://localhost:{WEB_PORT}")

    session = AiohttpSession()
    bot = Bot(token=DASH_TOKEN, session=session)
    await bot.set_my_commands([
        BotCommand(command="help", description="📖 Справка по командам"),
        BotCommand(command="start", description="Меню с кнопками"),
        BotCommand(command="scan", description="Анализ: /scan BTC или /scan SOL"),
        BotCommand(command="add", description="Добавить тикер"),
        BotCommand(command="remove", description="Удалить тикер"),
        BotCommand(command="settings", description="Настройки"),
        BotCommand(command="timeframes", description="Таймфреймы"),
        BotCommand(command="timer", description="Интервал авто-отчётов"),
        BotCommand(command="filter", description="Фильтр сигналов"),
        BotCommand(command="auto", description="Тогл авто-режима"),
        BotCommand(command="export", description="Экспорт CSV"),
        BotCommand(command="analyze_all", description="Анализ скриншотов"),
        BotCommand(command="autoscan", description="Автоскан: /autoscan 30"),
        BotCommand(command="status", description="Статус ботов"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="startbot", description="Запустить основной бот"),
        BotCommand(command="stopbot", description="Остановить основной бот"),
        BotCommand(command="version", description="Git HEAD"),
    ])
    me = await bot.get_me()
    logging.info(f"Dashboard bot: @{me.username} (id={me.id})")

    # Auto-resume autoscan if it was active before restart
    if _dash_get_setting("autoscan_active", False):
        logging.info("Autoscan was active — resuming...")
        _start_autoscan(bot)

    await dp.start_polling(bot)

if __name__ == "__main__":
    # setup_logging() уже вызван выше (строка 23) — basicConfig не нужен
    print(f"📊 Dashboard Bot + Web: http://localhost:{WEB_PORT}")
    asyncio.run(main())