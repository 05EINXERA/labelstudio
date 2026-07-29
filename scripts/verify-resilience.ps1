#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Confirm the Phase 0 resilience tooling is actually installed and healthy
    on this machine, not just that the scripts exist in the repo.

.DESCRIPTION
    install-service.ps1 and schedule-backup.ps1 are "run once as
    Administrator" setup scripts — nothing in the repo can prove they were
    ever actually run on the deployment PC, or that they're still working.
    This script checks the *installed state* directly:

      1. AnnotationApp scheduled task exists and is Ready
      2. AnnotationBackup scheduled task exists and is Ready
      3. The service wrapper log has recent activity
      4. A database snapshot exists in the backup destination from the
         last 24 hours
      5. Power plan is High Performance with sleep/hibernate disabled

    Run this after first installing the service/backup tasks, and
    periodically afterward (e.g. monthly) to catch silent drift — a task
    getting disabled, a share becoming unreachable, etc.

.PARAMETER BackupDest
    The backup destination directory passed to schedule-backup.ps1. Required
    to check for a recent snapshot; omit to skip that check.

.EXAMPLE
    .\scripts\verify-resilience.ps1 -BackupDest "\\fileserver\annotation-backups"
#>

param(
    [string]$BackupDest
)

$results = @()

function Add-Result([string]$Name, [bool]$Ok, [string]$Detail) {
    $script:results += [pscustomobject]@{ Check = $Name; Ok = $Ok; Detail = $Detail }
}

# 1 & 2 — scheduled tasks
foreach ($taskName in @("AnnotationApp", "AnnotationBackup")) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Add-Result $taskName $false "Not registered — run the matching install script as Administrator."
        continue
    }
    $info = $task | Get-ScheduledTaskInfo
    $ok = $task.State -in @("Ready", "Running")
    $detail = "State=$($task.State); LastRunTime=$($info.LastRunTime); LastResult=$($info.LastTaskResult)"
    Add-Result $taskName $ok $detail
}

# 3 — service wrapper log activity
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $repoRoot ".env"
$dataDir = $null
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^\s*DATA_DIR\s*=' } | Select-Object -First 1
    if ($line) { $dataDir = ($line -split '=', 2)[1].Trim().Trim('"') }
}
if (-not $dataDir) { $dataDir = Join-Path $repoRoot "data" }

$serviceLog = Join-Path $dataDir "logs\service.log"
if (Test-Path $serviceLog) {
    $age = (Get-Date) - (Get-Item $serviceLog).LastWriteTime
    $ok = $age.TotalHours -lt 24
    Add-Result "Service log activity" $ok "Last write: $([math]::Round($age.TotalHours, 1))h ago ($serviceLog)"
} else {
    Add-Result "Service log activity" $false "Not found at $serviceLog — is install-service.ps1 actually running the app?"
}

# 4 — recent backup snapshot
if ($BackupDest) {
    if (Test-Path $BackupDest) {
        $snapshot = Get-ChildItem $BackupDest -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^workspace-\d{8}-\d{6}\.(db|dump)$' } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($snapshot) {
            $age = (Get-Date) - $snapshot.LastWriteTime
            $ok = $age.TotalHours -lt 26  # a bit over 24h to tolerate the nightly schedule jitter
            Add-Result "Recent backup snapshot" $ok "$($snapshot.Name), $([math]::Round($age.TotalHours, 1))h old"
        } else {
            Add-Result "Recent backup snapshot" $false "No workspace-*.db/.dump found in $BackupDest"
        }
    } else {
        Add-Result "Recent backup snapshot" $false "Destination not reachable: $BackupDest"
    }
} else {
    Add-Result "Recent backup snapshot" $null "Skipped (-BackupDest not provided)"
}

# 5 — power plan / sleep settings
$activeScheme = (powercfg /getactivescheme) 2>$null
$isHighPerf = $activeScheme -match "High performance"
Add-Result "Power plan = High Performance" $isHighPerf "$activeScheme"

$standbyAc = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null) -join "`n"
$standbyOff = $standbyAc -match "Current AC Power Setting Index:\s*0x00000000"
Add-Result "Sleep disabled (AC)" $standbyOff "See 'powercfg /query' output for detail if this fails"

# --- Report ---
Write-Host ""
Write-Host "Resilience verification — $(Get-Date)" -ForegroundColor Cyan
Write-Host "================================================"
$failCount = 0
foreach ($r in $results) {
    if ($null -eq $r.Ok) {
        $mark = "-"; $color = "Gray"
    } elseif ($r.Ok) {
        $mark = "OK"; $color = "Green"
    } else {
        $mark = "FAIL"; $color = "Red"; $failCount++
    }
    Write-Host ("[{0,-4}] {1,-28} {2}" -f $mark, $r.Check, $r.Detail) -ForegroundColor $color
}
Write-Host "================================================"
if ($failCount -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
} else {
    Write-Host "$failCount check(s) FAILED — see .devnotes/deployment-hardening/06_RESILIENCE_PLAN.md P1." -ForegroundColor Yellow
}
exit $failCount
