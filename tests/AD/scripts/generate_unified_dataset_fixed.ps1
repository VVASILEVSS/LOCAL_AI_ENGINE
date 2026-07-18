param(
  [int]$ContextN = 10,
  [string]$LabelAnchor = "center",
  [string]$ResultsDir = "results",
  [string]$OutFile = "results/unified_dataset_fixed.csv"
)

New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null

# ✅ FIX #1: Proper JSON serialization helper
function ConvertToProperJson($obj) {
  $json = ConvertTo-Json $obj -Compress
  # Escape quotes properly for CSV
  $json = $json -replace '"', '""'
  return "`"$json`""
}

function ParseDt($s) {
  if ($null -eq $s -or $s -eq "") { return $null }
  $formats = @(
    "M/d/yyyy h:mm:ss tt",
    "M/d/yyyy H:mm:ss",
    "yyyy-MM-dd HH:mm:ss",
    "yyyy-MM-dd HH:mm:ss.fff",
    "yyyy-MM-dd",
    "M/d/yyyy"
  )
  foreach ($fmt in $formats) {
    try { 
      $dt = [datetime]::ParseExact($s, $fmt, [System.Globalization.CultureInfo]::InvariantCulture)
      return $dt
    } catch {}
  }
  try { 
    return [datetime]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture)
  } catch { 
    Write-Warning "Failed to parse date: $s"
    return $null 
  }
}

function SafeDouble($val) {
  if ($null -eq $val -or $val -eq "") { return 0.0 }
  try { return [double]$val } catch { return 0.0 }
}

# ✅ FIX #2: Load mapping
$mapPath = Join-Path $ResultsDir "autotune_best_params.json"
$mapping = $null
if (Test-Path $mapPath) {
  try { 
    $mapping = Get-Content $mapPath -Raw | ConvertFrom-Json 
  } catch { 
    Write-Warning "Failed to load mapping: $mapPath"
    $mapping = $null 
  }
}

$candFiles = Get-ChildItem "$ResultsDir\*_candidates.json" -File -ErrorAction SilentlyContinue
$outRows = @()
$validCount = 0
$skipCount = 0

foreach ($cf in $candFiles) {
  Write-Host "Processing: $($cf.Name)"
  
  $j = $null
  try { 
    $j = Get-Content $cf.FullName -Raw | ConvertFrom-Json 
  } catch { 
    Write-Warning "Skip invalid JSON: $($cf.FullName)"
    $skipCount++
    continue 
  }
  
  if ($null -eq $j.candidates) { 
    Write-Warning "No candidates in: $($cf.Name)"
    $skipCount++
    continue 
  }
  
  $srcPath = $j.file
  if (-not (Test-Path $srcPath)) { 
    Write-Warning "Source csv missing: $srcPath"
    $skipCount++
    continue 
  }
  
  $src = @(Import-Csv $srcPath)
  $rowsCount = $src.Count
  if ($rowsCount -eq 0) {
    Write-Warning "Empty source file: $srcPath"
    $skipCount++
    continue
  }

  foreach ($c in $j.candidates) {
    # ✅ FIX #3: Safe field extraction
    $symbol = ([IO.Path]::GetFileName($srcPath) -split '_')[0].ToUpper()
    $tf_profile = $j.profile ?? "1h"
    
    $tfMinutes = @{ "5m"=5; "15m"=15; "1h"=60; "4h"=240; "1d"=1440 }
    $pivotRight = switch ($tf_profile) { 
      "15m" {12} 
      "1h" {16} 
      "4h" {18} 
      "1d" {20} 
      default {12} 
    }
    
    if ($mapping -and $mapping.$symbol -and $mapping.$symbol.$tf_profile) {
      $mappedPivot = $mapping.$symbol.$tf_profile.pivotRight
      if ($null -ne $mappedPivot) {
        try { $pivotRight = [int]$mappedPivot } catch {}
      }
    }

    $i = if ($c.i -is [int]) { $c.i } else { [int]($c.i ?? 0) }
    
    # ✅ FIX #4: Proper label index calculation
    $labelIndex = switch ($LabelAnchor.ToLower()) {
      "center" { $i }
      "left"   { $i - $pivotRight }
      "right"  { $i + $pivotRight }
      default  { $i }
    }
    
    if ($labelIndex -lt 0) { $labelIndex = 0 }
    if ($labelIndex -ge $rowsCount) { $labelIndex = $rowsCount - 1 }

    # ✅ FIX #5: Validate row exists
    if ($labelIndex -lt 0 -or $labelIndex -ge $src.Count) {
      Write-Warning "Label index out of range: $labelIndex (max: $($src.Count-1))"
      $skipCount++
      continue
    }

    $labelRow = $src[$labelIndex]
    $dtLabel = ParseDt($labelRow.time)
    
    $labelTimeUTC = ""
    $labelEndUTC = ""
    $durM = if ($tfMinutes.ContainsKey($tf_profile)) { $tfMinutes[$tf_profile] } else { 60 }
    
    if ($null -eq $dtLabel) {
      $labelTimeUTC = $labelRow.time ?? "N/A"
      $labelEndUTC = "N/A"
    } else {
      $labelTimeUTC = $dtLabel.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
      $labelEndUTC = $dtLabel.AddMinutes($durM).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    }
    
    # ✅ FIX #6: Proper ISO time conversion
    $dtIso = ParseDt($c.time)
    $time_iso = if ($null -eq $dtIso) { $c.time ?? "N/A" } else { $dtIso.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }

    $labelPrice = if (($c.type ?? "bull") -eq "bull") { 
      SafeDouble($labelRow.low) 
    } else { 
      SafeDouble($labelRow.high) 
    }

    # ✅ FIX #7: Build context with proper idx preservation
    $ctxStart = [math]::Max(0, $labelIndex - $ContextN)
    $ctxEnd = [math]::Min($rowsCount - 1, $labelIndex + $ContextN)
    $ctx = @()
    $sumLeftVol = 0.0
    $sumRightVol = 0.0
    
    for ($k=$ctxStart; $k -le $ctxEnd; $k++) {
      if ($k -ge $src.Count) { break }
      $r = $src[$k]
      $dtRow = ParseDt($r.time)
      $timeISO = if ($null -ne $dtRow) { $dtRow.ToString("yyyy-MM-dd HH:mm:ss") } else { $r.time ?? "" }
      
      $ctx += @{
        idx    = $k;
        time   = $timeISO;
        open   = SafeDouble($r.open);
        high   = SafeDouble($r.high);
        low    = SafeDouble($r.low);
        close  = SafeDouble($r.close);
        volume = SafeDouble($r.volume)
      }
      
      if ($k -lt $labelIndex) { 
        $sumLeftVol += SafeDouble($r.volume) 
      } elseif ($k -gt $labelIndex) { 
        $sumRightVol += SafeDouble($r.volume) 
      }
    }

    # ✅ FIX #8: Proper JSON escaping
    $ctxJson = ConvertTo-Json $ctx -Compress
    # Double-escape for CSV: quotes become ""
    $ctxJsonEscaped = ($ctxJson -replace '"', '""')

    $highs = $ctx | ForEach-Object { $_.high }
    $lows = $ctx | ForEach-Object { $_.low }
    $top = if ($highs) { ($highs | Measure-Object -Maximum).Maximum } else { 0 }
    $bottom = if ($lows) { ($lows | Measure-Object -Minimum).Minimum } else { 0 }
    $mid = if ($top -gt 0 -and $bottom -gt 0) { ($top + $bottom) / 2.0 } else { 0 }

    $closeStart = SafeDouble($src[$ctxStart].close)
    $closeEnd = SafeDouble($src[$ctxEnd].close)
    $momentum = if ($closeStart -eq 0) { 0 } else { ($closeEnd - $closeStart) / $closeStart }
    $deltaVol = if ($sumLeftVol -eq 0) { ($sumRightVol - $sumLeftVol) } else { ($sumRightVol - $sumLeftVol) / $sumLeftVol }

    $minFlowThresh = SafeDouble($c.minFlowAbsThreshold)
    $ratio = if ($minFlowThresh -eq 0) { 9999 } else { (SafeDouble($c.flowAbsChange)) / $minFlowThresh }
    $strength = if ($ratio -ge 3) { "strong" } else { "weak" }

    $pivotLeft = ""
    if ($mapping -and $mapping.$symbol -and $mapping.$symbol.$tf_profile) {
      $mappedPivotL = $mapping.$symbol.$tf_profile.pivotLeft
      if ($null -ne $mappedPivotL) {
        try { $pivotLeft = [int]$mappedPivotL } catch {}
      }
    }

    # ✅ FIX #9: Create proper CSV row
    $obj = [PSCustomObject]@{
      symbol             = $symbol
      tf_profile         = $tf_profile
      candidate_idx      = $i
      candidate_time     = $c.time ?? ""
      time_iso           = $time_iso
      label_index        = $labelIndex
      label_time         = $labelTimeUTC
      label_end_time     = $labelEndUTC
      label_price        = [math]::Round($labelPrice, 2)
      prev_price         = [math]::Round((SafeDouble($c.prevPrice)), 2)
      curr_price         = [math]::Round((SafeDouble($c.currPrice)), 2)
      prev_flow          = [math]::Round((SafeDouble($c.prevFlow)), 6)
      curr_flow          = [math]::Round((SafeDouble($c.currFlow)), 6)
      flow_abs_change    = [math]::Round((SafeDouble($c.flowAbsChange)), 6)
      flow_pct_change    = [math]::Round((SafeDouble($c.flowPctChange)), 6)
      price_move_pct     = [math]::Round((SafeDouble($c.priceMovePct)), 6)
      atr                = [math]::Round((SafeDouble($c.atr)), 2)
      flow_scale         = [math]::Round((SafeDouble($c.flowScale)), 2)
      min_flow_abs_threshold = [math]::Round($minFlowThresh, 6)
      min_flow_pct       = [math]::Round((SafeDouble($c.minFlowPct)), 6)
      min_price_move_pct = [math]::Round((SafeDouble($c.minPriceMovePct)), 6)
      pivot_left         = $pivotLeft
      pivot_right        = $pivotRight
      context_start_idx  = $ctxStart
      context_end_idx    = $ctxEnd
      top_price          = [math]::Round($top, 2)
      bottom_price       = [math]::Round($bottom, 2)
      mid_price          = [math]::Round($mid, 6)
      context_ohlcv_json = $ctxJsonEscaped  # ✅ Properly escaped
      delta_volume       = [math]::Round($deltaVol, 6)
      momentum           = [math]::Round($momentum, 6)
      ratio              = [math]::Round($ratio, 6)
      strength           = $strength
      action             = ""
      comment            = ""
      llm_feedback       = ""
    }
    
    $outRows += $obj
    $validCount++
  }
}

Write-Host "`n========== EXPORT SUMMARY =========="
Write-Host "Valid rows exported: $validCount"
Write-Host "Skipped rows: $skipCount"

if ($validCount -eq 0) {
  Write-Host "❌ No valid data to export!"
} else {
  # ✅ FIX #10: Proper CSV export with correct encoding
  $outRows | Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8 -Force
  Write-Host "✅ WROTE: $OutFile"
  Write-Host "   Rows: $($outRows.Count)"
  
  # Verify export
  $verify = Import-Csv $OutFile
  Write-Host "   Verified: $($verify.Count) rows can be read"
  Write-Host "   First row JSON keys: $($verify[0].context_ohlcv_json.Length) chars"
}
