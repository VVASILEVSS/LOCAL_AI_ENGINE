import ccxt
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)
_MARKETS_CACHE: Dict[str, Any] = {}

# ✅ Принудительный список: пары, которые должны быть ФЬЮЧЕРСАМИ (без слеша)
FORCED_FUTURES = {'XAGUSDT'}

async def load_markets_cache():
    """Загружает рынки с чётким разделением Spot/Futures и хранит ID для API"""
    global _MARKETS_CACHE
    if not _MARKETS_CACHE:
        _MARKETS_CACHE = {}

        # 1. Загружаем Спот (приоритет)
        ex_spot = ccxt.binance({'options': {'defaultType': 'spot'}, 'enableRateLimit': True})
        spot_markets = await asyncio.to_thread(ex_spot.load_markets)
        for m in spot_markets.values():
            clean_sym = m.get('symbol', m['id']).split(':')[0]
            _MARKETS_CACHE[m['id']] = {'id': m['id'], 'symbol': clean_sym, 'is_futures': False}

        # 2. Загружаем Фьючерсы (только если их нет в Споте)
        ex_fut = ccxt.binance({'options': {'defaultType': 'future'}, 'enableRateLimit': True})
        fut_markets = await asyncio.to_thread(ex_fut.load_markets)
        for m in fut_markets.values():
            if m['id'] not in _MARKETS_CACHE:
                _MARKETS_CACHE[m['id']] = {'id': m['id'], 'symbol': m['id'], 'is_futures': True}

        # ✅ ПЕРЕОПРЕДЕЛЯЕМ статус для пар из FORCED_FUTURES
        for fid in FORCED_FUTURES:
            if fid in _MARKETS_CACHE:
                _MARKETS_CACHE[fid]['is_futures'] = True
                _MARKETS_CACHE[fid]['symbol'] = fid  # Без слеша: XAGUSDT

        logger.info(f"📊 Загружено рынков Binance: {len(_MARKETS_CACHE)}")

def format_symbol(symbol_id: str) -> str:
    """Спот → BTC/USDT | Фьючерсы → XAGUSDT"""
    m = _MARKETS_CACHE.get(symbol_id)
    if not m:
        for q in ['USDT', 'BUSD', 'BTC', 'ETH', 'DAI', 'TRY', 'EUR']:
            if symbol_id.endswith(q):
                return f"{symbol_id[:-len(q)]}/{q}"
        return symbol_id
    return symbol_id if m['is_futures'] else m['symbol']

def is_futures(symbol_id: str) -> bool:
    m = _MARKETS_CACHE.get(symbol_id)
    return m['is_futures'] if m else False

async def validate_symbol(raw: str) -> dict:
    raw = raw.strip().upper()
    if not _MARKETS_CACHE:
        await load_markets_cache()

    market = _MARKETS_CACHE.get(raw) or _MARKETS_CACHE.get(raw.replace('/', '')) or _MARKETS_CACHE.get(raw.replace('_', ''))
    if not market:
        prefix = raw[:3] if len(raw) >= 3 else raw
        suggestions = [m['id'] for m in _MARKETS_CACHE.values() if prefix in m.get('id', '')]
        sugg_text = f"\n💡 Похожие: {', '.join(list(set(suggestions))[:5])}" if suggestions else ""
        return {
            'valid': False,
            'error': f"❌ Тикер `{raw}` не найден на Binance.{sugg_text}\n📝 Вводите ID биржи (напр: XAGUSDT, BTCUSDT, XAUTUSDT)."
        }

    # ✅ ИСПРАВЛЕНИЕ: вернул ключ 'symbol', которого не хватало cmd_scan
    return {
        'valid': True,
        'id': market['id'],
        'symbol': market['symbol'],
        'type': 'Futures' if market['is_futures'] else 'Spot'
    }

async def fetch_ticker_safe(symbol_id: str) -> Any:
    for m_type in ['spot', 'future', 'swap']:
        try:
            ex = ccxt.binance({'options': {'defaultType': m_type}, 'enableRateLimit': True})
            return await asyncio.to_thread(ex.fetch_ticker, symbol_id)
        except (ccxt.BadSymbol, ccxt.ExchangeError):
            continue
        except Exception:
            continue
    raise ValueError(f"Не удалось получить данные для {symbol_id}")

# Справочник приоритета ТФ (0 — самый старший)
TF_ORDER = {
    "1D": 0,
    "4h": 1,
    "1h": 2,
    "15m": 3,
    "5m": 4,
    "1m": 5
}

def normalize_timeframe(tf: str) -> str:
    tf = tf.strip()
    if tf.lower() == "1d":
        return "1D"
    return tf.lower()

def sort_timeframes(tfs: list) -> list:
    """Сортирует список таймфреймов от старшего к младшему"""
    normalized = [normalize_timeframe(tf) for tf in tfs]
    return sorted(normalized, key=lambda tf: TF_ORDER.get(tf, 99))