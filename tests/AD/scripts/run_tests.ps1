# Пример PowerShell: скачать исторические свечи (пример Binance) и запустить тест
# Требует: Python + pandas + numpy, indicator_test.py в той же папке

$symbol = "ETHUSDT"
$interval = "15m"
$limit = 500
$outCsv = "data.csv"

# Пример вызова публичного API Binance Klines (сохраняем как CSV)
$uri = "https://api.binance.com/api/v3/klines?symbol=$symbol&interval=$interval&limit=$limit"
$response = Invoke-RestMethod -Method Get -Uri $uri
# Преобразуем в таблицу OHLCV
$rows = $response | ForEach-Object {
    [PSCustomObject]@{
        time = ([datetime]'1970-01-01').AddMilliseconds($_[0])
        open = [double]$_[1]
        high = [double]$_[2]
        low  = [double]$_[3]
        close= [double]$_[4]
        volume= [double]$_[5]
    }
}
$rows | Export-Csv -Path $outCsv -NoTypeInformation

# Запускаем python тест
python indicator_test.py $outCsv | Out-File result.json -Encoding utf8

# Печатаем результат
Get-Content result.json