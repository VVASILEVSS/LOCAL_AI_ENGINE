# patch_fix_indent_return.ps1
# Fix indentation of previously auto-inserted return block in indicator_test_full.py
# Usage: Set-Location "D:\telega\LOCAL_AI_ENGINE\tests\AD"; .\patch_fix_indent_return.ps1

$path = Join-Path -Path (Get-Location).Path -ChildPath "indicator_test_full.py"
if (-not (Test-Path $path)) {
  Write-Error "File not found: $path"
  exit 1
}

# Backup current file
$bak = "$path.bak.indentfix.$((Get-Date).ToString('yyyyMMddHHmmss'))"
Copy-Item -Path $path -Destination $bak -Force
Write-Host "Backup created:" $bak

$content = Get-Content -Path $path -Raw -ErrorAction Stop

$marker = "# ----------------- CLI -----------------"
$startTag = "# --- Auto-inserted return output (added by patch_insert_return.ps1) ---"

if ($content -notmatch [regex]::Escape($marker)) {
  Write-Error "Marker not found: $marker. Aborting."
  exit 2
}

# Indented block to insert (each line prefixed with 4 spaces)
$indented = @"
    # --- Auto-inserted return output (fixed indentation) ---
    out = {
        ""profile"": profile,
        ""flow_mode"": flowMode,
        ""raw_flow"": float(rawFlow.iloc[last_idx]) if not rawFlow.empty else 0.0,
        ""smoothed_flow"": float(smoothedFlow.iloc[last_idx]) if not smoothedFlow.empty else 0.0,
        ""flow_slope_pct"": float(flowSlopePct.iloc[last_idx]) if not flowSlopePct.empty else 0.0,
        ""cmf"": float(cmfVal.iloc[last_idx]) if not cmfVal.empty else 0.0,
        ""ad_bias"": adBias,
        ""ad_confirmation"": adConfirmation,
        ""ad_divergence"": adDivergence,
        ""ad_regime"": adRegime,
        ""ad_quality"": adQuality,
        ""ad_comment"": adComment
    }

    return out

"@

# If previous auto-insert exists, replace everything from startTag up to marker with indented block + marker
$pattern = ([regex]::Escape($startTag) + ".*?" + [regex]::Escape($marker))
if ($content -match $startTag) {
  $new = [regex]::Replace($content, $pattern, $indented + $marker, [System.Text.RegularExpressions.RegexOptions]::Singleline)
  Set-Content -Path $path -Value $new -Encoding utf8
  Write-Host "Replaced previous auto-insert with indented block."
} else {
  # Otherwise, insert indented block before marker
  $new = $content -replace [regex]::Escape($marker), ($indented + $marker)
  Set-Content -Path $path -Value $new -Encoding utf8
  Write-Host "Inserted indented block before marker."
}

# Run quick check if check_compute_full.py exists
$check = Join-Path -Path (Get-Location).Path -ChildPath "check_compute_full.py"
if (Test-Path $check) {
  Write-Host "Running check_compute_full.py ..."
  & python $check ".\ETHUSDT_1d.csv" "1d"
  $rc = $LASTEXITCODE
  Write-Host "check_compute_full.py exit code:" $rc
} else {
  Write-Host "check_compute_full.py not found; please run it manually."
}