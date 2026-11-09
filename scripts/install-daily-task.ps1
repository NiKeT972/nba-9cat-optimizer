<#
.SYNOPSIS
  Register refresh.ps1 with Windows Task Scheduler so the data refreshes every day.

.DESCRIPTION
  Creates (or updates) a task for your Windows user that runs scripts\refresh.ps1 daily.
  If the computer is off or asleep at that time, Windows runs it as soon as it can.
  It needs no administrator rights and uses the git sign-in you already have.

  NBA games finish overnight US time; noon (in India or the US) is after the night's
  games are final. Change the time with -Time.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-daily-task.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-daily-task.ps1 -Time 09:30
.EXAMPLE
  # remove it again
  Unregister-ScheduledTask -TaskName "NBA 9-Cat daily refresh" -Confirm:$false
#>
param(
    [string]$Time = "12:00",
    [string]$TaskName = "NBA 9-Cat daily refresh"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "refresh.ps1"
if (-not (Test-Path $script)) { throw "Can't find $script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"{0}`"" -f $script) `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Downloads the latest NBA games, rebuilds the 9-Cat Optimizer site and pushes it to GitHub." `
    -Force | Out-Null

Write-Host ""
Write-Host "Done: '$TaskName' will run every day at $Time."
Write-Host "Run it now to test:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "See what happened:    Get-Content '$repo\data\refresh.log' -Tail 30"
