#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Launch the development instance (isolated from the live LAN deployment).

.DESCRIPTION
    Same shape as run.ps1, with three differences that exist to keep the dev
    instance from ever becoming visible to annotators:

      1. Refuses to start if APP_HOST is not a loopback address. Binding the dev
         instance to 0.0.0.0 would put unfinished code on the LAN, and it is a
         one-character edit away, so the check is enforced rather than documented.

      2. Refuses to start if DATABASE_URL points at the production database.
         The Teams work runs Alembic upgrade/downgrade cycles; pointing those at
         production would drop columns under ~25 live annotators.

      3. Runs with --reload. Safe here precisely because nobody else is served
         by this process. Never add it to run.ps1.

    Applies pending migrations on first launch (the dev database usually starts
    empty), then starts uvicorn.

.PARAMETER EnvFile
    Env file to load. Default: .env in the dev worktree.

.PARAMETER NoReload
    Start without --reload. Useful when profiling, or when an editor's
    save-on-focus-change is causing restart churn.

.PARAMETER SkipMigrations
    Do not run 'alembic upgrade head' before starting. Use when deliberately
    testing a downgraded or partially-migrated schema.

.EXAMPLE
    .\scripts\run-dev.ps1

.EXAMPLE
    .\scripts\run-dev.ps1 -SkipMigrations -NoReload
#>
[CmdletBinding()]
param(
    [string]$EnvFile = ".env",
    [switch]$NoReload,
    [switch]$SkipMigrations
)

$ErrorActionPreference = "Stop"

function Write-Ok   { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Fail { param([string]$m) Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Write-Info { param([string]$m) Write-Host "  $m" -ForegroundColor White }

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $Root

try {
    Write-Host "`nDevelopment instance" -ForegroundColor Cyan
    Write-Info "Tree: $Root"

    # --- load .env ----------------------------------------------------------

    if (-not (Test-Path $EnvFile)) {
        Write-Fail "$EnvFile not found in $Root"
        Write-Info "Run scripts\setup-dev-instance.ps1 from the production tree first."
        exit 1
    }

    foreach ($line in (Get-Content $EnvFile)) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -match '^\s*(\w+)\s*=\s*(.+?)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            Set-Item -Path "env:$key" -Value $value
        }
    }

    # --- isolation guards ---------------------------------------------------
    #
    # These are the whole point of the script. A dev instance that binds the LAN
    # interface or talks to the production database is not a dev instance, and
    # both are a single careless edit away from happening.

    Write-Host "`nIsolation checks:" -ForegroundColor Cyan

    $devHost = $env:APP_HOST
    if ([string]::IsNullOrWhiteSpace($devHost)) { $devHost = "127.0.0.1" }

    # 192.168.110.150 is this box's own LAN address, allowed so the dev instance
    # can be reached from another machine for cross-machine testing. It is a
    # deliberate hole in the loopback rule: on that address the dev instance IS
    # visible to annotators, on a different port to production. Keep it on a
    # non-production port and do not point it at the production database - the
    # DATABASE_URL check below is what actually stops the damaging case.
    # (Was 192.168.1.150, an address from a different network that this machine
    # has never had, so LAN dev runs failed to bind.)
    if ($devHost -notin @("127.0.0.1", "localhost", "192.168.110.150", "::1")) {
        Write-Fail "APP_HOST is '$devHost' - the dev instance must bind loopback only."
        Write-Info ""
        Write-Info "Binding anything else publishes unfinished code to the LAN, where"
        Write-Info "annotators can reach it. Set APP_HOST = `"127.0.0.1`" in $EnvFile."
        Write-Info "If you genuinely need LAN access for cross-machine testing, run the"
        Write-Info "production launcher knowingly - do not weaken this guard."
        exit 1
    }
    Write-Ok "APP_HOST=$devHost (loopback - not reachable from the LAN)"

    $dbUrl = $env:DATABASE_URL
    if ([string]::IsNullOrWhiteSpace($dbUrl)) {
        Write-Fail "DATABASE_URL is not set. Refusing to fall back to the default SQLite path."
        exit 1
    }

    # The production database is whatever the production tree's .env names. Read
    # it rather than hardcoding, so renaming the prod database keeps this honest.
    $prodEnvPath = Join-Path (Split-Path $Root -Parent) "labelstudio\.env"
    if (Test-Path $prodEnvPath) {
        $prodDbLine = Select-String -Path $prodEnvPath -Pattern '^\s*DATABASE_URL\s*=' -ErrorAction SilentlyContinue |
                      Select-Object -First 1
        if ($prodDbLine -and $prodDbLine.Line -match '=\s*"?([^"]+)"?\s*$') {
            $prodDbUrl = $Matches[1].Trim()
            if ($dbUrl -eq $prodDbUrl) {
                Write-Fail "DATABASE_URL points at the PRODUCTION database."
                Write-Info ""
                Write-Info "Development runs Alembic upgrade/downgrade cycles. Against production"
                Write-Info "that drops columns while annotators are working. Point DATABASE_URL at"
                Write-Info "the *_dev database in $EnvFile."
                exit 1
            }
        }
    }

    $dbName = if ($dbUrl -match '/([^/?]+)(\?|$)') { $Matches[1] } else { "(unparsed)" }
    Write-Ok "DATABASE_URL -> $dbName (not production)"

    $dataDir = $env:DATA_DIR
    if ([string]::IsNullOrWhiteSpace($dataDir)) { $dataDir = "." }
    Write-Ok "DATA_DIR=$dataDir"

    if ($env:APP_ENV -eq "production") {
        Write-Host "  [WARN] APP_ENV=production in a dev instance. Intentional?" -ForegroundColor Yellow
    }

    $port = $env:APP_PORT
    if ([string]::IsNullOrWhiteSpace($port)) { $port = "8001" }

    $conflict = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
    if ($conflict) {
        Write-Fail "Port $port is already in use (PID $($conflict[0].OwningProcess))."
        Write-Info "Another dev instance is probably still running. Stop it, or change APP_PORT."
        exit 1
    }
    Write-Ok "Port $port free"

    # --- python -------------------------------------------------------------

    # The dev worktree has no venv of its own: dependencies are identical, and
    # the production venv lives outside the tree so sharing it is safe. It is
    # only ever read.
    $venvPython = Join-Path (Split-Path $Root -Parent) "labelstudio\venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $venvPython = "python"
        Write-Host "  [WARN] Shared venv not found; falling back to 'python' on PATH." -ForegroundColor Yellow
    }

    # --- migrations ---------------------------------------------------------

    if (-not $SkipMigrations) {
        Write-Host "`nApplying migrations..." -ForegroundColor Cyan
        & $venvPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "alembic upgrade head failed. Fix the migration before starting."
            exit 1
        }
        Write-Ok "Schema at head"
    }

    # --- launch -------------------------------------------------------------

    Write-Host "`nStarting uvicorn on http://${devHost}:${port}/" -ForegroundColor Cyan
    if (-not $NoReload) {
        Write-Info "--reload is on (safe: this process serves nobody but you)"
    }
    Write-Host ""

    $uvicornArgs = @("-m", "uvicorn", "main:app", "--host", $devHost, "--port", $port)
    if (-not $NoReload) {
        # Watch only application code. Without --reload-dir, uvicorn also watches
        # DATA_DIR and node_modules-sized trees and restarts on every upload.
        $uvicornArgs += @("--reload", "--reload-dir", "api", "--reload-dir", "frontend", "--reload-dir", "formats")
    }

    & $venvPython @uvicornArgs
}
finally {
    Pop-Location
}
