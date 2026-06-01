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
  if ($null -eq $s) { return $null }
  $formats = @("M/d/yyyy h:mm:ss tt","M/d/yyyy H:mm:ss","yyyy-MM-dd HH:mm:ss","yyyy-MM-dd")
  foreach ($fmt in $formats) {
    try { return [datetime]::ParseExact($s, $fmt, $null) } catch {}
  }
  try { return [datetime]::Parse($s) } catch { return $null }
}

$candFiles = Get-ChildItem "$ResultsDir\*_candidates.json" -File -ErrorAction SilentlyContinue
$outRows = @()
foreach ($cf in $candFiles) {
  $j = $null
  try { $j = Get-Content $cf.FullName -Raw | ConvertFrom-Json } catch { Write-Host "Skip invalid JSON: $($cf.FullName)"; continue }
  if ($null -eq $j.candidates) { continue }
  $srcPath = $j.file
  if (-not (Test-Path $srcPath)) { Write-Host "Source csv missing: $srcPath"; continue }
  $src = Import-Csv $srcPath
  $rowsCount = $src.Count

  foreach ($c in $j.candidates) {
    $symbol = ([IO.Path]::GetFileName($srcPath) -split '_')[0].ToUpper()
    $tf_profile = $j.profile
    $tfMinutes = @{ "5m"=5; "15m"=15; "1h"=60; "4h"=240; "1d"=1440 }
    $pivotRight = switch ($tf_profile) { "15m" {12} "1h" {16} "4h" {18} "1d" {20} default {12} }
    if ($mapping -and $mapping.$symbol -and $mapping.$symbol.$tf_profile -and $mapping.$symbol.$tf_profile.pivotRight) {
      try { $pivotRight = [int]$mapping.$symbol.$tf_profile.pivotRight } catch {}
    }
    $i = [int]$c.i
    switch ($LabelAnchor.ToLower()) {
      "center" { $labelIndex = $i }
      "left"   { $labelIndex = $i - $pivotRight }
      "right"  { $labelIndex = $i + $pivotRight }
      default  { $labelIndex = $i }
    }
    if ($labelIndex -lt 0) { $labelIndex = 0 }
    if ($labelIndex -ge $rowsCount) { $labelIndex = $rowsCount - 1 }

    $labelRow = $src[$labelIndex]
    $dtLabel = ParseDt($labelRow.time)
    $labelTimeUTC = ""
    $labelEndUTC = ""
    $durM = if ($tfMinutes.ContainsKey($tf_profile)) { $tfMinutes[$tf_profile] } else { 60 }
    if ($null -eq $dtLabel) {
      $labelTimeUTC = $labelRow.time
      $labelEndUTC = ""
    } else {
      $labelTimeUTC = $dtLabel.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
      $labelEndUTC = $dtLabel.AddMinutes($durM).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    }
    $dtIso = ParseDt($c.time)
    $time_iso = if ($null -eq $dtIso) { $c.time } else { $dtIso.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }

    $labelPrice = if ($c.type -eq "bull") { [double]$labelRow.low } else { [double]$labelRow.high }

    $ctxStart = [math]::Max(0, $labelIndex - $ContextN)
    $ctxEnd = [math]::Min($rowsCount - 1, $labelIndex + $ContextN)
    $ctx = @()
    $sumLeftVol = 0.0; $sumRightVol = 0.0
    for ($k=$ctxStart; $k -le $ctxEnd; $k++) {
      $r = $src[$k]
      $dtRow = ParseDt($r.time)
      $timeISO = if ($null -ne $dtRow) { $dtRow.ToString("yyyy-MM-dd HH:mm:ss") } else { $r.time }
      $ctx += @{
        idx = $k;
        time = $timeISO;
        open = $r.open;
        high = $r.high;
        low = $r.low;
        close = $r.close;
        volume = $r.volume
      }
      if ($k -lt $labelIndex) { $sumLeftVol += [double]$r.volume } elseif ($k -gt $labelIndex) { $sumRightVol += [double]$r.volume }
    }

    # Теперь сериализация context_ohlcv_json как single-line-JSON
    $ctxJson = (ConvertTo-Json $ctx -Compress -EscapeHandling EscapeNonAscii)
    # И затем экранировать для CSV:
    $ctxJson = $ctxJson -replace '"', '\"'

    $highs = $src[$ctxStart..$ctxEnd] | ForEach-Object { [double]$_.high }
    $lows = $src[$ctxStart..$ctxEnd]  | ForEach-Object { [double]$_.low }
    $top = ($highs | Measure-Object -Maximum).Maximum
    $bottom = ($lows | Measure-Object -Minimum).Minimum
    $mid = ([double]$top + [double]$bottom)/2.0

    $closeStart = [double]$src[$ctxStart].close
    $closeEnd = [double]$src[$ctxEnd].close
    $momentum = if ($closeStart -eq 0) { 0 } else { ($closeEnd - $closeStart) / $closeStart }
    $deltaVol = if ($sumLeftVol -eq 0) { ($sumRightVol - $sumLeftVol) } else { ($sumRightVol - $sumLeftVol) / [double]($sumLeftVol) }

    $ratio = if ([double]$c.minFlowAbsThreshold -eq 0) { 9999 } else { [double]$c.flowAbsChange / [double]$c.minFlowAbsThreshold }
    $strength = if ($ratio -ge 3) { "strong" } else { "weak" }

    $pivotLeft = ""
    if ($mapping -and $mapping.$symbol -and $mapping.$symbol.$tf_profile -and $mapping.$symbol.$tf_profile.pivotLeft) {
      try { $pivotLeft = [int]$mapping.$symbol.$tf_profile.pivotLeft } catch {}
    }

    $obj = [PSCustomObject]@{
      symbol             = $symbol
      tf_profile         = $tf_profile
      candidate_idx      = $i
      candidate_time     = $c.time
      time_iso           = $time_iso
      label_index        = $labelIndex
      label_time         = $labelTimeUTC
      label_end_time     = $labelEndUTC
      label_price        = [double]$labelPrice
      prev_price         = [double]$c.prevPrice
      curr_price         = [double]$c.currPrice
      prev_flow          = [double]$c.prevFlow
      curr_flow          = [double]$c.currFlow
      flow_abs_change    = [double]$c.flowAbsChange
      flow_pct_change    = [double]$c.flowPctChange
      price_move_pct     = [double]$c.priceMovePct
      atr                = ([double]$c.atr)
      flow_scale         = ([double]$c.flowScale)
      min_flow_abs_threshold = ([double]$c.minFlowAbsThreshold)
      min_flow_pct           = ([double]$c.minFlowPct)
      min_price_move_pct     = ([double]$c.minPriceMovePct)
      pivot_left         = $pivotLeft
      pivot_right        = $pivotRight
      context_start_idx  = $ctxStart
      context_end_idx    = $ctxEnd
      top_price          = $top
      bottom_price       = $bottom
      mid_price          = [math]::Round($mid,6)
      context_ohlcv_json = $ctxJson
      delta_volume       = [math]::Round($deltaVol,6)
      momentum           = [math]::Round($momentum,6)
      ratio              = [math]::Round($ratio,6)
      strength           = $strength
      action             = ""
      comment            = ""
      llm_feedback       = ""
    }
    $outRows += $obj
  }
}

if ($outRows.Count -eq 0) {
  Write-Host "No candidates found to export."
} else {
  foreach ($row in $outRows) {
    $row.context_ohlcv_json = $row.context_ohlcv_json -replace '"', '""'  # CSV-экранирование
  }
  $outRows | Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8
  Write-Host "WROTE $OutFile (count: $($outRows.Count))"
}