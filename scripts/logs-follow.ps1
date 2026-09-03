<#
.SYNOPSIS
    Follow the service log live, merged across methods, with noise filtered.

.DESCRIPTION
    `Get-Content -Wait` follows one file. The service log is one file per HTTP
    method per day (.devnotes/logging/02_PLAN.md D2), so following it properly
    means merging several files AND moving to a new directory at midnight -
    neither of which a plain tail does.

    This polls the current day's files, prints new lines in timestamp order,
    and rolls over on its own when the date changes. Ctrl+C to stop.

    By default the lock/timer chatter (heartbeat, claim, release-beacon,
    lock-status, time pings) is hidden even though it is already sampled in the
    file - watching a live tail is exactly when it drowns everything else.
    -All turns it back on.

.PARAMETER Method
    Follow only these methods. Default: all of them plus errors.
    e.g. -Method POST,DELETE

.PARAMETER Filter
    Only print lines matching this regex. Applied after the noise filter.
    e.g. -Filter 'event=task\.save'

.PARAMETER Interesting
    Only lines carrying an `event=` field. Drops the plain request record and
    leaves the semantic events - the fastest way to watch what the app is
    actually doing rather than which URLs were hit.

.PARAMETER Warnings
    Only WARN and ERROR lines: the destructive actions and the failures.

.PARAMETER Tail
    How many existing lines to print before following. Default 10, 0 for none.

.PARAMETER All
    Include the sampled lock/timer traffic that is hidden by default.

.PARAMETER LogDir
    Overrides LOG_DIR / DATA_DIR discovery. Point this at the dev instance when
    following it: -LogDir D:\annotation-data-dev\logs

.EXAMPLE
    .\scripts\logs-follow.ps1
    Everything interesting, live.

.EXAMPLE
    .\scripts\logs-follow.ps1 -LogDir D:\annotation-data-dev\logs -Interesting
    Follow the dev instance, semantic events only.

.EXAMPLE
    .\scripts\logs-follow.ps1 -Warnings
    Watch only destructive actions and failures.

.EXAMPLE
    .\scripts\logs-follow.ps1 -Filter 'task=728'
    Watch one task as an annotator works on it.
#>
[CmdletBinding()]
param(
    [string[]]$Method,
    [string]$Filter,
    [switch]$Interesting,
    [switch]$Warnings,
    [int]$Tail = 10,
    [switch]$All,
    [string]$LogDir
)

$ErrorActionPreference = "Stop"

if (-not $LogDir) {
    $LogDir = if ($env:LOG_DIR) { $env:LOG_DIR }
              elseif ($env:DATA_DIR) { Join-Path $env:DATA_DIR "logs" }
              else { Join-Path (Split-Path -Parent $PSScriptRoot) "logs" }
}
$serviceDir = Join-Path $LogDir "service"

if (-not (Test-Path $serviceDir)) {
    Write-Error "No service log at $serviceDir. Is the server running, and is DATA_DIR/LOG_DIR pointing where you think? (Dev usually needs -LogDir D:\annotation-data-dev\logs)"
    exit 1
}

# errors.log duplicates lines that are already in the method files, so it is
# never followed - including it would print every failure twice.
$methodFiles = if ($Method) {
    $Method | ForEach-Object { "$($_.ToUpper()).log" }
} else {
    @("GET.log", "POST.log", "PATCH.log", "DELETE.log", "OTHER.log")
}

# The sampled lock and timer traffic. Already thinned in the file (one line per
# tab per minute), but on a live tail with several annotators it still buries
# the saves, which is what anyone watching is watching for.
$noise = 'heartbeat|/claim|release-beacon|lock-status|/api/team/time|/api/time-logs/time'

function Test-Line {
    param([string]$Line)
    if (-not $All -and $Line -match $noise) { return $false }
    if ($Warnings -and $Line -notmatch '\s(WARN|ERROR)\s') { return $false }
    if ($Interesting -and $Line -notmatch '\sevent=') { return $false }
    if ($Filter -and $Line -notmatch $Filter) { return $false }
    return $true
}

function Write-Line {
    param([string]$Line)
    # Colour by level so a WARN is visible in a scrolling window. The level is
    # the second field, after the ISO timestamp.
    $colour = switch -Regex ($Line) {
        '\sERROR\s' { "Red";    break }
        '\sWARN\s'  { "Yellow"; break }
        default     { "Gray" }
    }
    # Highlight an object-count regression: this is the line the whole logging
    # exercise exists to surface, and it must not scroll past unnoticed.
    if ($Line -match 'delta=-[1-9]') { $colour = "Magenta" }
    Write-Host $Line -ForegroundColor $colour
}

# Per-file byte offsets. Following by offset rather than by line count means a
# file that is appended to between polls is read exactly once, with no
# re-printing and nothing skipped.
$offsets = @{}

function Get-DayDir {
    Join-Path $serviceDir (Get-Date -Format "yyyy-MM-dd")
}

$currentDay = Get-DayDir

# Seed the offsets at the end of each file, so following starts from "now"
# rather than replaying the whole day. The -Tail lines are printed first.
if (Test-Path $currentDay) {
    $seed = @()
    foreach ($file in $methodFiles) {
        $path = Join-Path $currentDay $file
        if (Test-Path $path) {
            $offsets[$path] = (Get-Item $path).Length
            if ($Tail -gt 0) {
                $seed += Get-Content $path -Tail $Tail -ErrorAction SilentlyContinue
            }
        }
    }
    if ($seed) {
        # Sorted across files: every line starts with an ISO timestamp, so an
        # ordinal sort is chronological.
        $seed | Where-Object { Test-Line $_ } |
            Sort-Object -CaseSensitive |
            Select-Object -Last $Tail |
            ForEach-Object { Write-Line $_ }
    }
}

Write-Host "`nFollowing $currentDay  (Ctrl+C to stop)" -ForegroundColor Cyan
$active = @()
if ($Warnings)     { $active += "warnings only" }
if ($Interesting)  { $active += "events only" }
if ($Filter)       { $active += "filter: $Filter" }
if (-not $All)     { $active += "lock/timer traffic hidden (-All to show)" }
if ($active) { Write-Host "  $($active -join ', ')" -ForegroundColor DarkGray }
Write-Host ""

while ($true) {
    # Midnight rollover: the writer opens a new dated directory, so follow it
    # there instead of waiting forever on yesterday's files.
    $day = Get-DayDir
    if ($day -ne $currentDay) {
        Write-Host "`n--- date rolled over, now following $day ---`n" -ForegroundColor Cyan
        $currentDay = $day
        $offsets = @{}
    }

    if (Test-Path $currentDay) {
        $batch = @()
        foreach ($file in $methodFiles) {
            $path = Join-Path $currentDay $file
            if (-not (Test-Path $path)) { continue }

            $length = (Get-Item $path).Length
            $from = if ($offsets.ContainsKey($path)) { $offsets[$path] } else { 0 }

            # A shorter file than last poll means it was rotated or truncated
            # under us; start over from the beginning rather than seeking past
            # the end and reading nothing forever.
            if ($length -lt $from) { $from = 0 }
            if ($length -eq $from) { continue }

            $stream = [System.IO.FileStream]::new(
                $path, [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                # ReadWrite share: the server holds these files open for
                # writing, so anything less throws "file in use".
                [System.IO.FileShare]::ReadWrite)
            try {
                $null = $stream.Seek($from, [System.IO.SeekOrigin]::Begin)
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $text = $reader.ReadToEnd()
                $offsets[$path] = $stream.Position
            }
            finally {
                $stream.Dispose()
            }

            $batch += $text -split "`r?`n" | Where-Object { $_ }
        }

        if ($batch) {
            $batch | Where-Object { Test-Line $_ } |
                Sort-Object -CaseSensitive |
                ForEach-Object { Write-Line $_ }
        }
    }

    Start-Sleep -Milliseconds 500
}
