#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Launch the annotation app with environment configuration from .env

.DESCRIPTION
    Reads the .env file, sets each variable as an environment variable, and
    starts uvicorn. If a required variable is missing or invalid (in production),
    the app startup will fail with a descriptive error before the process binds.

.EXAMPLE
    # Default: read .env
    .\scripts\run.ps1

    # Override a single variable (e.g., for testing)
    $env:CORS_ORIGINS = "http://localhost:8000"; .\scripts\run.ps1
#>

param(
    [string]$EnvFile = ".env",
    [switch]$NoValidate
)

# Load .env file into the current session
function Load-EnvFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        Write-Error "Error: $Path not found. Copy from the DEPLOYMENT_GUIDE.md or create it."
        exit 1
    }

    $env_content = Get-Content $Path
    foreach ($line in $env_content) {
        $line = $line.Trim()

        # Skip empty lines and comments
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }

        # Parse KEY = VALUE (allowing spaces around =)
        if ($line -match '^\s*(\w+)\s*=\s*(.+)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim()

            # Remove surrounding quotes if present
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or `
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }

            Set-Item -Path "env:$key" -Value $value
            Write-Host "✓ $key=$($value.Substring(0, [Math]::Min($value.Length, 30)))" -ForegroundColor Green
        }
    }
}

# Load the .env file
Write-Host "Loading environment from $EnvFile..." -ForegroundColor Cyan
Load-EnvFile $EnvFile

# Show the active configuration
Write-Host "`nActive configuration:" -ForegroundColor Cyan
Write-Host "  APP_ENV: $($env:APP_ENV ?? 'development')" -ForegroundColor White
Write-Host "  APP_HOST: $($env:APP_HOST ?? '127.0.0.1')" -ForegroundColor White
Write-Host "  APP_PORT: $($env:APP_PORT ?? '8765')" -ForegroundColor White
Write-Host "  CORS_ORIGINS: $($env:CORS_ORIGINS ?? '(none)' )" -ForegroundColor White
Write-Host "  ALLOW_REGISTRATION: $($env:ALLOW_REGISTRATION ?? '1')" -ForegroundColor White

# Remind about JWT_SECRET
if ($env:APP_ENV -eq "production") {
    if ([string]::IsNullOrWhiteSpace($env:JWT_SECRET) -or $env:JWT_SECRET -eq "<64-hex-chars>") {
        Write-Host "`n⚠️  WARNING: JWT_SECRET not set properly. Startup will fail." -ForegroundColor Yellow
    } elseif ($env:JWT_SECRET.Length -lt 32) {
        Write-Host "`n⚠️  WARNING: JWT_SECRET is too short (< 32 chars). Startup will fail." -ForegroundColor Yellow
    }
}

Write-Host "`nStarting uvicorn (single-worker process, AnyIO threadpool enabled)...`n" -ForegroundColor Cyan

# Run the app in single-worker mode to unify ML model singletons and embedding cache
& python -m uvicorn main:app --host $env:APP_HOST --port $env:APP_PORT --workers 1
