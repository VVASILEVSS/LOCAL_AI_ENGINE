import ccxt
import logging

logger = logging.getLogger(__name__)

def fetch_binance_metrics(symbol: str = "BTC/USDT") -> str:
    """Загружает рыночные метрики Binance и возвращает строку для промпта ИИ"""
    try:
        fut = ccxt.binance({'options': {'defaultType': 'future'}})
        spot = ccxt.binance({'options': {'defaultType': 'spot'}})

        ticker = fut.fetch_ticker(symbol)
        spot_ticker = spot.fetch_ticker(symbol)

        price = ticker.get('last') or spot_ticker.get('last') or 'N/A'
        change = ticker.get('percentage') or spot_ticker.get('percentage') or 0
        vol_24h = ticker.get('quoteVolume') or spot_ticker.get('quoteVolume') or 0

        # Funding Rate
        fund = fut.fetch_funding_rate(symbol)
        funding = fund.get('fundingRate') if fund else None
        funding_str = f"{funding * 100:+.4f}%" if funding is not None else "N/A"

        # Open Interest (безопасный парсинг без asyncio)
        oi_raw = fut.fetch_open_interest(symbol)
        oi: float | int | None = None
        if isinstance(oi_raw, dict):
            oi = oi_raw.get('openInterest') or oi_raw.get('amount')
        elif isinstance(oi_raw, (int, float)):
            oi = oi_raw

        oi_str = f"{oi:,.0f}" if oi is not None else "N/A"

        # Order Book Imbalance
        ob = spot.fetch_order_book(symbol, limit=20)
        bids = sum(float(b[1]) for b in ob['bids'] if b[1] is not None)
        asks = sum(float(a[1]) for a in ob['asks'] if a[1] is not None)
        total_ob = bids + asks
        imbalance = (bids / total_ob * 100) if total_ob > 0 else 50.0

        return (
            f"📊 РЫНОЧНЫЙ КОНТЕКСТ ({symbol}):\n"
            f"Цена: {price} USDT | 24ч: {change:+.2f}% | Объём: {vol_24h:,.0f} USDT\n"
            f"Funding: {funding_str} | Open Interest: {oi_str} USDT\n"
            f"Стакан (Imbalance): {imbalance:.1f}% Buy / {100 - imbalance:.1f}% Sell"
        )
    except Exception as e:
        logger.warning(f"Ошибка загрузки метрик {symbol}: {e}")
        return f"📊 РЫНОЧНЫЙ КОНТЕКСТ ({symbol}): Данные временно недоступны."