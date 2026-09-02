#!/usr/bin/env python3
"""Tier 1: fast prose index via pypdf. NOT math-faithful -- for grep/locating only.

Run: uv run --with pypdf python extract_text.py
"""
import os
import subprocess
import sys

from pypdf import PdfReader

ROOT = os.path.expanduser("~/resources/gromov")
RAW = os.path.join(ROOT, "raw")
OUT = os.path.join(ROOT, "text")

WARNING = (
    "=== TIER 1 EXTRACTION -- NOT MATH-FAITHFUL ===\n"
    "Fractions are split across lines, radicals are orphaned, and glyph names\n"
    "(/uni227B, /divides.alt0) leak as literal text: these PDFs embed CM/SF font\n"
    "subsets with no /ToUnicode map. Use this file to LOCATE passages only.\n"
    "Quote from markdown/ (marker output), never from here.\n"
    "===============================================\n\n"
)


def distinct_files():
    """One path per sha256, preferring the shortest path."""
    man = os.path.join(ROOT, "manifest.tsv")
    best = {}
    with open(man) as fh:
        next(fh)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            _, path, nbytes, digest = parts[0], parts[1], parts[2], parts[3]
            if not digest:
                continue
            cur = best.get(digest)
            if cur is None or len(path) < len(cur):
                best[digest] = path
    return sorted(best.values())


def is_pdf(path):
    with open(path, "rb") as fh:
        return fh.read(5) == b"%PDF-"


def main():
    paths = distinct_files()
    print(f"{len(paths)} distinct files", flush=True)
    no_text, skipped, stats = [], [], []
    for rel in paths:
        src = os.path.join(RAW, rel)
        if not os.path.exists(src):
            continue
        if not is_pdf(src):
            skipped.append(rel)
            print(f"  non-pdf  {rel}", flush=True)
            continue
        try:
            reader = PdfReader(src)
            pages = len(reader.pages)
            text = "".join((p.extract_text() or "") for p in reader.pages)
        except Exception as exc:
            skipped.append(f"{rel}  [ERROR {exc}]")
            print(f"  ERROR    {rel}: {exc}", flush=True)
            continue
        cpp = len(text) // max(pages, 1)
        dst = os.path.join(OUT, rel + ".txt")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w") as fh:
            fh.write(WARNING)
            fh.write(f"source: raw/{rel}\npages: {pages}\nchars_per_page: {cpp}\n\n")
            fh.write(text)
        stats.append((rel, pages, len(text), cpp))
        flag = "  <-- NO TEXT LAYER" if cpp < 100 else ""
        if cpp < 100:
            no_text.append(f"{rel}\t{pages} pages\t{cpp} chars/page")
        print(f"  {pages:4d}p {cpp:6d} c/p  {rel}{flag}", flush=True)

    with open(os.path.join(OUT, "NO_TEXT_LAYER.txt"), "w") as fh:
        fh.write("Files with <100 chars/page -- no usable text layer (scans/handwriting).\n")
        fh.write("Tier 2 (marker --force_ocr) handles these identically; OCR of handwriting\n")
        fh.write("is imperfect, so spot-check against the PDF before quoting.\n\n")
        fh.write("\n".join(no_text) + "\n")
    if skipped:
        with open(os.path.join(OUT, "SKIPPED.txt"), "w") as fh:
            fh.write("\n".join(skipped) + "\n")

    total_pages = sum(s[1] for s in stats)
    print(f"\nextracted={len(stats)} total_pages={total_pages} "
          f"no_text_layer={len(no_text)} skipped={len(skipped)}", flush=True)


if __name__ == "__main__":
    main()
