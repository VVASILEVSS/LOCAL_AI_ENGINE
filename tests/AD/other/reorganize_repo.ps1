# reorganize_repo_fixed.ps1
# Usage: Set-Location "D:\telega\LOCAL_AI_ENGINE\tests\AD"; .\reorganize_repo_fixed.ps1
$root = (Get-Location).Path  # string path
$me = $MyInvocation.MyCommand.Name

# target folders mapping (folder -> patterns)
$mapping = @{
  "data"    = @("data_*_*.csv", "*_*.csv", "*.csv")
  "traces"  = @("*_trace_postpatch.csv","*_trace*.csv","trace_*.csv")
  "results" = @("*_full_postpatch.json","*_full*.json","*_summary*.json","summary_*.json")
  "scripts" = @("*.py","*.ps1")
  "pine"    = @("*.pine","*.pinescript")
  "logs"    = @("*.log","*.txt")
}

# Ensure folders exist
foreach ($folder in $mapping.Keys) {
  $path = Join-Path -Path $root -ChildPath $folder
  if (-not (Test-Path $path)) {
    New-Item -ItemType Directory -Path $path | Out-Null
    Write-Host "Created folder: $folder"
  }
}

# Generic mover: move files matching patterns into folder, skipping this script
function Move-FilesToFolder {
  param(
    [string[]] $Patterns,
    [string] $FolderName
  )
  $targetDir = Join-Path -Path $root -ChildPath $FolderName
  foreach ($pat in $Patterns) {
    $items = Get-ChildItem -Path $root -Filter $pat -File -ErrorAction SilentlyContinue
    foreach ($it in $items) {
      if ($it.Name -eq $me) { continue }                     # skip running script
      # if file already in target dir (name collision), skip/move with suffix
      $destFull = Join-Path -Path $targetDir -ChildPath $it.Name
      if ($it.FullName -eq $destFull) {
        # already in place
        continue
      }
      try {
        Move-Item -Path $it.FullName -Destination $destFull -Force
        Write-Host ("Moved {0} -> {1}" -f $it.Name, $FolderName)
      } catch {
        Write-Warning ("Could not move {0} -> {1}: {2}" -f $it.Name, $FolderName, $_.Exception.Message)
      }
    }
  }
}

# Apply mapping in order (more specific first)
Move-FilesToFolder -Patterns @("*_trace_postpatch.csv","*_trace*.csv","trace_*.csv") -FolderName "traces"
Move-FilesToFolder -Patterns @("*_full_postpatch.json","*_full*.json") -FolderName "results"
Move-FilesToFolder -Patterns @("*_summary_postpatch.json","*_summary*.json","summary_*.json") -FolderName "results"
Move-FilesToFolder -Patterns @("data_*_*.csv","*_*.csv") -FolderName "data"
Move-FilesToFolder -Patterns @("*.py") -FolderName "scripts"
Move-FilesToFolder -Patterns @("*.ps1") -FolderName "scripts"
Move-FilesToFolder -Patterns @("*.pine","*.pinescript") -FolderName "pine"
Move-FilesToFolder -Patterns @("*.log","*.txt") -FolderName "logs"

# Finally move any remaining files (except this script) into "other"
$otherDir = Join-Path -Path $root -ChildPath "other"
if (-not (Test-Path $otherDir)) { New-Item -ItemType Directory -Path $otherDir | Out-Null }
Get-ChildItem -Path $root -File -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Name -eq $me) { return }
  if ($_.DirectoryName -eq $otherDir) { return }
  $dest = Join-Path -Path $otherDir -ChildPath $_.Name
  try {
    Move-Item -Path $_.FullName -Destination $dest -Force
    Write-Host ("Moved remaining {0} -> other" -f $_.Name)
  } catch {
    Write-Warning ("Could not move remaining {0}: {1}" -f $_.Name, $_.Exception.Message)
  }
}

# Create manifest
