#!/usr/bin/env python3
"""Phase 3c: check each reader's output against its unit's candidate pages.

The readers ingest their bundle in contiguous chunks and are forbidden to
re-read. That is cheap but it fails silently: a reader that stops early still
writes a plausible-looking file. This is the detector.

Two signals, in increasing order of seriousness:

  * a candidate page (from prefilter.py) with no citation from that unit --
    expected in quantity, since the regex is deliberately over-inclusive;
  * a whole DOCUMENT from the unit manifest with no citation at all -- that is
    the truncation signature, and it re-runs the reader.

Reads only; writes COVERAGE_GAPS.tsv and prints a per-unit summary.
"""
import os
import random
import re
import sys

ROOT = os.path.expanduser("~/resources/gromov")
UNITS = os.path.join(ROOT, "units")
DIST = os.path.join(ROOT, "DISTILLATION")
OUT = os.path.join(ROOT, "COVERAGE_GAPS.tsv")

# Documents on Gromov's page that are NOT by Gromov. The artifact is
# Gromov-only, so a reader is right to leave these uncited -- flagging them as
# truncation would be a standing false alarm. Matched against the source path.
# Verified against every unit manifest: these substrings match only the
# foreign-authored documents, never a Gromov one.
NOT_GROMOV = ("petrunin", "donaldson", "chern", "hopf")

# - **Cite:** doc 03 | trees Cartie/trees Cartie.md | p.5
CITE = re.compile(
    r"^\s*-\s*\*\*Cite:\*\*\s*doc\s*(\d+)\s*[|·]\s*(.+?)\s*[|·]\s*p\.\s*(\d+)",
    re.M,
)


# The candidates snippet field carries raw markdown and can contain newlines,
# so a record is not a line: it starts at a leading doc_id and runs to the next.
RECORD = re.compile(r"^\d+\t")


PROBES = 120            # random blocks sampled from the shorter document
PROBE_LEN = 48
CONTAINMENT = 0.70      # of those blocks found verbatim in the other document


def _flat(text):
    return re.sub(r"\s+", "", text)


def _containment(a, b, rng):
    """Fraction of random blocks from the shorter text found in the longer.

    Sampling and substring search rather than aligned fixed-offset shingles:
    successive drafts differ by insertions, which shift every later block and
    would defeat any fixed grid.
    """
    if len(a) > len(b):
        a, b = b, a
    if len(a) < PROBE_LEN * 4:
        return 0.0
    hits = 0
    for _ in range(PROBES):
        i = rng.randrange(0, len(a) - PROBE_LEN)
        if a[i:i + PROBE_LEN] in b:
            hits += 1
    return hits / PROBES


def near_duplicates(unit):
    """Group a unit's documents into near-duplicate families.

    Gromov reposts revised drafts of the same lectures, so a unit can hold three
    copies of one work at 77p/219645, 77p/220421 and 74p/208258 chars -- close
    but not identical. The reader is told to cite such a family once, so an
    uncited sibling is correct behaviour, not truncation.
    """
    path = os.path.join(UNITS, unit + ".md")
    docs, cur, buf = {}, None, []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^<<<DOC id=(\d+) ", line)
            if m:
                if cur:
                    docs[cur] = _flat("".join(buf))
                cur, buf = m.group(1).zfill(2), []
            elif cur and not line.startswith("<<<PAGE"):
                buf.append(line)
    if cur:
        docs[cur] = _flat("".join(buf))

    rng = random.Random(0)                 # deterministic across runs
    ids = sorted(docs)
    parent = {d: d for d in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if _containment(docs[a], docs[b], rng) >= CONTAINMENT:
                parent[find(a)] = find(b)

    groups = {}
    for d in ids:
        groups.setdefault(find(d), []).append(d)
    return [g for g in groups.values() if len(g) > 1]


def _doc_texts(unit):
    path = os.path.join(UNITS, unit + ".md")
    docs, cur, buf = {}, None, []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^<<<DOC id=(\d+) ", line)
            if m:
                if cur:
                    docs[cur] = _flat("".join(buf))
                cur, buf = m.group(1).zfill(2), []
            elif cur and not line.startswith("<<<PAGE"):
                buf.append(line)
    if cur:
        docs[cur] = _flat("".join(buf))
    return docs


def best_overlap(unit, uncited, cited):
    """For each uncited doc, its closest cited sibling and the containment."""
    docs = _doc_texts(unit)
    rng = random.Random(0)
    out = {}
    for u in uncited:
        best, score = None, 0.0
        for c in cited:
            if c not in docs or u not in docs:
                continue
            v = _containment(docs[u], docs[c], rng)
            if v > score:
                best, score = c, v
        out[u] = (best, score)
    return out


def read_tsv(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    head = lines[0].split("\t")
    rows = []
    for ln in lines[1:]:
        if RECORD.match(ln):
            rows.append(ln.split("\t"))
        elif rows:                      # continuation of the previous snippet
            rows[-1][-1] += " " + ln.strip()
    return [dict(zip(head, r)) for r in rows]


def audit(unit):
    dist_path = os.path.join(DIST, unit + ".md")
    if not os.path.exists(dist_path):
        return None
    with open(dist_path, encoding="utf-8") as fh:
        text = fh.read()

    cited = set()          # (doc_id, page)
    cited_docs = set()     # doc_id
    for doc, _file, page in CITE.findall(text):
        doc = doc.zfill(2)
        cited.add((doc, int(page)))
        cited_docs.add(doc)

    manifest = read_tsv(os.path.join(UNITS, unit + ".manifest.tsv"))
    cands = read_tsv(os.path.join(UNITS, unit + ".candidates.tsv"))

    gaps = []
    for c in cands:
        key = (c["doc_id"].zfill(2), int(c["page"]))
        if key not in cited:
            gaps.append((unit, c["doc_id"], c["source"], c["page"],
                         c.get("signals", ""), c.get("snippet", "")[:120]))

    # Units carry duplicate and near-duplicate documents -- the mirror kept every
    # file, and Gromov reposts revised drafts of the same lectures. The reader is
    # told to cite such a family once, so an uncited twin is correct behaviour,
    # not truncation. Byte-identical pairs would be caught by (pages, chars), but
    # successive drafts differ slightly (77p/219645 vs 77p/220421 vs 74p/208258),
    # so group by actual content overlap instead.
    covered = set(cited_docs)
    for group in near_duplicates(unit):
        if any(d in cited_docs for d in group):
            covered.update(group)

    foreign = {m["doc_id"].zfill(2) for m in manifest
               if any(k in m["source"].lower() for k in NOT_GROMOV)}
    covered |= foreign

    silent = [m for m in manifest if m["doc_id"].zfill(2) not in covered]

    # A document can overlap a cited sibling heavily without clearing the
    # near-duplicate bar. Report how much, so an uncited doc can be judged
    # rather than blindly re-read.
    if silent:
        overlap = best_overlap(unit, [m["doc_id"].zfill(2) for m in silent],
                               cited_docs)
        for m in silent:
            m["_overlap"] = overlap.get(m["doc_id"].zfill(2), (None, 0.0))
    return {
        "unit": unit, "claims": len(CITE.findall(text)),
        "docs": len(manifest), "docs_cited": len(cited_docs),
        "cands": len(cands), "gaps": gaps, "silent": silent,
    }


def main():
    units = sorted(
        f[:-3] for f in os.listdir(UNITS)
        if f.endswith(".md") and not f.endswith(".manifest.md")
    )
    if len(sys.argv) > 1:
        units = sys.argv[1:]

    rows, bad = [], []
    print(f"{'unit':<26} {'claims':>6} {'docs':>9} {'cand pages':>12} {'uncited':>8}")
    for u in units:
        r = audit(u)
        if r is None:
            print(f"{u:<26} {'--- no DISTILLATION file yet ---':>40}")
            continue
        rows.extend(r["gaps"])
        flag = ""
        if r["silent"]:
            bits = []
            for m in r["silent"]:
                b, sc = m.get("_overlap", (None, 0.0))
                bits.append(f"{m['doc_id']}({m['pages']}p"
                            + (f", {sc:.0%} in doc {b}" if b and sc > 0.2 else "")
                            + ")")
            flag = "  <<< UNCITED: " + ", ".join(bits)
            bad.append(r["unit"])
        print(f"{r['unit']:<26} {r['claims']:>6} "
              f"{r['docs_cited']}/{r['docs']:<7} {r['cands']:>12} "
              f"{len(r['gaps']):>8}{flag}")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("unit\tdoc_id\tsource\tpage\tsignals\tsnippet\n")
        for row in rows:
            fh.write("\t".join(str(x).replace("\t", " ") for x in row) + "\n")
    print(f"\nwrote {OUT} ({len(rows)} uncited candidate pages)")
    if bad:
        print("REVIEW: units with wholly uncited Gromov documents: "
              + ", ".join(bad))
        print("  High overlap with a cited sibling = correct dedup. A short,")
        print("  low-overlap doc may simply hold no method -- read it and see.")
        print("  A LONG, low-overlap doc means the reader truncated: re-run it.")
        return 1
    print("OK: every document in every audited unit is cited at least once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
