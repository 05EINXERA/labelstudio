# Development Instance

How to develop without touching the live LAN deployment, and how to deploy once
you are done.

---

## Why this exists

The production server runs uvicorn **out of the repository working tree** and
serves `frontend/` straight off disk via `StaticFiles`. Two consequences that
are easy to miss:

- **Saving a frontend file changes the live site.** There is no build step and
  no `--reload` on production, so Python edits sit dormant until a restart — but
  a saved `.js` or `.css` reaches the next annotator who hard-reloads. A
  half-finished module ships itself.
- **`git checkout` repoints the running server.** Switching branches in the
  production tree swaps the files uvicorn is serving from underneath ~25 people.

So development gets its own everything.

---

## The four boundaries

| Boundary | Production | Development | Why it matters on its own |
|---|---|---|---|
| Filesystem | `…/labelstudio` | `…/labelstudio-dev` (git worktree) | Edits and branch switches stop being live |
| Database | `annotation` | `annotation_dev` | **Alembic upgrade/downgrade cycles never touch production data** |
| `DATA_DIR` | `D:/annotation-data` | `D:/annotation-data-dev` | Dev uploads and logs stay out of the real ones |
| Network | `0.0.0.0:8000` | `127.0.0.1:8001` | Unfinished code is unreachable from the LAN by construction |

The **database** boundary is the sharp one. The Teams feature adds four
migrations, and `.devnotes/teams/TASKS.md` T1.3 asks for an `upgrade head` then
`downgrade -3` cycle. Against `annotation` that drops columns while annotators
are working. Against `annotation_dev` it is a no-op you can repeat all day.

A shared `venv` is fine — dependencies are identical, and it lives outside both
trees so neither can clobber it.

---

## Setup (once)

Run from the **production** tree. The script is idempotent, read-only towards
production, and never restarts the server:

```powershell
cd D:\ai\projects\annotation\labelstudio
.\scripts\setup-dev-instance.ps1
```

It creates the worktree, the `annotation_dev` database, `D:/annotation-data-dev`,
and a dev `.env` derived from production's with only the isolation keys changed.

To develop against realistic data instead of empty tables:

```powershell
.\scripts\setup-dev-instance.ps1 -SeedFromProd
```

`pg_dump` is an online read — it does not lock or block the live server — and
uploads are mirrored with `robocopy /E`, which only ever adds. Worth doing: the
permission resolver's cost is only honest against the real project with 6,332
annotations, not three fixtures.

---

## Daily use

```powershell
cd D:\ai\projects\annotation\labelstudio-dev
.\scripts\run-dev.ps1
```

Then open **http://127.0.0.1:8001/**.

`run-dev.ps1` applies pending migrations, then starts uvicorn with `--reload`
(safe here — this process serves nobody but you; **never** add it to `run.ps1`).

Useful switches:

| Switch | Effect |
|---|---|
| `-NoReload` | No auto-restart. For profiling, or when editor autosave causes churn |
| `-SkipMigrations` | Start without `alembic upgrade head`. For testing a deliberately downgraded schema |

### The guards

`run-dev.ps1` refuses to start if:

- **`APP_HOST` is not loopback.** Binding `0.0.0.0` would publish unfinished
  code to the LAN. It is a one-character edit away, so it is enforced, not
  documented.
- **`DATABASE_URL` equals production's.** Read live from the production `.env`
  rather than hardcoded, so renaming the production database keeps the check
  honest.
- **The port is already in use.** Usually a dev instance you forgot was running.

If a guard fires, fix the `.env` — do not weaken the guard.

---

## Branches

The production tree holds `feat/teams`; the dev worktree holds `feat/teams-dev`.
Git will not check out the same branch in two worktrees, and that restriction is
useful here — it is what stops the two trees drifting onto the same ref.

Work and commit in the dev worktree. Both trees share one `.git`, so commits,
branches and fetches are the same repository. `git worktree list` shows both.

---

## Deploying to production

Development is now separate, so deploying is an explicit sequence rather than
"the files are already there." Run on the production PC:

```powershell
# 1. Back up FIRST, and confirm the backup is readable.
python scripts\backup.py --dest <backup-destination>

# 2. Tell annotators to stop and close their tabs (drafts survive, but a
#    restart mid-save is avoidable noise).

# 3. Update the production tree.
cd D:\ai\projects\annotation\labelstudio
git pull

# 4. Apply migrations.
venv\Scripts\python.exe -m alembic upgrade head

# 5. Restart the service (or the run.ps1 process).

# 6. Confirm.
curl http://127.0.0.1:8000/health

# 7. Tell annotators to HARD RELOAD (Ctrl+Shift+R).
```

**Step 7 is not optional.** Asset cache-busting is manual `?v=N`
(deferred item D4), so a stale bundle keeps running old code against the new
API. After the Teams change that means an old `project-nav.js` rendering nav
items the new API rejects with 403 — see `.devnotes/teams/06_EDGE_CASES.md`
E-17.

The Postgres restore drill (`.devnotes/deployment-hardening/tasks.md` T6.3) has
**still never been run against real Postgres**. Step 1's "confirm the backup is
readable" is doing real work — do not skip it before the first schema change
since `c2d8f1a390bb`.

---

## Tearing down

```powershell
cd D:\ai\projects\annotation\labelstudio
git worktree remove ..\labelstudio-dev
dropdb -h 127.0.0.1 -p 5435 -U annot annotation_dev
Remove-Item -Recurse D:\annotation-data-dev
```

Only do this once the work is merged — the worktree may hold unpushed commits.

---

## Troubleshooting

**"Port 8001 already in use"** — a previous dev instance is still running.
`Get-NetTCPConnection -LocalPort 8001 -State Listen` gives the PID.

**Dev shows no projects after `-SeedFromProd`** — the dump restored but you are
logged in as a different user. Project visibility is owner-scoped; log in with
the seeded account's credentials.

**Task images 404 in dev** — `uploads/` was not mirrored. Re-run with
`-SeedFromProd`, or copy `D:/annotation-data/uploads` to
`D:/annotation-data-dev/uploads`.

**Migration fails in dev** — that is the boundary working. Fix it here; it would
have been an outage in production.

**Accidentally started production's `run.ps1` from the dev tree** — it would
read the dev `.env` and bind 127.0.0.1:8001, so no harm done. The reverse
(production `.env` in the dev tree) is what the `run-dev.ps1` guards catch.
