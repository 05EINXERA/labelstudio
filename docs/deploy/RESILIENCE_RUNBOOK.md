# Resilience Runbook — Operator Guide

**Who this is for:** whoever runs the annotation server on the office PC.
No assistant, no developer required — every step below is a command you run
yourself, with the expected output written out so you can tell pass from
fail.

**What it covers:** confirming crash-recovery and backups are actually
working, proving a backup can be restored, and knowing what to do when
something breaks.

**Time:** ~30 minutes the first time. ~5 minutes for the recurring checks.

> ### ⚠️ Have you installed the tooling yet?
>
> This runbook **verifies** supervision, backups, and health monitoring. It
> assumes they're already set up. If `verify-resilience.ps1` reports tasks
> as "not registered", or you've never run `install-service.ps1` /
> `schedule-backup.ps1` on this machine, start with
> **[SETUP_ORDER.md](SETUP_ORDER.md)** — it walks through installing them in
> the right order, then sends you back here.

> **A note on why this exists:** installing a backup script is not the same
> as having backups. Registering a service is not the same as knowing it
> restarts. This runbook is about *verifying*, not installing — several
> steps exist specifically to catch "we set that up months ago and assumed
> it was fine."

---

## Before you start

Open PowerShell **as Administrator** on the deployment PC, and go to the repo:

```powershell
cd D:\ai\projects\annotation\labelstudio
```

You'll need to know two things. Write them down now:

| Thing | How to find it | Yours |
|---|---|---|
| **Backup destination** | The `-Dest` you used with `schedule-backup.ps1` | `________` |
| **Which database** | Open `.env`, look at `DATABASE_URL`. Starts with `postgresql` → Postgres. Empty or starts with `sqlite` → SQLite | `________` |

Throughout this runbook, commands are written with a network share
(`\\fileserver\annotation-backups`) as the destination. **If you don't have a
file server, use a local path instead** — every command works identically,
just substitute the path:

```powershell
# Network share (best)
--dest "\\fileserver\annotation-backups"

# External USB drive (good)
--dest "E:\annotation-backups"

# Local second partition (weakest — read the warning below)
--dest "D:\annotation-backups"
```

### ⚠️ How much protection does a local backup actually give?

Be honest with yourself about this, because it decides how much risk you're
carrying.

| Destination | Survives accidental delete / bad import? | Survives DB corruption? | Survives **disk failure**? | Survives PC theft/fire? |
|---|---|---|---|---|
| Network share / NAS | ✅ | ✅ | ✅ | ✅ (if elsewhere) |
| External USB drive | ✅ | ✅ | ✅ | ❌ (usually same room) |
| Separate *physical* disk in the PC | ✅ | ✅ | ✅ | ❌ |
| **Another partition on the same disk** (e.g. `C:` → `D:`) | ✅ | ✅ | ❌ **No** | ❌ |
| Same folder tree as the app | ❌ | ❌ | ❌ | ❌ |

**A different drive letter is not necessarily a different disk.** On this
machine, `C:` and `D:` are two partitions on the *same* physical NVMe drive —
so backing up `C:` → `D:` protects against someone deleting a project, but
not against the drive dying, which is the failure backups exist for.

Check whether your drive letters are genuinely separate disks:

```powershell
Get-CimInstance Win32_LogicalDiskToPartition | ForEach-Object {
  $ld = ($_.Dependent -split '"')[1]; $pt = ($_.Antecedent -split '"')[1]; "$ld  <-  $pt"
}
```

If both letters report the same `Disk #N`, they share one physical drive.

**Recommended order, best to worst:**

1. **Network share on another machine** — protects against everything.
2. **External USB drive** — nearly as good; cheap; unplug-and-carry offsite
   occasionally for fire/theft protection.
3. **A second physical disk in the PC** — protects against drive failure but
   not fire/theft.
4. **Another partition on the same disk** — acceptable *only as a stopgap*.
   It genuinely protects against the most *common* incidents (accidental
   deletion, a bad import, DB corruption), which is far better than nothing —
   but pair it with the periodic manual copy in Part 5 so one drive failure
   isn't total loss.

> **If local-only is where you are today:** set it up now rather than waiting
> for the perfect solution. A same-disk backup you actually have beats a
> network backup you keep meaning to configure. Then work through Part 5 to
> close the disk-failure gap.

---

# PART 1 — First-time verification (do this once, now)

## Step 1: Run the automated check

This one command checks most of what matters:

```powershell
# Network share
.\scripts\verify-resilience.ps1 -BackupDest "\\fileserver\annotation-backups"

# — or, local disk / USB —
.\scripts\verify-resilience.ps1 -BackupDest "D:\annotation-backups"
```

*(substitute your actual backup destination)*

**What you should see** — a table where everything is `OK`:

```
Resilience verification — 07/28/2026 09:14:22
================================================
[OK  ] AnnotationApp                State=Ready; LastRunTime=...; LastResult=0
[OK  ] AnnotationBackup             State=Ready; LastRunTime=...; LastResult=0
[OK  ] Service log activity         Last write: 0.3h ago (D:\annotation-data\logs\service.log)
[OK  ] Recent backup snapshot       workspace-20260728-020014.dump, 7.2h old
[OK  ] Power plan = High Performance ...
[OK  ] Sleep disabled (AC)          ...
================================================
All checks passed.
```

**If you see any `FAIL`, find it below and fix it before moving on:**

| Failed check | What it means | Fix |
|---|---|---|
| `AnnotationApp` not registered | The app isn't supervised — a crash or reboot takes it down until someone notices | `.\scripts\install-service.ps1` (as Administrator) |
| `AnnotationBackup` not registered | **No backups are running at all** | `.\scripts\schedule-backup.ps1 -Dest "\\your\share" -Keep 14` — or local: `.\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups" -Keep 14` |
| Service log activity FAIL | The supervised app isn't running, or is logging somewhere else | Check `Get-ScheduledTask -TaskName "AnnotationApp" \| Get-ScheduledTaskInfo`; look at `LastTaskResult` |
| Recent backup snapshot FAIL | Backups are registered but not landing — broken share, permissions, or disk full. **On a local destination**, the usual cause is the folder not existing or the SYSTEM account lacking write access to it | Run the backup manually (Step 2) and read the error |
| Power plan / Sleep FAIL | The PC may sleep mid-shift and drop everyone's connection | Re-run `.\scripts\install-service.ps1`, which sets these |

Re-run the verify script until it's all `OK`.

---

## Step 2: Run a backup manually and read the output

Even if Step 1 passed, run one by hand once so you know what healthy looks
like:

```powershell
# Network share
python scripts\backup.py --dest "\\fileserver\annotation-backups" --keep 14

# — or, local disk / USB —
python scripts\backup.py --dest "D:\annotation-backups" --keep 14
```

The script creates the destination folder if it doesn't exist, so a local
path needs no setup beyond picking one.

**Healthy output:**

```
Database -> \\fileserver\annotation-backups\workspace-20260728-091530.dump
Uploads  -> 12 new file(s)
Backup destination total size: 4.31 GB
```

**Things that should worry you:**

| Output | Meaning | Action |
|---|---|---|
| `WARNING: only 0.62 GB free on the volume holding DATA_DIR` | The **app's own disk** is nearly full. Uploads and saves will start failing soon | Free space on the app drive now — this is urgent, it causes data loss, not just backup failure |
| `Backup destination disk-space check FAILED` | The destination is full. **No backup was written** | Free space there, or lower `--keep` to retain fewer snapshots |
| `integrity_check failed` (SQLite only) | Your live database is corrupt | See Part 4 — do not ignore this |
| `Database backup FAILED: ... pg_dump ...` | `pg_dump` missing from PATH, or Postgres credentials/connection wrong | Confirm `pg_dump --version` works in this shell |
| Total size growing a lot every run | Uploads mirror keeps growing (nothing auto-prunes it) | Keep an eye on free space at the destination |

> **Local destinations need tighter watching.** If your backups land on the
> same physical disk as the app, every snapshot eats space the *app* also
> needs — and a full disk breaks uploads and saves, not just backups. Use a
> smaller `--keep` (5–7 rather than 14) and check free space more often
> (Part 3). A backup that fills the disk it's protecting has caused the
> outage it was meant to prevent.

---

## Step 3: Prove a backup actually restores

**This is the most important step in this document.** Everything above only
proves that files are being *written*. This proves they can be *read back*.

It is safe: it restores into a disposable scratch copy and never touches
your live database.

### If you're on SQLite

```powershell
# Network share
python scripts\restore_drill.py --dest "\\fileserver\annotation-backups" --backend sqlite

# — or, local disk / USB —
python scripts\restore_drill.py --dest "D:\annotation-backups" --backend sqlite
```

### If you're on Postgres

```powershell
# Network share
python scripts\restore_drill.py --dest "\\fileserver\annotation-backups" --backend postgres --pg-admin-url "postgresql://USER:PASSWORD@127.0.0.1:5435/postgres"

# — or, local disk / USB —
python scripts\restore_drill.py --dest "D:\annotation-backups" --backend postgres --pg-admin-url "postgresql://USER:PASSWORD@127.0.0.1:5435/postgres"
```

The drill itself always runs locally — it restores into a scratch database on
your own Postgres instance (or a temp folder for SQLite) regardless of where
the snapshot came from. Only `--dest` changes.

Fill in the user/password/port from your `.env`'s `DATABASE_URL`, but keep
`/postgres` at the end — it connects to the admin database to create a
temporary scratch one (`annotation_restore_drill`), then drops it when done.

**What you should see:**

```
Using snapshot: \\fileserver\annotation-backups\workspace-20260728-020014.dump
Creating scratch database 'annotation_restore_drill'...
Restoring snapshot into scratch database...
Dropping scratch database 'annotation_restore_drill'...
PASS: snapshot restores cleanly — 14 project(s), 3122 task(s) readable.
```

**Sanity-check the numbers.** "PASS" with `0 project(s), 0 task(s)` is a
*failure* dressed up as a pass — it means the snapshot is empty. The counts
should roughly match what you actually have in the app.

**If this fails**, your backups are not usable. Treat it as an emergency:
your data is currently protected only by the live disk. Work through Step 2's
error table, fix the cause, take a fresh backup, and re-run this drill.

---

## Step 4: Turn on health monitoring (optional but recommended)

```powershell
.\scripts\schedule-health-check.ps1
```

This polls the app every 15 minutes and writes the result to a file. It does
**not** email or message anyone — it just means "is it up?" is answerable by
looking at one file instead of waiting for an annotator to complain.

Test it immediately:

```powershell
.\scripts\health-check.ps1
```

Expected: `[OK] 2026-07-28T09:22:10 status=ok database=up`

---

## Step 5: The one thing no script can do — get a UPS

If the PC loses power mid-write, no amount of software prevents the
disruption. A basic consumer UPS (even 5–10 minutes of runtime) lets
Postgres flush its data properly and lets the machine come back cleanly.

For a machine holding 20–25 people's work, this is the cheapest meaningful
protection you can buy. Nothing in this repo substitutes for it.

---

# PART 2 — Recurring checks

Put these on a calendar. They're quick, and the whole point is catching
silent drift.

### Weekly (1 minute)

Open these two files and look at the date and `ok` field:

```powershell
# Network share
type "\\fileserver\annotation-backups\last_backup_status.json"

# — or, local disk / USB —
type "D:\annotation-backups\last_backup_status.json"

# (health status is always local)
type "D:\annotation-data\logs\last_health_status.json"
```

Expected:

```json
{
  "timestamp": "2026-07-28T02:00:14",
  "ok": true,
  "detail": "...; 12 upload(s) copied; 4.31 GB total"
}
```

**Red flags:** `"ok": false`, or a `timestamp` more than a day or two old
(means the scheduled task stopped running).

### Monthly (2 minutes)

```powershell
# Network share
.\scripts\verify-resilience.ps1 -BackupDest "\\fileserver\annotation-backups"

# — or, local disk / USB —
.\scripts\verify-resilience.ps1 -BackupDest "D:\annotation-backups"
```

Catches a task that got disabled, a share that became unreachable, or power
settings that got reset by a Windows update.

**If you're on a local-only destination**, also do the offsite copy in
Part 5 while you're here — monthly is a reasonable cadence for it.

### Quarterly (5 minutes)

Re-run the restore drill from Step 3. Backups silently rot — a share gets
remounted read-only, a credential expires, a Postgres version changes. The
drill is the only thing that catches this before you need it for real.

---

# PART 3 — Disk space: the most likely thing to bite you

Two disks matter, and they fill for different reasons.

**The app's disk (`DATA_DIR`, e.g. `D:\annotation-data`)** — grows with every
uploaded image. When it fills: uploads fail, saves fail, logs fail. This is
the one that actually loses work.

**The backup destination** — grows with every snapshot and every mirrored
upload. Old database snapshots auto-prune to your `--keep` value; **mirrored
uploads never auto-prune**. When it fills: backups stop (loudly, by design —
the script aborts rather than writing a truncated file).

Check both:

```powershell
Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}, @{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}}
```

Rule of thumb: investigate below ~10% free, act below ~5%.

### If your backups are on the same disk as the app

The two problems above collapse into one, and it gets worse rather than
better: the app and its backups now compete for the same free space, and
whichever fills it first breaks the other. Practical adjustments:

- **Lower `--keep`** (try 5–7 instead of 14). Re-register with the new value:
  `.\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups" -Keep 7`
- **Check free space weekly**, not monthly — you have less margin.
- **Watch the "Backup destination total size" line** in the backup output.
  If it's climbing steadily toward your free space, act before it lands.
- **Prune mirrored uploads by hand** if the destination is dominated by them:
  delete the whole `uploads` folder at the destination; the next backup
  re-mirrors from the live copy. Only do this immediately *after* a
  successful drill (Step 3), never when the live disk is in doubt.

**Free space right now on this machine:** `C:` ~19 GB free (359 GB used),
`D:` ~34 GB free (63 GB used) — both partitions of the same 477 GB drive.
That's enough headroom today, but it's shared headroom, not two independent
budgets.

---

# PART 4 — When something goes wrong

### The app is down

```powershell
# 1. Is it actually down?
Invoke-WebRequest http://127.0.0.1:8000/health

# 2. Is the supervisor running?
Get-ScheduledTask -TaskName "AnnotationApp" | Get-ScheduledTaskInfo

# 3. What did it say before it died?
Get-Content "D:\annotation-data\logs\service.log" -Tail 50
Get-Content "D:\annotation-data\logs\app.log" -Tail 50
```

The supervisor restarts uvicorn automatically after a crash, so a brief
outage that self-heals is expected behavior, not a bug. Repeated restarts in
`service.log` mean something is crashing on startup — read `app.log`.

**Expected after any restart:** in-progress AI detection jobs are lost
(annotators must re-run detection), and task locks clear. **Saved annotation
work is not affected** — it's committed to the database.

### `/health` says `"database": "down"`

The app is up but can't reach Postgres.

```powershell
Get-Service | Where-Object { $_.Name -like "*postgres*" }
```

Start it if stopped. If it won't start, that's a Postgres problem — check
its own logs before touching the app.

### SQLite `integrity_check failed`

Your live database is corrupt. **Do not run more backups** — you'd overwrite
good snapshots with a corrupt one (the script now blocks this, but don't
fight it).

1. Stop the app: `Stop-ScheduledTask -TaskName "AnnotationApp"`
2. Copy the corrupt DB aside (don't delete it — it may be partially recoverable)
3. Restore the most recent snapshot that passes the drill in Step 3
4. Accept the data loss between that snapshot and now
5. Restart the app and verify projects appear

### A real restore (the PC died, you're on new hardware)

> **First: where are your backups?** If they were on a network share or a USB
> drive, continue below. **If they were only on a partition of the dead PC's
> disk, and the disk itself failed, they're gone with it** — this is exactly
> the gap Part 5 exists to close. Before assuming the worst, try mounting the
> old drive in an enclosure or another machine; a Windows install can fail
> while the drive is still readable.

1. Install the app per `docs/deploy/SETUP.md`
2. Restore the database — the snapshot path is wherever your backups live:
   - **Postgres:** `pg_restore --dbname "postgresql://user:pass@host:5432/annotation" --no-owner --no-privileges "\\share\workspace-YYYYMMDD-HHMMSS.dump"`
     *(local/USB: swap in `E:\annotation-backups\workspace-...dump`)*
   - **SQLite:** copy `workspace-YYYYMMDD-HHMMSS.db` to `DATA_DIR\workspace.db`
3. **Copy `uploads/` back from the backup destination into `DATA_DIR\uploads\`**
   — annotations reference these image files and are useless without them.
   This is the step people forget.
4. `alembic upgrade head`
5. Start the app, log in, confirm projects and images both appear
6. Re-run Steps 1–4 of Part 1 on the new machine

---

# PART 5 — Closing the gap when backups are local-only

Skip this if your backups already go to another machine.

Automated local backups protect against the *common* incidents — someone
deletes a project, an import goes wrong, the database corrupts. They do not
protect against the disk dying, the PC being stolen, or a fire. Closing that
gap doesn't require a file server; it requires getting a copy **off this
machine** periodically.

Pick whichever you'll actually keep doing:

### Option A — USB drive (simplest, recommended)

Point backups at the USB drive directly, so no second step can be forgotten:

```powershell
.\scripts\schedule-backup.ps1 -Dest "E:\annotation-backups" -Keep 14
```

Leave it plugged in. The only rule: **occasionally unplug it and take it
home / to another building**, then plug it back in. A drive sitting in the
same machine survives disk failure but not fire or theft.

If the drive isn't always connected, backups will fail on the nights it's
absent — that's visible in `last_backup_status.json`, and acceptable if you
plug it in on a known schedule.

### Option B — Keep local backups, copy them off monthly

Keep the automated local backup as-is, and add a manual copy:

```powershell
# Copy the newest snapshot + the uploads mirror to a USB drive or share
robocopy "D:\annotation-backups" "E:\annotation-offsite" /E /R:2 /W:5
```

`robocopy /E` is incremental — it only transfers what changed, so repeat runs
are fast even with a large uploads mirror.

Do this on the monthly cadence from Part 2, and verify afterward:

```powershell
python scripts\restore_drill.py --dest "E:\annotation-offsite" --backend postgres --pg-admin-url "postgresql://user:pass@127.0.0.1:5435/postgres"
```

Running the drill against the *copy* is the point — it proves the thing
you'd actually reach for in a disaster is intact, not just the original.

### Option C — Cloud storage folder

If the PC has OneDrive / Google Drive / Dropbox installed, pointing backups
at the synced folder gets you offsite copies with no manual step:

```powershell
.\scripts\schedule-backup.ps1 -Dest "C:\Users\<you>\OneDrive\annotation-backups" -Keep 7
```

Two cautions: uploads can be large, so watch your cloud quota and use a
smaller `--keep`; and confirm the folder actually finishes syncing (a
"backup" stuck in a pending-upload queue is not offsite yet). Also consider
whether your organization permits annotation imagery on that service before
choosing this.

### Which to choose

| Your situation | Do this |
|---|---|
| Have a spare USB drive | **Option A** — least to remember |
| Need backups always-on but want offsite too | **Option B** |
| Already use OneDrive/Drive and data policy allows it | **Option C** |
| None of the above available yet | Keep local-only, and revisit — it's a real gap, not a solved problem |

---

# Quick reference

Replace the destination path with yours — network share, `D:\annotation-backups`,
or `E:\annotation-backups` for USB. Everything else is identical.

```powershell
# Verify everything
.\scripts\verify-resilience.ps1 -BackupDest "D:\annotation-backups"

# Backup now
python scripts\backup.py --dest "D:\annotation-backups" --keep 14

# Prove a backup restores (safe — uses a scratch copy)
python scripts\restore_drill.py --dest "D:\annotation-backups" --backend postgres --pg-admin-url "postgresql://user:pass@127.0.0.1:5435/postgres"
#   SQLite instead:
python scripts\restore_drill.py --dest "D:\annotation-backups" --backend sqlite

# Health now
.\scripts\health-check.ps1

# Status files
type "D:\annotation-backups\last_backup_status.json"
type "D:\annotation-data\logs\last_health_status.json"

# Free space (both app disk and backup disk)
Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}

# Are two drive letters actually the same physical disk?
Get-CimInstance Win32_LogicalDiskToPartition | ForEach-Object {
  $ld = ($_.Dependent -split '"')[1]; $pt = ($_.Antecedent -split '"')[1]; "$ld  <-  $pt"
}

# Copy backups offsite (Part 5, Option B)
robocopy "D:\annotation-backups" "E:\annotation-offsite" /E /R:2 /W:5

# Service control
Get-ScheduledTask -TaskName "AnnotationApp" | Get-ScheduledTaskInfo
Stop-ScheduledTask  -TaskName "AnnotationApp"
Start-ScheduledTask -TaskName "AnnotationApp"

# Logs
Get-Content "D:\annotation-data\logs\service.log" -Tail 50
Get-Content "D:\annotation-data\logs\app.log" -Tail 50
```

**Deeper background:** `.devnotes/deployment-hardening/07_RESILIENCE_IMPLEMENTATION.md`
(what's built and why), `06_RESILIENCE_PLAN.md` (the reasoning),
`03_DEPLOYMENT_GUIDE.md` (full deployment reference).
