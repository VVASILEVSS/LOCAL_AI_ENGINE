"""
extend_ohlcv.py — Download 1500 bars per symbol/TF to replace current 500-bar files.
This gives candidates enough forward context for label back-computation.
"""
import ccxt
import csv
import os
import time
from datetime import datetime

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XAUTUSDT']
TIMEFRAMES = ['15m', '1h', '4h', '1d']
OHLCV_DIR = r'D:\LOCAL_AI_ENGINE\data\ohlcv\current'
BAR_COUNT = 1500

exchange = ccxt.binance({'enableRateLimit': True})

for symbol in SYMBOLS:
    for tf in TIMEFRAMES:
        filepath = os.path.join(OHLCV_DIR, f'{symbol}_{tf}.csv')
        print(f'[{datetime.now():%H:%M:%S}] Downloading {symbol} {tf} ({BAR_COUNT} bars)...')

        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=BAR_COUNT)

            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['time', 'timestamp', 'open', 'high', 'low', 'close', 'volume'])
                for candle in ohlcv:
                    ts = candle[0]
                    dt = datetime.utcfromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    writer.writerow([
                        dt,
                        ts,
                        candle[1],  # open
                        candle[2],  # high
                        candle[3],  # low
                        candle[4],  # close
                        candle[5],  # volume
                    ])

            print(f'  -> Saved {len(ohlcv)} rows to {filepath}')

        except Exception as e:
            print(f'  -> ERROR: {e}')

        time.sleep(0.5)  # Rate limit

exchange.close()
print()
print('Done! All OHLCV files extended to 1500 bars.')
