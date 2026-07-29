# Deployment Documentation Index

All setup guides, configurations, and deployment instructions for the annotation workspace.

## 🚀 Quick Start (Pick One)

| Guide | Time | Use Case |
|---|---|---|
| [QUICKSTART.md](QUICKSTART.md) | 2 min | Overview of all three paths |
| [SETUP.md](SETUP.md) | 10 min | Full walkthrough with detailed steps |
| [SETUP_ORDER.md](SETUP_ORDER.md) | 20 min | **After deploying:** install supervision + backups in the right order (do this before the runbook) |
| [RESILIENCE_RUNBOOK.md](RESILIENCE_RUNBOOK.md) | 30 min | **Then:** verify backups/crash-recovery actually work, prove a restore, and know what to do when things break |

## 📋 Scenario-Specific Guides

Choose based on your deployment:

### Scenario 1: Local Dev (Solo Annotator)
- **Guide:** [SETUP.md § Quick Start](SETUP.md#quick-start-local-development)
- **Checklist:** [DEPLOYMENT_CHECKLIST.md § Scenario 1](DEPLOYMENT_CHECKLIST.md#-scenario-1-local-dev-solo-annotator)
- **Time:** 30 seconds
- **Database:** SQLite (file)
- **Command:** `.\scripts\run.ps1`

### Scenario 2: LAN Deployment (Team of 20–30)
- **Guide:** [SETUP.md § Deployment to LAN](SETUP.md#deployment-to-lan)
- **Checklist:** [DEPLOYMENT_CHECKLIST.md § Scenario 2](DEPLOYMENT_CHECKLIST.md#-scenario-2-lan-deployment-team-of-2030)
- **Time:** 10–15 minutes
- **Database:** PostgreSQL (shared)
- **Command:** `.\scripts\run.ps1` (after `.env` setup)

### Scenario 3: Wildcard CORS (Dev Testing)
- **Guide:** [SETUP.md § CORS & Cross-Origin Requests](SETUP.md#cors--cross-origin-requests)
- **Checklist:** [DEPLOYMENT_CHECKLIST.md § Scenario 3](DEPLOYMENT_CHECKLIST.md#-scenario-3-development--wildcard-cors)
- **Use:** Testing separate frontend (React, etc.)
- **⚠️ Warning:** Dev-only (insecure for production)

## 🔧 Configuration

| File | Purpose |
|---|---|
| [.env](.env) | **Active configuration** — edit this for your deployment |
| [.env.example](.env.example) | Safe template copy (can commit) |
| [scripts/run.ps1](scripts/run.ps1) | Launcher: reads `.env` and starts uvicorn |

### How Configuration Works

1. `scripts/run.ps1` reads `.env` and sets environment variables
2. `config.py` reads those variables and applies defaults
3. In **development** mode (`APP_ENV=development`): permissive (no JWT required, wildcard CORS allowed)
4. In **production** mode (`APP_ENV=production`): strict (fails on unsafe config)

See [.env](.env) for all settings and examples.

## 📚 Deep Dives

| Document | Topic |
|---|---|
| [.devnotes/CORS_SETUP_REFERENCE.md](.devnotes/CORS_SETUP_REFERENCE.md) | CORS security, three scenarios, decision tree |
| [.devnotes/deployment-hardening/03_DEPLOYMENT_GUIDE.md](.devnotes/deployment-hardening/03_DEPLOYMENT_GUIDE.md) | Detailed LAN deployment (existing guide) |
| [.devnotes/SETUP_SUMMARY.html](.devnotes/SETUP_SUMMARY.html) | Visual reference (HTML, responsive light/dark) |

## 💡 Common Tasks

### Run locally (30 seconds)
```powershell
.\scripts\run.ps1
# → http://localhost:8765/
```

### Deploy to LAN (15 minutes)
```powershell
# 1. Edit .env (see SETUP.md § Deployment to LAN)
# 2. Start Postgres (see SETUP.md § Step 2)
# 3. Run migrations: alembic upgrade head
# 4. Create accounts: python scripts/create_user.py alice
# 5. Start: .\scripts\run.ps1
```

### Allow all origins (dev testing only)
```powershell
# Edit .env:
APP_ENV = "development"          # Must be development (not production)
CORS_ORIGINS = "*"               # Wildcard (dev-only)

# Run:
.\scripts\run.ps1
```

⚠️ **Important:** Wildcard CORS is **development-only**. Production mode forbids it. See [CORS_SETUP_REFERENCE.md](.devnotes/CORS_SETUP_REFERENCE.md) for security implications.

### Check health
```powershell
Invoke-WebRequest http://192.168.1.81:8000/health
```

### Reset password
```powershell
python scripts/create_user.py alice --reset
```

## 🎯 Three CORS Options

| Option | CORS_ORIGINS | APP_ENV | Use Case |
|---|---|---|---|
| **Same-Origin** (recommended) | `""` (empty) | any | Frontend and backend at same address |
| **Exact Origins** (secure) | `"http://192.168.1.81:8000"` | `production` | Multi-host setup, LAN deployment |
| **Wildcard** (dev-only) | `"*"` | `development` | Testing separate frontend (lab only) |

**Production rule:** Wildcard is **forbidden**. Use exact origins or same-origin only.

See [CORS_SETUP_REFERENCE.md](.devnotes/CORS_SETUP_REFERENCE.md) for full security implications.

## ✨ Security Features

- ✓ Same-origin by default (most secure)
- ✓ Exact CORS origins supported
- ✓ Wildcard CORS allowed **only** in development mode
- ✓ Production validation: fails fast on unsafe config
- ✓ Session cookies: `SameSite=strict`, `httpOnly`
- ✓ CSRF protection: double-submit tokens
- ✓ JWT_SECRET required in production

## 📖 Related Documentation

- [CLAUDE.md](CLAUDE.md) — Project instructions & architecture
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — System design
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md) — Code standards
- [docs/DEVELOPMENT_GUIDE.md](docs/DEVELOPMENT_GUIDE.md) — Development workflow

## 🆘 Troubleshooting

**"Refusing to start with an unsafe production configuration"**
→ See [SETUP.md § Troubleshooting](SETUP.md#troubleshooting)

**Session cookies not working**
→ Check `COOKIE_SECURE` setting (see [.env](.env))

**Annotators can't connect from LAN**
→ See [SETUP.md § Troubleshooting](SETUP.md#troubleshooting)

**CORS-related errors**
→ See [CORS_SETUP_REFERENCE.md](.devnotes/CORS_SETUP_REFERENCE.md)

---

**Start here:** [QUICKSTART.md](QUICKSTART.md)  
**Then read:** [SETUP.md](SETUP.md)
