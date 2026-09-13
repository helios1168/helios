---
name: impl
description: Implement one helios bead inside its worktree. Used as the Role section of every impl bead prompt; any harness.
---

# Implementation bead

You implement exactly one bead. The contract, the bead, its docs and your attempt facts follow
this section.

## Method

1. Read the bead's `accept`, `files` and `test`, then only the docs it names.
2. Read the code you will touch before changing it. Search narrowly; open the one function you
   need, not whole directories.
3. Write types first: signatures and data shapes, then bodies.
4. Write the failing test that encodes `accept`, then make it pass.
5. Run the bead test, then the full suite. Fix what fails. Do not weaken a test to pass it.
6. Commit on your branch: `<bead>: <summary>`.
7. Write the report (see the contract's Finish section), then end the turn.

## Stop instead of guessing

- The accept text has two readings: ask (`needs_input`).
- The work needs a path outside `files` and `tests/`: ask.
- A native CLI output fixture you need is missing: ask.
- The SPEC and the existing code disagree: ask, quoting both.
