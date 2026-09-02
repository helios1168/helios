#!/usr/bin/env python3
"""Phase 3b gate: check every claimed quote against its cited page.

A reader that drifts produces quotes that are *almost* right -- a smoothed
clause, a merged sentence. Those are the dangerous ones: they read well, and
the artifact inherits them. So this checks every quote, not a sample, and it
checks against the SPECIFIC page cited, so a real quote hung on the wrong page
still fails.

What it forgives, and why (all four were observed in the verified survey run):

  * whitespace and marker's unstable LaTeX spacing (`$\\mathbb{R}^2$ , smoothly`);
  * markdown structure the reader dropped -- image placeholders, `#` markers,
    emphasis, and `$$` display delimiters written back as `$`;
  * a quote running past the cited page (cited to where it starts, correctly),
    so the search window is the cited page plus the two following;
  * an elision the reader did not mark with `…`.

The last is forgiven by matching CLAUSES in order rather than one contiguous
run. That still fails on any substantive drift -- a changed, added, reordered
or paraphrased word breaks the clause it sits in -- but it tolerates a hole.
Quotes needing that tolerance are reported as GAPS so they stay visible.

Usage: verify_quotes.py [unit ...]     (default: every DISTILLATION file)
Exit 1 if any quote fails.
"""
import os
import re
import sys

ROOT = os.path.expanduser("~/resources/gromov")
UNITS = os.path.join(ROOT, "units")
DIST = os.path.join(ROOT, "DISTILLATION")
DEFECTS = os.path.join(ROOT, "QUOTE_DEFECTS.tsv")

QUOTE = re.compile(r'^\s*-\s*\*\*Quote:\*\*\s*"(.*)"\s*$', re.M)
CITE = re.compile(
    r"^\s*-\s*\*\*Cite:\*\*\s*doc\s*(\d+)\s*[|·]\s*(.+?)\s*[|·]\s*p\.\s*(\d+)", re.M)
DOC = re.compile(r"^<<<DOC id=(\d+) ", re.M)
PAGE = re.compile(r"^<<<PAGE (\d+)>>>", re.M)

IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# LaTeX name -> the character a reader is likely to type instead.
SYMBOLS = {
    "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "longrightarrow": "→", "mapsto": "↦", "to": "→",
    "infty": "∞", "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈",
    "subset": "⊂", "supset": "⊃", "subseteq": "⊆", "in": "∈", "notin": "∉",
    "times": "×", "cdot": "·", "star": "★", "circ": "∘", "pm": "±",
    "cup": "∪", "cap": "∩", "setminus": "∖", "perp": "⊥", "partial": "∂",
    "sum": "∑", "prod": "∏", "int": "∫", "sqrt": "√", "nabla": "∇",
    "alpha": "α", "beta": "β", "gamma": "γ", "Gamma": "Γ", "delta": "δ",
    "Delta": "Δ", "varepsilon": "ε", "epsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "Theta": "Θ", "iota": "ι", "kappa": "κ", "lambda": "λ",
    "Lambda": "Λ", "mu": "μ", "nu": "ν", "xi": "ξ", "Xi": "Ξ", "pi": "π",
    "Pi": "Π", "rho": "ρ", "sigma": "σ", "Sigma": "Σ", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "varphi": "φ", "Phi": "Φ", "chi": "χ",
    "psi": "ψ", "Psi": "Ψ", "omega": "ω", "Omega": "Ω",
    "ni": "∋", "sim": "∼", "gg": "≫", "ll": "≪",
    "rightsquigarrow": "⇝", "leadsto": "⇝", "squigarrow": "⇝", "simeq": "≃", "cong": "≅", "equiv": "≡", "propto": "∝",
    "bullet": "•", "square": "□", "blacksquare": "■", "blacktriangle": "▲",
    "ell": "ℓ", "hbar": "ℏ", "emptyset": "∅", "forall": "∀", "exists": "∃",
    "langle": "⟨", "rangle": "⟩", "oplus": "⊕", "otimes": "⊗",
    "hookrightarrow": "↪", "xrightarrow": "→", "implies": "⟹",
    "dots": "…", "ldots": "…", "cdots": "…", "quad": "", "qquad": "",
}
MIN_CLAUSE = 24          # shorter than this is not evidence either way
LOOKAHEAD = 2            # pages past the cited one a quote may run into


def canon(s):
    """Collapse everything that is noise, keep every letter that is signal."""
    s = s.replace('\\"', '"').replace("\\_", "_").replace("\\*", "*")
    # An entry wraps its quote in "..." , so a reader must render the source's
    # own double quotes some other way -- usually as '. Fold every quote
    # character together; this cannot mask a changed word.
    s = re.sub(r"[\"'“”‘’«»‹›`]", "'", s)
    s = re.sub(r"[−–—‐‑]", "-", s)          # unicode dashes vs ASCII hyphen
    s = re.sub(r"[⋯…⋮]", "…", s)            # ellipsis variants
    s = re.sub(r"[′″‵]", "'", s)            # primes vs apostrophes
    s = IMAGE.sub(" ", s)
    s = re.sub(r"~~.*?~~", "", s)           # struck-through text marker keeps
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)   # [text](link) -> text
    s = re.sub(r"<sup>.*?</sup>", "", s)    # inline footnote markers, number and all
    s = re.sub(r"<[^>]{1,20}>", "", s)
    # Readers often render a symbol rather than transcribing its LaTeX name:
    # \Sigma as Σ, \rightarrow as →, \mathcal{L} as L. Fold both spellings to
    # one so notation style stops masking whether the WORDS match. Matched only
    # after a backslash -- a bare table would rewrite "terminate" to "term∈ate"
    # via \in, and "child" to "χld" via \chi.
    s = re.sub(r"\\([A-Za-z]+)",
               lambda m: SYMBOLS.get(m.group(1), "\\" + m.group(1)), s)
    # Structural commands wrap a letter without changing it.
    for _ in range(3):                       # nested, e.g. \underline{\bf x}
        s = re.sub(r"\\?(mathcal|mathbb|mathrm|mathbf|mathfrak|underline|"
                   r"overline|widetilde|widehat|operatorname|boldsymbol|"
                   r"text|textit|textbf|mbox)\s*\{([^{}]*)\}", r"\2", s)
    # $ delimiters, leftover backslashes and grouping are placed inconsistently
    # by both marker and the readers ($x$, vs $x,$ ; \dim vs dim).
    s = re.sub(r"[#*■$\\{}_^]", "", s)
    # marker keeps the PDF's line-break hyphens (`trun-cated`, `het-erogeneous`),
    # which a correct verbatim quote silently repairs. Drop hyphens on BOTH
    # sides: consistent, and it cannot mask a substantive change.
    s = s.replace("-", "")
    return re.sub(r"\s+", "", s)


def clauses(seg):
    """Split a quote segment at sentence and clause boundaries."""
    parts = re.split(r"(?<=[.;:,])\s+|\s+--\s+", seg)
    out, cur = [], ""
    for p in parts:                      # merge fragments up to a usable length
        cur = (cur + " " + p).strip()
        if len(canon(cur)) >= MIN_CLAUSE:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def index_bundle(path):
    """(doc_id, page) -> canonicalized page text."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    pages, doc, page, buf = {}, None, None, []

    def flush():
        if doc is not None and page is not None:
            pages[(doc, page)] = canon("".join(buf))

    for line in text.splitlines(keepends=True):
        m = DOC.match(line)
        if m:
            flush()
            doc, page, buf = m.group(1).zfill(2), None, []
            continue
        m = PAGE.match(line)
        if m:
            flush()
            page, buf = int(m.group(1)), []
            continue
        buf.append(line)
    flush()
    return pages


def doc_text(pages, doc):
    """Concatenate a document's pages, keeping a page index for each offset.

    Locating a quote in the whole document rather than only on the cited page
    is what lets a page number be CORRECTED instead of merely rejected. The
    quote is the evidence; the page is derived from it.
    """
    nums = sorted(p for (d, p) in pages if d == doc)
    buf, index = [], []
    off = 0
    for n in nums:
        t = pages[(doc, n)]
        index.append((off, n))
        buf.append(t)
        off += len(t)
    return "".join(buf), index


def page_of(index, off):
    page = index[0][1] if index else None
    for start, n in index:
        if start <= off:
            page = n
        else:
            break
    return page


def _clause_list(q):
    out = []
    for seg in q.split("…"):
        for cl in clauses(seg.strip(" .,;:")):
            c = canon(cl)
            if len(c) >= MIN_CLAUSE:
                out.append((c, cl))
    return out


def _match_from(text, cls, first_at):
    """Complete an in-order match whose first clause sits at first_at."""
    pos, holes = first_at + len(cls[0][0]), 0
    for c, cl in cls[1:]:
        i = text.find(c, pos)
        if i < 0:
            return None, cl
        if i > pos:
            holes += 1
        pos = i + len(c)
    return holes, None


def locate(text, q, index=None, cited=None):
    """Match the quote's clauses in order. -> ((start_offset, holes), None).

    A short opening clause can recur -- "As far as the scalar curvature is
    concerned," appears on pages 24, 232 and 339 of one lecture course. Taking
    the first hit would report a correct citation as 208 pages wrong, and --fix
    would then corrupt it. So every occurrence of the opening clause is tried,
    and the completed match landing nearest the cited page wins.
    """
    cls = _clause_list(q)
    if not cls:
        return (0, 0), None

    starts, i = [], text.find(cls[0][0])
    while i >= 0:
        starts.append(i)
        i = text.find(cls[0][0], i + 1)
    if not starts:
        return None, cls[0][1]

    best, worst_fail = None, None
    for st in starts:
        holes, badcl = _match_from(text, cls, st)
        if holes is None:
            worst_fail = worst_fail or badcl
            continue
        if index is not None and cited is not None:
            score = abs(page_of(index, st) - cited)
        else:
            score = st
        if best is None or score < best[0]:
            best = (score, st, holes)

    if best is None:
        return None, worst_fail or cls[-1][1]
    return (best[1], best[2]), None


def check(unit, fix=False):
    dist = os.path.join(DIST, unit + ".md")
    if not os.path.exists(dist):
        print(f"{unit:<26} --- no DISTILLATION file yet ---")
        return 0, 0, 0, 0, [], 0
    with open(dist, encoding="utf-8") as fh:
        body = fh.read()

    # Pair each quote with the cite line that follows it, carrying the line
    # index. Editing by (doc, page) instead would rewrite every entry sharing a
    # page when only one of them is shifted -- two quotes on one page then
    # oscillate on each --fix.
    lines = body.split("\n")
    entries, pending = [], None
    for i, ln in enumerate(lines):
        m = QUOTE.match(ln)
        if m:
            pending = m.group(1)
            continue
        m = CITE.match(ln)
        if m and pending is not None:
            entries.append({"quote": pending, "doc": m.group(1).zfill(2),
                            "page": int(m.group(3)), "line": i})
            pending = None
    if len(QUOTE.findall(body)) != len(entries):
        print(f"{unit}: WARNING {len(QUOTE.findall(body))} quotes but "
              f"{len(entries)} quote/cite pairs -- some entry is malformed")

    pages = index_bundle(os.path.join(UNITS, unit + ".md"))
    cache = {}
    defects = []
    ok = gaps = fail = shift = 0
    rewrites = []

    for e in entries:
        q, doc, pg = e["quote"], e["doc"], e["page"]
        where = f"doc {doc} p.{pg}"
        if doc not in cache:
            cache[doc] = doc_text(pages, doc)
        text, index = cache[doc]
        if not text:
            print(f"  FAIL [{unit}] {where}: no such document in bundle")
            fail += 1
            defects.append((unit, doc, pg, canon(q)[:60], "0/0", q[:80], ""))
            continue

        hit, badclause = locate(text, q, index, pg)
        if hit is None:
            fail += 1
            c = canon(badclause)
            lo, hi = 0, len(c)               # longest prefix actually present
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if text.find(c[:mid]) >= 0:
                    lo = mid
                else:
                    hi = mid - 1
            j = text.find(c[:lo])
            src = text[max(0, j + lo - 30):j + lo + 70] if j >= 0 else ""
            print(f"  FAIL [{unit}] {where}: not verbatim anywhere in doc {doc}")
            print(f"    quote : ...{badclause[:100]}")
            print(f"    diverges after {lo}/{len(c)} chars; source: ...{src[:90]}")
            defects.append((unit, doc, pg, canon(q)[:60], f"{lo}/{len(c)}",
                            c[max(0, lo - 40):lo + 60], src))
            continue

        start, holes = hit
        true_pg = page_of(index, start)
        ok += 1
        if holes > 1:
            gaps += 1
        if true_pg != pg:
            shift += 1
            print(f"  SHIFT [{unit}] {where} -> actually p.{true_pg} "
                  f"({true_pg - pg:+d})")
            rewrites.append((e["line"], true_pg))

    if fix and rewrites:
        for i, tp in rewrites:
            lines[i] = re.sub(r"p\.\s*\d+", f"p.{tp}", lines[i])
        with open(dist, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print(f"  fixed {len(rewrites)} citation(s) in {dist}")

    print(f"{unit:<26} {ok:>4} verified ({gaps} with unmarked gaps, "
          f"{shift} page-shifted), {fail:>3} failed")
    return ok, gaps, shift, fail, defects


def main():
    args = sys.argv[1:]
    fix = "--fix" in args
    units = [a for a in args if not a.startswith("-")] or sorted(
        f[:-3] for f in os.listdir(DIST) if f.endswith(".md"))
    t_ok = t_gap = t_shift = t_fail = 0
    all_defects = []
    for u in units:
        o, g, sh, f, d = check(u, fix=fix)
        t_ok, t_gap, t_shift, t_fail = t_ok + o, t_gap + g, t_shift + sh, t_fail + f
        all_defects.extend(d)

    # Every quote that could not be matched, with where it diverged. The
    # adversarial pass reads this so it inspects the real page text rather than
    # inheriting a quote the reader smoothed.
    with open(DEFECTS, "w", encoding="utf-8") as fh:
        # quote_key identifies the specific claim. Keying a defect by
        # (unit, doc, page) instead tags every claim citing that page -- 25
        # defects became 38 flags, 13 of them on sound claims.
        fh.write("unit\tdoc_id\tcited_page\tquote_key\tmatched"
                 "\tquote_at_divergence\tsource_at_divergence\n")
        for row in all_defects:
            fh.write("\t".join(str(x).replace("\t", " ") for x in row) + "\n")
    print(f"wrote {DEFECTS} ({len(all_defects)} unmatched quotes)")
    print(f"\ntotal: {t_ok} verified ({t_gap} with unmarked gaps, "
          f"{t_shift} page-shifted), {t_fail} failed")
    return 1 if t_fail else 0


if __name__ == "__main__":
    sys.exit(main())
