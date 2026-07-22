"""
Position tracker — stateful tracking of open positions with partial closes,
breakeven on TP1, and reverse-signal handling.

Architecture:
- Table `positions` in forecasts.db stores open/closed positions.
- `update_positions_on_tick(prices)` called every autoscan tick:
    * Check TP1 → close 50%, move SL to entry (breakeven)
    * Check TP2 → close half of remaining (25%)
    * Check TP3 → close remaining (25%)
    * Check SL → close whatever remains
- `handle_new_signal(parsed, symbol_id)` called on every new actionable signal:
    * If open position exists for symbol and direction matches → ignore (or update SL/TP)
    * If open position exists and direction REVERSES → close old, open new
    * If no open position → open new

Position lifecycle (long example, entry=100, sl=95, tp1=105, tp2=110, tp3=115):
  1. OPEN:        size=1.0,  sl=95,   status=open
  2. price>=105 (TP1):  close 0.5 @105, sl→100 (breakeven), size=0.5, status=breakeven_active
  3. price>=110 (TP2):  close 0.25@110, size=0.25
  4. price>=115 (TP3):  close 0.25@115, size=0, status=closed_tp3
  5. price<=100 (SL after breakeven): close 0.5 @100, size=0, status=closed_breakeven

Reverse signal (long open, new short signal):
  - Close remaining at current price
  - Open new short position with new entry/sl/tp1/tp2/tp3
"""
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_DB_PATH = "forecasts.db"

# Statuses
STATUS_OPEN = "open"
STATUS_BREAKEVEN = "breakeven_active"
STATUS_CLOSED_TP = "closed_tp"
STATUS_CLOSED_SL = "closed_sl"
STATUS_CLOSED_BREAKEVEN = "closed_breakeven"
STATUS_CLOSED_REVERSE = "closed_reverse"

# Actionable signals that open positions
ACTIONABLE_SIGNALS = {
    "aggressive_breakout",
    "false_breakout",
    "false_breakout_down",
    "false_breakout_up",
    "retest",
    "breakout_retest",
    "reversal",
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_positions_table() -> None:
    """Create positions table if not exists."""
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                direction TEXT NOT NULL,           -- 'long' | 'short'
                entry_price REAL NOT NULL,
                sl_original REAL,
                tp1 REAL, tp2 REAL, tp3 REAL,
                sl_current REAL,                   -- updated after breakeven
                remaining_size REAL DEFAULT 1.0,   -- 1.0 → 0.5 → 0.25 → 0
                breakeven_active INTEGER DEFAULT 0,
                status TEXT DEFAULT 'open',
                signal_log_id INTEGER,             -- link to signal_log
                closed_at TEXT,
                close_reason TEXT,                 -- tp1/tp2/tp3/sl/breakeven/reverse
                realised_rr REAL,
                notes TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol_status ON positions(symbol, status)")
        conn.commit()
        logger.info("positions table ready")
    except Exception as e:
        logger.error("init_positions_table failed: %s", e)
    finally:
        conn.close()


def get_open_position(symbol: str) -> Optional[sqlite3.Row]:
    """Return the open position for a symbol, or None."""
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM positions WHERE symbol=? AND status IN ('open','breakeven_active') ORDER BY id DESC LIMIT 1",
            (symbol,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _open_position(symbol: str, direction: str, entry: float, sl: Optional[float],
                   tp1: Optional[float], tp2: Optional[float], tp3: Optional[float],
                   signal_log_id: Optional[int] = None) -> int:
    """Open a new position. Returns position id."""
    conn = _conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute("""
            INSERT INTO positions
                (symbol, opened_at, direction, entry_price, sl_original, tp1, tp2, tp3,
                 sl_current, remaining_size, breakeven_active, status, signal_log_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0, 'open', ?)
        """, (symbol, now, direction, entry, sl, tp1, tp2, tp3, sl, signal_log_id))
        conn.commit()
        pos_id = cur.lastrowid
        logger.info("POSITION OPEN id=%s %s %s entry=%.4f sl=%s tp1=%s tp2=%s tp3=%s",
                    pos_id, symbol, direction, entry, sl, tp1, tp2, tp3)
        return pos_id
    finally:
        conn.close()


def _close_position(pos_id: int, close_price: float, size: float, reason: str,
                    entry: float, sl_original: Optional[float]) -> None:
    """Partially or fully close a position. Updates realised_rr."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
        if not row:
            return
        direction = row["direction"]
        remaining = row["remaining_size"] - size
        # Per-trade RR for this partial:
        risk = abs(entry - (sl_original or entry * 0.99))
        # reward со знаком: long → close выше entry = +, ниже = −; short → наоборот
        # abs() убирал знак → loss записывался как win (баг id=10 ETH short rr=1.56)
        reward = (close_price - entry) if direction == "long" else (entry - close_price)
        partial_rr = round(reward / risk, 2) if risk > 0 else 0
        blurb = f"{reason}@{close_price:.2f} size={size:.2f} rr={partial_rr}"
        notes_field = (row["notes"] + "\n" if row["notes"] else "") + blurb

        if remaining <= 0.0001:
            conn.execute("""
                UPDATE positions
                SET remaining_size=0, status=?, closed_at=?, close_reason=?, realised_rr=?,
                    notes=?
                WHERE id=?
            """, (
                STATUS_CLOSED_TP if reason.startswith("tp") else
                STATUS_CLOSED_SL if reason == "sl" else
                STATUS_CLOSED_BREAKEVEN if reason == "breakeven" else
                STATUS_CLOSED_REVERSE,
                datetime.now(timezone.utc).isoformat(), reason, partial_rr, notes_field, pos_id
            ))
            logger.info("POSITION CLOSED id=%s %s reason=%s price=%.4f rr=%.2f",
                        pos_id, row["symbol"], reason, close_price, partial_rr)
        else:
            conn.execute("""
                UPDATE positions SET remaining_size=?, notes=? WHERE id=?
            """, (remaining, notes_field, pos_id))
            logger.info("POSITION PARTIAL CLOSE id=%s %s reason=%s price=%.4f size=%.2f remaining=%.2f",
                        pos_id, row["symbol"], reason, close_price, size, remaining)
        conn.commit()
    finally:
        conn.close()


def _activate_breakeven(pos_id: int, entry: float) -> None:
    """Move SL to entry (breakeven)."""
    conn = _conn()
    try:
        conn.execute("""
            UPDATE positions
            SET sl_current=?, breakeven_active=1, status='breakeven_active'
            WHERE id=?
        """, (entry, pos_id))
        conn.commit()
        logger.info("POSITION BREAKEVEN id=%s sl→entry=%.4f", pos_id, entry)
    finally:
        conn.close()


def update_positions_on_tick(prices: Dict[str, float]) -> int:
    """
    Check all open positions against current prices.
    Implements: TP1→50% close + breakeven, TP2→25%, TP3→25%, SL→close rest.

    Returns: number of position updates made.
    """
    conn = _conn()
    updated = 0
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status IN ('open','breakeven_active')"
        ).fetchall()
    finally:
        conn.close()

    for pos in rows:
        price = prices.get(pos["symbol"])
        if not price:
            continue
        entry = pos["entry_price"]
        direction = pos["direction"]
        sl = pos["sl_current"] or pos["sl_original"]
        tp1, tp2, tp3 = pos["tp1"], pos["tp2"], pos["tp3"]
        size = pos["remaining_size"]
        pos_id = pos["id"]

        # Determine hits based on direction
        if direction == "long":
            tp1_hit = tp1 is not None and price >= tp1
            tp2_hit = tp2 is not None and price >= tp2
            tp3_hit = tp3 is not None and price >= tp3
            sl_hit = sl is not None and price <= sl
        else:  # short
            tp1_hit = tp1 is not None and price <= tp1
            tp2_hit = tp2 is not None and price <= tp2
            tp3_hit = tp3 is not None and price <= tp3
            sl_hit = sl is not None and price >= sl

        # SL check first (protection) — only if not already breakeven-active and SL is original
        if sl_hit and not pos["breakeven_active"]:
            _close_position(pos_id, price, size, "sl", entry, pos["sl_original"])
            updated += 1
            continue

        # If breakeven active and price hit the breakeven SL (= entry) → close at breakeven
        if sl_hit and pos["breakeven_active"]:
            _close_position(pos_id, price, size, "breakeven", entry, pos["sl_original"])
            updated += 1
            continue

        # TP3 → close everything remaining
        if tp3_hit:
            _close_position(pos_id, price, size, "tp3", entry, pos["sl_original"])
            updated += 1
            continue

        # TP2 → close half of remaining
        if tp2_hit:
            partial = size / 2
            _close_position(pos_id, price, partial, "tp2", entry, pos["sl_original"])
            updated += 1
            continue

        # TP1 → close 50%, activate breakeven (if not already)
        if tp1_hit and not pos["breakeven_active"]:
            _close_position(pos_id, price, 0.5, "tp1", entry, pos["sl_original"])
            _activate_breakeven(pos_id, entry)
            updated += 1
            continue

    return updated


def handle_new_signal(parsed: Dict[str, Any], symbol: str, signal_log_id: Optional[int] = None) -> str:
    """
    Called on every new actionable signal. Opens/reverses/ignores positions.

    Returns: action taken — 'opened', 'reversed', 'ignored', 'skipped'
    """
    status = str(parsed.get("signal_status", "")).lower()
    if status not in ACTIONABLE_SIGNALS:
        return "skipped"

    # Direction: LLM may return signal_direction explicitly, otherwise infer from
    # signal_status (mirrors backtest._detect_direction logic).
    direction = str(parsed.get("signal_direction", "")).lower()
    if direction not in ("long", "short"):
        if "long" in status or "up" in status:
            direction = "long"
        elif "short" in status or "down" in status:
            direction = "short"
        elif "reversal" in status:
            trend = str(parsed.get("trend_structure", "")).lower()
            wave = str(parsed.get("wave_phase", "")).lower()
            direction = "short" if ("down" in trend or "down" in wave) else "long"
        elif "false_breakout" in status:
            ltf = str(parsed.get("ltf_structure", "")).lower()
            sub = str(parsed.get("current_substructure", "")).lower()
            if "up" in ltf or "up" in sub:
                direction = "short"
            elif "down" in ltf or "down" in sub:
                direction = "long"
            else:
                direction = "short"
        else:
            trend = str(parsed.get("trend_structure", "")).lower()
            ltf = str(parsed.get("ltf_structure", "")).lower()
            wave = str(parsed.get("wave_phase", "")).lower()
            if "up" in trend or "up" in ltf or "up" in wave or "bull" in trend:
                direction = "long"
            elif "down" in trend or "down" in ltf or "down" in wave or "bear" in trend:
                direction = "short"
            else:
                direction = "long"
    if direction not in ("long", "short"):
        return "skipped"

    risk = parsed.get("risk_management") or {}
    primary = risk.get("primary") if isinstance(risk, dict) else None
    # Try alternative if primary empty
    if not primary or not (primary.get("sl") and primary.get("tp1")):
        primary = risk.get("alternative") if isinstance(risk, dict) else None
    if not primary:
        return "skipped"

    entry = parsed.get("entry_price") or parsed.get("price")
    sl = primary.get("sl")
    tp1 = primary.get("tp1")
    tp2 = primary.get("tp2")
    tp3 = primary.get("tp3")

    if not (entry and sl and tp1):
        return "skipped"

    # SAFETY-NET: SL/TP direction validation
    # long: SL < entry, TP1 > entry | short: SL > entry, TP1 < entry
    # Если инвертированы — не открываем (защита от direction mismatch)
    entry_f = float(entry)
    sl_f = float(sl)
    tp1_f = float(tp1)
    if direction == "long":
        if sl_f >= entry_f or tp1_f <= entry_f:
            logger.warning(
                "POSITION skip: %s %s SL/TP inverted for long (entry=%.2f sl=%.2f tp1=%.2f)",
                symbol, direction, entry_f, sl_f, tp1_f,
            )
            return "skipped"
    elif direction == "short":
        if sl_f <= entry_f or tp1_f >= entry_f:
            logger.warning(
                "POSITION skip: %s %s SL/TP inverted for short (entry=%.2f sl=%.2f tp1=%.2f)",
                symbol, direction, entry_f, sl_f, tp1_f,
            )
            return "skipped"

    existing = get_open_position(symbol)

    if existing is None:
        _open_position(symbol, direction, float(entry), float(sl),
                       float(tp1) if tp1 else None,
                       float(tp2) if tp2 else None,
                       float(tp3) if tp3 else None,
                       signal_log_id)
        return "opened"

    # Position exists. Check direction.
    if existing["direction"] == direction:
        # Same direction — ignore, keep existing (SL/TP already set)
        return "ignored"

    # ANTI-CHURN: reverse guard.
    # Проблема: LLM меняет long↔short каждые 15-45 мин. 4 из 5 reverse-закрытий
    # приходили в минусе (rr<0.5) — цена не дошла до TP1, а трекер уже реверсировал.
    # Фикс: реверс только если выполнено ОДНО из условий:
    #   (a) цена дошла до TP1 (in profit) — reverse фиксирует прибыль
    #   (b) позиция открыта ≥90 мин — достаточно времени дойти до SL, тренд сменился по-настоящему
    # Иначе — ignore, ждём SL/TP.
    entry_existing = float(existing["entry_price"])
    tp1_existing = existing["tp1"]
    opened_at_str = existing["opened_at"]
    cur_price = parsed.get("price") or entry

    # Parse opened_at to datetime
    try:
        from datetime import datetime as _dt
        opened_dt = _dt.fromisoformat(opened_at_str)
        now_dt = _dt.now(timezone.utc)
        hold_minutes = (now_dt - opened_dt).total_seconds() / 60.0
    except Exception:
        hold_minutes = 999.0  # если не распарсилось — пропускаем guard

    in_profit = False
    if tp1_existing is not None:
        tp1_f = float(tp1_existing)
        cur_price_f = float(cur_price)
        if existing["direction"] == "long" and cur_price_f >= tp1_f:
            in_profit = True
        elif existing["direction"] == "short" and cur_price_f <= tp1_f:
            in_profit = True

    MIN_HOLD_MINUTES = 90  # 6 autoscan циклов по 15 мин

    if not in_profit and hold_minutes < MIN_HOLD_MINUTES:
        logger.info(
            "POSITION reverse-blocked id=%s %s %s: hold=%.0fm < %dm, not in profit (entry=%.2f tp1=%s price=%.2f)",
            existing["id"], symbol, existing["direction"],
            hold_minutes, MIN_HOLD_MINUTES, entry_existing, tp1_existing, cur_price,
        )
        return "ignored"

    # Reverse: close existing at current price, open new
    _close_position(existing["id"], float(cur_price), existing["remaining_size"],
                    "reverse", entry_existing, existing["sl_original"])
    _open_position(symbol, direction, float(entry), float(sl),
                   float(tp1) if tp1 else None,
                   float(tp2) if tp2 else None,
                   float(tp3) if tp3 else None,
                   signal_log_id)
    return "reversed"


def get_positions_stats() -> Dict[str, Any]:
    """Summary stats for positions."""
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        open_count = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status IN ('open','breakeven_active')"
        ).fetchone()[0]
        closed_tp = conn.execute("SELECT COUNT(*) FROM positions WHERE status='closed_tp'").fetchone()[0]
        closed_sl = conn.execute("SELECT COUNT(*) FROM positions WHERE status='closed_sl'").fetchone()[0]
        closed_brk = conn.execute("SELECT COUNT(*) FROM positions WHERE status='closed_breakeven'").fetchone()[0]
        closed_rev = conn.execute("SELECT COUNT(*) FROM positions WHERE status='closed_reverse'").fetchone()[0]
        return {
            "total": total, "open": open_count,
            "closed_tp": closed_tp, "closed_sl": closed_sl,
            "closed_breakeven": closed_brk, "closed_reverse": closed_rev,
        }
    finally:
        conn.close()
