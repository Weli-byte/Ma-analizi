# Registers a Windows Scheduled Task that runs scripts\shadow_tick.ps1 every 30 minutes for the current user (no admin needed). NOT registered automatically:
# the owner runs this once if they want prospective shadow records to be collected. Remove it with:  Unregister-ScheduledTask -TaskName FootballShadowTick -Confirm:$false
# (the t-24h window of a fixture is 3 hours long, so a 30-minute tick cannot miss it; the t-90m / t-30m / kickoff windows are shorter and need the 10-minute tick of the snapshot engine.)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tick = Join-Path $root "scripts\shadow_tick.ps1"
$action = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "`"$(Join-Path $PSScriptRoot 'hidden_run.vbs')`" `"$tick`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FootballShadowTick" -Action $action -Trigger $trigger `
    -Settings $settings -Description "League-model shadow tick (ADR 0068), every 30 min" -Force | Out-Null
Get-ScheduledTask -TaskName "FootballShadowTick" | Select-Object TaskName, State
