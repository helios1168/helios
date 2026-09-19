# General purpose helios

A stage set is data (SPEC §3). A hub declares its stages as `[[stage]]` entries in
`.agents/workflow.toml` and helios reads them; declaring one replaces all ten shipped research
stages of §3.2. The dispatch core names no stage, so a hub whose only stage is called `solo` goes
through `helios run` and `helios merge` the way a research hub's `impl` bead does. General purpose
use is the same mechanism as research use with a different declaration.

This guide takes a repository from nothing to a merged bead of a stage the hub invented. It uses no
research stage and no unit file.

## What the hub needs

Everything lives at the hub root, the directory holding `.agents/workflow.toml`:

- a git repository whose default branch is `main`, and a bd database (`bd init`);
- `.agents/workflow.toml`, the declaration below;
- `skills/<id>/SKILL.md` for every declared stage id;
- `AGENTS.md` with a `## Worker contract` section. `helios run` pastes that section into every
  prompt verbatim (§7.2) and fails without it;
- `.gitignore` entries covering `.helios/runs/` and `.claude/worktrees/`. Preflight refuses
  otherwise with `preflight: .gitignore must ignore .helios/runs/`.

Run every `helios` command from inside the hub. Config loading walks up from the current directory
to the first `.agents/workflow.toml` (§5).

## One stage

```toml
[project]
test = ""

[agents]
worker = "claude"

[[stage]]
id = "solo"
author = "worker"
gate = "report"
ownership = "none"
```

Why each line is there:

- `project.test = ""` disables the default `uv run pytest -q`. That default runs at merge (§12 step
  4), and in a repository without pytest it fails with `helios: test failed with exit 5`. A hub with
  a real check names the command here, or names an ordered list of `[[project.check]]` entries.
- `[agents]` is open keyed (§5): any key is a role a stage's `author` may name. `worker` is not one
  of the six roles the research set uses, so the hub declares it. An `author` naming an undeclared
  role is refused with `helios: stage solo author 'worker' is not a key of [agents]`.
- `id` is the stage. It is the bead's `kind`, its `kind:<id>` label and the directory of its skill
  (§3.1). It matches `[a-z][a-z0-9-]*` and is unique in the file.
- `author` names who runs the stage. `helios run --harness <name>` overrides it for one run.
- `gate = "report"` judges a finished bead on its report status: the bead closes when execution
  completed, the report is `done` and every helios check passed (§7.5). It is the default value, so
  a shorter declaration may leave it out.
- `ownership = "none"` lets the bead change any path (§7.4). A stage whose beads carry no `files`
  needs it, because the default `files` would reject every changed path. Under every mode `.git`,
  `.beads/`, `.helios/`, `.agents/`, the memory export directory and `project.confidential` stay
  rejected.

Three fields are absent. `requires` defaults to empty, which demands nothing of the bead.
`verifies` is absent, so this stage checks no other stage and its worktree starts at `main`.
`scaffold` defaults to true, which offers the stage to `helios unit new`.

Declaration order is stage order (§3.1): it sets the candidate order of §11 and the order
`helios unit new` chains a unit in.

## The skill

`skills/solo/SKILL.md`, in the hub, not in the worktree and not under `.agents`:

```markdown
---
name: solo
---

# Solo bead

Do exactly what the bead asks. Write the report JSON to the path the prompt names.
```

The front matter is stripped and the rest becomes the prompt's `## Role` section (§7.2). Preflight
refuses a bead whose skill file is missing with
`preflight: <bead>: skills/solo/SKILL.md does not exist in the hub`.

## The bead

```bash
bd create --title "add the release note" --type task \
  --metadata '{"kind":"solo","unit":"notes","test":"test -s NOTES.md"}' \
  --labels kind:solo,unit:notes --silent
```

`kind` is the only field the `solo` stage needs. A bead reads its stage from metadata `kind`, or
from a `kind:<id>` label when that metadata is absent (`src/helios/beads.py`); `helios next` and
`helios unit run` list candidates by the label, so set both. `unit` is not demanded by `requires`,
and `helios merge` uses it to find the evidence, so set it too. A bead whose `kind` names no
declared stage is refused with
`preflight: <bead>: 'nope' is not a declared stage; declared: solo`.

What each `requires` entry demands of the bead at preflight (§7.1 step 2):

| entry | demand |
| --- | --- |
| `files` | metadata `files`, the paths the bead may own |
| `test` | metadata `test`, a shell command helios runs in the worktree |
| `unit` | metadata `unit` |
| `parent` | metadata `parent`, and `output_commit` metadata on that parent bead |
| `model` | a `## Model` section of at least 200 characters of body in `<project.units>/<unit>.md` |

A bead carrying a `test` has it run as the check named `test` whether or not `requires` asks for
one. `model` is the entry a hub with no unit file cannot satisfy.

## Run it

```bash
helios run <bead>
```

`helios run` prints nothing on success and exits 0. It creates the worktree
`.claude/worktrees/<bead>` on branch `worktree-<bead>`, assembles the prompt from the skill, the
worker contract and the bead, launches the author's harness, runs the bead `test` and the
configured `run` checks, then checks ownership (§7.1). If ownership passed it commits the changed
paths as `<bead>: <report summary>` and records the worktree HEAD as the bead's `output_commit`
metadata. Under gate `report` a `done` report closes the bead. The other exit codes are in §7.1.

## Merge it

`helios merge` refuses until the bead has evidence (§12 step 1): a bead carrying the label
`unit:<unit>` whose metadata `parent` is this bead, closed, with metadata `verdict` of `verified`
and a readable envelope. Only a stage whose `gate` is `verdict` writes `verdict` metadata back
(§7.5), so a hub that intends to merge declares a second stage to produce it:

```toml
[[stage]]
id = "check"
author = "other"
verifies = "solo"
requires = ["parent"]
gate = "verdict"
ownership = "artifacts"
```

- `verifies = "solo"` makes `check` the check on `solo`. The solo bead is this bead's `parent`, and
  that bead's `output_commit` is the base commit of this bead's worktree instead of `main` (§7.3).
- `author = "other"` asks for a harness differing from the one that wrote the work, resolved through
  `agents.verify_order` (§5). Any declared role works here instead; `other` is valid only on a stage
  that declares `verifies`.
- `gate = "verdict"` judges the bead on the overall verdict of its findings and writes that verdict
  to the bead, which is what merge reads.
- `requires = ["parent"]` turns a missing pointer into a preflight refusal rather than a failure at
  worktree time.
- `ownership = "artifacts"` limits the bead to `<project.verify_artifacts>/<unit>/`.

The verify agent's report needs at least one finding, and every finding's `verdict` must be
`verified` for the overall verdict to be (§4.2). Then:

```bash
bd create --title "check the release note" --type task \
  --metadata '{"kind":"check","unit":"notes","parent":"<work bead>"}' \
  --labels kind:check,unit:notes --silent
helios run <check bead>
helios merge <work bead>
```

Merge rebases the branch onto `main`, reruns the configured `merge` checks, merges fast forward,
pushes when `origin` exists, removes the worktree, and prints `merged`.

## What a hub gives up

Declaring stages replaces the research pack, not the core.

The unit chain of §10.2 thins out. `helios unit new <unit> "<title>" --stages solo` still creates
the bead and writes `<project.units>/<unit>.md`, but the unit file template belongs to the research
pack: its sections are `## Brief`, `## Model`, `## Verify`, `## Code verify`, `## Validate` and
`## Report` whatever the hub declared, and nothing reads them unless a stage declares
`requires = ["model"]`. With one stage there is nothing to chain, so the command creates one bead
and no dependency.

The control defaults change. `control.until` defaults to `""` and `control.stop_at` to `[]` for a
hub that declares any stage (§5), rather than to `verify-code` and `frame, model, report`. So
`helios unit run <unit>` under the default `control.default = "manual"` refuses with
`helios: manual control requires --until`, and the stage has to be named:
`helios unit run notes --until solo`. `helios next <unit>` needs no stop list and is unaffected.

The loops of §3.2 do not exist. A refuted verify does not reopen the stage it checked, no bug bead
is opened on the unit, and no `remember` stage drains the learned queue of §14. Those are pack
semantics. §11 stops the loop and prints the reason; the recovery is the orchestrator's.

Some commands have nothing to read. `helios claims check`, `helios claims attack` and the three
`helios program` commands refuse with `helios: project.program is not configured`, or the same
refusal naming `project.claims`, because they read the §15 modules a research hub declares.
`helios gate` reports the ★ gates of `docs/PROBLEM.md`, which only the `frame` stage writes, so it
finds none.

What keeps working with a declared stage: `helios run`, `helios merge`, `helios next`,
`helios unit run --until <stage>` and `helios unit new`.
