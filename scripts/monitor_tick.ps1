# One pass of the S16 monitor (ADR 0032): ingest newly finished results, refresh the registry, write
# artifacts\ops\report.{json,md}, append alerts. Appends to logs\monitor_tick.log. No LLM calls.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "monitor_tick.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$out = & $py -m src.mlops.report --collect-results --registry 2>&1 | ForEach-Object { $_.ToString() }
"[$stamp] $($out -join ' | ')" | Out-File -Append -Encoding utf8 $log
$lines = Get-Content $log -ErrorAction SilentlyContinue
if ($lines.Count -gt 3000) { $lines | Select-Object -Last 3000 | Set-Content -Encoding utf8 $log }
