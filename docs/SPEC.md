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
- **turn**: one prompt and response inside an attempt's native session. Resume adds a turn.
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
- `partial`: progress made, work remains inside the same contract. helios resumes once.
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
2. `interrupted`: helios received SIGINT or `helios stop` stopped the attempt. This holds whatever the child printed or wrote.
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
  `POST <server_url>/session/<session_id>/abort`.
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
- `parse(spec, exit_code, stdout_path)` never raises and never reads stderr. Input handling:
  - A missing `stdout_path` gives session_id None and native_error `no stdout`.
  - claude and agy: `json.loads` of the stripped text. A decode failure or a value that is not
    an object gives native_error `invalid JSON`.
  - codex and opencode: every line is stripped. Blank lines and lines that are not JSON objects
    are skipped, with one note `skipped <N> non-JSON lines` when N is not 0. No JSON object at
    all gives native_error `no events`.
- session_id is the first non-empty value of: claude `session_id`; codex `thread_id` of the
  first `thread.started`; opencode the top-level `sessionID` of the first line that has one;
  agy `conversation_id`. An empty string counts as None. session_id is returned even when
  native_error is set.
- When the input rules above set no native_error, it is set when `exit_code` is an int other
  than 0, or when the output signals failure: claude `is_error` true; codex any `turn.failed`,
  or no `turn.completed`; opencode any `error` event, or a last event that is not
  `step_finish`; agy a `status` other than `SUCCESS`. `exit_code` None is not an error by
  itself. The text is a non-empty string, the first that applies: claude `result`; codex
  `error.message` of the last `turn.failed`, else `message` of the last `error` event; opencode
  `error.data.message` of the first `error` event, else its `error.name`; agy `error`; else
  `exit code <N>` when the exit code is not 0, else `turn did not complete`.
- structured is None whenever native_error is set, and always None for opencode. Otherwise it
  is claude or agy `structured_output`, or for codex the JSON in `<raw_dir>/last-message.json`.
  Only a JSON object counts. A missing key or file gives None and the note
  `no structured result`; invalid JSON or a value that is not an object gives None and the note
  `structured result is not a JSON object`. Never fall back to claude `result` or agy
  `response`.
- notes is empty unless a rule above adds one.
- `helios.harness.get(name)` returns the adapter for `claude`, `codex`, `opencode`, `agy` and
  `fake`, and raises ValueError naming any other name.

## 7. `helios run`

`helios run <bead>... [--harness H] [--again] [--timeout S] [--dry-run] [--max-parallel N]
[--tmux]`

### 7.1 Steps

1. Load config (§5). Read each bead with `bd show <id> --json` through `helios.beads` (the only
   module that calls `bd`). Metadata values may arrive JSON-encoded as strings; decode them.
2. Preflight (`helios.preflight`), all failures exit 2 before anything is created:
   - `impl` and `validate` need `files` and `test`; verify kinds need `unit` and `parent`.
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
6. Assemble the prompt (§7.2); write `prompt.md` and `input.json` with the input hashes.
7. Launch with `subprocess.Popen(argv, cwd=worktree, stdin=<stdin_text, else DEVNULL>,
   stdout=stdout file, stderr=stderr.log, start_new_session=True)`, environment plus
   `HELIOS_BEAD`, `HELIOS_ATTEMPT`, `HELIOS_HARNESS`, `HELIOS_HUB`, `HELIOS_REPORT`. Record
   `launched` with the child pid in `state.json` as soon as `Popen` returns, before waiting.
   Every signal goes to the child's process group (`os.killpg`). The stop sequence is SIGINT,
   wait up to 10 s, SIGTERM, wait up to 5 s, SIGKILL. A wait ends early only when the whole
   group is gone (`os.killpg(pgid, 0)` raises `ProcessLookupError`), not when the leader exits.
   On timeout run the stop sequence and record `timed_out`. The session id comes only from the
   adapter's `parse`; `helios.run` never reads harness inputs such as `HELIOS_FAKE_SCRIPT` (an
   adapter may read its own).
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
captured report), `checks/`, `envelope.json`. The agent writes its report inside the worktree at
`<worktree>/.helios/attempt-<n>/report.json`, a path unique per attempt.

### 8.2 States

`allocated`, then `launched`, then one of `native_completed`, `interrupted`, `timed_out`,
`crashed`, `launch_failed`, then `validated` or `invalid`, then `finalized`. `state.json` is
`{"state", "attempt_id", "pid", "session_id", "execution_status", "updated"}`, where
`execution_status` is null until step 8 classifies the attempt and never changes after. Every
write carries all six keys, and a transition keeps the stored `execution_status` (and `pid`
and `session_id`) unless it sets a new value, so the key survives `validated`, `invalid`,
`finalized` and every resume. It is written to a temp file and
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
channel of the current process. The input hashes (sha256 of the prompt, the bead JSON, each doc
and memory value, the unit file) are fixed in `input.json` before launch.

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

A verify envelope is stale when any `input_hashes` value differs from the current value, or its
`base_commit` is not an ancestor of the impl bead's current `output_commit`. `helios merge`
refuses stale evidence (§12).

## 9. Sessions, messages and events

### 9.1 Windows

`helios run --tmux` launches each attempt in window `<bead>` of session `helios` on the tmux
server `tmux -L helios` (created with `tmux -L helios new-session -d -s helios` when absent),
running `helios run <bead> --in-window` so the output is visible. For opencode with a server,
the window runs the attach command and the attempt runs headless. Users attach with
`tmux -CC -L helios new -A -s helios`. `helios ps` works without tmux.

### 9.2 Commands

- `helios ps [--json]`: one row per bead with an attempt that is not finalized or whose bead is
  in progress: bead, unit, kind, harness, state, attempt, age, worktree, session. State comes
  from `state.json`, whether the pid is alive, and the last event.
- `helios attach <bead>`: exec the adapter's attach command with cwd the worktree.
- `helios say <bead> "<text>" [--kind steer|answer]`: write a message (§9.4), deliver it by
  resuming the session when idle, log a `steer:` or `answer:` comment. Refuse when the pid is
  alive and print that the message is queued in the inbox.
- `helios stop <bead>`: SIGINT the attempt pid (or the opencode abort route), record
  `interrupted`.
- `helios resume <bead> ["<text>"]`: new turn on the same session and attempt; default text
  `Continue.`; the turn writes `stdout-turn-<k>.jsonl`.

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
`[helios-msg <msg_id>]` prefixed to the text, and after the resumed turn completes writes
`<runs>/<bead>/acks/<msg_id>`. Before delivering, skip any id already acked. A crash between
delivery and ack can deliver twice; the marker lets the agent and the transcript audit
recognise the duplicate. Workers never message workers.

## 10. Units and chains

### 10.1 Unit file

Created from `templates/unit.md` with `{unit}`, `{title}` and `{stages}` substituted: title
line, `Status:` (`open`, `in-progress`, `done`, `dropped`), `Stages:`, and sections `## Brief`,
`## Model`, `## Verify`, `## Code verify`, `## Validate`, `## Report`, each holding `_empty_`.

### 10.2 `helios unit new <unit> "<title>" --stages s1,s2,... [--files glob,...] [--test CMD]`

1. Refuse when the unit file exists or a stage id is not in §3.
2. Write the unit file.
3. For each stage create a bead titled `<unit> <stage>: <title>` with labels `unit:<unit>` and
   `kind:<stage>`, metadata `unit`, `kind`, `author` (resolved agent spec, §5), and for `impl`
   and `validate` the given `files` and `test`.
4. Chain the beads in stage order: each stage is blocked by the previous one. A verify stage
   records `parent` = the bead it verifies (the nearest earlier non-verify stage).
5. Print a table: bead, stage, author, blocked-by.

Exact `bd` flags are read from `bd <command> --help` and used only inside `helios.beads`.
Tests run against a real `bd init` in a temporary repository when `bd` is on PATH, else skip.

## 11. Control

- `helios next [<unit>]`: the first bead from `bd ready --json` (filtered by label
  `unit:<unit>` when given) whose kind is not in `control.stop_at`; run it (§7); print the
  envelope summary.
- `helios unit run <unit> [--until <stage>]`: repeat `next` for the unit. Stop at: the `until`
  stage (after running it), a stage in `stop_at` (before running it), no ready bead (an open gate
  or blocker), a report status other than `done`, a verdict other than verified, or an execution
  failure. Print why it stopped.
- `control.default = "manual"` makes `unit run` refuse without an explicit `--until`;
  `"until"` uses `control.until` when `--until` is absent.

## 12. Merge

`helios merge <bead> [--dry-run]` integrates an impl bead:

1. Refuse unless the bead is closed, its verify bead (the bead whose `parent` is this bead) is
   closed with verdict verified, and that verify envelope is not stale (§8.5).
2. Refuse unless the hub is on `main` with a clean tree.
3. Record `main_before`. In the worktree run `git rebase main`; on conflict run
   `git rebase --abort`, set `run=conflict`, stop.
4. Rerun `project.test` and `project.typecheck` in the worktree; failure stops.
5. If `main` moved since step 3, stop and say so; a second run starts over.
6. In the hub `git merge --ff-only worktree-<bead>`; record the candidate commit on the bead.
7. Push `main` when a remote named `origin` exists. Then `git worktree unlock`, `git worktree
   remove`, and delete the branch.
8. Recovery: when `main` already contains the candidate commit, skip to step 7.

Every step writes `merge: [<bead>] <step>` comments with the replay rule of §7.5.

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

- **beads backend**: `bd remember --key <key> "<value>"`, `bd recall <key>`, `bd memories`.
  Every `write` also writes the export file.
- **files backend**: `<export_dir>/<key>.md` holding exactly the value format.
- `export` writes each memory to `<directory>/<key>.md`; `import_` reads the same format;
  export after import reproduces the tree byte for byte.
- `stale()` lists active memories whose `source` bead carries the label `truth:wrong`.
- A write without `source` is refused. Workers never write memories.
- `inject(keys)` returns each value under a `### <key>` heading, in the given order; a missing
  key raises.

## 14. Learned queue and gates

- `helios learned [--unit U] [--json]`: every `learned:` and `missing_context:` comment line,
  across beads of any status, whose `[attempt_id#k]` marker has no matching `curated:` comment.
  Grouped by unit, then bead, then attempt.
- `helios learned --mark <attempt_id#k> memory|template|drop`: add `curated: [attempt_id#k] ->
  <decision>`. When every line of a bead is marked, add label `curated`.
- `helios gate [--json]`: open gates (`bd gate list --json`) with the beads each blocks.

## 15. Claims and program

### 15.1 Registry

`helios.program`: `Block(id, kind, expr, build=None, satisfies=(), relaxes=(), since=None,
replaces=None)` with kind in `set`, `parameter`, `variable`, `definition`, `objective`,
`constraint`, `stage`. `expr` is a string or a SymPy expression. `build(model, data)` adds the
block to a solver model using `name=<id>` or `name=<id>[...]`. `Registry` holds active and
retired blocks; ids are unique and never reused. A project's program module
(`project.program`) exposes `REGISTRY`.

### 15.2 Claims

`helios.claims`: decorator `@claim(name, covers, backend, method, scope, bound=None,
artifact=None, redundant=())` registers a zero-argument callable returning `True`, `False` or a
`Finding`. `project.claims` names the module. Backends and their allowed methods and scopes come
from `backends.toml` (the project's `.agents/backends.toml`, else `templates/backends.toml`).

`helios claims check [--backend B] [--covers ID]`:

1. Every claim covers at least one active block id; unknown ids fail.
2. The backend exists and allows the claim's method and scope.
3. Run every non-`manual` claim in a subprocess with a timeout. `True` gives a verified finding
   with the declared method and scope. `False` gives a refuted finding only when the backend
   allows `counterexample`, else inconclusive. An exception gives inconclusive with the
   traceback saved and its path in `artifact`.
4. Print the findings; exit 0 only when every non-manual claim is verified.

`helios claims attack <claim>`: for each covered block, rebuild the registry without it and
rerun the claim. Each mutation has an expected effect: `must_fail` by default, `may_pass` for
blocks listed in the claim's `redundant`. A `must_fail` mutation that still passes is reported
as a surviving mutation. A surviving mutation is a finding to review, not proof of a defective
claim.

### 15.3 Program commands

- `helios program show [--output PATH]`: deterministic markdown: one section per kind in the
  order of §15.1, one line per block `ID  expr  # satisfies R.. / relaxes R..`, then
  `## Retired`. The same registry gives the same bytes.
- `helios program check`: call each active block's `build` on a recording model object and
  assert the set of recorded names, with any `[...]` suffix stripped, equals the active
  constraint and objective ids that have a `build`. Report missing and unexpected names.
- `helios program diff <rev1> <rev2>`: load the registry at two git revisions (`git show
  <rev>:<path>` into a temporary module) and print `+ID` and `-ID` lines.

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
