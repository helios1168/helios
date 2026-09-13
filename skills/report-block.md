# Report block

Every stage skill ends with two outputs.

1. The JSON report (SPEC §4.1) at the report path in the prompt. This is what helios reads.
   Each claim you checked is one entry in `findings` with separate `verdict`, `method`,
   `scope`, `bound`, `assumptions`, `artifact` and `checker`.
2. This text block as your final message, one block per claim, for the human reader:

```
CLAIM:     <restatement, with the source: file and section, or proposition id>
METHOD:    <proof | machine_checked_proof | exhaustive_finite_check | numerical_certificate |
            certified_solve | empirical_support | counterexample | review>
SCOPE:     <universal | bounded: the bound | instance: the instances>
ATTACK:    <what you tried in order to break it>
VERDICT:   <VERIFIED | REFUTED | INCONCLUSIVE | UNSUPPORTED>
EVIDENCE:  <what the checker returned: identity closed, oracle agreed within tolerance, ...>
ARTIFACT:  <repo-relative path, or the exact command to rerun>
CAVEATS:   <assumptions, what the check did not cover>
LEARNED:   <what the next run should know, or none>
```

With several claims, end with one line `VERDICT: <word>`: REFUTED if any claim is, else
INCONCLUSIVE if any is, else UNSUPPORTED if any is, else VERIFIED.

Rules:

- VERIFIED needs a real attack that failed. The ATTACK line is never empty.
- REFUTED needs a concrete counterexample or a reproduced mismatch beyond the stated tolerance.
- A search that found nothing is INCONCLUSIVE with its scope. A simplification that does not
  reach zero is INCONCLUSIVE. An unsat core is not a proof object.
- Never VERIFIED from a spot check of a universal claim, from a gap you did not close, or from
  code checking itself.
- LEARNED lines go in the report's `learned` list. Never write memory.
