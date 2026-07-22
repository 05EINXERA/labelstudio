# Principles Behind the Conventions

CONVENTIONS.md tells you *what* to do. This file explains *why* — the general
engineering principle underneath each rule. When you hit a situation the rules
don't cover, reason from the principle, not the letter of the rule. If a rule
and its principle ever seem to conflict, the principle wins; raise it in
review so the rule can be improved.

---

## Naming (§ 1)

**Principle: code is read far more often than it is written — optimize for the reader.**
A name is the cheapest documentation there is: `savedFiles` needs no comment,
`sf` needs one forever. Consistent casing per language (`snake_case` in
Python, `camelCase` in JS) matters less for its own sake than for what breaking
it signals — an inconsistent name makes readers stop and wonder whether the
difference *means* something.

**Principle: names should carry information at the point of use.**
When you see `autoDetectButton` three hundred lines from where it was defined,
you know what it is and what it references without scrolling. Apply this
anywhere: a function used far from its definition needs a name that stands
alone.

---

## File and folder structure (§ 2)

**Principle: things that change together should live together; things that change separately should live apart.**
One router per resource means a change to "how tasks work" touches one file,
and two people working on different resources never collide. The same logic
tells you when to split a file that has grown: if edits for unrelated reasons
keep landing in the same file, it has more than one job.

**Principle: the repository is a shared, permanent record — keep it signal, not noise.**
Everything committed is something every teammate must download, read around,
and trust forever (git never forgets). Generated files, local databases, and
one-off scripts at the root all cost a little attention from everyone,
forever; the "could a teammate regenerate this?" test generalizes to any file
you're unsure about.

---

## Error handling (§ 3)

**Principle: fail loudly and early — a visible crash is cheaper than a silent corruption.**
An error that surfaces immediately gets fixed in minutes; an error that's
swallowed surfaces weeks later as mysteriously wrong data, far from its cause.
This is why bare `except:` is banned: it converts the cheap kind of failure
into the expensive kind.

**Principle: handle only what you can actually recover from, at the level that can recover.**
Catching `json.JSONDecodeError` and substituting an empty list is a real
recovery; catching `Exception` and returning "something failed" is not — it
just destroys the information someone else needed. If your code can't do
something meaningful about an error, let it propagate to code that can (in
this app, FastAPI's 500 handler and the server log).

**Principle: errors have two audiences — the user and the operator — and each needs a different message.**
The user needs a safe, actionable message ("save failed, retry"); the operator
needs the full truth (the logged exception with its traceback). Most
error-handling mistakes come from serving one audience and forgetting the
other.

---

## Writing functions (§ 4)

**Principle: a function is a promise — the name states it, the body keeps it, nothing else happens.**
Callers reason about your function from its name alone; a hidden side effect
(like a GET that writes) breaks every caller's reasoning at once. This is also
the deep reason behind HTTP verb semantics: GET's "promise" of read-only is
relied on by browsers, caches, and colleagues alike.

**Principle: separate deciding from doing.**
Logic that computes an answer (pure, testable, reusable) should be separated
from code that touches the outside world (HTTP, database, disk). Thin
endpoints over plain functions is one instance; it applies equally to
frontend code (compute the new annotation state, *then* redraw the canvas).

**Principle: minimize what the reader must hold in their head at once.**
Early returns, one indentation level, small functions — these all serve the
same goal: at any line, the set of facts you must remember ("we're inside
three ifs and a loop") stays small. When code feels hard to follow, reduce
the working-memory load rather than adding explanatory comments.

---

## State management (§ 5)

**Principle: every piece of data needs exactly one source of truth.**
Two copies of the same fact (server + localStorage, DB + in-memory cache)
will disagree eventually, and then the system's behavior depends on which
copy each code path happens to read. Before caching or mirroring anything,
name which copy is authoritative and what happens when they diverge — if you
can't answer, don't make the copy.

**Principle: design for concurrency the moment more than one writer exists.**
Two browser tabs are already "distributed computing": without a conflict rule,
the last writer silently destroys the first writer's work. Optimistic locking
(reject if the data changed since you read it) is this project's chosen rule;
the principle — *detect conflicts rather than pretend they can't happen* —
applies to any new shared, editable resource.

**Principle: state you didn't write down is state you've agreed to lose.**
Anything in process memory (the `JOBS` dict) or a single browser tab vanishes
on restart or refresh. That's fine when losing it is acceptable and documented;
it's a bug when it isn't. Deciding *where* data lives is deciding *how durable
it is* — make that decision consciously.

---

## Database & migrations (§ 6)

**Principle: shared structures need a shared, ordered history of change.**
Your database schema exists on many machines (every dev, production), and
they only stay consistent if every change is a recorded, ordered step that
each copy applies — which is all a migration is. The same principle covers
API contracts and file formats: anything two parties share can only evolve
through explicit, versioned changes, never by one side quietly drifting.

---

## Tests (§ 7)

**Principle: a test is an executable promise that a behavior stays fixed.**
Untested behavior is protected only by everyone remembering it forever;
tested behavior is protected by the machine. That's why every bug fix gets a
test — the bug is proof that this exact behavior is both important and easy
to break.

**Principle: test what you own, at the boundary you promise.**
Testing "upload rejects .exe files" guards *your* promise; testing "FastAPI
returns JSON" guards the framework's, which is not your job. When deciding
what to test, ask: whose promise breaks if this fails, and would we get paged
for it?

**Principle: a test suite is only useful if it's fast and trustworthy enough to run every time.**
One test that hangs (a model download, a live network call) teaches the team
to skip the suite, which silently disables *all* the tests. This is why heavy
tests are marked and separated, and why manual scripts must not wear the
`test_` prefix.

---

## API design (§ 8)

**Principle: follow the convention your caller already knows, unless you have a stated reason not to.**
REST verbs, 404-for-missing, plural nouns — none of these are intrinsically
correct, but they're what every developer and tool assumes, so following them
means your API is documented by everyone else's experience. Deviating isn't
forbidden; deviating *silently* is.

**Principle: tell the caller the truth, even when it's awkward.**
Returning `ok` for a delete that deleted nothing is a comfortable lie that
converts the caller's bug into invisible data weirdness later. Statuses,
error codes, and responses exist to transmit reality; any handler that
"smooths over" reality is manufacturing a future debugging session.

**Principle: secure by default — safety must be the path of least resistance.**
Auth on the router (not per-endpoint) means a new endpoint is protected even
when its author forgets to think about security; the three unprotected
routers show what happens when protection is opt-in. Generalize this: design
defaults so that the lazy path is the safe path, for security, for
migrations, for anything with sharp edges.

---

## The meta-principle

Almost every rule above reduces to one idea: **software is a long conversation
among many people (including your future self), and most cost lives in
understanding and changing code, not in writing it.** When no rule covers your
case, choose the option that leaves the next person — reader, caller,
debugger, reviewer — with the least to untangle. If you can't tell which
option that is, ask in review; that conversation is the process working, not a
failure of it.
