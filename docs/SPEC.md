# helios specification

Status: canonical, version 1, 2026-09-13. Owner: the orchestrator. Consolidated from the
workflow design note of 2026-09-11 to 2026-09-13 and its Codex review; that note is not in this
repository and is not needed to build helios. Where code and this file disagree, this file wins
until the orchestrator amends it. Beads cite sections as `SPEC §N`.

## 1. Glossary

- **hub**: the main checkout of a project repository, on branch `main`.
- **unit**: the smallest piece of research that can be verified on its own: one claim, one
  model component, one algorithm or one experiment. One file, `docs/units/<unit>.md`.
- **stage**: one step of a unit chain (§3). Each stage is one bead.
- **bead**: one issue in the beads tracker (`bd`). One bead is one task for one agent.
- **attempt**: one launch of a bead, numbered from 1. A rerun is a new attempt.
- **turn**: one prompt and response in a native session. A resume adds a turn as a new attempt
  on the same session (§9.2).
- **harness**: an agent CLI helios can drive: `claude`, `codex`, `opencode`, `agy`, and `fake`
  for tests.
- **orchestrator**: the main agent session that plans, files beads, reviews, merges and writes
  memory. **worker**: an agent session running an implementation bead. **verifier**: an agent
  session running a verify bead; never the harness that authored the work.
- **report**: the JSON the agent writes as its last act (§4.1). **envelope**: the JSON helios
  writes after the process ends (§4.3).

## 2. Layout

### 2.1 This repository

```
src/helios/              the CLI package
  cli.py                 subcommand discovery (§2.3)
  commands/<name>.py     one module per subcommand
  envelope.py            result contracts (§4)
  harness/base.py        adapter interface (§6); one module per harness
schemas/                 generated JSON Schemas; never edit by hand
skills/<name>/SKILL.md   stage skills, Agent Skills format; skills/report-block.md shared
templates/               workflow.toml, backends.toml, unit.md shipped to projects
tests/                   pytest; tests/fixtures/native/<harness>/ holds recorded CLI output
docs/SPEC.md             this file
AGENTS.md                the worker contract (its "Worker contract" section)
```

### 2.2 A project that uses helios

```
.agents/workflow.toml                  project facts (§5)
docs/units/<unit>.md                   unit files (§10)
tools/verify/<unit>/                   verifier artifacts, committed
.claude/worktrees/<bead>/              one git worktree per bead, gitignored
.helios/runs/<bead>/attempt-<n>/       attempt records (§8), gitignored
.helios/events.jsonl                   event stream (§9.3), gitignored
```

### 2.3 Command modules

`helios.cli` imports every public module in `helios.commands`. A module defines `NAME`
(`"run"` or a nested path such as `"unit new"`), `HELP`, `add_arguments(parser)` and
`run(args) -> int`. Nested names build group parsers on demand. A name may not be both a command
and a group. Modules for nested commands are named with underscores: `commands/unit_new.py`
defines `NAME = "unit new"`. Library code lives outside `commands/`; a command module only parses
arguments and calls it.

- A command module imports optional dependencies (for example `sympy`, `z3`) inside `run`, never
  at module level, so a missing optional package breaks only that command.
- A command that gets a configuration error (`ValueError` or `TypeError` from `helios.config`)
  prints `helios: <message>` to stderr and exits 2.
- `helios.templates.path(name)` returns `<repository>/templates/<name>` (the directory two levels
  above the package) and raises `FileNotFoundError` naming the path. Shipping templates inside a
  wheel is not in this wave.

## 3. Stages

| id | input | output | author role | verifier | done when |
| --- | --- | --- | --- | --- | --- |
| `frame` | question in the owner's words | `docs/PROBLEM.md` with R ids, ★ gates, Q-list | orchestrator with the user | `frame-review` | every ★ that blocks `model` is a closed gate |
| `survey` | PROBLEM | `docs/DOMAIN_<d>.md`, `docs/LIT_<d>.md`, `docs/BRIEF.md` | orchestrator; search by any harness | a lens review | BRIEF names every unit with files, inputs, stop rule |
| `model` | unit brief | `## Model` in the unit file; program and claims modules (§15) | `[agents].model` | `verify-math` | claims check green |
| `verify-math` | `## Model`, claims | findings with `covers` | not the model author | none | overall verdict verified, or refuted rows returned to `model` |
| `impl` | `## Model`, bead `files`, `test`, `accept` | commits on `worktree-<bead>` | `[agents].implement` | `verify-code` | report done, bead test passes, ownership ok |
| `verify-code` | impl branch, `## Model` | findings, artifacts under `tools/verify/<unit>/` | not the impl author | none | overall verdict verified |
| `validate` | code, analysis plan written before outcomes | `experiments/<run>/manifest.json`, results, `REVIEW.md` | `[agents].implement` runs, orchestrator reviews | `verify-validate` | adopted results frozen with provenance |
| `verify-validate` | manifest, results | reproduction of every result behind a conclusion plus boundary cases | not the validate author | none | reproduced within the stated tolerance |
| `report` | adopted results, unit files | note, figures, `PROVENANCE.md`, `reproduce.sh` | orchestrator | the user | the user signs off |
| `remember` | `learned` and `missing_context` lines | memories, template fixes | orchestrator only | none | the learned queue for the unit is empty (§14) |

Loops: a refuted `verify-math` reopens `model` with the counterexample pasted into the unit file.
A refuted `verify-code` opens a bug bead on the unit; the verify bead stays open. A validation
surprise opens a ★ gate and returns to `frame` or `model`. `remember` runs after every stage,
asynchronously; it never blocks research. `frame`, `model` and `report` always start by hand.

## 4. Result contracts

The code is `src/helios/envelope.py`. `uv run python -m helios.envelope` regenerates
`schemas/agent-report.schema.json` and `schemas/envelope.schema.json`; a test fails when they
drift.

### 4.1 AgentReport (written by the agent)

Fields: `schema_version`, `status`, `summary`, `files_changed`, `tests`, `findings`,
`question`, `learned`, `missing_context`, `followups`. Status values:

- `done`: the accept text is met and the bead test passes.
- `partial`: progress made, work remains inside the same contract. `helios run` does not
  resume automatically in this wave; `partial` exits 3 (§7.1).
- `needs_input`: blocked on the orchestrator; `question` is required. End the turn after
  writing the report; the answer arrives as the next turn.
- `needs_review`: the agent wants review before continuing. Same mechanics as `needs_input`.
- `blocked`: cannot proceed; the reason is in `summary`.

The agent writes the report as JSON to the path its prompt names (§7.2). It never states an
execution status and never closes its bead.

### 4.2 Findings and evidence

A finding is one claim with separate fields: `verdict`, `method`, `scope`, `bound`,
`assumptions`, `artifact`, `checker`, `covers`, `notes`. The method selects how evidence was
produced; the actual output and the checker result decide the verdict. Rules enforced by the
model validators:

1. `scope` `bounded` or `instance` requires `bound` (domain, sizes, tolerance or instance names).
2. `exhaustive_finite_check` is never `universal`.
3. `empirical_support` never verifies a `universal` claim.
4. `refuted` needs a `counterexample` method or an artifact that reproduces the mismatch.

Rules the verifier skills enforce (not machine-checkable):

- A search that found no counterexample is `inconclusive` with its scope, never `verified`.
- A computer algebra simplification that fails to reach zero is `inconclusive`, not `refuted`.
- An SMT unsat core is a subset of assertions, not a proof object; rechecking it with a second
  solver is a separate finding.
- An instance certificate and a universal theorem differ in scope; never rank one above the
  other.

`overall_verdict(findings)`: `refuted` if any finding is, else `inconclusive`, else
`unsupported`, else `verified`; no findings gives none.

### 4.3 Envelope (written by helios)

Fields: `schema_version`, `task_id`, `attempt`, `attempt_id` (`<bead>#<n>`), `kind`,
`harness`, `model`, `session_id`, `started_at`, `finished_at`, `base_commit`,
`output_commit`, `input_hashes`, `execution_status`, `exit_code`, `report`, `report_error`,
`checks`, `artifacts`, `steered`, `notes`. Timestamps are UTC ISO 8601 with `Z`.

### 4.4 Execution status

| value | meaning |
| --- | --- |
| `completed` | native turn completed and a valid report was captured |
| `interrupted` | stopped by SIGINT, `helios stop` or the user |
| `timed_out` | the attempt exceeded its timeout |
| `crashed` | nonzero exit or a native error and no valid report |
| `missing_output` | native completion but no report anywhere |
| `invalid_output` | a report exists but fails the schema; the error goes in `report_error` |
| `launch_failed` | the binary is missing or exec failed |

helios takes the first value that applies, in this order:
1. `launch_failed`: `Popen` raised.
2. `interrupted`: helios received SIGINT, or the attempt directory holds `stop-requested`
   (written by `helios stop`, §9.2). This holds whatever the child printed or wrote. The
   file's existence is tested once, after the process group is gone and before parse; recovery
   (§8.4) tests it too when it classifies.
3. `timed_out`: helios hit the timeout.
4. `completed`: a valid report was captured. A nonzero exit or a native error only adds a note.
5. `invalid_output`: a report exists but fails validation.
6. `crashed`: the exit code is nonzero or the adapter set a native error.
7. `missing_output`: none of the above.

Execution failure is never a scientific verdict. A crashed verifier does not refute anything.

## 5. Configuration

File: `.agents/workflow.toml` at the project root, read with `tomllib`. The loader
`helios.config.load(start: Path) -> Config` walks up from `start` to the first directory holding
`.agents/workflow.toml`, else the first holding `.git`, else `start`; that directory is the
hub. Without the file helios runs in ad hoc mode with the defaults below. An unknown key, at any
depth including `[harness.<name>]` and `[tolerance.<tier>]`, and a value of the wrong type are
errors naming the dotted key. `templates/workflow.toml` is the commented template.

| key | default | meaning |
| --- | --- | --- |
| `project.test` | `"uv run pytest -q"` | shell command, run with `bash -c` from the worktree root |
| `project.typecheck` | `""` | empty disables |
| `project.units` | `"docs/units"` | unit file directory |
| `project.verify_artifacts` | `"tools/verify"` | verifier artifact root |
| `project.experiments` | `"experiments"` | validation run root |
| `project.worktrees` | `".claude/worktrees"` | bead worktrees |
| `project.runs` | `".helios/runs"` | attempt records |
| `project.link_into_worktrees` | `[]` | globs, relative to the hub, symlinked into each new worktree |
| `project.confidential` | `[]` | globs never staged, pushed or printed |
| `project.always_allowed` | `["tests/"]` | path prefixes any impl bead may touch |
| `project.program` | none | module exposing `REGISTRY` (§15) |
| `project.claims` | none | module holding `@claim` functions (§15) |
| `agents.orchestrate` | `"claude"` | harness of the orchestrator |
| `agents.implement` | `"codex"` | agent spec for `impl` and `validate` |
| `agents.model` | `"claude"` | agent spec for `model` |
| `agents.verify_code` | `"other"` | agent spec for `verify-code` |
| `agents.verify_math` | `"other"` | agent spec for `verify-math` |
| `agents.verify_validate` | `"other"` | agent spec for `verify-validate` |
| `agents.verify_order` | `["claude", "codex", "opencode", "agy"]` | how `other` is resolved |
| `harness.<name>.binary` | the harness name | executable |
| `harness.<name>.model` | none | passed to the CLI |
| `harness.<name>.effort` | none | passed to the CLI |
| `harness.<name>.timeout_s` | `3600` | per turn |
| `harness.<name>.extra_args` | `[]` | appended before the prompt |
| `harness.opencode.server_url` | none | attach to `opencode serve` when set |
| `control.default` | `"manual"` | `manual`, `until` or `auto` (§11) |
| `control.until` | `"verify-code"` | stop stage for an unqualified `unit run` |
| `control.stop_at` | `["frame", "model", "report"]` | stages that always start by hand |
| `control.confirm` | `["merge"]` | stages the orchestrator asks before starting |
| `memory.backend` | `"beads"` | `beads` or `files` (§13) |
| `memory.export_dir` | `".helios/memories"` | files export of the beads backend |
| `memory.inject_cap_bytes` | `32000` | cap on injected memories and docs |
| `tolerance.<tier>` | none | named numeric tolerances for skills and claims |

Agent spec syntax: `harness` or `harness:profile`, or `other`. `other` resolves to the first
harness in `agents.verify_order` that differs from the author's harness. The author is the
`author` metadata of the parent bead, an agent spec; its harness is the part before any `:`, so
author `claude:opus` excludes `claude`. The resolved harness is recorded on the bead.

## 6. Harness adapters

### 6.1 Interface

`src/helios/harness/base.py`: `LaunchSpec`, `NativeResult`, `Harness` with `argv`,
`stdin_text`, `parse`, `attach_command`. Adapters build argv and parse output; they never run
processes. `helios.harness.get(name) -> Harness` returns the adapter.

### 6.2 Report capture, same for every harness

1. The prompt names `report_path` and embeds the report schema (§7.2).
2. Where the harness has a native schema channel, the adapter passes the schema and returns the
   structured result in `NativeResult.structured`.
3. helios takes the native structured result when present, else the report file. When both
   exist and differ, it takes the native result and adds a note.
4. The report is validated with `AgentReport.model_validate`. Failure gives `invalid_output`.
5. The chosen report is written to `attempt-<n>/report.json` (the captured report, §8.1)
   before `execution_status` is stored, and no captured report is written when none was found.
   When the file was chosen, the captured report is the file's bytes. When the native result
   was chosen, it is `json.dumps(structured, ensure_ascii=False, indent=2, sort_keys=True)`
   plus a newline, whatever the report file holds; the file's bytes are never captured in that
   case. From then on validation, recovery and write-back read only the captured
   report, never the worktree path again, so an agent or user deleting or editing the worktree
   report after step 8 changes nothing.

### 6.3 Per harness

Flags and shapes below were checked against live runs on 2026-09-13; the recorded output is in
`tests/fixtures/native/<harness>/` (see its README for what each file is). An adapter must parse
the fixtures; it must not invent output shapes. If a needed fixture is missing, report
`needs_input` naming it.

Each form lists the argv elements in order. A bracketed part appears only when its `LaunchSpec`
field is set (not None and not empty): `M` is `spec.model`, `E` is `spec.effort`, `<session_id>`
is `spec.resume_session`. `<prompt>` is `spec.prompt`. Paths are `str()` of the `LaunchSpec` path,
never resolved. `[extra_args]` is `spec.extra_args`, one element each.

- **claude** (Claude Code): `claude -p --output-format json --json-schema <schema text>
  --permission-mode bypassPermissions [--model M] [--effort E] [--resume <session_id>]
  [extra_args] <prompt>`, where `<schema text>` is `spec.report_schema_path.read_text()`
  unchanged. A resume keeps the same `session_id`. stdout is one JSON object: `session_id`,
  `is_error`, `subtype`, `terminal_reason`, `structured_output` (the schema result, absent on
  error), `result` (text). A native error is `is_error: true`, with a non-null
  `api_error_status` when the API refused, and exit code 1. `subtype` stays `success` on error,
  so never use it. Never pass `--bare`. Attach: `claude --resume <session_id>`.
- **codex** (Codex CLI): fresh `codex exec --json --output-schema <schema path> -o
  <raw_dir>/last-message.json -C <worktree> --skip-git-repo-check
  --dangerously-bypass-approvals-and-sandbox [-m M] [-c model_reasoning_effort="E"]
  [extra_args] -`. Resume `codex exec resume <session_id> --json --output-schema <schema path>
  -o <raw_dir>/last-message.json --skip-git-repo-check
  --dangerously-bypass-approvals-and-sandbox [-m M] [-c model_reasoning_effort="E"]
  [extra_args] -`; `exec resume` has no `-C`, and helios launches with cwd the worktree. The
  effort is two elements, `-c` and `model_reasoning_effort="E"` with literal double quotes.
  The prompt goes on stdin: `stdin_text` returns `spec.prompt` for codex and None for every
  other harness. stdout is JSON lines in no fixed order, parsed by `type`: `thread.started` with
  `thread_id` (the session id, unchanged on resume), `turn.started`, `item.completed` (an
  `item.type` of `error` is a warning, not a failure), then `turn.completed` or, on failure,
  `error` (`message`) and `turn.failed` (`error.message`) with exit code 1. The structured
  result is the JSON in the `-o` file, which is not written when the turn fails. Never
  `--last`. Attach: `codex resume <session_id>`.
- **opencode**: `opencode run --format json --dir <worktree> --title <bead>#<attempt> [-m M]
  [--variant E] [--attach <server_url>] [-s <session_id>] --auto [extra_args] <prompt>`, with
  `<server_url>` from `spec.server_url`. The title is the same on a resume, and a resume keeps
  the id. stdout is JSON lines, each with `type`, `timestamp` and `sessionID`; every type except
  `error` also carries `part`. Types seen: `step_start`, `text`, `tool_use` (`part.tool`,
  `part.state`), `step_finish` (`part.reason`, `part.tokens`), and `error` (`error.name`,
  `error.data.message`) with exit code 1. A completed turn ends with `step_finish`. No schema
  channel: the report file is the only source. Attach:
  `opencode attach <server_url> --dir <worktree> -s <session_id>` when `spec.server_url` is set,
  else `opencode <worktree> -s <session_id>`. Stop with a server:
  `POST <server_url>/session/<session_id>/abort` (recorded for later; `helios stop` does not use
  it in this wave, §9.2).
- **agy** (Antigravity CLI): the prompt is the value of `-p`, so `-p` comes last:
  `agy --output-format json --json-schema <schema path> --dangerously-skip-permissions
  [--model M] [--effort E] [--conversation <session_id>] [extra_args] -p <prompt>`, with cwd
  the worktree; a resume keeps the id. stdout is one JSON object: `conversation_id` (the
  session id, an empty string on some errors), `status` (`SUCCESS` or `ERROR`),
  `structured_output`, `response`, `error` (text on failure, with exit code 1), `num_turns`,
  `usage`; other fields are ignored. Attach: `agy --conversation <session_id>`.
- **fake**: `python -m helios.harness.fake` driven by the JSON file in env
  `HELIOS_FAKE_SCRIPT`: `{"exit_code": 0, "sleep_s": 0, "stdout": "...", "session_id": "s1",
  "report": {...} | null, "report_text": "..." | null, "ignore_sigint": false}`. It writes
  `report` (or raw `report_text`) to `HELIOS_REPORT`, prints `stdout`, sleeps, exits. Tests use
  it for every failure path.

### 6.4 Rules for the real adapters

These apply to claude, codex, opencode and agy.

- `argv` and `attach_command` return `list[str]`; `stdin_text` returns `str | None`. They do no
  I/O, except that the claude `argv` reads the schema file. Element 0 of both lists is the
  harness name; `helios run` and `helios attach` replace it with `harness.<name>.binary` when
  that is configured. `attach_command` adds no model, effort or permission flags and sets no
  cwd, because `helios attach` runs it in the worktree. Model and effort are passed verbatim
  and never validated.
- `parse(spec, exit_code, stdout_path)` never raises and never reads stderr. It reads stdout as
  bytes. Input handling:
  - A `stdout_path` that is missing or is not a regular file, or that cannot be read, gives
    session_id None and native_error `no stdout`.
  - claude and agy: decode the bytes as UTF-8, strip, `json.loads`. A decode failure (invalid
    UTF-8 included) or a value that is not an object gives native_error `invalid JSON`.
  - codex and opencode: split the bytes on `\n` only (never `str.splitlines`, which also splits
    on U+2028, U+2029 and U+0085 that JSON allows inside strings). A final piece after the last
    `\n` is a line only when it is not empty. Each line is decoded as UTF-8 and stripped. A line
    that is blank, is not valid UTF-8, or is not a JSON object is skipped; N counts every
    skipped line, blank ones included, and one note `skipped <N> non-JSON lines` is added when N
    is not 0. No JSON object at all gives native_error `no events`.
- session_id is the first non-empty string among: claude `session_id`; codex `thread_id` of the
  `thread.started` events, in line order; opencode the top-level `sessionID` of the lines, in
  line order; agy `conversation_id`. A value that is empty or not a string is passed over, so a
  later event can still supply the id. session_id is returned even when native_error is set.
- When the input rules above set no native_error, it is set when `exit_code` is an int other
  than 0, or when the output signals failure: claude `is_error` is JSON `true` (any other value
  is not a failure); codex any `turn.failed`,
  or no `turn.completed`; opencode any `error` event, or a last event that is not
  `step_finish`; agy a `status` other than `SUCCESS`. `exit_code` None is not an error by
  itself. The text is a non-empty string, the first that applies: claude `result`; codex
  `error.message` of the last `turn.failed`, else `message` of the last `error` event; opencode
  `error.data.message` of the first `error` event, else its `error.name`; agy `error`; else
  `exit code <N>` when the exit code is not 0, else `turn did not complete`.
- structured is None whenever native_error is set, and always None for opencode. Otherwise it
  is claude or agy `structured_output`, or for codex the JSON in `<raw_dir>/last-message.json`.
  Only a JSON object counts. A missing key, or a `last-message.json` that is missing or not a
  regular file, gives None and the note `no structured result`. A present key or file whose
  value is not an object (JSON `null` included), whose bytes are not valid UTF-8, or that is not
  valid JSON gives None and the note `structured result is not a JSON object`. Never fall back to claude `result` or agy
  `response`.
- notes is empty unless a rule above adds one.
- `helios.harness.get(name)` returns the adapter for `claude`, `codex`, `opencode`, `agy` and
  `fake`, and raises ValueError naming any other name.

## 7. `helios run`

`helios run <bead>... [--harness H] [--again] [--timeout S] [--dry-run] [--max-parallel N]
[--tmux | --in-window]` (§9.1)

### 7.1 Steps

1. Load config (§5). Read each bead with `bd show <id> --json` through `helios.beads` (the only
   module that calls `bd`). Metadata values may arrive JSON-encoded as strings; decode them.
2. Preflight (`helios.preflight`), all failures exit 2 before anything is created:
   - `impl` and `validate` need `files` and `test`; verify kinds need `unit` and `parent`, and
     a verify bead without a worktree needs `output_commit` metadata on its parent (§7.3).
   - every name in `memories` exists (preflight takes the memory lookup as a required argument);
     every path in `docs` is an existing file (a directory is an error), and every `path#key`
     resolves to a section (§7.2);
   - `verify-math` needs a substantive `## Model`. That section starts at the first level 2
     heading whose text is exactly `Model` (not a `path#key` first-word match, so `### Model
     scope` never counts) and runs to the next heading of level 1 or 2. Its body counts the
     lines that are not headings in the §7.2 sense, not blank (whitespace only) and not the `_empty_` placeholder, each stripped of
     surrounding whitespace, total at least 200 characters; line breaks do not count. A line
     inside a code fence, or `#text` without a space, is counted like any other line;
   - beads launched together have disjoint `files`. Two entries overlap when either matches the
     other read as a literal path, or when neither is a literal path and the literal prefix of
     one starts with the literal prefix of the other. The literal prefix of a glob is its text
     before the first `*`, `?` or `[`; of a directory entry, the entry itself. The rule
     over-approximates on purpose: a false overlap only means the beads run separately;
   - `skills/<kind>/SKILL.md` exists in the hub for the bead kind;
   - the bead is not closed;
   - the latest attempt of the bead is finalized, unless recovery (§8.4) applies.
3. Resolve the harness (§5). `--harness` overrides.
4. Prepare the worktree (§7.3).
5. Allocate the attempt (§8).
6. Assemble the prompt (§7.2); write `prompt.md` and `input.json`. `input.json` is a JSON
   object written with `sort_keys` and indent 2, to a temp file then moved with `os.replace`.
   It holds at least `harness` (the resolved harness name) and `input_hashes`, and for a
   resumed attempt (§9.2) `resumed_from` (the attempt id resumed) and, when the attempt delivers
   a message, `msg_id`. `input_hashes` maps keys to sha256 digests: `prompt`; `bead`, the §7.2
   `Bead` section JSON exactly as it appears in the prompt; `docs/<entry>` per docs entry;
   `memory/<key>` per memory; and `unit` when the unit file exists.
7. Launch with `subprocess.Popen(argv, cwd=worktree, stdin=<stdin_text, else DEVNULL>,
   stdout=stdout file, stderr=stderr.log, start_new_session=True)`, environment plus
   `HELIOS_BEAD`, `HELIOS_ATTEMPT`, `HELIOS_HARNESS`, `HELIOS_HUB`, `HELIOS_REPORT`. Record
   `launched` with the child pid in `state.json` as soon as `Popen` returns, before waiting.
   Every signal goes to the child's process group (`os.killpg`). The stop sequence is SIGINT,
   wait up to 10 s, SIGTERM, wait up to 5 s, SIGKILL. A wait ends early only when the whole
   group is gone (`os.killpg(pgid, 0)` raises `ProcessLookupError`), not when the leader exits.
   On timeout run the stop sequence and record `timed_out`. The session id comes only from the
   adapter's `parse`; `helios.run` never reads harness inputs such as `HELIOS_FAKE_SCRIPT` (an
   adapter may read its own). While waiting, `helios run` checks for `stop-requested` in the
   attempt directory every 1 s and, when it appears, runs the stop sequence.
8. Parse with the adapter, capture the report (§6.2), classify execution status (§4.4), then
   record the result in one `state.json` write: the terminal state of §8.2, `execution_status`
   and the parsed `session_id`. A crash before that write leaves `launched`, which recovery
   treats as crashed (§8.4).
9. Run checks from helios itself: the bead `test` in the worktree (log to `checks/test.log`),
   `typecheck` when set, ownership (§7.4). When the captured report is missing or fails
   `AgentReport.model_validate` while `execution_status` is `completed` (only reachable in
   recovery, §8.4), the check `report` fails with a detail naming the problem.
10. If ownership passed, commit the changed paths of §7.4, after its skips, in three calls with
    literal pathspecs, never the whole tree. A path may no longer exist anywhere (a `git mv` or
    `git rm` source, or a file staged and then deleted on disk), and git rejects a pathspec
    that matches nothing, so each call gets its own filtered list:
    - stage: `git add -A -- ':(literal)<path>' ...` with the paths that exist on disk
      (`os.path.lexists`) or are in the index (`git ls-files -z --cached -- <pathspecs>`);
      skip the call when that list is empty;
    - select: `git diff --cached --name-only --no-renames -z HEAD -- <pathspecs of all changed
      paths>` lists the paths whose index entry differs from HEAD, deletions included;
    - commit: when the selected list is not empty, `git commit -m '<bead>: <report summary>'
      -- ':(literal)<path>' ...` with exactly the selected paths.
    `output_commit` is the worktree HEAD after this step, whether helios committed, the agent
    committed, or nothing changed. When ownership failed, nothing is staged and `output_commit`
    is null.
11. Write `envelope.json`, write back to the bead (§7.5), finalize the attempt, then append the
    event (§9.3). The event append is best effort: a crash after finalize loses that line and
    nothing else. Write-back, the event and the exit code handle a missing report (report
    fields null, `report_error` set); no step assumes one exists.

Exit codes: 0 finalized with report `done` (impl, validate) or overall verdict `verified`
(verify kinds); 3 report `partial`, `needs_input`, `needs_review` or `blocked`, or a verdict
other than verified; 4 execution failure; 5 a helios check failed; 2 usage or preflight. With
several beads the exit code is the highest per-bead code. A run that received SIGINT exits 4.

Interrupts: `helios run` installs a SIGINT handler for its whole life, so KeyboardInterrupt is
never raised. The first SIGINT sets a run-wide interrupt flag and runs the stop sequence on the
process group of every running child. That covers harness processes and the step 9 check
processes, which are also started with `start_new_session=True`. Beads not yet launched are
never launched. An attempt whose harness child was still running when the flag was set records
`interrupted`; an attempt already classified in step 8 keeps its status. A check stopped by the
interrupt fails with detail `interrupted`. Every launched attempt still completes steps 8 to
11. Later SIGINTs are ignored. An attempt allocated but not yet launched when the flag is set
never launches: it goes from `allocated` straight to `interrupted`, emits no `launched` event,
and completes steps 8 (classified `interrupted`) and 11. It skips steps 9 and 10: every check,
ownership included, is recorded as not run with detail `interrupted`, nothing is staged or
committed, and `output_commit` is null.

The handler never blocks. It only calls `set()` on the flag (a `threading.Event`) and returns;
it acquires no lock of helios's own (the Event's internal lock is fine, since only the handler
and the stopper touch it). A stopper thread started by the run blocks in `Event.wait()`, never
polls in a loop, and then runs the stop sequences of all running groups concurrently, so each
group gets its SIGINT at once. Groups started after the flag is set (a check that raced it) get
a stop sequence too. `run_many` returns only after every stop sequence has ended with its group
gone or SIGKILL sent and the group gone; it joins the stopper without a timeout. The handler is
installed only when `run_many` is called from the main thread (elsewhere `signal.signal` is not
allowed, and SIGINT keeps its current behavior), and the previous handler is restored before
`run_many` returns.

Every refusal and preflight message (lock held, live attempt, preflight errors, including under
`--dry-run`) goes to stderr.

`--dry-run` prints harness, argv, worktree, attempt path and prompt size. It performs no
recovery (§8.4) and prints the recovery action instead. The attempt path is the one the real run
would use: the existing attempt when recovery resumes it, else the next `n`. For a resumed
attempt the prompt size is that of its stored `prompt.md`. When the bead lock is held or the latest
attempt is live, it prints the refusal and exits 2. It creates, moves or writes nothing,
including bead updates and events.
Several beads run in parallel up to `--max-parallel` (default 3).

### 7.2 Prompt assembly

`helios.prompt.assemble(...)` returns one string. Sections, in order, each under a `## `
heading:

1. `Role`: the skill for the bead kind, `skills/<kind>/SKILL.md` without front matter.
2. `Contract`: the `## Worker contract` section of the repository's `AGENTS.md`, verbatim.
3. `Bead`: JSON of id, title, description, kind, unit, accept, files, test.
4. `Docs`: each `docs` entry. A bare `path` includes the whole file. `path#key` includes one
   section: the first heading, of any level, whose text equals `key` or whose first
   whitespace-separated word equals `key` (so `#5.` selects `## 5. Configuration` and `#9.3`
   selects `### 9.3 Events`). The section runs from that heading up to the next heading of the
   same or a higher level, so it includes its subsections. A heading is a line outside code
   fences that starts, after up to three spaces, with 1 to 6 `#` followed by a space, a tab or
   the end of the line; its level is the number of `#` and its text is the rest of the line,
   stripped. A fence opens with a line starting (after up to three spaces) with three
   or more backticks or tildes, and closes with a line of the same character at least as long
   as the opener; an unclosed fence runs to the end of the file. A key with no match is a
   preflight error.
5. `Memories`: each key's value from the memory backend (§13).
6. `Attempt`: worktree path, branch, attempt id, report path, and the report schema JSON.

Sections 4 and 5 together are capped at `memory.inject_cap_bytes` UTF-8 bytes. The docs entries
followed by the memory values form one ordered list of items, each kept whole or cut whole.
helios keeps the longest prefix of that list whose bytes, plus the note line naming the cut items
when any are cut, fit within the cap. It tries prefix lengths from longest to shortest and takes
the first that fits, so the computation always ends. When even the empty prefix does not fit,
the note is shortened at a character boundary to the cap. The prompt never names the harness,
so the bytes are identical across harnesses; a test asserts this.

### 7.3 Worktree

- Path `<hub>/<project.worktrees>/<bead>`, branch `worktree-<bead>`.
- New: `git worktree add <path> -b worktree-<bead> main`, then `git worktree lock --reason keep
  <path>`. Existing: reuse; refuse when its branch is not `worktree-<bead>`.
- `--again` on an existing worktree resets it to its branch tip (`git reset --hard` inside the
  worktree, then `git clean -fd -e .helios`). Earlier attempt records are never deleted.
- Symlink each `link_into_worktrees` glob match from the hub; skip matches the hub lacks.
- A new worktree for a verify kind starts at the parent bead's `output_commit` (metadata of the
  bead named by `parent`) instead of `main`; preflight fails when that metadata is missing.
- Record `base_commit` as the worktree HEAD before launch.

### 7.4 Ownership

Changed paths are the union of `git diff --name-only --no-renames -z <base_commit>` (working
tree), `git diff --cached --name-only --no-renames -z <base_commit>` (index, so a change staged
and then reverted on disk still counts), `git diff --cached --name-only --no-renames -z HEAD`
(so a path the agent committed and then removed again is a change) and
`git ls-files --others --exclude-standard -z`. `--no-renames` lists both sides of a rename, so a
deleted path is checked too; `-z` keeps non-ASCII paths unquoted. An untracked symlink whose
path matches a `project.link_into_worktrees` glob (§7.3) is not a change.

Each path must match a `files` entry or start with a prefix in `project.always_allowed`. A
`files` entry ending in `/` covers everything below that directory. Any other entry is a glob
matched against the whole path: `*` and `?` never match `/`, `**` matches any number of path
segments, `[...]` is a character class that never matches `/` and is negated by a leading `!`.
So `src/*.py` owns `src/a.py` but not `src/sub/a.py`. Verify kinds may touch only
`<verify_artifacts>/<unit>/`.

Always rejected, whatever the globs say: `.beads/`, `.helios/`, `.agents/`, the memory export
directory, and anything matching `project.confidential`. Confidential globs follow the same
rules, except that a glob without `/` matches the last path segment at any depth, so `*.pem`
matches `keys/a.pem`. A failed ownership check commits nothing and lists the paths in the check
detail.

### 7.5 Write-back

Every write carries the marker `[<attempt_id>]` (or `[<attempt_id>#k]` per line) and is skipped
when a comment with that kind and marker already exists, so a crashed write-back can be replayed.
A comment has that kind and marker when its text starts with `<kind>: <marker>`; a marker quoted
later in the text does not count.

- metadata: `harness`, `session` (`<harness>:<session_id>`), `worktree`, `attempt`,
  `execution_status`, `verdict` (verify kinds), `output_commit`.
- comments: `event: [id] <execution_status> <status> <summary>`, one `learned: [id#k] <line>`
  per learned line, one `missing_context: [id#k] <line>`, one `followup: [id#k] <line>`, and
  `question: [id] <text>` when present.
- close: an `impl` or `validate` bead closes when execution completed, the report is `done`,
  and all helios checks passed. A verify bead closes only when additionally the overall verdict
  is verified. Otherwise the bead stays `in_progress` and its state is recorded with
  one `bd set-state <bead> run=<state>` call with the final state. The state is the first that
  applies:
  - `failed`: any execution failure (§4.4) or failed helios check;
  - `blocked`: report `blocked`;
  - `waiting`: report `partial`, `needs_input` or `needs_review`, or a verify verdict other than verified.
- helios never reopens, relabels or deletes beads in this wave.

## 8. Attempt lifecycle

### 8.1 Records

`<hub>/<project.runs>/<bead>/attempt-<n>/` holds `state.json`, `state.log`, `input.json`,
`prompt.md`, `stdout.jsonl`, `stderr.log`, `raw/` (adapter side files), `report.json` (the
captured report), `checks/`, `envelope.json`, and `stop-requested` when `helios stop` asked the
attempt to stop (§9.2). The agent writes its report inside the worktree at
`<worktree>/.helios/attempt-<n>/report.json`, a path unique per attempt.

### 8.2 States

`allocated`, then `launched`, then one of `native_completed`, `interrupted`, `timed_out`,
`crashed`, `launch_failed`, then `validated` or `invalid`, then `finalized`. `state.json` is
`{"state", "attempt_id", "pid", "session_id", "execution_status", "updated"}`, where
`execution_status` is null until step 8 classifies the attempt and never changes after. Every
write carries all six keys, and a transition keeps the stored `execution_status` (and `pid`
and `session_id`) unless it sets a new value, so the key survives `validated`, `invalid`,
`finalized` and every recovery write (§8.4). It is written to a temp file and
moved with `os.replace`. Every transition appends one line to `state.log`. `launched` is
recorded with the pid as soon as the process starts, so a running attempt is always `launched`
with a live pid.

### 8.3 Allocation

A run holds an exclusive, non-blocking `fcntl.flock` on `<runs>/<bead>/lock` from before
recovery (§8.4) until its attempt is finalized. A run that cannot take the lock refuses with
exit 2 and prints the `helios attach` and `helios stop` commands. Recovery and allocation
happen only under the lock. The lock dies with its process, so it never goes stale.

`n` is one more than the highest existing `attempt-<n>` directory. The directory is created with
an exclusive `mkdir`; if it already exists (a concurrent run took `n`), helios tries `n + 1`.
`state.json` is written right after the `mkdir`, but a process can die between the two. Every
reader (preflight, recovery, `helios ps`) treats an attempt directory whose `state.json` is
missing or unreadable (empty, not valid JSON, not an object, or without a string `state`) as
state `allocated` with `pid` null, `session_id` null and `execution_status` null; reading it
never raises. Preflight reads attempt state without the lock, so it must accept this case and
leave the decision to recovery under the lock. Before launch helios checks the
worktree report path; if a file is there (stale), it moves it to `attempt-<n>/stale-report.json`
and adds a note. A report is accepted only from the current attempt's path or the native schema
channel of the current process. The input hashes (§7.1 step 6) are fixed in `input.json`
before launch.

### 8.4 Recovery

When `helios run` finds the latest attempt not finalized:

- `pid` alive: refuse, and print the `helios attach` and `helios stop` commands.
- state `native_completed`, `validated` or `invalid`: redo validation, checks and write-back
  (idempotent, §7.5), then finalize. No new attempt. A stored `execution_status` (§8.2) is
  kept; only an attempt without one is classified again. Validation reads the captured
  `attempt-<n>/report.json` (§6.2 step 5). A stored `completed` whose captured report is
  missing, not JSON, not an object, or fails `AgentReport.model_validate` keeps `completed`,
  fails the helios check `report` whose detail names the problem, sets `run=failed`, and
  exits 5, so the bead is not closed.
- state `interrupted`, `timed_out`, `crashed` or `launch_failed` with no live process: write
  `envelope.json` with that state as the execution status, finalize, then allocate a new
  attempt.
- state `allocated` or `launched` with no live process: record `crashed`, write `envelope.json`
  with `crashed`, finalize, allocate a new attempt.

### 8.5 Stale evidence

A verify envelope is stale when any key of its `input_hashes` other than `prompt` differs from
the current value computed the same way (a key missing on either side counts as different), or
its `base_commit` differs from the impl bead's current `output_commit` metadata. `helios merge`
refuses stale evidence (§12).

## 9. Sessions, messages and events

### 9.1 Windows

- `helios run --tmux <bead>...` runs preflight in the outer process (exit 2 on failure). It
  ensures session `helios` on the tmux server `tmux -L helios`: it runs
  `tmux -L helios new-session -d -s helios` when `tmux -L helios has-session -t =helios` fails,
  and a failed `new-session` still succeeds when `has-session` then exits 0. For each bead it
  runs `tmux -L helios new-window -d -t helios: -n <bead> helios run <bead> --in-window` plus
  the forwarded `--harness`, `--again` and `--timeout`, then
  `tmux -L helios set-option -w -t helios:<bead> remain-on-exit on`. It prints
  `<bead>\t<window>` per bead to stdout, exits 0, and never waits. A missing `tmux` binary
  exits 2.
- `--in-window` accepts exactly one bead. It copies the child's stdout bytes to its own stdout
  as well as to `stdout.jsonl`. Under `--in-window`, SIGHUP and SIGTERM are handled exactly like
  SIGINT (§7.1 Interrupts), because closing a window sends SIGHUP.
- Every harness, opencode included, runs `helios run --in-window` in its window. The attach
  command is for users (`helios attach`, §9.2), since the session id is known only after §7.1
  step 8.
- Users attach to the session with `tmux -CC -L helios new -A -s helios`. `helios ps` works
  without tmux.

### 9.2 Commands

- `helios ps [--json]`: rows come from the directories under `<runs>`, one per bead, using its
  highest attempt (§8.3 reading rules apply), sorted by bead id. A bead gets a row when that
  attempt is not finalized or the bead is `in_progress`. Each bead is read once with `bd show`
  through `helios.beads`; when that fails, `unit`, `kind` and the in-progress test fall back to
  `-` and false. Columns: `bead`, `unit`, `kind`, `harness` (key `harness` of the attempt's
  `input.json`, else `-`), `state`, `attempt` (the attempt id), `age`, `worktree`, `session`
  (`<harness>:<session_id>`, else `-`). Text output is tab-separated with that header line;
  `state` shows the stored state, plus ` (dead)` when the state is `launched` and the pid is not
  alive. Age is now minus `updated`: under 60 s `<n>s`, under 60 min `<n>m`, under 24 h `<n>h`,
  else `<n>d`, whole units rounded down, `-` when unknown. `--json` prints a list of objects
  with those keys (null for unknown) plus `alive` (bool) and `last_event` (the `type` of the
  last parseable line of the events file (§9.3) whose `bead` equals the bead and whose `attempt`
  equals the attempt id, else null). With no rows, text prints the header only and `--json`
  prints `[]`. Exit 0.
- `helios attach <bead>`: uses the highest attempt. It exits 2 with a message on stderr when
  there is no attempt, when `input.json` has no `harness`, when the worktree is missing, or when
  `session_id` is null (`no session recorded for <attempt_id>`; the session id is only known
  after the adapter's parse, §7.1 step 8). Otherwise it builds the `LaunchSpec` from
  configuration and the attempt paths with prompt `""`, calls the adapter's
  `attach_command(session_id, spec)`, replaces element 0 with the configured binary (§6.4),
  changes to the worktree directory and calls `os.execvp`. The harness lookup and the exec
  function are parameters of the library function, so tests inject them.
- `helios say <bead> "<text>" [--kind steer|answer]`: `--kind` defaults to `steer`. A bead with
  no directory under `<runs>` exits 2. Otherwise `say` always writes the message first (§9.4),
  then adds the comment `<kind>: [<msg_id>] <text>` with the replay rule of §7.5, then appends
  the event with type `<kind>`, source `orchestrator` and detail `<msg_id>`. When the highest
  attempt is `launched` with a live pid, it prints `queued <msg_id>` to stderr and exits 3.
  Otherwise it prints `<msg_id>` to stdout and exits 0. `say` never delivers: delivery belongs
  to `helios resume`.
- `helios stop <bead>`: exits 2 with `no running attempt for <bead>` unless the highest attempt
  is `launched` with a live pid. It writes `attempt-<n>/stop-requested` holding the UTC time
  (`yyyy-mm-ddThh:mm:ssZ` and a newline) with a temp file and `os.replace`, then sends SIGINT
  once to the attempt's process group with `os.killpg(pid, SIGINT)`, and exits 0 (also when the
  group is already gone). It never escalates and never writes `state.json`. The running
  `helios run` classifies an attempt whose `stop-requested` file exists as `interrupted` in
  step 8. The opencode abort route is not used in this wave.
- `helios resume <bead> ["<text>"]`: exits 2 with a message on stderr when the bead has no
  attempt, the bead is closed, the bead lock (§8.3) is held, the highest attempt is live or not
  finalized, or its `session_id` is null. Otherwise it takes the bead lock and handles the
  inbox, then its own turn.
  - Inbox: only file names matching `^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.json$` count, sorted as
    strings and listed once at start. A file that is not a valid §9.4 message is skipped with a
    note on stderr. An id with an ack is skipped.
  - Each message is one turn. Then the text (default `Continue.`) is one turn, run when text was
    given or no message was delivered.
  - A turn allocates attempt n+1 with `resume_session` set to the `session_id` of the highest
    attempt and the harness from that attempt's `input.json`, and runs §7.1 steps 5 to 11
    (never `--again`). The turn prompt is `<text>` followed by `\n\nReport path: <report
    path>\n`; for a message, `<text>` is `[helios-msg <msg_id>] <message text>`.
  - After a message turn whose `execution_status` is `completed`, `missing_output` or
    `invalid_output`, resume writes the ack. Any other status writes no ack and stops, and
    resume exits with that turn's code. Otherwise the exit code is the highest §7.1 code over
    the turns.
  - The envelope `steered` field lists the delivered msg_ids.

### 9.3 Events

`<hub>/.helios/events.jsonl`, one JSON object per line, appended with a single `write`:
`{"ts", "source", "type", "bead", "attempt", "session", "detail"}`. The line, newline included,
is at most 4096 bytes: helios shortens `detail` first, and raises when the line is still too long. Types: `launched`,
`completed`, `needs_input`, `needs_review`, `failed`, `finalized`, `steer`, `answer`, `idle`,
`error`. Sources: `helios`, `opencode-plugin`, `orchestrator`. The orchestrator watches this
file.

### 9.4 Messages

A message is `<runs>/<bead>/inbox/<msg_id>.json`: `{"id", "kind", "text", "created", "from",
"to"}`. `msg_id` is `<UTC yyyymmddThhmmssZ>-<8 hex>`. Delivery resumes the session with
`[helios-msg <msg_id>]` prefixed to the text (§9.2), and after the resumed turn ends with
`execution_status` `completed`, `missing_output` or `invalid_output` writes
`<runs>/<bead>/acks/<msg_id>`. Before delivering, skip any id already acked. A crash between
delivery and ack can deliver twice; the marker lets the agent and the transcript audit
recognise the duplicate. Workers never message workers.

The message file is written to a temp name in `inbox/` and moved with `os.replace`. `created` is
UTC `yyyy-mm-ddThh:mm:ssZ`; the 8 hex digits come from `secrets.token_hex(4)`; `from` is
`orchestrator`; `to` is the bead id. `inbox/` and `acks/` are created on demand.

## 10. Units and chains

### 10.1 Unit file

Created from `templates/unit.md` with `{unit}`, `{title}` and `{stages}` substituted: title
line, `Status:` (`open`, `in-progress`, `done`, `dropped`), `Stages:`, and sections `## Brief`,
`## Model`, `## Verify`, `## Code verify`, `## Validate`, `## Report`, each holding `_empty_`.

### 10.2 `helios unit new <unit> "<title>" --stages s1,s2,... [--files glob,...] [--test CMD]`

1. Validate. Every failure exits 2 with a message on stderr before anything is written:
   - `<unit>` matches `^[A-Za-z0-9][A-Za-z0-9._-]*$`;
   - `--stages` split on `,` has no empty or duplicate items, every item is a stage id of §3
     except `remember` (it never blocks, so it gets no bead), and the items are strictly
     increasing in the row order of the §3 table;
   - a verify stage (`verify-math`, `verify-code`, `verify-validate`) has an earlier non-verify
     stage in the list, else `<stage> has no earlier stage to verify`;
   - when `impl` or `validate` is present, `--files` (split on `,`, no empty items) and `--test`
     are given;
   - the unit file `<hub>/<project.units>/<unit>.md` does not exist.
2. For each stage in order, reuse the bead that is not closed and carries labels `unit:<unit>`
   and `kind:<stage>`; else create one titled `<unit> <stage>: <title>` with those labels and
   metadata `unit`, `kind`, `author`, and for `impl` and `validate` `files` and `test`. Metadata
   is passed as one JSON object so every value keeps its JSON type.
3. `author` is the resolved agent spec of §5 for the stage: `model` takes `agents.model`, `impl`
   and `validate` take `agents.implement`, each verify stage takes its `agents.verify_*` key, and
   `frame`, `survey` and `report` take `agents.orchestrate`. `other` resolves against the author
   of the stage's `parent` (the nearest earlier non-verify stage in the list), and the recorded
   author is the harness name.
4. Chain in list order: `bd dep add <later> <earlier>` for each adjacent pair, skipping a
   dependency that already exists. A verify stage also records metadata `parent` = the bead of
   its parent stage.
5. Write the unit file last, with `open(path, "x")`, creating missing parent directories. It is
   `templates/unit.md` (through `helios.templates.path`) with `{unit}`, `{title}` and `{stages}`
   replaced in one `re.sub` pass, never `str.format`; `{stages}` is the stage ids joined with
   `,`. A crash before this step leaves no unit file, so a rerun reuses the beads made so far.
6. Print to stdout a tab-separated table with the header line `bead`, `stage`, `author`,
   `blocked-by` and one row per stage, `-` for none. Exit 0.

Exact `bd` flags are read from `bd <command> --help` and used only inside `helios.beads`.
Tests run against a real `bd init` in a temporary repository when `bd` is on PATH, else skip.

## 11. Control

Candidates are the beads from `bd ready --json` (through `helios.beads`) with status `open` that
carry a `kind:<stage>` label naming a §3 stage, filtered by label `unit:<unit>` when a unit is
given, in bd's order. The §3 stage order comes from `helios.stages.STAGES`.

- `helios next [<unit>]` runs the first candidate whose kind is not in `control.stop_at`
  (`helios run`, §7.1) and prints one line from its envelope:
  `<attempt_id>\t<execution_status>\t<status or ->\t<verdict or ->\t<summary or ->`. With no
  such candidate it prints `no ready bead` to stderr and exits 3. Otherwise it exits with the
  run's code.
- `helios unit run <unit> [--until <stage>]` loops. Each round takes the first candidate,
  `stop_at` kinds included. It stops before running that candidate when its kind is in
  `control.stop_at`; otherwise it runs it and prints its line as `next` does. It also stops
  after running the `until` stage; on a report status other than `done`, a verdict other than
  verified, or an execution failure; when no candidate remains (an open gate or blocker); and
  when a candidate was already run in this invocation (reason
  `bead <id> still ready after its run`). On stopping it prints `stopped: <reason>` to stdout.
- `unit run` exits 0 when it stopped after a successful `until` stage, 3 when it stopped at a
  `stop_at` kind or with no ready bead, else with the last run's code.
- `control.default`: `manual` makes `unit run` refuse without `--until` (exit 2); `until` uses
  `control.until` when `--until` is absent; `auto` runs until a stop condition other than
  `until`. An unknown `control.default`, an `--until` that is not a §3 stage, or an `--until`
  stage with no bead of the unit (label `kind:<until>`) exits 2. These value checks live in the
  control module, not in config loading.

## 12. Merge

`helios merge <bead> [--dry-run]` integrates an impl bead. It holds `<runs>/<bead>/lock` (§8.3)
for its whole run; a held lock exits 2. Steps run in the order 1, 2, 8, 3, 4, 5, 6, 7.

1. Find the evidence. The verify beads are the beads with label `unit:<unit>` (the bead's
   `unit`) whose metadata `parent` is the bead, listed through `helios.beads`; none means
   refuse. Every one must be closed with metadata `verdict` `verified`. Its evidence is
   `<runs>/<verify>/attempt-<n>/envelope.json`, with `<n>` its metadata `attempt`, which must
   exist and not be stale (§8.5). The impl bead must be closed. Any failure refuses.
2. Refuse unless the hub is on `main` and both trees are clean. The hub is on `main` when
   `git symbolic-ref --short HEAD` prints `main`. A tree is clean when
   `git status --porcelain --untracked-files=no` prints nothing, in the hub and in the worktree.
3. Record `main_before`. In the worktree run `git rebase main`; on conflict run
   `git rebase --abort`, set `run=conflict`, stop.
4. Rerun `project.test` and `project.typecheck` in the worktree; failure stops.
5. If `main` moved since step 3, stop and say so; a second run starts over.
6. Write metadata `merge_commit` = the worktree HEAD, then in the hub run
   `git merge --ff-only worktree-<bead>`.
7. When `origin` exists (`git remote` lists it), run `git push origin main`. Then remove the
   worktree: `git worktree unlock <path>`, `git worktree remove --force <path>`,
   `git branch -d worktree-<bead>`.
8. Recovery runs after step 2 and before step 3: when metadata `merge_commit` is set and
   `git merge-base --is-ancestor <merge_commit> main` exits 0, skip to step 7.

Every step writes the comment `merge: [<bead>@<main_before>:<step>] <detail>` under the replay
rule of §7.5, where `<step>` is one of `rebased` or `conflict` (step 3), `tested` or
`test-failed` (step 4), `main-moved` (step 5), `merged` (step 6), `pushed` and `removed`
(step 7).

A test checks that `bd set-state` on a closed bead does not reopen it. If it does, merge sets no
state on closed beads.

Exit codes: 0 merged or recovered; 2 refusal (steps 1 and 2, lock); 3 rebase conflict or `main`
moved; 5 test or typecheck failed; 4 push or worktree removal failed. Messages go to stderr.

`--dry-run` runs steps 1 and 2 and the recovery test of step 8, prints the planned steps, and
writes nothing.

## 13. Memory

Interface `helios.memory.Backend`: `inject(keys) -> str`, `write(key, header, body)`,
`read(key) -> Memory`, `stale() -> list[str]`, `export(directory)`, `import_(directory)`.

Memory value format, version 1, lossless:

```
helios-memory 1
{"commit": "3f1e9a", "confidence": "proof", "source": "hel-a02#1", "status": "active", "supersedes": null, "unit": "U14"}

<body, verbatim>
```

Line 1 is literal. Line 2 is one JSON object with sorted keys; `source` is required; `status` is
`active`, `superseded` or `retracted`. Line 3 is empty. The body follows unchanged, including
its trailing newline or lack of one.

- `Memory(key, header, body)` is a frozen dataclass; `header` is the line 2 object as a dict.
  `read` of a missing key raises `KeyError`.
- Keys match `^[A-Za-z0-9][A-Za-z0-9._-]*$`, else `ValueError` naming the key.
- Line 2 is `json.dumps(header, sort_keys=True, ensure_ascii=False)` with the default
  separators. Parsing requires the text to start with `helios-memory 1\n`, then one line holding
  a JSON object, then `\n`; anything else raises `ValueError` naming the key. The body may be
  empty. Line endings are never normalized.
- **beads backend**: `bd remember --key <key> "<value>"`, `bd recall <key>`, `bd memories`.
  `write` calls `bd` first (through `helios.beads`), then writes the export file under
  `memory.export_dir` via a temp file and `os.replace`. The backend skips `bd memories` values
  that do not start with line 1 and prints a note naming each skipped key to stderr. A memory
  body must survive `bd remember` and `bd recall` unchanged. If bd changes it (for example a
  trailing newline), the beads backend stores the whole value in a form bd keeps, and a test
  round-trips bodies ending in no newline, `\n` and `\n\n`.
- **files backend**: `<export_dir>/<key>.md` holding exactly the value format.
- `write` refuses: a header without `source` as a non-empty string (`ValueError`), a `status`
  outside `active`, `superseded`, `retracted` (`ValueError`; a missing `status` is written as
  `active`), and any call while the environment has `HELIOS_BEAD` set (`PermissionError`, since
  workers never write memories). Other header keys are kept. `supersedes` never edits another
  memory.
- `export` writes each memory to `<directory>/<key>.md` and never deletes files; `import_` reads
  the same format from only the `*.md` files directly in the directory, in sorted name order;
  export after import reproduces the tree byte for byte.
- `stale()` lists active memories whose `source` bead carries the label `truth:wrong`. The bead
  id is `source` up to the first `#`; a bead that does not exist is not stale; the result is
  sorted by key.
- `inject(keys)` returns `### <key>\n\n<body>` for each key, joined with `\n\n`, in the given
  order; a missing key raises `KeyError`.
- `helios.memory.open_backend(config)` returns the backend for `memory.backend` and raises
  `ValueError` `unknown memory backend '<value>'` for any other value.

## 14. Learned queue and gates

- `helios learned [--unit U] [--json]` lists the comment lines matching
  `^(learned|missing_context): \[(.+)#(\d+)#(\d+)\] (.*)\Z`, compiled with `re.DOTALL`; the
  groups are kind, bead, attempt number, k and text. It reads beads of any status that carry a
  helios `kind:<stage>` label (§3), deduplicates lines by kind and marker, and keeps only lines
  without a matching `curated:` comment. `--unit U` keeps beads with label `unit:U`. Lines are
  sorted by unit (`-` when none), bead, attempt number as a number, kind, then k. Text output
  groups by unit, then bead, then attempt. `--json` prints a list of
  `{"unit", "bead", "attempt", "kind", "k", "text"}`.
- `helios learned --mark <kind>:<attempt_id>#<k> memory|template|drop` adds
  `curated: [<kind>:<attempt_id>#<k>] -> <decision>` under the replay rule of §7.5. The marker
  includes the kind because `learned` and `missing_context` lines share `[<attempt_id>#<k>]`.
  An unknown marker exits 2; an existing curated mark is a no-op with exit 0; `--mark` with
  `--unit` or `--json` exits 2. When no uncurated line of that bead remains, add label
  `curated`.
- `helios gate [--json]` lists open gates from `bd gate list --json -n 0` with the beads each
  blocks, read from the gate's dependents in `bd show <gate> --json` (through `helios.beads`).

## 15. Claims and program

### 15.1 Registry

`helios.program`: `Block(id, kind, expr, build=None, satisfies=(), relaxes=(), since=None,
replaces=None)` with kind in `set`, `parameter`, `variable`, `definition`, `objective`,
`constraint`, `stage`. `expr` is a string or a SymPy expression. `build(model, data)` adds the
block to a solver model using `name=<id>` or `name=<id>[...]`. `Registry` holds active and
retired blocks; ids are unique and never reused. A project's program module
(`project.program`) exposes `REGISTRY`.

`Registry()` has `add(block)` (`ValueError` when the id was ever used, active or retired),
`retire(id)` (moves an active block to retired; `KeyError` when not active), and `active()` and
`retired()` in insertion order. `active()` leaves out every id listed in the environment
variable `HELIOS_OMIT` (comma-separated). A program module may expose `DATA`;
`build(model, data)` receives `getattr(module, "DATA", None)`.

### 15.2 Claims

`helios.claims`: decorator `@claim(name, covers, backend, method, scope, bound=None,
artifact=None, redundant=())` registers a zero-argument callable returning `True`, `False` or a
`Finding`. `bound` is `dict[str, str] | None`, the same type as `Finding.bound`. `project.claims`
names the module. Backends and their allowed methods and scopes come from `backends.toml` (the
project's `.agents/backends.toml`, else `templates/backends.toml`).

`helios claims check [--backend B] [--covers ID] [--timeout S]`:

1. Every claim covers at least one active block id (requirement ids do not count toward this, so
   a claim covering only requirement ids fails); unknown ids fail. An id matching
   `^R[0-9]+$` is a requirement id and is not checked against the registry; a retired block id
   counts as unknown.
2. The backend exists and allows the claim's method and scope. Steps 1 and 2 run for every
   selected claim before anything runs; any failure prints one line per problem to stderr and
   exits 2.
3. The claims module is imported with the hub and `<hub>/src` put in front of `sys.path`. Each
   non-`manual` claim runs as `python -m helios.claims.runner <module> <name>` with cwd the hub
   and a timeout from `--timeout S` (default 600). The runner prints one JSON line:
   `{"result": true}`, `{"result": false}`, `{"finding": {...}}` or `{"error": "<traceback>"}`.
   Only a JSON boolean or a `Finding` counts; any other return value is inconclusive with a
   note. `True` gives a verified finding with `id` and `claim` set to the claim name and the
   declared `covers`, `method`, `scope`, `bound` and `artifact`. `False` gives a refuted finding
   with method `counterexample` when the backend allows it, else inconclusive. An error, a
   timeout (note `timeout after <S> s`) or unparsable runner output gives inconclusive; an
   error's traceback is saved to `<hub>/.helios/claims/<name>.traceback.txt` and that path goes
   in `artifact`.
4. Print one JSON finding per line to stdout. Exit 0 when every selected non-manual claim is
   verified (also when none is selected), else 3.

`helios claims attack <claim>`: for each covered block, rebuild the registry without it and
rerun the claim. Each mutation has an expected effect: `must_fail` by default, `may_pass` for
blocks listed in the claim's `redundant`. A `must_fail` mutation that still passes is reported
as a surviving mutation. A surviving mutation is a finding to review, not proof of a defective
claim.

The claim first runs unchanged; unless that returns `True`, print `baseline did not pass` to
stderr and exit 2. Then for each covered id that is an active block id (requirement ids are
skipped), in `covers` order, rerun the claim with `HELIOS_OMIT=<id>` and print one line
`<id> <must_fail|may_pass> <passed|failed|inconclusive>`. Exit 3 when a `must_fail` mutation
passed, else 0.

### 15.3 Program commands

- `helios program show [--output PATH]`: deterministic markdown. Sections `## <kind>` follow
  the kind order of §15.1, only for kinds with active blocks, with blocks sorted by id and one
  line per block `<id>  <expr>  # satisfies <ids> / relaxes <ids>`, where `expr` is `str(expr)`
  with newlines replaced by spaces and id lists are joined with `, `. A half with no ids is
  dropped with its ` / ` separator, and the whole comment is dropped when both are empty.
  `## Retired` follows only when there are retired blocks, in the same line format sorted by
  id. Output ends with one newline. `--output PATH` writes the file instead of stdout. The same
  registry gives the same bytes.
- `helios program check`: call each active block's `build` on a recording model object and
  assert the set of recorded names, with any `[...]` suffix stripped, equals the active
  constraint and objective ids that have a `build`. Only names recorded while building
  `constraint` and `objective` blocks are compared; blocks are built in insertion order. The
  recording model returns a recorder for any attribute; calling a recorder records
  `kwargs["name"]` when it is a string and returns a new recorder. The suffix stripped is
  everything from the first `[`. Print `missing: <ids>` and `unexpected: <names>` lines (sorted,
  joined with `, `, only when non-empty) and exit 5 on a mismatch (a failed check, as in §7.1),
  else 0.
- `helios program diff <rev1> <rev2>`: load the registry at two git revisions (`git show
  <rev>:<path>` into a temporary module). The registry module path at a revision is
  `src/<module with dots as slashes>.py`, else `<module with dots as slashes>.py`; when neither
  exists at that revision, exit 2. Only active ids are compared. Print every `+<id>` line (in
  rev2, not rev1) sorted, then every `-<id>` line sorted. Exit 0.

Semantic conformance beyond names (coefficients, bounds, domains, objective direction, feasible
sets on small instances) is a verifier task in `verify-code`, not a `program check` result.

## 16. Authority

- beads: helios moves bead state only on evidence (a valid envelope, a merge). The orchestrator
  writes content (title, description, accept, files, dependencies). Workers and verifiers never
  create, close, reopen or label beads; they write reports.
- git: workers commit only on `worktree-<bead>`; verifiers commit only under
  `<verify_artifacts>/<unit>/`; only `helios merge` or the orchestrator advances `main`; no
  force pushes and no tags from workers.
- memory: only the orchestrator writes, after `helios learned`. Workers report `learned` lines.

## 17. Operating model (★I, provisional)

One human owner. The owner resolves conflicting decisions and alone accepts conditional
evidence (a verified finding with bounded or instance scope standing in for a universal claim).
One orchestrator session at a time holds the memory write role and the merge role; a second
orchestrator session may plan and review but does not write memories or merge. Revisit when a
second person joins.

## 18. Decisions

| id | subject | status |
| --- | --- | --- |
| ★A | environment per worktree | closed: environment per worktree, shared uv cache |
| ★B | test runner | open for projects; helios itself uses pytest |
| ★C | where the kit lives | closed: `repos/helios`, own beads database |
| ★D | may any harness author a model | closed: yes, with a fresh verifier on another harness |
| ★E | validation verifier scope | closed: reproduce every result behind a conclusion plus boundary and failure cases |
| ★F | move dispatch to ACP | open |
| ★G | read-only subagents inside a bead | closed: allowed, one patch owner |
| ★H | Microsoft Conductor as run layer | open, after the slice |
| ★I | operating model | provisional, §17 |
| ★J | codex daemon threads | deferred: this wave uses `codex exec`; daemon threads come with remote control |
| ★K | trace backend | open |
| ★L | session runtime | closed: native CLIs plus tmux |
| ★M | Graphiti | open |
| ★N | memory store | closed: beads, with a files export |
| ★O | Lean beyond a pilot | open |
| ★P | harness set | closed 2026-09-13: claude, codex, opencode, agy |
| ★Q | beads database scope | closed 2026-09-13: one database per repository |

## 19. Superseded proposals (historical, do not build)

- Fast-forward-only merge without integration: replaced by §12.
- One verdict word per unit: replaced by findings with separate evidence fields (§4.2).
- A shared environment for all worktrees: replaced by ★A.
- Memory files as the source of truth, and a `learned.jsonl` ledger: replaced by §13 and §14.
- Orca as runtime: declined (★L).
- The names `research-kit` and `rk`: now `helios`.
- Routing agent work by runtime length: route by capability; established numerical jobs run as
  plain processes.
- A result file whose presence signals completion: completion is native process completion plus
  validation (§8).

## 20. Not in this wave

Plugin hooks (Stop hook inbox draining, PreToolUse denies), a separate `helios verify` command
(a verify bead runs through `helios run`), telemetry tagging and trace export, the beads board,
codex daemon threads and remote control, rootshell notifications, Graphiti, Lean, VIPR, porting
the existing `math-verify` and `code-verify` skills, and any project migration.
