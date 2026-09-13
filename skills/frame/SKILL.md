---
name: frame
description: Turn a question, hypothesis or business problem in the owner's words into docs/PROBLEM.md with requirement ids, open decisions and a measurable success criterion. Use at the start of a unit or project, before any modelling or code.
---

# Frame

The output is `docs/PROBLEM.md`. It states the problem; it proposes no formulation.

## Sections, in this order

1. **Decision.** What will be decided, by whom, how often.
2. **Objective.** In business or scientific terms, in the owner's words where possible.
3. **Hard rules.** Things a solution must satisfy.
4. **Preferences.** Things that trade against each other, with the direction of each.
5. **Data at hand.** Sources, grain, size, freshness, what is confidential.
6. **Success criterion.** One measurable statement of what a good answer looks like.
7. **Out of scope.** Each item with the reason.
8. **Open decisions.** One ★ per choice that changes the model. Each names the stage it blocks.
9. **Questions.** The Q-list for the owner.

## Method

- Work with the owner in the session. Quote their words; mark every restatement as yours.
- Every sentence that carries a number or a rule gets a requirement id `R1`, `R2`, ... Ids are
  stable and never reused; a changed requirement gets a new id and the old one is struck through
  with a pointer.
- Separate rules from preferences explicitly. "Should" in the owner's words is a question until
  answered.
- A success criterion that cannot be measured is a ★, not a criterion.
- Name what would make the problem not worth solving (a kill test).
- Do not pick a method, a solver or a model class. That is `formulate`.

## Finish

Open one gate per ★ that blocks `model` (`bd gate`), or list them for the orchestrator when you
cannot. Then ask for `frame-review` in a fresh session on another harness. End with the report
block (`skills/report-block.md`); the only claim is "PROBLEM.md is complete", method `review`.
