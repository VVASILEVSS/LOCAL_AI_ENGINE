"""
P3-1: Backtest pipeline — сохраняет полные LLM-прогнозы и проверяет точность.

Таблица signal_log:
  - Каждый прогноз сохраняется с signal_status, direction, SL, TP1, confidence
  - Через N часов проверяется: достиг ли цена TP/SL?
  - Статистика формируется для LLM-контекста (accuracy%, avg RR realised)

Интеграция:
  scheduler.py → save_signal_log(parsed, symbol, timeframes)
  scheduler.py → backtest_context = get_backtest_context(symbol)
  prev_ctx["backtest"] = backtest_context
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'forecasts.db')

# Сколько часов ждать перед проверкой прогноза
CHECK_HORIZON_HOURS = 4


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_backtest_table() -> None:
    """Создать таблицу signal_log если не существует."""
    c = _conn().cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            signal_status TEXT,
            direction TEXT,
            entry_price REAL,
            sl REAL,
            tp1 REAL,
            tp2 REAL,
            tp3 REAL,
            rr_planned REAL,
            confidence TEXT,
            htf_structure TEXT,
            abc_risk TEXT,

            -- Поля проверки (заполняются позже)
            checked_at TEXT,
            actual_price REAL,
            sl_hit INTEGER,
            tp1_hit INTEGER,
            tp2_hit INTEGER,
            tp3_hit INTEGER,
            max_favorable REAL,
            max_adverse REAL,
            outcome TEXT,
            rr_realised REAL,

            -- Зоны для backtest no_signal (цена вышла за resistance/support?)
            zone_upper REAL,
            zone_lower REAL,
            key_resistance REAL,
            key_support REAL,

            -- Метаданные
            consistency_runs INTEGER,
            consistency_agreed INTEGER,
            prompt_variant TEXT DEFAULT 'A',
            raw_json TEXT
        )
    """)
    # Индекс для быстрого поиска непроверенных
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_log_pending
        ON signal_log(symbol, timestamp)
        WHERE checked_at IS NULL
    """)
    # Миграция — добавить колонки если отсутствуют (existing DB)
    for col_sql in [
        "ALTER TABLE signal_log ADD COLUMN prompt_variant TEXT DEFAULT 'A'",
        "ALTER TABLE signal_log ADD COLUMN zone_upper REAL",
        "ALTER TABLE signal_log ADD COLUMN zone_lower REAL",
        "ALTER TABLE signal_log ADD COLUMN key_resistance REAL",
        "ALTER TABLE signal_log ADD COLUMN key_support REAL",
    ]:
        try:
            c.execute(col_sql)
        except Exception:
            pass  # колонка уже существует
    _conn().commit()
    _conn().close()
    logger.info("backtest table ready")


def cleanup_old_signal_logs(retain_days: int = 14) -> int:
    """Удалить записи signal_log старше retain_days. Возвращает количество удалённых."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM signal_log WHERE timestamp < ?", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info("signal_log cleanup: deleted %d records older than %d days", deleted, retain_days)
    return deleted


def save_signal_log(
    parsed: dict,
    symbol: str,
    timeframes: list[str] | None = None,
    prompt_variant: str = "A",
) -> int | None:
    """Сохранить полный прогноз в signal_log. Возвращает id строки."""
    if not isinstance(parsed, dict):
        return None

    try:
        # Определяем direction
        status = str(parsed.get("signal_status", "")).lower()
        direction = _detect_direction(parsed, status)

        # Извлекаем risk management
        rm = parsed.get("risk_management")
        primary = rm.get("primary", {}) if isinstance(rm, dict) else {}

        # Consistency info
        consistency = parsed.get("_consistency") or {}
        runs = consistency.get("runs", 1)
        agreed = 1 if consistency.get("agreed", True) else 0

        # entry_price = live_price (current_price), а не last_closed_price.
        # LLM часто возвращает price = last_closed_price, но для backtest
        # нужна реальная цена на момент прогноза.
        entry = _safe_float(parsed.get("current_price") or parsed.get("price"))
        sl = _safe_float(primary.get("sl"))
        tp1 = _safe_float(primary.get("tp1"))
        tp2 = _safe_float(primary.get("tp2"))
        tp3 = _safe_float(primary.get("tp3"))
        rr = _safe_float(primary.get("rr"))

        now = datetime.now(timezone.utc).isoformat()
        variant = (prompt_variant or "A").upper()[:1]

        conn = _conn()
        c = conn.cursor()

        # ── DEDUP: не дублировать сигнал, если он не изменился ──────────
        # Концепция: «если цель есть — либо тейк, либо стоп».
        # TP/SL фиксируются при первом расчёте и НЕ пересчитываются каждый цикл.
        # Новая запись создаётся только если:
        #   (a) сменился signal_status (aggressive_breakout → retest / no_signal / ...)
        #   (b) сменилось direction (long → short)
        #   (c) нет предыдущей активной записи для этого symbol+status+direction
        direction_norm = (direction or "").lower()
        c.execute(
            "SELECT id, entry_price, sl, tp1, tp2, tp3 FROM signal_log "
            "WHERE symbol = ? AND signal_status = ? AND direction = ? "
            "ORDER BY id DESC LIMIT 1",
            (symbol, status, direction_norm),
        )
        prev = c.fetchone()
        if prev is not None:
            prev_id, prev_entry, prev_sl, prev_tp1, prev_tp2, prev_tp3 = prev
            # Проверяем: сработал ли TP или SL предыдущего сигнала?
            # Если да — предыдущий трейд закрыт, новый сетап → разрешаем INSERT.
            # Если нет — сигнал активен, TP/SL зафиксированы → пропускаем (dedup).
            hit = False
            cur_price = entry  # entry = live_price (current_price)
            if cur_price is not None:
                if direction_norm == "long":
                    if prev_sl is not None and cur_price <= prev_sl:
                        hit = True
                    elif prev_tp1 is not None and cur_price >= prev_tp1:
                        hit = True
                elif direction_norm == "short":
                    if prev_sl is not None and cur_price >= prev_sl:
                        hit = True
                    elif prev_tp1 is not None and cur_price <= prev_tp1:
                        hit = True
            if not hit:
                # Сигнал не изменился и TP/SL не сработали → пропускаем INSERT.
                # TP/SL зафиксированы до исхода (трейд активен).
                conn.close()
                logger.info(
                    "signal_log dedup: %s %s %s unchanged (id=%d) — TP/SL зафиксированы до исхода",
                    symbol, status, direction_norm, prev_id,
                )
                return prev_id
            else:
                logger.info(
                    "signal_log: %s %s %s — prev TP/SL hit (id=%d), новый сетап → INSERT",
                    symbol, status, direction_norm, prev_id,
                )

        # P3: Извлечь зоны для backtest no_signal прогнозов.
        # Берём LTF зону (последний ТФ) как рабочую + key_zones.
        # Phase 2: зона может быть {range: [low, high], bos_price, bos_dir, bos_age}
        # или legacy {upper, lower}. Извлекаем upper/lower из range если есть.
        tf_zones = parsed.get("tf_zones") or {}
        # Последняя зона по каноническому порядку (самый младший ТФ)
        ltf_zone = None
        for tf_key in ["5M", "15M", "1H", "4H", "1D"]:
            z = tf_zones.get(tf_key)
            if isinstance(z, dict) and (z.get("upper") is not None or z.get("lower") is not None
                                        or (isinstance(z.get("range"), list) and len(z["range"]) == 2)):
                ltf_zone = z
        # Если канонического нет — берём последнюю из dict
        if ltf_zone is None and tf_zones:
            last_key = list(tf_zones.keys())[-1]
            ltf_zone = tf_zones[last_key]

        # Нормализуем upper/lower (с поддержкой Phase 2 range)
        def _zone_bounds(z):
            if not isinstance(z, dict):
                return None, None
            rng = z.get("range")
            if isinstance(rng, list) and len(rng) == 2:
                return _safe_float(rng[0]), _safe_float(rng[1])
            return _safe_float(z.get("lower")), _safe_float(z.get("upper"))

        zone_lower, zone_upper = _zone_bounds(ltf_zone)

        key_zones = parsed.get("key_zones") or {}
        key_resistance = _safe_float(key_zones.get("resistance"))
        key_support = _safe_float(key_zones.get("support"))

        c.execute("""
            INSERT INTO signal_log
              (symbol, timestamp, signal_status, direction, entry_price,
               sl, tp1, tp2, tp3, rr_planned, confidence, htf_structure, abc_risk,
               zone_upper, zone_lower, key_resistance, key_support,
               consistency_runs, consistency_agreed, prompt_variant, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, now, status, direction, entry,
            sl, tp1, tp2, tp3, rr,
            parsed.get("confidence"),
            parsed.get("htf_structure"),
            parsed.get("abc_risk"),
            zone_upper, zone_lower, key_resistance, key_support,
            runs, agreed, variant,
            json.dumps(parsed, ensure_ascii=False, default=str)[:8000],
        ))
        conn.commit()
        row_id = c.lastrowid
        conn.close()
        logger.info("signal_log saved: id=%d %s %s dir=%s variant=%s", row_id, symbol, status, direction, variant)
        return row_id
    except Exception as e:
        logger.warning("save_signal_log failed: %s", e)
        return None


def check_pending_forecasts(current_prices: dict[str, float]) -> int:
    """
    Проверить непроверенные прогнозы старше CHECK_HORIZON_HOURS.
    Для каждого: загрузить цены с момента прогноза, определить SL/TP hit.

    Returns: количество проверенных прогнозов.
    """
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, symbol, timestamp, direction, entry_price, sl, tp1, tp2, tp3
        FROM signal_log
        WHERE checked_at IS NULL
          AND datetime(timestamp, '+{} hours') < datetime('now')
    """.format(CHECK_HORIZON_HOURS))

    pending = c.fetchall()
    if not pending:
        conn.close()
        return 0

    checked = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in pending:
        row_id, symbol, ts, direction, entry, sl, tp1, tp2, tp3 = row

        actual = current_prices.get(symbol)
        if actual is None:
            continue

        # Определяем исход
        sl_hit = 0
        tp1_hit = 0
        tp2_hit = 0
        tp3_hit = 0
        outcome = "expired"
        rr_realised = None

        if direction == "long":
            if sl is not None and actual <= sl:
                sl_hit = 1
                outcome = "sl_hit"
            else:
                if tp1 is not None and actual >= tp1:
                    tp1_hit = 1
                if tp2 is not None and actual >= tp2:
                    tp2_hit = 1
                if tp3 is not None and actual >= tp3:
                    tp3_hit = 1
                if tp1_hit and not sl_hit:
                    outcome = "tp1_hit"
                    if tp2_hit:
                        outcome = "tp2_hit"
                    if tp3_hit:
                        outcome = "tp3_hit"
                elif not tp1_hit:
                    outcome = "no_hit"
        elif direction == "short":
            if sl is not None and actual >= sl:
                sl_hit = 1
                outcome = "sl_hit"
            else:
                if tp1 is not None and actual <= tp1:
                    tp1_hit = 1
                if tp2 is not None and actual <= tp2:
                    tp2_hit = 1
                if tp3 is not None and actual <= tp3:
                    tp3_hit = 1
                if tp1_hit and not sl_hit:
                    outcome = "tp1_hit"
                    if tp2_hit:
                        outcome = "tp2_hit"
                    if tp3_hit:
                        outcome = "tp3_hit"
                elif not tp1_hit:
                    outcome = "no_hit"
        else:
            outcome = "no_direction"

        # Расчёт реализованного RR
        if entry and sl and sl_hit:
            risk = abs(entry - sl)
            if risk > 0:
                reward = abs(actual - entry)
                rr_realised = round(-reward / risk, 2)  # отрицательный = убыток
        elif entry and tp1 and tp1_hit:
            risk = abs(sl - entry) if sl else abs(entry * 0.01)
            if risk > 0:
                rr_realised = round(abs(tp1 - entry) / risk, 2)

        # Max favorable / adverse excursion
        max_favorable = 0.0
        max_adverse = 0.0
        if entry:
            move = (actual - entry) / entry * 100
            if direction == "long":
                max_favorable = max(move, 0)
                max_adverse = max(-move, 0)
            else:
                max_favorable = max(-move, 0)
                max_adverse = max(move, 0)

        c.execute("""
            UPDATE signal_log SET
                checked_at = ?, actual_price = ?,
                sl_hit = ?, tp1_hit = ?, tp2_hit = ?, tp3_hit = ?,
                max_favorable = ?, max_adverse = ?,
                outcome = ?, rr_realised = ?
            WHERE id = ?
        """, (now, actual, sl_hit, tp1_hit, tp2_hit, tp3_hit,
              round(max_favorable, 3), round(max_adverse, 3),
              outcome, rr_realised, row_id))
        checked += 1

    conn.commit()
    conn.close()
    if checked:
        logger.info("backtest: checked %d forecasts", checked)
    return checked


def get_backtest_context(symbol: str = "BTCUSDT", last_n: int = 30) -> str:
    """
    Сформировать строку со статистикой точности для LLM-промпта.

    Формат:
      Backtest (last 30 signals, checked: 25):
      Accuracy: 68.0% (17/25)
      TP1 hit rate: 52.0%, SL hit rate: 20.0%
      Avg RR planned: 2.8, Avg RR realised: 1.5
      Last 5 outcomes: tp1_hit, no_hit, sl_hit, tp2_hit, no_hit
    """
    try:
        conn = _conn()
        c = conn.cursor()

        # Общая статистика по символу
        c.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'sl_hit' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN outcome = 'no_hit' THEN 1 ELSE 0 END) as no_hit,
                AVG(CASE WHEN rr_planned IS NOT NULL AND rr_planned > 0 THEN rr_planned END) as avg_rr_plan,
                AVG(CASE WHEN rr_realised IS NOT NULL THEN rr_realised END) as avg_rr_real
            FROM signal_log
            WHERE symbol = ? AND checked_at IS NOT NULL
        """, (symbol,))
        row = c.fetchone()

        if not row or row[0] == 0:
            conn.close()
            return "Backtest: статистика формируется (прогнозы накопляются)."

        total, wins, losses, no_hit, avg_rr_plan, avg_rr_real = row
        accuracy = (wins / total * 100) if total > 0 else 0
        sl_rate = (losses / total * 100) if total > 0 else 0
        tp_rate = (wins / total * 100) if total > 0 else 0

        # Последние N исходов
        c.execute("""
            SELECT outcome FROM signal_log
            WHERE symbol = ? AND checked_at IS NOT NULL
            ORDER BY timestamp DESC LIMIT ?
        """, (symbol, min(last_n, 5)))
        recent = [r[0] for r in c.fetchall()]
        recent_str = ", ".join(recent) if recent else "нет"

        # Точность по signal_status
        c.execute("""
            SELECT signal_status,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins
            FROM signal_log
            WHERE symbol = ? AND checked_at IS NOT NULL
            GROUP BY signal_status
            ORDER BY cnt DESC
            LIMIT 4
        """, (symbol,))
        by_signal = c.fetchall()
        by_signal_str = "; ".join(
            f"{s}: {w}/{n} ({w/n*100:.0f}%)" for s, n, w in by_signal if n > 0
        ) if by_signal else "нет данных"

        conn.close()

        rr_plan_str = f"{avg_rr_plan:.1f}" if avg_rr_plan else "N/A"
        rr_real_str = f"{avg_rr_real:.1f}" if avg_rr_real else "N/A"

        return (
            f"Backtest ({symbol}, checked: {total}):\n"
            f"Accuracy: {accuracy:.0f}% ({int(wins)}/{total}) | "
            f"TP hit: {tp_rate:.0f}% | SL hit: {sl_rate:.0f}% | "
            f"No hit: {(no_hit/total*100) if total else 0:.0f}%\n"
            f"Avg RR planned: {rr_plan_str} | Avg RR realised: {rr_real_str}\n"
            f"By signal: {by_signal_str}\n"
            f"Last 5: {recent_str}"
        )
    except Exception as e:
        logger.warning("get_backtest_context failed: %s", e)
        return "Backtest: статистика временно недоступна."


def get_backtest_stats_dict(symbol: str | None = None) -> dict[str, Any]:
    """Словарь со статистикой (для TG-команд, отчетов)."""
    try:
        conn = _conn()
        c = conn.cursor()

        where = "WHERE checked_at IS NOT NULL"
        params: list = []
        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)

        c.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'sl_hit' THEN 1 ELSE 0 END) as sl_hits,
                SUM(CASE WHEN outcome = 'no_hit' THEN 1 ELSE 0 END) as no_hit,
                AVG(CASE WHEN rr_realised IS NOT NULL THEN rr_realised END) as avg_rr
            FROM signal_log {where}
        """, params)
        row = c.fetchone()

        c.execute(f"SELECT COUNT(*) FROM signal_log WHERE checked_at IS NULL {('AND symbol = ?' if symbol else '')}", params)
        pending = c.fetchone()[0]

        conn.close()

        if not row or row[0] == 0:
            return {"total": 0, "wins": 0, "accuracy": 0, "pending": pending}

        total, wins, sl_hits, no_hits, avg_rr = row
        return {
            "total": total,
            "wins": int(wins),
            "sl_hits": int(sl_hits),
            "no_hit": int(no_hits),
            "accuracy": round(wins / total * 100, 1) if total else 0,
            "avg_rr": round(avg_rr, 2) if avg_rr else None,
            "pending": pending,
        }
    except Exception as e:
        logger.warning("get_backtest_stats_dict failed: %s", e)
        return {"total": 0, "error": str(e)}


# ── Helpers ─────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            m = __import__("re").search(r"-?\d+(?:\.\d+)?", value.replace(",", "."))
            return float(m.group(0)) if m else None
        return None
    except (TypeError, ValueError):
        return None


def _detect_direction(parsed: dict, signal_status: str) -> str:
    """Определить direction из parsed данных."""
    if signal_status in ("no_signal", "accumulation", "unknown", ""):
        return "flat"

    trend = str(parsed.get("trend_structure", "")).lower()
    ltf = str(parsed.get("ltf_structure", "")).lower()
    sub = str(parsed.get("current_substructure", "")).lower()
    wave = str(parsed.get("wave_phase", "")).lower()

    if "long" in signal_status or "up" in signal_status:
        return "long"
    if "short" in signal_status or "down" in signal_status:
        return "short"
    if signal_status == "reversal":
        if "down" in trend or "down" in wave:
            return "short"
        return "long"
    if signal_status == "false_breakout":
        if "up" in ltf or "up" in sub:
            return "short"
        if "down" in ltf or "down" in sub:
            return "long"
        return "short"
    if "up" in trend or "up" in ltf or "up" in wave or "bull" in trend:
        return "long"
    if "down" in trend or "down" in ltf or "down" in wave or "bear" in trend:
        return "short"
    return "long"