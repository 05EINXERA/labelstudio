# AGENTS.md

**The project instructions live in [`CLAUDE.md`](CLAUDE.md). Read that file.**

This file is a pointer, not a copy, and must stay that way.

It used to be a full duplicate of `CLAUDE.md`. The two drifted: by October 2026
this file was still describing `frontend/app.js` as a "4,500-line monolith being
decomposed" (it had been deleted), still said deployment Phase 4 was pending
(it was done), and was missing the authorization, annotation-storage, wipe-guard
and task-status rules entirely — roughly three months of decisions. Any agent
that happened to read this file instead of `CLAUDE.md` got a confidently wrong
picture of the codebase.

A second copy of a living document is not redundancy; it is a second source of
truth that nobody remembers to update. **Do not restate any rule here.** If
something is missing for agents, add it to `CLAUDE.md`.

Narrow, non-duplicated steering notes that are genuinely scoped to one topic
live in [`.agents/AGENTS.md`](.agents/AGENTS.md) (currently: the modal
`is-active` rule, which `CLAUDE.md` rule 15 also points at).
