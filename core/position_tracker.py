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
                notes TEXT,
                trailing_peak REAL                  -- max/min price after TP1 (for trailing SL)
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


def _init_trailing_peak(pos_id: int, price: float) -> None:
    """Initialize trailing_peak after TP1 hit."""
    conn = _conn()
    try:
        conn.execute("UPDATE positions SET trailing_peak=? WHERE id=?", (price, pos_id))
        conn.commit()
    finally:
        conn.close()


def _update_trailing_sl(pos_id: int, new_sl: float, new_peak: float, direction: str) -> None:
    """Update trailing SL and peak. SL only moves in favour (long: up, short: down)."""
    conn = _conn()
    try:
        conn.execute("""
            UPDATE positions SET sl_current=?, trailing_peak=? WHERE id=?
        """, (new_sl, new_peak, pos_id))
        conn.commit()
        logger.info("POSITION TRAILING id=%s %s sl→%.4f peak=%.4f", pos_id, direction, new_sl, new_peak)
    finally:
        conn.close()


def update_positions_on_tick(prices: Dict[str, float]) -> int:
    """
    Check all open positions against current prices.
    Implements: TP1→50% close + breakeven, TP2→25% + trailing, TP3→25%, SL→close rest.
    Trailing: after TP2, SL follows price (long: SL = peak - 1×risk, short: SL = trough + 1×risk).

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
        be_active = pos["breakeven_active"]
        trailing_peak = pos["trailing_peak"]

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
        if sl_hit and not be_active:
            _close_position(pos_id, price, size, "sl", entry, pos["sl_original"])
            updated += 1
            continue

        # If breakeven active and price hit the breakeven SL (= entry) → close at breakeven
        if sl_hit and be_active:
            _close_position(pos_id, price, size, "breakeven", entry, pos["sl_original"])
            updated += 1
            continue

        # TP3 → close everything remaining
        if tp3_hit:
            _close_position(pos_id, price, size, "tp3", entry, pos["sl_original"])
            updated += 1
            continue

        # TP2 → close half of remaining + activate trailing
        if tp2_hit:
            partial = size / 2
            _close_position(pos_id, price, partial, "tp2", entry, pos["sl_original"])
            # Trailing SL to TP1 level (lock profit)
            _update_trailing_sl(pos_id, tp1, price, direction)
            updated += 1
            continue

        # TP1 → close 50%, activate breakeven (if not already)
        if tp1_hit and not be_active:
            _close_position(pos_id, price, 0.5, "tp1", entry, pos["sl_original"])
            _activate_breakeven(pos_id, entry)
            # Initialize trailing peak
            _init_trailing_peak(pos_id, price)
            updated += 1
            continue

        # Trailing: after TP1+breakeven, if price moves favourably, trail SL
        # Long: SL = peak - 1×risk (risk = entry - sl_original)
        # Short: SL = trough + 1×risk
        if be_active and tp1_hit and not tp2_hit:
            # Update trailing peak and SL
            risk = abs(entry - (pos["sl_original"] or entry * 0.99))
            if risk > 0:
                if direction == "long":
                    new_peak = max(trailing_peak or price, price)
                    new_sl = new_peak - risk  # trail by 1×risk behind peak
                    # Only move SL up, never down; SL must be > entry (breakeven lock)
                    cur_sl = pos["sl_current"] or entry
                    if new_sl > cur_sl and new_sl > entry:
                        _update_trailing_sl(pos_id, new_sl, new_peak, direction)
                        updated += 1
                else:  # short
                    new_peak = min(trailing_peak or price, price)
                    new_sl = new_peak + risk  # trail by 1×risk below trough
                    cur_sl = pos["sl_current"] or entry
                    # Only move SL down, never up; SL must be < entry
                    if new_sl < cur_sl and new_sl < entry:
                        _update_trailing_sl(pos_id, new_sl, new_peak, direction)
                        updated += 1

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

    # EMA200 trend filter (4H): не открывать против тренда
    # long только если price > EMA200, short только если price < EMA200
    ema200_htf = parsed.get("ema200_htf")
    if ema200_htf is not None:
        try:
            ema200_f = float(ema200_htf)
            if direction == "long" and entry_f < ema200_f:
                logger.info(
                    "POSITION skip: %s long blocked by EMA200 filter (price=%.2f < ema200=%.2f)",
                    symbol, entry_f, ema200_f,
                )
                return "skipped"
            if direction == "short" and entry_f > ema200_f:
                logger.info(
                    "POSITION skip: %s short blocked by EMA200 filter (price=%.2f > ema200=%.2f)",
                    symbol, entry_f, ema200_f,
                )
                return "skipped"
        except (TypeError, ValueError):
            pass  # ema200 не число — пропускаем фильтр

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

    # REVERSE DISABLED: позиция держится до TP1/TP2/TP3/SL.
    # Бэктест показал: hold-to-TP1/SL даёт WR=50% vs 12% с reverse.
    # LLM меняет long↔short каждые 15-45 мин → churn уничтожает позиции.
    # Теперь: opposite-direction сигнал игнорируется, ждём исхода текущей позиции.
    logger.info(
        "POSITION reverse-disabled id=%s %s %s: new signal %s ignored, waiting for TP/SL (entry=%.2f sl=%s tp1=%s)",
        existing["id"], symbol, existing["direction"], direction,
        float(existing["entry_price"]), existing["sl_current"] or existing["sl_original"], existing["tp1"],
    )
    return "ignored"


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
