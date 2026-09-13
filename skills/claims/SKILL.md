---
name: claims
description: Write each proposition of a unit's ## Model as a checkable claim with a backend, method and scope, then run helios claims check. Use right after formulate, in the model stage.
---

# Claims

Input: the propositions in `## Model`. Output: the claims module named in `project.claims`,
one `@claim` per proposition (SPEC §15.2), and a green `helios claims check`.

## One proposition, one claim

- `covers` lists the block ids the proposition is about. A claim that covers nothing is wrong.
- Pick the backend by the claim's class:

| class | backend | method and scope |
| --- | --- | --- |
| identity, derivative, sign of a second derivative | `sympy` | proof, universal |
| feasibility or existence on bounded integer instances | `z3` | exhaustive_finite_check, bounded |
| first-order linear statement for all instances | `z3` | proof, universal |
| nonlinear with log or exp, up to a tolerance | `dreal` | numerical_certificate, bounded |
| numeric value on named instances | `numeric` | numerical_certificate, instance |
| property over generated inputs | `hypothesis` | empirical_support, bounded |
| optimal value of a solved model | `vipr` | certified_solve, instance |
| statement for all n that no tool above decides | `manual` or `lean` | proof, universal |

- A bounded check is labelled bounded, with the bound. Never tag a bounded run as universal.
- When no backend fits, write a `manual` stub with the statement, the hypotheses and what a proof
  must show. Do not force an encoding.
- A claim that uses a numeric guard (random substitution) says so in its docstring; the guard
  supports a sympy proof, it is not the proof.

## Finish

Run `helios claims check`. Every non-manual claim must pass. A failing claim means either the
proposition or the encoding is wrong: fix the one that is wrong and say which in `learned`.
End with the report block (`skills/report-block.md`).
