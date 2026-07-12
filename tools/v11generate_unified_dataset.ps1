param(
  [int]$ContextN = 10,
  [ValidateSet("center","left","right")]
  [string]$LabelAnchor = "center",
  [string]$ResultsDir = "results",
  [string]$OutFile = "results/unified_dataset_v11.csv"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ParseDt {
  param([object]$Value)

  if ($null -eq $Value) { return $null }
  $raw = [string]$Value
  if ([string]::IsNullOrWhiteSpace($raw)) { return $null }

  $formats = @(
    "M/d/yyyy h:mm:ss tt",
    "M/d/yyyy H:mm:ss",
    "yyyy-MM-dd HH:mm:ss",
    "yyyy-MM-ddTHH:mm:ss",
    "yyyy-MM-dd"
  )

  foreach ($fmt in $formats) {
    try {
      return [datetime]::ParseExact($raw, $fmt, [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {}
  }

  try {
    return [datetime]::Parse($raw, [System.Globalization.CultureInfo]::InvariantCulture)
  } catch {
    return $null
  }
}

function Convert-DateTimeText {
  param([object]$Value)

  $dt = ParseDt $Value
  if ($null -eq $dt) { return $null }
  return $dt.ToString("yyyy-MM-dd HH:mm:ss")
}

function ToDoubleOrNull {
  param([object]$Value)

  if ($null -eq $Value) { return $null }
  $s = [string]$Value
  if ([string]::IsNullOrWhiteSpace($s)) { return $null }

  $tmp = 0.0
  if ([double]::TryParse($s, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$tmp)) {
    return $tmp
  }

  $s2 = $s.Replace(",", ".")
  if ([double]::TryParse($s2, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$tmp)) {
    return $tmp
  }

  return $null
}

function ToIntOrNull {
  param([object]$Value)
  $d = ToDoubleOrNull $Value
  if ($null -eq $d) { return $null }
  return [int][math]::Floor($d)
}

function Get-PropValue {
  param(
    [object]$Obj,
    [string[]]$Names
  )

  foreach ($n in $Names) {
    if ($null -ne $Obj.PSObject.Properties[$n]) {
      $v = $Obj.$n
      if ($null -ne $v -and -not [string]::IsNullOrWhiteSpace([string]$v)) {
        return $v
      }
    }
  }

  return $null
}

function Get-TimeField {
  param([object]$Row)
  return (Get-PropValue -Obj $Row -Names @("timestamp","datetime","time","date"))
}

function Clamp01 {
  param([double]$x)
  if ($x -lt 0.0) { return 0.0 }
  if ($x -gt 1.0) { return 1.0 }
  return $x
}

function Get-CandidateMetrics {
  param(
    [double]$PriceMovePct,
    [double]$FlowAbsChange,
    [double]$MinFlowAbsThreshold,
    [double]$FlowScale,
    [double]$Atr,
    [double]$CurrPrice
  )

  $priceComp = Clamp01 ([math]::Abs($PriceMovePct) / 0.01)

  $flowRatio = 0.0
  if ($MinFlowAbsThreshold -gt 0) {
    $flowRatio = [math]::Abs($FlowAbsChange) / $MinFlowAbsThreshold
  }
  $flowComp = Clamp01 ($flowRatio / 5.0)

  $scaleComp = 0.0
  if ($FlowScale -gt 0) {
    $scaleComp = Clamp01 (([math]::Log10($FlowScale + 1.0)) / 4.0)
  }

  $atrRatio = 0.0
  if ($CurrPrice -gt 0 -and $Atr -gt 0) {
    $atrRatio = $Atr / $CurrPrice
  }
  $atrComp = Clamp01 ($atrRatio / 0.02)

  $score01 =
    (0.40 * $priceComp) +
    (0.35 * $flowComp) +
    (0.15 * $scaleComp) +
    (0.10 * $atrComp)

  $score = [math]::Round($score01 * 100.0, 2)

  $quality = "weak"
  if ($score -ge 70) {
    $quality = "strong"
  } elseif ($score -ge 40) {
    $quality = "medium"
  }

  return [PSCustomObject]@{
    score = $score
    quality = $quality
    strength = [math]::Round($score01, 4)
    flow_ratio = [math]::Round($flowRatio, 6)
    atr_ratio = [math]::Round($atrRatio, 6)
  }
}

# Ensure dirs
New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
$outDir = Split-Path -Parent $OutFile
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

# Load mapping
$mapPath = Join-Path $ResultsDir "autotune_best_params.json"
$mapping = $null
if (Test-Path $mapPath) {
  try {
    $mapping = Get-Content $mapPath -Raw | ConvertFrom-Json
    Write-Host "Loaded mapping: $mapPath" -ForegroundColor Green
  } catch {
    Write-Host "Warning: cannot parse mapping: $mapPath" -ForegroundColor Yellow
    $mapping = $null
  }
} else {
  Write-Host "Mapping not found: $mapPath (defaults will be used)" -ForegroundColor Yellow
}

$candFiles = @(Get-ChildItem "$ResultsDir\*_candidates.json" -File -ErrorAction SilentlyContinue)
if ($candFiles.Count -eq 0) {
  Write-Host "No *_candidates.json found in $ResultsDir" -ForegroundColor Red
  exit 1
}

Write-Host ("Found candidate files: {0}" -f $candFiles.Count) -ForegroundColor Cyan

$outRows = @()
$processedFiles = 0
$skippedFiles = 0
$invalidCandidateRows = 0

foreach ($cf in $candFiles) {
  $processedFiles++

  $json = $null
  try {
    $json = Get-Content $cf.FullName -Raw | ConvertFrom-Json
  } catch {
    Write-Host ("Skip invalid JSON: {0}" -f $cf.FullName) -ForegroundColor Yellow
    $skippedFiles++
    continue
  }

  if ($null -eq $json.candidates -or @($json.candidates).Count -eq 0) {
    Write-Host ("No candidates in: {0}" -f $cf.Name) -ForegroundColor Yellow
    continue
  }

  $srcPath = [string](Get-PropValue -Obj $json -Names @("file","source_file","csv_file","source"))
  if ([string]::IsNullOrWhiteSpace($srcPath) -or -not (Test-Path $srcPath)) {
    Write-Host ("Source csv missing: {0}" -f $srcPath) -ForegroundColor Yellow
    $skippedFiles++
    continue
  }

  $src = @(Import-Csv $srcPath)
  $rowsCount = $src.Count
  if ($rowsCount -eq 0) {
    Write-Host ("Source csv is empty: {0}" -f $srcPath) -ForegroundColor Yellow
    continue
  }

  $symbol = ([IO.Path]::GetFileName($srcPath) -split '_')[0].ToUpper()
  $tfProfile = [string](Get-PropValue -Obj $json -Names @("profile","tf_profile","timeframe","tf"))
  if ([string]::IsNullOrWhiteSpace($tfProfile)) {
    $tfProfile = "unknown"
  }

  $pivotRight = switch ($tfProfile) {
    "15m" { 12 }
    "1h"  { 16 }
    "4h"  { 18 }
    "1d"  { 20 }
    default { 12 }
  }

  if ($null -ne $mapping -and $null -ne $mapping.$symbol -and $null -ne $mapping.$symbol.$tfProfile) {
    $bp = $mapping.$symbol.$tfProfile
    $mapPivot = ToIntOrNull (Get-PropValue -Obj $bp -Names @("pivotRight","pivot_right","right","pivot"))
    if ($null -ne $mapPivot) {
      $pivotRight = $mapPivot
    }
  }

  Write-Host ("Processing {0} | symbol={1} tf={2} rows={3} candidates={4}" -f $cf.Name, $symbol, $tfProfile, $rowsCount, @($json.candidates).Count) -ForegroundColor Gray

  foreach ($c in $json.candidates) {
    $candIdx = ToIntOrNull (Get-PropValue -Obj $c -Names @("i","idx","index","candidate_index"))
    if ($null -eq $candIdx) {
      $invalidCandidateRows++
      continue
    }

    if ($candIdx -lt 0) { $candIdx = 0 }
    if ($candIdx -ge $rowsCount) { $candIdx = $rowsCount - 1 }

    $labelIndex = switch ($LabelAnchor.ToLower()) {
      "center" { $candIdx }
      "left"   { $candIdx - $pivotRight }
      "right"  { $candIdx + $pivotRight }
      default  { $candIdx }
    }

    if ($labelIndex -lt 0) { $labelIndex = 0 }
    if ($labelIndex -ge $rowsCount) { $labelIndex = $rowsCount - 1 }

    $labelRow = $src[$labelIndex]
    $labelRawTime = Get-TimeField $labelRow
    $labelTimeText = Convert-DateTimeText $labelRawTime
    if ([string]::IsNullOrWhiteSpace($labelTimeText)) {
      $labelTimeText = [string]$labelRawTime
    }

    $contextStart = [Math]::Max(0, $candIdx - $ContextN)
    $contextEnd = [Math]::Min($rowsCount - 1, $candIdx + $ContextN)

    $ctx = @()
    for ($k = $contextStart; $k -le $contextEnd; $k++) {
      $r = $src[$k]
      $rTime = Get-TimeField $r

      $inWindow = "NO"
      if ($k -eq $labelIndex) { $inWindow = "YES" }

      $ctx += [PSCustomObject]@{
        offset = $k - $candIdx
        datetime = Convert-DateTimeText $rTime
        open = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("open","Open"))
        high = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("high","High"))
        low = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("low","Low"))
        close = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("close","Close"))
        volume = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("volume","Volume","vol"))
        label_in_window = $inWindow
      }
    }

    $labelValue = Get-PropValue -Obj $labelRow -Names @("label","Label","y","target")

    $candType = [string](Get-PropValue -Obj $c -Names @("type","kind","direction","signal_type"))

    $candTimeRaw = Get-PropValue -Obj $c -Names @("time","datetime","timestamp")
    $candTime = Convert-DateTimeText $candTimeRaw

    if ([string]::IsNullOrWhiteSpace($candTime) -or $candTime -match '^\d{4}-\d{2}-\d{2}$') {
      $rowTimeRaw = Get-TimeField $src[$candIdx]
      $rowTime = Convert-DateTimeText $rowTimeRaw
      if (-not [string]::IsNullOrWhiteSpace($rowTime)) {
        $candTime = $rowTime
      }
    }

    if ([string]::IsNullOrWhiteSpace($candTime) -or $candTime -notmatch '\d{2}:\d{2}:\d{2}') {
      $candTime = $labelTimeText
    }

    $prevPrice = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("prevPrice","prev_price"))
    $currPrice = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("currPrice","curr_price","price","candidate_price","pivot_price"))

    $priceMovePct = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("priceMovePct","price_move_pct"))
    $prevFlow = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("prevFlow","prev_flow"))
    $currFlow = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("currFlow","curr_flow"))
    $flowPctChange = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("flowPctChange","flow_pct_change"))
    $flowAbsChange = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("flowAbsChange","flow_abs_change"))
    $flowScale = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("flowScale","flow_scale","flow","flow_value","money_flow"))
    $minFlowAbsThreshold = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("minFlowAbsThreshold","min_flow_abs_threshold"))
    $atrVal = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("atr","atr_value"))

    $pm = if ($null -ne $priceMovePct) { $priceMovePct } else { 0.0 }
    $fa = if ($null -ne $flowAbsChange) { $flowAbsChange } else { 0.0 }
    $mt = if ($null -ne $minFlowAbsThreshold) { $minFlowAbsThreshold } else { 0.0 }
    $fs = if ($null -ne $flowScale) { $flowScale } else { 0.0 }
    $av = if ($null -ne $atrVal) { $atrVal } else { 0.0 }
    $cp = if ($null -ne $currPrice) { $currPrice } else { 0.0 }

    $computed = Get-CandidateMetrics -PriceMovePct $pm -FlowAbsChange $fa -MinFlowAbsThreshold $mt -FlowScale $fs -Atr $av -CurrPrice $cp

    $rawScore = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("candidate_score","score","confidence","conf","probability","p","rank_score"))
    $rawQuality = Get-PropValue -Obj $c -Names @("candidate_quality","quality","grade","signal_quality","status")

    $finalScore = if ($null -ne $rawScore) { $rawScore } else { $computed.score }
    $finalQuality = if ($null -ne $rawQuality) { [string]$rawQuality } else { [string]$computed.quality }

    $outRows += [PSCustomObject]@{
      symbol = $symbol
      tf_profile = $tfProfile
      timeframe = $tfProfile
      source_file = $srcPath
      rows_count = $rowsCount

      candidate_index = $candIdx
      label_index = $labelIndex
      pivot_right = $pivotRight
      context_n = $ContextN
      label_anchor = $LabelAnchor

      label_value = $labelValue
      label_datetime = $labelTimeText
      candidate_time = $candTime
      candidate_type = $candType

      prev_price = $prevPrice
      curr_price = $currPrice
      price_move_pct = $priceMovePct

      prev_flow = $prevFlow
      curr_flow = $currFlow
      flow_pct_change = $flowPctChange
      flow_abs_change = $flowAbsChange
      flow_scale = $flowScale
      min_flow_abs_threshold = $minFlowAbsThreshold
      atr = $atrVal

      candidate_score = $finalScore
      candidate_quality = $finalQuality
      candidate_strength = $computed.strength
      candidate_flow_ratio = $computed.flow_ratio
      candidate_atr_ratio = $computed.atr_ratio

      context_json = ($ctx | ConvertTo-Json -Compress -Depth 6)
    }
  }
}

if ($outRows.Count -eq 0) {
  Write-Host "No data to export (outRows is empty)" -ForegroundColor Yellow
  Write-Host ("Processed files: {0} | Skipped files: {1} | Invalid candidate rows: {2}" -f $processedFiles, $skippedFiles, $invalidCandidateRows) -ForegroundColor Cyan
  exit 0
}

$outRows |
  Select-Object `
    symbol, tf_profile, timeframe, source_file, rows_count, `
    candidate_index, label_index, pivot_right, context_n, label_anchor, `
    label_value, label_datetime, candidate_time, candidate_type, `
    prev_price, curr_price, price_move_pct, `
    prev_flow, curr_flow, flow_pct_change, flow_abs_change, flow_scale, min_flow_abs_threshold, atr, `
    candidate_score, candidate_quality, candidate_strength, candidate_flow_ratio, candidate_atr_ratio, `
    context_json |
  Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8

Write-Host ("Unified dataset exported to: {0}" -f $OutFile) -ForegroundColor Green
Write-Host ("Total rows: {0}" -f $outRows.Count) -ForegroundColor Green
Write-Host ("Processed files: {0} | Skipped files: {1} | Invalid candidate rows: {2}" -f $processedFiles, $skippedFiles, $invalidCandidateRows) -ForegroundColor Cyan