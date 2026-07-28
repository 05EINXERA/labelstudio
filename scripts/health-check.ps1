#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Poll GET /health and record the result to a small status file.

.DESCRIPTION
    Minimal visibility for a 20-25 person deployment with no existing
    alerting infrastructure (06_RESILIENCE_PLAN.md P7) — this does not page
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
} catch {
    $ok = $false
    $detail = "request failed: $($_.Exception.Message)"
}

$payload = [ordered]@{
    timestamp = $timestamp
    ok        = $ok
    detail    = $detail
}
$payload | ConvertTo-Json | Set-Content -Path $statusPath -Encoding utf8

if ($ok) {
    Write-Host "[OK] $timestamp $detail"
    exit 0
} else {
    Write-Host "[FAIL] $timestamp $detail"
    exit 1
}
