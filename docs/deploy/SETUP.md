# Annotation Workspace — Setup Guide

Quick start for running the app locally and deploying to a LAN.

## Quick Start (Local Development)

The app runs with sensible development defaults. Start it like this:

```powershell
# Activate the virtual environment (if needed)
.\venv\Scripts\Activate.ps1

# Run with development config (from .env, or defaults)
.\scripts\run.ps1
```

Then open http://localhost:8765/ in your browser. You can sign up and start annotating right away.

### What happens at startup

- `scripts/run.ps1` reads `.env` and sets environment variables
- `config.py` reads those variables and sets defaults
- In **development mode** (`APP_ENV=development`), unsafe config is permitted:
  - No JWT_SECRET required (a dev default is generated)
  - CORS_ORIGINS can be empty (same-origin only) or a wildcard
  - Self-registration is open (anyone can sign up)
- In **production mode** (`APP_ENV=production`), config is validated at startup — missing or invalid settings cause a fatal error message, not silent misconfiguration

---

## Configuration

Configuration comes from environment variables. The `.env` file is a convenience template; it's not automatically loaded by the app. Instead:

- **`scripts/run.ps1`** loads `.env` and sets environment variables before launching uvicorn
- You can also set variables in the shell directly (they override `.env`)

### Example: Change the port

```powershell
# Option 1: Edit .env and run
#   APP_PORT = "8000"
#   .\scripts\run.ps1

# Option 2: Set in shell and run
$env:APP_PORT = "8000"
.\scripts\run.ps1
```

### Full Configuration Reference

See [`.env`](.env) — it has detailed comments for every setting.

Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `production` enables strict validation |
| `APP_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for LAN) |
| `APP_PORT` | `8765` | Listen port |
| `JWT_SECRET` | *(auto-generated in dev)* | Session signing key; **required** in production |
| `CORS_ORIGINS` | *(empty)* | Comma-separated allowed origins (or empty for same-origin only) |
| `DATABASE_URL` | SQLite in `DATA_DIR` | `postgresql+psycopg://…` for shared deployment |
| `ALLOW_REGISTRATION` | `1` | `0` disables self-signup (use `scripts/create_user.py` instead) |
| `DATA_DIR` | `.` | Persistent data location |

---

## Deployment to LAN

Run the app on a server machine so ~20–30 annotators can connect over the office network.

### Step 1: Environment Setup

Edit `.env` with production settings:

```powershell
APP_ENV       = "production"          # Strict validation on startup
APP_HOST      = "0.0.0.0"             # Listen on all interfaces (LAN-facing)
APP_PORT      = "8000"                # Standard port
JWT_SECRET    = "<64-hex-chars>"      # Generate: python -c "import secrets; print(secrets.token_hex(32))"
CORS_ORIGINS  = "http://192.168.1.81:8000"   # Your LAN address (exact, no wildcard)
ALLOW_REGISTRATION = "0"              # Operator creates accounts
DATABASE_URL  = "postgresql+psycopg://annot:<password>@127.0.0.1:5432/annotation"  # If using Postgres
DATA_DIR      = "D:\annotation-data"  # Persistent disk
```

**Key points:**
- `JWT_SECRET`: Generate once and keep it stable. Changing it signs everyone out.
- `CORS_ORIGINS`: Must be the exact address annotators use (no wildcard allowed in production).
- `ALLOW_REGISTRATION`: Set to `"0"` on shared deployments (prevents unauthorized signup).
- `DATABASE_URL`: Leave empty for SQLite (single-server), or use Postgres for scalability.

### Step 2: Database Setup

#### SQLite (simple, single-server)

No setup needed. The database is created at `$DATA_DIR/workspace.db`.

#### PostgreSQL (shared, recommended for LAN)

Install Postgres and create a container (or standalone):

```powershell
docker run -d --name annotation-db --restart unless-stopped `
  -e POSTGRES_USER=annot -e POSTGRES_PASSWORD=<password> -e POSTGRES_DB=annotation `
  -p 127.0.0.1:5432:5432 `
  -v annotation-pgdata:/var/lib/postgresql/data `
  postgres:16
```

Then install the driver:

```powershell
pip install -r requirements.txt
```

### Step 3: Run Database Migrations

```powershell
alembic upgrade head
```

This creates the schema. (Do NOT rely on `Base.metadata.create_all` — it only creates missing tables, never alters existing ones.)

### Step 4: Create Annotator Accounts

Since registration is disabled (`ALLOW_REGISTRATION=0`), create accounts manually:

```powershell
python scripts/create_user.py alice
python scripts/create_user.py bob
# To reset a forgotten password:
python scripts/create_user.py alice --reset
```

(Passwords are prompted for, never passed as arguments.)

### Step 5: Run the App

```powershell
.\scripts\run.ps1
```

Or manually:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

**Important:** Exactly one worker. The AI job queue and loaded models are in-process state; `--workers N` silently breaks job status polling.

### Step 6: Test Connectivity

From another machine on the LAN:

```
http://192.168.1.81:8000/health
→ {"status":"ok","database":"up",...}
```

### Step 7 (Optional): Windows Firewall

Allow inbound TCP traffic once:

```powershell
New-NetFirewallRule -DisplayName "Annotation Workspace" `
  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

Keep it on the `Private` profile so the app doesn't expose if the laptop joins a public network.

### Step 8 (Optional): NSSM Service

Run the app as a Windows service so it survives crashes and reboots:

```powershell
# Install
nssm install AnnotationWorkspace "D:\ai\projects\annotation\labelstudio\venv\Scripts\python.exe"
nssm set AnnotationWorkspace AppParameters "-m uvicorn main:app --host 0.0.0.0 --port 8000"
nssm set AnnotationWorkspace AppDirectory "D:\ai\projects\annotation\labelstudio"
nssm set AnnotationWorkspace AppEnvironmentExtra APP_ENV=production JWT_SECRET=<secret> CORS_ORIGINS=http://192.168.1.81:8000 ...

# Start
nssm start AnnotationWorkspace

# Check status
nssm status AnnotationWorkspace

# View logs
nssm edit AnnotationWorkspace  # Configure stderr/stdout redirection
```

### Step 9 (Optional): Daily Backups

Schedule a daily database + file backup to another machine:

```powershell
python scripts/backup.py --dest \\fileserver\annotation-backups --keep 14
```

Register with Task Scheduler:

```powershell
schtasks /create /tn "AnnotationBackup" /tr "D:\...\venv\Scripts\python.exe D:\...\scripts\backup.py --dest \\fileserver\annotation-backups" /sc daily /st 19:00
```

---

## CORS & Cross-Origin Requests

By default, the app serves the frontend and API at the **same origin** (e.g., http://192.168.1.81:8000/). This is the simplest and most secure setup — leave `CORS_ORIGINS` empty.

### When to use CORS

You need CORS if:
- Frontend is served from a different host (e.g., a CDN at https://annotation.company.com)
- API runs on a different port or hostname
- Reverse proxy is in front (Nginx, Caddy, etc.)

### How to enable CORS

Set `CORS_ORIGINS` to the exact origin(s):

```powershell
# One origin
CORS_ORIGINS = "https://annotation.company.com"

# Multiple origins (comma-separated)
CORS_ORIGINS = "http://localhost:3000,https://annotation.company.com"
```

**Important:**
- ✅ Exact origins only (no wildcards in production)
- ✅ Must include scheme + host + port (e.g., `https://example.com:443`)
- ❌ Wildcard `*` is fatal in production mode — it's insecure with session cookies

### Development: Allow All Origins

If you're testing locally and want to allow **any** origin, you can use a wildcard — but **only in development**:

```powershell
# .env
APP_ENV = "development"      # NOT production
CORS_ORIGINS = "*"           # Wildcard allowed in development only
```

**Why is this risky?**
- Session cookies (httpOnly) are sent to any page that calls your API
- An attacker's page on the same network could impersonate the user
- The app relies on `SameSite=strict` to mitigate this

**Production:** Never use `*` in production. The app will refuse to start and print a clear error message.

---

## Common Tasks

### Check if the app is running

```powershell
Invoke-WebRequest http://192.168.1.81:8000/health
```

### View logs

```powershell
tail -f $env:DATA_DIR\logs\app.log
```

(Or open `D:\annotation-data\logs\app.log` in an editor.)

### Restart the service (NSSM)

```powershell
nssm restart AnnotationWorkspace
```

### Reset a forgotten password

```powershell
python scripts/create_user.py alice --reset
```

### Export data

From the frontend: click **Project > Export** to download annotations as JSON, COCO, YOLO, etc.

### Restore from backup

1. Copy a backup to `D:\annotation-data` (or wherever)
2. Point `DATA_DIR` at it
3. Start the app
4. Verify projects appear

---

## Troubleshooting

### "Refusing to start with an unsafe production configuration"

You set `APP_ENV=production` but one or more config values are invalid. Check the error message — it lists all problems:

- Missing `JWT_SECRET`: generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- `CORS_ORIGINS` is empty: set it to your exact origin
- `CORS_ORIGINS` contains `*`: remove the wildcard (only allowed in development)

### "Cannot find module 'psycopg'"

If using Postgres, install the driver:

```powershell
pip install -r requirements.txt
```

### Session cookies not being sent over HTTP

If `COOKIE_SECURE=1` but you're using plain HTTP (no TLS), the browser won't send the cookie. Change `.env`:

```powershell
COOKIE_SECURE = "0"
```

(This is the default. Only set to `1` once TLS is in front.)

### Annotators can't connect from the LAN

- Check the server's firewall (see "Windows Firewall" section above)
- Verify the IP address matches `CORS_ORIGINS` if set
- Run `/health` from another machine to test connectivity

---

## For More Details

- [Deployment Hardening Guide](`.devnotes/deployment-hardening/03_DEPLOYMENT_GUIDE.md`) — detailed LAN deployment walkthrough
- [Architecture](docs/ARCHITECTURE.md) — system design
- [Code Conventions](docs/CONVENTIONS.md) — development rules
- [CLAUDE.md](CLAUDE.md) — project instructions for AI assistants
