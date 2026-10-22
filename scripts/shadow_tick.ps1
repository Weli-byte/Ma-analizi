# One tick of the league-model shadow layer (Sprint 6, ADR 0068): writes immutable PRE-MATCH shadow records for fixtures whose stage window is open, reconciles
# finished results, evaluates the alarms (a fail-closed alarm trips the kill switch) and refreshes the dashboard. Read-only towards the outside world (one free-tier
# schedule read per competition), append-only towards artifacts\league_models\rollout. No LLM calls, no paid calls, nothing user-visible. Appends to logs\shadow_tick.log.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "shadow_tick.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
foreach ($cmd in @("shadow", "reconcile", "monitor", "scorecard", "dashboard")) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $out = & $py -m src.league_models.rollout $cmd 2>&1 | ForEach-Object { $_.ToString() }
    "[$stamp] $cmd exit=$LASTEXITCODE $(($out | Select-Object -Last 3) -join ' | ')" | Out-File -Append -Encoding utf8 $log
}
$lines = Get-Content $log -ErrorAction SilentlyContinue
if ($lines.Count -gt 3000) { $lines | Select-Object -Last 3000 | Set-Content -Encoding utf8 $log }
