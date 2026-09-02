#!/usr/bin/env python3
"""Phase 3e prep: one file of every claim that survived adjudication.

Drops what the adversarial pass rejected, carries the verdict alongside each
surviving claim, and attaches the adjudicator's narrowed reading to every WEAK
one so the synthesis writes the defensible version rather than the reader's
original overreach.

No source context here -- that was for adjudicating. What the synthesis needs
is the claim, the quote that grounds it, and where it came from.

Writes SYNTHESIS_INPUT.md and prints what was kept and dropped.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_packets as B
import verify_quotes as V

ROOT = os.path.expanduser("~/resources/gromov")
DIST = os.path.join(ROOT, "DISTILLATION")
ADV = os.path.join(DIST, "adversarial.md")
OUT = os.path.join(ROOT, "SYNTHESIS_INPUT.md")

KEEP = {"SUPPORTED", "WEAK"}
VERDICT = re.compile(r"^- `([a-z-]+-\d{3})` \| \*\*([A-Z]+)\*\* \| (.+?)(?:\s*\*\(re-adjudicated\)\*)?$", re.M)


def main():
    verdicts = {m.group(1): (m.group(2), m.group(3))
                for m in VERDICT.finditer(open(ADV, encoding="utf-8").read())}
    if not verdicts:
        sys.exit("no verdicts parsed from " + ADV)

    units = sorted(f[:-3] for f in os.listdir(DIST) if f.endswith(".md"))
    kept, dropped = [], collections.Counter()

    for unit in units:
        body = open(os.path.join(DIST, unit + ".md"), encoding="utf-8").read()
        for i, e in enumerate(B.entries(body), 1):
            cid = f"{unit}-{i:03d}"
            verd, why = verdicts.get(cid, ("MISSING", ""))
            if verd not in KEEP:
                dropped[verd] += 1
                continue
            kept.append((cid, unit, e, verd, why))

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("# Gromov method corpus — claims surviving adversarial review\n\n")
        fh.write(f"{len(kept)} claims from 10 units of Gromov's writing. Each was extracted\n"
                 "verbatim from the OCR'd sources, machine-checked against the exact page\n"
                 "cited, and then judged by a fresh reader who saw the passage and its\n"
                 "surrounding pages but none of the extraction reasoning.\n\n"
                 "`SUPPORTED` = that reader found the move plainly stated in the passage.\n"
                 "`WEAK` = the move points the right way but the reader's phrasing outran the\n"
                 "text; the **Narrower reading** line says how far the text actually goes, and\n"
                 "that is the version you may use.\n\n")
        for cid, unit, e, verd, why in kept:
            fh.write(f"## {cid} — {e['name']}\n")
            fh.write(f"- **Quote:** \"{e['quote']}\"\n")
            fh.write(f"- **Move:** {e.get('move','')}\n")
            fh.write(f"- **Cite:** {unit} | doc {e.get('doc','?')} | "
                     f"{e.get('file','?')} | p.{e.get('page','?')}\n")
            fh.write(f"- **Verdict:** {verd}\n")
            if verd == "WEAK":
                fh.write(f"- **Narrower reading:** {why}\n")
            fh.write("\n")

    size = os.path.getsize(OUT)
    print(f"kept {len(kept)} claims -> {OUT}")
    print(f"  {size/1000:.0f} KB, ~{size/3500:.0f}k tokens")
    print("dropped: " + ", ".join(f"{k} {v}" for k, v in sorted(dropped.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
