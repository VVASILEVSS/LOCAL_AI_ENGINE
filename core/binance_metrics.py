import ccxt
import logging
import requests

logger = logging.getLogger(__name__)


def _fetch_oi_okx(symbol: str = "BTC/USDT") -> dict | None:
    """
    OI из OKX — возвращает контракты, BTC и USD в одном ответе.
    Endpoint: /api/v5/public/open-interest (бесплатно, без авторизации)
    """
    try:
        inst_id = symbol.replace("/", "-") + "-SWAP"  # BTC/USDT → BTC-USDT-SWAP
        r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest",
            params={"instType": "SWAP", "instId": inst_id},
            timeout=10,
        )
        data = r.json()
        if data.get("code") == "0" and data.get("data"):
            d = data["data"][0]
            return {
                "oi_contracts": float(d.get("oi", 0)),
                "oi_btc": float(d.get("oiCcy", 0)),
                "oi_usd": float(d.get("oiUsd", 0)),
                "source": "OKX",
            }
    except Exception as e:
        logger.debug("OKX OI failed: %s", e)
    return None


def _fetch_oi_binance(symbol: str = "BTC/USDT") -> dict | None:
    """
    OI из Binance через raw API (futures/data/openInterestHist).
    Возвращает sumOpenInterest (контракты) и sumOpenInterestValue (USD).
    Бесплатно, без авторизации.
    """
    try:
        binance_symbol = symbol.replace("/", "")  # BTC/USDT → BTCUSDT
        r = requests.get(
            "https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": binance_symbol, "period": "1h", "limit": 1},
            timeout=10,
        )
        data = r.json()
        if isinstance(data, list) and data:
            d = data[-1]
            oi_btc = float(d.get("sumOpenInterest", 0))
            oi_usd = float(d.get("sumOpenInterestValue", 0))
            return {
                "oi_contracts": oi_btc,
                "oi_btc": oi_btc,
                "oi_usd": oi_usd,
                "source": "Binance",
            }
    except Exception as e:
        logger.debug("Binance OI hist failed: %s", e)

    # Fallback: ccxt unified (только количество контрактов)
    try:
        fut = ccxt.binance({"options": {"defaultType": "swap"}})
        oi_raw = fut.fetch_open_interest(symbol + ":USDT")
        if isinstance(oi_raw, dict):
            amount = oi_raw.get("openInterestAmount")
            if amount is not None:
                return {
                    "oi_contracts": float(amount),
                    "oi_btc": float(amount),
                    "oi_usd": None,
                    "source": "Binance-ccxt",
                }
    except Exception as e:
        logger.debug("Binance ccxt OI failed: %s", e)

    return None


def _fetch_oi_bybit(symbol: str = "BTC/USDT") -> dict | None:
    """
    OI из Bybit (бесплатно, без авторизации).
    Возвращает контракты в BTC для linear perpetual.
    """
    try:
        bybit_symbol = symbol.replace("/", "")  # BTC/USDT → BTCUSDT
        r = requests.get(
            "https://api.bybit.com/v5/market/open-interest",
            params={"category": "linear", "symbol": bybit_symbol, "intervalTime": "1h"},
            timeout=10,
        )
        data = r.json()
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            d = data["result"]["list"][0]
            oi_btc = float(d.get("openInterest", 0))
            return {
                "oi_contracts": oi_btc,
                "oi_btc": oi_btc,
                "oi_usd": None,
                "source": "Bybit",
            }
    except Exception as e:
        logger.debug("Bybit OI failed: %s", e)
    return None


def fetch_open_interest(symbol: str = "BTC/USDT") -> dict | None:
    """
    Агрегация OI из нескольких бесплатных источников.
    Приоритет: OKX (USD напрямую) → Binance (USD через hist) → Bybit (только BTC).
    """
    for fetcher in (_fetch_oi_okx, _fetch_oi_binance, _fetch_oi_bybit):
        result = fetcher(symbol)
        if result and result.get("oi_btc"):
            return result
    return None


def fetch_binance_metrics(symbol: str = "BTC/USDT") -> str:
    """Загружает рыночные метрики и возвращает строку для промпта ИИ"""
    try:
        fut = ccxt.binance({"options": {"defaultType": "future"}})
        spot = ccxt.binance({"options": {"defaultType": "spot"}})

        ticker = fut.fetch_ticker(symbol)
        spot_ticker = spot.fetch_ticker(symbol)

        price = ticker.get("last") or spot_ticker.get("last") or "N/A"
        change = ticker.get("percentage") or spot_ticker.get("percentage") or 0
        vol_24h = ticker.get("quoteVolume") or spot_ticker.get("quoteVolume") or 0

        # Funding Rate
        fund = fut.fetch_funding_rate(symbol)
        funding = fund.get("fundingRate") if fund else None
        funding_str = f"{funding * 100:+.4f}%" if funding is not None else "N/A"

        # Open Interest — multi-source fallback (OKX → Binance → Bybit)
        oi_data = fetch_open_interest(symbol)
        if oi_data and oi_data.get("oi_usd"):
            oi_str = f"${oi_data['oi_usd'] / 1e9:.2f}B ({oi_data['oi_btc']:,.0f} BTC, {oi_data['source']})"
        elif oi_data and oi_data.get("oi_btc"):
            oi_str = f"{oi_data['oi_btc']:,.0f} BTC ({oi_data['source']})"
        else:
            oi_str = "N/A"

        # Order Book Imbalance
        ob = spot.fetch_order_book(symbol, limit=20)
        bids = sum(float(b[1]) for b in ob["bids"] if b[1] is not None)
        asks = sum(float(a[1]) for a in ob["asks"] if a[1] is not None)
        total_ob = bids + asks
        imbalance = (bids / total_ob * 100) if total_ob > 0 else 50.0

        return (
            f"📊 РЫНОЧНЫЙ КОНТЕКСТ ({symbol}):\n"
            f"Цена: {price} USDT | 24ч: {change:+.2f}% | Объём: {vol_24h:,.0f} USDT\n"
            f"Funding: {funding_str} | Open Interest: {oi_str}\n"
            f"Стакан (Imbalance): {imbalance:.1f}% Buy / {100 - imbalance:.1f}% Sell"
        )
    except Exception as e:
        logger.warning("Ошибка загрузки метрик %s: %s", symbol, e)
        return f"📊 РЫНОЧНЫЙ КОНТЕКСТ ({symbol}): Данные временно недоступны."