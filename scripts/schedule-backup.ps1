#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register a Windows Task Scheduler job that runs backup.py nightly.

.DESCRIPTION
    Creates a scheduled task called "AnnotationBackup" that runs
    scripts/backup.py every night at 02:00 using the repo's venv.

    Run once as Administrator from the repo root:
        .\scripts\schedule-backup.ps1 -Dest "\\fileserver\annotation-backups"

    To remove the task later:
        Unregister-ScheduledTask -TaskName "AnnotationBackup" -Confirm:$false

.PARAMETER Dest
    Destination directory for the backup (ideally a network share or a
    separate disk from the one holding the live data).

.PARAMETER Keep
    Number of database snapshots to retain (default 7).

.PARAMETER Hour
    Hour of day (24h) to run the backup (default 2 = 02:00).

.EXAMPLE
    # Back up to a network share, keep two weeks:
    .\scripts\schedule-backup.ps1 -Dest "\\NAS\backups\annotation" -Keep 14
#>

param(
    [Parameter(Mandatory)]
    [string]$Dest,

    [int]$Keep = 7,
    [int]$Hour = 2
)

$ErrorActionPreference = "Stop"

# Resolve paths relative to the repo root (the directory containing this script's parent).
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python   = Join-Path $repoRoot "venv\Scripts\python.exe"
$script   = Join-Path $repoRoot "scripts\backup.py"

if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python — run: python -m venv venv && venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "backup.py not found at $script"
    exit 1
}

$taskName = "AnnotationBackup"
$args     = "`"$script`" --dest `"$Dest`" --keep $Keep"
$action   = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory $repoRoot
$trigger  = New-ScheduledTaskTrigger -Daily -At "${Hour}:00"
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
    -Description "Nightly pg_dump + uploads mirror for the annotation workspace" `
    | Out-Null

Write-Host "✓ Scheduled task '$taskName' registered — runs daily at ${Hour}:00."
Write-Host "  Backup destination : $Dest"
Write-Host "  Snapshots to keep  : $Keep"
Write-Host ""
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Test run  :   Start-ScheduledTask -TaskName '$taskName'"
