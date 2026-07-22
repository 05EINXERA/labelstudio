# Gotchas — mistakes already in this code

These patterns exist in the codebase **and look normal**, so a beginner will
copy them in good faith. Each entry explains why it's wrong and what to do
instead. If you're about to imitate existing code, check this list first.

Ordered roughly by how much damage copying it would cause.

---

## 1. A secret file is committed to git (`.jwt_secret`)

**Where:** repo root — `git ls-files` shows `.jwt_secret` is tracked.

**Why it's bad:** this file signs login tokens. Anyone who can read the repo
(or its history, forever) can forge a valid login for any user. Deleting the
file in a later commit does **not** help — git history keeps every version.

**The fix:** `git rm --cached .jwt_secret`, add it to `.gitignore`, commit —
and then treat the old secret as burned: delete the local file so the app
generates a fresh one (this logs everyone out once; that's the point).

**The lesson to not copy:** never commit anything that would matter if a
stranger read it — secrets, tokens, API keys, real user data. If it happens,
rotating the secret is the only real fix.

---

## 2. Some API routers skip authentication

**Where:** `api/routers/tasks.py`, `data.py`, `label_studio.py` have no
`dependencies=[Depends(get_current_user)]`, while `projects.py`, `labels.py`,
`team.py`, `detect.py` do.

**Why it's bad:** an unauthenticated visitor can list, modify, and delete
every task. A beginner creating a new router by copying `tasks.py` will
inherit the hole.

**The fix:** when creating a router, copy the header line from `projects.py`:

```python
router = APIRouter(prefix="/api/things", tags=["things"],
                   dependencies=[Depends(get_current_user)])
```

Only `/api/auth` endpoints may be public. (Fixing the three existing routers
is tracked in ARCHITECTURE.md § 3.2.)

---

## 3. `localStorage['logged_in']` looks like authentication — it isn't

**Where:** `frontend/js/init.js` top: `if (!localStorage.getItem('logged_in')) window.location.href = '/'`.

**Why it's misleading:** anyone can open DevTools and run
`localStorage.setItem('logged_in','1')`. The *real* auth is the httpOnly
cookie the server sets, which JS can't touch. The localStorage flag exists
only so pages can redirect to login without waiting for a 401.

**Don't copy:** any code that *grants* access based on localStorage. UI
convenience only; the server's 401 is the enforcement.

---

## 4. Bare `except:` that swallows everything

**Where:** `api/routers/tasks.py` (`except: pass` around `json.loads`),
similar broad catches in `projects.py`.

**Why it's bad:** it doesn't just ignore corrupt JSON — it ignores *every*
error, including the typo you just wrote. Code inside that `try` can be
completely broken and you'll never see a message. This is the #1 way bugs
become invisible.

**Do instead:** catch the one exception you expect, and log it:

```python
except json.JSONDecodeError:
    logger.warning("Task %s: corrupt annotations JSON", t.id)
    annotations_data = []
```

---

## 5. An error is caught, hidden, and replaced with a vague message

**Where:** `api/routers/label_studio.py`:

```python
except Exception as e:
    raise HTTPException(status_code=500, detail="Label Studio sync failed.")
```

**Why it's bad:** `e` — the actual reason (wrong URL? bad API key? network?) —
is thrown away, never logged. Debugging becomes guesswork. Note this is a
*different* mistake from #4: here the user sees an error, but the operator
still can't diagnose it.

**Do instead:** log the real error, return the safe message:

```python
except Exception:
    logger.exception("Label Studio sync failed")
    raise HTTPException(status_code=500, detail="Label Studio sync failed.")
```

---

## 6. A GET endpoint that writes to the database

**Where:** `api/routers/projects.py` → `get_project_metrics` updates
`project.status` and commits, inside a GET handler.

**Why it's bad:** everything (browsers, caches, monitoring, a curious teammate
refreshing a page) assumes GET is read-only. Side effects on GET fire at
unpredictable times and are impossible to reason about. It also means simply
*viewing* metrics changes data.

**Do instead:** GET computes and returns. If status should update when tasks
complete, do it in the handler that *changes* the tasks.

---

## 7. `datetime.utcnow()` and naive datetimes

**Where:** `api/routers/tasks.py` (several places).

**Why it's bad:** `utcnow()` is deprecated and returns a "naive" datetime (no
timezone attached). The same file also parses client timestamps *with*
timezones, then strips them to compare. Mixing naive and aware datetimes
either crashes or silently compares wrong — the current optimistic-lock check
only works because of a careful strip-and-fudge (a 1-second tolerance).

**Do instead:** `datetime.now(timezone.utc)` everywhere, keep everything
timezone-aware end to end.

---

## 8. Imports in the middle of files and inside functions

**Where:** `projects.py` (`import schemas` at line 87, `from config import
DATA_DIR` at line 121, `import json` inside two functions), `detect.py`
(`from detector import segment_point` inside a function).

**Why it's bad:** readers (and tools) expect the top of the file to declare
everything it uses. Mid-file imports hide dependencies and usually signal
copy-paste growth. (The one legitimate use — dodging a circular import or
deferring a heavy module — deserves a comment saying so.)

**Do instead:** all imports at the top, standard library first, then
third-party, then local.

---

## 9. Delete endpoints that report success for things that don't exist

**Where:** `tasks.py` `delete_task`, `team.py` `delete_team_member` — both
return `{"status": "ok"}` whether or not anything was deleted.

**Why it's bad:** a frontend bug sending the wrong ID looks like it worked.
The user believes the item is gone; it isn't. Silent no-ops hide bugs on the
caller's side.

**Do instead:** check the row count / fetch first, and `raise
HTTPException(status_code=404, ...)` when the target doesn't exist
(`projects.py` `delete_project` gets this right — copy that one).

---

## 10. `test_*.py` files that aren't tests

**Where:** repo root — `test_upload.py` (POSTs to a live server),
`test_sam_mask.py` (loads a real SAM model).

**Why it's bad:** the `test_` prefix tells pytest "collect and run me". The
moment someone runs `pytest`, these fire real network calls / model loads and
fail or hang, making people distrust the (future) real suite. They also teach
newcomers that "a test" means "a script I run by hand".

**Do instead:** manual scripts go in `scripts/` without the `test_` prefix.
Real tests go in `tests/` and must run offline against a temp database.

---

## 11. `sync.js`: monkey-patched localStorage + synchronous XHR

**Where:** `frontend/sync.js` — replaces `localStorage.setItem` globally and
does `xhr.open('GET', '/api/data', false)` (the `false` = synchronous,
freezing the page until the server answers).

**Why it's bad:** three stacked problems: (1) synchronous XHR blocks the whole
page and is deprecated by browsers; (2) patching a built-in API means every
`localStorage.setItem` anywhere now has a hidden network side effect —
invisible spooky behavior; (3) the synced blob is one shared value for *all*
users with no conflict handling — two users overwrite each other, last writer
wins.

**Do instead:** nothing — this file is legacy (ARCHITECTURE.md § 3.6). Never
extend `SYNC_KEYS`, never copy the pattern. New features call the API
explicitly with async `fetch` via `apiFetch`.

---

## 12. The optimistic-lock check can be silently skipped

**Where:** `tasks.py` update path — if parsing the client's `updated_at`
throws `ValueError`, the code does `pass` and **saves anyway**.

**Why it's bad:** the whole point of the check is to stop one user from
overwriting another's work. A malformed timestamp (a frontend bug, a locale
issue) disables the protection exactly when things are already going wrong —
and nobody finds out.

**Do instead:** an unparseable `updated_at` is a bad request — reject with 400
so the frontend bug surfaces immediately, instead of eating a teammate's
annotations.

---

## 13. Hand-built response dicts duplicated per endpoint

**Where:** almost every router — e.g. the task-shaped dict is built by hand in
two places in `tasks.py` alone.

**Why it's bad:** add a column to `Task` and you must remember every dict that
should include it; miss one and the API is inconsistent with no error
anywhere. This is "shotgun surgery": one logical change, many scattered edits.

**Do instead:** define a Pydantic schema in `schemas.py` and put
`response_model=...` on the endpoint; FastAPI serializes the ORM object for
you, in one place. (`labels.py` already does this — it's the pattern to copy.)

---

## 14. Duplicated / dead configuration values

**Where:** `main.py` defines `HOST`/`PORT` used only in its `__main__` block
(the README starts the app differently, on another port); `detector.py`
computes a module-level `model_path`/`download_url` that `ensure_model_file`
then shadows with its own; `requirements.txt` lists `python-multipart` twice
with different constraints and `passlib`, which the code no longer uses
(`api/auth.py` calls `bcrypt` directly).

**Why it's bad:** the next person "fixes" the copy that isn't actually used
and burns an hour wondering why nothing changed. Config that exists in two
places is wrong in one of them within a month.

**Do instead:** one definition per setting, delete the unused copy the moment
you notice it. If you find yourself copying a constant, move it to `config.py`
and import it from both places.
