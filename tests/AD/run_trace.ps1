param(
  [string]$symbol,
  [string]$tf
)

# examples:
# .\run_trace.ps1 ETHUSDT 1d

$root = (Get-Location).Path
$infile = Join-Path $root ("data\" + $symbol + "_" + $tf + ".csv")
$outfile = Join-Path $root ("traces\" + $symbol + "_" + $tf + "_trace.csv")

if (-not (Test-Path $infile)) { Write-Error "Input not found: $infile"; exit 1 }

Write-Host "Running trace: $infile -> $outfile"
python .\scripts\indicator_test_full_trace.py $infile $tf $outfile
