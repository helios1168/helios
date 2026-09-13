---
name: frame-review
description: Adversarially review docs/PROBLEM.md and decide WELL-POSED or UNDERSPECIFIED with the missing decisions listed. Use after frame, in a fresh session, never by the author of PROBLEM.md.
---

# Frame review

You did not write `docs/PROBLEM.md`. Your job is to find where two competent modellers would
build different models from it.

## Attacks

1. **Two readings.** For each requirement, write the second plausible reading. If it changes the
   model, it is a gap.
2. **Unmeasurable success.** Can the success criterion be computed from data that exists?
3. **Rule or preference.** Is any hard rule really a trade-off, or any preference really a rule?
4. **Hidden objective.** Does the objective combine quantities in different units or measures
   without saying how?
5. **Missing data.** Does any requirement need data the Data section does not list?
6. **Scope leaks.** Does an in-scope requirement depend on an out-of-scope item?
7. **Kill test.** Is there a stated condition under which the work stops?

## Output

One finding per requirement group, method `review`, scope `universal`.

- No gap: verdict `verified`, with the attacks tried in `notes`.
- A gap: verdict `inconclusive`; `notes` names the missing ★ or question and the requirement
  ids it touches.

Overall: WELL-POSED when every finding is verified, else UNDERSPECIFIED. Put the missing ★ list
in `followups`, one per line. End with the report block (`skills/report-block.md`).
