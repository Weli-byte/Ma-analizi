# Registers a Windows Scheduled Task running scripts\monitor_tick.ps1 every 30 minutes (current user).
# Remove with:  Unregister-ScheduledTask -TaskName FootballMonitorTick -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tick = Join-Path $root "scripts\monitor_tick.ps1"
$action = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "`"$(Join-Path $PSScriptRoot 'hidden_run.vbs')`" `"$tick`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FootballMonitorTick" -Action $action -Trigger $trigger `
    -Settings $settings -Description "S16 monitoring, results ingestion, registry (ADR 0032), every 30 min" -Force | Out-Null
Get-ScheduledTask -TaskName "FootballMonitorTick" | Select-Object TaskName, State
