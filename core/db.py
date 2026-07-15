import sqlite3
import os
import json
import pandas as pd
from datetime import datetime
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'forecasts.db')


def init_all_tables() -> None:
    """Create all DB tables if they don't exist."""
    init_db()
    init_settings()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS forecasts 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, timestamp TEXT, 
                  pred_trend TEXT, pred_price REAL, pred_target REAL, 
                  actual_price_1h REAL, is_correct INTEGER, telegram_msg_id INTEGER)''')
    conn.commit()
    conn.close()


def init_settings() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''INSERT OR IGNORE INTO settings (key, value) VALUES ('symbols', '["BTCUSDT", "ETHUSDT", "XAUTUSDT"]')''')
    c.execute('''INSERT OR IGNORE INTO settings (key, value) VALUES ('interval_minutes', '60')''')
    c.execute('''INSERT OR IGNORE INTO settings (key, value) VALUES ('timeframes', '["15m","1h","4h","1D"]')''')
    c.execute('''INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_mode', 'false')''')
    conn.commit()
    conn.close()


def get_setting(key: str, default: Optional[Any] = None) -> Any:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    if row is None:
        if default is not None:
            set_setting(key, default)
            return default
        return None
    val = row[0]
    # ✅ Автоматический парсинг списков
    if key in ('symbols', 'timeframes'):
        return json.loads(val)
    if key == 'interval_minutes':
        return int(val)
    return val


def set_setting(key: str, value: Any) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    str_val = json.dumps(value) if isinstance(value, list) else str(value)
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str_val))
    conn.commit()
    conn.close()


def save_forecast(asset: str, pred_trend: str, pred_price: float, pred_target: float, msg_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO forecasts (asset, timestamp, pred_trend, pred_price, pred_target, telegram_msg_id) VALUES (?, ?, ?, ?, ?, ?)",
              (asset, datetime.utcnow().isoformat(), pred_trend, pred_price, pred_target, msg_id))
    conn.commit()
    conn.close()
    return c.lastrowid


def update_actual_prices(prices: dict[str, float]) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for asset, price in prices.items():
        # 1. Находим неотработанные прогнозы (>1 часа назад)
        c.execute("""SELECT id, pred_trend, pred_price FROM forecasts 
                     WHERE asset = ? AND actual_price_1h IS NULL 
                     AND datetime(timestamp, '+60 minutes') < datetime('now')""", (asset,))
        forecasts = c.fetchall()
        
        # 2. Корректно считаем is_correct в Python (безопасно для SQLite)
        for f_id, trend, pred_price in forecasts:
            is_correct = 0
            if trend == 'Long' and price > pred_price:
                is_correct = 1
            elif trend == 'Short' and price < pred_price:
                is_correct = 1
            c.execute("UPDATE forecasts SET actual_price_1h = ?, is_correct = ? WHERE id = ?", 
                      (price, is_correct, f_id))
    conn.commit()
    conn.close()


def init_breakout_events_table() -> None:
    """Phase 3: breakout_events — фиксация и трекинг пробоев уровней."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS breakout_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        level_type TEXT,
        level_price REAL,
        breakout_dir TEXT,
        volume_ratio REAL,
        confirmed INTEGER DEFAULT 0,
        confirmed_at TEXT,
        outcome TEXT,
        candles_after INTEGER DEFAULT 0
    )''')
    # Индекс для быстрого поиска незакрытых событий
    c.execute('''CREATE INDEX IF NOT EXISTS idx_breakout_pending ON breakout_events(symbol, confirmed)''')
    conn.commit()
    conn.close()


def save_breakout_event(symbol: str, timeframe: str, level_type: str,
                        level_price: float, breakout_dir: str,
                        volume_ratio: float) -> int:
    """Сохраняет новый пробой (confirmed=0 — pending)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO breakout_events
        (timestamp, symbol, timeframe, level_type, level_price, breakout_dir, volume_ratio, confirmed)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)''',
        (datetime.utcnow().isoformat(), symbol, timeframe, level_type,
         level_price, breakout_dir, volume_ratio))
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id


def get_pending_breakout_events(symbol: str, max_age_minutes: int = 45) -> list[dict]:
    """Возвращает незакрытые пробои для символа (для подтверждения через N циклов)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT id, timestamp, symbol, timeframe, level_type, level_price,
                 breakout_dir, volume_ratio, candles_after
                 FROM breakout_events
                 WHERE symbol = ? AND confirmed = 0
                 AND datetime(timestamp, '+' || ? || ' minutes') > datetime('now')
                 ORDER BY timestamp ASC''',
              (symbol, str(max_age_minutes)))
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "timestamp": r[1], "symbol": r[2], "timeframe": r[3],
         "level_type": r[4], "level_price": r[5], "breakout_dir": r[6],
         "volume_ratio": r[7], "candles_after": r[8]}
        for r in rows
    ]


def confirm_breakout_event(event_id: int, confirmed: int, outcome: str = "",
                            candles_after: int = 0) -> None:
    """Подтверждает пробой: confirmed=1 (истинный), -1 (ложный), outcome='continued'/'reversed'/'retest'."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''UPDATE breakout_events
                 SET confirmed = ?, confirmed_at = ?, outcome = ?, candles_after = ?
                 WHERE id = ?''',
              (confirmed, datetime.utcnow().isoformat(), outcome, candles_after, event_id))
    conn.commit()
    conn.close()


def get_breakout_stats(symbol: str) -> dict:
    """Возвращает статистику пробоев по символу (для обучения volume thresholds)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT
        COUNT(*) as total,
        AVG(CASE WHEN confirmed = 1 THEN volume_ratio END) as avg_vol_true,
        AVG(CASE WHEN confirmed = -1 THEN volume_ratio END) as avg_vol_false,
        SUM(CASE WHEN confirmed = 1 THEN 1 ELSE 0 END) as true_count,
        SUM(CASE WHEN confirmed = -1 THEN 1 ELSE 0 END) as false_count
        FROM breakout_events
        WHERE symbol = ? AND confirmed != 0''', (symbol,))
    row = c.fetchone()
    conn.close()
    total, avg_vol_true, avg_vol_false, true_count, false_count = row
    return {
        "total_confirmed": total or 0,
        "true_breakouts": true_count or 0,
        "false_breakouts": false_count or 0,
        "avg_volume_true": round(avg_vol_true, 2) if avg_vol_true else None,
        "avg_volume_false": round(avg_vol_false, 2) if avg_vol_false else None,
    }


def get_backtest_stats() -> dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(is_correct), AVG(CASE WHEN is_correct IS NOT NULL THEN ABS((actual_price_1h - pred_price)/pred_price) ELSE NULL END) FROM forecasts WHERE is_correct IS NOT NULL")
    row = c.fetchone()
    total, wins, avg_dev = row
    win_rate = (wins / total * 100) if total > 0 else 0
    conn.close()
    return {"total": total, "wins": wins, "win_rate": round(win_rate, 1), "mae_pct": round(avg_dev * 100, 2) if avg_dev else 0}


def get_history_df() -> str:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM forecasts ORDER BY timestamp DESC LIMIT 100", conn)
    conn.close()
    return df.to_csv(index=False, sep=';')