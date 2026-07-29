# Making the Postgres container survive a reboot

Your database runs in Docker as the container `annotation-db`. Right now it
does **not** come back after a restart, and there are three independent
reasons — fixing only one of them is not enough.

---

## What's actually wrong

| # | Layer | Current state | Consequence |
|---|---|---|---|
| 1 | `com.docker.service` (Windows service) | **Manual**, stopped | The Docker engine doesn't start with Windows |
| 2 | Docker Desktop autostart | Run key exists, but `StartOnLogin: False` | Needs you to log in *and* launch it manually |
| 3 | `annotation-db` restart policy | **`no`** | Even once Docker is up, the container stays down |
| 4 | Startup ordering | `AnnotationApp` runs as SYSTEM at boot | The app can start before Postgres exists |

Note on #2/#4: the Docker Desktop autostart entry lives under `HKCU` — it's
tied to *your user session*. The annotation service runs as `SYSTEM` at boot,
before any login. So even with Desktop autostart enabled, an unattended
reboot brings the app up against a database that isn't there yet.

**Good news first:** the container's exit was clean. Its logs show normal
checkpoint activity right up to the end, and `ExitCode=255, OOMKilled=false`
with no error — that's the Docker engine shutting down, not a database fault.
Your data is intact in the named volume `annotation-pgdata`, which exists
independently of the container.

---

## Fix (run as Administrator)

### Step 1 — Make the Docker engine start with Windows

```powershell
Set-Service -Name com.docker.service -StartupType Automatic
Start-Service -Name com.docker.service
```

### Step 2 — Make the container restart itself

```powershell
docker update --restart unless-stopped annotation-db
```

`unless-stopped` (rather than `always`) means: come back after reboots and
crashes, but stay down if *you* deliberately stopped it. That's the right
policy for a database you occasionally take offline on purpose.

Apply the same to anything else this deployment needs:

```powershell
docker update --restart unless-stopped annotation-db
```

### Step 3 — Start it now and confirm

```powershell
docker start annotation-db
Start-Sleep -Seconds 10
docker ps --filter "name=annotation-db" --format "{{.Names}}  {{.Status}}  {{.Ports}}"
```

Expected: `annotation-db  Up ... (healthy)  127.0.0.1:5435->5432/tcp`

Verify Postgres is actually accepting connections, not just that the
container is running:

```powershell
docker exec annotation-db pg_isready -U annot
```

Expected: `... accepting connections`

### Step 4 — Verify the policy stuck

```powershell
docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' annotation-db
```

Expected: `unless-stopped`

---

## Step 5 — Make the app wait for the database

Steps 1–4 get Postgres up on boot, but nothing guarantees it's *ready*
before `AnnotationApp` starts. Postgres typically takes 5–15 seconds to
accept connections after the container starts; uvicorn starts in about one.

Without this, an unattended reboot produces an app that's up but broken until
someone notices. The wrapper written by `install-service.ps1` already
restart-loops, so it will eventually recover — but it will log a burst of
connection failures and serve errors to whoever gets there first.

Add a readiness wait to the service wrapper. Open
`scripts/install-service.ps1`, find the port-guard block in the generated
wrapper (search for `ABORT: port`), and add this **after** it:

```powershell
# Wait for Postgres to accept connections before starting uvicorn. On a cold
# boot Docker and the database container are still coming up; without this
# the app starts first and thrashes against a refused connection.
$dbHost = "127.0.0.1"; $dbPort = 5435
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    $ok = Test-NetConnection -ComputerName $dbHost -Port $dbPort -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($ok) { break }
    Add-Content -Path $log -Value "$(Get-Date -Format 'HH:mm:ss')  [service] waiting for Postgres on ${dbHost}:${dbPort}..."
    Start-Sleep -Seconds 5
}
```

Then re-run `.\scripts\install-service.ps1` as Administrator to regenerate
the wrapper.

> Keep the `${dbHost}:${dbPort}` braces exactly as written. Windows
> PowerShell reads an undelimited name before a colon as a drive-qualified
> variable and fails to parse the whole file — this already broke the wrapper
> once.

---

## Step 6 — Prove it actually works

The only real test is a reboot. Do it at a quiet moment:

```powershell
Restart-Computer
```

After it comes back — **without logging in if you can check remotely**, since
that's the scenario that matters:

```powershell
docker ps --filter "name=annotation-db" --format "{{.Names}} {{.Status}}"
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected: container `Up`, and `{"status":"ok","database":"up"}`.

If the container is up but health says `database: down`, the app started
before Postgres was ready and hasn't retried yet — that's Step 5 missing or
its timeout too short.

---

## A note on Docker Desktop vs. a real service

Docker Desktop is a *desktop* application: its engine is tied to a user
session, which is an awkward fit for an unattended server. Steps 1–2 work,
but if this machine is genuinely a shared server, consider either:

- **Keeping Docker Desktop** and ensuring the machine auto-logs-in to the
  account that runs it (simplest, slightly less clean), or
- **Moving Postgres out of Docker** onto the native Windows Postgres already
  installed on this box (`C:\Program Files\PostgreSQL\17\`), which runs as a
  proper Windows service with no session dependency at all.

The second is more robust long-term but means migrating the data — see
`scripts/migrate_sqlite_to_postgres.py` for the shape of that work, and take
a verified backup first (`scripts/backup.py`, then `scripts/restore_drill.py`).
Not urgent; Steps 1–5 are sufficient for now.

---

## Quick reference

```powershell
# Fix autostart (Administrator, one time)
Set-Service -Name com.docker.service -StartupType Automatic
Start-Service -Name com.docker.service
docker update --restart unless-stopped annotation-db

# Start / check
docker start annotation-db
docker ps --filter "name=annotation-db" --format "{{.Names}} {{.Status}} {{.Ports}}"
docker exec annotation-db pg_isready -U annot

# Check the policy stuck
docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' annotation-db

# Why did it stop?
docker inspect --format 'ExitCode={{.State.ExitCode}} FinishedAt={{.State.FinishedAt}}' annotation-db
docker logs --tail 30 annotation-db
```
