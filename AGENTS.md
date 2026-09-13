# helios

helios is a research workflow kit: a Python CLI that dispatches beads (tasks) to agent CLIs
(Claude Code, Codex, opencode, Antigravity), validates what they return, and records the
evidence. The canonical specification is `docs/SPEC.md`. Read only the sections your bead names.

## Worker contract

This section is pasted into every bead prompt. You implement or verify exactly one bead.

### Scope

- Work only inside your worktree, the current directory. Never read or write outside it.
- Change only paths that match the bead's `files`, plus `tests/`. If the work needs another
  path, stop and report `needs_input` naming the path.
- Meet the bead's `accept` text and nothing beyond it. No speculative features or options.
- Never edit `docs/SPEC.md`, `AGENTS.md`, `schemas/`, `skills/`, `templates/`, `opencode.json`
  or `.opencode/` unless the bead's `files` name them. If the SPEC looks wrong or ambiguous,
  report `needs_input` with the question. Do not guess a contract.

### Environment

- Python 3.12 through uv. Run everything as `uv run ...`. Add a dependency only when the bead
  says so (`uv add <pkg>`).
- Full suite: `uv run pytest -q`. The bead's `test` command is the acceptance test.
- Tests never need the network, a real agent CLI, or any path outside the temporary
  directories they create (`tmp_path`). A test that calls a real CLI carries
  `@pytest.mark.live` and is skipped by default.
- `bd` may be used inside a temporary repository a test creates (`bd init` in `tmp_path`).
  Against this repository only `bd show <your bead> --json` is allowed.
- Native CLI output shapes come from `tests/fixtures/native/<harness>/`. Never invent one; if a
  fixture you need is missing, report `needs_input`.

### Code

- Type hints and `from __future__ import annotations`. Result shapes come from
  `helios.envelope`; do not define parallel models.
- Commands follow SPEC §2.3: argument parsing in `commands/<name>.py`, logic in library modules.
- `subprocess` with list argv. `shell=True` only for the project's configured test and
  typecheck commands, run through `bash -c`.
- Small functions, docstrings that cite the SPEC section they implement, comments only where the
  reason is not obvious. Match the style of the surrounding code.
- No em dashes in comments, docstrings or messages.

### Git

- Commit on your branch `worktree-<bead>` with the message `<bead>: <summary>`.
- Never merge, rebase, checkout, switch, push, tag, `reset --hard`, or touch another worktree.

### Beads and memory

Never create, update or close beads, and never write memories. Report instead:
`learned` for durable facts, `missing_context` for what the bead should have told you,
`followups` for new work (one short bead title each).

### Finish

Your last act is writing the report JSON to the report path named in the prompt's `Attempt`
section. It must validate against `schemas/agent-report.schema.json` (check with
`uv run python -c "import json,sys; from helios.envelope import AgentReport;
AgentReport.model_validate(json.load(open(sys.argv[1])))" <path>`). Then end your turn with one
line.

- `done`: `accept` is met, the bead test passes and the full suite is green.
- `partial`: progress made, work remains.
- `needs_input`: set `question`, write the report, end the turn. The answer arrives as your next
  message, prefixed `[helios-msg <id>]`. Then continue and write a new report at the end.
- `needs_review`: you want the orchestrator to look before you continue. Same mechanics.
- `blocked`: you cannot proceed; say why in `summary`.

## Orchestrator

The orchestrator session plans, files beads, reviews diffs, merges into `main` and is the only
memory writer (SPEC §16, §17). Beads use prefix `hel`; this repository has its own beads
database.
