# 🚀 Quick Start

Three ways to run the annotation app. Pick one and go.

---

## 🏃 Option 1: Local Dev (30 seconds)

**Just want to annotate images on your laptop?**

```powershell
# Activate Python environment
.\venv\Scripts\Activate.ps1

# Run the app
.\scripts\run.ps1
```

Then open **http://localhost:8765/** and start annotating.

**What you get:**
- SQLite database (file-based, disposable)
- Self-registration (anyone can sign up)
- Single-user only

---

## 👥 Option 2: Team on LAN (10 minutes)

**Deploy to a shared server for ~20–30 annotators?**

### Step 1: Edit `.env`

```powershell
APP_ENV       = "production"
APP_HOST      = "0.0.0.0"
APP_PORT      = "8000"
JWT_SECRET    = "abc123...xyz"  # Generate: python -c "import secrets; print(secrets.token_hex(32))"
CORS_ORIGINS  = "http://192.168.1.81:8000"
ALLOW_REGISTRATION = "0"
DATABASE_URL  = "postgresql+psycopg://annot:password@127.0.0.1:5432/annotation"
DATA_DIR      = "D:\annotation-data"
```

### Step 2: Start Postgres

```powershell
docker run -d --name annotation-db `
  -e POSTGRES_USER=annot -e POSTGRES_PASSWORD=MyPassword123 -e POSTGRES_DB=annotation `
  -p 127.0.0.1:5432:5432 `
  -v annotation-pgdata:/var/lib/postgresql/data `
  postgres:16
```

### Step 3: Migrate & Create Accounts

```powershell
pip install -r requirements.txt
alembic upgrade head
python scripts/create_user.py alice
python scripts/create_user.py bob
```

### Step 4: Run

```powershell
.\scripts\run.ps1
```

Annotators connect at: **http://192.168.1.81:8000/**

---

## 🔬 Option 3: Wildcard CORS (Dev Testing)

**Testing a separate frontend (React dev server)?**

⚠️ **Development only** — never in production.

```powershell
# Edit .env
CORS_ORIGINS = "*"
APP_ENV = "development"

# Run
.\scripts\run.ps1
```

Your React server at http://localhost:3000 can now call the backend.

---

## 📚 Full Documentation

| Document | Use When |
|---|---|
| **SETUP.md** | You need step-by-step instructions for any option |
| **DEPLOYMENT_CHECKLIST.md** | You're deploying to production / team |
| **CORS_SETUP_REFERENCE.md** | You need to understand CORS & cross-origin setup |
| **deployment-hardening/03_DEPLOYMENT_GUIDE.md** | You want deep hardening/security details |

---

## ⚡ Common Commands

```powershell
# Check if app is running
Invoke-WebRequest http://192.168.1.81:8000/health

# View logs
tail -f D:\annotation-data\logs\app.log

# Reset a password
python scripts/create_user.py alice --reset

# Restart (if running as NSSM service)
nssm restart AnnotationWorkspace
```

---

## 🎯 Decision: Which Option?

**Solo dev?** → Option 1  
**Team on office LAN?** → Option 2  
**Testing separate frontend?** → Option 3

See [SETUP.md](SETUP.md) for full details.
