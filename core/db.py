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