# Grothendieck corpus — status / resume point

**Last updated: 2026-09-02.** Nothing built yet. `~/.claude/commands/grothendieck.md` is an
**uncited draft from memory**; this corpus replaces it with a cited artifact, following the
gromov pipeline (`~/resources/gromov/STATUS.md`, plan `~/.claude/plans/fluffy-kindling-fern.md`).

## Decisions (2026-09-01, user)
- English-translated sources first; French originals only where no translation exists,
  quoted verbatim in French.
- Mirror what is public; the user drops journal/book PDFs into `raw/` by hand. `manifest.tsv`
  records provenance, language and translator per file.
- Nash corpus: not started, not planned for now.

## Candidate sources (verify availability and licence at build time)
| source | language | where | note |
|---|---|---|---|
| Récoltes et Semailles, Slaoui translation | en | web.ma.utexas.edu/users/slaoui/notes/recoltes_et_semailles.pdf | the UT Austin one |
| Récoltes et Semailles, Tong Chow | en | tongchow.github.io/ReS.html | web pages, not PDF |
| Récoltes et Semailles, SayantanRoy95 | en | github.com/SayantanRoy95/R-et-S | repo |
| R&S first part (Columbia course copy) | en | math.columbia.edu/~harris/… recoltes-et-semailles-first-part.pdf | partial |
| Promenade + Introduction, Lisker | en | book (ISBN 9780993926914) | user-supplied if owned |
| Esquisse d'un Programme | en | Schneps–Lochak 1997 pp. 243–283; Extremadura-hosted copy | |
| Pursuing Stacks | en | arXiv 2111.01000 | 600 pp, mostly maths; method in the letter and asides |
| La Clef des Songes | en (partial) | github.com/ReveLogic/AlexGrothendieck; Columbia excerpt | chapterwise |
| Grothendieck–Serre Correspondence | fr/en | AMS bilingual (user-supplied) | |
| Récoltes et Semailles | fr | Grothendieck Circle / iut-theory.org archive; Gallimard 2022 | fallback for untranslated parts |
| Index of translations | — | csg.igrothendieck.org/translations, Grothendieck Circle | start here |

## Pipeline changes vs gromov
- `_tools/` to be copied from `../gromov/_tools/` with `ROOT` and `bundle.py`'s `RULES` moved
  into `corpus.toml`; `mirror.py` replaced by a `sources.tsv`-driven fetcher.
- Reader brief = fluffy-kindling-fern §3b with the chunk-plan fix from gromov STATUS.md;
  "Gromov" → "Grothendieck"; add: "quote translations verbatim as printed; record translator".
- Artifact must say per quote whether it is a translation and by whom.

## Not started
mirror · extraction · units · readers · verify_quotes · adversarial · synthesis · install.
