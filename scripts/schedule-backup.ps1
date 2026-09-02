#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register a Windows Task Scheduler job that runs backup.py hourly.

.DESCRIPTION
    Creates a scheduled task called "AnnotationBackup" that runs
    scripts/backup.py every hour using the repo's venv, keeping the newest
    20 snapshots (~20 hours of hourly cover).

    The cadence is hourly rather than nightly because a daily dump means up to
    24 hours of exposure: the 2026-08-06 loss of task 707 was recoverable only
    from a 22-hour-old snapshot, and that day's own scheduled run had not
    landed at all. See .devnotes/offline/INCIDENT_707.md and INCIDENT_692.md.

    Run once as Administrator from the repo root:
        .\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups"

    To remove the task later:
        Unregister-ScheduledTask -TaskName "AnnotationBackup" -Confirm:$false

.PARAMETER Dest
    Destination directory for the backup (ideally a network share or a
    separate disk from the one holding the live data).

.PARAMETER Keep
    Number of database snapshots to retain (default 20). Pruning is done by
    backup.py itself, newest-first, and only touches workspace-*.db/.dump —
    the uploads mirror is never pruned.

.PARAMETER IntervalHours
    Hours between runs (default 1). Use 24 for the old nightly behaviour.

.PARAMETER Hour
    Hour of day (24h) for the first run; subsequent runs follow
    -IntervalHours from there (default 0, so runs land on the hour).

.EXAMPLE
    # Hourly to the local backup disk, keeping 20 snapshots:
    .\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups"

.EXAMPLE
    # Every 4 hours to a network share, keeping a week of them:
    .\scripts\schedule-backup.ps1 -Dest "\\NAS\backups\annotation" -IntervalHours 4 -Keep 42
#>

param(
    [Parameter(Mandatory)]
    [string]$Dest,

    [int]$Keep = 20,
    [ValidateRange(1, 24)]
    [int]$IntervalHours = 1,
    [ValidateRange(0, 23)]
    [int]$Hour = 0
)

$ErrorActionPreference = "Stop"

# Resolve paths relative to the repo root (the directory containing this script's parent).
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python   = Join-Path $repoRoot "venv\Scripts\python.exe"
$script   = Join-Path $repoRoot "scripts\backup.py"

if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python - run: python -m venv venv, then venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "backup.py not found at $script"
    exit 1
}

$taskName = "AnnotationBackup"
$args     = "`"$script`" --dest `"$Dest`" --keep $Keep"
$action   = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory $repoRoot
# A daily trigger carrying a repetition interval, rather than -Once -RepetitionDuration:
# an -Once trigger's repetition expires and the task quietly stops firing, which is
# exactly the failure mode found on the deployment (no task registered at all, and
# the last automatic dump two days stale). Anchoring to -Daily and repeating within
# the day means the schedule renews itself every day and cannot lapse.
$trigger = New-ScheduledTaskTrigger -Daily -At "${Hour}:00"
if ($IntervalHours -lt 24) {
    $trigger.Repetition = (New-ScheduledTaskTrigger `
        -Once -At "${Hour}:00" `
        -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
        -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition
}
# -DontStopIfGoingOnBatteries / -AllowStartIfOnBatteries are the fix for the
# 2026-09-02 truncated-backup incident, and are not optional on this box.
# Windows defaults BOTH to "stop the task on battery", and the deployment
# machine is a laptop: every truncated dump (~1.7-2.9 GB against a normal
# ~3.8 GB) matched a Kernel-Power 105 "power source change" to the second,
# with the task exiting 0x8007050B. The dumps still looked valid on disk.
#
# The time limit is 4h, not 30m: a full dump already takes ~28 minutes and
# grows a few hundred MB a day, so the old 30m limit sat ~2 minutes from
# killing every single run. This bounds a genuinely hung dump without
# clipping a healthy slow one. See backup.py's BACKUP_TRUNCATION note.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable   # run ASAP if the PC was off at trigger time

# Run as SYSTEM so it does not require a logged-in user.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

# Remove any old registration before re-registering.
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
    -Description "Hourly pg_dump + uploads mirror for the annotation workspace" `
    | Out-Null

# Read the settings back rather than trusting the register call. Some Windows
# builds silently drop the battery flags depending on the power profile, and
# that failure is invisible until a dump truncates weeks later.
$applied = (Get-ScheduledTask -TaskName $taskName).Settings
if ($applied.DisallowStartIfOnBatteries -or $applied.StopIfGoingOnBatteries) {
    Write-Warning "Battery settings did not apply (DisallowStart=$($applied.DisallowStartIfOnBatteries), StopIfGoing=$($applied.StopIfGoingOnBatteries))."
    Write-Warning "On a laptop this WILL truncate dumps mid-write. Fix in Task Scheduler > $taskName > Conditions > Power, or re-run elevated."
}

$cadence = if ($IntervalHours -eq 24) { "daily at ${Hour}:00" }
           else { "every $IntervalHours hour(s), starting ${Hour}:00" }
Write-Host "[OK] Scheduled task '$taskName' registered - runs $cadence."
Write-Host "  Power conditions   : runs on battery, not stopped on power-source change"
Write-Host "  Backup destination : $Dest"
Write-Host "  Snapshots to keep  : $Keep  (~$([math]::Round($Keep * $IntervalHours)) hours of cover)"
Write-Host ""
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Test run  :   Start-ScheduledTask -TaskName '$taskName'"
