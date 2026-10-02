# Registers a Windows Scheduled Task running scripts\odds_tick.ps1 every 15 minutes (current user).
# Remove with:  Unregister-ScheduledTask -TaskName FootballOddsTick -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tick = Join-Path $root "scripts\odds_tick.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tick`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FootballOddsTick" -Action $action -Trigger $trigger `
    -Settings $settings -Description "S15 odds collection + paper value (ADR 0030), every 15 min" -Force | Out-Null
Get-ScheduledTask -TaskName "FootballOddsTick" | Select-Object TaskName, State
