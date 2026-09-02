#!/usr/bin/env python3
"""Flatten raw/ into raw_flat/ for marker.

marker only globs the top level of the directory it is given -- it does NOT
recurse -- so a nested corpus silently converts only its root files. This
builds a flat directory of hardlinks whose names encode the original relative
path, so nothing is lost and bundle.py can still route documents to units.

  raw/probability 2022-23/CIMS lecure2.pdf
    -> raw_flat/probability 2022-23 ~ CIMS lecure2.pdf

Only distinct sha256s are staged (duplicates are wasted OCR time), and files
already converted into markdown/ are skipped so a re-run resumes.
"""
import os
import shutil
import sys

ROOT = os.path.expanduser("~/resources/gromov")
RAW = os.path.join(ROOT, "raw")
FLAT = os.path.join(ROOT, "raw_flat")
MD = os.path.join(ROOT, "markdown")
SEP = " ~ "  # path separator encoding; must not occur in any real filename


def distinct_from_manifest():
    """One path per sha256, preferring the shortest (least nested) path."""
    best = {}
    with open(os.path.join(ROOT, "manifest.tsv")) as fh:
        next(fh)
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or not p[3]:
                continue
            path, digest = p[1], p[3]
            if digest not in best or len(path) < len(best[digest]):
                best[digest] = path
    return sorted(best.values())


def already_converted():
    return {d for d in os.listdir(MD)
            if os.path.isdir(os.path.join(MD, d))} if os.path.isdir(MD) else set()


def main():
    if SEP.strip() and any(SEP in r for r in distinct_from_manifest()):
        sys.exit(f"separator {SEP!r} occurs in a real filename -- pick another")
    os.makedirs(FLAT, exist_ok=True)
    done = already_converted()
    staged = skipped = nonpdf = 0
    for rel in distinct_from_manifest():
        src = os.path.join(RAW, rel)
        if not os.path.exists(src):
            continue
        with open(src, "rb") as fh:
            if fh.read(5) != b"%PDF-":
                nonpdf += 1
                continue
        flat_name = rel.replace("/", SEP)
        stem = flat_name[:-4] if flat_name.lower().endswith(".pdf") else flat_name
        # marker names its output dir after the stem; skip if already converted
        if stem in done or os.path.splitext(os.path.basename(rel))[0] in done:
            skipped += 1
            continue
        dst = os.path.join(FLAT, flat_name)
        if not os.path.exists(dst):
            try:
                os.link(src, dst)          # hardlink: marker sees a real file
            except OSError:
                shutil.copy2(src, dst)
        staged += 1
    print(f"staged={staged}  already_converted={skipped}  non_pdf={nonpdf}")
    print(f"flat dir: {FLAT}")


if __name__ == "__main__":
    main()
