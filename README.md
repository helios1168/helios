# helios

helios is a research workflow kit: a Python CLI that dispatches beads (tasks) to agent CLIs
(Claude Code, Codex, opencode, Antigravity), validates what they return, and records the evidence.

- `docs/SPEC.md` is the specification. Read only the sections you need.
- `docs/GENERAL_PURPOSE.md` is the short guide for a hub that declares its own stages instead of
  the shipped research set: one stage, its skill, its bead, and the two commands that run and
  merge it.
- `AGENTS.md` holds the worker contract every bead prompt carries.
