# CORS & Deployment Configuration Quick Reference

## Three Setup Paths

### Path 1: Local Development (Default) ✅

**What it is:** Single machine, development mode. Annotators access the app at the same address the backend runs on.

```powershell
# .env
APP_ENV           = "development"
APP_HOST          = "127.0.0.1"          # Local machine only
APP_PORT          = "8765"
CORS_ORIGINS      = ""                   # Empty = same-origin only (no CORS middleware needed)
ALLOW_REGISTRATION = "1"                 # Anyone can sign up
JWT_SECRET        = (auto-generated)     # Generated on first run
DATABASE_URL      = (SQLite default)
```

**How to run:**
```powershell
.\scripts\run.ps1
→ http://localhost:8765/
```

**Pros:**
- No config needed (uses defaults)
- Secure by default (same-origin only)
- Single file database, easy to reset

**Cons:**
- Only one machine can run it
- No multi-user capability

---

### Path 2: LAN Deployment (Recommended for Teams) ✅

**What it is:** Multiple annotators on the office network. App runs on a shared server. Exact origin specified.

```powershell
# .env
APP_ENV           = "production"                                   # Fail-fast validation
APP_HOST          = "0.0.0.0"                                      # Listen on all interfaces
APP_PORT          = "8000"
CORS_ORIGINS      = "http://192.168.1.81:8000"                    # Exact origin (no wildcard!)
ALLOW_REGISTRATION = "0"                                           # Operator creates accounts
JWT_SECRET        = "abc123...xyz"                                 # Generated once, kept stable
DATABASE_URL      = "postgresql+psycopg://annot:pass@127.0.0.1:5432/annotation"
DATA_DIR          = "D:\annotation-data"                           # Persistent disk
```

**How to run:**
```powershell
.\scripts\run.ps1
→ http://192.168.1.81:8000/  (from LAN machines)
```

**Pros:**
- Multi-user, multi-machine
- Secure CORS (exact origin only)
- Postgres for shared state
- Production-grade validation

**Cons:**
- More setup (Postgres, accounts, backups)
- Requires stable IP address
- TLS still deferred

---

### Path 3: Development with Wildcard CORS ⚠️ (NOT for Production)

**What it is:** Any origin can call your API. Development/lab testing only.

```powershell
# .env
APP_ENV           = "development"        # ⚠️  ONLY in development!
APP_HOST          = "127.0.0.1"
APP_PORT          = "8765"
CORS_ORIGINS      = "*"                  # ⚠️  Wildcard (dangerous with cookies)
ALLOW_REGISTRATION = "1"
JWT_SECRET        = (auto-generated)
DATABASE_URL      = (SQLite default)
```

**How to run:**
```powershell
.\scripts\run.ps1
→ http://localhost:8765/
   (any website on the network can now call your API with your session)
```

**⚠️ Security Notes:**
- Session cookies (httpOnly) are sent to ANY origin that requests them
- An attacker's page on the same network could impersonate users
- Mitigated by `SameSite=strict`, but not fully prevented
- **Never use in production** — config.py explicitly forbids it

**Why would you do this?**
- Testing a separate frontend (React dev server, etc.) during development
- Lab environment, no production data
- Temporary testing before full CORS setup

**Production:** If you try `APP_ENV=production` with `CORS_ORIGINS="*"`, the app will refuse to start with a clear error message.

---

## Comparison Table

| Aspect | Local Dev | LAN Deploy | Wildcard Dev |
|---|---|---|---|
| **Access** | http://localhost:8765/ | http://192.168.1.81:8000/ | http://localhost:8765/ |
| **Database** | SQLite (single file) | Postgres (shared) | SQLite (single file) |
| **Multi-user** | No | Yes | No |
| **CORS** | Same-origin (empty) | Exact origin | Wildcard * |
| **Registration** | Open (`1`) | Closed (`0`) | Open (`1`) |
| **APP_ENV** | `development` | `production` | `development` |
| **Validation** | Permissive | Strict (fail-fast) | Permissive |
| **Suitable for** | 1 dev on 1 machine | ~20–30 annotators on LAN | Frontend dev/testing |

---

## How to Switch Between Paths

### From Local Dev → LAN Deploy

1. Edit `.env` (see template in `SETUP.md`)
2. Set `APP_ENV = "production"`, `APP_HOST = "0.0.0.0"`, etc.
3. Run migrations: `alembic upgrade head`
4. Create accounts: `python scripts/create_user.py alice`
5. Start: `.\scripts\run.ps1`

### From Local Dev → Dev with Wildcard

1. Edit `.env`:
   ```powershell
   CORS_ORIGINS = "*"
   ```
2. Run: `.\scripts\run.ps1`

(No other changes needed.)

---

## About Cookies & CSRF

All three paths use the same security model:

- **Session cookie** (httpOnly, SameSite=strict): stores the JWT
- **CSRF token** (readable, SameSite=strict): sent in `X-CSRF-Token` header on mutations

On **same-origin** (path 1), CORS middleware isn't needed — the browser allows the request by default.

On **different origins** (path 2 with proper CORS, or path 3 with wildcard):
- CORS middleware allows the cross-origin request
- SameSite=strict + CSRF token prevent most CSRF attacks
- But in path 3 (wildcard), an attacker's page can still trigger requests with the user's session

**Why production forbids wildcard:** Because wildcard + credentials (cookies) is fundamentally risky in the cross-domain model. Exact origins (path 2) let you grant permission only to servers you control.

---

## Quick Decision Tree

**"What should I use?"**

1. **Solo dev on one machine?**
   → Path 1 (Local Dev). Defaults work fine; just run `.\scripts\run.ps1`.

2. **20–30 annotators on the office LAN?**
   → Path 2 (LAN Deploy). Follow `SETUP.md` steps 1–6.

3. **Testing a separate frontend (React, etc.)?**
   → Path 3 (Wildcard Dev). Edit `.env`, set `CORS_ORIGINS="*"`, run.

4. **Production?**
   → Path 2 only. Wildcard is forbidden. Single-origin is safest.

---

See [SETUP.md](../SETUP.md) for detailed instructions for each path.
