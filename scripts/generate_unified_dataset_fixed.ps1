param(
  [int]$ContextN = 10,
  [string]$LabelAnchor = "center",
  [string]$ResultsDir = "results",
  [string]$OutFile = "results/unified_dataset.csv"
)

New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null

$mapPath = Join-Path $ResultsDir "autotune_best_params.json"
$mapping = $null
if (Test-Path $mapPath) {
  try { $mapping = Get-Content $mapPath -Raw | ConvertFrom-Json } catch { $mapping = $null }
}

function ParseDt($s) {
  if ($null -eq $s -or $s -eq "") { return $null }
  $formats = @(
    "M/d/yyyy h:mm:ss tt",
    "M/d/yyyy H:mm:ss",
    "yyyy-MM-dd HH:mm:ss",
    "yyyy-MM-dd HH:mm",
    "yyyy-MM-dd"
  )
  foreach ($fmt in $formats) {
    try { return [datetime]::ParseExact($s.Trim(), $fmt, [System.Globalization.CultureInfo]::InvariantCulture) } catch {}
  }
  try { return [datetime]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return $null }
}

function NormalizeTime($dt) {
  if ($null -eq $dt) { return "" }
  return $dt.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
}

function EscapeJsonForCsv($jsonStr) {
  if ($null -eq $jsonStr -or $jsonStr -eq "") { return '""' }
  # Экранируем кавычки для CSV
  $escaped = $jsonStr -replace '"', '""'
  # Возвращаем в кавычках для безопасности
  return '"' + $escaped + '"'
}

$candFiles = Get-ChildItem "$ResultsDir\*_candidates.json" -File -ErrorAction SilentlyContinue
if ($candFiles.Count -eq 0) {
  Write-Host "❌ No candidate files found in $ResultsDir"
  exit 1
}

$outRows = @()

foreach ($cf in $candFiles) {
  Write-Host "Processing: $($cf.Name)"
  
  $j = $null
  try { 
    $j = Get-Content $cf.FullName -Raw | ConvertFrom-Json 
  } catch { 
    Write-Host "⚠️  Skip invalid JSON: $($cf.FullName)"
    continue 
  }
  
  if ($null -eq $j.candidates -or $j.candidates.Count -eq 0) { 
    Write-Host "⚠️  No candidates in $($cf.Name)"
    continue 
  }
  
  $srcPath = $j.file
  if (-not (Test-Path $srcPath)) { 
    Write-Host "❌ Source csv missing: $srcPath"
    continue 
  }
  
  $src = Import-Csv $srcPath
  $rowsCount = $src.Count
  if ($rowsCount -eq 0) {
    Write-Host "⚠️  Source csv is empty: $srcPath"
    continue
  }

  foreach ($c in $j.candidates) {
    # === ПАРСИНГ БАЗОВЫХ ПОЛЕЙ ===
    $symbol = ([IO.Path]::GetFileName($srcPath) -split '_')[0].ToUpper()
    $tf_profile = $j.profile
    
    # Таймфрейм в минуты
    $tfMinutes = @{ "5m"=5; "15m"=15; "1h"=60; "4h"=240; "1d"=1440 }
    $durM = if ($tfMinutes.ContainsKey($tf_profile)) { $tfMinutes[$tf_profile] } else { 60 }
    
    # Получаем параметры pivots из маппинга или используем дефолты
    $pivotRight = switch ($tf_profile) { 
      "15m" {12} 
      "1h" {16} 
      "4h" {18} 
      "1d" {20} 
      default {12} 
    }
    
    $pivotLeft = 12  # дефолт
    
    if ($mapping -and $mapping.$symbol -and $mapping.$symbol.$tf_profile) {
      $tfMap = $mapping.$symbol.$tf_profile
      if ($tfMap.pivotRight) { try { $pivotRight = [int]$tfMap.pivotRight } catch {} }
      if ($tfMap.pivotLeft) { try { $pivotLeft = [int]$tfMap.pivotLeft } catch {} }
    }
    
    # === РАСЧЁТ ИНДЕКСА МЕТКИ ===
    $i = [int]$c.i
    $labelIndex = switch ($LabelAnchor.ToLower()) {
      "center" { $i }
      "left"   { $i - $pivotRight }
      "right"  { $i + $pivotRight }
      default  { $i }
    }
    
    if ($labelIndex -lt 0) { $labelIndex = 0 }
    if ($labelIndex -ge $rowsCount) { $labelIndex = $rowsCount - 1 }
    
    # === МЕТКИ ВРЕМЕНИ ===
    $labelRow = $src[$labelIndex]
    $dtLabel = ParseDt($labelRow.time)
    
    $labelTimeUTC = NormalizeTime($dtLabel)
    $labelEndUTC = ""
    if ($null -ne $dtLabel) {
      $labelEndUTC = NormalizeTime($dtLabel.AddMinutes($durM))
    }
    
    $dtIso = ParseDt($c.time)
    $time_iso = NormalizeTime($dtIso)
    if ($time_iso -eq "") { $time_iso = $c.time }
    
    $candidate_time = $c.time
    
    # === ЦЕНА МЕТКИ ===
    $labelPrice = if ($c.type -eq "bull") { 
      [double]$labelRow.low 
    } else { 
      [double]$labelRow.high 
    }
    
    # === КОНТЕКСТ OHLCV ===
    $ctxStart = [math]::Max(0, $labelIndex - $ContextN)
    $ctxEnd = [math]::Min($rowsCount - 1, $labelIndex + $ContextN)
    $ctx = @()
    $sumLeftVol = 0.0
    $sumRightVol = 0.0
    
    for ($k = $ctxStart; $k -le $ctxEnd; $k++) {
      $r = $src[$k]
      $dtRow = ParseDt($r.time)
      $timeISO = NormalizeTime($dtRow)
      if ($timeISO -eq "") { $timeISO = $r.time }
      
      $vol = [double]$r.volume
      
      $ctx += @{
        idx    = [int]$k
        time   = $timeISO
        open   = [double]$r.open
        high   = [double]$r.high
        low    = [double]$r.low
        close  = [double]$r.close
        volume = $vol
      }
      
      if ($k -lt $labelIndex) { 
        $sumLeftVol += $vol 
      } elseif ($k -gt $labelIndex) { 
        $sumRightVol += $vol 
      }
    }
    
    # Сериализуем контекст в JSON (compact, одна строка)
    $ctxJson = ConvertTo-Json $ctx -Compress -Depth 10
    $ctxJsonCsv = EscapeJsonForCsv($ctxJson)
    
    # === HIGHS/LOWS ===
    $highs = @()
    $lows = @()
    for ($k = $ctxStart; $k -le $ctxEnd; $k++) {
      $r = $src[$k]
      $highs += [double]$r.high
      $lows += [double]$r.low
    }
    $top = ($highs | Measure-Object -Maximum).Maximum
    $bottom = ($lows | Measure-Object -Minimum).Minimum
    $mid = ([double]$top + [double]$bottom) / 2.0
    
    # === MOMENTUM ===
    $closeStart = [double]$src[$ctxStart].close
    $closeEnd = [double]$src[$ctxEnd].close
    $momentum = if ($closeStart -eq 0) { 0 } else { ($closeEnd - $closeStart) / $closeStart }
    
    # === DELTA VOLUME ===
    $deltaVol = if ($sumLeftVol -eq 0) { 
      ($sumRightVol - $sumLeftVol) 
    } else { 
      ($sumRightVol - $sumLeftVol) / $sumLeftVol 
    }
    
    # === RATIO & STRENGTH ===
    $minFlowAbsThreshold = [double]$c.minFlowAbsThreshold
    $flowAbsChange = [double]$c.flowAbsChange
    $ratio = if ($minFlowAbsThreshold -eq 0) { 9999 } else { $flowAbsChange / $minFlowAbsThreshold }
    $strength = if ($ratio -ge 3) { "strong" } else { "weak" }
    
    # === СОЗДАЁМ ОБЪЕКТ СТРОКИ ===
    $obj = [PSCustomObject]@{
      symbol                  = $symbol
      tf_profile              = $tf_profile
      candidate_idx           = $i
      candidate_time          = $candidate_time
      time_iso                = $time_iso
      label_index             = $labelIndex
      label_time              = $labelTimeUTC
      label_end_time          = $labelEndUTC
      label_price             = [math]::Round($labelPrice, 2)
      prev_price              = [math]::Round([double]$c.prevPrice, 2)
      curr_price              = [math]::Round([double]$c.currPrice, 2)
      prev_flow               = [math]::Round([double]$c.prevFlow, 6)
      curr_flow               = [math]::Round([double]$c.currFlow, 6)
      flow_abs_change         = [math]::Round($flowAbsChange, 6)
      flow_pct_change         = [math]::Round([double]$c.flowPctChange, 6)
      price_move_pct          = [math]::Round([double]$c.priceMovePct, 6)
      atr                     = [math]::Round([double]$c.atr, 2)
      flow_scale              = [math]::Round([double]$c.flowScale, 2)
      min_flow_abs_threshold  = [math]::Round($minFlowAbsThreshold, 4)
      min_flow_pct            = [math]::Round([double]$c.minFlowPct, 4)
      min_price_move_pct      = [math]::Round([double]$c.minPriceMovePct, 6)
      pivot_left              = $pivotLeft
      pivot_right             = $pivotRight
      context_start_idx       = $ctxStart
      context_end_idx         = $ctxEnd
      top_price               = [math]::Round($top, 2)
      bottom_price            = [math]::Round($bottom, 2)
      mid_price               = [math]::Round($mid, 2)
      context_ohlcv_json      = $ctxJsonCsv  # Уже экранирован
      delta_volume            = [math]::Round($deltaVol, 6)
      momentum                = [math]::Round($momentum, 6)
      ratio                   = [math]::Round($ratio, 6)
      strength                = $strength
      action                  = ""
      comment                 = ""
      llm_feedback            = ""
    }
    
    $outRows += $obj
  }
}

# === ЭКСПОРТ В CSV ===
if ($outRows.Count -eq 0) {
  Write-Host "❌ No rows generated."
  exit 1
} else {
  # PowerShell Export-Csv автоматически экранирует в CSV
  $outRows | Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8 -Force
  Write-Host "✅ WROTE: $OutFile"
  Write-Host "   Rows: $($outRows.Count)"
  Write-Host ""
  Write-Host "First 3 rows:"
  $outRows | Select-Object -First 3 | Format-Table symbol, tf_profile, label_price, strength -AutoSize
}
