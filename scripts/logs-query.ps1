<#
.SYNOPSIS
    Ask the service log the questions an operator actually has.

.DESCRIPTION
    The service log is deliberately plain key=value text so it can be grepped
    with whatever is on the box (see .devnotes/logging/02_PLAN.md, D3). This
    script is not a layer over that - it is a set of shortcuts for the handful
    of queries that come up repeatedly, and every one of them prints the raw
    matching lines. Reading them directly with Select-String is always valid.

    Modes:

      -Losses           Saves that REDUCED an object count. The first thing to
                        run when an annotator reports missing work: it lists
                        who saved, on which task, and how many objects went.
      -Task <id>        Everything that happened to one task, in time order.
      -User <name>      Everything one person did.
      -Destructive      Every WARN line: deletes, bulk deletes, clears,
                        project deletes, class deletes, replace-mode imports.
      -Errors           Every 4xx/5xx.
      -Event <name>     One event type, e.g. task.review or auth.login_failed.

.PARAMETER Days
    How many days back to search. Default 7.

.EXAMPLE
    .\scripts\logs-query.ps1 -Losses
    .\scripts\logs-query.ps1 -Task 728
    .\scripts\logs-query.ps1 -User kushal -Days 2
    .\scripts\logs-query.ps1 -Destructive -Days 30
#>
[CmdletBinding()]
param(
    [switch]$Losses,
    [int]$Task,
    [string]$User,
    [switch]$Destructive,
    [switch]$Errors,
    [string]$Event,
    [int]$Days = 7,
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
    Write-Error "No service log at $serviceDir. Is SERVICE_LOG_ENABLED set, and is DATA_DIR/LOG_DIR pointing where you think?"
    exit 1
}

# Dated directories only, newest last so output reads forward in time. Anything
# an operator dropped in by hand is skipped rather than searched.
$cutoff = (Get-Date).AddDays(-$Days).Date
$days = Get-ChildItem -Path $serviceDir -Directory |
    Where-Object {
        $parsed = [datetime]::MinValue
        [datetime]::TryParseExact($_.Name, 'yyyy-MM-dd', $null, 'None', [ref]$parsed) -and $parsed -ge $cutoff
    } |
    Sort-Object Name

if (-not $days) {
    Write-Host "No log directories in the last $Days days under $serviceDir."
    exit 0
}

function Find-Lines {
    param([string]$Pattern, [string[]]$Files = @("*.log"))
    foreach ($day in $days) {
        foreach ($file in $Files) {
            Get-ChildItem -Path $day.FullName -Filter $file -ErrorAction SilentlyContinue |
                Select-String -Pattern $Pattern |
                ForEach-Object { $_.Line }
        }
    }
}

if ($Losses) {
    Write-Host "`n=== Saves that reduced an object count (last $Days days) ===" -ForegroundColor Yellow
    Write-Host "Read: objects_prev -> objects. A large negative delta on a task"
    Write-Host "the annotator says lost work is the answer, and the previous blob"
    Write-Host "is recoverable from task_annotation_history for that task id.`n"
    # errors.log duplicates lines from the method files, so it is excluded here
    # and everywhere below to avoid reporting the same event twice.
    Find-Lines -Pattern 'event=task\.save .*delta=-' -Files @("POST.log", "PATCH.log")
}
elseif ($Task) {
    Write-Host "`n=== Everything that happened to task $Task (last $Days days) ===" -ForegroundColor Yellow
    Find-Lines -Pattern "task=$Task(\s|$)" -Files @("POST.log", "PATCH.log", "DELETE.log", "GET.log", "OTHER.log")
}
elseif ($User) {
    Write-Host "`n=== Everything $User did (last $Days days) ===" -ForegroundColor Yellow
    Find-Lines -Pattern "user=$User(\s|$)" -Files @("POST.log", "PATCH.log", "DELETE.log", "OTHER.log")
}
elseif ($Destructive) {
    Write-Host "`n=== Destructive actions (last $Days days) ===" -ForegroundColor Yellow
    Write-Host "Every WARN line: task/project/class deletes, bulk operations,"
    Write-Host "annotation clears, replace-mode imports, grant revokes.`n"
    Find-Lines -Pattern '\sWARN\s' -Files @("POST.log", "PATCH.log", "DELETE.log", "OTHER.log")
}
elseif ($Errors) {
    Write-Host "`n=== Failed requests (last $Days days) ===" -ForegroundColor Yellow
    Find-Lines -Pattern '.' -Files @("errors.log")
}
elseif ($Event) {
    Write-Host "`n=== event=$Event (last $Days days) ===" -ForegroundColor Yellow
    Find-Lines -Pattern "event=$([regex]::Escape($Event))(\s|$)" -Files @("POST.log", "PATCH.log", "DELETE.log", "GET.log", "OTHER.log")
}
else {
    Write-Host "Pick a mode: -Losses, -Task <id>, -User <name>, -Destructive, -Errors, or -Event <name>."
    Write-Host "Run 'Get-Help .\scripts\logs-query.ps1 -Full' for details."
    Write-Host "`nAvailable days under ${serviceDir}:"
    $days | ForEach-Object { "  $($_.Name)" }
}
