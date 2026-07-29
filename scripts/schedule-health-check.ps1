#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register a Windows Task Scheduler job that polls /health every N minutes.

.DESCRIPTION
    Creates a scheduled task called "AnnotationHealthCheck" that runs
    scripts/health-check.ps1 on a repeating interval, writing
    $DATA_DIR/logs/last_health_status.json each time.

    This is intentionally minimal (06_RESILIENCE_PLAN.md P7) — it does not
    page or message anyone. It exists so a FAIL is visible to whoever looks,
    rather than only discoverable by an annotator complaining the app is down.

    Run once as Administrator from the repo root:
        .\scripts\schedule-health-check.ps1

    To remove the task later:
        Unregister-ScheduledTask -TaskName "AnnotationHealthCheck" -Confirm:$false

.PARAMETER Url
    The health endpoint to poll (default: http://127.0.0.1:8000/health).

.PARAMETER IntervalMinutes
    How often to poll (default: 15).

.EXAMPLE
    .\scripts\schedule-health-check.ps1 -IntervalMinutes 5
#>

param(
    [string]$Url = "http://127.0.0.1:8000/health",
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script   = Join-Path $repoRoot "scripts\health-check.ps1"

if (-not (Test-Path $script)) {
    Write-Error "health-check.ps1 not found at $script"
    exit 1
}

$taskName = "AnnotationHealthCheck"
$pwshExe  = (Get-Process -Id $PID).Path  # pwsh.exe running this script
$args     = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Url `"$Url`""
$action   = New-ScheduledTaskAction -Execute $pwshExe -Argument $args -WorkingDirectory $repoRoot
# Daily trigger whose repetition re-arms every 24h, rather than -Once with an
# indefinite RepetitionDuration: [TimeSpan]::MaxValue serialises to
# P99999999DT23H59M59S, which Task Scheduler rejects as out of range on current
# Windows builds ([TimeSpan]::Zero is rejected the same way).
#
# -Daily and -RepetitionInterval live in different parameter sets, so the
# repetition is built on a throwaway -Once trigger and grafted on. This is the
# documented workaround; the resulting values are PT15M / P1D.
$trigger  = New-ScheduledTaskTrigger -Daily -At (Get-Date)
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable

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
    -Description "Poll /health every $IntervalMinutes min and record status" `
    -ErrorAction Stop `
    | Out-Null

if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    Write-Error "Task '$taskName' was not registered."
    exit 1
}

Write-Host "Scheduled task '$taskName' registered — polls every $IntervalMinutes min."
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Test run  :   Start-ScheduledTask -TaskName '$taskName'"
