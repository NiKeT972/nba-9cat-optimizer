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

# Everything the hidden run prints (including errors before the script's own log starts)
# goes to data\task-output.log, so a scheduled run is never a silent mystery.
$outLog = Join-Path $repo "data\task-output.log"
$command = '& ''{0}'' *> ''{1}''; exit $LASTEXITCODE' -f $script, $outLog
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"{0}`"" -f $command) `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Laptop-friendly: Windows' defaults refuse to start (or kill) tasks on battery power.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Downloads the latest NBA games, rebuilds the 9-Cat Optimizer site and pushes it to GitHub." `
    -Force | Out-Null

Write-Host ""
Write-Host "Done: '$TaskName' will run every day at $Time."
Write-Host "Run it now to test:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "See what happened:    Get-Content '$repo\data\refresh.log' -Tail 30"
Write-Host "Raw output of the last scheduled run:  Get-Content '$outLog'"
