#!/usr/bin/env python3
"""
divergence_context_patch.py — Патч для core/divergence_context.py

Добавляет:
  1. load_backtest_stats() — загрузка results/backtest_stats.json
  2. get_reliability_summary() — текстовая сводка надежности по символам
  3. get_multi_context_with_stats() — расширенная версия get_multi_context со stats

ИНСТРУКЦИЯ:
  Этот патч нужно вручную применить к D:\LOCAL_AI_ENGINE\core\divergence_context.py

  Добавить в imports (верх файла):
    import json, os
    from pathlib import Path

  Добавить новые функции (в конец файла или после load_candidates):
    ... (см. код ниже)

  В get_multi_context() — добавить reliability_summary в выходной текст.
"""

import json
import os
from pathlib import Path
from typing import Optional


def load_backtest_stats(stats_path: str = None) -> dict:
    """
    Загрузить backtest_stats.json из results/.

    Returns dict with structure:
      {
        "overall": {"n": 447, "winrate": 87.5, ...},
        "by_symbol": {"BTCUSDT": {...}, ...},
        "by_tf": {"1h": {...}, ...},
        "by_type": {"bull": {...}, "bear": {...}},
        "summary_for_prompt": "...",
        ...
      }
    """
    if stats_path is None:
        # Default path relative to project root
        stats_path = os.path.join("results", "backtest_stats.json")

    if not os.path.exists(stats_path):
        return {}

    try:
        with open(stats_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARNING] Failed to load backtest stats: {e}")
        return {}


def get_reliability_summary(stats: dict, symbol: str = None) -> str:
    """
    Сгенерировать компактную сводку надежности для LLM prompt.

    Если symbol указан — только по этому символу.
    Если нет — по всем.
    """
    if not stats:
        return ""

    lines = []

    if symbol:
        by_sym = stats.get("by_symbol", {})
        sym_data = by_sym.get(symbol, {})
        if sym_data and sym_data.get("n", 0) > 0:
            lines.append(
                f"Backtest {symbol}: WR={sym_data['winrate']}%, "
                f"PF={sym_data['profit_factor']}x, n={sym_data['n']}, "
                f"Avg P&L={sym_data['avg_return_pct']}%"
            )
            # Per-TF breakdown for this symbol
            by_tf = sym_data.get("by_tf", {})
            if by_tf:
                tf_parts = []
                for tf, tf_data in sorted(by_tf.items()):
                    if tf_data.get("n", 0) > 0:
                        tf_parts.append(f"{tf}:WR={tf_data['winrate']}%")
                if tf_parts:
                    lines.append(f"  Per-TF: {', '.join(tf_parts)}")
    else:
        # All symbols summary
        overall = stats.get("overall", {})
        if overall.get("n", 0) > 0:
            lines.append(
                f"Backtest A/D signals: {overall['n']} trades, "
                f"WR={overall['winrate']}%, PF={overall['profit_factor']}x"
            )

        by_sym = stats.get("by_symbol", {})
        for sym, sym_data in sorted(by_sym.items()):
            if sym_data.get("n", 0) > 0:
                lines.append(
                    f"  {sym}: WR={sym_data['winrate']}%, "
                    f"PF={sym_data['profit_factor']}x, n={sym_data['n']}"
                )

    return "\n".join(lines)


def get_symbol_stats_for_context(stats: dict, symbol: str) -> dict:
    """
    Получить stats dict для конкретного символа.

    Returns:
      {
        "total_candidates": 95,
        "winrate": 87.5,
        "profit_factor": 19.4,
        "avg_pnl_pct": 1.46,
        "best_tf": "4h",
        "best_tf_winrate": 88.0,
        "bull_wr": 84.6,
        "bear_wr": 90.8,
        "quality_breakdown": {
          "strong": {"n": 50, "winrate": 89.0},
          "medium": {"n": 40, "winrate": 85.0},
          "weak": {"n": 5, "winrate": 100.0}
        }
      }
    """
    if not stats:
        return {}

    by_sym = stats.get("by_symbol", {})
    sym_data = by_sym.get(symbol, {})

    if not sym_data or sym_data.get("n", 0) == 0:
        return {}

    # Find best TF
    by_tf = sym_data.get("by_tf", {})
    best_tf = "N/A"
    best_tf_wr = 0.0
    for tf, tf_data in by_tf.items():
        if tf_data.get("n", 0) > 0 and tf_data.get("winrate", 0) > best_tf_wr:
            best_tf_wr = tf_data["winrate"]
            best_tf = tf

    # Type breakdown
    by_type = sym_data.get("by_type", {})

    # Quality breakdown (from global by_quality if available, approximate)
    quality_breakdown = {}
    global_quality = stats.get("by_quality", {})
    for q, q_data in global_quality.items():
        quality_breakdown[q] = {
            "n": q_data.get("n", 0),
            "winrate": q_data.get("winrate", 0),
        }

    return {
        "total_candidates": sym_data.get("n", 0),
        "winrate": sym_data.get("winrate", 0),
        "profit_factor": sym_data.get("profit_factor", 0),
        "avg_pnl_pct": sym_data.get("avg_return_pct", 0),
        "best_tf": best_tf,
        "best_tf_winrate": best_tf_wr,
        "bull_wr": by_type.get("bull", {}).get("winrate", 0),
        "bear_wr": by_type.get("bear", {}).get("winrate", 0),
        "quality_breakdown": quality_breakdown,
    }


# ============================================================
# ПАТЧ: Как интегрировать в divergence_context.py
# ============================================================
#
# 1. Скопируй функции load_backtest_stats, get_reliability_summary,
#    get_symbol_stats_for_context в core/divergence_context.py
#
# 2. В начале файла добавь:
#    import json, os
#    from pathlib import Path
#
# 3. В функции get_multi_context() (или там где формируется контекст для LLM):
#    Добавь в конец возвращаемой строки:
#
#    # === Добавить reliability stats ===
#    try:
#        bt_stats = load_backtest_stats()
#        if bt_stats:
#            # Summary по конкретному символу
#            reliability = get_reliability_summary(bt_stats, symbol=symbol)
#            if reliability:
#                context_text += f"\n\n=== BACKTEST RELIABILITY ===\n{reliability}"
#
#            # Или по всем символам
#            if not symbol:
#                all_reliability = get_reliability_summary(bt_stats)
#                if all_reliability:
#                    context_text += f"\n\n=== BACKTEST RELIABILITY ===\n{all_reliability}"
#    except Exception:
#        pass
#
# 4. В handlers.py /analyze_ad — добавить stats в вывод:
#    bt_stats = load_backtest_stats()
#    sym_stats = get_symbol_stats_for_context(bt_stats, symbol)
#    if sym_stats:
#        txt += f"\n📈 Backtest: WR={sym_stats['winrate']}%, PF={sym_stats['profit_factor']}x"
#
# ============================================================

if __name__ == "__main__":
    # Quick test
    stats = load_backtest_stats()
    if not stats:
        print("No backtest_stats.json found. Run backtest_stats_generator.py first.")
    else:
        print("=== All Symbols ===")
        print(get_reliability_summary(stats))

        print("\n=== BTCUSDT ===")
        print(get_reliability_summary(stats, symbol="BTCUSDT"))

        print("\n=== BTCUSDT Stats ===")
        import json as j
        print(j.dumps(get_symbol_stats_for_context(stats, "BTCUSDT"), indent=2))
