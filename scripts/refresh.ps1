<#
.SYNOPSIS
  Daily data refresh: download the latest NBA games, rebuild the web app and push to
  GitHub (Vercel redeploys automatically about a minute later).

.DESCRIPTION
  stats.nba.com blocks cloud servers (GitHub Actions included), so the refresh runs on
  your own computer. Run this script by hand, or let Windows Task Scheduler run it every
  day: see install-daily-task.ps1.

  Every run is appended to data\refresh.log, so you can check what happened.
  The download is "strict": if the NBA site can't be reached, the run fails and nothing
  is pushed, instead of re-publishing old data as if it were new.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\refresh.ps1
#>

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logFile = Join-Path $repo "data\refresh.log"
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

# Run a command, log its output, and stop if it fails. Python and git write normal
# progress to stderr, so stderr must not count as an error by itself (only the exit code).
function Run([string]$exe, [string[]]$argList) {
    Log ("> {0} {1}" -f $exe, ($argList -join " "))
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $exe @argList 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    foreach ($line in $output) { Log ("    " + $line) }
    if ($code -ne 0) { throw ("'{0} {1}' failed (exit code {2})" -f $exe, ($argList -join " "), $code) }
}

try {
    Log "===== NBA 9-Cat refresh started ====="

    if (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
    else { throw "Python was not found. Install Python 3.11+ and tick 'Add python.exe to PATH'." }

    # 1. Get the latest code (and any data pushed from elsewhere).
    Run "git" @("pull", "--rebase", "--autostash")

    # 2. Download the newest games and schedule; fail if the NBA site can't be reached.
    Run $py @("-m", "src.data.pipeline", "--force", "--strict")

    # 3. Make sure last season (the projection prior) is cached. It never changes, so this is instant after the first time.
    $prior = (& $py -c "from src import config; print(config.PRIOR_SEASON)").Trim()
    Run $py @("-m", "src.data.pipeline", "--season", $prior)

    # 4. Rebuild the web app from the new data.
    Run $py @("-m", "app.static_site")

    # 5. Commit and push only if something changed.
    Run "git" @("add", "data/raw", "public")
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & git diff --cached --quiet
    $changed = $LASTEXITCODE -ne 0
    $ErrorActionPreference = $previous
    if ($changed) {
        $today = Get-Date -Format "yyyy-MM-dd"
        Run "git" @("commit", "-m", "Refresh NBA data and site ($today)")
        Run "git" @("push")
        Log "Pushed. The site updates in about a minute: https://nba-9cat-optimizer.vercel.app"
    } else {
        Log "No changes to push."
    }
    Log "===== Done ====="
    exit 0
}
catch {
    Log ("FAILED: " + $_.Exception.Message)
    Log "Nothing was pushed. Run this script by hand to see the full output, or check data\refresh.log."
    exit 1
}
