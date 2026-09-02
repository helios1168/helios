# Gromov corpus — status / resume point

**Last updated: 2026-09-01 09:15.** Read this first after a restart or a new session.

Plan: `~/.claude/plans/fluffy-kindling-fern.md` (approved 2026-08-30).
Goal: mirror Gromov's CIMS page, OCR it math-faithfully, distil a **Gromov-only** method
artifact grounded in citations. `~/claude-dev/method.md` is NOT touched; the output is
separate. **All phases are now complete** — see "Phase 4 DONE" below.

## Done

- **Phase 1 mirror** — 73 files, 200.5 MB, 66 distinct docs in `raw/`. `manifest.tsv` has
  url/path/bytes/sha256/Last-Modified. 3 files are **permanently 403** on NYU's server:
  `CIMS isoperimetryetc.pdf`, `nash.png`, `Probablity&topology.tex`.
- **Phase 2 Tier 1** — `text/`, 2,900 pages via pypdf. NOT math-faithful; locating only.
- **Phase 2 Tier 2** — `markdown/`, **66/66 documents, 2,601 pages**, marker 2.0 + Surya on
  MPS, 7h44m at 4.1 pages/min. Validation gate PASSED: 0 glyph artifacts, 11,331 inline math
  spans, 353 `\frac`.
- **Phase 3a bundles** — `units/*.md`, 10 units, all under the 400k-token cap, ~2.06 M tokens
  total. `units/*.candidates.tsv` holds the coverage-audit page lists.
- **Phase 3b COMPLETE — all 10 readers done.** 947 method passages; **922 machine-verified
  verbatim against the exact page cited (97.4%)**. All page-citation shifts corrected. The 25
  unverified are graded in `QUOTE_DEFECTS.tsv`.

| unit | entries | verified | note |
|---|---|---|---|
| cognition-b | 180 | 170 | slide scans; 6 INVENTED |
| cognition-a | 147 | 140 | scans; 3 INVENTED |
| bio-dimensions | 112 | 107 | 1 INVENTED |
| probability-nash-entropy | 100 | 100 | |
| curvature-scalar | 98 | 98 | |
| probability-paris | 76 | 76 | pilot; 4 page shifts corrected |
| survey | 62 | 62 | |
| isoperimetry | 60 | 60 | **re-run**; first attempt rejected |
| curvature-hopf | 57 | 54 | |
| curvature-misc | 55 | 55 | |

**`QUOTE_DEFECTS.tsv` — 25 rows, graded by `_tools/classify_defects.py`:**
INVENTED 10 · MINOR 6 · ELISION 4 · SYMBOL 3 · REPAIRED 2.

The **INVENTED** ten are the only ones that threaten the artifact, and they share one cause:
in the scanned cognition material and one bio document, OCR interleaves a caption, epigraph or
bibliography *into the middle of a sentence*, and the reader wrote a smooth completion over the
gap — "this is why we" became "this is why we would rather reject than accept them". The prose
is not Gromov's. **The adversarial pass must re-read these pages and drop or repair the quote;
they must not reach the synthesis on trust.** They cluster entirely in `cognition-a`/`-b` and
`bio-dimensions` — the three units built from scans.

- **Phase 3d COMPLETE — adversarial pass.** All 947 claims adjudicated on Sonnet 5, one agent
  per unit, none with sight of the extraction reasoning. Merged to `DISTILLATION/adversarial.md`.
  **SUPPORTED 878 (92.7%) · WEAK 52 · TECHNICAL 7 · UNSUPPORTED 4 · VACUOUS 3 · OVERREACH 2 ·
  MISATTRIBUTED 1.** Coverage is machine-checked by `_tools/merge_adversarial.py`: every claim
  id in the packets has exactly one verdict, and no verdict names a claim that does not exist.

  **Treat 92.7% with suspicion, not satisfaction.** The readers were told to err toward
  inclusion, so a filter that clears 93% is either finding little chaff or being agreeable.
  The synthesis must not read SUPPORTED as a guarantee — it means one fresh reader, shown the
  passage and its surrounding pages, did not find the Move overstated.

- **Phase 3e COMPLETE — `ARTIFACT_DRAFT.md`.** Fable 5 clustered the 930 surviving claims into
  **14 moves**, plus a constructed stopping rule and an evidence-limits section. 292 lines.
  **Citations verified by `_tools/verify_artifact.py`: all 41 block quotes resolve both to the
  claim id they cite AND, independently, to the bundle page that claim cites; all 143 distinct
  claim ids cited anywhere in the prose exist.** Evidence spans all 10 units and 19 documents.
  Cited claims fall in every decile of the input file, so the single pass did not truncate.

## Phase 4 DONE — 2026-09-01

Both open questions were answered by the user on 2026-09-01: **all fourteen moves**, installed
as a **slash command**, not a skill.

| file | role | state |
|---|---|---|
| `ARTIFACT_DRAFT.md` | evidence base, 292 lines, never loaded into context | de-AI'd 2026-09-01, gate still 41/41 |
| `~/.claude/commands/gromov.md` | the operable extract, 174 lines | installed, registered as `/gromov` |
| https://claude.ai/code/artifact/dbd9b02a-18ae-467c-8cdb-beb7dd4519e2 | readable version | republished with the de-AI'd prose |

**How fourteen moves stay usable.** The command routes by trigger rather than dumping all
fourteen: it tells the model to pick the two or three whose *Fires when* line genuinely matches,
name two or three that plausibly apply but do not and say why not, and treat "none fires" as a
real answer meaning the problem is not yet stated well enough (which is Move 1). Each move
compresses to two lines, *Fires when* and *Do*. The quotes and Range paragraphs stay in
`ARTIFACT_DRAFT.md`; the command points at it rather than carrying it.

**The stopping rule travels, labelled.** Heading is `## Stopping: NOT Gromov's`, the disclaimer
is in the body text rather than only in the heading, and it states *why* it is labelled. The
skill-vs-command choice is what made this safe: a deliberately invoked command cannot fire
unbidden mid-task, which was the specific risk against carrying it into a skill.

**The command asserts zero claim ids of its own** — so there is nothing in it that can drift out
of sync with the evidence base. Its closing rule is: never invent a Gromov citation; quote from
`ARTIFACT_DRAFT.md` with its claim id or do not quote.

Move 9 carries an inline "least validated of the fourteen" flag, Move 7 its generalization cap,
Move 11 its precondition, Move 12 its "a prior, not a law".

### The de-AI pass on `ARTIFACT_DRAFT.md`

Ran `/avoid-ai-writing` in edit mode, prose only. Vocabulary was already clean (3 real hits in
5,291 words of compiler prose); the tells were structural.

| | before | after |
|---|---|---|
| prose em dashes | 65 (12.3 per 1,000 words) | 2 (0.38) |
| Range paragraphs closing on the same em-dash aphoristic reversal | 8 of 14 | 0 |
| `load-bearing` metaphor | 3 | 0 |

Also cut: moral adverbs on near-passive constructions (`estimate honestly`, `priced honestly`,
`an honest account`), `real`/`actual` intensifiers with no named contrast (`a real yield`,
`the actual problem`), `quietly`, and adverbial hyphenation (`character-by-character`,
`hour-by-hour`). Move 10's title changed from "Dissect the proof for its load-bearing atom" to
"Dissect the proof down to the property it uses".

Kept deliberately: the `**The move** / Trigger / In his words / Range` schema (it is what makes
the document scannable, and it is genuinely list-shaped), one "not X but Y" reversal in Move 2's
Range, and the Move 4 heading em dash, which is quoting his motto.

**The edit was gated, not eyeballed.** The applying script asserted every `> ` quote line
byte-identical and every claim id unchanged, and aborts without writing if either fails. It did
abort once, on an apostrophe mismatch, and wrote nothing. Afterwards `verify_artifact.py` still
returns **41/41**. Pre-edit backup: `$CLAUDE_JOB_DIR/tmp/ARTIFACT_DRAFT.pre-deai.md` (job-scoped,
will not survive job deletion — re-copy it somewhere durable if it matters).

**Trap for the artifact republish:** the HTML prose had drifted slightly from the markdown
(shortened Range paragraphs, some claim ids dropped), so the two files do NOT share replacement
strings. They were edited separately, each with its own guard — the HTML script asserted all 41
`<blockquote>` elements byte-identical. Do not assume a markdown edit can be replayed on the
artifact by string substitution.

### If you pick this up again

Nothing is pending. Possible next steps, none started:

- `~/claude-dev/method.md` is still untouched. The corpus's verdict on its five asserted moves is
  at the bottom of this file and was never acted on.
- The 3 permanently-403 CIMS files were never recovered.
- `/gromov` has not been exercised on a real problem yet, so its trigger-routing instruction is
  untested against anything but its own design.

**Already applied in Phase 3e — recorded here as the rule, not as pending work.** The 4
UNSUPPORTED claims were dropped outright, their substance living entirely in text the reader
invented over an OCR break: `bio-dimensions-061`, `cognition-a-038`, `cognition-b-092`,
`cognition-b-106`. WEAK was treated as "usable only in the narrower reading the adjudicator
states", and TECHNICAL/VACUOUS/MISATTRIBUTED/OVERREACH as unusable. Re-apply the same rule to
any future synthesis pass.

## Gates — run these on every reader output, do not eyeball

```
python3 _tools/coverage_audit.py <unit>          # truncation detector
python3 _tools/verify_quotes.py  <unit>          # every quote vs. its cited page
python3 _tools/verify_quotes.py  <unit> --fix    # rewrite p.<n> to the true page
python3 _tools/classify_defects.py               # grade QUOTE_DEFECTS.tsv by severity
```

`verify_quotes.py` locates each quote in the whole cited *document*, so it can **correct** a
page number rather than merely reject it — the quote is the evidence, the page is derived.
It reports `SHIFT` when the cited page is wrong and `FAIL` only when the text is not in the
document at all. Comparison ignores whitespace, marker's unstable LaTeX spacing, line-break
hyphens, `<sup>` footnotes, image placeholders and `$$`→`$`; it still catches any changed,
added, dropped or reordered word.

**Calibration.** Comparison folds away what OCR and notation style vary without changing
words: whitespace, marker's LaTeX spacing, line-break hyphens (`trun-cated`), `<sup>`
footnotes, image placeholders, `$`/`$$` delimiters, quote characters (an entry wraps its quote
in `"..."`, so readers render the source's own `"` as `'`), and LaTeX names against the symbols
readers type instead (`\Sigma`/`Σ`, `\mathcal{L}`/`L`). A changed, added, dropped or reordered
word still fails, and so does a dropped subscript.

The control for "is this now too loose": the **rejected** first `isoperimetry` output still
fails 23 of 46 (50%) under these rules, against 0–5% for every accepted unit. Keep that file at
`$CLAUDE_JOB_DIR/tmp/isoperimetry.rejected-nonverbatim-math.md` and re-run it as a control
after any change to `canon()`.

`QUOTE_DEFECTS.tsv` classifies every surviving failure: INVENTED (4), ELISION (3), MINOR (3),
REPAIRED (2), SYMBOL (2). Feed it to the adversarial pass — those entries need their page read,
not their quote trusted.

## Units

| unit | docs | est tokens |
|---|---|---|
| curvature-scalar | 1 | 344,530 |
| curvature-misc | 16 | 300,146 |
| cognition-a | 3 | 271,430 |
| probability-nash-entropy | 15 | 260,865 |
| isoperimetry | 5 | 227,619 |
| cognition-b | 5 | 218,411 |
| curvature-hopf | 3 | 198,054 |
| bio-dimensions | 6 | 169,456 |
| probability-paris (DONE) | 8 | 71,049 |
| survey (DONE) | 3 | 43,012 |

## Tools (`_tools/`)

`mirror.py` · `extract_text.py` · `stage.py` · `bundle.py` · `prefilter.py` ·
`coverage_audit.py` · `verify_quotes.py` · `classify_defects.py` · `build_packets.py` ·
`merge_adversarial.py` · `run_pipeline.sh` · `dns_proxy.py` ·
`.venv/` (marker-pdf 2.0.0) · `llamacpp/` (vendored llama-server).

**Re-running is safe and idempotent**: `bash _tools/run_pipeline.sh` skips every downloaded
file and every converted document. `_tools/.venv` and `llamacpp/` are ~1 GB — regenerable but
slow, don't delete casually. `raw_flat/` is hardlinks, costs no extra disk.

## Traps already hit — do not rediscover these

- **marker does NOT recurse** into subdirectories; pointing it at `raw/` converts only the 11
  root files and exits 0. `stage.py` flattens into `raw_flat/` with ` ~ ` encoding paths;
  `bundle.py` decodes it. Gate on `markdown files: 66`.
- **Unit rules must match `.md` paths**, never `.pdf` — three rules silently stranded
  documents.
- **One mangled stem**: raw `mathematicals models of "understanding"pdf` lacks the dot before
  `pdf` and sits in a directory ending in `.`, so marker's `splitext` truncated its output to
  `mathematic of mental processes/`. Explicitly mapped to `cognition-b`.
- **marker page separators are 0-based**; `bundle.py` converts to 1-based so citations match
  real PDF pages.
- **Agent-sandbox limits** (see `~/.claude/CLAUDE.md`): no `getaddrinfo`, no Metal, no
  `nohup`, `pgrep`/`pkill` fail with exit 3. GPU work must run in the user's own shell —
  `bash _tools/run_pipeline.sh` from a tmux pane, never via the `!` prefix.
- The dns_proxy is only needed when something must reach the network from the agent sandbox.
  Nothing left in this pipeline does.

## Traps hit in Phase 3b — do not rediscover these either

- **`Read` returns 2,000 lines by default, and caps output around ~40 K characters.** The
  plan's "read the bundle in ONE Read call" only ever worked for `survey` (1,138 lines). Every
  other bundle is 5,898–15,685 lines, and even a 2,000-line call **fails outright** on the
  token cap. Readers get an explicit chunk plan sized to ~40 K chars/read (450–650 lines
  depending on the unit's chars-per-line). Contiguous, read-once chunks cost the same as one
  read — the ~1.15× multiplier comes from never RE-reading, not from the call count.
- **Readers cite the page marker that FOLLOWS the quote.** `probability-paris` had 4 citations
  off by exactly −1, all in its longest document. The brief now carries an explicit
  "cite the marker that precedes the text" guard, and `verify_quotes.py --fix` corrects the
  rest mechanically.
- **marker keeps the PDF's line-break hyphens** (`trun-cated`, `het-erogeneous`). A correct
  verbatim quote silently repairs them, so any naive string compare fails. Both sides get
  hyphens stripped.
- **Do not trust a reader's own coverage self-report.** The pilot's was confident, detailed,
  and wrong about its citations. It was right about its read plan — which it had to deviate
  from, and said so. Ask for the self-report, then verify it mechanically anyway.
- **`units/*.candidates.tsv` snippets contain raw newlines**, so a record is not a line. Parse
  by leading doc_id, folding continuation lines into the previous record.
- **Duplicate documents are not truncation.** `probability-paris` holds two duplicate pairs
  (02=04, 05=06) and the brief tells the reader to cite each pair once, so a twin is correctly
  uncited. `coverage_audit.py` pairs docs by identical (pages, chars) before flagging. No other
  unit has duplicates.

## Traps hit while building the gates — mine, not the readers'

- **A control is mandatory when loosening a verbatim check.** Folding away OCR noise took
  failures from 115 to 25, and every loosening risks turning the gate into a rubber stamp. The
  rejected `isoperimetry` output is kept as the control precisely because it *should* fail;
  it still fails 22/46 (48%) against 0–5% for accepted units. **Re-run it after any change to
  `canon()`.** Note that running `verify_quotes.py` on the control alone overwrites
  `QUOTE_DEFECTS.tsv` with control rows — re-run the full pass afterwards.
- **`--fix` took the FIRST occurrence of a quote in the document.** "As far as the scalar
  curvature is concerned," appears on pages 24, 232 and 339 of one course, so a correct
  citation was reported 208 pages wrong and would have been "corrected" into a wrong one. It
  now tries every occurrence and keeps the completed match nearest the cited page.
- **`--fix` rewrote by (doc, page), not by entry.** Two quotes citing one page, only one
  shifted, made every run flip both back and forth. One bad edit reached disk before this was
  caught and was reverted. It now edits a single line by index.
- **Readers cannot put a raw `"` inside a quote** — the entry format wraps quotes in `"..."`,
  so they render the source's own double quotes as `'`. That alone accounted for ~70 apparent
  failures in `cognition-a`.

## Phase 3d mechanics, and the bug that nearly wasted it

The plan's "one agent re-opens each cited page" does not scale to 947 claims.
`_tools/build_packets.py` instead assembles a self-contained packet per claim — move name,
quote, claimed move, reader confidence, verbatim-check result, and the raw bundle text of the
pages the quote actually spans. Costs no tokens, and the adjudicators still never see the
extraction reasoning, which is the point of running the pass fresh.

- **Never window packet context by character offset.** Offsets come from the *canonicalized*
  text (whitespace and markup stripped) and do not map onto raw page text. The first build had
  **450 of 947 packets not containing the quote they asked about.** Select whole PAGES by the
  span the match covers; 6 then miss, and those 6 are the INVENTED quotes that are genuinely
  not in the source.
- **`verify_quotes.py` rewrites `QUOTE_DEFECTS.tsv` WITHOUT the grading columns.** Run
  `classify_defects.py` after it, every time. A packet built in between shows every defect as
  `? —`, which is how 10 INVENTED claims first reached the adjudicators unlabelled.
  `build_packets.py` now exits with an error rather than emitting placeholders.
- **Key a defect by its quote, not by `(unit, doc, page)`.** Several claims can cite one page;
  page-keying turned 25 defects into 38 flags and mislabelled 13 sound claims.
- **The re-run proved the fix mattered.** Re-adjudicated with correct flags, 4 of the 10
  INVENTED claims flipped SUPPORTED → UNSUPPORTED: the move's substance lived entirely in the
  fabricated tail. Without the fix all four would have entered the artifact.
- Three adjudicators independently reported the broken flags instead of working around them.
  Their reports are still not evidence of coverage — `merge_adversarial.py` is.
- marker emits stray NUL bytes; two of them make `grep` treat a packet as binary. Control
  characters are stripped at packet build.
- **Watch the shell's working directory.** A `cd` in one command persists into later ones; an
  earlier `cd DISTILLATION/_packets` silently broke a run of relative-path checks.

## What the corpus said about `~/claude-dev/method.md`

The original motive was to test five moves that file asserts from memory. The synthesis never
saw it, so the comparison is clean:

- **Soft/rigid and the h-principle survive** and are strongly attested (35 and 38 hits across
  the 930 claims; draft Move 12 is built on the soft-hard chart). This is the file's best call.
- **"Space of the solutions"** survives as draft Move 7 ("pass to the space of its kin"), though
  the corpus grounds it in moduli/configuration spaces rather than in ranking a list of options.
- **"Scale out" / "change the resolution"** is much weaker than asserted. `coarse` appears 7
  times and `resolution` twice in 930 claims, and neither produced a cluster. The file's framing
  sentence — "Grothendieck changes the object, Gromov changes the resolution" — is not what this
  corpus shows; changing the *object* is if anything the better-attested Gromov move.
- **"Growth"** did not survive as a move at all (9 hits, no cluster).
- The corpus instead yields moves the file has none of: counting as a first instrument,
  bounding by the stupidity of the generating process, the statement ladder, ignorance
  bookkeeping, and language-building as the primary deliverable.

Draft Move 14 (ignorance bookkeeping) and Move 8 (the priced surrogate retreat) are the two the
synthesis rates most transferable at no technical cost.
