#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Create an isolated development instance alongside the live LAN deployment.

.DESCRIPTION
    The production server runs uvicorn out of this very working tree and serves
    frontend/ straight off disk via StaticFiles. Editing files here is therefore
    editing the running deployment: a saved .js reaches the next annotator who
    reloads, and `git checkout` repoints the live server's source.

    This script builds a second, fully isolated instance so development stops
    touching production. Four boundaries, each of which matters on its own:

      1. Filesystem  — a separate git worktree (shared .git, separate files)
      2. Database    — a separate Postgres database on the same server
      3. DATA_DIR    — a separate uploads/logs directory
      4. Network     — port 8001 bound to 127.0.0.1, invisible to the LAN

    The database boundary is the sharp one. The Teams feature adds four Alembic
    migrations, and .devnotes/teams/TASKS.md T1.3 asks for an upgrade/downgrade
    cycle. Run that against the production database and you are dropping columns
    while ~25 people annotate. A separate database makes it a no-op.

    IDEMPOTENT: safe to re-run. Every step checks for existing state first and
    skips it. Nothing is ever overwritten or deleted.

    READ-ONLY towards production: this script never writes inside the live tree,
    never touches the production database, and never restarts the server. The
    only thing it reads from production is .env, as a template.

.PARAMETER Branch
    Branch to check out in the dev worktree. Default: feat/teams

.PARAMETER DevPort
    Port for the dev instance. Default: 8001

.PARAMETER SeedFromProd
    Copy the production database and uploads into the dev instance, so you are
    developing against realistic data volumes rather than empty tables. Uses
    pg_dump (an online, non-blocking read) and a file copy. Off by default
    because it can take a while on a large uploads/ directory.

.EXAMPLE
    .\scripts\setup-dev-instance.ps1

.EXAMPLE
    .\scripts\setup-dev-instance.ps1 -SeedFromProd

.NOTES
    Run from the repository root. Requires psql/createdb on PATH (PostgreSQL
    bin directory) and git >= 2.5 for worktree support.
#>
[CmdletBinding()]
param(
    [string]$Branch = "fix/worktree",
    [int]$DevPort = 8001,
    [switch]$SeedFromProd
)

$ErrorActionPreference = "Stop"

# --- output helpers ---------------------------------------------------------

function Write-Step { param([string]$m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Fail { param([string]$m) Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Write-Skip { param([string]$m) Write-Host "  [SKIP] $m" -ForegroundColor DarkGray }
function Write-Warn { param([string]$m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Info { param([string]$m) Write-Host "  $m" -ForegroundColor White }

# --- locate the production tree ---------------------------------------------

$ProdRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path (Join-Path $ProdRoot "main.py"))) {
    Write-Error "Not a repository root: $ProdRoot (main.py not found). Run from the repo."
    exit 1
}

$ParentDir = Split-Path $ProdRoot -Parent
$DevRoot = Join-Path $ParentDir "labelstudio-dev"
$ProdEnv = Join-Path $ProdRoot ".env"
$DevEnvOut = Join-Path $DevRoot ".env"

Write-Host "Isolated dev instance setup" -ForegroundColor Cyan
Write-Info "Production tree : $ProdRoot   (LIVE - will not be modified)"
Write-Info "Dev worktree    : $DevRoot"
Write-Info "Branch          : $Branch"
Write-Info "Dev port        : $DevPort (bound to 127.0.0.1)"

# --- 0 · preflight ----------------------------------------------------------

Write-Step "0. Preflight"

foreach ($tool in @("git", "createdb", "psql")) {
    $found = Get-Command $tool -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Error "'$tool' is not on PATH. Add the PostgreSQL bin directory (e.g. C:\Program Files\PostgreSQL\17\bin) and re-run."
        exit 1
    }
    Write-Ok "$tool found"
}

if (-not (Test-Path $ProdEnv)) {
    Write-Error "Production .env not found at $ProdEnv. It is used as the template for the dev .env."
    exit 1
}
Write-Ok ".env template found"

# Parse the production .env. Same KEY = VALUE parsing as run.ps1, so the two
# agree about quoting rules.
$prodSettings = @{}
foreach ($line in (Get-Content $ProdEnv)) {
    $trimmed = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) { continue }
    if ($trimmed -match '^\s*(\w+)\s*=\s*(.+?)\s*$') {
        $key = $Matches[1]
        $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $prodSettings[$key] = $value
    }
}

$prodDbUrl = $prodSettings["DATABASE_URL"]
if ([string]::IsNullOrWhiteSpace($prodDbUrl) -or -not $prodDbUrl.StartsWith("postgresql")) {
    Write-Error "Production DATABASE_URL is not Postgres ('$prodDbUrl'). This script assumes the LAN Postgres deployment."
    exit 1
}

# postgresql+psycopg://user:pass@host:port/dbname
if ($prodDbUrl -notmatch '^postgresql(\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)$') {
    Write-Error "Could not parse DATABASE_URL. Expected postgresql+psycopg://user:pass@host:port/dbname"
    exit 1
}
$PgUser = $Matches[2]
$PgPass = $Matches[3]
$PgHost = $Matches[4]
$PgPort = $Matches[5]
$ProdDb = $Matches[6]
$DevDb = "${ProdDb}_dev"

Write-Ok "Postgres $PgHost`:$PgPort  prod db '$ProdDb'  ->  dev db '$DevDb'"

$prodDataDir = $prodSettings["DATA_DIR"]
if ([string]::IsNullOrWhiteSpace($prodDataDir)) { $prodDataDir = "." }
$DevDataDir = "c:/annotation-data-dev"
Write-Ok "DATA_DIR '$prodDataDir'  ->  '$DevDataDir'"

# Guard: refuse to proceed if the dev port is already taken by something else.
$portInUse = Get-NetTCPConnection -LocalPort $DevPort -State Listen -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Warn "Port $DevPort is already listening (PID $($portInUse[0].OwningProcess))."
    Write-Warn "That may be a dev instance you already started. Stop it, or pass -DevPort with a free port."
}

# --- 1 · git worktree -------------------------------------------------------

Write-Step "1. Git worktree (filesystem isolation)"

Push-Location $ProdRoot
try {
    $worktrees = git worktree list --porcelain 2>&1 | Out-String

    if (Test-Path $DevRoot) {
        if ($worktrees -match [regex]::Escape($DevRoot.Replace('\', '/'))) {
            Write-Skip "Worktree already registered at $DevRoot"
        }
        else {
            Write-Error "$DevRoot exists but is not a registered git worktree. Move or remove it, then re-run."
            exit 1
        }
    }
    else {
        # A branch checked out in another worktree cannot be checked out again.
        # The production tree is on $Branch right now, so create the dev worktree
        # on a dedicated branch that tracks it instead of fighting over the name.
        $currentProdBranch = (git rev-parse --abbrev-ref HEAD).Trim()

        if ($currentProdBranch -eq $Branch) {
            $devBranch = "$Branch-dev"
            Write-Info "Production tree already has '$Branch' checked out."
            Write-Info "Creating dev worktree on '$devBranch' (branched from '$Branch')."

            $existing = git branch --list $devBranch
            if ($existing) {
                $gitArgs = @("worktree", "add", "$DevRoot", $devBranch)
            }
            else {
                $gitArgs = @("worktree", "add", "-b", $devBranch, "$DevRoot", $Branch)
            }
        }
        else {
            $gitArgs = @("worktree", "add", "$DevRoot", $Branch)
        }

        # git writes progress ("Preparing worktree...") to stderr even on success.
        # With $ErrorActionPreference='Stop', piping that through 2>&1 makes
        # PowerShell treat a successful command as a terminating error, so the
        # stream is redirected to a file and the exit code is the only verdict.
        $gitLog = Join-Path $env:TEMP "worktree-add-$PID.log"
        $proc = Start-Process -FilePath "git" -ArgumentList $gitArgs `
            -WorkingDirectory $ProdRoot -NoNewWindow -Wait -PassThru `
            -RedirectStandardError $gitLog -RedirectStandardOutput "$gitLog.out"

        if ($proc.ExitCode -ne 0) {
            Write-Fail "git worktree add failed. The production tree was NOT modified."
            if (Test-Path $gitLog) { Get-Content $gitLog | ForEach-Object { Write-Info $_ } }
            exit 1
        }
        Remove-Item $gitLog, "$gitLog.out" -ErrorAction SilentlyContinue
        Write-Ok "Worktree created at $DevRoot"
    }
}
finally {
    Pop-Location
}

$devBranchActual = (git -C "$DevRoot" rev-parse --abbrev-ref HEAD).Trim()
Write-Ok "Dev worktree is on branch '$devBranchActual'"

# --- 2 · dev database -------------------------------------------------------

Write-Step "2. Postgres database (data isolation)"

$env:PGPASSWORD = $PgPass
try {
    # No 2>&1 anywhere in this block: with $ErrorActionPreference='Stop' it turns
    # a native tool's harmless stderr chatter into a terminating error.
    $exists = & psql -h $PgHost -p $PgPort -U $PgUser -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname='$DevDb'"

    if ("$exists".Trim() -eq "1") {
        Write-Skip "Database '$DevDb' already exists"
    }
    else {
        & createdb -h $PgHost -p $PgPort -U $PgUser $DevDb
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "createdb failed for '$DevDb'. Production database '$ProdDb' untouched."
            exit 1
        }
        Write-Ok "Created database '$DevDb'"
    }
}
finally {
    Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue
}

# --- 3 · dev DATA_DIR -------------------------------------------------------

Write-Step "3. DATA_DIR (uploads/logs isolation)"

foreach ($sub in @("", "uploads", "logs")) {
    $path = if ($sub) { Join-Path $DevDataDir $sub } else { $DevDataDir }
    if (Test-Path $path) { Write-Skip "$path exists" }
    else {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        Write-Ok "Created $path"
    }
}

# --- 4 · dev .env -----------------------------------------------------------

Write-Step "4. Dev .env"

if (Test-Path $DevEnvOut) {
    Write-Skip ".env already exists at $DevEnvOut (not overwritten)"
}
else {
    # Start from the production .env so every setting not listed below is
    # inherited verbatim, then override exactly the isolation-critical keys.
    # Rewriting rather than regenerating keeps the file's comments intact.
    $overrides = @{
        "APP_ENV"            = "development"
        "APP_HOST"           = "127.0.0.1"
        "APP_PORT"           = "$DevPort"
        "DATABASE_URL"       = "postgresql+psycopg://${PgUser}:${PgPass}@${PgHost}:${PgPort}/${DevDb}"
        "DATA_DIR"           = $DevDataDir
        "CORS_ORIGINS"       = ""
        "ALLOW_REGISTRATION" = "1"
    }

    $seen = @{}
    $out = New-Object System.Collections.Generic.List[string]

    $out.Add("# =========================================================================")
    $out.Add("# DEVELOPMENT INSTANCE - generated by scripts/setup-dev-instance.ps1")
    $out.Add("#")
    $out.Add("# Isolated from the live LAN deployment on four axes:")
    $out.Add("#   filesystem : this git worktree, separate from the production tree")
    $out.Add("#   database   : $DevDb (production is $ProdDb)")
    $out.Add("#   DATA_DIR   : $DevDataDir")
    $out.Add("#   network    : 127.0.0.1:$DevPort - NOT reachable from the LAN")
    $out.Add("#")
    $out.Add("# APP_HOST is deliberately 127.0.0.1. Do not change it to 0.0.0.0:")
    $out.Add("# that is the one edit that would expose unfinished code to annotators.")
    $out.Add("# =========================================================================")
    $out.Add("")

    foreach ($line in (Get-Content $ProdEnv)) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\s*(\w+)\s*=\s*(.+?)\s*$' -and -not $trimmed.StartsWith("#")) {
            $key = $Matches[1]
            if ($overrides.ContainsKey($key)) {
                $out.Add("$key = `"$($overrides[$key])`"")
                $seen[$key] = $true
                continue
            }
        }
        $out.Add($line)
    }

    # Any override that had no line in the production .env still has to land.
    foreach ($key in $overrides.Keys) {
        if (-not $seen.ContainsKey($key)) {
            $out.Add("$key = `"$($overrides[$key])`"")
        }
    }

    Set-Content -Path $DevEnvOut -Value $out -Encoding UTF8
    Write-Ok "Wrote $DevEnvOut"
    Write-Info "  APP_ENV=development  APP_HOST=127.0.0.1  APP_PORT=$DevPort"
    Write-Info "  DATABASE_URL -> $DevDb"
    Write-Info "  DATA_DIR     -> $DevDataDir"
}

# --- 5 · seed from production (optional) ------------------------------------

if ($SeedFromProd) {
    Write-Step "5. Seed from production (read-only against prod)"

    Write-Info "pg_dump is an online read; it does not lock or block the live server."

    $dumpFile = Join-Path $env:TEMP "annotation-seed-$(Get-Date -Format 'yyyyMMdd-HHmmss').dump"
    $env:PGPASSWORD = $PgPass
    $dumpLog = Join-Path $env:TEMP "seed-$PID.log"
    try {
        $dp = Start-Process -FilePath "pg_dump" -NoNewWindow -Wait -PassThru `
            -ArgumentList @("-h", $PgHost, "-p", $PgPort, "-U", $PgUser, "-Fc", "-f", $dumpFile, $ProdDb) `
            -RedirectStandardError $dumpLog
        if ($dp.ExitCode -ne 0) {
            Write-Warn "pg_dump failed; skipping seed. Dev database remains empty."
            if (Test-Path $dumpLog) { Get-Content $dumpLog | Select-Object -First 5 | ForEach-Object { Write-Info $_ } }
        }
        else {
            Write-Ok "Dumped '$ProdDb' ($([math]::Round((Get-Item $dumpFile).Length / 1MB, 1)) MB)"
            # --clean so a re-run replaces rather than collides. Restore noise on
            # an empty target is normal and non-fatal, hence the tolerated exit.
            $rp = Start-Process -FilePath "pg_restore" -NoNewWindow -Wait -PassThru `
                -ArgumentList @("-h", $PgHost, "-p", $PgPort, "-U", $PgUser, "-d", $DevDb,
                "--clean", "--if-exists", "--no-owner", $dumpFile) `
                -RedirectStandardError "$dumpLog.restore"
            if ($rp.ExitCode -ne 0) {
                Write-Warn "pg_restore reported errors (often harmless on an empty target)."
            }
            Write-Ok "Restored into '$DevDb'"
            Remove-Item $dumpFile -ErrorAction SilentlyContinue
        }
    }
    finally {
        Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue
        Remove-Item $dumpLog, "$dumpLog.restore" -ErrorAction SilentlyContinue
    }

    $prodUploads = Join-Path $prodDataDir "uploads"
    $devUploads = Join-Path $DevDataDir "uploads"
    if (Test-Path $prodUploads) {
        Write-Info "Mirroring uploads (this can take a while)..."
        # /E copies subdirectories but never deletes; /MIR would delete from the
        # destination and is not worth the risk of a mistyped path.
        # robocopy uses exit codes 0-7 for success (bit flags), 8+ for failure,
        # so it is launched out-of-band rather than being let near $LASTEXITCODE.
        $rc = Start-Process -FilePath "robocopy" -NoNewWindow -Wait -PassThru `
            -ArgumentList @($prodUploads, $devUploads, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
        if ($rc.ExitCode -ge 8) {
            Write-Warn "robocopy reported failures (exit $($rc.ExitCode)); some images may be missing."
        }
        $count = (Get-ChildItem $devUploads -File -ErrorAction SilentlyContinue).Count
        Write-Ok "Uploads mirrored ($count files)"
    }
    else {
        Write-Warn "Production uploads not found at $prodUploads; task images will 404 in dev."
    }
}
else {
    Write-Step "5. Seed from production - SKIPPED"
    Write-Info "Dev database is empty. Re-run with -SeedFromProd to copy real data,"
    Write-Info "or create a user and work from scratch:"
    Write-Info "  cd $DevRoot; .\scripts\run-dev.ps1   (then register at the login page)"
}

# --- 6 · alembic ------------------------------------------------------------

Write-Step "6. Database schema"

if (-not $SeedFromProd) {
    Write-Info "Empty dev database. Build the schema with:"
    Write-Info "  cd $DevRoot"
    Write-Info "  `$env:DATABASE_URL='postgresql+psycopg://${PgUser}:***@${PgHost}:${PgPort}/${DevDb}'"
    Write-Info "  ..\labelstudio\venv\Scripts\python.exe -m alembic upgrade head"
    Write-Info ""
    Write-Info "run-dev.ps1 does this for you on first launch."
}
else {
    Write-Info "Seeded from production, so the schema is already at production's revision."
    Write-Info "Apply new migrations (e.g. the Teams chain) with 'alembic upgrade head' in the dev tree."
}

# --- done -------------------------------------------------------------------

Write-Step "Done"
Write-Host @"

  Production   $ProdRoot
               http://$($prodSettings['APP_HOST']):$($prodSettings['APP_PORT'])/  -- LIVE, untouched by this script

  Development  $DevRoot
               http://127.0.0.1:$DevPort/  -- local only, invisible to the LAN

  Next:
    cd $DevRoot
    .\scripts\run-dev.ps1

  From now on, do all Teams work in the dev worktree. The production tree is
  for deploys only: git pull, alembic upgrade head, restart the service.
  See docs/DEV_INSTANCE.md.

"@ -ForegroundColor Cyan
