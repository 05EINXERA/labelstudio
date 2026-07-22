# Development Guide

The practical workflow for contributing to this repo, start to finish. Each
step includes *why* — the rules make more sense (and get followed more) when
the point is clear.

---

## 1. Local setup

Prerequisites: Python 3.10+, git. A GPU is **not** required (models run on CPU,
just slower).

```powershell
git clone <repo-url>
cd labelstudio

# Create an isolated Python environment for this project
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt

# Run the server
.\venv\Scripts\uvicorn.exe main:app --reload --port 8765
```

Open http://127.0.0.1:8765/ and register a user (any username/password — it's
your local database).

**Why the venv?** It keeps this project's package versions separate from every
other Python project on your machine. Without it, upgrading a package for
another project can silently break this one.

**Why `--reload`?** Uvicorn restarts automatically when you save a Python
file, so you don't have to stop/start the server after every edit. (Frontend
files don't need a restart at all — just refresh the browser.)

Notes:

- First use of Auto-Detect / Magic Wand / Auto-Tag downloads model weights
  (hundreds of MB to several GB) into `models/`. That's expected; it happens
  once. These files are gitignored — **never commit them**.
- `workspace.db` (and `-wal`/`-shm` siblings) is your local database. It's
  gitignored. Delete it to start fresh; you'll lose local projects and users.
- The `.jwt_secret` file is auto-generated on first run so logins survive
  restarts. It must stay out of git.

---

## 2. Branching

Never commit directly to `main`. For each piece of work:

```powershell
git checkout main
git pull
git checkout -b fix/task-delete-returns-404
```

Branch names: `feat/<short-slug>`, `fix/<short-slug>`, `docs/<short-slug>`,
`refactor/<short-slug>`. Lowercase, hyphens, describes the change not the file.

**Why?** `main` should always be a version that runs. Branches let you work on
something half-broken without blocking teammates, and let a reviewer look at
exactly one change at a time.

Keep branches small — one fix or one feature. A branch that changes ten
unrelated things is nearly impossible to review well, so mistakes slip through.

---

## 3. Commits

```powershell
git add api/routers/tasks.py tests/test_tasks.py   # add the files you meant to change
git status                                          # verify nothing unexpected is staged
git commit -m "fix: return 404 when deleting a nonexistent task"
```

Format: `<type>: <imperative summary, ≤72 chars>`. Types: `feat`, `fix`,
`docs`, `refactor`, `test`, `chore`. "Imperative" means write it as a command —
"add X", "fix Y" — not "added X" or "fixes Y".

**Why the summary style?** Six months from now, `git log --oneline` is how you
find "when did delete behavior change?". A log full of "wip", "update", "fix
stuff" is useless for that.

**Why `git status` before committing?** The most common beginner accident is
committing files you didn't mean to: the database, a model weight, a debug
script. Look at the staged list every time. If you see `workspace.db`,
`*.pt`, `uploads/`, or `.jwt_secret` — stop; those must never be committed.

---

## 4. Test before pushing

Minimum bar before every push:

1. **The server starts cleanly:** `.\venv\Scripts\uvicorn.exe main:app --port 8765` with no traceback.
2. **You exercised your change in the browser** (or with a real request for API-only changes) — not just "it compiles".
3. **Run the test suite** if `tests/` covers your area: `.\venv\Scripts\python.exe -m pytest`.
4. **Skim your own diff:** `git diff main`. Remove leftover `print()`/`console.log` debugging and commented-out code.

**Why "exercise it in the browser"?** Most of this app's behavior lives in
canvas interactions and fetch calls that no current test covers. Loading the
page and clicking through your feature catches the errors a unit test can't.

If you fixed a bug, add a test that fails without your fix (see
CONVENTIONS.md § 7). **Why?** A bug that happened once will happen again the
next time someone refactors that code — unless a test is standing guard.

---

## 5. Pull requests

Push your branch and open a PR against `main`:

```powershell
git push -u origin fix/task-delete-returns-404
```

A good PR description answers three questions:

1. **What** changed (one or two sentences).
2. **Why** — the bug or need that motivated it. Link the issue if there is one.
3. **How you verified it** — "deleted a task with a bad ID, got 404; existing
   delete still works; pytest passes."

Include a screenshot or short screen recording for any UI change. **Why?** The
reviewer can judge a UI change in five seconds from a picture, versus ten
minutes checking out and running your branch.

Keep PRs under ~400 changed lines where possible. **Why?** Review quality
falls off a cliff with size; two small PRs get better review than one big one.

---

## 6. Asking for review

- Request one reviewer explicitly; "anyone" means no one.
- Say what kind of review you want: "logic check on the locking change" vs.
  "sanity pass, it's mostly renames".
- If you're unsure about something in your own PR, **say so in a comment on
  that line.** Pointing at your own doubts is a strength — it directs the
  reviewer's attention exactly where it's needed.
- When you get feedback: respond to every comment (fix it, or explain why
  not), push the fixes as new commits (don't rewrite history mid-review —
  it destroys the reviewer's ability to see what changed since their pass).

**Why review at all?** It's not gatekeeping — it's how knowledge spreads.
The reviewer learns what changed; you learn patterns you didn't know. On a
team with beginners, review is the main teaching channel.

---

## 7. After merge

```powershell
git checkout main
git pull
git branch -d fix/task-delete-returns-404
```

Delete merged branches (locally and on the remote). **Why?** Dead branches
pile up fast and make it hard to see what's actually in flight.

---

## Quick reference: things that must never be pushed

| Thing | Why |
|---|---|
| `workspace.db*` | Your local data; clobbers everyone's assumptions, bloats the repo |
| `models/**/*.pt`, `*.onnx` | Hundreds of MB — GitHub rejects them and the repo history is poisoned even after deleting |
| `uploads/` | User images, potentially private, unbounded size |
| `.jwt_secret`, API keys, passwords | Anyone with repo access can forge logins. If a secret lands in a commit, **rotating it is the only fix** — deleting the file later does not remove it from git history |
| `print()` debugging left in code | Noise in production logs drowns out real errors |
