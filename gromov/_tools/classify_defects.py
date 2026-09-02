#!/usr/bin/env python3
"""Grade the quotes verify_quotes.py could not match, by how much they matter.

The severities are the point. A reader that INVENTED a continuation is a
different problem from one that dropped a reference marker, and the adversarial
pass should spend its attention accordingly.

  INVENTED  the source breaks mid-sentence (OCR interleaved a caption or
            epigraph) and the reader wrote a smooth completion. The words in
            the quote are not Gromov's. Highest priority.
  SYMBOL    a mathematical symbol was changed (|cdot| -> |tau|, 2R -> -2R).
  REPAIRED  OCR lost a sub/superscript and the reader restored it from context;
            probably right, but it is not what the page says.
  ELISION   material dropped mid-quote without the required "…".
  MINOR     a reference marker or stray OCR punctuation; no effect on meaning.

Usage: classify_defects.py   (reads QUOTE_DEFECTS.tsv, rewrites it graded)
"""
import collections
import csv
import os
import sys

ROOT = os.path.expanduser("~/resources/gromov")
PATH = os.path.join(ROOT, "QUOTE_DEFECTS.tsv")

GRADED = {
    ("cognition-a", "01", 38): ("INVENTED", "source breaks after 'celebrating'"),
    ("cognition-a", "01", 51): ("INVENTED", "source breaks after 'this is why we'"),
    ("cognition-a", "01", 68): ("INVENTED", "source breaks after 'but where'"),
    ("cognition-b", "03", 49): ("INVENTED", "breaks at 'convince people of'"),
    ("cognition-b", "03", 58): ("INVENTED", "breaks at 'potentially an'"),
    ("cognition-b", "04", 18): ("INVENTED", "breaks at 'no reinforcement'"),
    ("cognition-b", "04", 45): ("INVENTED", "breaks at 'kindergarten explanations'"),
    ("cognition-b", "04", 51): ("INVENTED", "quote does not track the page text"),
    ("cognition-b", "05", 32): ("INVENTED", "breaks at 'N&T'; also N&T rendered NET"),
    ("cognition-b", "01", 10): ("INVENTED", "quote not on the cited page"),
    ("bio-dimensions", "04", 36): ("INVENTED", "appended 'is the optimal map'"),
    ("curvature-hopf", "03", 21): ("SYMBOL", "|tau|_g where source has |cdot|_g"),
    ("bio-dimensions", "04", 38): ("SYMBOL", "-2R_A where source has 2R_A"),
    ("cognition-b", "03", 55): ("SYMBOL", "arrow ⇝ where source has ∼"),
    ("bio-dimensions", "06", 25): ("SYMBOL", "arrow ⇝ where source has ∼"),
    ("curvature-hopf", "03", 67): ("REPAIRED", "OCR lost the subscript; reader restored A_eps"),
    ("bio-dimensions", "01", 23): ("REPAIRED", "OCR lost exponents; reader restored 10^9 / 10^100"),
    ("bio-dimensions", "06", 24): ("REPAIRED", "OCR lost the minus in 1-sigma"),
    ("cognition-a", "01", 125): ("ELISION", "unmarked elision of '2^9='"),
    ("cognition-a", "02", 100): ("ELISION", "dropped 'lea' from the arrow label"),
    ("bio-dimensions", "04", 45): ("ELISION", "unmarked elision of a defining clause"),
    ("cognition-b", "03", 62): ("ELISION", "dropped the ?1 / ?2 footnote markers"),
    ("cognition-a", "02", 105): ("MINOR", "dropped a '[45]' reference marker"),
    ("bio-dimensions", "06", 6): ("MINOR", "dropped a '[24]' reference marker"),
    ("curvature-hopf", "03", 55): ("MINOR", "source has a stray '()' from OCR"),
    ("cognition-a", "02", 9): ("MINOR", "prose matches; later clause differs"),
    ("cognition-b", "05", 18): ("MINOR", "stray ')' after an OCR music glyph"),
    ("bio-dimensions", "06", 7): ("MINOR", "notation spacing only"),
    ("bio-dimensions", "04", 3): ("MINOR", "notation only"),
    ("cognition-b", "05", 11): ("MINOR", "ellipsis variant"),
    ("cognition-b", "05", 45): ("MINOR", "prime vs apostrophe"),
    ("cognition-b", "03", 45): ("MINOR", "empty markdown link in source"),
    ("cognition-b", "04", 111): ("MINOR", "struck-through text in source"),
}
ORDER = ["INVENTED", "SYMBOL", "REPAIRED", "ELISION", "MINOR", "UNGRADED"]


def main():
    with open(PATH, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    out = []
    for r in rows:
        key = (r["unit"], r["doc_id"], int(r["cited_page"]))
        # rows are unique per quote now; the table is still keyed by page
        # because that is how they were graded by hand
        sev, why = GRADED.get(key, ("UNGRADED", "not yet reviewed by hand"))
        out.append((sev, why, r))
    out.sort(key=lambda t: (ORDER.index(t[0]), t[2]["unit"]))

    # Idempotent: drop any grading columns from a previous run before adding
    # them back, so re-running never stacks severity/note pairs.
    cols = [c for c in (rows[0].keys() if rows else [])
            if c not in ("severity", "note")]
    with open(PATH, "w", encoding="utf-8") as fh:
        fh.write("severity\tnote\t" + "\t".join(cols) + "\n")
        for sev, why, r in out:
            fh.write(sev + "\t" + why + "\t"
                     + "\t".join(r[c] for c in cols) + "\n")

    counts = collections.Counter(s for s, _w, _r in out)
    for k in ORDER:
        if counts[k]:
            print(f"{k:<10} {counts[k]}")
    if counts["UNGRADED"]:
        print("\nUNGRADED rows need a human read of the cited page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
