# Run tuner (indentation-safe): sweep minFlowAbsFrac and profileAdMovePct for one profile.
# Run from D:\telega\LOCAL_AI_ENGINE\tests\AD
# Example:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_tuner.ps1

# === SETTINGS (edit if needed) ===
$profileToTune = "1h"
$symbols = @("BTCUSDT","XAUTUSDT")
$minFlowGrid = @(0.02,0.05,0.10,0.20,0.35)  # абс. порог в единицах flowNorm (диап. ~0..1.2)
$adPctGrid   = @(0.05,0.10,0.20,0.35)      # абс. порог flowAbsChange в единицах flowNorm
$runTraceScript = ".\run_trace.ps1"
# ==================================

$root = (Get-Location).Path
$scriptsDir = Join-Path $root "scripts"
$indicatorPath = Join-Path $scriptsDir "indicator_test_full.py"
$resultsDir = Join-Path $root "results"
$backsDir = Join-Path $resultsDir "backs"
New-Item -Path $resultsDir -ItemType Directory -Force | Out-Null
New-Item -Path $backsDir -ItemType Directory -Force | Out-Null

if (-not (Test-Path $indicatorPath)) {
    Write-Error ("Indicator file not found: {0}" -f $indicatorPath)
    exit 1
}

# read original
$origContent = Get-Content -Path $indicatorPath -Raw -ErrorAction Stop
$origBackup = Join-Path $backsDir ("indicator_test_full.py.orig." + (Get-Date -Format "yyyyMMddHHmmss") + ".bak")
Set-Content -Path $origBackup -Value $origContent -Encoding utf8
Write-Host ("Original backed up to: {0}" -f $origBackup)

$startMarker = "# --- AUTO_TUNER_START ---"
$endMarker   = "# --- AUTO_TUNER_END ---"
$markerLine  = '    # --- TUNER_INSERT_HERE ---'

function Apply-TunerPatch {
    param([double]$minFlow, [double]$adPct, [string]$profile)

    $content = Get-Content -Path $indicatorPath -Raw -ErrorAction Stop

    # remove existing tuner block between markers if present
    if ($content -match [regex]::Escape($startMarker)) {
        $patternBlock = [regex]::Escape($startMarker) + '.*?' + [regex]::Escape($endMarker)
        $content = [regex]::Replace($content, $patternBlock, "", [System.Text.RegularExpressions.RegexOptions]::Singleline)
    }

    # find marker line and its indentation
    $markerRegex = "(?m)^(?<indent>[ \t]*)" + [regex]::Escape($markerLine) + "\s*$"
    $m = [regex]::Match($content, $markerRegex)
    if (-not $m.Success) {
        Write-Error ("Marker line not found: {0}. Aborting patch." -f $markerLine)
        return $false
    }

    # [FIX 1] $null always on the left in PowerShell comparisons
    $indent = $m.Groups['indent'].Value
    if ($null -eq $indent -or $indent -eq "") { $indent = "    " }

    # [FIX 2] Format doubles with InvariantCulture to avoid "0,02" on Russian locale
    $minFlowStr = $minFlow.ToString("G", [System.Globalization.CultureInfo]::InvariantCulture)
    $adPctStr   = $adPct.ToString("G", [System.Globalization.CultureInfo]::InvariantCulture)

    # build block lines
    $blockLines = @()
    $blockLines += $startMarker
    $blockLines += "# AUTO_TUNER: set minFlowAbsFrac and profileAdMovePct for profile '" + $profile + "'"
    $blockLines += "if profile == `"" + $profile + "`":"
    $blockLines += "    minFlowAbsFrac = " + $minFlowStr
    $blockLines += "    profileAdMovePct = " + $adPctStr
    $blockLines += "# ensure threshold in flowNorm units"
    $blockLines += "minFlowAbsChange = max(minFlowAbsFrac, 1e-6)"
    $blockLines += $endMarker

    # apply same indentation as marker to each block line
    $blockIndented = ($blockLines | ForEach-Object { $indent + $_ }) -join "`r`n"

    # insert block after marker line
    # Вставляем блок ПЕРЕД маркером, маркер остаётся на месте для следующего запуска

    $new = $content -replace [regex]::Escape($markerLine), ($blockIndented + "`r`n" + $markerLine)
    Set-Content -Path $indicatorPath -Value $new -Encoding utf8
    return $true
}

$tuneResults = @()
$totalRuns = $minFlowGrid.Count * $adPctGrid.Count * $symbols.Count
$runIndex = 0

foreach ($minFlow in $minFlowGrid) {
    foreach ($adPct in $adPctGrid) {
        foreach ($sym in $symbols) {
            $runIndex++
            Write-Host ("`n=== RUN {0} / {1}: profile={2} sym={3} minFlow={4} adPct={5} ===" -f $runIndex, $totalRuns, $profileToTune, $sym, $minFlow, $adPct)

            $ok = Apply-TunerPatch -minFlow $minFlow -adPct $adPct -profile $profileToTune
            if (-not $ok) {
                Write-Warning ("Patch failed for minFlow={0} adPct={1}. Skipping." -f $minFlow, $adPct)
                continue
            }

            Start-Sleep -Milliseconds 200

            Write-Host ("Running trace: {0} {1} ..." -f $sym, $profileToTune)
            & $runTraceScript $sym $profileToTune
            Start-Sleep -Milliseconds 300

            $traceFile = Join-Path $root ("traces\$($sym)_$($profileToTune)_trace.csv")
            if (-not (Test-Path $traceFile)) {
                Write-Warning ("Trace file not found: {0}" -f $traceFile)
                $entry = [ordered]@{
                    profile              = $profileToTune
                    symbol               = $sym
                    minFlowAbsFrac       = $minFlow
                    profileAdMovePct     = $adPct
                    rows                 = 0
                    divergences          = "missing"
                    strong_confirmations = "missing"
                }
                $tuneResults += $entry
                continue
            }

            $csv    = Import-Csv $traceFile
            $rows   = $csv.Count
            $divs   = ($csv | Where-Object { $_.ad_divergence -ne 'none' }).Count
            $strong = ($csv | Where-Object { $_.ad_confirmation -like '*strong*' }).Count

            Write-Host (" -> {0}: rows={1} divergences={2} strong_confirmations={3}" -f $sym, $rows, $divs, $strong)

            $entry = [ordered]@{
                profile              = $profileToTune
                symbol               = $sym
                minFlowAbsFrac       = $minFlow
                profileAdMovePct     = $adPct
                rows                 = $rows
                divergences          = $divs
                strong_confirmations = $strong
            }
            $tuneResults += $entry

            Start-Sleep -Seconds 1
        }
    }
}

# restore original
Set-Content -Path $indicatorPath -Value $origContent -Encoding utf8
Write-Host ("`nRestored original indicator file from in-memory backup: {0}" -f $origBackup)

$outPath = Join-Path $resultsDir ("tune_summary_" + $profileToTune + "_" + (Get-Date -Format "yyyyMMddHHmmss") + ".json")
$tuneResults | ConvertTo-Json -Depth 5 | Out-File $outPath -Encoding utf8
Write-Host ("Tuning finished. Results saved to: {0}" -f $outPath)

$tabPath = Join-Path $resultsDir ("tune_table_" + $profileToTune + "_" + (Get-Date -Format "yyyyMMddHHmmss") + ".csv")
$tuneResults | Export-Csv -Path $tabPath -NoTypeInformation -Encoding utf8
Write-Host ("Summary table exported to: {0}" -f $tabPath)
