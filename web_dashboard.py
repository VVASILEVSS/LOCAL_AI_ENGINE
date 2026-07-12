"""
LOCAL_AI_ENGINE — Web Dashboard
Запуск: python web_dashboard.py → http://localhost:5000

Flask + фоновый поток с Telegram ботом + APScheduler.
Дашборд: статус бота, точность, A/B тест, последние сигналы.
"""
from __future__ import annotations

import os
import sys
import threading
import asyncio
import sqlite3
import logging

# Фикс DNS для aiohttp на Windows
import aiohttp
from aiohttp.resolver import ThreadedResolver
_orig_init = aiohttp.TCPConnector.__init__
def _patched_init(self, *a, **kw):
    if 'resolver' not in kw or kw['resolver'] is None:
        kw['resolver'] = ThreadedResolver()
    return _orig_init(self, *a, **kw)
aiohttp.TCPConnector.__init__ = _patched_init

from flask import Flask, jsonify, render_template_string
from core.config import MODEL_NAME, PROMPT_VARIANT, LLM_MODE
from core.config import TOKEN, MY_CHAT_ID
from core.db import DB_PATH

app = Flask(__name__)
logger = logging.getLogger(__name__)

WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
BOT_RUNNING = False
LAST_ANALYSIS_TIME = None

DB_PATH_D = os.path.join(os.path.dirname(__file__), 'forecasts.db')


def _query_db(query, args=()):
    """Чтение из SQLite (потокобезопасно для чтения)."""
    try:
        conn = sqlite3.connect(DB_PATH_D)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(query, args)
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"DB query failed: {e}")
        return []


def get_stats():
    """Сбор статистики для дашборда."""
    stats = {
        "bot_status": "🟢 Running" if BOT_RUNNING else "🔴 Stopped",
        "last_analysis": LAST_ANALYSIS_TIME or "—",
        "model": MODEL_NAME,
        "llm_mode": LLM_MODE,
        "prompt_variant": PROMPT_VARIANT,
        "interval": "30 мин",
    }

    # Backtest статистика
    rows = _query_db("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'sl_hit' THEN 1 ELSE 0 END) as sl_hits,
            SUM(CASE WHEN outcome = 'no_hit' THEN 1 ELSE 0 END) as no_hits,
            AVG(CASE WHEN rr_planned IS NOT NULL AND rr_planned > 0 THEN rr_planned END) as avg_rr_plan,
            AVG(CASE WHEN rr_realised IS NOT NULL THEN rr_realised END) as avg_rr_real
        FROM signal_log WHERE checked_at IS NOT NULL
    """)
    if rows and rows[0]["total"] and rows[0]["total"] > 0:
        r = rows[0]
        total = r["total"]
        stats["checked"] = total
        stats["wins"] = int(r["wins"] or 0)
        stats["accuracy"] = round(r["wins"] / total * 100, 1) if total else 0
        stats["tp_rate"] = round(r["wins"] / total * 100, 1) if total else 0
        stats["sl_rate"] = round((r["sl_hits"] or 0) / total * 100, 1) if total else 0
        stats["no_hit_rate"] = round((r["no_hits"] or 0) / total * 100, 1) if total else 0
        stats["avg_rr_plan"] = round(r["avg_rr_plan"], 2) if r["avg_rr_plan"] else "N/A"
        stats["avg_rr_real"] = round(r["avg_rr_real"], 2) if r["avg_rr_real"] else "N/A"
    else:
        stats.update({"checked": 0, "wins": 0, "accuracy": 0, "tp_rate": 0,
                       "sl_rate": 0, "no_hit_rate": 0, "avg_rr_plan": "N/A", "avg_rr_real": "N/A"})

    # Pending
    pending_rows = _query_db("SELECT COUNT(*) as cnt FROM signal_log WHERE checked_at IS NULL")
    stats["pending"] = pending_rows[0]["cnt"] if pending_rows else 0

    # A/B variants
    ab_rows = _query_db("""
        SELECT prompt_variant,
               COUNT(*) as cnt,
               SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins
        FROM signal_log WHERE checked_at IS NOT NULL AND prompt_variant IS NOT NULL
        GROUP BY prompt_variant ORDER BY prompt_variant
    """)
    stats["ab_variants"] = []
    for r in ab_rows:
        v = r["prompt_variant"] or "A"
        n = r["cnt"]
        w = r["wins"] or 0
        stats["ab_variants"].append({
            "variant": v, "total": n, "wins": w,
            "accuracy": f"{w/n*100:.0f}%" if n else "—"
        })

    # Last 10 signals
    last_rows = _query_db("""
        SELECT timestamp, symbol, signal_status, direction, entry_price,
               sl, tp1, outcome, rr_realised
        FROM signal_log ORDER BY timestamp DESC LIMIT 10
    """)
    stats["last_signals"] = last_rows

    # By signal type
    by_type_rows = _query_db("""
        SELECT signal_status,
               COUNT(*) as cnt,
               SUM(CASE WHEN outcome IN ('tp1_hit','tp2_hit','tp3_hit') THEN 1 ELSE 0 END) as wins
        FROM signal_log WHERE checked_at IS NOT NULL
        GROUP BY signal_status ORDER BY cnt DESC LIMIT 5
    """)
    stats["by_type"] = by_type_rows

    return stats


# ── HTML ─────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LOCAL_AI_ENGINE Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }
  h1 { color: #e94560; margin-bottom: 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 20px; }
  .card { background: #16213e; border-radius: 10px; padding: 15px; border: 1px solid #0f3460; }
  .card h2 { color: #e94560; font-size: 1.1em; margin-bottom: 10px; }
  .stat { display: flex; justify-content: space-between; padding: 4px 0; font-family: 'Courier New', monospace; }
  .stat .val { color: #53d769; font-weight: bold; }
  .stat .val.red { color: #e94560; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th { text-align: left; color: #e94560; padding: 6px; font-size: 0.85em; }
  td { padding: 6px; font-family: 'Courier New', monospace; font-size: 0.85em; border-top: 1px solid #0f3460; }
  .controls { margin: 15px 0; }
  .btn { padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 1em; margin-right: 10px; }
  .btn-start { background: #53d769; color: #1a1a2e; }
  .btn-stop { background: #e94560; color: #fff; }
  #refresh { color: #53d769; font-size: 0.8em; }
</style>
</head>
<body>
<h1>📊 LOCAL_AI_ENGINE Dashboard</h1>
<div class="controls">
  <button class="btn btn-start" onclick="ctrl('start')">▶ Start</button>
  <button class="btn btn-stop" onclick="ctrl('stop')">⏹ Stop</button>
  <span id="refresh">Auto-refresh: 60s</span>
</div>
<div class="grid" id="dashboard"></div>
<script>
async function fetchStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  let html = '';

  // Status card
  html += `<div class="card"><h2>⚙️ Статус</h2>`;
  html += `<div class="stat"><span>Бот:</span><span class="val">${d.bot_status}</span></div>`;
  html += `<div class="stat"><span>Последний анализ:</span><span>${d.last_analysis}</span></div>`;
  html += `<div class="stat"><span>Модель:</span><span>${d.model}</span></div>`;
  html += `<div class="stat"><span>LLM Mode:</span><span>${d.llm_mode}</span></div>`;
  html += `<div class="stat"><span>Prompt Variant:</span><span>${d.prompt_variant}</span></div>`;
  html += `<div class="stat"><span>Интервал:</span><span>${d.interval}</span></div>`;
  html += `</div>`;

  // Accuracy card
  html += `<div class="card"><h2>🎯 Точность</h2>`;
  html += `<div class="stat"><span>Проверено:</span><span class="val">${d.checked}</span></div>`;
  html += `<div class="stat"><span>Accuracy:</span><span class="val">${d.accuracy}%</span></div>`;
  html += `<div class="stat"><span>TP hit:</span><span class="val">${d.tp_rate}%</span></div>`;
  html += `<div class="stat"><span>SL hit:</span><span class="val red">${d.sl_rate}%</span></div>`;
  html += `<div class="stat"><span>No hit:</span><span>${d.no_hit_rate}%</span></div>`;
  html += `<div class="stat"><span>Avg RR plan:</span><span>${d.avg_rr_plan}</span></div>`;
  html += `<div class="stat"><span>Avg RR real:</span><span>${d.avg_rr_real}</span></div>`;
  html += `<div class="stat"><span>Pending:</span><span>${d.pending}</span></div>`;
  html += `</div>`;

  // A/B test card
  if (d.ab_variants && d.ab_variants.length > 0) {
    html += `<div class="card"><h2>🧪 A/B Тест</h2>`;
    d.ab_variants.forEach(v => {
      html += `<div class="stat"><span>Variant ${v.variant}:</span><span class="val">${v.wins}/${v.total} (${v.accuracy})</span></div>`;
    });
    html += `</div>`;
  }

  // By signal type card
  if (d.by_type && d.by_type.length > 0) {
    html += `<div class="card"><h2>📋 По типам сигналов</h2>`;
    html += `<table><tr><th>Signal</th><th>Кол-во</th><th>Wins</th></tr>`;
    d.by_type.forEach(s => {
      html += `<tr><td>${s.signal_status}</td><td>${s.cnt}</td><td>${s.wins||0}</td></tr>`;
    });
    html += `</table></div>`;
  }

  // Last signals table
  if (d.last_signals && d.last_signals.length > 0) {
    html += `<div class="card" style="grid-column: 1 / -1"><h2>📈 Последние сигналы</h2>`;
    html += `<table><tr><th>Время</th><th>Символ</th><th>Signal</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP1</th><th>Outcome</th><th>RR</th></tr>`;
    d.last_signals.forEach(s => {
      html += `<tr><td>${(s.timestamp||'').slice(11,19)}</td><td>${s.symbol}</td><td>${s.signal_status}</td><td>${s.direction}</td><td>${s.entry_price||'—'}</td><td>${s.sl||'—'}</td><td>${s.tp1||'—'}</td><td>${s.outcome||'pending'}</td><td>${s.rr_realised||'—'}</td></tr>`;
    });
    html += `</table></div>`;
  }

  document.getElementById('dashboard').innerHTML = html;
}

async function ctrl(action) {
  await fetch('/api/' + action, { method: 'POST' });
  setTimeout(fetchStats, 500);
}

fetchStats();
setInterval(fetchStats, 60000);
</script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(HTML)


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/start", methods=["POST"])
def api_start():
    global BOT_RUNNING
    if not BOT_RUNNING:
        start_bot_thread()
    return jsonify({"status": "started", "running": BOT_RUNNING})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global BOT_RUNNING
    BOT_RUNNING = False
    return jsonify({"status": "stopped", "running": False})


# ── Bot thread ────────────────────────────────────────────────────────────

_bot_thread = None


def start_bot_thread():
    """Запуск Telegram бота в фоновом потоке."""
    global _bot_thread, BOT_RUNNING

    def _run():
        global BOT_RUNNING, LAST_ANALYSIS_TIME
        BOT_RUNNING = True
        try:
            from main import main
            asyncio.run(main())
        except Exception as e:
            logger.error(f"Bot thread error: {e}")
        finally:
            BOT_RUNNING = False

    _bot_thread = threading.Thread(target=_run, daemon=True)
    _bot_thread.start()


if __name__ == "__main__":
    # Бот стартует сразу при запуске веб-сервера
    start_bot_thread()
    print(f"📊 Dashboard: http://localhost:{WEB_PORT}")
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)
