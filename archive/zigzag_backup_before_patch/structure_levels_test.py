from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.zigzag.benchmark_zigzag import run_benchmark
from core.zigzag.structural_zigzag import get_structural_extremums_zigzag


def main() -> None:
    symbol = "BTC/USDT"
    market_type = "future"
    timeframes = ["15m", "1h", "4h", "1d"]

    benchmark = run_benchmark(
        symbol=symbol,
        market_type=market_type,
        timeframes=timeframes,
        limit=200,
        mode="hybrid_atr",
        confirmation_mode="close",
        debug=False,
        output=None,
    )

    print("\n=== STRUCTURE LEVELS TEST ===")
    print(json.dumps(benchmark["stack"], ensure_ascii=False, indent=2))

    for tf, data in benchmark["timeframes"].items():
        result = get_structural_extremums_zigzag(
            symbol=symbol,
            timeframe=tf,
            current_price=data["current_price"],
            upper=data["upper"],
            lower=data["lower"],
            swing_direction=data["swing_direction"],
            swing_points=data["swing_points"],
            zones=data.get("zones"),
            atr_last=data.get("atr_last"),
        )

        print(f"\n--- {tf.upper()} ---")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()