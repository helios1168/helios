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
- A command that gets a configuration error (`ValueError`, `TypeError` or `RecursionError` from
  `helios.config`) prints `helios: <message>` to stderr and exits 2, in every command. The same
  rule covers a program or claims module that fails to import: the import is caught as
  `BaseException`, and the command prints `helios: cannot import <module>: <exception
  type>: <message>` to stderr and exits 2.
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

Every JSON file helios reads or writes, harness output and helios's own artifacts alike, goes
through `helios.jsonio`. Reading uses a `parse_constant` that raises and a `parse_float` that
raises when `float(s)` is not finite, so `NaN`, `Infinity`, `-Infinity` and a number that
overflows to infinity (for example `1e999` or `-1e999`) are never valid JSON; each such value
takes the same not-JSON path the site already has for `NaN` and `Infinity`. Writing uses
`allow_nan=False`.

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

Agent spec syntax: `harness` or `harness:profile`, or `other`. `other` is valid only for verify
stages; `other` configured for a non-verify stage is a refusal. `other` resolves to the first
harness in `agents.verify_order` that differs from the author's harness. The author is the
`author` metadata of the parent bead, an agent spec; its harness is the part before any `:`, so
author `claude:opus` excludes `claude`. When no harness in `agents.verify_order` differs, the
stage is refused with `helios: no harness in agents.verify_order differs from <harness>`. The
resolved harness is recorded on the bead.

## 6. Harness adapters

### 6.1 Interface

`src/helios/harness/base.py`: `LaunchSpec`, `NativeResult`, `Harness` with `argv`,
`stdin_text`, `parse`, `attach_command`. Adapters build argv and parse output; they never run
processes. `helios.harness.get(name) -> Harness` returns the adapter.

### 6.2 Report capture, same for every harness

1. The prompt names `report_path` and embeds the report schema (§7.2). The schema,
   `schemas/agent-report.schema.json`, is generated in the strict form OpenAI structured outputs
   require: every object lists all its properties in `required`, sets `additionalProperties:
   false`, drops `default`, makes each formerly optional property nullable, and a `$ref` carries
   no sibling keys. A null for a property whose model type forbids null is treated as absent
   during validation.
2. Where the harness has a native schema channel, the adapter passes the schema and returns the
   structured result in `NativeResult.structured`.
3. helios takes the native structured result when present, else the report file. When both
   exist and differ, it takes the native result and adds a note.
4. The report is validated with `AgentReport.model_validate`. Failure gives `invalid_output`.
5. The chosen report is written to `attempt-<n>/report.json` (the captured report, §8.1)
   before `execution_status` is stored, and no captured report is written when none was found.
   When the file was chosen, the captured report is the file's bytes. When the native result
   was chosen, it is `json.dumps(structured, ensure_ascii=False, indent=2, sort_keys=True,
   allow_nan=False)` plus a newline, whatever the report file holds; the file's bytes are never
   captured in that case. From then on validation, recovery and write-back read only the captured
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
  `--last`. Attach: `codex resume <session_id>`. codex enforces OpenAI strict structured outputs,
  so `<schema path>` is the strict-form schema of §6.2.
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
  - A path is not a regular file unless `os.stat` succeeds and `stat.S_ISREG` holds. This is
    checked before opening, so a FIFO, socket or device is never opened or read. Any `OSError`
    or `ValueError` from stat or read (a NUL byte in the path raises `ValueError`) means the path
    cannot be read.
  - Strip and stripped mean removing only the JSON whitespace characters space, tab, CR and LF
    from both ends (`strip(" \t\r\n")`), never Python's default `str.strip`, which also removes
    U+2028, U+2029, U+0085, NBSP and others.
  - `json.loads` is called through `helios.jsonio`, with a `parse_constant` that raises and a
    `parse_float` that raises when `float(s)` is not finite, so `NaN`, `Infinity`, `-Infinity`
    and a number that overflows to infinity (for example `1e999` or `-1e999`) all make a line or
    document not JSON.
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
  `step_finish`; agy a `status` other than `SUCCESS`, a missing `status` key included.
  `exit_code` None is not an error by itself. The text is a non-empty string, the first that
  applies: claude `result`; codex `error.message` of the last `turn.failed`, else `message` of
  the last `error` event; opencode `error.data.message` of the first `error` event, else its
  `error.name`; agy `error`; else `exit code <N>` when the exit code is not 0, else
  `turn did not complete`.
- structured is None whenever native_error is set, and always None for opencode. Otherwise it
  is claude or agy `structured_output`, or for codex the JSON in `<raw_dir>/last-message.json`.
  Only a JSON object counts. A missing key, or a `last-message.json` that is missing, is not a
  regular file or cannot be read, gives None and the note `no structured result`. The bytes of
  `last-message.json` are stripped by the same JSON whitespace rule before `json.loads`. A
  present key or file whose value is not an object (JSON `null` included), whose bytes are not
  valid UTF-8, or that is not valid JSON gives None and the note
  `structured result is not a JSON object`. Never fall back to claude `result` or agy
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
     a verify bead without a worktree needs `output_commit` metadata on its parent (§7.3), so a
     verify worktree is never created from `main` in any run mode.
   - every name in `memories` exists (preflight takes the memory lookup as a required argument);
     a name failing the §13 key rule, or equal to `schema_version`, does not exist. The files
     backend matches the exact name via a directory listing; on the beads backend a failing `bd
     recall` (`RuntimeError`, `OSError`, `ValueError`) is its own failure
     `helios: memory lookup failed for <key>: <message>`, distinct from a name that does not
     exist. Every path in `docs` is an existing file (a directory is an error), and every
     `path#key` resolves to a section (§7.2);
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
3. Resolve the harness (§5). `--harness` overrides. Then, unless the bead is already closed
   (which helios never reopens), set its status to `in_progress`.
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
   `launched` with the child pid in `state.json` as soon as `Popen` returns, before waiting, and
   read the leader's start time right after with `ps -o lstart= -p <pid>` stripped, stored as
   `pid_start`; null when that read fails. Every signal goes to the child's process group
   (`os.killpg`). The stop sequence is SIGINT,
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

A memory read for step 6 can still fail after step 5 allocates the attempt, even though
preflight's existence check of the same name already passed: the backend's `read(key)` can raise
on a value it can now not read or parse. That failure stops the run with `helios: memory lookup
failed for <key>: <message>`, exit 2, before launch; the value is never treated as empty. The
attempt is finalized as a launch that never started: `execution_status` is `launch_failed`, no
`launched` event is emitted, and steps 9 and 10 are skipped, with every check, ownership
included, recorded as not run with detail `memory lookup failed`, and `output_commit` null. No
bead close is written.

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
gone or SIGKILL sent and the group gone; it joins the stopper without a timeout. While the
handler is installed, the main thread itself never calls `set()` or `clear()` on the flag: a
SIGINT arriving while the main thread holds the `Event`'s internal lock would run the handler on
that same thread and deadlock on a reentrant `set()`. To wake the stopper at the end of an
uninterrupted run, a short helper thread calls `set()` and is joined, then the stopper is
joined. Restoring the previous handler and the run-depth exit run in their own
`try`/`finally`, so both still happen when an exception escapes the joins. The handler is
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
including bead updates (the status change of step 3 included) and events.
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
   preflight error. The included text, whole file or section, has its leading and trailing
   newlines removed (`strip("\n")`) before it goes in the prompt; no other whitespace is
   touched, so indentation on the first line is kept.
5. `Memories`: each key's value from the memory backend (`helios.memory`, §13), with leading and
   trailing newlines removed (`strip("\n")`) the same way; no other whitespace is touched. A
   read that raises is a memory lookup failure (§7.1).
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
- helios never reopens or deletes beads in this wave; the only label it adds is `curated` (§14).

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
`{"state", "attempt_id", "pid", "pid_start", "session_id", "execution_status", "updated"}`,
where `execution_status` is null until step 8 classifies the attempt and never changes after,
and `pid_start` is set once at launch (§7.1 step 7) and never changes after. Every write carries
all seven keys, and a transition keeps the stored `execution_status`, `pid`, `pid_start` and
`session_id` unless it sets a new value, so the key survives `validated`, `invalid`,
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
state `allocated` with `pid` null, `pid_start` null, `session_id` null and `execution_status`
null, and takes the attempt id `<bead>#<n>` from the directory name. In a readable file, a `pid`
that is not an int with `0 < pid < 2**31` (a bool counts as not an int) reads as null, and a
`session_id`, `execution_status` or `pid_start` that is not a string reads as null. Parsing
catches `RecursionError`, and uses a `parse_constant` that rejects `NaN`, `Infinity` and
`-Infinity` and a `parse_float` that rejects a number that overflows to infinity, so any of
these counts as not valid JSON. Reading it never raises. Preflight reads
attempt state without the lock, so it must accept these cases and leave the decision to recovery
under the lock. Before launch helios checks the worktree report path; if a file is there
(stale), it moves it to `attempt-<n>/stale-report.json` and adds a note. A report is accepted
only from the current attempt's path or the native schema channel of the current process. The
input hashes (§7.1 step 6) are fixed in `input.json` before launch.

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

- `helios run --tmux <bead>...` runs the full §7.1 preflight in the outer process (exit 2 on
  failure), before any attempt, worktree or status change. It ensures session `helios` on the
  tmux server `tmux -L helios`: it runs `tmux -L helios new-session -d -s helios` when
  `tmux -L helios has-session -t =helios` fails, and a failed `new-session` still succeeds when
  `has-session` then exits 0. For each bead it runs `tmux -L helios new-window -d -t helios: -n
  <bead> helios run <bead> --in-window` plus the forwarded `--harness`, `--again` and
  `--timeout`, then `tmux -L helios set-option -w -t helios:<bead> remain-on-exit on`. It prints
  `<bead>\t<window>` per bead to stdout, exits 0, and never waits. A missing `tmux` binary exits
  2. Each window then runs the same preflight again on its own, since `--in-window` always does
  (below).
- `--in-window` accepts exactly one bead and runs the full §7.1 preflight itself, with the same
  messages and exit 2, before any attempt, worktree or status change. It copies the child's
  stdout bytes to its own stdout as well as to `stdout.jsonl`. Under `--in-window`, SIGHUP and
  SIGTERM are handled exactly like SIGINT (§7.1 Interrupts), because closing a window sends
  SIGHUP.
- `--dry-run` combined with `--tmux` or `--in-window` prints `helios: --dry-run cannot be
  combined with --tmux or --in-window` and exits 2 before any other work.
- The `--in-window` CLI entry, after the run returns its code, sets SIGHUP, SIGTERM and SIGINT
  to ignored, flushes stdout and stderr, and exits with `os._exit(<code>)`; called in process, it
  restores the previous SIGHUP and SIGTERM handlers instead.
- Every harness, opencode included, runs `helios run --in-window` in its window. The attach
  command is for users (`helios attach`, §9.2), since the session id is known only after §7.1
  step 8.
- Users attach to the session with `tmux -CC -L helios new -A -s helios`. `helios ps` works
  without tmux.

### 9.2 Commands

Every stderr message of `helios ps`, `helios attach`, `helios say` and `helios stop` starts with
`helios: `, except an argparse usage error. The worktree of a bead is the §7.3 path
`<hub>/<project.worktrees>/<bead>`; helios never reads a worktree path from `input.json`.
`helios attach`, `helios say` and `helios stop` refuse a bead id that does not match
`^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$` (`re.fullmatch`) with `helios: invalid bead id <id>` and
exit 2, before any filesystem or `bd` access.

- `helios ps [--json]`: rows come from the directories under `<runs>`, one per bead, using its
  highest attempt (§8.3 reading rules apply), sorted by bead id. An unreadable `<runs>` directory
  prints `helios: <message>` and exits 1; an unreadable bead directory under it is skipped like a
  directory without attempts, and ps still exits 0. A bead gets a row when that
  attempt is not finalized or the bead is `in_progress`. Each bead is read once with `bd show`
  through `helios.beads`; when that fails, `unit`, `kind` and the in-progress test fall back to
  `-` and false. Columns: `bead`, `unit`, `kind`, `harness` (key `harness` of the attempt's
  `input.json` when it is a string, else `-`), `state`, `attempt` (the attempt id), `age`,
  `worktree` (the §7.3 path when that directory exists, else `-`), `session`
  (`<harness>:<session_id>` when `harness` is a string, else `-`).
  Text output is tab-separated with that header line; `state` shows the stored state, plus
  ` (dead)` when the state is `launched` and the pid is not alive. Each field of a text row is
  rendered by replacing `\` with `\\`, tab with `\t`, LF with `\n`, CR with `\r`, and each of the
  characters U+000B, U+000C, U+001C, U+001D, U+001E, U+0085, U+2028 and U+2029 with its escape
  text (one backslash, then `x0b`, `x0c`, `x1c`, `x1d`, `x1e`, `x85`, `u2028` or `u2029`), then
  encoding as
  `sys.stdout.encoding` (utf-8 when unset) with `backslashreplace` and decoding back, so a row is
  always exactly one line with the header's column count; `--json` is unaffected, since
  `json.dumps` already escapes. The
  events file is read and indexed once per `ps` invocation, not once per row, by the exact
  selection rules below. Age is now minus `updated`:
  under 60 s `<n>s`, under 60 min `<n>m`, under 24 h `<n>h`, else `<n>d`, whole units rounded
  down. A future `updated` gives `0s`. An `updated` without a UTC offset, or not parseable as
  ISO 8601, is unknown, and an unknown age is `-`. `--json` prints a list of objects with those
  keys plus `alive` (bool) and `last_event`, written with `json.dumps(..., allow_nan=False)` so
  the output is always strict JSON. In `--json` every unknown value is null, `age`
  included, and `state` is the stored state without the ` (dead)` suffix; the suffix is text
  output only, and `alive` carries it in JSON. `last_event` is the `type` of the last line of
  the events file (§9.3) whose `bead` equals the bead, whose `attempt` equals the attempt
  id, and whose `type` is a string; a matching line whose `type` is not a string does not count
  and is skipped, so an earlier matching line can still supply it, else null. helios reads the
  events file as bytes, splits it on `b"\n"` only and decodes
  each line as UTF-8; a line that does not decode, does not parse, or is not a JSON object is
  skipped. With no rows, text prints the header only and `--json` prints `[]`. Exit 0.
- `helios attach <bead>`: uses the highest attempt. It exits 2 with a message on stderr when
  there is no attempt, when `input.json` has no `harness` or `harness` is not a string, when
  the §7.3 worktree directory is missing, or when `session_id` is null
  (`helios: no session recorded for <attempt_id>`; the
  session id is only known after the adapter's parse, §7.1 step 8). The harness comes from
  `input.json` `harness`; an unknown harness name (the lookup raises `ValueError`) exits 2 with
  `helios: <message>`. Otherwise it builds the `LaunchSpec` from the attempt paths with prompt
  `""`, taking `model`, `effort`, `extra_args`, `timeout` and `server_url` from configuration
  for the attempt's harness (§5), never from `input.json`. It calls the adapter's
  `attach_command(session_id, spec)`, replaces element 0 with the configured binary (§6.4),
  changes to the worktree directory and calls `os.execvp`. The harness lookup and the exec
  function are parameters of the library function, so tests inject them.
- `helios say <bead> "<text>" [--kind steer|answer]`: `--kind` defaults to `steer`. Text that
  starts with `-` must follow `--` on the command line (`helios say <bead> -- "-text"`). A bead
  with no directory under `<runs>`, or with no attempt directory, exits 2. An `OSError` while
  reading the latest attempt's session state prints `helios: <message>` to stderr and exits 1.
  Otherwise `say` always writes the message first (§9.4), then adds the comment
  `<kind>: [<msg_id>] <text>` with the replay rule of §7.5, then appends the event with type
  `<kind>`, source `orchestrator` and detail `<msg_id>`. The event's `attempt` field is the
  attempt id of the highest attempt as §8.3 reads it; the comment keeps the format
  `<kind>: [<msg_id>] <text>`
  with no attempt id. Adding the comment can fail in the replay lookup, in `bd` output that is
  not JSON or not UTF-8, in `bd` itself, or in a comment payload of the wrong shape; `say`
  catches `Exception` around the whole comment step, and any such failure is a failed comment: it
  prints `helios: <message>` to stderr (`helios: <exception type name>` when the message is
  empty), appends no event and exits 1, and the message stays in the inbox. When the highest
  attempt is live (as defined under `helios stop`), it prints `helios: queued <msg_id>` to
  stderr and exits 3. Otherwise it prints `<msg_id>` to stdout and exits 0. `say` never
  delivers: delivery belongs to `helios resume`.
- `helios stop <bead>`: exits 2 with `helios: no running attempt for <bead>` unless the highest
  attempt is live. Live means state `launched`, a `pid` that reads as non-null under §8.3, and
  the process itself live: its leader pid exists and its current `ps -o lstart=` text equals the
  attempt's `pid_start`, or, when the leader no longer exists, `os.killpg(pid, 0)` succeeds or
  raises `PermissionError`. A leader pid whose start time differs from `pid_start` is a reused
  pid: the attempt counts as not live, and helios never signals that pid. A null `pid_start`
  falls back to the process-group test alone. It writes `attempt-<n>/stop-requested` holding the
  UTC time (`yyyy-mm-ddThh:mm:ssZ` and a newline) with a temp file and `os.replace`, then
  signals the process group (`os.killpg(pid, SIGINT)`) whenever the attempt is live by this
  rule, including after the leader has exited, and exits 0, also when the group is already gone.
  A `PermissionError` from `os.killpg` counts like `ProcessLookupError` (macOS gives EPERM for a
  zombie group leader): exit 0. It never escalates and never writes `state.json`. The running
  `helios run` classifies an attempt whose `stop-requested` file exists as `interrupted` in
  step 8. The opencode abort route is not used in this wave.
- `helios resume <bead> ["<text>"]`: before taking the bead lock, resume checks only that the
  bead has an attempt, exiting 2 with a message on stderr when it does not. It then takes the
  bead lock (§8.3), exiting 2 with a message on stderr when it is held; once taken, it re-reads
  the highest attempt and applies the other refusals, exiting 2 with a message on stderr: the
  bead is closed, the highest attempt is live or not finalized, or its `session_id` is null.
  Still under the lock and before allocating, it also refuses when the
  §7.3 worktree directory is missing (`helios: worktree <path> is missing`) or the worktree is
  not usable, on another branch or detached (`helios: <message>`), exit 2. Otherwise it handles
  the inbox, then its own turn.
  - Inbox: only file names matching `^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.json$` count, sorted as
    strings and listed once at start. A file that is not a valid §9.4 message is skipped with a
    note on stderr. An id with an ack is skipped.
  - Each message is one turn. Then the text (default `Continue.`) is one turn, run when text was
    given or no message was delivered.
  - Before each turn, resume reads the highest finalized attempt afresh: its `session_id` is
    `resume_session` and its attempt id is `resumed_from`. The turn allocates attempt n+1 with
    that `resume_session` and the harness from the read attempt's `input.json`, and runs §7.1
    steps 5 to 11 (never `--again`). The turn prompt is `<text>` followed by `\n\nReport path:
    <report path>\n`; for a message, `<text>` is `[helios-msg <msg_id>] <message text>`.
  - Resume installs the same interrupt handling as `helios run` for its whole run. The interrupt
    is checked before every turn and immediately before an attempt is allocated; once set,
    nothing more is allocated and resume exits 4.
  - After a message turn is finalized, the ack is written whenever its `execution_status` is
    `completed`, `missing_output` or `invalid_output`, and only then is an interrupt honored.
    Any other status writes no ack and stops, and resume exits with that turn's code. Otherwise
    the exit code is the highest over the turns.
  - A turn that records a null `session_id` ends resume after that turn (the ack rule above
    still applies to it), with that turn's exit code and no refusal message. This is the one
    exception to "the exit code is the highest over the turns".
  - The envelope `steered` field lists the delivered msg_ids.

### 9.3 Events

`<hub>/.helios/events.jsonl`, one JSON object per line, appended under an exclusive `fcntl.flock`
on the file, with a single `write`: `{"ts", "source", "type", "bead", "attempt", "session",
"detail"}`. When the file is non-empty and does not already end with a newline, the write starts
with a newline before the JSON line. The line, newline included,
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

`unit new` first validates `<unit>` (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` with `re.fullmatch`); a
bad id exits 2 before the lock path is built. It then holds an exclusive, non-blocking
`fcntl.flock` on `<hub>/<project.runs>/unit-<unit>.lock` from before step 1 until it exits. The
lock file is opened with `os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW |
os.O_NONBLOCK, 0o644)` then `os.fstat`; anything that is not a regular file, an `ELOOP` from a
symlink or an `EISDIR` from a directory included, is `helios: cannot lock <path>: not a regular
file`, exit 2. A lock that
cannot be created otherwise exits 2 with `helios: cannot lock unit <unit>`. A held lock exits 2
with `helios: unit <unit> is being created by another process`.

1. Validate. Every failure exits 2 with a message on stderr before anything is written, the
   lock file aside:
   - `--stages` split on `,` has no empty or duplicate items, every item is a stage id of §3
     except `remember` (it never blocks, so it gets no bead), and the items are strictly
     increasing in the row order of the §3 table;
   - a verify stage (`verify-math`, `verify-code`, `verify-validate`) has an earlier non-verify
     stage in the list, else `<stage> has no earlier stage to verify`;
   - when `impl` or `validate` is present, `--files` (split on `,`, no empty items) and a
     `--test` that is not empty or whitespace-only are given;
   - every existing component of `<hub>/<project.units>` is a directory, checked with `lstat`; a
     component that is a symlink must resolve to a directory; the units directory, after
     resolving symlinks (`os.path.realpath`), lies inside the realpath of the hub, else
     `helios: units directory resolves outside the hub: <path>`;
   - the deepest existing component of `<hub>/<project.units>` is writable
     (`os.access(path, os.W_OK | os.X_OK)`), else `helios: units directory is not writable:
     <path>`;
   - the unit file `<hub>/<project.units>/<unit>.md` does not exist: refused when the path
     `lexists` (`os.path.lexists`), a broken symlink included;
   - every stage's author resolves (step 3).
2. For each stage in order, reuse the bead that is not closed and carries labels `unit:<unit>`
   and `kind:<stage>`; else create one titled `<unit> <stage>: <title>` with those labels and
   metadata `unit`, `kind`, `author`, and for `impl` and `validate` `files` and `test`. Metadata
   is passed as one JSON object so every value keeps its JSON type. When more than one bead that
   is not closed carries both labels for a stage, exit 2 naming the stage and the bead ids,
   before any bead is created.
3. `author` is the resolved agent spec of §5 for the stage: `model` takes `agents.model`, `impl`
   and `validate` take `agents.implement`, each verify stage takes its `agents.verify_*` key, and
   `frame`, `survey` and `report` take `agents.orchestrate`. `other` is valid only for verify
   stages; `other` configured for a non-verify stage is a refusal. For a verify stage, `other`
   resolves against the author of the stage's `parent` (the nearest earlier non-verify stage in
   the list): the `author` metadata of the parent bead when step 2 reuses it, else the
   configured author of the parent stage. A usable author is a string matching
   `^(claude|codex|opencode|agy)(:[^\s]+)?$` (`re.fullmatch`); the harness is the part before the
   first `:`, and being one of the four names is enough, whether or not it appears in
   `agents.verify_order`. A reused parent bead whose author is not usable is a refusal
   `helios: parent bead <id> has no usable author`, exit 2, checked before the
   `agents.verify_order` check, when the verify stage's configured author is `other`. When no
   harness in `agents.verify_order` differs from the parent author's harness, the stage is
   refused with `helios: no harness in agents.verify_order differs from <harness>`. Only an
   `other` resolution records the bare harness name; an explicit agent spec is recorded verbatim.
   Every refusal of steps 1 and 3 happens before any bd write.
4. Chain in list order: `bd dep add <later> <earlier>` for each adjacent pair, skipping a
   dependency that already exists. A verify stage also records metadata `parent` = the bead of
   its parent stage.
5. Write the unit file last: repeat the step 1 path checks (existing components, the
   realpath-inside-hub check, and writability) immediately before writing, then create missing
   parent directories and write with `open(path, "x")`. A failed re-check or an `OSError` here
   prints `helios: cannot write unit file <path>: <message>` and exits 1; the beads already
   created stay. It is `templates/unit.md` (through `helios.templates.path`) with `{unit}`,
   `{title}` and `{stages}` replaced in one `re.sub` pass, never `str.format`; `{stages}` is the
   stage ids joined with `,`. A crash before this step leaves no unit file, so a rerun reuses the
   beads made so far.
6. Print to stdout a tab-separated table with the header line `bead`, `stage`, `author`,
   `blocked-by` and one row per stage, `-` for none. A reused bead shows its recorded `author`
   metadata, or `-` when it has none. Exit 0.

Exact `bd` flags are read from `bd <command> --help` and used only inside `helios.beads`.
Tests run against a real `bd init` in a temporary repository when `bd` is on PATH, else skip.

## 11. Control

Candidates are the beads from `bd ready --json` (through `helios.beads`) with status `open` that
carry a `kind:<stage>` label naming a §3 stage, filtered by label `unit:<unit>` when a unit is
given, in bd's order. The §3 stage order comes from `helios.stages.STAGES`. A `bd` error while
listing candidates prints `helios: <message>` and exits 1.

`next` and `unit run` call `helios.run.run_many`, passing it `helios run`'s memory check
(`memory_has_for`) so preflight (§7.1 step 2) sees the configured memory backend, and then read
the envelope of the bead's highest attempt (`attempt-<n>/envelope.json`,
`Envelope.model_validate_json`); no attempt or no
envelope file gives none. A verdict is `helios.envelope.overall_verdict(envelope.report.findings)`
when a report exists, else none.

Before calling `run_many`, `next` and `unit run` snapshot the bead's highest attempt number and
whether that attempt was finalized. A higher number after the run is a new attempt, and its
envelope is read normally. An unchanged number whose attempt was not finalized before the run is
§8.4 in-place recovery, and its envelope is read normally too. An unchanged number whose attempt
was already finalized before the run, or no attempt before and after, is the stop reason
`execution failure for <bead>: no new attempt`, with exit code the run's code when nonzero, else
4.

When the run or the envelope read raises, the result is an execution failure with reason
`execution failure for <bead>: <exception type>: <message>`; its exit code is the run's code when
nonzero, else 4.

- `helios next [<unit>]` runs the first candidate whose kind is not in `control.stop_at`
  (`helios run`, §7.1) and prints one line from its envelope:
  `<attempt_id>\t<execution_status>\t<status or ->\t<verdict or ->\t<summary or ->`. With no
  such candidate it prints `helios: no ready bead` to stderr and exits 3. When the run returns
  without an execution failure but no envelope exists, `next` prints `helios: execution failure
  for <bead>: missing envelope` to stderr and exits with the run's code when nonzero, else 4.
  Otherwise it exits with the run's code, or per the execution failure rule above.
- `helios unit run <unit> [--until <stage>]` loops. Each round takes the first candidate,
  `stop_at` kinds included. It stops before running that candidate when its kind is in
  `control.stop_at`; otherwise it runs it and prints its line as `next` does. After a run it
  checks, in order:
  1. execution failure (the rule above).
  2. `impl` and `validate` kinds: report status other than `done`, reason `bead <bead> report
     status <status>`.
  3. verify kinds (`helios.stages.VERIFY_STAGES`): verdict other than `verified`, missing
     included, reason `bead <bead> verdict <verdict or ->`. Verify kinds are never judged on
     report status.
  4. any other nonzero run code, reason `bead <bead> run exited <code>`.
  5. the bead's kind is the `until` stage: `until stage reached`, exit 0.

  A kind that is neither `impl`, `validate` nor a verify kind (`frame`, `survey`, `model`,
  `report`, `remember`) skips checks 2 and 3. It also stops when no candidate remains (an open
  gate or blocker), and when a candidate was already run in this invocation (reason `bead <id>
  still ready after its run`). On stopping it prints `stopped: <reason>` to stdout.
- `unit run` exits 0 when it stopped after a successful `until` stage, 3 when it stopped at a
  `stop_at` kind or with no ready bead, else with the last run's code, or per the execution
  failure rule above at check 1.
- `control.default`: `manual` makes `unit run` refuse without `--until` (exit 2); `until` uses
  `control.until` when `--until` is absent; `auto` honors an explicit `--until` and otherwise
  runs until a stop condition other than `until`. An unknown `control.default`, an `--until`
  that is not a §3 stage, a `control.until` used when `--until` is absent that is not a §3
  stage, or an `--until` stage with no bead of the unit (label `kind:<until>`) exits 2. These
  value checks live in the control module, not in config loading.

## 12. Merge

`helios merge <bead> [--dry-run]` integrates an impl bead. It holds `<runs>/<bead>/lock` (§8.3)
for its whole run; a held lock exits 2. Creating that lock file is runtime state, not a write,
for the step 2 clean check or for `--dry-run`, and the lock file is never unlinked. Steps run in
the order 1, 2, 8, 3, 4, 5, 6, 7.

1. Find the evidence. The verify beads are the beads with label `unit:<unit>` (the bead's
   `unit`) whose metadata `parent` is the bead, listed through `helios.beads`; none means
   refuse. Every one must be closed with metadata `verdict` `verified`. Its evidence is
   `<runs>/<verify>/attempt-<n>/envelope.json`, with `<n>` its metadata `attempt`, which must
   exist and not be stale (§8.5). The impl bead must be closed. Any failure refuses.
2. Refuse unless the hub is on `main` and both trees are clean. The hub is on `main` when
   `git symbolic-ref --short HEAD` prints `main`. A tree is clean when
   `git status --porcelain=v1 -z --untracked-files=no` parses to no entries, NUL-separated (a
   rename or copy entry carries two paths); in the hub only, an entry every one of whose paths
   starts with `.beads/` is dropped first (bd's own export, dirtied by any bd write), so in the
   worktree a change under `.beads/` is dirt like any other path. When the worktree directory is
   missing and step 8's recovery does not apply, refuse `helios: worktree missing for <bead>`.
   Step 2 runs on every invocation, before recovery (step 8). Refuse unless the worktree is on
   branch `worktree-<bead>` (`git -C <worktree> symbolic-ref --short HEAD`, else
   `helios: worktree is not on branch worktree-<bead>`). Refuse also when
   `git diff --no-renames --name-only <main>...worktree-<bead>` lists any path under `.beads/`
   (`helios: branch changes .beads/`). Before any merge-base call, refuse when `output_commit`
   names no commit (`git cat-file -e <sha>^{commit}` fails) with `helios: verified output_commit
   <sha> is not a commit`, exit 2. The worktree HEAD passes when it equals the impl bead's
   `output_commit` metadata, checked first, or when the ordered list of patch ids of the
   non-merge commits in `merge-base(HEAD, main)..HEAD` equals the ordered list for
   `merge-base(output_commit, main)..output_commit` (the verified commits rebased); an empty
   list for the `output_commit` range never passes this check. A commit's patch id is computed
   from bytes: `git show --binary --no-textconv --no-ext-diff --no-color --format= <commit>`
   with stdout read as bytes, piped as bytes to `git patch-id --verbatim`, first field of its
   output. Otherwise, or when either range contains a merge commit, refuse with `helios:
   worktree HEAD <sha> is not the verified output_commit <sha>`, exit 2; so the commit step 6
   merges, once rebased, is the verified commit. Git output that `helios merge` parses or passes
   back to git is never decoded strictly: text is decoded with `surrogateescape` and printed
   with `backslashreplace`.
3. Record `main_before` as metadata `merge_main_before`. Then in the worktree run
   `git rebase main`. A nonzero exit is a conflict, `git rebase --abort`, `run=conflict`, stop,
   only when a rebase is in progress (`git rev-parse --git-path rebase-merge` or `rebase-apply`
   exists in the worktree). Otherwise print `helios: rebase failed:` plus git's stderr lines,
   each prefixed `helios: `, and stop with exit 4, without running abort.
4. Rerun `project.test` and `project.typecheck` in the worktree; failure stops.
5. If `main` moved since step 3, stop and say so; a second run starts over.
6. In the hub run `git merge --ff-only worktree-<bead>`; when it fails because `main` moved,
   exit 3. On success, write metadata `merge_commit` = the worktree HEAD.
7. When `origin` exists (`git remote` lists it), run `git push origin main`. Then remove the
   worktree: `git worktree unlock <path>` whenever `git worktree list --porcelain` lists the
   path (unlock works on a missing directory), `git worktree remove --force <path>` when the
   directory exists, `git worktree prune`, then `git branch -d worktree-<bead>`. Each of these is
   skipped when its target is already gone, so a rerun after a crash during step 7 exits 0.
8. Recovery runs after step 2 and before step 3. It applies only when metadata `merge_commit` is
   set, `git merge-base --is-ancestor <merge_commit> main` exits 0, and either the worktree
   directory is missing or its HEAD is an ancestor of `merge_commit`; it then writes the
   `merged` comment marker (step 6) when missing and skips to step 7. Otherwise, a worktree
   holding a commit not in `merge_commit` is a new merge: steps 3 to 7 run with a new
   `main_before`, new markers, and `merge_commit` overwritten.

Every step writes the comment `merge: [<bead>@<main_before>:<step>] <detail>` under the replay
rule of §7.5, where `<step>` is one of `rebased` or `conflict` (step 3), `tested` or
`test-failed` (step 4), `main-moved` (step 5), `merged` (step 6), `pushed` and `removed`
(step 7). `<main_before>` is always the value of metadata `merge_main_before`, so the markers
of a recovery run use it too.

Checks (`project.test`, `project.typecheck`) run with stdout and stderr captured, never
inherited; their output is discarded on success. Captured check output is decoded with
`errors=replace`. On failure it prints
`helios: <name> failed with exit <code>` then the last 50 lines of the combined captured output,
each line prefixed `helios: `, and stops with exit 5. Every stderr line `helios merge` prints
starts with `helios: `, `MergeError` messages included; stdout success and `--dry-run` text is
unprefixed. An unknown bead prints `helios: no bead <id>` and exits 2; any other exception
prints `helios: <message>` (or the exception type name when empty) and exits 1, never a
traceback.

A test checks that `bd set-state` on a closed bead does not reopen it. If it does, merge sets no
state on closed beads.

Exit codes: 0 merged or recovered; 2 refusal (steps 1 and 2, lock); 3 rebase conflict or `main`
moved; 5 test or typecheck failed; 4 push, worktree removal, or a rebase failure that is not a
conflict. Refusal and error messages go to stderr; success and `--dry-run` text go to stdout.

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
- Keys match `^[a-z0-9][a-z0-9._-]{0,199}$` with `re.fullmatch`, else `ValueError` naming the
  key. Keys are lowercase and at most 200 characters, so they never collide on a
  case-insensitive filesystem and never exceed file name limits. The key `schema_version` is
  refused too, on every backend, because bd 1.2.2 hides a memory with that key from
  `bd memories`.
- Line 2 is `json.dumps(header, sort_keys=True, ensure_ascii=False, allow_nan=False)` with
  the default separators; a `NaN`, `Infinity` or `-Infinity` anywhere in the header raises
  `ValueError` naming the key before anything is written. Every Python-level walk over a header
  (checking its strings, checking it is finite) catches `RecursionError` and raises `ValueError`
  naming the key too, before anything is written; an iterative walk is equally fine. Parsing
  requires the text to start with `helios-memory 1\n`, then one line holding a JSON object whose
  `status` key is `active`, `superseded` or `retracted`, parsed with
  `json.loads` using a `parse_constant` that raises and a `parse_float` that raises when
  `float(s)` is not finite, with `RecursionError` caught, all treated as invalid, then `\n`, and
  requires line 2 to equal exactly
  `json.dumps(header, sort_keys=True, ensure_ascii=False, allow_nan=False)` of the object it
  parses to; anything else raises `ValueError` naming the key. A file without a `status` key is
  a bad file, so export after import reproduces the tree byte for byte. The body may be empty.
  Line endings are never normalized.
- A body over 60000 UTF-8 bytes, a body or header string containing NUL, or a header string
  that is not encodable as UTF-8 raises `ValueError` naming the key, on every backend, before
  anything is written. The whole serialized value, line 1 plus line 2 plus the blank line plus
  the body, is at most 65000 UTF-8 bytes on every backend; exceeding it raises `ValueError`
  naming the key before anything is written.
- **beads backend**: `bd remember --key <key> "<value>"`, `bd recall <key>`, `bd memories`.
  `write` calls `bd` first (through `helios.beads`), then writes the export file under
  `memory.export_dir` via a temp file and `os.replace`. When it reads `bd memories`, the backend
  skips values that do not parse and prints a note naming each skipped key to stderr. A memory
  body must survive `bd remember` and `bd recall` unchanged. If bd changes it (for example a
  trailing newline), the beads backend stores the whole value in a form bd keeps, and a test
  round-trips bodies ending in no newline, `\n` and `\n\n`. `helios.beads` reads `bd` output as
  bytes and decodes it with `errors="replace"` for commands whose output helios does not parse
  (for example the `bd remember` echo), so a truncated echo never fails a write that bd
  completed.
- **files backend**: `<export_dir>/<key>.md` holding exactly the value format. `read` finds a
  key's file by exact name through a directory listing (`<key>.md` in `os.listdir`), never by
  opening a path the filesystem may match case-insensitively; `read("a")` with only `A.md`
  present raises `KeyError`.
- `write` refuses: a header without `source` as a non-empty string (`ValueError`), a `status`
  outside `active`, `superseded`, `retracted` (`ValueError`; a missing `status` is written as
  `active`), and any call while the environment has `HELIOS_BEAD` set (`PermissionError`, since
  workers never write memories). Other header keys are kept. `supersedes` never edits another
  memory.
- `export` writes each memory to `<directory>/<key>.md` and never deletes files. On either
  backend it raises `ValueError` naming the key or file for a value that does not parse; only
  the beads backend's reading of `bd memories` skips such values with the stderr note.
- `import_` checks every directory entry directly in the directory whose name ends in `.md`,
  dotfiles included. A name that fails the key rule, or an entry that is not a regular file
  after following symlinks (a broken symlink, or a directory named `x.md`), raises `ValueError`
  naming it, and nothing is written. It reads the format from the valid ones, in order sorted by
  key (the file stem). It runs every write refusal (header normalization, limits, NUL and
  encoding) in this check pass, for all files, before writing any; a bad file raises `ValueError`
  naming it and nothing is written. Export after import reproduces the tree byte for byte.
- `stale()` lists active memories whose `source` bead carries the label `truth:wrong`, and
  prints the same stderr note for values that do not parse. The bead id is `source` up to the
  first `#`; an id that does not match `^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$` (`re.fullmatch`) is
  not a bead, not stale, and `bd show` is never called for it. `bd show` reports a missing id by
  exiting 1 with a JSON error (`no issues found matching the provided IDs`);
  `helios.beads.Beads.show` turns that, an ambiguous prefix, and a partial id that `bd` resolves
  to a different bead, into `BeadNotFound` (a `KeyError` subclass). `stale()` treats only
  `BeadNotFound` as not stale (a bead that does not exist); any other error propagates and is
  never read as not stale. The result is sorted by key.
- `inject(keys)` returns `### <key>\n\n<body>` for each key, joined with `\n\n`, in the given
  order; a missing key raises `KeyError`.
- `helios.memory.open_backend(config)` returns the backend for `memory.backend` and raises
  `ValueError` `unknown memory backend '<value>'` for any other value.

## 14. Learned queue and gates

- `helios learned [--unit U] [--json]` lists the comment lines matching
  `^(learned|missing_context): \[([A-Za-z0-9][A-Za-z0-9._-]*)#([0-9]{1,9})#([0-9]{1,9})\] (.*)\Z`,
  compiled with `re.DOTALL`; ASCII digits only, at most 9 digits, and the bead group cannot span
  `]`, `#` or a newline. The groups are kind, bead, attempt number, k and text; the marker is the
  text `<kind>:<bead>#<attempt>#<k>` exactly as written, so `[b#01#1]` and `[b#1#1]` are distinct
  markers, and the listed bead is the one named in the marker, not the bead carrying the comment.
  It reads beads of any status that carry a helios `kind:<stage>` label (§3), plus every bead
  named by a marker found on them, deduplicates lines by marker across beads (the first
  occurrence in bd order wins), and keeps only lines without a matching `curated:` comment,
  which counts on any bead. When checking a marker's bead for that comment, a named bead that
  does not exist or whose bd read fails is skipped, and listing continues. `--unit U` keeps
  beads with label `unit:U`. Lines are sorted by unit
  (`-` when none), bead, attempt number and k as numbers, then the marker text as a tiebreak.
  Text output groups by unit, then bead, then attempt. `--json` prints a list of
  `{"unit", "bead", "attempt", "kind", "k", "text"}`. A `RuntimeError` from `bd` prints
  `helios: <message>` and exits 1.
- `helios learned --mark <kind>:<bead>#<attempt>#<k> memory|template|drop` accepts the marker
  only when its exact text is one of the markers of the current queue (curated ones included),
  compared as text, never as parsed numbers; otherwise it is `helios: unknown marker <marker>`,
  exit 2, nothing written. An unknown decision word exits 2; `--mark` with `--unit` or `--json`
  exits 2. Before writing, `--mark` checks whether a `curated:` comment for that marker already
  exists on the bead named in the marker; when that bead does not exist the check is skipped, as
  not yet curated, but a `bd` failure there for any other reason prints `helios: <message>`,
  exits 1, and writes nothing. It then adds `curated: [<kind>:<bead>#<attempt>#<k>] ->
  <decision>` under the replay rule of §7.5, on the bead named in the marker, or, when that bead
  does not exist, on the source bead, the bead whose comment carries the line, skipping the
  label step in that case. When the comment already exists, `--mark` still runs the label step.
  When no uncurated line of that bead remains, add label `curated` (idempotent).
- `helios gate [--json]` lists open gates from `bd gate list --json -n 0` with the beads each
  blocks, read from the gate's dependents in `bd show <gate> --json` (through `helios.beads`).
  Text output is one line per gate, `<gate>:` followed by a space then each blocking bead,
  space-separated, with no trailing space when it blocks none. bd output that is not a list, or
  an entry without a string `id`, prints `helios: unexpected bd gate output` and exits 1; only an
  exception from `helios.config` loading is a config error (exit 2), and any other
  `RuntimeError` from `bd` prints `helios: <message>` and exits 1.

## 15. Claims and program

### 15.1 Registry

`helios.program`: `Block(id, kind, expr, build=None, satisfies=(), relaxes=(), since=None,
replaces=None)` with kind in `set`, `parameter`, `variable`, `definition`, `objective`,
`constraint`, `stage`. `expr` is a string or a SymPy expression. `satisfies` and `relaxes`
become an empty tuple when given a `float` or `int`, and raise `ValueError` when given a `str`
(iterating one would silently give per-character ids). `build(model, data)` adds the
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
names the module; claims are collected from that module and its submodules, where a submodule is
a module present in `sys.modules` under the module's package prefix after importing it (helios
never walks the package directory). A claim name matches `^[A-Za-z0-9_][A-Za-z0-9._-]{0,199}$`
(`re.fullmatch`); `@claim` raises `ValueError` for any other name, which the command reports as
an import failure (§2.3). A record's module is the `__name__` of the module-level frame that
called `claim(...)` (walk outward from the call to the first frame whose code name is
`<module>` and whose globals hold a `__name__`, skipping a module-level frame without one; when
none is found, `claim()` raises `ValueError` `claim <name>: cannot determine owning module`),
never `func.__module__`, `__qualname__`, `co_firstlineno` or `repr`. A record is keyed by (module,
claim name). Before importing the
configured module, helios removes it and every module under its package prefix from
`sys.modules` and clears the registry, so collection sees exactly one import pass; the runner
does the same in its own process. Collection keeps records whose module is the configured module
or under its package prefix. Two collected records with the same claim name, in the same module
or in different ones, are a step 1 problem (`<name>: duplicate claim name`), found over every
collected claim before `--backend`, `--covers` or name filters apply; this covers colliding
lambdas, factories, `functools.wraps` pairs and `exec` blocks. Callable instances and
`functools.partial` objects are valid claims. Backends and their allowed methods and scopes come
from `backends.toml` (the project's `.agents/backends.toml`, else `templates/backends.toml`).

`helios claims check [--backend B] [--covers ID] [--timeout S]`:

1. Every claim covers at least one active block id (requirement ids do not count toward this, so
   a claim covering only requirement ids fails); unknown ids fail. An id matching
   `^R[0-9]+$` is a requirement id and is not checked against the registry; a retired block id
   counts as unknown.
2. The backend exists and allows the claim's method and scope. The declared method, scope and
   bound build a valid `Finding` with verdict `verified` (§4.2 rules 1 to 3: for example
   `bounded` needs `bound`, and `exhaustive_finite_check` is never `universal`); a declaration
   that does not is a step 2 problem. Steps 1 and 2 run for every selected claim before
   anything runs; any failure prints one line per problem to stderr and exits 2. `claims check`
   and `claims attack` remove `HELIOS_OMIT` from their own environment before loading the
   registry; only a mutation run sets it.
3. The claims module is imported with the hub and `<hub>/src` put in front of `sys.path`. Each
   non-`manual` claim runs as `python -m helios.claims.runner <module> <name>`, with cwd the
   hub, `start_new_session=True` and a timeout from `--timeout S` (whole seconds, an int greater
   than 0, default 600). The runner removes the module and its package prefix from
   `sys.modules` and clears the registry as above, imports the module, collects the records
   under its package prefix with that name, and runs the single one; zero or more than one is
   `{"error": ...}` naming the claim. The runner prints one JSON line: `{"result": true}`,
   `{"result": false}`, `{"finding": {...}}`, `{"other": "<type name>"}` or
   `{"error": "<traceback>"}`.
   - The runner redirects file descriptor 1 to file descriptor 2 (`os.dup2`) while the claim
     module is imported and the claim runs, and, once it has the protocol line, flushes stdio
     and writes that line with `os.write` to the saved original fd, then calls `os._exit`, so
     claim output never reaches the protocol channel and nothing runs afterward to disturb it.
   - The runner catches `BaseException` from the import and the claim, so `SystemExit` and
     `KeyboardInterrupt` are errors with a traceback. A return value that is neither a JSON
     boolean nor a `Finding` gives `{"other": "<type name>"}`.
   - helios waits on the runner process itself, never on its stdout pipe reaching EOF, since a
     surviving grandchild holding the descriptor open would hang a read. On timeout helios sends
     SIGKILL to the runner's process group, and the result is inconclusive with the note
     `timeout after <S> s`, where `<S>` is the given int. After the runner exits, for any
     reason, helios sends SIGKILL to that group too, ignoring `ProcessLookupError` and
     `PermissionError`, so no grandchild in the runner's process group survives. A grandchild
     that left the group (`setsid`) may survive. helios reads the runner's stdout and stderr with
     daemon threads calling `os.read` on the raw descriptors, and collection is bounded at 2 s
     after the runner exits: after the bound helios keeps what it has read, abandons the threads
     without closing their streams from the main thread, and continues. helios keeps at most the
     last 64 KiB of the runner's stderr and at most 16 MiB of its stdout, draining both to the
     end of the bound either way; stdout past 16 MiB makes the result inconclusive with the note
     `runner output over 16 MiB`. A pump thread reading an abandoned runner's output stops once
     it reaches its bound (64 KiB for stderr, 16 MiB for stdout).
   - `import ctypes` for the best-effort C stdio flush sits inside the flush's own `try`, so a
     runner without `ctypes` still runs claims.
   - Apart from a timeout, the result counts only when the runner exits 0 and its stdout is
     exactly one line holding one JSON object of the protocol. Otherwise it is inconclusive
     with the note `runner exited <code>` when the exit code is not 0, else
     `unparsable runner output`.
   - helios never changes a declared scope or bound, and changes the method only in the
     `False` case below. `True` gives a verified finding
     with `id` and `claim` set to the claim name and the declared `covers`, `method`, `scope`,
     `bound` and `artifact`. `False` gives a refuted finding with method `counterexample` when
     the backend allows it. When that finding would not validate, or the backend does not
     allow `counterexample`, the result is inconclusive with a note naming the reason.
   - A returned `Finding` keeps its verdict, method, scope, bound, artifact and notes, and
     helios sets its `id`, `claim` and `covers` to the claim's. When its method or scope is not
     allowed by the backend, the result is inconclusive with a note.
   - An `other` result is inconclusive with the note `claim returned <type name>` and no
     traceback file. An `error` result is inconclusive; its traceback is saved to
     `<hub>/.helios/claims/<name>.traceback.txt`, and `artifact` holds the hub-relative path
     `.helios/claims/<name>.traceback.txt`.
   - An inconclusive finding always carries the declared method, scope and bound.
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
  with each `\r\n`, `\r` and `\n` replaced by one space, and each id list is sorted, then joined
  with `, `. A half with no ids is dropped with its ` / ` separator, and the whole comment is
  dropped when both are empty. `## Retired` follows only when there are retired blocks, in the
  same line format sorted by id. Output ends with one newline, except that an empty registry
  (no active and no retired blocks) prints nothing, zero bytes. `--output PATH` writes the file
  instead of stdout. The same registry gives the same bytes.
- `helios program check`: call each active block's `build` on a recording model object and
  assert the set of recorded names, with any `[...]` suffix stripped, equals the active
  constraint and objective ids that have a `build`. Only names recorded while building
  `constraint` and `objective` blocks are compared; blocks are built in insertion order. The
  recording model returns a recorder for any attribute; calling a recorder records
  `kwargs["name"]` when it is a string and returns a new recorder. The recording model and every
  recorder also support indexing, slicing, iteration (yields nothing), `len` (0), truth (true),
  attribute and item assignment (`setattr`, `__setitem__`, both no-ops), and every arithmetic,
  comparison and bitwise operator in both operand orders, and unary
  operators; each returns a recorder except `len`, iteration, truth and assignment. The suffix
  stripped is everything from the first `[`. A `build` that raises is the mismatch line
  `error: <id>: <exception type>: <message>`. Print `missing: <ids>` and `unexpected: <names>`
  lines (sorted, joined with `, `, only when non-empty) and exit 5 on a mismatch (a failed
  check, as in §7.1), else 0.
- `helios program diff <rev1> <rev2>`: load the registry at two git revisions. Each revision is
  loaded in its own subprocess, run with `python -P` and cwd the extracted tree, from
  `git archive <rev>` extracted into a temporary directory, with `sys.path` set explicitly to
  `[<tree>/src, <tree>, <parent of the helios package>, <stdlib>, <platstdlib>, <each entry of
  site.getsitepackages() of the helios interpreter>]`, importing the module by name, so
  sibling imports resolve at the same revision and third-party packages such as SymPy import.
  The child imports `helios.program` (and everything it depends on), together with every other
  module its own script needs, before it replaces `sys.path`, so a tree file such as `json.py`,
  `dataclasses.py`, `pathlib.py` or `typing.py` never shadows them. The child returns its active
  ids to helios on a
  separate channel from its stdout, a file inside the temporary directory, so output during
  import cannot corrupt them and nothing is left in `TMPDIR`. The registry
  module path at a revision is
  `src/<module with dots as slashes>.py`, else `<module with dots as slashes>.py`; when neither
  exists at that revision, exit 2. A module that fails to import at a revision exits 2 with
  `helios: cannot import <module> at <rev>: <exception type>: <message>`. Only active ids are
  compared. Print every `+<id>` line (in
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
