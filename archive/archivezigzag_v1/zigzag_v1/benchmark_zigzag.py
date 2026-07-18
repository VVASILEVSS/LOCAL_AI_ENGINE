import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import ccxt
import pandas as pd

from core.zigzag.structural_zigzag import get_structural_extremums_zigzag, analyze_multitimeframe


def normalize_symbol(symbol: str, market_type: str) -> str:
    """
    Normalize symbol for CCXT depending on market type.

    spot:
      BTC/USDT -> BTC/USDT
      XAUT/USDT -> XAUT/USDT

    future:
      BTC/USDT -> BTCUSDT
      XAUT/USDT -> XAUTUSDT
    """
    symbol = symbol.strip().upper()

    if market_type == "future":
        return symbol.replace("/", "").replace(":", "")
    return symbol


def fetch_ohlcv(symbol: str, timeframe: str, market_type: str, limit: int = 300) -> pd.DataFrame:
    exchange = ccxt.binance({
        "options": {
            "defaultType": "future" if market_type == "future" else "spot"
        }
    })
    ccxt_symbol = normalize_symbol(symbol, market_type)
    bars = exchange.fetch_ohlcv(ccxt_symbol, timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def summarize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    params = result.get("params", {})
    return {
        "swing_direction": result.get("swing_direction"),
        "upper": result.get("upper"),
        "lower": result.get("lower"),
        "channel_state": result.get("channel_state"),
        "breakout_state": result.get("breakout_state"),
        "market_mode": result.get("market_mode"),
        "price_position": result.get("price_position"),
        "pattern_tags": result.get("pattern_tags", []),
        "pivot_count": params.get("pivot_count"),
        "current_price": params.get("current_price"),
        "atr_last": params.get("atr_last"),
        "summary": result.get("summary", ""),
        "swing_points": result.get("swing_points", [])[-6:],
        "zones": result.get("zones", {}),
    }


def run_benchmark(
    symbol: str,
    market_type: str = "future",
    timeframes: Optional[List[str]] = None,
    limit: int = 300,
    mode: str = "hybrid_atr",
    length: Optional[int] = None,
    percent: Optional[float] = None,
    confirmation_mode: str = "close",
    debug: bool = False,
    output: Optional[str] = None,
) -> Dict[str, Any]:
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]

    layers: Dict[str, Dict[str, Any]] = {}
    per_tf: Dict[str, Any] = {}

    print(f"\n=== ZIGZAG BENCHMARK: {symbol} ===")
    print(
        f"settings: market_type={market_type}, mode={mode}, length={length}, percent={percent}, "
        f"confirmation_mode={confirmation_mode}, limit={limit}, debug={debug}"
    )

    for tf in timeframes:
        print(f"\n--- {tf.upper()} ---")
        try:
            df = fetch_ohlcv(symbol, tf, market_type=market_type, limit=limit)
            result = get_structural_extremums_zigzag(
                df=df,
                timeframe=tf,
                mode=mode,
                length=length,
                percent=percent,
                confirmation_mode=confirmation_mode,
                symbol=symbol,
                debug=debug,
            )
            layers[tf] = result
            per_tf[tf] = summarize_result(result)
            print(json.dumps(per_tf[tf], ensure_ascii=False, indent=2))
        except Exception as e:
            per_tf[tf] = {"error": f"{type(e).__name__}: {e}"}
            print(f"ERROR: {type(e).__name__}: {e}")

    stack = analyze_multitimeframe(layers) if layers else {}
    benchmark = {
        "symbol": symbol,
        "market_type": market_type,
        "normalized_symbol": normalize_symbol(symbol, market_type),
        "settings": {
            "mode": mode,
            "length": length,
            "percent": percent,
            "confirmation_mode": confirmation_mode,
            "limit": limit,
            "debug": debug,
        },
        "timeframes": per_tf,
        "stack": stack,
    }

    print("\n=== MULTI-TF STACK SUMMARY ===")
    print(json.dumps(stack, ensure_ascii=False, indent=2))

    if output:
        out_path = Path(output)
        out_path.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved benchmark to: {out_path}")

    return benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="ZigZag benchmark runner")
    parser.add_argument("--symbol", required=True, help="Symbol, e.g. BTC/USDT or XAUT/USDT")
    parser.add_argument("--market-type", default="future", choices=["spot", "future"])
    parser.add_argument("--mode", default="hybrid_atr", choices=["lux_channel", "reversal", "hybrid_atr"])
    parser.add_argument("--length", type=int, default=None)
    parser.add_argument("--percent", type=float, default=None)
    parser.add_argument("--confirmation-mode", default="close", choices=["close", "wick"])
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--output", default=None, help="Optional path to save JSON result")
    args = parser.parse_args()

    run_benchmark(
        symbol=args.symbol,
        market_type=args.market_type,
        mode=args.mode,
        length=args.length,
        percent=args.percent,
        confirmation_mode=args.confirmation_mode,
        limit=args.limit,
        debug=args.debug,
        output=args.output,
    )


if __name__ == "__main__":
    main()