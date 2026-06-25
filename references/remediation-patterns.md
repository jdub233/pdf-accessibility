# Remediation Patterns: Verifiable Tag-Tree Surgery

Proven on a 512-page QuarkXPress 6.5 book (1×H3/31×H5/252×H6 → clean
H1/H2/H3, zero bookmarks → 38-entry tree, pixel-identical output), a
96-page untagged QuarkXPress 7.31 handbook (from-scratch tagging spike,
Pattern 6), and a 25-page InDesign 18.5 magazine-style report
(tagged-but-flat: Patterns 2d, 7, 8 — 0→106 headings, 101 figure alts,
227 link descriptions, pixel-identical). All patterns use pikepdf.
Patterns 1–5, 7, and 8 touch ONLY structure elements, annotations, and
metadata — never content streams. Pattern 6 additionally inserts
non-painting marked-content operators (BDC/EMC) into content streams; no
pattern ever adds, removes, or alters a painting operator. That
restriction is what makes the visual-identity proof possible.

Read SKILL.md's "Remediation" section first for the non-negotiable
discipline (copy, verify, never claim PDF/UA, state limits).

## Shared plumbing

```python
import pikepdf
pdf = pikepdf.open('copy.pdf')          # ALWAYS a copy
pagemap = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}  # page obj -> index

def walk(e, fn, d=0):                    # struct-tree walker
    if d > 60: return
    fn(e)
    k = e.get('/K', None)
    if k is None: return
    items = k if isinstance(k, pikepdf.Array) else [k]
    for i in items:
        if isinstance(i, pikepdf.Dictionary):
            walk(i, fn, d + 1)
# start: walk each Dictionary child of pdf.Root['/StructTreeRoot']['/K']
```

Struct elements carry `/S` (type), `/Pg` (page ref — resolve via pagemap),
`/K` (kids: dicts, or integer MCIDs = leaf content), `/A` (attributes, where
Figure BBox and table Scope live), `/Alt`, `/Lang`.

## Pattern 1: Language, title, DisplayDocTitle (always safe, do first)

```python
pdf.Root.Lang = pikepdf.String("en-US")
with pdf.open_metadata() as meta:
    meta["dc:title"] = TITLE
pdf.docinfo["/Title"] = TITLE
if "/ViewerPreferences" not in pdf.Root:
    pdf.Root.ViewerPreferences = pikepdf.Dictionary()
pdf.Root.ViewerPreferences.DisplayDocTitle = True
```

Title should be the document's real title (render page 1 and look if
metadata is junk — don't guess from filename).

## Pattern 2: Heading recovery and re-leveling

Works when the visual design is consistent — professional layouts almost
always are. Three steps:

**(a) Recover true structure visually.** Scan all pages with pymupdf for text
spans; headings reveal themselves as a distinct (font, size) signature
(e.g., bold face at 2-3 discrete sizes). Dump candidates with page/y/size/
text, then YOU classify the levels by looking at the pattern (render a few
pages to confirm). Watch for: ToC entries mis-tagged as headings, running
headers, decorative drop-caps at huge sizes.

```python
import fitz, json, collections
doc = fitz.open('copy.pdf'); out = []
for pno in range(len(doc)):
    for b in doc[pno].get_text("dict")["blocks"]:
        if b["type"] != 0: continue
        for l in b["lines"]:
            for s in l["spans"]:
                if s["font"] == HEADING_FONT and s["text"].strip():
                    out.append({"page": pno+1, "y": round(s["bbox"][1]),
                                "size": round(s["size"],2), "text": s["text"]})
```

**(b) Build a page-context level map.** Decide each heading element's true
level from its tag + page context (e.g., on title page → H1; chapter-divider
pages → H1; 10pt article titles → H2; subsection size → H3; ToC pages → P).
Then walk the tree and rewrite `/S`:

```python
e.S = pikepdf.Name("/" + new_level)   # e.g. "/H2"
```

**(c) Sanity-check the result:** re-run the checker; `heading_issues` must be
empty and the census should match what you counted visually.

ToC entries demoted to P is an acceptable interim state; the strictly correct
structure (TOC/TOCI re-parenting) is harder and rarely the blocking issue.

**(d) Style-name recovery — tagged-but-flat InDesign exports.** The most
common modern failure: InDesign exports WITH tagging, but no Export Tags
were set on the paragraph styles, so the RoleMap maps every style —
headings included — to `/P`. "Tagged: yes, 2,400 tags" and still zero
headings. The original style names survive as the elements' `/S` types
(`Hed_base_v01`, `stephed_2`…), which beats font-signature recovery: dump
the RoleMap first (`StructTreeRoot/RoleMap`) — if all values are `/P`,
this is your case.

Two hard-won rules:

- **Classify per ELEMENT, not per style.** Designers reuse styles across
  semantics: one document's `Byline` style held 11pt bylines AND 18pt
  article titles; its `stephed_3` held 13pt subheads AND 7pt name labels;
  one "lead-in head" element contained its entire body paragraph (promote
  that and a whole paragraph becomes a heading). So: walk the tree, take
  each candidate element's text + max char size (via an independent
  parser keyed on MCIDs), then decide level from style + size + page
  context — and rewrite `/S` per element. A pure RoleMap remap is almost
  always wrong.
- **Resolve element→page via the ParentTree**, not `/Pg` — InDesign
  elements and their anonymous MCID-holder children typically carry no
  `/Pg`. Each page's `/StructParents` indexes a ParentTree array whose
  entries point at the MCID-owning elements; build objgen→page from that.

Verify with the checker: `heading_issues` empty, census matches the page
renders. Then Pattern 3 bookmarks come free from the recovered headings.

## Pattern 7: Layered display type (doubled text) — ActualText + merges

Chromatic/layered display fonts (Bungee and kin) paint each word two or
three times in stacked layers for the color effect; AT reads "HITTING
HITTING OUR OUR STRIDE STRIDE". Headlines are also often split across
sibling elements per text frame — sometimes in the wrong reading order
("BY SPARK!" element before "POWERED"). Both repair tag-tree-only:

1. **Detect:** extract each heading element's per-MCID text; layered text
   shows runs or tokens duplicated consecutively. Dedupe = collapse
   consecutive equal runs, then consecutive equal tokens; PRINT the
   before/after table and read it — don't trust the dedupe blind.
2. **Set `/ActualText`** (the deduped string) on the heading element. AT
   reads the replacement for the whole subtree; rendering untouched.
3. **Merge split headlines:** move the later elements' kids into the
   first element (set each moved kid dict's `/P`; for bare-integer MCID
   kids, repoint that page's ParentTree entry), delete the emptied
   element from its parent's `/K`, and set the merged element's
   ActualText to the full headline in VISUAL order — this also fixes the
   wrong-order reading. Re-derive the heading level from the merged
   group's max size (a 21pt fragment may belong to a 76pt headline).

The same ActualText fix applies to character-interleaved cover text
("AA yyeeaarr") — set ActualText from the visual reading.

## Pattern 8: Link descriptions (Matterhorn 28-004 / WCAG 2.4.4)

Link annotations from InDesign ship without `/Contents`. Set it from the
anchor text painted under the annotation: sample chars whose midpoints
fall inside the annot `/Rect` (normalize the rect — corner order is not
guaranteed; mind y-up vs top-down between parsers), join in reading
order; fall back to a humanized URI for image links. Mirror the same
string onto the owning Link struct element's `/Alt` by following its
OBJR kid back to the annotation (some AT reads Alt INSTEAD of the anchor
text, so only ever set Alt equal to it — a "better" Alt loses content).
Line-wrapped links are separate annotations per line; per-fragment
descriptions are an acceptable residual.

## Pattern 3: Bookmark generation

From the recovered heading list (pattern 2a), build a hierarchical outline:

```python
def dest(page1):
    return pikepdf.Array([pdf.pages[page1-1].obj, pikepdf.Name("/Fit")])
def item(title, page1):
    oi = pikepdf.OutlineItem(title); oi.destination = dest(page1); return oi

with pdf.open_outline() as outline:
    ch = item("Chapter 1 ...", 25)
    ch.children.append(item("Article title ...", 29))
    outline.root.append(ch)
```

Join multi-line titles (consecutive same-size spans on one page), strip
decorative fragments (very short text, absurd sizes). Verify with
`fitz.open(f).get_toc()` — titles and pages should match the visual ToC.

**(b) From a printed ToC — prefer this when the document has one.** If the
document prints its own Contents pages, parse THEM instead of recovering
headings by font signature: extract the ToC pages' text (pymupdf), walk the
lines with a small state machine (entry number → title lines → page number),
and validate hard before building the outline — entry numbers must form the
expected sequence (e.g. 1–56 exactly once each), and every target page must
exist. Proven on a 96-page handbook: 61 bookmarks, deterministic, zero
classification judgment. The printed ToC is the author's own structure
declaration; when present it beats font-signature recovery on both fidelity
and effort. Map printed page numbers to physical pages by locating the page
number text on a few pages (constant offset in practice).

## Pattern 4: Figure alt text (LLM-in-the-loop)

1. `scripts/render_figures.py copy.pdf` → PNGs + manifest keyed by
   **(page, full bbox)**. Key on the FULL bbox — pages can hold multiple
   figures and partial keys cause cross-matching (a real bug we hit).
2. View each PNG. Read caption text near the figure
   (`doc[p].get_text("text")`, look for "Figure N." lines).
3. Write alt text: caption + structure of the graphic + actual data values
   where legible off charts. For diagrams, enumerate nodes/relationships.
4. Degenerate bboxes (<2pt) are tagging artifacts; full-page boxes are often
   decorative — render to confirm, then empty alt / Artifact role.
5. Inject, matching on (page, bbox):

```python
e.Alt = pikepdf.String(alt_text)
```

Idempotency: skip elements that already have `/Alt` so the loop can resume
across sessions.

**Scaling the loop (proven at 101 figures in one session):**

- **Triage on contact sheets** — composite the rendered figures ~12-up
  (PIL) and review sheets, not files; one pass separates decorative
  strips/panels from content photos and drafts most scene descriptions.
  Use bbox dimensions to pre-sort: sub-2pt = degenerate, long-thin =
  likely divider, small squares = likely headshots.
- **Bind names by re-rendering WITH the caption zone.** For headshots,
  re-render each bbox expanded ~40pt toward the adjacent name label and
  read the name off the crop itself — never assign names from tree order
  or guesswork. Pull quote attributions ("— NAME (CDS'24)") from page
  text to verify spellings.
- **Decorative graphics:** true artifacting requires content-stream
  changes (off-limits outside Pattern 6). Tag-tree-only options: empty
  `/Alt` (some checkers flag it) or a minimal "Decorative divider" alt
  (mild AT verbosity). Pick one, list it as a residual, and recommend
  artifacting at source on re-export.

## Pattern 5: Table TH scope

For each Table element: TH cells in the first TR → Column scope; TH first-in-
row thereafter → Row scope. Render 2-3 tables first to confirm the layout
assumption holds.

```python
e.A = pikepdf.Dictionary(O=pikepdf.Name("/Table"), Scope=pikepdf.Name("/Column"))
# if /A already exists as a dict, convert to array and append; preserve BBox attrs
```

## Pattern 6: From-scratch tag construction (untagged legacy PDFs)

Last resort, by explicit request only, and Tier-3/central-team work — an
engineering task with per-design judgment, not a button. Use when: untagged,
source lost, HTML re-publication ruled out, content valuable enough to
justify a project. Proven END-TO-END on a complete 96-page QuarkXPress
7.31 handbook — every page tagged, 2,605 MCIDs, gate PASS — including
tables, rotated worksheets, TOC/TOCI, questionnaire and flow-diagram pages.
**For whole-document work read `combined-tagger-guide.md` first** — it holds
the architecture (census → handler registry → PagePlan → gate) that scaled;
the method below is the per-page mechanics.

This is the ONE pattern that writes into content streams. The boundary: only
non-painting marked-content operators (`BDC`/`EMC`) are inserted, only at
instruction boundaries; text bytes and painting operators are never touched.
The verify_tags.py gate (pixel hash + read-back + text coverage) is what
makes this safe.

**Method, in order:**

1. **Parse each page's content stream** (`pikepdf.parse_content_stream`) and
   recover lines. `Tm` establishes a baseline and the scale
   (`hypot(a, b)`); `Td`/`TD` with dy≠0 starts a new line. **Critical
   gotcha: Td operands are TEXT-SPACE and must be multiplied by the Tm
   scale** — unscaled, paragraph gaps collapse (~13× in practice) and all
   elements merge. This bug is invisible visually and was caught only by the
   MCID read-back verifier. Track per-line: first/last op index, font,
   effective size, x/y, and text runs with their x-shifts.
   Legacy producers often paint a whole page body in a single BT…ET object,
   so whole-block wrapping is impossible — insertion must happen *inside*
   the text object. **Assert on operators you didn't handle** (`T*`, `TL`,
   `'`, `"`): fail loudly rather than mis-tag; extend the parser per
   producer vocabulary.

2. **Classify lines by font signature** — per design family, not generic:
   distinct bold sizes → H1/H2; body faces → P; running header/footer fonts
   and y-bands → Artifact (`/Pagination`). Same approach as Pattern 2a, but
   EVERY line must be classified, not just headings. Render pages and look;
   confirm the signature table before tagging.

3. **Recover real list semantics.** A bullet line = single-glyph first run
   followed by a large (>~6pt, scaled) x-shift → `L > LI > Lbl("•") /
   LBody`, with continuation lines folded into the LBody. Verify
   character-exact on a list-heavy page.

4. **Inject marked content.** For each element, insert
   `BDC /Tag <</MCID n>>` before its first op and `EMC` after its last, at
   instruction boundaries only; emit closes before opens when both land at
   one position. Artifacts get `BDC /Artifact <</Type /Pagination>>`.
   Rebuild with `unparse_content_stream`.

5. **Build the structure tree:** StructTreeRoot → Document → elements in
   stream order; per-page `/StructParents` plus a ParentTree entry mapping
   each MCID to its element; consecutive LIs share an L parent; set
   `MarkInfo.Marked = true`.

6. **Verify (mandatory, both gates):** `verify_tags.py` full run — pixel
   hash on ALL pages (BDC/EMC are non-painting; any diff means you touched
   something you shouldn't have) and MCID read-back with `--dump`, read by a
   human. Then `pdf_a11y_check.py`: census sane, `heading_issues` empty.

**Do not ship partial passes.** A tag tree covering some pages implies
structure the rest lacks; a spike-tagged file is evidence, not a
deliverable. Ship complete documents only (Pattern 1 + bookmarks remain
shippable interim improvements).

**Rotated pages and tables (proven on a worksheet page):** rotation lives in
the Tm matrix (`(0,s,-s,0)` = 90°) — the text-space line model is unchanged;
track a per-line ROW coordinate (user-space position perpendicular to the
writing direction) to separate page bands (title/form/table/summary). For
tables: words often arrive as Td-shifted runs (6–17pt gaps), so **gap size
cannot find cells** — find the per-design column anchor x-positions (exact
across rows) and split cells where runs LAND on a higher anchor; off-anchor
runs join the current cell. Bold single-cell rows → TH Scope=Row (group
labels); bold multi-anchor row → header TR with TH Scope=Column. Symbol-font
glyphs (checkmarks etc.) without ToUnicode read back as `(cid:N)` and are
silent to AT — add `/ActualText` to those elements.

**Table regularity + headers (Acrobat's two table checks — fix at emission
time):** tagging only the cells where text landed fails Acrobat's
Regularity rule on every sparse row (blank form boxes, empty yes/no
columns, a header row missing its corner stub). Emit the FULL grid per TR:
group-label THs get `/ColSpan` spanning to the next occupied column; every
other blank grid position gets an empty TD (empty TH Scope=Column in
header rows). Empty cells carry no MCIDs, so the verification gate is
unaffected by construction — audit them separately with a struct-tree walk
(`scripts/table_audit.py`: per-row effective widths + Acrobat's header
heuristic). Tables with no visual header row: make the column that labels
each row TH Scope=Row; don't invent a header row that isn't on the page.
Full recipe in `combined-tagger-guide.md`.

**Formerly-open limits, now solved (recipes in `combined-tagger-guide.md`):**
two-column layouts with baseline-sharing cells → reuse the table handler in
horizontal orientation (real tables) or H1 + one Figure with prose Alt (flow
diagrams; arrows stay Artifact, meaning carried by the Alt); TOC/TOCI →
dedicated handler (entry number = Lbl, title+page = P, wrapped titles join
the open entry); questionnaire pages ride the linear handler with config.
Bookmarks can be generated from the printed ToC (decode via ToUnicode-aware
extractor, never raw content-stream bytes; verify printed→PDF page mapping).
Cost shape: line recovery, injection, and tree plumbing generalize; the
classifier and column anchors are per-design configuration. First document
of a template family is a project; siblings inherit nearly all of it.

## Verification (after every pass — not optional)

```bash
python scripts/verify_tags.py original.pdf copy.pdf          # full gate
python scripts/verify_tags.py original.pdf copy.pdf --dump   # + MCID→text listing
```

Three independent proofs in one run, exit 1 = do not ship:

1. **Visual identity** — pixel-hash of every page (or `--pages`) at the same
   dpi, original vs modified. Any tagging approach must leave rendering
   untouched.
2. **MCID read-back** — pdfplumber (a parser that did not write the tags)
   recovers the text actually painted under every MCID the structure tree
   claims. Catches struct elements pointing at MCIDs absent from the stream,
   stream MCIDs no element claims, and elements that wrap no text (this is
   the check that caught the Td text-space scaling bug). Use `--dump` and
   read the listing yourself: types and text should match what the rendered
   page shows.
3. **Text coverage** — every painted character on a tagged page must sit
   inside an MCID or an explicit Artifact; per-page artifact counts are
   reported so silently-artifacted real content shows up as an outlier.
   Caveat: content that is *legitimately wrapped* as Artifact but shouldn't
   be (e.g. a footer band swallowing the last ToC entry) PASSES this check —
   only human `--dump` review catches that class.

Defects already present in the original appear under `pre_existing_defects`
and don't fail the gate — carry them into the honest-residuals list (e.g.
RSMIv1 ships with 19 orphan stream MCIDs that Quark produced in 2003).

Plus: re-run `scripts/pdf_a11y_check.py` and report the before/after delta in
the same table format as the audit. Close with the honest-residuals list and
the screen-reader acceptance recommendation. Machine verification proves
structure and visual identity, not experience — NVDA/VoiceOver remains the
acceptance test.

## Collection scaling

Heading-font heuristics are per-design, not per-document: documents sharing a
template (book series, report families) share signatures. Cost shape: first
document ~a full session; siblings mostly machine time plus alt-text review.
