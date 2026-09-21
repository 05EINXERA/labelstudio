#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register a Windows Task Scheduler job that rolls up attendance daily and
    then prunes raw observations past retention.

.DESCRIPTION
    Creates a scheduled task called "AnnotationAttendanceRollup" that runs
    scripts/rollup_attendance.py once a day using the repo's venv.

    ROLL UP, THEN PRUNE -- and only one task, deliberately.

    Raw observations are deleted at 31 days; past that the attendance_days
    rollup is the only surviving record. Splitting these into two scheduled
    tasks would let the prune run on a day the rollup did not, which destroys
    history silently: nobody notices until someone asks about a month that is
    now empty. One script, one task, one order, and the script skips the prune
    entirely if any day failed to roll up.

    Runs at 02:00 by default: after midnight Kathmandu so "yesterday" is a
    finished day, and well clear of the hourly backup's ~28-minute window at
    the top of each hour.

    The script rolls up the last TWO local days, not one. A run that is missed
    (the box was off, the task was stopped) is then repaired by the next
    night's run rather than leaving a permanent hole in the record.

    Run once as Administrator from the repo root:
        .\scripts\schedule-attendance-rollup.ps1

    To remove the task later:
        Unregister-ScheduledTask -TaskName "AnnotationAttendanceRollup" -Confirm:$false

.PARAMETER Hour
    Hour of day (24h, local machine time) to run (default 2).

.PARAMETER RetentionDays
    Delete raw observations older than this many days (default 31, matching
    the retention answer in 05-open-questions.md Q1).

.PARAMETER Days
    How many trailing local days to roll up on each run (default 2).

.EXAMPLE
    .\scripts\schedule-attendance-rollup.ps1

.EXAMPLE
    # Keep raw rows for a fortnight instead, and run at 3am:
    .\scripts\schedule-attendance-rollup.ps1 -Hour 3 -RetentionDays 14
#>

param(
    [ValidateRange(0, 23)]
    [int]$Hour = 2,

    [ValidateRange(1, 365)]
    [int]$RetentionDays = 31,

    [ValidateRange(1, 31)]
    [int]$Days = 2
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python   = Join-Path $repoRoot "venv\Scripts\python.exe"
$script   = Join-Path $repoRoot "scripts\rollup_attendance.py"

if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python - run: python -m venv venv, then venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "rollup_attendance.py not found at $script"
    exit 1
}

$taskName = "AnnotationAttendanceRollup"
$args     = "`"$script`" --days $Days --retention-days $RetentionDays"
$action   = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory $repoRoot

# -Daily, not -Once with a repetition duration. An -Once trigger's repetition
# expires and the task quietly stops firing -- the failure mode already found
# on this deployment for the backup task (schedule-backup.ps1).
$trigger = New-ScheduledTaskTrigger -Daily -At "${Hour}:00"

# The battery flags are NOT optional on this box, and this is the same lesson
# the backup task learned the hard way in 2026-09-02: the deployment machine is
# a laptop, Windows defaults BOTH of these to "stop the task on battery", and
# every truncated backup matched a Kernel-Power 105 "power source change" to
# the second with the task exiting 0x8007050B.
#
# A killed rollup here is worse than a killed backup, because the prune is what
# follows it: a half-finished run that is then re-run is fine (the rollup is
# idempotent), but a rollup that is killed and never re-run leaves days
# unrolled until their raw rows prune. -StartWhenAvailable is what covers the
# box having been off at 02:00.
#
# 1h is far above the real runtime (~25 rows/day, seconds of work), and bounds
# a genuinely hung run without clipping a healthy one.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

# SYSTEM, so it does not require a logged-in user.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task '$taskName'."
}

Register-ScheduledTask `
    -TaskName  $taskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Description "Daily attendance rollup, then prune of raw observations past $RetentionDays days" `
    | Out-Null

# Read the settings back rather than trusting the register call. Some Windows
# builds silently drop the battery flags depending on the power profile, and
# that failure is invisible until a run is killed weeks later.
$applied = (Get-ScheduledTask -TaskName $taskName).Settings
if ($applied.DisallowStartIfOnBatteries -or $applied.StopIfGoingOnBatteries) {
    Write-Warning @"
The battery settings did not stick:
  DisallowStartIfOnBatteries = $($applied.DisallowStartIfOnBatteries)
  StopIfGoingOnBatteries     = $($applied.StopIfGoingOnBatteries)
This box is a laptop. Fix them in Task Scheduler (Conditions tab: untick both
'Start the task only if the computer is on AC power' and 'Stop if the computer
switches to battery power') or the rollup will be killed on a power change.
"@
} else {
    Write-Host "Battery settings verified."
}

Write-Host ""
Write-Host "Registered '$taskName':"
Write-Host "  runs daily at ${Hour}:00"
Write-Host "  rolls up the last $Days local day(s), then prunes raw rows older than $RetentionDays days"
Write-Host "  command: $python $args"
Write-Host ""
Write-Host "Check it with:"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host "Run it now (recommended, to confirm it works before relying on it):"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Dry run it by hand first if you prefer:"
Write-Host "  $python `"$script`" --dry-run"
