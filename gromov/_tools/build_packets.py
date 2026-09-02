#!/usr/bin/env python3
"""Phase 3d prep: build one adjudication packet per unit.

The adversarial pass asks a narrow question -- does the cited passage actually
support the Move claimed from it, or is the Move a plausible gloss? -- and that
question needs the claim plus the text around it, not the whole 344 K-token
bundle. Extracting the context mechanically costs no tokens and keeps the
adjudicator's context clean of the extraction reasoning, which is the whole
point of running this pass fresh.

Each packet carries, per claim: the move name, the quote, the claimed move, the
reader's confidence, the verbatim-check result from QUOTE_DEFECTS.tsv, and a
window of real bundle text around where the quote actually sits.

Writes DISTILLATION/_packets/<unit>.md and prints a per-unit claim count.
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_quotes as V

ROOT = os.path.expanduser("~/resources/gromov")
DIST = os.path.join(ROOT, "DISTILLATION")
OUT = os.path.join(DIST, "_packets")
DEFECTS = os.path.join(ROOT, "QUOTE_DEFECTS.tsv")

BUDGET = 7000            # chars of bundle text per claim, whole pages only

NAME = re.compile(r"^##\s+(?!Absent|Coverage)(.+?)\s*$", re.M)
MOVE = re.compile(r"^\s*-\s*\*\*Move:\*\*\s*(.+?)\s*$", re.M)
CONF = re.compile(r"^\s*-\s*\*\*Confidence:\*\*\s*(\w+)", re.M)


def load_defects():
    """quote_key -> (severity, note).

    Refuses an ungraded file rather than emitting "?" placeholders: running
    verify_quotes.py rewrites this file WITHOUT the grading columns, so a
    packet built straight afterwards silently loses every severity -- which is
    exactly how 10 INVENTED claims once reached the adjudicators unlabelled.
    """
    out = {}
    if not os.path.exists(DEFECTS):
        return out
    with open(DEFECTS, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if rows and "severity" not in rows[0]:
        sys.exit(f"{DEFECTS} has no 'severity' column.\n"
                 f"Run:  python3 _tools/classify_defects.py   first.")
    if rows and "quote_key" not in rows[0]:
        sys.exit(f"{DEFECTS} predates quote-keyed defects.\n"
                 f"Run:  python3 _tools/verify_quotes.py && "
                 f"python3 _tools/classify_defects.py")
    for r in rows:
        out[(r["unit"], r["quote_key"])] = (r["severity"], r["note"])
    return out


def entries(body):
    """Walk the file once, grouping each ## heading with its four fields."""
    out, cur = [], None
    for ln in body.split("\n"):
        m = NAME.match(ln)
        if m:
            if cur and cur.get("quote"):
                out.append(cur)
            cur = {"name": m.group(1)}
            continue
        if cur is None:
            continue
        m = V.QUOTE.match(ln)
        if m:
            cur["quote"] = m.group(1)
            continue
        m = V.CITE.match(ln)
        if m:
            cur["doc"] = m.group(1).zfill(2)
            cur["file"] = m.group(2)
            cur["page"] = int(m.group(3))
            continue
        m = MOVE.match(ln)
        if m:
            cur["move"] = m.group(1)
            continue
        m = CONF.match(ln)
        if m:
            cur["conf"] = m.group(1)
    if cur and cur.get("quote"):
        out.append(cur)
    return out


def raw_pages(path):
    """(doc, page) -> RAW page text, unlike verify_quotes' canonical form."""
    pages, doc, page, buf = {}, None, None, []

    def flush():
        if doc is not None and page is not None:
            pages[(doc, page)] = "".join(buf)

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = V.DOC.match(line)
            if m:
                flush()
                doc, page, buf = m.group(1).zfill(2), None, []
                continue
            m = V.PAGE.match(line)
            if m:
                flush()
                page, buf = int(m.group(1)), []
                continue
            buf.append(line)
    flush()
    return pages


def page_span(index, start, length):
    """The first and last page a match of this length covers."""
    return V.page_of(index, start), V.page_of(index, start + max(0, length - 1))


def context(raws, doc, first, last, budget=BUDGET):
    """Raw text of the pages the quote spans, plus a margin page each side.

    Windowing by character offset does not work: the canonical text used to
    locate a quote has had whitespace and markup stripped, so its offsets do
    not map onto the raw page text. Selecting whole PAGES by the span the match
    covers is exact, and guarantees the packet contains the quote it asks about
    -- which windowing failed to do for 450 of 947 claims.
    """
    core = [raws[(doc, p)] for p in range(first, last + 1) if (doc, p) in raws]
    body = "".join(core)
    for k in (1, 2):                     # widen only if there is room
        extra = []
        if (doc, first - k) in raws:
            extra.append(raws[(doc, first - k)])
        tail = raws.get((doc, last + k), "")
        if len("".join(extra)) + len(body) + len(tail) > budget:
            break
        body = "".join(extra) + body + tail
        first, last = first - k, last + k
    # marker occasionally emits NUL and other control bytes from a bad scan.
    # Two of them are enough to make grep treat a packet as binary.
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def build(unit, defects):
    dist = os.path.join(DIST, unit + ".md")
    bundle = os.path.join(V.UNITS, unit + ".md")
    if not (os.path.exists(dist) and os.path.exists(bundle)):
        return 0
    body = open(dist, encoding="utf-8").read()
    ents = entries(body)
    raws = raw_pages(bundle)
    canon_pages = V.index_bundle(bundle)
    cache = {}

    lines = [
        f"# Adjudication packet — unit: {unit}",
        f"# {len(ents)} claims. Source text is quoted from the OCR'd bundle.",
        "",
    ]
    for i, e in enumerate(ents, 1):
        doc, page = e.get("doc", "??"), e.get("page", 0)
        if doc not in cache:
            cache[doc] = V.doc_text(canon_pages, doc)
        text, index = cache[doc]
        hit, _bad = V.locate(text, e["quote"], index, page)
        if hit is not None:
            qlen = sum(len(c) for c, _ in V._clause_list(e["quote"]))
            first, last = page_span(index, hit[0], qlen)
        else:
            first = last = page          # unmatched: fall back to the cite
        ctx = context(raws, doc, first, last)

        sev, note = defects.get((unit, V.canon(e["quote"])[:60]),
                                ("VERIFIED", ""))
        check = "VERIFIED verbatim" if sev == "VERIFIED" else f"{sev} — {note}"

        lines += [
            f"### CLAIM {unit}-{i:03d}",
            f"- **Move name:** {e['name']}",
            f"- **Quote:** \"{e['quote']}\"",
            f"- **Move claimed:** {e.get('move','(missing)')}",
            f"- **Reader confidence:** {e.get('conf','?')}",
            f"- **Verbatim check:** {check}",
            f"- **Cite:** doc {doc} | {e.get('file','?')} | p.{page}",
            "- **Source context:**",
            "```",
            ctx,
            "```",
            "",
        ]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, unit + ".md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return len(ents)


def main():
    defects = load_defects()
    units = sys.argv[1:] or sorted(
        f[:-3] for f in os.listdir(DIST) if f.endswith(".md"))
    total = 0
    for u in units:
        n = build(u, defects)
        if n:
            size = os.path.getsize(os.path.join(OUT, u + ".md"))
            print(f"{u:<26} {n:>4} claims  {size/1000:>7.0f} KB"
                  f"  ~{size/3500:>6.0f}k tokens")
            total += n
    print(f"\n{total} claims packaged into {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
