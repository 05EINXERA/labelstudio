#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Poll GET /health and record the result to a small status file.

.DESCRIPTION
    Minimal visibility for a 20-25 person deployment with no existing
    alerting infrastructure (06_RESILIENCE_PLAN.md P7) - this does not page
    anyone or send a webhook. It writes last_health_status.json next to the
    app's logs so a human can glance at it (or a future script can), the
    same low-tech pattern backup.py uses for last_backup_status.json.

    Intended to run on a schedule via Task Scheduler (see
    scripts/schedule-health-check.ps1) or manually to spot-check.

.PARAMETER Url
    The health endpoint to poll (default: http://127.0.0.1:8000/health).

.EXAMPLE
    .\scripts\health-check.ps1
    .\scripts\health-check.ps1 -Url "http://127.0.0.1:8080/health"
#>

param(
    [string]$Url = "http://127.0.0.1:8000/health"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $repoRoot ".env"
$dataDir = $null
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^\s*DATA_DIR\s*=' } | Select-Object -First 1
    if ($line) { $dataDir = ($line -split '=', 2)[1].Trim().Trim('"') }
}
if (-not $dataDir) { $dataDir = Join-Path $repoRoot "data" }

$logsDir = Join-Path $dataDir "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$statusPath = Join-Path $logsDir "last_health_status.json"

$timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
try {
    $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 10
    $ok = $response.status -eq "ok"
    $detail = "status=$($response.status) database=$($response.database)"

    # Attendance drain, reported separately from $ok on purpose.
    #
    # A dead drain does not stop the app serving annotation work, so it must
    # not look like an outage or prompt a restart of a healthy process. But it
    # is invisible everywhere else: /health would say "ok" and the database
    # "up" while observations piled up in memory and were lost on the next
    # restart, with the day's attendance simply missing afterwards. Same
    # reasoning as the destructive-save scan below - the failure is silent by
    # nature, so reading it on every health check bounds how long it can go
    # unseen.
    $att = $response.attendance
    if ($att -and $att.enabled -eq $true) {
        $detail += " attendance=$(if ($att.healthy) { 'ok' } else { 'DEGRADED' })"
        $detail += " drain=$($att.drain_running) buffered=$($att.buffered)"
        if (-not $att.healthy) {
            Write-Warning "Attendance is not being written: drain_running=$($att.drain_running), buffered=$($att.buffered) rows. Observations are accumulating in memory and will be LOST if the app restarts. Restart the app to recover the drain."
        }
        if ($att.buffered -gt ($att.buffer_max * 0.5)) {
            Write-Warning "Attendance buffer is over half full ($($att.buffered)/$($att.buffer_max)). The oldest observations are dropped at the cap."
        }
    }
} catch {
    $ok = $false
    $detail = "request failed: $($_.Exception.Message)"
}

# --- Destructive-save scan (wipe-guard-bypass-fix, S2) -----------------------
#
# The server writes a WARN `event=task.save.destructive` line for any save that
# drops a large share of a task's annotations, and a `task.save.refused_clear`
# line whenever the clear-guard stops an empty payload. Neither is an error:
# the first is not refused at all, and the second means a guard did its job.
#
# They are surfaced here because the failure they describe is silent by nature.
# Task 691 lost 1437 annotations and the number sat in the log for a full day
# before a human noticed it in a browser. Reading the day's counts on every
# health check turns "found eventually" into "found today", which is the whole
# value: it bounds how long the next one goes unseen.
$logDir = $null
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^\s*LOG_DIR\s*=' } | Select-Object -First 1
    if ($line) { $logDir = ($line -split '=', 2)[1].Trim().Trim('"') }
}
if (-not $logDir) { $logDir = $logsDir }

$today = (Get-Date).ToString("yyyy-MM-dd")
$postLog = Join-Path $logDir "service/$today/POST.log"
$destructive = 0
$refusedClear = 0
$destructiveLines = @()
if (Test-Path $postLog) {
    # -Raw would load a multi-hundred-MB day into memory; Select-String streams.
    $hits = Select-String -Path $postLog -Pattern 'event=task\.save\.destructive' -ErrorAction SilentlyContinue
    if ($hits) {
        $destructive = @($hits).Count
        # The last few are the useful ones for a glance; the file has the rest.
        $destructiveLines = @($hits | Select-Object -Last 5 | ForEach-Object { $_.Line })
    }
    $refused = Select-String -Path $postLog -Pattern 'event=task\.save\.refused_clear' -ErrorAction SilentlyContinue
    if ($refused) { $refusedClear = @($refused).Count }
}

$payload = [ordered]@{
    timestamp              = $timestamp
    ok                     = $ok
    detail                 = $detail
    destructive_saves      = $destructive
    refused_clear_saves    = $refusedClear
    destructive_recent     = $destructiveLines
}
$payload | ConvertTo-Json -Depth 3 | Set-Content -Path $statusPath -Encoding utf8

if ($destructive -gt 0) {
    Write-Host "[WARN] $timestamp $destructive destructive save(s) today - see $postLog"
    foreach ($l in $destructiveLines) { Write-Host "       $l" }
}
if ($refusedClear -gt 0) {
    Write-Host "[INFO] $timestamp $refusedClear empty save(s) refused by the clear-guard today"
}

if ($ok) {
    Write-Host "[OK] $timestamp $detail"
    exit 0
} else {
    Write-Host "[FAIL] $timestamp $detail"
    exit 1
}
