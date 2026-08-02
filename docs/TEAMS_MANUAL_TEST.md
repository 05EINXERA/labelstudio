# Teams — manual test walkthrough

A step-by-step script for checking the Teams / roles / project-access feature by
hand in a browser. Every step says **what to do**, **what you should see**, and
**why it behaves that way**, so a wrong result tells you which rule was broken
rather than just "it's broken".

Run it against the **dev instance** (`http://127.0.0.1:8001`), never the live
LAN deployment — several steps create and delete data.

- Design rationale: `.devnotes/teams/01_DESIGN.md`
- Numbered edge cases referenced as `E-nn`: `.devnotes/teams/06_EDGE_CASES.md`
- The rules themselves: `CLAUDE.md` rules 1b, 1c, 18a–18d

---

## 0. Before you start

```powershell
cd D:\ai\projects\annotation\labelstudio-dev
.\scripts\run-dev.ps1
```

**Check the server is actually the current code** — this is the single most
common cause of confusing results. A uvicorn process started before a code
change keeps serving the *old* Python while still serving *new* static files
from disk, which looks like a broken feature but is a stale process:

```powershell
curl.exe -o NUL -w "%{http_code}`n" http://127.0.0.1:8001/api/teams
```

- **401** → correct. The route exists and is asking you to log in.
- **404** → the server predates the Teams code. Stop it and rerun
  `run-dev.ps1`. Note `--reload` only watches `api/`, `frontend/` and
  `formats/`, so edits to `main.py` or `schemas.py` need a manual restart.

Then **hard-reload the browser** (`Ctrl`+`Shift`+`R`). Module versions are
pinned by hand (`?v=`), so a soft reload can leave a half-updated bundle where
new JS talks to old cached JS.

### Test accounts

You need four. Create them with:

```powershell
python scripts\create_user.py owner_test
python scripts\create_user.py ann_test
python scripts\create_user.py rev_test
python scripts\create_user.py outsider_test
```

(The password is prompted for, never passed as an argument.) Use a different
browser profile or a private window per user — logging in as a second user in
the same window replaces the session cookie of the first.

| Account | Plays |
|---|---|
| `owner_test` | project owner + team owner |
| `ann_test` | annotator |
| `rev_test` | reviewer |
| `outsider_test` | in no team — the "should see nothing" control |

---

## 1. Empty state and onboarding

**Do:** log in as `outsider_test` → **Teams**.

**Expect:** the empty state shows **your own username in a code box**, with text
about asking a manager to add you.

**Why:** that username is the exact string they must hand to a manager, and a
brand-new user has no other way to find it. This is the entire onboarding path
(E-30) — if the username is missing, the empty state is broken even though it
looks fine.

---

## 2. Creating a team

**Do:** as `owner_test` → **Teams** → **+ New team** → name it `QA Team` →
create.

**Expect:**
- You land straight on the **Members** tab, not back on the list.
- You are listed once, with role **Owner**.
- The URL is `teams.html?id=N#/members`.

**Why:** adding people is the obvious next step, so the flow leaves you where
that happens. The owner's membership row is written in the *same transaction* as
the team — a team whose owner is not a member would be invisible to every roster
query.

**Also try:** create a second team with the **same name**. It should succeed,
and its slug gets a numeric suffix (`qa-team-2`). Two people naming a team
"Reviewers" is normal; a 409 for something you did not type and cannot fix is a
dead end. *(Delete the duplicate afterwards — §9.)*

---

## 3. Adding members (the interaction users hit hardest)

**Do:** on **Members** → **+ Add member** → type a username that does not exist,
e.g. `nosuchperson` → Add.

**Expect:** an **inline red error under the field**, the modal **stays open**,
and your typed text is **still there**.

**Why:** this is the most common real failure (a typo), and a toast over a
closed modal would throw the input away. If you see a toast, or the modal
closes, that is the bug (§ 5.1 of the UI spec).

**Then:**

| Do | Expect | Why |
|---|---|---|
| Add `ann_test` as **Member** | Appears in the roster | — |
| Add `rev_test` as **Member** | Appears in the roster | — |
| Add `ann_test` **again** | **Success**, message says already a member, role unchanged | Adding twice is a double-click, not an error (E-01) |
| Add `ann_test` again as **Manager** | Role stays **Member** | The idempotent path echoes the *current* role; a re-add must not silently promote or demote |

**Note:** there is deliberately **no username autocomplete**. That is not an
oversight — a search endpoint would turn add-member into a user-enumeration
oracle (E-14). Do not "fix" it.

---

## 4. A team alone grants nothing

**Do:** as `ann_test` (now in `QA Team`) → **Projects**.

**Expect:** you do **not** see `owner_test`'s projects.

**Why:** this is the central idea of the two-axis model. Being in a team is not
access; the team must be *granted* a project. If projects appear here before
§5, the resolver is wrong.

---

## 5. Granting access

**Do:** as `owner_test`, open (or create) a project → **Access** tab → pick
`QA Team`, role **Annotator** → Grant.

**Expect:**
- The team appears in the table with a role dropdown.
- As `ann_test`, refresh **Projects** — the project **now appears**, with a
  role badge reading **Annotator** and no owner controls.

**Why:** the grant is the access boundary. Notice the **Access tab only exists
for the project owner** — check as `ann_test` that there is no Access tab at
all.

**Now the deep-link check (important).** As `ann_test`, manually type:

```
http://127.0.0.1:8001/project.html?id=<PROJECT_ID>#/access
```

**Expect:** you land on **Home**, and the URL rewrites itself to `#/home`.

**Why:** hiding a nav tab does nothing about a URL that is typeable,
bookmarkable, or shared in chat — and a role can be revoked after a link was
saved. The router re-checks the role, and normalises the address bar so it does
not lie about which view you are on (CLAUDE.md rule 18d).

---

## 6. Roles actually restrict things

Set the grant role on the Access tab and check each level. The dropdown saves
immediately with an inline "Saved" — no separate save button.

| Grant role | `ann_test` should see / be able to |
|---|---|
| **Viewer** | Open tasks read-only. **No** upload button, **no** drop zone, **no** bulk bar. Drawing tools greyed out with a *"view-only access"* banner |
| **Annotator** | Draw and save normally. **No** Approve/Reject buttons |
| **Reviewer** | Everything an annotator can, **plus** ✓/✗ on completed tasks |
| **Manager** | Plus upload, bulk actions, edit/delete rows, the Imports tab |

**Key check — the annotator cannot approve.** As `ann_test` at **Annotator**,
open a task and set its status to `Approved`.

**Expect:** a **403** with a message naming the role required — *not* a
"someone else edited this" conflict message.

**Why:** permission checks run **before** conflict detection precisely so a
permission problem never surfaces as a 409, which would be unactionable
(CLAUDE.md rule 1c).

---

## 7. Review flow

**Do:** as `ann_test`, take a task to **Completed**. Then as `rev_test`
(grant `QA Team` **Reviewer** first), open the project's **Tasks** tab.

**Expect:** ✓ and ✗ buttons on that row.

| Do | Expect |
|---|---|
| Click ✓ Approve | Status → **Approved** |
| Click ✗ Reject | Prompted for a reason; status → **Rejected**, styled in a **warning colour distinct from Approved** |
| On the canvas, open a Completed task | Approve/Reject in the top bar |

**Why the colour matters:** "sent back for rework" must not be mistakable for
"accepted" at a glance.

**Bulk bypass check (the easy thing to get wrong).** As `ann_test` at
**Annotator**, select several completed tasks and try **bulk-update → Approved**.

**Expect:** `updated: 0`, everything in `skipped`, and **no task approved**.

**Why:** bulk-update is the obvious one-request bypass for the reviewer gate
(E-11). A reviewer *can* bulk-approve — that is their job — but an annotator
cannot.

---

## 8. Task assignment

As `owner_test` on the **Tasks** tab:

| Do | Expect | Why |
|---|---|---|
| Assign a task to a team **with** a grant | Team chip appears in the Team column | — |
| Assign to a team with **no** grant | **422**, "does not have access… grant it first" | A task assigned to a team that cannot see the project is invisible work (E-09) |
| Leave a task unassigned | Shows *"— Unassigned"* in muted text | That is the shared pool, not an error — anyone annotate-capable may work it |
| Bulk-assign a mix of your tasks and someone else's ids | `updated` counts yours, `skipped` counts the rest | Filter-don't-fail: one stray id must not lose the batch |

**`restrict_to_assigned_team` (opt-in enforcement).** There is no UI toggle yet,
so set it directly:

```sql
UPDATE projects SET restrict_to_assigned_team = true WHERE id = <PROJECT_ID>;
```

Assign a task to a team `ann_test` is **not** in, then have them open it.

**Expect:** a banner *before they draw* — *"This task is assigned to <Team>. You
can view it but not save changes."* — and a 403 on save.

**Why:** the banner appears on task open, not after the first rejected save.
Discovering ten minutes of work cannot be saved is the failure this prevents.
Default is `false`, so nothing changes unless a project opts in.

---

## 9. Revoke, transfer, delete

| Do | Expect | Why |
|---|---|---|
| **Access → Revoke** a team | Confirmation states the consequence in counts; `ann_test`'s next request gives **404** | Revocation bites on the very next request — no cross-request cache (E-08) |
| Check revoked team's tasks | Tasks **still exist**, now unassigned | Revoking access must never delete annotation work |
| **Team → Settings → Transfer** to `ann_test` | You become **Manager**, they become **Owner** | One owner, always — both rows move in one transaction (E-05) |
| Try **Leave team** as owner | **Blocked**, "transfer ownership first" | Nobody is trapped, but a team is never ownerless |
| **Delete team** | Must type the **slug**; states projects affected and that annotations are not deleted | Deletion revokes access for everyone in it (E-06) |
| After deleting | Its tasks return to the pool, **annotations intact** | `SET NULL`, never `CASCADE` — the single most important cascade decision |

---

## 10. The shared-account guarantee (do not skip)

**Do:** log in as an existing single-account user (`seinxera`, or whoever the
shared login is) who owns projects and is in **no team**. Open a project,
annotate, save, approve, export.

**Expect:** everything works exactly as before Teams existed.

**Why:** this is the guarantee the entire design rests on. One user who owns
everything passes every check via the **owner short-circuit**, so a deployment
that never creates a team is behaviourally unchanged. If anything here fails,
the feature is not safe to deploy regardless of how well §§1–9 went.

---

## 11. Unsaved work survives a permission error

The most important safety property, and the easiest to miss.

**Do:**
1. As `ann_test`, open a task and draw a few shapes — **do not save**.
2. As `owner_test`, revoke `QA Team`'s access in another window.
3. Back as `ann_test`, trigger a save.

**Expect:** a clear permission message, and **your shapes are still on screen**.
Reload — the draft is restored from local storage.

**Why:** a permission error must never destroy unsaved work (CLAUDE.md rule 18a,
E-24/E-27). The offline queue marks the write `forbidden`, reports it **once**,
and stops retrying — but **keeps the payload**. If the drawing disappears, or
the offline banner keeps flashing against a healthy server, that is a serious
bug, not cosmetic.

---

## Quick triage

| Symptom | Likely cause |
|---|---|
| `/api/teams` → **404**, "could not load teams" | Stale server process — see §0 |
| Create team → **405** | Same: old router matched `/api/team`, which has no POST |
| Project nav completely empty | `/api/auth/me` failing → role resolves to `null` → zero nav items (correct behaviour, wrong server) |
| Buttons visible but every click 403s | Stale JS bundle — hard-reload (E-17). Cosmetic, not a security hole: the server is enforcing |
| A role change seems ignored | Refresh. Roles resolve per request; the page does not poll |
| Everything 401s | Session cookie gone — log in again |

## What is deliberately not built

Do not report these as bugs (`.devnotes/teams/07_PHASING.md` F1–F9):

- **No self-approval block** — a reviewer may approve their own work. It is
  *recorded* in the audit trail instead; blocking it breaks the small-team case
  and is trivially defeated by two people approving each other's.
- **No username autocomplete** — deliberate (E-14).
- **No invitation/accept flow** — a manager adds you and you are in; the safety
  valve is that you can always leave.
- **No UI toggle for `restrict_to_assigned_team`** yet — SQL only.
- **Images at `/uploads/*` are served unauthenticated** — pre-existing, on a
  trusted LAN (E-20).
