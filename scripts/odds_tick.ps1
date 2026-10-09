# One pass of the S15 odds pipeline (ADR 0030), PAPER ONLY: collect real DraftKings 1X2 lines (ESPN),
# pull the exact-timestamp quotes the cloud collector committed (sync-remote),
# compute edge/EV for locked pre-match forecasts (paper bets only when thresholds in configs\odds.yaml
# are met), settle finished paper bets. Appends to logs\odds_tick.log. No LLM calls, no real bets.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "odds_tick.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
foreach ($cmd in @(@("collect"), @("sync-remote"), @("value", "--paper"), @("settle"))) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $out = & $py -m src.odds.run @cmd 2>&1 | ForEach-Object { $_.ToString() }
    "[$stamp] $($cmd -join ' '): $($out -join ' | ')" | Out-File -Append -Encoding utf8 $log
}
# GitHub's own */10 cron runs only about every 6 hours (measured 2026-10-09), which misses the t-90m / t-30m
# collection windows. While this PC is on, trigger the cloud collector from here every tick (it spends no credit
# unless a window is open). Needs the gh CLI logged in; failure is logged, never fatal.
if (Get-Command gh -ErrorAction SilentlyContinue) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $out = & gh workflow run odds-exact.yml --repo Weli-byte/Ma-analizi 2>&1 | ForEach-Object { $_.ToString() }
    "[$stamp] dispatch odds-exact: $($out -join ' | ')" | Out-File -Append -Encoding utf8 $log
}
$lines = Get-Content $log -ErrorAction SilentlyContinue
if ($lines.Count -gt 3000) { $lines | Select-Object -Last 3000 | Set-Content -Encoding utf8 $log }
