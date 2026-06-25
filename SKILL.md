---
name: pdf-accessibility
description: >-
  Audit and remediate PDF files for accessibility (WCAG 2.1 AA, PDF/UA,
  Matterhorn Protocol). Use this skill whenever the user asks about PDF
  accessibility, wants to check whether a PDF is accessible or "508/ADA
  compliant", mentions screen readers and PDFs, asks why a PDF fails an
  accessibility checker, wants alt text added to a PDF, or wants a PDF
  remediated, fixed, or tagged. Also use it for triage questions like "which
  of these PDFs are worst?" or "is this document worth fixing?" — even if the
  user doesn't say "accessibility" but mentions tagging, reading order,
  bookmarks, or document structure in a compliance context.
---

# PDF Accessibility Triage and Remediation

This skill turns Claude into a PDF accessibility auditor and remediation
assistant using free, open tooling. It encodes a methodology proven on real
university documents, including the structural repair of a 512-page legacy
book.

## The one rule that governs everything

**Deterministic code for detection; LLM judgment only where meaning enters.**

You cannot assess a PDF's accessibility by reading its extracted text — tag
trees, reading order, alt text, and table semantics are invisible in text
extraction. Never offer an opinion on a PDF's accessibility without running
the checker first. A hallucinated "looks fine" is worse than no answer: people
make legal compliance decisions based on these reports.

Where your judgment IS the tool: writing alt text from rendered images,
classifying recovered headings, assessing whether reading order makes sense,
explaining findings to a non-technical author. Use rendering (PDF → PNG →
look at it) to ground every judgment in what the document actually shows.

## Setup

Requires Python with `pikepdf`, `pymupdf`, and `pdfplumber` (the verifier
needs pdfplumber ≥ 0.10 as an independent second parser):

```bash
pip install pikepdf pymupdf pdfplumber --break-system-packages
```

## Workflow

### 1. Audit (always the first step)

Run the bundled checker on every PDF in scope:

```bash
python scripts/pdf_a11y_check.py file1.pdf file2.pdf ...
```

It emits JSON covering the core Matterhorn/PDF-UA checkpoints: tagging
presence, tag-type census, heading hierarchy issues, figure alt text, table
TH scope, document language, title + DisplayDocTitle, bookmarks,
scanned-vs-real text, fonts, and PDF/UA identifier.

Interpretation guidance, the WCAG criterion behind each check, and severity
ranking: read `references/audit-interpretation.md`.

**Trust trap:** "Tagged: yes" means nothing by itself. A document can carry
thousands of tags that are semantically wrong (e.g., every heading tagged H6,
zero alt text, no language). Always look at the tag census and heading
analysis, not just the tagged flag.

### 2. Triage — classify before recommending

Assign each document a tier. Full decision guide and what to ask the user:
`references/audit-interpretation.md` (Triage section).

| Tier | Signature | Path |
|---|---|---|
| 1 | Tagged, structure sound, isolated defects (bad title, missing alt, no TH scope) | Author fixes in source file (Word/InDesign), re-export. Minutes of work. |
| 2 | Untagged, but text is real and source file likely exists | Re-export from source with tagging enabled. Do NOT hand-remediate the PDF. |
| 3 | Legacy: tagged-but-broken or source lost; content valuable | Programmatic tag-tree repair (this skill, on request) or HTML re-publication. A project, not a quick fix. |

The two highest-leverage questions to ask the document owner, before any
remediation talk: **"Do you still have the source file?"** and **"Does this
need to be a PDF at all, or could it be a web page?"** Accessible HTML is far
cheaper to produce and maintain than PDF/UA.

### 3. Report

Deliver findings as a report the document owner can act on. Structure every
finding as: what's wrong → which WCAG/Matterhorn criterion → who is affected
and how → concrete fix in the tool they have. Report template:
`references/audit-interpretation.md` (Reporting section).

Plain language matters: the audience is usually a communications staffer or
faculty member, not a developer. "Screen reader users cannot skip to
sections because the headings aren't marked" lands; "fails Matterhorn 14-003"
does not (cite it, but in parentheses).

### 4. Remediation (only when asked, only with verification)

If the user wants direct repair of the PDF (typically tier 3, or a tier-1
tagged-but-flat InDesign export), read `references/remediation-patterns.md`
for the proven recipes: language/title/ViewerPreferences injection, heading
re-leveling via font-signature recovery or — for tagged-but-flat InDesign
exports — per-element style-name recovery (Pattern 2d), bookmark generation
(from recovered headings, or from a printed ToC when the document has one),
LLM-in-the-loop alt text via region rendering (with contact-sheet triage and
name-label binding at scale), layered display type repair via ActualText and
element merges (Pattern 7), link descriptions from anchor-text geometry
(Pattern 8), TH scope injection, and — for untagged documents where every
other path is exhausted — from-scratch tag construction (Pattern 6). For whole-document Pattern-6
projects, `references/combined-tagger-guide.md` holds the proven scaling
architecture (page census → handler registry → per-design configs → gate),
and `scripts/tagger_engine.py` is the reusable engine that implements it —
you write a per-document config (see `scripts/example_tagger_assessing_rr_rh.py`,
a complete worked example) rather than rebuilding the parser/handlers.

Non-negotiable discipline, regardless of which patterns you apply:

1. **Work on a copy.** Never modify the user's original file.
2. **Never add, remove, or alter painting operators.** Tag-tree and metadata
   surgery is always safe. Inserting non-painting marked-content operators
   (BDC/EMC) into content streams is permitted ONLY for Pattern 6 work, and
   only because step 3's pixel-hash proof verifies rendering is untouched.
   Anything that could paint differently — text, paths, images, graphics
   state — is off limits, always. This is what makes step 3 provable.
3. **Run the verifier.** `python scripts/verify_tags.py original.pdf
   modified.pdf` proves visual identity (pixel hashes, every page), reads
   every tagged MCID's text back through an independent parser, AND checks
   text coverage (every painted char in an MCID or an Artifact). Exit 1 means
   stop and investigate — do not ship. Defects already present in the
   original are reported as `pre_existing_defects` (put them in the
   honest-residuals list); only defects your pass introduced fail the gate.
4. **Re-run the checker** before/after and report the delta. For any work
   that touches tables, also run `python scripts/table_audit.py file.pdf` —
   it checks the two Acrobat table rules (row Regularity, header heuristic)
   that the verifier is structurally blind to; exit 1 = tables will fail an
   external Acrobat Full Check (fix recipe in `combined-tagger-guide.md`).
5. **Never add a PDF/UA identifier** (`pdfuaid:part`) unless every checkpoint
   genuinely passes — claiming conformance falsely is worse than silence.
6. **State the limits.** Machine verification proves structure, not
   experience. Recommend a human screen-reader pass (NVDA/VoiceOver) as the
   acceptance test, and note any residual defects honestly.

### Figure alt text: the render-and-look loop

For figures missing alt text (audit reports them with page + bbox):

```bash
python scripts/render_figures.py document.pdf            # renders all figures lacking alt
python scripts/render_figures.py document.pdf --page 175 # or one page
```

Then **view each PNG yourself** (Read tool), read the caption text near the
figure (`page.get_text()`), and write alt text that: includes the figure
caption, describes the structure of the graphic, and reads actual data values
off charts where legible. Skip degenerate figures (bbox < 2pt — tagging
artifacts) and confirm decorative full-page boxes by rendering before marking
them decorative. Injection code: `references/remediation-patterns.md`.

## What this skill cannot do

Be upfront about these when relevant: it cannot OCR scanned documents (it
detects them and says so), cannot verify color contrast yet, cannot fix
reading order that's wrong at the content-stream level, and its checker is a
strong subset of — not a replacement for — a certified validator like veraPDF
or Acrobat's checker when formal conformance claims are needed.

## Remediation report

When you run a remediation, prepare a report in the same shape as the audit but with emphasis that there should still be human review and verification. If possible, lay out in clear approachable language how the human can check the accessibility improvements themselves, highlighting any area that you may not be as confident about.

In terms of remediation, you are not trying to replace other authoring systems for PDFs, but if you are asked to try to remediate you are motivated in the best interest of the reader and the author, to try your best to leave the PDF better than you found it. The report is part of your contribution to that goal.
