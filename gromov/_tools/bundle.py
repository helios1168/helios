#!/usr/bin/env python3
"""Phase 3a: concatenate marker's per-document markdown into per-unit bundles.

Each reader gets exactly ONE file and reads it in ONE Read call -- that is what
keeps the token multiplier near 1.15x instead of ~2.5x. Citations must survive
concatenation, so every document and page boundary is marked explicitly:

    <<<DOC id=07 file="..." pages=112>>>
    <<<PAGE 1>>>

Writes units/<unit>.md and units/<unit>.manifest.tsv, and refuses to emit a
bundle over the token cap (that would force the chunked reading this design
exists to avoid).
"""
import os
import re
import sys

ROOT = os.path.expanduser("~/resources/gromov")
MD = os.path.join(ROOT, "markdown")
UNITS = os.path.join(ROOT, "units")

TOKEN_CAP = 400_000
CHARS_PER_TOKEN = 3.5

# First match wins. Anything unmatched is reported, never silently dropped.
RULES = [
    # Ordered; first match wins. Balanced by PAGES (from the Tier 1 index), not
    # by theme -- one 369-page document has to stand alone, and the lecture-course
    # directories carry more than half the corpus.
    ("curvature-scalar", ["Scalar-Jul-8-2021"]),                              # 369p
    ("curvature-hopf", ["Hopf", "Sign and geometric meaning",
                        "Chern "]),                                           # 351p
    ("isoperimetry", ["Isoperimery-Dec-14-2023", "Isoperimery 2023",
                      "Isoperimery-Dec 6 2023", "Convex Sets - Kahler",
                      "2024 lectures 1-4 life and cognition"]),               # 302p
    # NB: no "Formulas" pattern here -- Formulas\&Di_erential Operators..pdf is a
    # curvature document (99p) and falls through to curvature-misc below.
    ("probability-paris", ["probability Lecture in Paris", "lectures IprobHES",
                           "alternative probabilities", "CIMS lecture2+1:2",
                           "Probablity&topology"]),                           # 356p
    ("probability-nash-entropy", ["nash-Dec-2015", "NASH--2023-OCT",
                                  "manifolds poincare-june25",
                                  "probability:topology apr2022", "/CIMS/",
                                  "seim-zas", "CIMS Revision", "Cubes to Cubes",
                                  "Cubes Mean Values", "Pascal_", "CIMS lecure2",
                                  "CIMSBorsukUlam1", "entropy Nov 2022",
                                  "search for a structre-entropy",
                                  "Classical and Quantum Entropy",
                                  "entropy1"]),                           # 313p
    ("cognition-a", ["ergobrain", "ergo-new", "ergodictionary"]),             # 314p
    ("cognition-b", ["Quotations and Ideas", "memorandum ergo",
                     "mathematicals models", "Learning Language",
                     "Math Currents",
                     # marker mangled this stem (source filename lacks the dot in
                     # '"understanding"pdf'; its directory ends in '.'), so it lands here:
                     "mathematic of mental processes/mathematic of mental processes"]),                                       # 265p
    ("bio-dimensions", ["biology course", "proteins-crystals",
                        "Mendelian Dynamics", "Protein Spaces",
                        "manifolds-Poincare-Oct1-10", "large dimensions"]),    # 215p
    ("survey", ["Spaces and Questions", "Life Spaces", "trees Cartie"]),      #  56p
    # Directory catch-alls LAST: everything specific above has already matched.
    ("curvature-misc", ["lectures on Curvature 2025/",
                        "5  great theorems. lectures 2024 /"]),               # 259p
]

# Excluded from every bundle: a political statement, not method material.
EXCLUDE = ["Hamas and 1078 Mathematicians"]

# marker --paginate_output separator, e.g. "{5}------------------------------"
PAGE_SEP = re.compile(r"^\{(\d+)\}-{6,}\s*$", re.M)


def unit_for(rel):
    for name in EXCLUDE:
        if name in rel:
            return None
    for unit, pats in RULES:
        if any(p in rel for p in pats):
            return unit
    return "UNASSIGNED"


FLAT_SEP = " ~ "  # stage.py encodes raw/ subdirectories with this


def find_markdown():
    """marker writes markdown/<stem>/<stem>.md. Map ORIGINAL rel path -> md path.

    stage.py flattens raw/ for marker (which does not recurse), encoding "/" as
    FLAT_SEP; decode it here so the unit RULES still match real corpus paths.
    """
    out = {}
    for dirpath, _, files in os.walk(MD):
        for f in files:
            if f.endswith(".md"):
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, MD).replace(FLAT_SEP, "/")
                out[rel] = full
    return out


def normalize_pages(text):
    """Rewrite marker page separators into explicit <<<PAGE n>>> markers."""
    if not PAGE_SEP.search(text):
        return "<<<PAGE 1>>>\n" + text, 1
    parts = PAGE_SEP.split(text)
    # split yields [pre, num, body, num, body, ...]
    out = []
    pages = 0
    if parts[0].strip():
        out.append("<<<PAGE 1>>>\n" + parts[0])
        pages = 1
    for i in range(1, len(parts) - 1, 2):
        num = int(parts[i]) + 1  # marker is 0-based; cite real PDF page numbers
        out.append(f"<<<PAGE {num}>>>\n{parts[i + 1]}")
        pages = max(pages, num)
    return "\n".join(out), pages


def main():
    only = set(sys.argv[1:])
    os.makedirs(UNITS, exist_ok=True)
    found = find_markdown()
    if not found:
        sys.exit(f"no markdown under {MD} -- run Phase 2 first")

    buckets, unassigned = {}, []
    for rel, path in sorted(found.items()):
        unit = unit_for(rel)
        if unit is None:
            continue
        if unit == "UNASSIGNED":
            unassigned.append(rel)
            continue
        if only and unit not in only:
            continue
        buckets.setdefault(unit, []).append((rel, path))

    if unassigned:
        print("UNASSIGNED (add a rule or they are silently lost):", flush=True)
        for r in unassigned:
            print(f"   {r}", flush=True)

    over = []
    for unit, items in sorted(buckets.items()):
        chunks, rows, total = [], [], 0
        for i, (rel, path) in enumerate(items, 1):
            body = open(path, encoding="utf-8", errors="replace").read()
            body, pages = normalize_pages(body)
            chunks.append(
                f'<<<DOC id={i:02d} file="{rel}" pages={pages}>>>\n{body}\n')
            rows.append((f"{i:02d}", rel, pages, len(body),
                         int(len(body) / CHARS_PER_TOKEN)))
            total += len(body)

        bundle = os.path.join(UNITS, f"{unit}.md")
        with open(bundle, "w", encoding="utf-8") as fh:
            fh.write(f"# Gromov corpus -- unit: {unit}\n")
            fh.write(f"# {len(items)} documents. Read this file once, completely.\n")
            fh.write("# Cite as: doc <id> | <file> | p.<page>\n\n")
            fh.write("\n".join(chunks))

        with open(os.path.join(UNITS, f"{unit}.manifest.tsv"), "w") as fh:
            fh.write("doc_id\tsource\tpages\tchars\test_tokens\n")
            for r in rows:
                fh.write("\t".join(str(x) for x in r) + "\n")

        est = int(total / CHARS_PER_TOKEN)
        flag = ""
        if est > TOKEN_CAP:
            flag = f"  <-- OVER CAP ({TOKEN_CAP:,}), split this unit"
            over.append(unit)
        print(f"{unit:14s} {len(items):3d} docs  {total:9,d} chars  "
              f"~{est:8,d} tokens{flag}", flush=True)

    if over:
        print(f"\nFAIL: over cap: {', '.join(over)}", flush=True)
        sys.exit(1)
    print(f"\nbundles in {UNITS}", flush=True)


if __name__ == "__main__":
    main()
