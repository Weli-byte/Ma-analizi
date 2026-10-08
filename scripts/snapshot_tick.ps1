# One scheduler tick for the S13 snapshot engine (ADR 0027): runs `python -m src.snapshot.run`
# for each league and appends the output to logs\snapshot_tick.log. Cheap when no stage window is
# open (it only fetches the real fixture list). No LLM calls: --with-llm is deliberately NOT passed
# here, so a scheduled tick never spends API money; add it explicitly if you want that.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "snapshot_tick.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
foreach ($league in @("PL", "PD")) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    "[$stamp] league=$league" | Out-File -Append -Encoding utf8 $log
    & $py -m src.snapshot.run --league $league 2>&1 | ForEach-Object { $_.ToString() } | Out-File -Append -Encoding utf8 $log
    "[$stamp] league=$league exit=$LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
}
# match intelligence (scores, goals, corners, cards): skips fixtures refreshed within refresh_hours
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
& $py -m src.markets.run 2>&1 | ForEach-Object { $_.ToString() } | Out-File -Append -Encoding utf8 $log
"[$stamp] markets exit=$LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
# keep the log small: retain the last 2000 lines
$lines = Get-Content $log -ErrorAction SilentlyContinue
if ($lines.Count -gt 2000) { $lines | Select-Object -Last 2000 | Set-Content -Encoding utf8 $log }
