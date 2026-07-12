from __future__ import annotations

import ccxt
import pandas as pd

from core.liquidity_magnet import build_liquidity_magnet


def fetch_ohlcv(symbol: str = "ETHUSDT", timeframe: str = "1h", limit: int = 300) -> pd.DataFrame:
    exchange = ccxt.binance({"options": {"defaultType": "future"}})
    bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(
        bars,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def main() -> None:
    symbol = "ETHUSDT"
    timeframe = "1h"

    df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=300)
    result = build_liquidity_magnet(
        df=df,
        timeframe=timeframe,
        symbol=symbol,
        pivot_len=4,
        eq_tolerance_pct=0.8,
        max_pools=25,
        min_age=3,
        max_age=500,
        log_distance_cap=0.30,
    )

    print("\n=== LIQUIDITY MAGNET TEST ===")
    print(f"Symbol: {result.get('symbol')}")
    print(f"Timeframe: {result.get('timeframe')}")
    print(f"Last price: {result.get('last_price')}")
    print(f"Magnet pull: {result.get('magnet_pull')}")
    print(f"Top target: {result.get('top_target')}")
    print(f"Top probability: {result.get('top_probability')}")
    print(f"Distance %: {result.get('distance_pct')}")
    print(f"Proximity %: {result.get('proximity_pct')}")
    print(f"Active pools: {result.get('active_pools')}")
    print(f"Equal total %: {result.get('equal_total_pct')}")
    print(f"Historical touch %: {result.get('historical_touch_pct')}")
    print(f"Shared highs: {result.get('shared_highs')}")
    print(f"Shared lows: {result.get('shared_lows')}")
    print(f"Hierarchy: {result.get('hierarchy')}")
    print(f"Summary: {result.get('summary')}")

    print("\n--- TOP 3 ---")
    for i, item in enumerate(result.get("top3", []), start=1):
        print(
            f"{i}. price={item.get('price')} | prob={item.get('probability')} | "
            f"is_high={item.get('is_high')} | is_equal={item.get('is_equal')} | "
            f"shared_extremum={item.get('shared_extremum')}"
        )

    print("\n--- POOLS ---")
    for pool in result.get("pools", [])[:10]:
        print(
            f"tf={pool.get('tf')} | price={pool.get('price')} | "
            f"is_high={pool.get('is_high')} | equal={pool.get('is_equal')} | "
            f"shared={pool.get('shared_extremum')} | prob={pool.get('probability')}"
        )


if __name__ == "__main__":
    main()