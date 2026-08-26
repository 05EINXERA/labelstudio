#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register a Windows Task Scheduler job that runs backup.py twice a day.

.DESCRIPTION
    Creates a scheduled task called "AnnotationBackup" that runs
    scripts/backup.py at 12:00 and 16:00 using the repo's venv, retaining
    every snapshot indefinitely.

    Nothing is deleted automatically. Count-based retention at an hourly
    cadence previously bought under two days of history, which is why task 280's
    annotations could not be investigated on 2026-08-26 — the relevant dumps had
    already been pruned. Deleting a backup is now a deliberate act: pass -Keep to
    opt back in, or delete files by hand.

    The trade-off is unbounded growth (~2 dumps/day). backup.py prints the
    snapshot count, total size, and remaining runway on every run, and warns
    when the destination holds under 30 days of space.

    Run once as Administrator from the repo root:
        .\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups"

    To remove the task later:
        Unregister-ScheduledTask -TaskName "AnnotationBackup" -Confirm:$false

.PARAMETER Dest
    Destination directory for the backup (ideally a network share or a
    separate disk from the one holding the live data).

.PARAMETER Keep
    Number of database snapshots to retain. Default 0 means retention is
    DISABLED and --keep is not passed to backup.py, so nothing is ever deleted.
    A positive value re-enables newest-first pruning of workspace-*.db/.dump —
    the uploads mirror is never pruned either way.

.PARAMETER Hours
    Hours of the day (24h) to run at. Default @(12, 16). Each gets its own
    plain daily trigger.

.EXAMPLE
    # Default: 12:00 and 16:00, keeping every snapshot forever.
    .\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups"

.EXAMPLE
    # Three times a day to a network share, still keeping everything:
    .\scripts\schedule-backup.ps1 -Dest "\\NAS\backups\annotation" -Hours 8,12,16

.EXAMPLE
    # Opt back in to pruning, keeping the newest 60 snapshots (~30 days):
    .\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups" -Keep 60
#>

param(
    [Parameter(Mandatory)]
    [string]$Dest,

    [int]$Keep = 0,

    [ValidateCount(1, 24)]
    [ValidateRange(0, 23)]
    [int[]]$Hours = @(12, 16)
)

$ErrorActionPreference = "Stop"

# Resolve paths relative to the repo root (the directory containing this script's parent).
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
$script = Join-Path $repoRoot "scripts\backup.py"

if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python - run: python -m venv venv, then venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "backup.py not found at $script"
    exit 1
}

$taskName = "AnnotationBackup"
# --keep is omitted unless explicitly asked for: backup.py then retains every
# snapshot. Passing --keep 0 would delete ALL of them, so 0 means "don't pass it".
$taskArgs = "`"$script`" --dest `"$Dest`""
if ($Keep -gt 0) { $taskArgs += " --keep $Keep" }
$action = New-ScheduledTaskAction -Execute $python -Argument $taskArgs -WorkingDirectory $repoRoot

# One plain -Daily trigger per run time, rather than a single trigger carrying a
# repetition interval: an -Once repetition expires and the task quietly stops
# firing, which is exactly the failure mode found on this deployment (no task
# registered at all, and the last automatic dump two days stale). Plain daily
# triggers renew themselves and cannot lapse.
$trigger = @($Hours | Sort-Object -Unique | ForEach-Object {
        New-ScheduledTaskTrigger -Daily -At ("{0:00}:00" -f $_)
    })
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
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
    -Description "Scheduled pg_dump + uploads mirror for the annotation workspace" `
| Out-Null

$times = ($Hours | Sort-Object -Unique | ForEach-Object { "{0:00}:00" -f $_ }) -join " and "
Write-Host "[OK] Scheduled task '$taskName' registered - runs daily at $times."
Write-Host "  Backup destination : $Dest"
if ($Keep -gt 0) {
    Write-Host "  Snapshots to keep  : $Keep (older ones are DELETED each run)"
}
else {
    Write-Host "  Retention          : disabled - snapshots are never auto-deleted"
}
Write-Host ""
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Test run  :   Start-ScheduledTask -TaskName '$taskName'"