#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Automated setup for Caddy Reverse Proxy & TLS on Windows LAN (P5).

.DESCRIPTION
    Installs and configures Caddy as a reverse proxy for the annotation workspace:
      1. Downloads caddy.exe if not found on PATH or in scripts\caddy\
      2. Validates the Caddyfile configuration
      3. Trusts Caddy's internal certificate authority on the local machine (`caddy trust`)
      4. Exports the local root certificate so annotators can trust it on their devices
      5. Registers a Windows Scheduled Task `AnnotationProxy` that starts Caddy at boot

    Run as Administrator:
        .\scripts\setup-caddy.ps1

    To remove:
        Unregister-ScheduledTask -TaskName "AnnotationProxy" -Confirm:$false
#>

param(
    [string]$Caddyfile = "Caddyfile",
    [switch]$SkipTrust,
    [switch]$StartImmediately
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$caddyDir = Join-Path $repoRoot "scripts\caddy"
$caddyExe = Join-Path $caddyDir "caddy.exe"
$caddyfilePath = Join-Path $repoRoot $Caddyfile

Write-Host "=== Annotation Workspace Caddy Reverse Proxy Setup ===" -ForegroundColor Cyan

# 1. Locate or download Caddy
$caddyCmd = Get-Command caddy -ErrorAction SilentlyContinue
if ($caddyCmd) {
    $caddyBin = $caddyCmd.Source
    Write-Host "✓ Found Caddy on PATH: $caddyBin" -ForegroundColor Green
} elseif (Test-Path $caddyExe) {
    $caddyBin = $caddyExe
    Write-Host "✓ Found Caddy in scripts directory: $caddyBin" -ForegroundColor Green
} else {
    New-Item -ItemType Directory -Force -Path $caddyDir | Out-Null
    Write-Host "Downloading Caddy for Windows (x64)..." -ForegroundColor Yellow
    $downloadUrl = "https://caddyserver.com/api/download?os=windows&arch=amd64"
    $zipPath = Join-Path $caddyDir "caddy.exe"
    
    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing
        $caddyBin = $zipPath
        Write-Host "✓ Caddy downloaded successfully to $caddyBin" -ForegroundColor Green
    } catch {
        Write-Error "Failed to download Caddy automatically. Please download caddy.exe from https://caddyserver.com/download and place it in $caddyDir"
        exit 1
    }
}

# 2. Validate Caddyfile
if (-not (Test-Path $caddyfilePath)) {
    Write-Error "Caddyfile not found at $caddyfilePath"
    exit 1
}

Write-Host "Validating Caddyfile configuration..." -ForegroundColor Cyan
& $caddyBin validate --config $caddyfilePath --adapter caddyfile
if ($LASTEXITCODE -ne 0) {
    Write-Error "Caddyfile validation failed. Please check syntax in $caddyfilePath"
    exit 1
}
Write-Host "✓ Caddyfile syntax is valid" -ForegroundColor Green

# 3. Trust root certificate
if (-not $SkipTrust) {
    Write-Host "Installing Caddy internal Root CA to Windows Certificate Store..." -ForegroundColor Cyan
    try {
        & $caddyBin trust --config $caddyfilePath --adapter caddyfile
        Write-Host "✓ Local Root CA trusted successfully" -ForegroundColor Green
    } catch {
        Write-Warning "Could not automatically trust Caddy CA; you may need to run as Administrator or trust manually."
    }
}

# 4. Locate and copy Root CA cert for client distribution
$localPkiRoot = Join-Path $env:LOCALAPPDATA "Caddy\pki\authorities\local\root.crt"
$roamingPkiRoot = Join-Path $env:APPDATA "Caddy\pki\authorities\local\root.crt"
$certsOutDir = Join-Path $repoRoot "certs"
New-Item -ItemType Directory -Force -Path $certsOutDir | Out-Null

$foundCert = $null
if (Test-Path $localPkiRoot) {
    $foundCert = $localPkiRoot
} elseif (Test-Path $roamingPkiRoot) {
    $foundCert = $roamingPkiRoot
}

if ($foundCert) {
    $destCert = Join-Path $certsOutDir "caddy-lan-root.crt"
    Copy-Item $foundCert $destCert -Force
    Write-Host "✓ Exported LAN root certificate to: $destCert" -ForegroundColor Green
    Write-Host "  -> Distribute this certificate to annotator client PCs to prevent HTTPS browser warnings." -ForegroundColor Gray
}

# 5. Create Windows Scheduled Task for Auto-Start
Write-Host "Registering Caddy Scheduled Task (AnnotationProxy)..." -ForegroundColor Cyan
$taskName = "AnnotationProxy"

$action = New-ScheduledTaskAction `
    -Execute $caddyBin `
    -Argument "run --config `"$caddyfilePath`" --adapter caddyfile" `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -Priority 4

$principal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Reverse Proxy (Caddy) for Annotation Workspace TLS termination" `
        -Force | Out-Null
    Write-Host "✓ Scheduled task '$taskName' registered to start at boot." -ForegroundColor Green
} catch {
    Write-Warning "Could not register scheduled task under SYSTEM (requires Administrator). Registering for current user..."
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Reverse Proxy (Caddy) for Annotation Workspace TLS termination" `
        -Force | Out-Null
    Write-Host "✓ Scheduled task '$taskName' registered for $env:USERNAME." -ForegroundColor Green
}

if ($StartImmediately) {
    Write-Host "Starting '$taskName' task..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2
    $state = (Get-ScheduledTask -TaskName $taskName).State
    Write-Host "✓ Caddy proxy is now: $state" -ForegroundColor Green
}

Write-Host "`nSetup complete! Next steps:" -ForegroundColor Cyan
Write-Host "  1. Set COOKIE_SECURE=1 in .env to enforce secure session cookies." -ForegroundColor White
Write-Host "  2. Access the application over HTTPS: https://localhost (or https://<server-lan-ip>)" -ForegroundColor White
