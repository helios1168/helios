---
name: formulate
description: Turn docs/PROBLEM.md into candidate formulations (APPROACHES.md), a program registry with stable block ids tied to requirements, and the ## Model section of a unit file. Use for the model stage, after frame-review is WELL-POSED.
---

# Formulate

Inputs: `docs/PROBLEM.md` with R ids. Outputs:

- `docs/model/APPROACHES.md`: candidates `A0`, `A1`, ... Each states its class (LP, MIP, convex
  program, decomposition, heuristic with a bound, statistical estimator, simulation), which R it
  keeps, which it relaxes and why, the evidence it can produce, and a kill test.
- The program module named in `project.program`: blocks with stable ids (`S` sets, `P`
  parameters, `V` variables, `D` definitions, `O` objectives, `C` constraints, `ST` stages),
  each tagged `satisfies=[R..]` or `relaxes=[(R.., "why")]` (SPEC §15.1).
- The unit file's `## Model`: setup, numbered propositions each with its claim id, and the
  numbers to compute.

## The tractability ladder

Climb only as far as needed, and record every rung you pass.

1. State the exact problem, even if it is intractable at the instance size.
2. The smallest relaxation that solves at the instance size, with what it gives up in `relaxes`.
3. A decomposition when the exact problem separates, as stage blocks.
4. A heuristic only together with a bound or certificate that sits beside it.

At each rung name the evidence it can produce from `backends.toml`, so tractability and
verifiability are chosen together.

## Checks that catch the usual errors

- Units and scales agree across every term of the objective and every constraint.
- No objective adds quantities measured on different bases without an explicit exchange rate.
- Symmetry that will blow up the search is broken or noted.
- Big-M only where an indicator or a disjunction is not cleaner; every M has a justified value.
- A constraint that is really a preference moves to the objective, and the other way round.
- Every R is satisfied, relaxed with a reason, or listed out of scope in PROBLEM.md.
- Every block satisfies or relaxes at least one R.

## Rules

- Block ids are stable and never reused. A changed block gets a new id; the old one is retired.
- Propositions are claims. Write one claim stub per proposition with the `claims` skill.
- Do not verify your own propositions. The `verify-math` bead runs on another harness.

End with the report block (`skills/report-block.md`): one finding per proposition you stated,
verdict `inconclusive` and method `review` (they are unverified until verify-math).
