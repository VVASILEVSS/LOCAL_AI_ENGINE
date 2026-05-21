# move_and_manifest_fixed.ps1
# Moves files from repository root into folders: traces,data,results,scripts,pine,logs,other
# Works NON-RECURSIVELY (only files located directly in the root folder).
# Avoids -Include/-Recurse pitfalls; excludes reorganize/patch scripts.
# Usage:
#   Set-Location "D:\telega\LOCAL_AI_ENGINE\tests\AD"
#   .\move_and_manifest_fixed.ps1
#
# Recommended: create a backup_before_move folder before running (example below).

$root = (Get-Location).Path

function Get-SafeDest([string]$destPath) {
    if (-not (Test-Path $destPath)) { return $destPath }
    $base = [System.IO.Path]::GetFileNameWithoutExtension($destPath)
    $ext  = [System.IO.Path]::GetExtension($destPath)
    $dir  = [System.IO.Path]::GetDirectoryName($destPath)
    $ts   = (Get-Date).ToString("yyyyMMddHHmmss")
    $new  = "{0}_{1}{2}" -f $base, $ts, $ext
    return (Join-Path -Path $dir -ChildPath $new)
}

# Ensure target folders exist
$folders = @("traces","data","results","scripts","pine","logs","other")
foreach ($f in $folders) {
    $p = Join-Path $root $f
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

# Collect files only from the root (non-recursive)
$rootFiles = Get-ChildItem -Path $root -File -ErrorAction SilentlyContinue

# Skip this script itself if present among root files
$selfName = if ($MyInvocation.MyCommand.Path) { Split-Path -Leaf $MyInvocation.MyCommand.Path } else { "" }
$rootFiles = $rootFiles | Where-Object { $_.Name -ne $selfName }

$moved = @()

# 1) traces: files with "trace" in name and .csv extension
$traces = $rootFiles | Where-Object { $_.Extension -ieq ".csv" -and ($_.Name -match "(?i)trace") }
foreach ($f in $traces) {
    $dest = Join-Path $root ("traces\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved trace: $($f.Name) -> traces\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 2) data: other .csv files
$dataFiles = $rootFiles | Where-Object { $_.Extension -ieq ".csv" -and -not ($_.Name -match "(?i)trace") }
foreach ($f in $dataFiles) {
    $dest = Join-Path $root ("data\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved data:  $($f.Name) -> data\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 3) json -> results
$jsons = $rootFiles | Where-Object { $_.Extension -ieq ".json" }
foreach ($f in $jsons) {
    $dest = Join-Path $root ("results\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved json:  $($f.Name) -> results\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 4) scripts (.py, .ps1) excluding reorganize/patch helpers (case-insensitive)
$scripts = $rootFiles | Where-Object { ($_.Extension -ieq ".py" -or $_.Extension -ieq ".ps1") -and ($_.Name -notmatch "(?i)reorganize_repo|patch") }
foreach ($f in $scripts) {
    $dest = Join-Path $root ("scripts\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved script: $($f.Name) -> scripts\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 5) pine files
$pines = $rootFiles | Where-Object { $_.Extension -ieq ".pine" -or $_.Extension -ieq ".pinescript" }
foreach ($f in $pines) {
    $dest = Join-Path $root ("pine\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved pine:   $($f.Name) -> pine\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 6) logs / txt
$logs = $rootFiles | Where-Object { $_.Extension -ieq ".log" -or $_.Extension -ieq ".txt" }
foreach ($f in $logs) {
    $dest = Join-Path $root ("logs\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved log:    $($f.Name) -> logs\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 7) remaining root files -> other (only files still in root)
$remaining = Get-ChildItem -Path $root -File | Where-Object { $_.FullName -notin $moved -and $_.DirectoryName -eq $root }
foreach ($f in $remaining) {
    $dest = Join-Path $root ("other\$($f.Name)")
    $safe = Get-SafeDest $dest
    Move-Item -Path $f.FullName -Destination $safe -Force
    Write-Host "Moved other:  $($f.Name) -> other\" (Split-Path $safe -Leaf)
    $moved += $f.FullName
}

# 8) Create manifest in results
$manifest = @()
foreach ($cat in $folders) {
    $folderPath = Join-Path $root $cat
    Get-ChildItem -Path $folderPath -File -ErrorAction SilentlyContinue | ForEach-Object {
        $manifest += [PSCustomObject]@{
            folder   = $cat
            name     = $_.Name
            size     = $_.Length
            modified = $_.LastWriteTimeUtc.ToString("o")
            path     = $_.FullName
        }
    }
}

$summaryPath = Join-Path $root "results\summary_all.json"
$manifest | ConvertTo-Json -Depth 5 | Out-File -FilePath $summaryPath -Encoding utf8
Write-Host "`nWrote manifest: $summaryPath (entries: $($manifest.Count))"

# 9) Print quick counts
foreach ($cat in $folders) {
    $count = (Get-ChildItem -Path (Join-Path $root $cat) -File -ErrorAction SilentlyContinue).Count
    Write-Host "$cat`t$count files"
}