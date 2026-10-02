# Registers a Windows Scheduled Task that runs scripts\snapshot_tick.ps1 every 10 minutes for the
# current user (no admin needed). Remove it with:  Unregister-ScheduledTask -TaskName FootballSnapshotTick -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tick = Join-Path $root "scripts\snapshot_tick.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tick`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FootballSnapshotTick" -Action $action -Trigger $trigger `
    -Settings $settings -Description "S13 snapshot engine tick (ADR 0027), every 10 min" -Force | Out-Null
Get-ScheduledTask -TaskName "FootballSnapshotTick" | Select-Object TaskName, State
