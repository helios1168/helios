#!/usr/bin/env python3
"""Mirror https://cims.nyu.edu/~gromov/ (Apache autoindex) with curl.

Polite: bounded pool of 3 connections, ~1s stagger. Resumable: skips files whose
local size already matches Content-Length. Writes manifest.tsv + duplicates.txt.
"""
import concurrent.futures as cf
import hashlib
import html as _html
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

BASE = "https://cims.nyu.edu/~gromov/"
ROOT = os.path.expanduser("~/resources/gromov")
RAW = os.path.join(ROOT, "raw")
MAX_CONN = 3
STAGGER = 1.0

HOST = urllib.parse.urlparse(BASE).hostname

_gate = threading.Semaphore(MAX_CONN)
_last = [0.0]
_lock = threading.Lock()


def _resolve_args():
    """getaddrinfo/mDNSResponder is unreachable in this environment, so curl
    cannot resolve names. nslookup's direct UDP queries still work -- resolve
    once here and pin the result with --resolve on every request."""
    out = subprocess.run(["nslookup", HOST], capture_output=True,
                         text=True).stdout
    ips = re.findall(r"^Address:\s*([0-9.]+)$", out, re.M)
    if not ips:
        raise SystemExit(f"could not resolve {HOST} via nslookup")
    args = []
    for port in (80, 443):
        args += ["--resolve", f"{HOST}:{port}:{ips[0]}"]
    print(f"resolved {HOST} -> {ips[0]}", flush=True)
    return args


RESOLVE = _resolve_args()


def _throttle():
    with _lock:
        wait = _last[0] + STAGGER - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def curl(args, binary=False):
    _throttle()
    with _gate:
        r = subprocess.run(["curl", "-sSL", "--max-time", "600", "--retry", "3"]
                           + RESOLVE + args,
                           capture_output=True)
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


HREF = re.compile(r'<a href="([^"]+)"', re.I)


def list_dir(url):
    """Return (subdirs, files) as absolute URLs."""
    html = curl([url])
    dirs, files = [], []
    for href in HREF.findall(html):
        if href.startswith("?") or href.startswith("#"):
            continue
        if href in ("/", "../") or href.startswith("http"):
            continue
        full = urllib.parse.urljoin(url, _html.unescape(href))
        # never escape the /~gromov/ prefix
        if not full.startswith(BASE):
            continue
        if full == url:
            continue
        (dirs if full.endswith("/") else files).append(full)
    return dirs, files


def local_path(url):
    rel = urllib.parse.unquote(url[len(BASE):])
    rel = rel.replace("/./", "/")
    return os.path.join(RAW, rel)


def remote_size(url):
    head = curl(["-I", url])
    m = re.search(r"^content-length:\s*(\d+)", head, re.I | re.M)
    return int(m.group(1)) if m else None


def last_modified(url_head):
    m = re.search(r"^last-modified:\s*(.+)$", url_head, re.I | re.M)
    return m.group(1).strip() if m else ""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url):
    path = local_path(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _throttle()
    with _gate:
        head = subprocess.run(["curl", "-sSLI", "--max-time", "120"] + RESOLVE + [url],
                              capture_output=True).stdout.decode("utf-8", "replace")
    m = re.search(r"^content-length:\s*(\d+)", head, re.I | re.M)
    size = int(m.group(1)) if m else None
    lm = last_modified(head)
    if size is not None and os.path.exists(path) and os.path.getsize(path) == size:
        print(f"  skip  {os.path.relpath(path, RAW)}", flush=True)
        return url, path, os.path.getsize(path), sha256(path), lm
    _throttle()
    with _gate:
        subprocess.run(["curl", "-fsSL", "--retry", "3", "--max-time", "1800"]
                       + RESOLVE + ["-C", "-", "-o", path, url],
                       capture_output=True)
    got = os.path.getsize(path) if os.path.exists(path) else 0
    if got == 0:
        if os.path.exists(path):
            os.remove(path)
        print(f"  FAIL  {os.path.relpath(path, RAW)}  (server refused; see index)",
              flush=True)
        return url, path, 0, "", lm
    status = "ok" if (size is None or got == size) else f"SIZE-MISMATCH exp={size}"
    print(f"  get   {os.path.relpath(path, RAW)}  {got}B {status}", flush=True)
    return url, path, got, sha256(path) if got else "", lm


def main():
    print(f"crawling {BASE}", flush=True)
    seen, queue, files = {BASE}, [BASE], []
    while queue:
        d = queue.pop(0)
        print(f"[dir] {urllib.parse.unquote(d[len(BASE):]) or '/'}", flush=True)
        subdirs, fs = list_dir(d)
        files.extend(fs)
        for sd in subdirs:
            if sd not in seen:
                seen.add(sd)
                queue.append(sd)
    files = sorted(set(files))
    print(f"\n{len(files)} files across {len(seen)} directories\n", flush=True)

    rows = []
    with cf.ThreadPoolExecutor(max_workers=MAX_CONN) as ex:
        for row in ex.map(fetch, files):
            rows.append(row)

    man = os.path.join(ROOT, "manifest.tsv")
    with open(man, "w") as fh:
        fh.write("url\tpath\tbytes\tsha256\tlast_modified\n")
        for url, path, n, digest, lm in rows:
            fh.write(f"{url}\t{os.path.relpath(path, RAW)}\t{n}\t{digest}\t{lm}\n")

    groups = {}
    for _, path, n, digest, _ in rows:
        if digest:
            groups.setdefault(digest, []).append(os.path.relpath(path, RAW))
    dupes = {d: p for d, p in groups.items() if len(p) > 1}
    with open(os.path.join(ROOT, "duplicates.txt"), "w") as fh:
        for d, paths in sorted(dupes.items()):
            fh.write(f"{d}\n")
            for p in sorted(paths):
                fh.write(f"\t{p}\n")

    total = sum(r[2] for r in rows)
    print(f"\nfiles={len(rows)} distinct={len(groups)} dupe-groups={len(dupes)} "
          f"bytes={total} ({total/1e6:.1f} MB)", flush=True)
    print(f"manifest: {man}", flush=True)


if __name__ == "__main__":
    main()
