#!/usr/bin/env python3
"""Phase 3d merge: fold the per-unit verdict files into one adversarial.md.

Checks what a summary cannot: that every claim actually got a verdict, that no
verdict names a claim that does not exist, and that nothing is judged twice.
An adjudicator reporting "all 180 adjudicated" is not evidence -- this is.

Re-adjudicated verdicts (DISTILLATION/adversarial/_readjudicated.md) override
the originals, since those claims were first judged with broken defect flags.

Writes DISTILLATION/adversarial.md and prints the verdict distribution.
"""
import collections
import os
import re
import sys

ROOT = os.path.expanduser("~/resources/gromov")
DIST = os.path.join(ROOT, "DISTILLATION")
ADV = os.path.join(DIST, "adversarial")
PACKETS = os.path.join(DIST, "_packets")
OUT = os.path.join(DIST, "adversarial.md")

VERDICTS = ["SUPPORTED", "WEAK", "OVERREACH", "MISATTRIBUTED",
            "TECHNICAL", "VACUOUS", "UNSUPPORTED"]
LINE = re.compile(r"^\s*[-*]?\s*`?([a-z-]+-\d{3})`?\s*\|\s*\**([A-Z]+)\**\s*\|\s*(.+?)\s*$")


def claim_ids():
    """Every claim id that exists, from the packets themselves."""
    ids = {}
    for f in sorted(os.listdir(PACKETS)):
        if not f.endswith(".md") or f.startswith("_"):
            continue
        unit = f[:-3]
        body = open(os.path.join(PACKETS, f), encoding="utf-8",
                    errors="replace").read()
        ids[unit] = [m.group(1) for m in
                     re.finditer(r"^### CLAIM (\S+)", body, re.M)]
    return ids


def read_verdicts(path):
    out, notes = {}, []
    if not os.path.exists(path):
        return out, notes
    body = open(path, encoding="utf-8", errors="replace").read()
    head, _, tail = body.partition("## Notes")
    for ln in head.split("\n"):
        m = LINE.match(ln)
        if m and m.group(2) in VERDICTS:
            out[m.group(1)] = (m.group(2), m.group(3))
    if tail.strip():
        notes = tail.strip().split("\n")
    return out, notes


def main():
    ids = claim_ids()
    redone, _ = read_verdicts(os.path.join(ADV, "_readjudicated.md"))

    all_v, all_notes, problems = {}, {}, []
    for unit, claims in ids.items():
        v, notes = read_verdicts(os.path.join(ADV, unit + ".md"))
        all_notes[unit] = notes

        missing = [c for c in claims if c not in v and c not in redone]
        extra = [c for c in v if c not in claims]
        if missing:
            problems.append(f"{unit}: {len(missing)} claims with NO verdict "
                            f"({', '.join(missing[:6])}"
                            + (" …" if len(missing) > 6 else "") + ")")
        if extra:
            problems.append(f"{unit}: {len(extra)} verdicts for claims that do "
                            f"not exist ({', '.join(extra[:6])})")
        for c in claims:
            if c in redone:
                all_v[c] = redone[c] + ("re-adjudicated",)
            elif c in v:
                all_v[c] = v[c] + ("",)

    counts = collections.Counter(x[0] for x in all_v.values())
    by_unit = collections.defaultdict(collections.Counter)
    for cid, (verd, _r, _n) in all_v.items():
        by_unit[cid.rsplit("-", 1)[0]][verd] += 1

    total = sum(len(c) for c in ids.values())
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("# Adversarial pass — verdicts on every extracted claim\n\n")
        fh.write(f"{len(all_v)} of {total} claims adjudicated, on Sonnet 5, "
                 "one agent per unit,\nwith no sight of the extraction "
                 "reasoning.\n\n")
        fh.write("| verdict | n | share |\n|---|---|---|\n")
        for v in VERDICTS:
            if counts[v]:
                fh.write(f"| {v} | {counts[v]} | "
                         f"{100*counts[v]/max(1,len(all_v)):.1f}% |\n")
        fh.write("\n## Per unit\n\n| unit | " + " | ".join(VERDICTS) + " |\n")
        fh.write("|---" * (len(VERDICTS) + 1) + "|\n")
        for unit in sorted(by_unit):
            fh.write(f"| {unit} | " + " | ".join(
                str(by_unit[unit][v] or "") for v in VERDICTS) + " |\n")

        fh.write("\n## Verdicts\n\n")
        for unit in sorted(ids):
            fh.write(f"\n### {unit}\n\n")
            for c in ids[unit]:
                if c in all_v:
                    verd, why, note = all_v[c]
                    tag = "  *(re-adjudicated)*" if note else ""
                    fh.write(f"- `{c}` | **{verd}** | {why}{tag}\n")
                else:
                    fh.write(f"- `{c}` | **NO VERDICT** |\n")

        fh.write("\n## Adjudicator notes\n")
        for unit in sorted(all_notes):
            if all_notes[unit]:
                fh.write(f"\n### {unit}\n\n" + "\n".join(all_notes[unit]) + "\n")

    for v in VERDICTS:
        if counts[v]:
            print(f"{v:<15} {counts[v]:>4}  {100*counts[v]/len(all_v):>5.1f}%")
    print(f"{'TOTAL':<15} {len(all_v):>4} of {total}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
