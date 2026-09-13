# Native CLI output fixtures

Recorded by the orchestrator from live runs on 2026-09-13 (SPEC §6.3). Adapters in
`src/helios/harness/` parse these files; tests must not call the real CLIs. Re-record when a CLI
upgrade changes its output, and note the version here.

Versions: Claude Code 2.1.269, Codex CLI 0.154.0, opencode 1.18.30, agy 1.2.2.

The claude, codex and agy runs used a two-field schema (`status` in `done|blocked`, `summary`),
kept as `schema.json` where the CLI takes a path. Prompts: fresh "Return status done and summary
pong", resume "Now return status blocked and summary second turn", error a nonexistent model.

| harness | file | what it is | exit code |
| --- | --- | --- | --- |
| claude | `fresh.stdout` | one JSON object, `structured_output` = `{"status":"done",...}` | 0 |
| claude | `resume.stdout` | same session id, `structured_output` = `{"status":"blocked",...}` | 0 |
| claude | `error.stdout` | `is_error: true`, `api_error_status: 404`, no `structured_output` | 1 |
| codex | `fresh.stdout` | JSON lines: `thread.started`, `turn.started`, `item.completed`, `turn.completed` | 0 |
| codex | `fresh-last-message.json` | the `-o` file of the fresh run | |
| codex | `resume.stdout` | same `thread_id` | 0 |
| codex | `resume-last-message.json` | the `-o` file of the resume run | |
| codex | `error.stdout` | `error` and `turn.failed` events; no `-o` file was written | 1 |
| agy | `fresh.stdout` | one JSON object, `status: SUCCESS`, `conversation_id`, `structured_output` | 0 |
| agy | `resume.stdout` | same `conversation_id`, `num_turns` grows | 0 |
| agy | `error.stdout` | `status: ERROR`, empty `conversation_id`, `error` text | 1 |
| opencode | `fresh.stdout` | JSON lines for a one-word reply: `step_start`, `text`, `step_finish` | 0 |
| opencode | `tools.stdout` | a full bead turn with `tool_use` events (bash, read, edit, write) | 0 |
| opencode | `resume.stdout` | a second turn on the same `sessionID` (run with `-s`) | 0 |
| opencode | `error.stdout` | one `error` event, `error.name: UnknownError` | 1 |
