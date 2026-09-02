#!/bin/bash
# Phases 1-2: mirror, Tier 1 prose index, Tier 2 math-faithful markdown.
#
#   bash run_pipeline.sh          # auto: mps if Metal works, else cpu
#   bash run_pipeline.sh mps      # force GPU  (~30-90 min)
#   bash run_pipeline.sh cpu      # force CPU  (hours)
#
# Environment notes, all discovered the hard way on this box:
#  * getaddrinfo/mDNSResponder is unreachable from the agent sandbox, so uv,
#    pip and huggingface_hub cannot resolve ANY hostname. _tools/dns_proxy.py
#    is a local CONNECT proxy that resolves via nslookup; TLS stays end-to-end.
#    Start it first:  python3 _tools/dns_proxy.py 8899 &
#    In a normal login shell none of this is needed -- unset the proxy vars.
#  * mirror.py does its own resolution via curl --resolve, proxy or not.
#  * HuggingFace's Xet transport is a Rust client that ignores the proxy;
#    HF_HUB_DISABLE_XET=1 forces classic HTTP downloads that respect it.
#  * marker 2.0 runs inference through llama.cpp and needs a llama-server
#    binary. brew install llama.cpp fails through the proxy (its downloader
#    chokes on a -1 content length), so a prebuilt binary is vendored in
#    _tools/llamacpp/. In a normal shell, `brew install llama.cpp` is cleaner.
#  * Metal (MTLCompilerService) is also unreachable from the agent sandbox, so
#    MPS reports available but every compute call throws. That is why this
#    script probes the device instead of trusting torch.
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export NO_PROXY=127.0.0.1,localhost
export HF_HUB_DISABLE_XET=1

ROOT="$HOME/resources/gromov"
T="$ROOT/_tools"
LOG="$ROOT/pipeline.log"
VENV="$T/.venv"

# Prefer the network directly. Only fall back to the local CONNECT proxy when
# this shell genuinely cannot resolve names (the agent sandbox) AND the proxy
# is up -- a normal login shell must not depend on the agent's proxy, which
# dies with the agent session.
if ! python3 -c "import socket;socket.getaddrinfo('pypi.org',443)" 2>/dev/null; then
  if nc -z 127.0.0.1 8899 2>/dev/null; then
    export HTTPS_PROXY=http://127.0.0.1:8899
    export HTTP_PROXY=http://127.0.0.1:8899
    echo "note: system resolver unavailable -- routing through local dns_proxy"
  else
    echo "WARNING: cannot resolve hostnames and no proxy on 8899."
    echo "         Downloads will fail. Start: python3 _tools/dns_proxy.py 8899 &"
  fi
fi

# Prefer a system llama-server (brew); fall back to the vendored binary.
if command -v llama-server >/dev/null 2>&1; then
  export LLAMA_CPP_BINARY="$(command -v llama-server)"
else
  VENDORED=$(find "$T/llamacpp" -name llama-server -type f 2>/dev/null | head -1)
  [ -n "$VENDORED" ] && export LLAMA_CPP_BINARY="$VENDORED"
fi

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# --- device selection -------------------------------------------------------
DEVICE="${1:-auto}"
if [ "$DEVICE" = "auto" ]; then
  if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" - <<'PY' 2>/dev/null
import sys, torch
try:
    (torch.ones(4, device="mps") * 2).sum().item()
except Exception:
    sys.exit(1)
PY
  then DEVICE=mps; else DEVICE=cpu; fi
fi
export TORCH_DEVICE="$DEVICE"
[ "$DEVICE" = "cpu" ] && export GGML_METAL=0 LLAMA_ARG_N_GPU_LAYERS=0
WORKERS=4; [ "$DEVICE" = "cpu" ] && WORKERS=2
say "device=$DEVICE workers=$WORKERS llama_server=${LLAMA_CPP_BINARY:-NONE}"

say "=== PHASE 1: mirror ==="
python3 "$T/mirror.py" 2>&1 | tee -a "$LOG"
say "phase 1 exit=${PIPESTATUS[0]}"

say "=== PHASE 2 TIER 1: pypdf prose index ==="
uv run --quiet --with pypdf python "$T/extract_text.py" 2>&1 | tee -a "$LOG"
say "tier 1 exit=${PIPESTATUS[0]}"

say "=== PHASE 2 TIER 2: marker install ==="
if [ ! -x "$VENV/bin/marker" ]; then
  uv venv --python 3.12 "$VENV" 2>&1 | tee -a "$LOG"
  uv pip install --python "$VENV/bin/python" marker-pdf==2.0.0 2>&1 | tail -15 | tee -a "$LOG"
fi
if [ ! -x "$VENV/bin/marker" ]; then
  say "MARKER INSTALL FAILED -- stopping before Tier 2"; exit 1
fi
if [ -z "${LLAMA_CPP_BINARY:-}" ]; then
  say "NO llama-server BINARY -- marker 2.0 cannot run. brew install llama.cpp"; exit 1
fi

# marker does NOT recurse into subdirectories -- pointing it at raw/ silently
# converts only the 11 root-level files. stage.py hardlinks every distinct PDF
# into a flat raw_flat/ with path-encoded names, skipping ones already done.
say "=== PHASE 2 TIER 2: staging flat corpus ==="
python3 "$T/stage.py" 2>&1 | tee -a "$LOG"

say "=== PHASE 2 TIER 2: marker convert (force_ocr, paginate, $DEVICE, $WORKERS workers) ==="
"$VENV/bin/marker" "$ROOT/raw_flat" \
    --output_dir "$ROOT/markdown" \
    --force_ocr --paginate_output --workers "$WORKERS" --output_format markdown 2>&1 \
    | tail -60 | tee -a "$LOG"
say "marker exit=${PIPESTATUS[0]}"

say "=== VALIDATION GATE ==="
BAD=$(grep -rlE '/uni[0-9A-F]{4}|/divides\.alt0|/slash\.right' "$ROOT/markdown" 2>/dev/null | wc -l | tr -d ' ')
say "files with glyph-name artifacts: $BAD  (expect 0)"
say "markdown files: $(find "$ROOT/markdown" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')  (expect 66)"
say "raw: $(du -sh "$ROOT/raw" 2>/dev/null | cut -f1)  $(find "$ROOT/raw" -type f | wc -l | tr -d ' ') files"
say "=== PIPELINE DONE ==="
