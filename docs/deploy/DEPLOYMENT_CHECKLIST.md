# Deployment Checklist

Complete one checklist below, depending on your scenario.

---

## 🏠 Scenario 1: Local Dev (Solo Annotator)

Just want to annotate some images on your laptop? This is the fastest path.

### Checklist

- [ ] Activate venv: `.\venv\Scripts\Activate.ps1`
- [ ] Run the app: `.\scripts\run.ps1`
- [ ] Open http://localhost:8765/ in browser
- [ ] Click **Sign Up** and create an account
- [ ] Create a project, upload images, start annotating ✓

**That's it.** The app uses development defaults (SQLite, self-registration, no CORS middleware).

---

## 🏢 Scenario 2: LAN Deployment (Team of 20–30)

Roll out the app to a shared server so your team can annotate together over the network.

### Prerequisites

- [ ] One machine to serve the app (Windows or Linux, must stay on for annotators)
- [ ] Postgres installed (or Docker for container)
- [ ] Python 3.9+ with dependencies installed

### Configuration

- [ ] Edit `.env`:
  ```powershell
  APP_ENV = "production"
  APP_HOST = "0.0.0.0"
  APP_PORT = "8000"
  JWT_SECRET = "<64-hex-chars>"          # Generate: python -c "import secrets; print(secrets.token_hex(32))"
  CORS_ORIGINS = "http://192.168.1.81:8000"   # Your LAN IP:port (exact, no wildcard)
  ALLOW_REGISTRATION = "0"               # Operator creates accounts
  DATABASE_URL = "postgresql+psycopg://annot:password@127.0.0.1:5432/annotation"
  DATA_DIR = "D:\annotation-data"        # Persistent disk (backup location)
  ```

### Database

- [ ] Start Postgres container (or connect to existing instance)
  ```powershell
  docker run -d --name annotation-db --restart unless-stopped `
    -e POSTGRES_USER=annot -e POSTGRES_PASSWORD=MyPassword123 -e POSTGRES_DB=annotation `
    -p 127.0.0.1:5432:5432 `
    -v annotation-pgdata:/var/lib/postgresql/data `
    postgres:16
  ```

- [ ] Install Python driver: `pip install -r requirements.txt`

- [ ] Run migrations: `alembic upgrade head`

### User Accounts

- [ ] Create annotator accounts (registration is disabled):
  ```powershell
  python scripts/create_user.py alice
  python scripts/create_user.py bob
  ```

### Launch

- [ ] Run the app: `.\scripts\run.ps1`

- [ ] Verify `/health` from another machine:
  ```powershell
  Invoke-WebRequest http://192.168.1.81:8000/health
  → {"status":"ok","database":"up",...}
  ```

### Firewall (Windows)

- [ ] Allow inbound TCP on port 8000:
  ```powershell
  New-NetFirewallRule -DisplayName "Annotation Workspace" `
    -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
  ```

### Optional: Background Service

Keep the app running after reboot — no NSSM needed, this ships in the repo:

- [ ] Run once as Administrator: `.\scripts\install-service.ps1`

  Registers a Task Scheduler job (`AnnotationApp`) that wraps uvicorn in a
  self-restarting loop, triggers `-AtStartup`, and disables sleep/hibernate.

- [ ] Check status: `Get-ScheduledTask -TaskName "AnnotationApp" | Get-ScheduledTaskInfo`

### Optional: Daily Backups

Schedule incremental backups to another machine:

- [ ] Register with Task Scheduler (defaults to 2 AM daily):
  ```powershell
  .\scripts\schedule-backup.ps1 -Dest "\\fileserver\annotation-backups" -Keep 14
  ```

  `scripts/backup.py` snapshots the DB (SQLite: online-backup API preceded by
  a `PRAGMA integrity_check`; Postgres: `pg_dump`), mirrors new uploads,
  aborts cleanly if destination disk space is low, and writes
  `last_backup_status.json` next to the backup.

### Optional but recommended: Verify supervision + backups, and prove restore works

- [ ] `.\scripts\verify-resilience.ps1 -BackupDest "\\fileserver\annotation-backups"`
      — checks the scheduled tasks are actually registered and `Ready`, the
      service log is recent, a backup landed in the last day, and sleep is
      disabled. Don't just trust the install scripts ran cleanly.
- [ ] `python scripts/restore_drill.py --dest \\fileserver\annotation-backups --backend sqlite` (or `--backend postgres`)
      — restores the latest snapshot into a disposable scratch DB and
      confirms the data is actually readable. Run at least once; a backup
      nobody has ever restored is not a proven recovery capability.
- [ ] Consider `.\scripts\schedule-health-check.ps1` for a lightweight
      `/health` poll with its own status file.

**➡️ Full step-by-step walkthrough: [RESILIENCE_RUNBOOK.md](RESILIENCE_RUNBOOK.md)**
— expected output for each command, how to read a failure, the recurring
checks to calendar, and recovery procedures. Deeper background in
`.devnotes/deployment-hardening/06_RESILIENCE_PLAN.md` /
`07_RESILIENCE_IMPLEMENTATION.md`.

### Communicate to Team

Once live, share:

- [ ] **Server address:** http://192.168.1.81:8000/
- [ ] **Account credentials** (email or print / give in person, NOT via Slack)
- [ ] **First-login instructions** (sign in, create a project, etc.)

---

## 🔬 Scenario 3: Development + Wildcard CORS

Testing a separate frontend (React dev server, etc.)? Allow any origin temporarily.

### ⚠️ Important

- Only for **local development / lab testing**
- Never use in production — the app forbids wildcard in production mode
- Session cookies (httpOnly) will be sent to ANY origin

### Checklist

- [ ] Edit `.env`:
  ```powershell
  APP_ENV = "development"          # NOT production
  CORS_ORIGINS = "*"               # Wildcard (dev-only)
  ```

- [ ] Activate venv: `.\venv\Scripts\Activate.ps1`

- [ ] Run the app: `.\scripts\run.ps1`

- [ ] Backend now running at: http://localhost:8765/

- [ ] Start your frontend dev server (e.g., React):
  ```powershell
  npm run dev  # or whatever your frontend uses
  → Frontend at http://localhost:3000
  ```

- [ ] Frontend can now call the backend API (CORS permitted)

**When you're done testing:**

- [ ] Revert `.env` to remove the wildcard
- [ ] Remember: don't commit the wildcard CORS config to git

---

## Troubleshooting

### App fails to start with "Refusing to start with an unsafe production configuration"

You set `APP_ENV=production` but a config value is invalid. Check the error message — it lists all problems. Common fixes:

- **Missing JWT_SECRET:** Generate with `python -c "import secrets; print(secrets.token_hex(32))"`, paste into `.env`
- **CORS_ORIGINS is empty:** Set it to your exact origin, e.g., `http://192.168.1.81:8000`
- **CORS_ORIGINS contains `*`:** Remove the wildcard (only allowed in development)

### Session cookies not sent (HTTP 401)

If you see login page looping instead of staying logged in:

- Check if `COOKIE_SECURE=1` but you're using plain HTTP (no TLS)
- Fix: set `COOKIE_SECURE=0` for plain HTTP (default)
- Only set `COOKIE_SECURE=1` once TLS is in front

### "Cannot find module 'psycopg'"

Using Postgres but haven't installed the driver yet:

```powershell
pip install -r requirements.txt
```

### Annotators can't connect from the LAN

- [ ] Check Windows Firewall allows port 8000 (see above)
- [ ] Verify the IP address (`ipconfig` on the server)
- [ ] Confirm `CORS_ORIGINS` matches (or is empty for same-origin)
- [ ] Test `/health`: `Invoke-WebRequest http://<IP>:8000/health`

### "Database is locked" (SQLite only)

SQLite doesn't handle concurrent writes well. If you see this in a team:

- [ ] Migrate to Postgres (see Scenario 2)
- [ ] Or limit to <5 concurrent users and increase WAL checkpoint interval (contact maintainers)

---

## Next Steps

- **Full documentation:** See [SETUP.md](../SETUP.md)
- **CORS deep dive:** See [.devnotes/CORS_SETUP_REFERENCE.md](CORS_SETUP_REFERENCE.md)
- **Architecture:** See [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)
- **Deployment hardening:** See [.devnotes/deployment-hardening/03_DEPLOYMENT_GUIDE.md](03_DEPLOYMENT_GUIDE.md)
