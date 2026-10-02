# One pass of the S14 live engine (ADR 0029) over every configured live feed; appends to
# logs\live_tick.log. Cheap when nothing is live (3 small requests). No LLM calls.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "live_tick.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
$feeds = @(
    @("fdorg", "PL"),
    @("fdorg", "PD"),
    @("openligadb", "bl1")
)
foreach ($f in $feeds) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $out = & $py -m src.live.run --source $f[0] --league $f[1] 2>&1 | ForEach-Object { $_.ToString() }
    "[$stamp] $($f[0]) $($f[1]): $($out -join ' | ')" | Out-File -Append -Encoding utf8 $log
}
$lines = Get-Content $log -ErrorAction SilentlyContinue
if ($lines.Count -gt 3000) { $lines | Select-Object -Last 3000 | Set-Content -Encoding utf8 $log }
