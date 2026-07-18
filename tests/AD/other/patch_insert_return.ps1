# patch_insert_return.ps1
# Usage:
# Set-Location "D:\telega\LOCAL_AI_ENGINE\tests\AD"
# .\patch_insert_return.ps1
# This script makes a timestamped backup of indicator_test_full.py and inserts the return block
# before the "# ----------------- CLI -----------------" marker.

$path = Join-Path -Path (Get-Location).Path -ChildPath "indicator_test_full.py"
if (-not (Test-Path $path)) {
  Write-Error "File not found: $path"
  exit 1
}

# Backup
$bak = "$path.bak.$((Get-Date).ToString('yyyyMMddHHmmss'))"
Copy-Item -Path $path -Destination $bak -Force
Write-Host "Backup created:" $bak

# Read file
$content = Get-Content -Path $path -Raw -ErrorAction Stop

$marker = "# ----------------- CLI -----------------"
if ($content -notmatch [regex]::Escape($marker)) {
  Write-Error "Marker not found: $marker. Aborting."
  exit 2
}

# Block to insert (Python), ensure exact indentation and blank line before marker
$insert = @'
# --- Auto-inserted return output (added by patch_insert_return.ps1) ---
out = {
    "profile": profile,
    "flow_mode": flowMode,
    "raw_flow": float(rawFlow.iloc[last_idx]) if not rawFlow.empty else 0.0,
    "smoothed_flow": float(smoothedFlow.iloc[last_idx]) if not smoothedFlow.empty else 0.0,
    "flow_slope_pct": float(flowSlopePct.iloc[last_idx]) if not flowSlopePct.empty else 0.0,
    "cmf": float(cmfVal.iloc[last_idx]) if not cmfVal.empty else 0.0,
    "ad_bias": adBias,
    "ad_confirmation": adConfirmation,
    "ad_divergence": adDivergence,
    "ad_regime": adRegime,
    "ad_quality": adQuality,
    "ad_comment": adComment
}

return out

'@

# Insert before marker
$new = $content -replace [regex]::Escape($marker), ($insert + "`r`n" + $marker)

# Write back file (utf8)
Set-Content -Path $path -Value $new -Encoding utf8
Write-Host "Inserted return block into" $path

# Run quick check (check_compute_full.py) if exists
$check = Join-Path -Path (Get-Location).Path -ChildPath "check_compute_full.py"
if (Test-Path $check) {
  Write-Host "Running check_compute_full.py ..."
  & python $check ".\ETHUSDT_1d.csv" "1d"
  $rc = $LASTEXITCODE
  Write-Host "check_compute_full.py exit code:" $rc
} else {
  Write-Host "check_compute_full.py not found in folder; please run it manually after verifying the file."
}