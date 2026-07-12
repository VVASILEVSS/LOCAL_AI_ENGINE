@echo off
setlocal

cd /d D:\telega\LOCAL_AI_ENGINE

python -m core.zigzag.compare_zigzag ^
  --market-type future ^
  --symbols BTC/USDT XAUT/USDT ETH/USDT ^
  --output zigzag_compare.json

echo.
echo Done. Output:
echo D:\telega\LOCAL_AI_ENGINE\zigzag_compare.json
echo D:\telega\LOCAL_AI_ENGINE\zigzag_compare.md
pause