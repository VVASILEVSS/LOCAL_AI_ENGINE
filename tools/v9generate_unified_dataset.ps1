param(
  [int]$ContextN = 10,
  [ValidateSet("center","left","right")]
  [string]$LabelAnchor = "center",
  [string]$ResultsDir = "results",
  [string]$OutFile = "results/unified_dataset.csv"
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
      return [datetime]::ParseExact(
        $raw,
        $fmt,
        [System.Globalization.CultureInfo]::InvariantCulture
      )
    } catch {}
  }

  try {
    return [datetime]::Parse($raw, [System.Globalization.CultureInfo]::InvariantCulture)
  } catch {
    return $null
  }
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

# ensure dirs
New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
$outDir = Split-Path -Parent $OutFile
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

# load mapping
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
    $labelDt = ParseDt $labelRawTime
    $labelTimeText = if ($null -ne $labelDt) { $labelDt.ToString("yyyy-MM-dd HH:mm:ss") } else { [string]$labelRawTime }

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
        datetime = [string]$rTime
        open = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("open","Open"))
        high = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("high","High"))
        low = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("low","Low"))
        close = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("close","Close"))
        volume = ToDoubleOrNull (Get-PropValue -Obj $r -Names @("volume","Volume","vol"))
        label_in_window = $inWindow
      }
    }

    $labelValue = Get-PropValue -Obj $labelRow -Names @("label","Label","y","target")

    # improved score / quality extraction
    $candScore = ToDoubleOrNull (Get-PropValue -Obj $c -Names @(
      "candidate_score","score","confidence","conf","probability","p","rank_score"
    ))

    $candQuality = Get-PropValue -Obj $c -Names @(
      "candidate_quality","quality","grade","signal_quality","status"
    )

    # optional enrichment fields
    $candType = Get-PropValue -Obj $c -Names @("type","kind","direction","signal_type")
    $candPrice = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("price","candidate_price","pivot_price"))
    $atrVal = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("atr","atr_value"))
    $flowVal = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("flow","flow_value","money_flow"))
    $momentumVal = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("momentum","mom"))
    $ratioVal = ToDoubleOrNull (Get-PropValue -Obj $c -Names @("ratio","rr","risk_reward"))

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

      candidate_score = $candScore
      candidate_quality = $candQuality
      candidate_type = $candType
      candidate_price = $candPrice

      atr = $atrVal
      flow = $flowVal
      momentum = $momentumVal
      ratio = $ratioVal

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
    label_value, label_datetime, `
    candidate_score, candidate_quality, candidate_type, candidate_price, `
    atr, flow, momentum, ratio, `
    context_json |
  Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8

Write-Host ("Unified dataset exported to: {0}" -f $OutFile) -ForegroundColor Green
Write-Host ("Total rows: {0}" -f $outRows.Count) -ForegroundColor Green
Write-Host ("Processed files: {0} | Skipped files: {1} | Invalid candidate rows: {2}" -f $processedFiles, $skippedFiles, $invalidCandidateRows) -ForegroundColor Cyan