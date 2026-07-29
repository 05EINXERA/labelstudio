# Setup Order — What to run before the resilience checks

The [RESILIENCE_RUNBOOK.md](RESILIENCE_RUNBOOK.md) verifies that supervision,
backups, and health monitoring are working. It assumes they're already
installed. **This page is what you do first.**

---

## Current state of this machine (checked 2026-07-28)

| Thing | Status |
|---|---|
| `AnnotationApp` (crash/reboot supervision) | ❌ **Not registered** |
| `AnnotationBackup` (nightly backups) | ❌ **Not registered** |
| `AnnotationHealthCheck` (health polling) | ❌ Not registered (optional) |
| App responding on `:8000` | ✅ Yes, `{"status":"ok","database":"up"}` |
| `APP_ENV` | ✅ `production` |
| Postgres reachable | ✅ Yes (port 5435) |
| `pg_dump` on PATH | ✅ `C:\Program Files\PostgreSQL\17\bin\pg_dump.exe` |
| venv present | ✅ Yes |

**Translation: you are running production with no automated backups and no
crash recovery.** If this disk fails or the process dies overnight, there is
nothing to fall back on. That's what Steps 1–4 below fix.

---

## ⚠️ Step 0 — Resolve the duplicate uvicorn processes first

Right now **two** uvicorn instances are running against the same app:

| PID | Python | Serving port 8000? |
|---|---|---|
| 20636 | `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` (system) | ✅ **Yes** |
| 4752 | `D:\...\labelstudio\venv\Scripts\python.exe` (venv) | ❌ No — lost the bind |

Two problems with leaving this:

1. The instance actually serving traffic uses **system Python, not the
   venv** — so it may not have the versions pinned in `requirements.txt`.
2. `install-service.ps1` will start a **third** instance from the venv, which
   will fail to bind port 8000 and restart-loop forever while the stale
   process keeps serving.

**Fix — stop both, then let the supervisor own the process:**

```powershell
Stop-Process -Id 20636 -Force
Stop-Process -Id 4752  -Force

# Confirm nothing is left on 8000 (should print nothing)
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

> Do this at a quiet moment — it drops annotators' connections. Their saved
> work is safe (it's committed to Postgres), but anyone mid-AI-detection
> loses that job.

If the PIDs above have changed by the time you read this, re-find them:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*uvicorn*" } |
  Select-Object ProcessId, CommandLine
```

---

## Step 0.5 — Make Postgres survive a reboot (Docker deployments)

If your database runs in Docker — it does on this machine (`annotation-db`) —
it will **not** come back after a restart until you fix three separate
settings: the Docker Windows service is set to Manual, Docker Desktop's
autostart is off, and the container's restart policy is `no`.

Supervising the *app* while the *database* stays down just gives you a
restart loop. Do this first:

**→ [DOCKER_AUTOSTART.md](DOCKER_AUTOSTART.md)**

Short version (as Administrator):

```powershell
Set-Service -Name com.docker.service -StartupType Automatic
Start-Service -Name com.docker.service
docker update --restart unless-stopped annotation-db
docker start annotation-db
docker exec annotation-db pg_isready -U annot
```

That doc also covers making the app *wait* for Postgres on boot, which
matters because uvicorn starts far faster than a database container.

---

## Step 1 — Decide the backup destination

You need this before Step 3. See the protection table in the runbook's
"Before you start" section for the tradeoffs.

Reminder specific to this machine: **`C:` and `D:` are two partitions of the
same physical disk**, so `D:\annotation-backups` does *not* protect against
drive failure. It's still worth doing as a stopgap — it covers accidental
deletion and corruption — but plan to add an offsite copy (runbook Part 5).

```powershell
# Best available option, in order:
#   \\fileserver\annotation-backups   (another machine)
#   E:\annotation-backups             (external USB)
#   D:\annotation-backups             (same disk — stopgap only)
```

Write your choice down; every later command uses it.

---

## Step 2 — Install process supervision

Makes the app restart automatically after a crash, start on boot, and stops
the PC sleeping mid-shift.

**Run PowerShell as Administrator:**

```powershell
cd D:\ai\projects\annotation\labelstudio
.\scripts\install-service.ps1
```

Expected: a confirmation that the `AnnotationApp` task was registered.

**Then verify it actually took over:**

```powershell
Start-ScheduledTask -TaskName "AnnotationApp"
Start-Sleep -Seconds 15
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected: `status=ok, database=up`. If you get a connection error, check
`D:\annotation-data\logs\service.log` — the wrapper logs uvicorn's own
startup errors there.

---

## Step 3 — Install scheduled backups

**As Administrator**, using the destination from Step 1:

```powershell
.\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups" -Keep 7
```

*(`-Keep 7` rather than 14 if backups share a disk with the app — see the
runbook's Part 3.)*

**Then run one immediately** rather than waiting for 2 AM, so you find
problems now:

```powershell
python scripts\backup.py --dest "D:\annotation-backups" --keep 7
```

Expected:

```
Database -> D:\annotation-backups\workspace-20260728-...dump
Uploads  -> N new file(s)
Backup destination total size: X.XX GB
```

The first run copies **all** uploads, so it may take a while and consume
real space. Later runs are incremental.

---

## Step 4 — Prove the backup restores

Do not skip this. A backup that has never been restored is an assumption,
not a safety net. It's safe — it uses a disposable scratch database.

```powershell
python scripts\restore_drill.py --dest "D:\annotation-backups" --backend postgres --pg-admin-url "postgresql://annot:seinxera@127.0.0.1:5435/postgres"
```

Expected: `PASS: snapshot restores cleanly — N project(s), M task(s) readable.`

**Check the numbers look right.** `0 project(s), 0 task(s)` is a failure
wearing a PASS label.

---

## Step 5 — (Optional) Health polling

```powershell
.\scripts\schedule-health-check.ps1
.\scripts\health-check.ps1          # test it immediately
```

Expected: `[OK] ... status=ok database=up`

---

## Step 6 — Now run the resilience checks

Everything the runbook checks now exists, so it should come back clean:

```powershell
.\scripts\verify-resilience.ps1 -BackupDest "D:\annotation-backups"
```

Expected: all `OK`. From here, follow
[RESILIENCE_RUNBOOK.md](RESILIENCE_RUNBOOK.md) — especially Part 2's
recurring checks and Part 5 if you're on a local-only destination.

---

## The whole thing, in order

```powershell
# 0. Stop duplicate uvicorn instances (find current PIDs first)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*uvicorn*" } | Select-Object ProcessId, CommandLine
Stop-Process -Id <each-pid> -Force

# 0.5 Docker/Postgres autostart   [Administrator]  — see DOCKER_AUTOSTART.md
Set-Service -Name com.docker.service -StartupType Automatic
Start-Service -Name com.docker.service
docker update --restart unless-stopped annotation-db
docker start annotation-db
docker exec annotation-db pg_isready -U annot

# 1. (decide destination — no command)

# 2. Supervision            [Administrator]
cd D:\ai\projects\annotation\labelstudio
.\scripts\install-service.ps1
Start-ScheduledTask -TaskName "AnnotationApp"
Start-Sleep -Seconds 15; Invoke-RestMethod http://127.0.0.1:8000/health

# 3. Backups                [Administrator]
.\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups" -Keep 7
python scripts\backup.py      --dest "D:\annotation-backups" --keep 7

# 4. Prove restore works
python scripts\restore_drill.py --dest "D:\annotation-backups" --backend postgres --pg-admin-url "postgresql://annot:seinxera@127.0.0.1:5435/postgres"

# 5. Health polling (optional) [Administrator]
.\scripts\schedule-health-check.ps1
.\scripts\health-check.ps1

# 6. Verify the lot
.\scripts\verify-resilience.ps1 -BackupDest "D:\annotation-backups"
```

---

## Not a script — but do it

**Get a UPS.** Steps 2–6 handle software failure. They do nothing for a power
cut mid-write. A basic consumer UPS (5–10 minutes runtime) lets Postgres
flush cleanly and the machine come back on its own. For a box holding 20–25
people's work, it's the cheapest real protection available and nothing in
this repo substitutes for it.

**Plan an offsite copy** if your backups live on this machine's disk — see
runbook Part 5.
