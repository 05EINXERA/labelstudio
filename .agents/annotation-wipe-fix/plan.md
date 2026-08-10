What's actually going wrong

Think of this like a shared Google Doc, except instead of merging edits, every time someone hits "save," the whole document gets replaced with whatever that person's browser thinks the content is. If their browser's copy is temporarily blank, "save" wipes everything.

That's the core problem here. The annotation tool (this looks like a Label Studio-style image annotation app) stores all the annotations for a task as one big blob. Every save overwrites the entire blob — there's no "add this one shape" or "merge my changes with yours." So if the app's local copy of the annotations is empty for even a split second, and something triggers a save at that exact moment, the real annotations on the server get replaced with nothing.

The document found three separate ways this empty-save can slip through, all stemming from the same root cause: when you switch from viewing one image/task to another, the app briefly clears its local list of annotations before it has loaded the new task's real annotations from the server. That gap — empty local state, waiting on a network request — is the danger zone.

Vector 1: Switching browser tabs at the wrong moment (the scary one)

Imagine a reviewer is flipping through tasks. They click to the next image. The app does this in order:

Clear the screen (annotations = empty, ready for the new image)
Ask the server "give me this task's real annotations" (this takes a moment over the network)
Once the server replies, fill the screen with the real annotations

Now imagine that during step 2 — while waiting on the network — the reviewer alt-tabs to check Slack. The browser has a background feature: when you switch tabs, it quietly sends a "save my progress" signal (called a "beacon") so no work timer data is lost. That signal is supposed to just save time-tracking info. But because of how the code is wired, it also grabs "whatever annotations are currently in memory" and sends that to the server too — and right now, "whatever is in memory" is empty (step 1 hasn't finished loading step 3 yet).

Normally the server has a safety check: "if you're sending me an empty list but I already have hundreds of annotations, reject it." That check does fire here — but the browser's background "beacon" signal doesn't wait around for a response, so the rejection is sent into the void and nobody notices. Worse, this failed attempt quietly disables part of the server's other safety net (a timestamp check), making the next real save more likely to succeed in wiping things out too.

There's an even worse flavor of this: if the network request in step 2 straight-up fails (bad wifi, timeout), the app just logs an error to the console and moves on — it never retries, never blocks further saves. The screen is now permanently empty for that task, and literally any save button click or autosave after that will try to wipe the real data.

Vector 2: The "keep my version" trap in the conflict popup

If two people (say an annotator and a reviewer) have the same task open, and the server notices they're about to overwrite each other, it pops up a conflict dialog: "Reload to get the latest" or "Cancel to keep what I have."

The bug: if the reviewer opened the task, made no edits, and just sat there while the annotator kept working and adding more annotations, then clicks "keep what I have" — the reviewer's screen still has the old version they loaded, not the annotator's newer work. The button implies "keep my changes" but there were no changes — it actually means "throw away everything the annotator did since I opened this tab." Nothing checks whether "my version" is even different or complete before allowing the overwrite.

Vector 3: A "review" action quietly makes annotator saves look conflict-free when they shouldn't

When a reviewer approves/rejects a task, the server updates a timestamp but doesn't update "who last touched the annotations." That means the annotator's next save — which still thinks it's working from an older timestamp — sails past the conflict check that should have flagged it. It's described as lower risk, but it's the same family of "the safety check has a blind spot" issue.

The common thread

All three boil down to: the app can end up in a state where its local copy of the annotations is empty or stale, and nothing reliably stops an empty/stale save from going out and overwriting real data. The server does have some safety nets (reject empty overwrites, reject saves with old timestamps), but each vector finds a specific gap in those nets.

The recommended fixes (and why they make sense)

The document's own recommendations target the actual gaps, not just symptoms — I agree with the logic:

#	Fix	Why it's the right call
P0	Make the "tab switch" background signal only save timer/time-tracking data, never annotations	This signal's job was never to save annotations — it's piggybacking on data that happens to be sitting in memory. Stripping it out removes the #1 attack vector entirely, with no downside since a real annotation save still happens through the normal path.
P0	If loading a task's real annotations fails, mark that task as "not safely loaded" and block any save until it's retried successfully	Right now a failed load = silent, permanent empty state. Blocking saves until it's confirmed loaded is the only way to guarantee you never overwrite with something you never actually fetched.
P1	In the conflict popup, if the local copy is empty (or would erase existing server work), don't offer "keep mine" — say "your view is empty, this would erase server data, reload instead"	This directly closes the UX trap — it stops the button from meaning something destructive without the user realizing it.
P1	On the server: if the timestamp token is missing/null AND the person saving isn't the last person who saved, treat it as a conflict (409) instead of skipping the check	This closes the loophole where "no timestamp" was being read as "no need to check" instead of "we can't verify this is safe."
P2	Add a proper "hydrated" (fully loaded) flag per task, and don't allow autosave until it's true	Belt-and-suspenders on top of the P0 fix above — an explicit flag is more robust than inferring state from timing.
P2	Log a warning whenever annotations drop from some number to zero without an explicit "user confirmed clear" flag	This doesn't prevent anything, but means if a wipe does slip through some future gap, someone finds out from the logs instead of a user complaint.

These are sound: they fix root causes (don't send empty data; don't trust unverifiable saves) rather than just patching each individual crash site, and they're ordered sensibly — P0s stop active data loss, P1s close remaining loopholes, P2s add resilience/visibility.

Implementation Plan

Since I don't have the actual project files in this session (the paths in the doc point to a local Windows path, E:/icn/annot/...), I can't directly edit the code right now — but here's the concrete plan to execute, in order, with what "done" looks like for each:

Phase 1 — Stop the bleeding (P0s)

timer.js — handleVisibilityChange: Change the call from drainTaskTime(task, { useBeacon: true }) to a variant that explicitly sends no annotations field (or a dedicated "time-only" beacon endpoint/payload). Verify drainTaskTime's fallback logic (annotations !== undefined ? annotations : task.annotations) is never reached with an implicit empty array in this path.
Test: simulate a tab switch mid-task-load and confirm the beacon payload contains no annotations key.
init.js — hydration failure handling: In the catch block around the GET /api/tasks/{id} call, set task._hydrationFailed = true (or equivalent) and route all save triggers (autosave, manual save, beacon) through a guard that checks this flag before sending. Add a retry (e.g., one automatic retry, then a visible "couldn't load — click to retry" state) rather than failing silently.
Test: force the fetch to fail and confirm no POST with empty annotations ever fires afterward.

Phase 2 — Close remaining loopholes (P1s)
3. Conflict dialog logic: Before rendering the "keep mine" option, compare state.annotations.length against the server's known annotation count for that task. If local is empty (or trivially smaller/different in a way that signals staleness) and server has data, replace the button with a warning + forced reload instead of an overwrite option.
4. tasks.py conflict check (server): Change if task.updated_at: to explicitly handle the null case — when updated_at is null/missing and client_id != last_client_id, return 409 instead of skipping the whole block.

Test: send a save with updated_at: null from a different client_id than the last saver and confirm it's rejected.

Phase 3 — Defense in depth (P2s)
5. Add a hydrated: boolean field to gallery/task items, set only after a successful hydration fetch; gate autosave and beacon saves on hydrated === true.
6. Add a server-side log/alert whenever annotations count drops from N>0 to 0 on a save that isn't explicitly flagged allow_clear=true — even if it's ultimately rejected by the clear-guard — so these near-misses are visible.

Cross-cutting

Add a regression test suite specifically simulating the three race conditions (tab-switch mid-load, failed hydration, conflict-dialog-with-empty-state) so these can't silently regress.
Since annotation loss is currently silent and destructive, consider (as a safety net, not a fix) keeping a short server-side history/backup of the last N versions of Task.annotations per task, so any future wipe is recoverable even if a new gap is found later.