# Combined tagger: architecture for whole-document Pattern-6 tagging

How to structure a from-scratch tagger that scales from a spike to a full
document. Proven end-to-end on a 96-page untagged QuarkXPress handbook
(2,605 MCIDs, 37 page-design signatures, gate PASS on every page); the
handler-registry abstraction survived three missions of new page designs
with zero restructures.

**The engine ships with this skill.** `scripts/tagger_engine.py` is the
document-agnostic machinery — the content-stream parser, the PagePlan
mark/emit IR, the three handlers (`sheet`/`worksheet`/`toc`), the
struct-element factory, and `build()`. You do NOT rebuild it per document.
`scripts/example_tagger_assessing_rr_rh.py` is a complete worked config for
the pilot handbook — read it to see what a real `PAGE_CLASSES`/`CONFIGS`
pair looks like (anchors, bands, builder functions, ToC bookmark parser).
A new document is a new config file like the example, calling
`build(src, dst, PAGE_CLASSES, CONFIGS, doc_title=..., doc_lang=...,
bookmarks_fn=...)`. The coordinates in the example are document-specific
and will not transfer; the shape is what transfers.

## Mission ladder (don't tag page 1 first)

1. **Census first.** Script a page-class census over the whole document:
   per page, the font/size signature set, rotation, painting ops. Cluster
   into design classes. This is the bill of materials — it tells you how
   many *handlers* (structural strategies) and how many *configs* (per-design
   parameters) you need, and which pages are siblings. Expect the census to
   be optimistic: visual siblings can differ semantically (a "4–6 design"
   estimate became 11 on inspection).
2. **Tag family-by-family,** largest/simplest first, re-running the FULL
   gate over the whole document after every mission (untouched pages must
   stay pixel-identical too).
3. **Close:** front matter, ToC, residual odd pages, then document metadata
   and bookmarks.

## Core architecture (pinned — these survived; don't relitigate)

- **Always rebuild from the pristine original.** Never tag-on-tagged.
  Corollary: every document-level fix (title, /Lang, ViewerPreferences,
  bookmarks) must live IN the build, or it silently vanishes on the next
  rebuild — we shipped two builds missing title/lang before catching this.
- **One shared content-stream parser** (`parse_page`) producing BT dicts of
  lines: per line, first/last op index, font set, effective size, runs with
  absolute writing-direction positions. Coordinate model that generalizes to
  rotation: per-BT `rot` ("H" or "R90" from the Tm matrix), per-line `row`
  (position ACROSS the writing direction — stacks reading lines) and `wx`
  (ABSOLUTE position ALONG it — anchors table columns across BTs that start
  at different x). Assert on unhandled operators; extend per producer.
- **Handler registry.** A handler = one structural strategy
  (`sheet` linear flow, `worksheet` banded tables, `toc` TOC/TOCI). New page
  designs should normally be a new CONFIG for an existing handler; a new
  handler is rare (one added per ~40 pages here). If you're rewriting a
  handler for one page, you probably want a config key with a default that
  preserves old behavior (`h1_stack_max`, `footer_y`, `min_cell_gap`…).
- **PagePlan intermediate representation.** Handlers emit a plan (marks =
  op ranges to wrap with BDC/EMC; items = element tree of node dicts with
  S-type, kids, attrs) — they never touch pikepdf objects. One emitter turns
  plans into stream rebuilds + struct elements. This is what keeps handlers
  small and the tree plumbing written once.
- **Struct-element factory takes Alt / ActualText / Scope** so any handler
  can attach semantics: Figures get prose Alt; symbol-font dingbats get
  per-glyph Spans with real Unicode ActualText (✗=U+2717 — `(cid:N)` reads
  as silence otherwise); TH gets Scope.
- **One /Document root, per-page StructParents + ParentTree.** Cross-page
  elements need MCR dicts with explicit /Pg — usually not worth it (a ToC
  spanning two pages shipped as two sibling TOCs; low harm, honest residual).

## Per-design judgment calls that recur

- **Flow diagrams / arrow-and-box pages:** linear tagging interleaves cells
  that share baselines mid-sentence. Tag H1 + ONE Figure over the body text
  groups with full prose Alt describing the flow; arrows stay Artifact.
- **Real two-column tables hiding in prose pages:** when cells share
  baselines, reuse the table handler with horizontal orientation rather
  than inventing splitting logic in the linear handler.
- **Right-aligned ToC:** entry number → Lbl, title+page runs → one P per
  TOCI, wrapped titles join the open entry. Watch `footer_y`: a default
  footer band silently artifacted the last legitimate entry on the page —
  coverage gates pass artifacted-but-shouldn't-be content; only human dump
  review catches it.
- **Bookmarks from the printed ToC:** content-stream bytes are useless under
  custom encodings — decode via a ToUnicode-aware extractor (pymupdf/
  pdfplumber), verify printed→PDF page mapping on actual footers, and guard
  the parser with asserts (sequential refs, no entry without a target).
  Outline writing is non-painting; the visual gate is unaffected.

## Table regularity and headers (Acrobat's two table checks)

Acrobat Full Check enforces two table rules the gate is structurally blind
to, and an external reviewer running it WILL find them (ours did: 24 of 43
tables failed): **Regularity** (every row must sum to the same column
count, counting ColSpan) and **Headers** (heuristic: a full-TH first row
or a full-TH first column). Tagging only the cells where text landed
fails Regularity on every sparse row — blank practice boxes, empty
yes/no/partial columns, a header row missing its corner stub.

The fix is emission-time, not parse-time — cells are already keyed by
column-anchor index, so grid positions are known:

- **Walk the full grid per TR** (table width = `len(anchors)`), not just
  occupied cells. Real cells emit marks in the same order as before, so
  MCID numbering is untouched.
- **Group-label THs get ColSpan** spanning the empty run up to the next
  occupied column (a section label like "People:" in a 3-column table →
  ColSpan 3; a label with a trailing comments cell → ColSpan 4 + TD).
- **Every other empty grid position gets an EMPTY struct cell** — TD in
  data rows, TH Scope=Column in header rows (the unlabeled corner stub
  over a row-label column, an unlabeled symbol column). Empty cells carry
  no MCIDs, so all three gate checks are unaffected by construction.
- **Tables with no visual header row:** pick the column that labels each
  row (the condition column of a rules table) → TH Scope=Row via config,
  rather than inventing a header row that isn't on the page.

Audit independently of the tagger: walk the struct tree, compute per-row
effective widths (sum of ColSpans) and apply Acrobat's header heuristic
(`scripts/table_audit.py`). Run it before AND after — it found the
header-less table the census had missed. More generally: an external
Acrobat Full Check is a cheap independent reviewer loop; its only
remaining items on a clean build are the two always-manual checks
(Logical Reading Order, Color contrast).

## The gate (three checks, every mission, full document)

`verify_tags.py original modified` — (1) pixel-identity on ALL pages at
fixed dpi; (2) MCID read-back by an independent parser (0 duplicates/
orphans/empties); (3) text coverage: every painted char on every tagged
page is inside an MCID or an explicit Artifact, with per-page artifact
counts — a sudden artifact-count jump on one page is how silently-dropped
content shows up. Then `pdf_a11y_check.py` for the census/heading/alt/scope
checks and the before/after delta table.

## Cost shape (for scoping the next document)

Parser, plan/emit plumbing, and the gate are document-agnostic — they port
as-is. Handlers port across documents of the same broad genre. What's
per-design: the classifier config (fonts, sizes, bands, anchors) and the
judgment calls above. First document of a template family ≈ several
sessions; siblings inherit handlers + most configs and are mostly machine
time plus alt-text review.
