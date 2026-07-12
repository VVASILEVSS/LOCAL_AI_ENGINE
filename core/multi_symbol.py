"""
P3-3: Multi-symbol correlation context.

Provides market-wide context for LLM:
  - BTC Dominance (% of total crypto market cap)
  - ETH/BTC ratio (risk-on/risk-off indicator)
  - ETH and SOL 24h change (altcoin market health)
  - DXY (Dollar Index) — when available via free API
  - Fear & Greed Index (alternative.me, free, no auth)

Integration:
  scheduler.py → multi_ctx = get_multi_symbol_context(current_symbol)
  prev_ctx["multi_symbol"] = multi_ctx
  ollama_client.py → PRO_TA_USER_PROMPT: {multi_symbol}
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Cache for the analysis cycle (all symbols share one call)
_cache: dict[str, Any] = {}
_cache_key: str = ""


def _fetch_coingecko_simple(ids: list[str]) -> dict[str, dict]:
    """
    CoinGecko /simple/price — free, no auth, returns price+24h_change+market_cap.
    Rate limit: ~10-30 req/min for free tier.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
            },
            timeout=10,
        )
        if r.status_code == 429:
            logger.debug("CoinGecko rate limited")
            return {}
        return r.json()  # {"bitcoin": {"usd": 64000, "usd_24h_change": 1.2, "usd_market_cap": ...}, ...}
    except Exception as e:
        logger.debug("CoinGecko failed: %s", e)
        return {}


def _fetch_dominance() -> float | None:
    """
    BTC dominance from CoinGecko /global.
    Free, no auth.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10,
        )
        if r.status_code == 429:
            return None
        data = r.json()
        return float(data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0))
    except Exception as e:
        logger.debug("BTC dominance failed: %s", e)
        return None


def _fetch_fear_greed() -> dict[str, Any] | None:
    """
    Fear & Greed Index from alternative.me.
    Free, no auth, updated daily.
    """
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        data = r.json()
        if data.get("data") and len(data["data"]) > 0:
            d = data["data"][0]
            return {
                "value": int(d.get("value", 50)),
                "classification": d.get("value_classification", "Neutral"),
            }
    except Exception as e:
        logger.debug("Fear & Greed failed: %s", e)
    return None


def _fetch_dxy() -> float | None:
    """
    DXY (US Dollar Index) — attempt via exchangerate-api or fallback.
    DXY is not freely available via standard APIs.
    We try a free proxy, gracefully degrade to None.
    """
    # DXY requires paid feed in most cases. Skip for now — return None.
    # Can be added later with a free source if found.
    return None


def get_multi_symbol_context(current_symbol: str = "BTCUSDT", cache_buster: str = "") -> str:
    """
    Build multi-symbol context string for LLM prompt.

    Returns something like:
      Мульти-символьный контекст:
      BTC Dominance: 52.3% | Fear & Greed: 45 (Fear)
      ETH/BTC: 0.0512 (-1.2% 24ч) | ETH 24ч: +2.1% | SOL 24ч: +3.5%

    Uses per-cycle cache (cache_buster) to avoid redundant API calls
    when processing multiple symbols in one cycle.
    """
    global _cache, _cache_key

    if cache_buster and cache_buster == _cache_key and _cache:
        return _format_context(current_symbol, _cache)

    context: dict[str, Any] = {}

    # 1. CoinGecko: BTC, ETH, SOL prices + 24h change
    cg = _fetch_coingecko_simple(["bitcoin", "ethereum", "solana"])
    if cg:
        btc = cg.get("bitcoin", {})
        eth = cg.get("ethereum", {})
        sol = cg.get("solana", {})

        context["btc_price"] = btc.get("usd")
        context["btc_24h"] = btc.get("usd_24h_change")
        context["eth_price"] = eth.get("usd")
        context["eth_24h"] = eth.get("usd_24h_change")
        context["sol_price"] = sol.get("usd")
        context["sol_24h"] = sol.get("usd_24h_change")

        # ETH/BTC ratio
        btc_p = btc.get("usd")
        eth_p = eth.get("usd")
        if btc_p and eth_p and btc_p > 0:
            context["eth_btc_ratio"] = round(eth_p / btc_p, 6)

    # 2. BTC Dominance
    dom = _fetch_dominance()
    if dom is not None:
        context["btc_dominance"] = round(dom, 1)

    # 3. Fear & Greed
    fng = _fetch_fear_greed()
    if fng:
        context["fear_greed"] = fng

    # 4. DXY (skip — no free source)
    dxy = _fetch_dxy()
    if dxy is not None:
        context["dxy"] = dxy

    # Update cache
    if cache_buster:
        _cache = context
        _cache_key = cache_buster

    return _format_context(current_symbol, context)


def _format_context(current_symbol: str, ctx: dict[str, Any]) -> str:
    """Format context dict into a concise string for the LLM prompt."""
    if not ctx:
        return "Мульти-символьный контекст: данные недоступны."

    parts: list[str] = []

    # BTC Dominance + Fear & Greed
    meta_parts = []
    if ctx.get("btc_dominance") is not None:
        meta_parts.append(f"BTC Dominance: {ctx['btc_dominance']}%")
    fng = ctx.get("fear_greed")
    if fng:
        meta_parts.append(f"Fear & Greed: {fng['value']} ({fng['classification']})")
    if meta_parts:
        parts.append(" | ".join(meta_parts))

    # Altcoin changes
    eth_btc = ctx.get("eth_btc_ratio")
    eth_24h = ctx.get("eth_24h")
    sol_24h = ctx.get("sol_24h")

    alt_parts = []
    if eth_btc is not None:
        alt_parts.append(f"ETH/BTC: {eth_btc}")
    if eth_24h is not None:
        alt_parts.append(f"ETH 24ч: {eth_24h:+.1f}%")
    if sol_24h is not None:
        alt_parts.append(f"SOL 24ч: {sol_24h:+.1f}%")
    if alt_parts:
        parts.append(" | ".join(alt_parts))

    # Interpretation hints for LLM
    hints: list[str] = []

    # ETH/BTC falling = BTC outperforming ETH = risk-off
    if eth_btc is not None:
        # We can't calculate change without previous value, so skip delta hint
        pass

    # Fear & Greed interpretation
    if fng:
        v = fng["value"]
        if v <= 25:
            hints.append("Extreme Fear — потенциал отскока, но риск паники")
        elif v <= 40:
            hints.append("Fear — осторожность, возможны дальнейшие продажи")
        elif v >= 75:
            hints.append("Extreme Greed — риск коррекции,慎重")
        elif v >= 60:
            hints.append("Greed — рынок оптимистичен, но возможен разворот")

    # Dominance interpretation
    dom = ctx.get("btc_dominance")
    if dom is not None:
        if dom > 55:
            hints.append("BTC dominance высокий — капитал в BTC, альткоины слабые")
        elif dom < 48:
            hints.append("BTC dominance низкий — capital rotation в альткоины")

    # BTC itself 24h change for non-BTC symbols
    if not current_symbol.startswith("BTC"):
        btc_24h = ctx.get("btc_24h")
        if btc_24h is not None:
            parts.append(f"BTC 24ч: {btc_24h:+.1f}%")

    if hints:
        parts.append("Дополнительно: " + "; ".join(hints))

    if not parts:
        return "Мульти-символьный контекст: данные недоступны."

    return "Мульти-символьный контекст:\n" + "\n".join(parts)


def invalidate_cache() -> None:
    """Clear the per-cycle cache (called at start of each analysis cycle)."""
    global _cache, _cache_key
    _cache = {}
    _cache_key = ""