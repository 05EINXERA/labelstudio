#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register a Windows Task Scheduler job that watches for annotation wipes.

.DESCRIPTION
    Creates a scheduled task called "AnnotationWipeWatch" that runs
    scripts/watch_annotation_wipes.py on a repeating interval, writing
    $DATA_DIR/logs/last_annotation_watch_status.json each time.

    Catches a task's annotation count dropping unexpectedly — the failure
    mode behind the 2026-08-23 pool-exhaustion incident, which took days to
    notice because nothing was watching for it. This is intentionally minimal
    (06_RESILIENCE_PLAN.md P7) — it does not page or message anyone. It exists
    so a FAIL is visible to whoever looks, within minutes of it happening,
    rather than only discoverable by an annotator reporting lost work later.

    Run once as Administrator from the repo root:
        .\scripts\schedule-annotation-watch.ps1

    To remove the task later:
        Unregister-ScheduledTask -TaskName "AnnotationWipeWatch" -Confirm:$false

.PARAMETER IntervalMinutes
    How often to check (default: 10).

.PARAMETER MinDrop
    Flag a task whose annotation count drops by at least this much (default 10).

.EXAMPLE
    .\scripts\schedule-annotation-watch.ps1 -IntervalMinutes 5
#>

param(
    [int]$IntervalMinutes = 10,
    [int]$MinDrop = 10
)

$ErrorActionPreference = "Stop"

$repoRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script    = Join-Path $repoRoot "scripts\watch_annotation_wipes.py"
$venvPython = Join-Path $repoRoot "venv\Scripts\python.exe"

if (-not (Test-Path $script)) {
    Write-Error "watch_annotation_wipes.py not found at $script"
    exit 1
}
if (-not (Test-Path $venvPython)) {
    Write-Error "venv Python not found at $venvPython"
    exit 1
}

$taskName = "AnnotationWipeWatch"
$args     = "`"$script`" --min-drop $MinDrop"
$action   = New-ScheduledTaskAction -Execute $venvPython -Argument $args -WorkingDirectory $repoRoot
# Same daily-trigger-plus-repetition workaround as schedule-health-check.ps1:
# -Daily and -RepetitionInterval live in different parameter sets, and an
# indefinite RepetitionDuration is rejected by Task Scheduler, so a throwaway
# -Once trigger supplies the repetition and gets grafted onto a -Daily trigger.
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
    -Description "Check for unexpected annotation-count drops every $IntervalMinutes min" `
    -ErrorAction Stop `
    | Out-Null

if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    Write-Error "Task '$taskName' was not registered."
    exit 1
}

Write-Host "Scheduled task '$taskName' registered — checks every $IntervalMinutes min."
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Test run  :   Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Status file:  `$DATA_DIR\logs\last_annotation_watch_status.json"
