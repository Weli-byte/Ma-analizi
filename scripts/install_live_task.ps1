# Registers a Windows Scheduled Task that runs scripts\live_tick.ps1 every 2 minutes for the current
# user. Remove it with:  Unregister-ScheduledTask -TaskName FootballLiveTick -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tick = Join-Path $root "scripts\live_tick.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tick`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FootballLiveTick" -Action $action -Trigger $trigger `
    -Settings $settings -Description "S14 live engine pass (ADR 0029), every 2 min" -Force | Out-Null
Get-ScheduledTask -TaskName "FootballLiveTick" | Select-Object TaskName, State
