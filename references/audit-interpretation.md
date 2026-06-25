# Interpreting Audit Results, Triage, and Reporting

## Checker output → meaning → WCAG/Matterhorn mapping

| Checker field | Failing value | Means | Criterion | Severity |
|---|---|---|---|---|
| `marked_tagged` / `struct_tree_root` | false | No structure at all; document is opaque to assistive tech | WCAG 1.3.1, 4.1.2 / Matterhorn 01 | Blocker |
| `likely_scanned` | true | Image-only pages; no text exists for any reader | WCAG 1.1.1 | Blocker (needs OCR, out of scope) |
| `doc_language` | null | Screen readers can't pick pronunciation rules | WCAG 3.1.1 / Matterhorn 11 | High (trivial fix) |
| `title` | null or junk | Wrong/garbage announced as document name | WCAG 2.4.2 / Matterhorn 06 | High (trivial fix) |
| `display_doc_title` | false | Even a good title won't be shown/announced | Matterhorn 07-002 | High (trivial fix) |
| `heading_issues` | any | Navigation by heading is broken or misleading | WCAG 1.3.1, 2.4.6 / Matterhorn 14 | High |
| `headings` | empty but doc has visual headings | Same as above, worse | WCAG 1.3.1 | High |
| `figures_no_alt` / `figures_empty_alt` | > 0 | Graphics convey nothing to non-visual users | WCAG 1.1.1 / Matterhorn 13 | High |
| `th_no_scope` | > 0 | Header-cell associations are guesswork in tables | WCAG 1.3.1 / Matterhorn 15 | Medium-High |
| `bookmarks` | 0 on long docs (>20 pages) | No practical navigation in long documents | Matterhorn 02 / WCAG 2.4.5 | Medium (rises with page count) |
| `links_no_alt` | > 0 | Link purpose may be unclear out of context | WCAG 2.4.4 / Matterhorn 28 | Low-Medium (check link text first) |
| `nonstandard_unmapped_types` | non-empty | AT may not understand custom tag types | Matterhorn 09 | Medium |
| `suspects` | true | Producer flagged its own tagging as unreliable | Matterhorn 01-002 | High |
| `pdfua_id` | null | No conformance claim (informational — absence is honest, not a defect) | Matterhorn 06-002 | Info |

Severity logic: anything that makes content *unreachable* (untagged, scanned)
outranks anything that makes it *harder* (bad headings) which outranks
*polish* (link alts). Weight by document traffic and importance when ranking
across documents.

## Triage decision guide

Walk this in order; first match wins:

1. **Scanned/image-only** → needs OCR first; out of this skill's scope. Flag
   and recommend an OCR pass (or locating a text original) before anything.
2. **Untagged** (`struct_tree_root` false) → **Tier 2.** Ask: "Do you still
   have the source file (Word/InDesign/PowerPoint)?" If yes: fix at source,
   re-export with tagging on — never hand-remediate the PDF. If no: treat as
   Tier 3.
3. **Tagged with isolated defects** (sound heading hierarchy, most checks
   pass, a few specific failures) → **Tier 1.** Author-fixable in the source
   in minutes. Give per-defect instructions for their authoring tool.
4. **Tagged but semantically broken** (heading hierarchy inverted/absent,
   wholesale missing alt, no language — tags exist but lie) → **Tier 3.**
   Programmatic repair is plausible *if the visual design is consistent*
   (headings identifiable by font signature). See remediation-patterns.md.
   Also always raise: would HTML re-publication serve better?

Always also ask: **does this content need to be a PDF?** If it's informational
content (not a form, not print-destined), an accessible web page is cheaper to
make and maintain, and serves everyone better.

## Report structure

Use this template (adjust headings to audience):

```
# Accessibility Audit: <filename>
**Date / Tool:** <date>, deterministic structural analysis (Matterhorn/PDF-UA subset)
**Verdict:** <one sentence: passes / fails, tier, recommended path>

## What this means for readers
<2-3 sentences, plain language: who is blocked and how. No jargon.>

## Findings
<One block per finding, ordered by severity:>
### <N>. <Plain-language problem statement>
- **Affects:** <who and how, concretely>
- **Standard:** <WCAG x.x.x / Matterhorn NN> (parenthetical, not the headline)
- **Fix:** <concrete steps in the tool the owner actually has>

## Recommended path
<Tier + the source-file and does-it-need-to-be-a-PDF questions answered or asked>

## What was not checked
<Honest scope: contrast, reading-order judgment, alt-text quality of existing
alts, scanned-content OCR. Recommend human screen-reader pass for acceptance.>
```

Keep the "fix" lines tool-specific. Common ones:

- **Word:** File → Info → Properties → Title; Review → Check Accessibility;
  right-click image → Edit Alt Text; Table Design → Header Row checkbox;
  export via File → Save As PDF with "Document structure tags" enabled (not
  Print-to-PDF, which strips all tagging).
- **InDesign:** Object → Object Export Options → Alt Text; paragraph styles
  mapped to export tags (Paragraph Style Options → Export Tagging); File →
  Export → PDF (Print) with "Create Tagged PDF" checked; use Articles panel
  for reading order.
- **PowerPoint:** Review → Check Accessibility; alt text per image; ensure
  slide reading order in Selection Pane; export with tags as in Word.
