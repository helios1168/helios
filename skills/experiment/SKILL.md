---
name: experiment
description: Plan, run and review a numerical validation of implemented code, with the analysis plan fixed before outcomes are seen and every adopted result reproducible. Use for the validate stage.
---

# Experiment

## 1. Analysis plan, before any run

Write `experiments/<run>/PLAN.md` and commit it before the first result exists:

- the question and the conclusion each outcome would support;
- baselines, and why each is a fair comparison;
- the grid: every parameter with its range and spacing;
- seeds and repetitions per cell;
- exclusion rules (what counts as a failed run and what happens to it);
- tolerances, named from `[tolerance]`;
- the selection criterion for the adopted cell, stated as a rule;
- which cells are exploratory and which are confirmatory.

A plan changed after results exist is recorded as a change with the reason; confirmatory claims
then need a fresh run.

## 2. Run

Each run writes `experiments/<run>/manifest.json`: command, git commit, parameters, seeds,
package versions, sha256 of every input file, start and end time, exit status. Established
numerical jobs run as plain processes, not agent sessions. Keep raw output; filter only what you
read.

Add metamorphic checks where theory predicts them: scale, permute or translate the instance and
check the result moves as it should.

## 3. Review

`experiments/<run>/REVIEW.md`: one row per cell with the outcome against the plan, a verdict per
cell, and the adopted cell chosen by the stated rule. Name boundary and failure cases.

## 4. Freeze

The adopted result gets `PROVENANCE.md` (commit, manifest hash, command) and `reproduce.sh`.
The `verify-validate` bead reproduces every result behind a conclusion plus the boundary and
failure cases, within the stated tolerance.

End with the report block (`skills/report-block.md`): one finding per conclusion, method
`empirical_support` or `numerical_certificate`, scope `instance` or `bounded` with the grid in
`bound`.
