#!/usr/bin/env python3
"""Phase 3e gate: check every quote in the artifact draft against the corpus.

The synthesis ran in a subagent, so nothing it produced is relayed unchecked.
This resolves each quote in ARTIFACT_DRAFT.md back to the claim id it cites and
then, independently, back to the bundle page that claim cites -- so a quote that
was smoothed, merged from two claims, or attached to the wrong citation fails
here rather than in the finished artifact.

Also reports which units and documents the surviving moves actually rest on: a
draft whose every move traces to one essay is a different object from one whose
moves recur across the geometry lectures and the biology essays.

Usage: verify_artifact.py [path]     (default: ARTIFACT_DRAFT.md)
Exit 1 on any unresolvable quote or citation.
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
DRAFT = os.path.join(ROOT, "ARTIFACT_DRAFT.md")

# > "quote"   /  — file, p.12  `unit-007`
CLAIM_ID = re.compile(r"`([a-z-]+-\d{3})`")
BLOCKQUOTE = re.compile(r"^\s*>\s?(.*)$")


def load_claims():
    """claim id -> the extracted entry it names."""
    out = {}
    for f in sorted(os.listdir(DIST)):
        if not f.endswith(".md") or f in ("adversarial.md",):
            continue
        unit = f[:-3]
        body = open(os.path.join(DIST, f), encoding="utf-8").read()
        for i, e in enumerate(B.entries(body), 1):
            e["unit"] = unit
            out[f"{unit}-{i:03d}"] = e
    return out


ATTRIB = re.compile(r"^\s*[—–-]\s")


def _quote_only(buf):
    """Drop the attribution line from inside a blockquote.

    The draft writes the cite as another `>` line under the quote, so joining
    the whole block would compare the source text plus "— file, p.12" against
    the stored quote and fail every time.
    """
    keep = []
    for ln in buf:
        if ATTRIB.match(ln):
            break
        keep.append(ln)
    return " ".join(keep)


def _clean(q):
    """Drop the quotation marks the draft wraps around a quote.

    The stored claim holds the bare text, so leaving the delimiters on makes a
    correctly-copied quote fail to match its own claim.
    """
    q = q.strip()
    while q and q[0] in "\"'\u201c\u201d\u2018\u2019":
        q = q[1:]
    while q and q[-1] in "\"'\u201c\u201d\u2018\u2019":
        q = q[:-1]
    return q.strip()


def quote_blocks(text):
    """(quote text, claim ids on or just after it) for every blockquote."""
    lines = text.split("\n")
    out, buf, start = [], [], None
    for i, ln in enumerate(lines):
        m = BLOCKQUOTE.match(ln)
        if m:
            if start is None:
                start = i
            buf.append(m.group(1))
            continue
        if buf:
            window = "\n".join(lines[start:i + 3])
            out.append((_clean(_quote_only(buf)), CLAIM_ID.findall(window)))
            buf, start = [], None
    if buf:
        out.append((_clean(_quote_only(buf)),
                    CLAIM_ID.findall("\n".join(lines[start:]))))
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DRAFT
    if not os.path.exists(path):
        sys.exit(f"no draft at {path}")
    text = open(path, encoding="utf-8").read()
    claims = load_claims()

    ok = fail = 0
    units = collections.Counter()
    docs = collections.Counter()
    unresolved = []

    for quote, ids in quote_blocks(text):
        if len(quote) < 40:
            continue                        # a pull-quote fragment, not evidence
        ids = [i for i in ids if i in claims] or [i for i in ids]
        if not ids:
            fail += 1
            unresolved.append(("NO CLAIM ID", quote[:90]))
            continue
        cid = ids[0]
        if cid not in claims:
            fail += 1
            unresolved.append((f"{cid}: no such claim", quote[:90]))
            continue

        e = claims[cid]
        # 1. does the draft's quote match the claim's quote?
        src = V.canon(e["quote"])
        got = V.canon(quote)
        matched = all(c in src for c, _ in V._clause_list(quote)
                      if len(c) >= V.MIN_CLAUSE)
        if not matched and got not in src:
            fail += 1
            unresolved.append((f"{cid}: quote not in that claim", quote[:90]))
            continue

        # 2. does it still resolve to the bundle page the claim cites?
        pages = V.index_bundle(os.path.join(V.UNITS, e["unit"] + ".md"))
        dtext, index = V.doc_text(pages, e["doc"])
        hit, _bad = V.locate(dtext, quote, index, e["page"])
        if hit is None:
            fail += 1
            unresolved.append((f"{cid}: not on p.{e['page']} of the bundle",
                               quote[:90]))
            continue
        true_pg = V.page_of(index, hit[0])
        if true_pg != e["page"]:
            fail += 1
            unresolved.append((f"{cid}: draft cites p.{e['page']}, text is on "
                               f"p.{true_pg}", quote[:90]))
            continue

        ok += 1
        units[e["unit"]] += 1
        docs[f"{e['unit']}/doc {e['doc']}"] += 1

    moves = len(re.findall(r"^## ", text, re.M))
    print(f"{path}")
    print(f"  {moves} top-level sections, {ok + fail} quotes checked")
    print(f"  {ok} resolve to their claim AND its bundle page, {fail} do not")
    if unresolved:
        print("\nUNRESOLVED:")
        for why, q in unresolved:
            print(f"  {why}\n    {q}")
    print("\nEvidence spread — quotes per unit:")
    for u, n in units.most_common():
        print(f"  {u:<26} {n}")
    print(f"  ({len(docs)} distinct source documents)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
