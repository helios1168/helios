#!/usr/bin/env python3
"""Phase 3a: mark pages that look like method-talk, per unit.

This is deliberately NOT a filter -- the reader still sees the whole bundle.
It produces the coverage audit used in 3c: if a reader's single pass silently
truncated, whole documents will appear here with no matching citation.

Runs on the bundles (which carry <<<DOC>>>/<<<PAGE>>> markers), not on the
Tier-1 text, so page numbers line up with what the reader can cite.
"""
import os
import re
import sys

ROOT = os.path.expanduser("~/resources/gromov")
UNITS = os.path.join(ROOT, "units")

# Language Gromov uses when talking about how to attack a problem rather than
# about a result. Over-inclusive on purpose; precision is the reader's job.
SIGNALS = [
    r"\bthe point (is|of)\b", r"\bone may ask\b", r"\bthe (real |true )?question\b",
    r"\bwhat makes\b", r"\bwhy (do|does|should|would) ", r"\bthe idea\b",
    r"\bthe (general |basic |guiding )?principle\b", r"\bphilosoph",
    r"\bstrateg", r"\bheuristic", r"\bnaive", r"\bthe difficulty\b",
    r"\bthe (main )?problem is\b", r"\bit is tempting\b", r"\bone should\b",
    r"\blet us try\b", r"\bin order to understand\b", r"\bwe want to\b",
    r"\bthe right (definition|notion|question|concept)\b", r"\bmeaningful\b",
    r"\binteresting (structure|problem|question)", r"\bbeautiful\b",
    r"\bsoft(ness)?\b", r"\brigid", r"\bflexib", r"\bh-principle\b",
    r"\bcoarse", r"\bat (large |small )?scale", r"\basymptotic",
    r"\binvariant", r"\bgeneraliz", r"\babstract", r"\banalog",
    r"\bmetaphor", r"\bintuition", r"\bunderstand(ing)?\b",
    r"\bstructure\b", r"\bpoint of view\b", r"\bperspective\b",
    r"\bmodel(s|ling|ing)? of\b", r"\bapproach\b", r"\bmethod\b",
    # terse lecture-note register (Gromov opens sections with bare questions)
    r"\bwhat is\b", r"\bwhy\b", r"\bstandpoint", r"\bnaive\b",
    r"\bin a sense\b", r"\bone (can|may|must|should)\b", r"\bwe (look|think|say)\b",
    r"\bexample", r"\bidea\b", r"\bnotion\b", r"\bmotivat",
]
PATTERNS = [re.compile(p, re.I) for p in SIGNALS]
THRESHOLD = 2  # distinct signals on a page (pages average ~120 words)

DOC = re.compile(r"^<<<DOC id=(\d+) file=\"(.*?)\" pages=(\d+)>>>$", re.M)
PAGE = re.compile(r"^<<<PAGE (\d+)>>>$", re.M)


def pages_of(bundle_text):
    """Yield (doc_id, file, page_no, page_text)."""
    docs = list(DOC.finditer(bundle_text))
    for i, m in enumerate(docs):
        start = m.end()
        end = docs[i + 1].start() if i + 1 < len(docs) else len(bundle_text)
        body = bundle_text[start:end]
        marks = list(PAGE.finditer(body))
        for j, pm in enumerate(marks):
            p_start = pm.end()
            p_end = marks[j + 1].start() if j + 1 < len(marks) else len(body)
            yield m.group(1), m.group(2), int(pm.group(1)), body[p_start:p_end]


def main():
    if not os.path.isdir(UNITS):
        sys.exit(f"no {UNITS} -- run bundle.py first")
    bundles = sorted(f for f in os.listdir(UNITS)
                     if f.endswith(".md") and not f.endswith(".manifest.tsv"))
    if not bundles:
        sys.exit("no bundles found")

    for b in bundles:
        unit = b[:-3]
        text = open(os.path.join(UNITS, b), encoding="utf-8",
                    errors="replace").read()
        rows, npages = [], 0
        for doc_id, fname, pno, body in pages_of(text):
            npages += 1
            hits = [p.pattern for p in PATTERNS if p.search(body)]
            if len(hits) >= THRESHOLD:
                rows.append((doc_id, fname, pno, len(hits),
                             body.strip().replace("\t", " ")[:110]))
        out = os.path.join(UNITS, f"{unit}.candidates.tsv")
        with open(out, "w") as fh:
            fh.write("doc_id\tsource\tpage\tsignals\tsnippet\n")
            for r in rows:
                fh.write("\t".join(str(x) for x in r) + "\n")
        pct = (100 * len(rows) / npages) if npages else 0
        print(f"{unit:14s} {npages:5d} pages  {len(rows):5d} candidates "
              f"({pct:.0f}%)", flush=True)


if __name__ == "__main__":
    main()
