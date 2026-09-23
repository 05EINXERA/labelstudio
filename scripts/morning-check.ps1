#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Morning review of the app and the attendance feature for a given day.

.DESCRIPTION
    Written for the morning after the 2026-09-23 attendance rollout: the box
    starts the app at 07:00 but nobody is at it until ~10:00, so the first
    three hours are unobserved. This reads what was recorded in that window
    and prints a verdict, rather than leaving the operator to grep four log
    files by hand.

    READ-ONLY. It touches no service, writes nothing outside -OutFile, and
    runs fine from a normal (non-elevated) shell. The app's own Scheduled
    Task is invisible from such a shell, so the script probes /health over
    HTTP and reads the database instead of asking Task Scheduler.

    What it checks, in the order that matters:

      1. /health right now -- including the attendance drain block. A dead
         drain is the one failure that is otherwise silent: /health still
         says "ok" and the database "up" while observations pile up in
         memory and are LOST on the next restart.
      2. The health-check task's own last verdict, which covers the window
         before anyone was watching.
      3. Attendance capture actually reaching Postgres -- rows, distinct
         users, first/last observation, and whether the 19:00 rollup ran.
      4. Errors and warnings in app.log and the service errors.log.
      5. Destructive saves (a save that wipes most of a task). Pre-existing
         concern, not an attendance one, but it is the highest-cost thing in
         these logs and belongs in a morning read.
      6. Startup banner lines, to confirm the 07:00 start was clean.

.PARAMETER Date
    Local date to review, yyyy-MM-dd. Defaults to today.

.PARAMETER Since
    Only report log lines at or after this local time (HH:mm). Default 00:00,
    i.e. the whole day. Pass -Since 07:00 to look only at the unobserved
    morning window.

.PARAMETER OutFile
    Also write the full report here, so it can be pasted or handed to Claude
    for analysis.

.PARAMETER Url
    Base URL of the running app. Default http://localhost:8000

.EXAMPLE
    .\scripts\morning-check.ps1
    .\scripts\morning-check.ps1 -Since 07:00
    .\scripts\morning-check.ps1 -Date 2026-09-24 -OutFile C:\temp\morning.txt
#>
[CmdletBinding()]
param(
    [string]$Date    = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$Since   = '00:00',
    [string]$OutFile,
    [string]$Url     = 'http://localhost:8000'
)

$ErrorActionPreference = 'Continue'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python   = Join-Path $repoRoot 'venv\Scripts\python.exe'

# Collected so the whole report can be echoed and written in one pass.
$script:Lines    = @()
$script:Problems = @()
$script:Warnings = @()

function Emit    { param([string]$t = '') $script:Lines += $t; Write-Host $t }
function Section { param([string]$t) Emit ''; Emit ("=" * 72); Emit "  $t"; Emit ("=" * 72) }
function Problem { param([string]$t) $script:Problems += $t; Emit "  [PROBLEM] $t" }
function Warn    { param([string]$t) $script:Warnings += $t; Emit "  [WARN]    $t" }
function Ok      { param([string]$t) Emit "  [ok]      $t" }

# The cutoff as a real DateTime, so log lines can be compared rather than
# string-matched (a string match on "07:" would also catch 17:).
try {
    $cutoff = [datetime]::ParseExact("$Date $Since", 'yyyy-MM-dd HH:mm', $null)
} catch {
    Write-Error "Could not parse -Date '$Date' / -Since '$Since' (want yyyy-MM-dd and HH:mm)."
    exit 2
}

Emit "Morning check for $Date (from $Since) -- generated $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Emit "Repo: $repoRoot"

# --------------------------------------------------------------------------
Section '1. Live health'
# --------------------------------------------------------------------------
$health = $null
try {
    $health = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 15
} catch {
    Problem "Cannot reach $Url/health -- the app may be down. ($($_.Exception.Message))"
}

if ($health) {
    Emit "  status=$($health.status) database=$($health.database) environment=$($health.environment)"
    if ($health.status -ne 'ok')      { Problem "status is '$($health.status)', expected 'ok'." }
    if ($health.database -ne 'up')    { Problem "database is '$($health.database)', expected 'up'." }

    $att = $health.attendance
    if (-not $att) {
        Problem "No 'attendance' block in /health -- the app is running PRE-ROLLOUT code. Was it restarted?"
    } elseif (-not $att.enabled) {
        Warn "Attendance is DISABLED (ATTENDANCE_ENABLED is not true in .env)."
    } else {
        Emit "  attendance: enabled=$($att.enabled) healthy=$($att.healthy) drain_running=$($att.drain_running) buffered=$($att.buffered)/$($att.buffer_max) last_flush_age=$($att.last_flush_age_seconds)s"
        if (-not $att.healthy -or -not $att.drain_running) {
            Problem "Attendance drain is NOT running. Observations are accumulating in memory and will be LOST on the next restart. Restart the app to recover it."
        } else {
            Ok "Attendance drain healthy."
        }
        if ($att.buffer_max -and $att.buffered -gt ($att.buffer_max * 0.5)) {
            Warn "Attendance buffer over half full ($($att.buffered)/$($att.buffer_max)); oldest rows are dropped at the cap."
        }
        # The drain flushes on a timer; an age far past the flush interval
        # means the timer is wedged even though the thread claims to be alive.
        if ($att.last_flush_age_seconds -ne $null -and $att.last_flush_age_seconds -gt 600) {
            Warn "Last attendance flush was $($att.last_flush_age_seconds)s ago (expected ~60s)."
        }
    }
}

# --------------------------------------------------------------------------
Section '2. Health-check task verdict (covers the unobserved window)'
# --------------------------------------------------------------------------
# Resolve LOG_DIR from config rather than assuming a path: it is a .env
# setting and differs between the dev tree and the deploy box.
$logDir = $null
if (Test-Path $python) {
    $logDir = & $python -c "import config,sys; sys.stdout.write(str(config.LOG_DIR))" 2>$null
}
if (-not $logDir -or -not (Test-Path $logDir)) {
    Warn "Could not resolve LOG_DIR from config.py; log sections will be skipped."
} else {
    Emit "  LOG_DIR: $logDir"
    $statusFile = Join-Path $logDir 'last_health_status.json'
    if (Test-Path $statusFile) {
        try {
            $st = Get-Content $statusFile -Raw | ConvertFrom-Json
            Emit "  last run: $($st.timestamp)  ok=$($st.ok)"
            Emit "  detail  : $($st.detail)"
            if (-not $st.ok) { Problem "The last scheduled health check FAILED: $($st.detail)" }

            # Stale status = the health-check task is not firing at all, which
            # is itself the thing that would have caught an overnight problem.
            try {
                $age = (New-TimeSpan -Start ([datetime]$st.timestamp) -End (Get-Date)).TotalHours
                if ($age -gt 2) { Warn ("Health status is {0:N1}h old -- is the health-check task running?" -f $age) }
            } catch { }

            if ($st.destructive_saves -gt 0) {
                Warn "$($st.destructive_saves) destructive save(s) flagged by the health check."
                foreach ($d in $st.destructive_recent) { Emit "      $d" }
            }
        } catch {
            Warn "Could not parse $statusFile ($($_.Exception.Message))."
        }
    } else {
        Warn "No last_health_status.json -- the health-check task may not be registered."
    }
}

# --------------------------------------------------------------------------
Section '3. Attendance data in the database'
# --------------------------------------------------------------------------
if (-not (Test-Path $python)) {
    Warn "No venv python at $python; skipping database checks."
} else {
$py = @"
import sys, datetime
sys.path.insert(0, r'$repoRoot')
import config, sqlalchemy as sa
day = '$Date'
try:
    e = sa.create_engine(config.DATABASE_URL)
    with e.connect() as c:
        head = c.execute(sa.text('select version_num from alembic_version')).scalar()
        print('alembic_head|%s' % head)
        n = c.execute(sa.text('select count(*) from attendance_observations')).scalar()
        print('obs_total|%s' % n)
        row = c.execute(sa.text(
            "select count(*), count(distinct user_id), min(seen_at), max(seen_at) "
            "from attendance_observations "
            "where (seen_at at time zone 'UTC') at time zone :tz >= :d ::date "
            "and (seen_at at time zone 'UTC') at time zone :tz < (:d ::date + 1)"
        ), {'tz': config.ATTENDANCE_TZ, 'd': day}).first()
        print('obs_day|%s|%s|%s|%s' % (row[0], row[1], row[2], row[3]))
        d = c.execute(sa.text(
            'select count(*), count(distinct user_id) from attendance_days where local_date = :d ::date'
        ), {'d': day}).first()
        print('days_rows|%s|%s' % (d[0], d[1]))
        prev = (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
        p = c.execute(sa.text(
            'select count(*) from attendance_days where local_date = :d ::date'
        ), {'d': prev}).scalar()
        # Were there raw observations to roll up at all? Without this the
        # check cries wolf for every day before the feature was switched on.
        po = c.execute(sa.text(
            "select count(*) from attendance_observations "
            "where (seen_at at time zone 'UTC') at time zone :tz >= :d ::date "
            "and (seen_at at time zone 'UTC') at time zone :tz < (:d ::date + 1)"
        ), {'tz': config.ATTENDANCE_TZ, 'd': prev}).scalar()
        print('days_prev|%s|%s|%s' % (prev, p, po))
        print('admins|%s' % c.execute(sa.text('select count(*) from users where is_admin')).scalar())
        print('annotations|%s' % c.execute(sa.text('select count(*) from annotations')).scalar())
        print('tz|%s' % config.ATTENDANCE_TZ)
        print('instance|%s' % config.ATTENDANCE_INSTANCE_ID)
except Exception as exc:
    print('error|%s' % exc)
"@
    $tmp = Join-Path $env:TEMP "morning-check-$PID.py"
    Set-Content -Path $tmp -Value $py -Encoding UTF8
    $out = & $python $tmp 2>&1
    Remove-Item $tmp -ErrorAction SilentlyContinue

    $db = @{}
    foreach ($line in $out) {
        $parts = "$line".Split('|')
        if ($parts.Count -ge 2) { $db[$parts[0]] = $parts[1..($parts.Count - 1)] }
    }

    if ($db.ContainsKey('error')) {
        Problem "Database query failed: $($db['error'][0])"
    } else {
        Emit "  alembic head : $($db['alembic_head'][0])"
        if ($db['alembic_head'][0] -ne '72a3921edd78') {
            Warn "Alembic head is not 72a3921edd78 -- the attendance migration may be missing or superseded."
        }
        Emit "  timezone     : $($db['tz'][0])   instance: $($db['instance'][0])"
        Emit "  annotations  : $($db['annotations'][0])"
        Emit "  admins       : $($db['admins'][0])"
        if ([int]$db['admins'][0] -eq 0) { Warn "No admin user -- the attendance dashboard is unreachable. Run scripts/grant_admin.py <user>." }

        $o = $db['obs_day']
        Emit "  observations today : $($o[0]) row(s), $($o[1]) distinct user(s)"
        Emit "    first: $($o[2])"
        Emit "    last : $($o[3])"
        Emit "  observations total : $($db['obs_total'][0])"
        if ([int]$o[0] -eq 0) {
            Warn "No observations recorded for $Date. Nobody logged in yet, or capture is not running."
        } else {
            Ok "Capture is reaching the database."
        }

        $d = $db['days_rows']
        Emit "  attendance_days for $Date      : $($d[0]) row(s), $($d[1]) user(s)"
        $p = $db['days_prev']
        Emit "  attendance_days for $($p[0]) : $($p[1]) row(s)"
        # The 19:00 rollup writes yesterday and today. Missing rows only mean
        # a failed run if there were raw observations to roll up -- otherwise
        # it is simply a day nobody worked, or one before capture was on.
        if ([int]$p[1] -eq 0 -and [int]$p[2] -gt 0) {
            Warn "No rollup rows for $($p[0]) despite $($p[2]) raw observation(s). The 19:00 AnnotationAttendanceRollup task did not run. Check Task Scheduler (elevated)."
        } elseif ([int]$p[1] -eq 0) {
            Emit "    (no observations that day either -- nothing to roll up)"
        } else {
            Ok "Previous day was rolled up ($($p[1]) row(s))."
        }
    }
}

# --------------------------------------------------------------------------
Section '4. Errors and warnings in the logs'
# --------------------------------------------------------------------------
function Get-LinesAfterCutoff {
    # Log lines start with a timestamp in one of two shapes:
    #   app.log      2026-09-23 18:53:39,801 WARNING ...
    #   service logs 2026-09-23T18:58:48.218+05:45 INFO ...
    # Anything unparseable is kept: better a spurious line than a dropped one.
    param([string]$Path, [datetime]$Cutoff)
    if (-not (Test-Path $Path)) { return @() }
    Get-Content -Path $Path -ErrorAction SilentlyContinue | Where-Object {
        if ($_ -match '^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})') {
            try { [datetime]("$($Matches[1]) $($Matches[2])") -ge $Cutoff } catch { $true }
        } else { $true }
    }
}

if ($logDir -and (Test-Path $logDir)) {
    # app.log rotates by date; today's is the live file, older days are suffixed.
    $appLog = Join-Path $logDir 'app.log'
    if ($Date -ne (Get-Date -Format 'yyyy-MM-dd')) {
        $dated = Join-Path $logDir "app.log.$Date"
        if (Test-Path $dated) { $appLog = $dated }
    }

    $appLines = Get-LinesAfterCutoff -Path $appLog -Cutoff $cutoff

    # Known-benign noise, counted but not raised as a problem. This one is a
    # Windows/asyncio artifact of a client dropping a connection mid-response
    # (ProactorEventLoop closing a pipe that is already gone). It has appeared
    # ~700 times since 2026-07 with no user-visible effect; leaving it in the
    # PROBLEM list would bury the errors that do matter.
    $benign = '_ProactorBasePipeTransport\._call_connection_lost|^handle: <Handle _ProactorBasePipeTransport'

    $allErrs = @($appLines | Where-Object { $_ -match '\b(ERROR|CRITICAL)\b' })
    $errs    = @($allErrs  | Where-Object { $_ -notmatch $benign })
    $noise   = $allErrs.Count - $errs.Count
    $warns   = @($appLines | Where-Object { $_ -match '\bWARNING\b' })

    Emit "  app.log: $($errs.Count) error(s), $($warns.Count) warning(s) since $Since"
    if ($noise -gt 0) { Emit "    (plus $noise known-benign asyncio connection-lost line(s), ignored)" }
    if ($errs.Count -gt 0) {
        Problem "$($errs.Count) ERROR line(s) in app.log."
        $errs | Select-Object -First 25 | ForEach-Object { Emit "      $_" }
        if ($errs.Count -gt 25) { Emit "      ... $($errs.Count - 25) more" }
    }

    # Attendance-specific trouble, called out separately: these are the
    # warnings that mean capture is silently degrading.
    $attWarn = @($appLines | Where-Object { $_ -match 'attendance' -and $_ -match '\b(WARNING|ERROR)\b' })
    if ($attWarn.Count -gt 0) {
        Problem "$($attWarn.Count) attendance warning/error line(s) -- capture or the drain is degrading."
        $attWarn | Select-Object -First 20 | ForEach-Object { Emit "      $_" }
    } else {
        Ok "No attendance warnings in app.log."
    }

    # Failed logins are grouped: a handful is someone fat-fingering, a burst
    # from one IP is worth a look.
    $fails = @($appLines | Where-Object { $_ -match 'Failed login' })
    if ($fails.Count -gt 0) {
        Emit "  Failed logins: $($fails.Count)"
        $fails | ForEach-Object {
            if ($_ -match "username='([^']*)'.*from ([\d\.]+)") { "$($Matches[1]) @ $($Matches[2])" }
        } | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object {
            Emit "      $($_.Count)x  $($_.Name)"
        }
        if ($fails.Count -ge 20) { Warn "$($fails.Count) failed logins -- check whether someone is locked out." }
    }

    # Per-day service logs.
    $svcDir = Join-Path $logDir "service\$Date"
    if (Test-Path $svcDir) {
        $errFile = Join-Path $svcDir 'errors.log'
        $svcErrs = @(Get-LinesAfterCutoff -Path $errFile -Cutoff $cutoff)
        Emit "  service/$Date/errors.log: $($svcErrs.Count) line(s) since $Since"
        if ($svcErrs.Count -gt 0) {
            # 4xx are user-facing and mostly benign (401 on an expired token,
            # 409 on a genuine conflict); 5xx are ours.
            $server = @($svcErrs | Where-Object { $_ -match '\s5\d{2}\s' })
            if ($server.Count -gt 0) {
                Problem "$($server.Count) 5xx response(s) in the service log."
                $server | Select-Object -First 20 | ForEach-Object { Emit "      $($_.Substring(0, [Math]::Min(220, $_.Length)))" }
            } else {
                Ok "No 5xx responses; the error lines are 4xx (auth/conflict/validation)."
            }
            $svcErrs | Group-Object {
                if ($_ -match '\s(\d{3})\s') { $Matches[1] } else { 'other' }
            } | Sort-Object Count -Descending | ForEach-Object { Emit "      $($_.Count)x  HTTP $($_.Name)" }
        }

        # Attendance endpoint traffic, as evidence the UI is actually used.
        foreach ($m in @('GET', 'POST')) {
            $f = Join-Path $svcDir "$m.log"
            if (Test-Path $f) {
                $hits = @(Get-LinesAfterCutoff -Path $f -Cutoff $cutoff | Where-Object { $_ -match '/api/attendance' })
                if ($hits.Count -gt 0) { Emit "  $m /api/attendance: $($hits.Count) request(s)" }
            }
        }
    } else {
        Warn "No service log directory for $Date ($svcDir)."
    }
}

# --------------------------------------------------------------------------
Section '5. Destructive saves'
# --------------------------------------------------------------------------
# A save that replaces most of a task's objects with far fewer. Pre-dates the
# attendance work, but it is the costliest thing in these logs, so a morning
# read should surface it.
if ($logDir -and (Test-Path (Join-Path $logDir "service\$Date"))) {
    $postLog = Join-Path $logDir "service\$Date\POST.log"
    $dest = @(Get-LinesAfterCutoff -Path $postLog -Cutoff $cutoff | Where-Object { $_ -match 'task\.save\.destructive' })
    if ($dest.Count -gt 0) {
        Problem "$($dest.Count) destructive save(s) -- a task lost most of its objects."
        $dest | Select-Object -First 15 | ForEach-Object { Emit "      $($_.Substring(0, [Math]::Min(240, $_.Length)))" }
    } else {
        Ok "No destructive saves."
    }
} else {
    Emit "  (no service log for $Date)"
}

# --------------------------------------------------------------------------
Section '6. Startup'
# --------------------------------------------------------------------------
# Confirms the 07:00 start was clean, and that the drain started with it.
if ($logDir -and (Test-Path $logDir)) {
    $stdout = Join-Path $logDir 'stdout.log'
    if (Test-Path $stdout) {
        $starts = @(Get-LinesAfterCutoff -Path $stdout -Cutoff $cutoff |
                    Where-Object { $_ -match 'Started server|Application startup|Uvicorn running|attendance drain|Waiting for application' })
        if ($starts.Count -gt 0) {
            $starts | Select-Object -First 15 | ForEach-Object { Emit "      $($_.Substring(0, [Math]::Min(200, $_.Length)))" }
        } else {
            Emit "  (no startup banner since $Since -- the process has been up since before then)"
        }
        $tracebacks = @(Get-LinesAfterCutoff -Path $stdout -Cutoff $cutoff | Where-Object { $_ -match 'Traceback \(most recent call last\)' })
        if ($tracebacks.Count -gt 0) { Problem "$($tracebacks.Count) traceback(s) in stdout.log -- see $stdout" }
    }
}

# --------------------------------------------------------------------------
Section 'VERDICT'
# --------------------------------------------------------------------------
if ($script:Problems.Count -eq 0 -and $script:Warnings.Count -eq 0) {
    Emit '  ALL CLEAR -- no problems or warnings found.'
} else {
    if ($script:Problems.Count -gt 0) {
        Emit "  $($script:Problems.Count) PROBLEM(S):"
        $script:Problems | ForEach-Object { Emit "    - $_" }
    }
    if ($script:Warnings.Count -gt 0) {
        Emit "  $($script:Warnings.Count) WARNING(S):"
        $script:Warnings | ForEach-Object { Emit "    - $_" }
    }
}
Emit ''
Emit 'Rollback reminders:'
Emit '  attendance only : set ATTENDANCE_ENABLED = "false" in .env, restart (no schema change)'
Emit '  code            : git reset --hard c306c1d, restart'
Emit '  schema          : venv\Scripts\alembic.exe downgrade b2d5f8c13a67'

if ($OutFile) {
    $script:Lines | Set-Content -Path $OutFile -Encoding UTF8
    Write-Host ''
    Write-Host "Report written to $OutFile"
}

# 1 if anything needs attention, so this can gate another script later.
if ($script:Problems.Count -gt 0) { exit 1 } else { exit 0 }
