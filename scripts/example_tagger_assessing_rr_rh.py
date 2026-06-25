#!/usr/bin/env python3
"""example_tagger_assessing_rr_rh.py — WORKED EXAMPLE, not a general tool.

The complete, gate-passing config that tagged a 96-page untagged
QuarkXPress handbook (Assessing Readiness for Rehabilitation) from scratch:
37 page-design signatures across three handlers, every page tagged, 2,605
MCIDs, Acrobat table rules satisfied. Read it as the reference for what a
real PAGE_CLASSES/CONFIGS pair looks like — the anchor coordinates, bands,
and per-design judgment calls are all specific to THIS document and will not
transfer; the SHAPE is what transfers.

The reusable machinery lives in tagger_engine.py. A new document = a new
file like this one. See references/combined-tagger-guide.md.

Run:  python3 example_tagger_assessing_rr_rh.py SRC.pdf DST.pdf [--pages 7,9] [--dump]
"""
import argparse
import json
import re
import sys

import pikepdf

from tagger_engine import cfg, wcfg, build

P74_FLOWCHART_ALT = (
    "Flowchart. Start: Process ratings with client. Then: Is the client "
    "ready for rehabilitation? If Yes: set an overall rehabilitation goal. "
    "If Unsure: set an overall rehabilitation goal and develop readiness. "
    "If No: Is the client interested in rehabilitation? If interested, Yes: "
    "either develop readiness or set an overall rehabilitation goal. If not "
    "interested, No: provide alternative services, proceed with connecting "
    "only, or disengage."
)

CONFIGS = {
    # full sheet classes (H1+H2+body, masthead): the five spike-proven roles
    "sheet": cfg(),
    # questionnaire pages: bold-13 lines are prompts ("Explain:"), not heads
    "sheet-noh2": cfg(h2=False),
    # 12.5pt body variant (pp67-69)
    "sheet-12.5": cfg(body_size=12.5, leading_max=17.5),
    # 11pt flowchart page: H1 + the whole diagram as one Figure with Alt
    "flowchart-74": cfg(body_size=11.0, h2=False, lists=False,
                        figure={"alt": P74_FLOWCHART_ALT}),
}

# page (1-based) -> config name. Missions extend this map.
PAGE_CLASSES = {}
for p in (7, 9, 12, 13, 17, 25, 27, 38, 55, 70,          # title sheets
          23, 24, 36, 37, 47, 54, 82, 88, 94,            # continuation
          8, 10, 39, 48, 56, 81,                         # + italic body
          14, 22, 35, 46, 53, 79,                        # no-H2-role titles
          28, 57, 87,                                    # cont. with H2
          11, 83, 84, 86,                                # cont. + italic
          80, 85, 90):                                   # cont. italic noH1
    PAGE_CLASSES[p] = "sheet"
for p in (89, 91, 92, 93):                # questionnaire: prompts not heads
    PAGE_CLASSES[p] = "sheet-noh2"
PAGE_CLASSES[26] = "sheet-noh2"           # BoldItalic lead-ins are Ps
for p in (67, 68, 69):
    PAGE_CLASSES[p] = "sheet-12.5"
PAGE_CLASSES[74] = "flowchart-74"

# Document-level quick wins, re-applied on every build (see end of build()).
DOC_TITLE = "Assessing Readiness for Rehabilitation: Reference Handbook"
DOC_LANG = "en-US"

# --------------------------------------------------------------------------
# Mission 2 worksheet designs. Anchors/bands are ABSOLUTE user-space values
# verified per page against rendered output (see MISSION2 report).
# --------------------------------------------------------------------------

# Inferring Need (3-col: Characteristics | Feelings | Reasons), spike design
CONFIGS["ws-inferring-ex"] = wcfg(anchors=(59.0, 235.5, 319.0),
                                  table_band=(170, 490))            # p15
CONFIGS["ws-inferring-pr"] = wcfg(anchors=(59.0, 235.5, 319.0),
                                  table_band=(210, 470))            # p16
# Others' Perspectives (3-col; a TR per feeling line: trigger cols 0 and 1)
CONFIGS["ws-perspect-ex"] = wcfg(anchors=(56.0, 231.0, 315.0),
                                 trigger=(0, 1), table_band=(215, 425))
CONFIGS["ws-perspect-pr"] = wcfg(anchors=(57.0, 231.0, 315.0),
                                 trigger=(0, 1), table_band=(295, 510))


def _lvl(a0, a1, lo, hi):
    """Level-rating design: bold level label TH(Row) | descriptions TD."""
    return wcfg(anchors=(a0, a1), table_band=(lo, hi),
                header=False, trigger_bold=True)


CONFIGS["ws-level-20"] = _lvl(72, 206, 210, 495)   # rating need EX
CONFIGS["ws-level-21"] = _lvl(72, 207, 235, 505)   # rating need PR
CONFIGS["ws-level-33"] = _lvl(66, 198, 210, 420)   # rating commitment EX
CONFIGS["ws-level-34"] = _lvl(64, 196, 210, 425)   # rating commitment PR
CONFIGS["ws-level-44"] = _lvl(66, 223, 210, 400)   # rating env awareness EX
CONFIGS["ws-level-45"] = _lvl(64, 222, 195, 460)   # rating env awareness PR
CONFIGS["ws-level-51"] = _lvl(65, 225, 195, 420)   # rating self-awareness EX
CONFIGS["ws-level-52"] = _lvl(65, 226, 205, 480)   # rating self-awareness PR
CONFIGS["ws-level-65"] = _lvl(64, 237, 195, 440)   # rating pers closeness EX
CONFIGS["ws-level-66"] = _lvl(70, 243, 200, 440)   # rating pers closeness PR

# Commitment 4-col (Area | Item bullets | Yes/No/Partial | Comments).
# Bullets stay plain TD text (no L-in-TD; logged residual). The Y/N/P group
# is three columns so each ✗ Span lands in its own TD. p29 sits 3pt left of
# pp30-32 (separate Quark master). Wrapped bold summary labels re-trigger
# one leading (12pt) below their first line -> trigger_gap=14.
def _commit(d, lo, hi):
    return wcfg(anchors=(51.0 + d, 176.0 + d, 353.0 + d, 377.0 + d,
                         395.0 + d, 439.0 + d),
                header_anchors=(51.0 + d, 185.0 + d, 353.0 + d, 377.0 + d,
                                395.0 + d, 439.0 + d),
                trigger=(0, 1), trigger_gap=14.0, min_cell_gap=10.0,
                table_band=(lo, hi))


CONFIGS["ws-commit-29"] = _commit(0, 140, 440)
CONFIGS["ws-commit-30"] = _commit(3, 140, 460)
CONFIGS["ws-commit-31"] = _commit(3, 170, 460)
CONFIGS["ws-commit-32"] = _commit(3, 140, 490)
for _p in (29, 30, 31, 32):
    PAGE_CLASSES[_p] = f"ws-commit-{_p}"

# Environmental resume 6-col (Name/Type | Physical Description | Role |
# Requirements | Dates | Method of Choosing). p41/p43 continuation pages
# carry only the first 4 columns. p42 (practice) has no col-0 landings —
# the only band text is the Method option lines at wx 595 (groups of three,
# one per ruled row) -> trigger on col 5; its header "Method of" sits at 584,
# hence header_anchors. p43 is a header-only blank grid.
CONFIGS["ws-envres-40"] = wcfg(anchors=(55.0, 171.0, 326.0, 380.0, 542.0,
                                        585.0),
                               trigger_gap=14.0, min_cell_gap=10.0,
                               table_band=(145, 450))
CONFIGS["ws-envres-41"] = wcfg(anchors=(56.0, 172.0, 325.0, 389.0),
                               trigger_gap=14.0, min_cell_gap=10.0,
                               table_band=(110, 260))
CONFIGS["ws-envres-42"] = wcfg(anchors=(54.0, 170.0, 325.0, 379.0, 524.0,
                                        595.0),
                               header_anchors=(54.0, 170.0, 325.0, 379.0,
                                               524.0, 584.0),
                               trigger=(5,), trigger_gap=14.0,
                               min_cell_gap=10.0, table_band=(180, 510))
CONFIGS["ws-envres-43"] = wcfg(anchors=(54.0, 170.0, 323.0, 387.0),
                               trigger_gap=14.0, min_cell_gap=10.0,
                               table_band=(120, 160))
for _p in (40, 41, 42, 43):
    PAGE_CLASSES[_p] = f"ws-envres-{_p}"

# Self-awareness estimating 3-col (Categories | Item | Comments). Item
# header sits at 241, item data lines (blank-prefixed) at 252. Bold
# category labels (Interests/Values/Personal Preferences) open the TRs;
# the client's own entries share the category cell (italic lines at 57)
# and join its TH — logged residual, faithful to the drawn grid.
def _selfaw(lo, hi):
    return wcfg(anchors=(57.0, 252.0, 436.0),
                header_anchors=(57.0, 241.0, 436.0),
                trigger_gap=14.0, min_cell_gap=10.0, table_band=(lo, hi))


CONFIGS["ws-selfaw-49"] = _selfaw(170, 410)
CONFIGS["ws-selfaw-50"] = _selfaw(175, 450)
PAGE_CLASSES[49] = "ws-selfaw-49"
PAGE_CLASSES[50] = "ws-selfaw-50"

# Closeness discriminating (Type of Personal Closeness | Yes | No |
# Partial | Conclusions/Comments). One item per 14pt line; ✗ checks sit in
# the three Y/N/P sub-columns; conclusions option lines (wx 426+) snap into
# the comments column. p59/60/62/63 carry TWO ruled tables (conf["tables"]);
# p60/63 second headers have no col-0 run ("Feelings Toward Practitioner").
def _close(a0, dx, bands):
    c = wcfg(anchors=(a0, 312.0 + dx, 334.0 + dx, 356.0 + dx, 415.0 + dx),
             min_cell_gap=10.0, table_band=bands[0])
    if len(bands) > 1:
        c["tables"] = [{"table_band": b} for b in bands]
    return c


CONFIGS["ws-close-58"] = _close(55.0, 1.0, [(180, 400)])
CONFIGS["ws-close-59"] = _close(54.0, 0.0, [(160, 325), (340, 495)])
CONFIGS["ws-close-60"] = _close(52.0, -2.0, [(160, 310), (345, 465)])
CONFIGS["ws-close-61"] = _close(54.0, 0.0, [(280, 495)])
CONFIGS["ws-close-62"] = _close(54.0, 0.0, [(160, 300), (330, 465)])
CONFIGS["ws-close-63"] = _close(54.0, 0.0, [(160, 290), (320, 430)])
for _p in (58, 59, 60, 61, 62, 63):
    PAGE_CLASSES[_p] = f"ws-close-{_p}"

# Categories reference 5-col (Connecting Styles | Prefers | Like |
# Tolerates | Avoids Closeness): explanation grid, no form band, bold row
# labels (Physical/Emotional/...) -> TH Scope=Row via bold_cell_th.
CONFIGS["ws-categories-64"] = wcfg(anchors=(53.0, 149.0, 285.0, 397.0,
                                            534.0),
                                   form_wx=None, bold_cell_th=True,
                                   min_cell_gap=10.0, table_band=(130, 365))
PAGE_CLASSES[64] = "ws-categories-64"

# Profiling readiness (71 EX / 73 PR): readiness line graph (gray band,
# rating leaders 5..1, plotted line + dots on 71) -> ONE Figure with Alt +
# scale labels (High/5/4/3/2/1/Low); then the conclusions table: category
# header TR + a single "Conclusions:" group row whose per-category cells
# hold the Ready/Unsure/Not Ready options (✗ Spans) and comment sentences.
# p71 dot/leader geometry decoded from the painting (dots at ratings
# 4,3,3,2,4); the ✗Unsure under Self-Awareness corroborates the 2.
P71_GRAPH_ALT = (
    "Line graph plotting the client's readiness ratings on a scale from "
    "1 (Low) to 5 (High). Need: 4. Commitment to Change: 3. Personal "
    "Closeness: 3. Self-Awareness: 2. Environmental Awareness: 4. A line "
    "connects the five plotted points."
)
P73_GRAPH_ALT = (
    "Blank readiness line graph for plotting your own ratings on a scale "
    "from 1 (Low) to 5 (High) for each readiness indicator: Need, "
    "Commitment to Change, Personal Closeness, Self-Awareness, and "
    "Environmental Awareness."
)
CONFIGS["ws-profile-71"] = wcfg(
    anchors=(53.0, 137.0, 233.0, 330.0, 443.0, 549.0),
    header_anchors=(53.0, 138.0, 200.0, 328.0, 432.0, 517.0),
    min_cell_gap=10.0, table_band=(150, 395),
    paint_figure={"alt": P71_GRAPH_ALT, "rows": (145, 270),
                  "wx": (40, 660), "text_rows": (170, 260)})
CONFIGS["ws-profile-73"] = wcfg(
    anchors=(52.0, 146.0, 256.0, 367.0, 478.0, 577.0),
    header_anchors=(52.0, 137.0, 200.0, 328.0, 432.0, 517.0),
    min_cell_gap=10.0, table_band=(235, 410),
    paint_figure={"alt": P73_GRAPH_ALT, "rows": (240, 350),
                  "wx": (40, 660), "text_rows": (255, 345)})
PAGE_CLASSES[71] = "ws-profile-71"
PAGE_CLASSES[73] = "ws-profile-73"

# Strategy selection 2-col (Strategy to Proceed | Future Activities):
# blank-prefixed strategy options (wx 65) map to future-activity option
# groups (wx 443); ✗ checks at 57/435. Ruled blocks 19pt apart, wrapped
# second strategy lines 12pt -> trigger_gap=14. Headers sit 11pt left of
# the option columns -> header_anchors.
def _strategy(lo, hi):
    return wcfg(anchors=(65.0, 443.0), header_anchors=(54.0, 432.0),
                trigger_gap=14.0, min_cell_gap=10.0, table_band=(lo, hi))


CONFIGS["ws-strategy-75"] = _strategy(150, 415)
CONFIGS["ws-strategy-78"] = _strategy(200, 465)
PAGE_CLASSES[75] = "ws-strategy-75"
PAGE_CLASSES[78] = "ws-strategy-78"

# Horizontal arrow pages (72: conclusion guidelines, 76/77: goal-activity
# suggestions): condition text ➔ result, one ruled block per mapping.
# orientation "H" (rrow = -y), masthead/footer handled by the sheet
# artifact rules. The ➔ (14pt ZapfDingbats byte 3) gets its own middle
# anchor so the Span lands in its own TD between condition and result.
# Within-block leading is 14pt, between blocks 28 -> trigger_gap=20.
def _arrow(anchors, lo, hi, header=True):
    return wcfg(orientation="H", anchors=anchors, header=header,
                trigger_gap=20.0, min_cell_gap=10.0, form_wx=None,
                table_band=(lo, hi))


CONFIGS["ws-arrow-72"] = _arrow((72.0, 417.0, 444.0), -590, -320,
                                header=False)
CONFIGS["ws-arrow-72"]["row_th"] = True   # no visual header row: the
                                          # condition cell is the row header
CONFIGS["ws-arrow-76"] = _arrow((72.0, 274.0, 306.0), -570, -360)
CONFIGS["ws-arrow-77"] = _arrow((71.0, 273.0, 305.0), -590, -320)
PAGE_CLASSES[72] = "ws-arrow-72"
PAGE_CLASSES[76] = "ws-arrow-76"
PAGE_CLASSES[77] = "ws-arrow-77"

PAGE_CLASSES[15] = "ws-inferring-ex"
PAGE_CLASSES[16] = "ws-inferring-pr"
PAGE_CLASSES[18] = "ws-perspect-ex"
PAGE_CLASSES[19] = "ws-perspect-pr"
for _p in (20, 21, 33, 34, 44, 45, 51, 52, 65, 66):
    PAGE_CLASSES[_p] = f"ws-level-{_p}"

# --------------------------------------------------------------------------
# Mission 3: front matter (1-4), ToC (5-6), near-empty NOTES (95-96), and
# the pp9/12 same-baseline column-head residual fix.
# --------------------------------------------------------------------------

# p1 cover: three 20pt title banner blocks (series / type / title) on
# 21-22pt leading -> one H1 each (h1_stack_max raised; blocks are separate
# BTs so they never merge). Authors 16.8pt apart -> leading_max 16 keeps one
# P per author. Publisher block sits at y 37-63 -> footer_y 0 keeps it
# content (3 lines, one P). Bar art + logo painting -> Artifact.
CONFIGS["cover-1"] = cfg(h2=False, lists=False, footer_y=0.0,
                         leading_max=16.0, h1_stack_max=23.0)
PAGE_CLASSES[1] = "cover-1"

# p2 copyright: 11pt body paragraphs; masthead_band already artifacts the
# four bold 11/11.5 masthead blocks; lists off so the (c) glyph never
# parses as a bullet.
CONFIGS["copyright-2"] = cfg(body_size=11.0, h2=False, lists=False)
PAGE_CLASSES[2] = "copyright-2"

# p3 dedication: italic 11pt lines, top rule -> painting Artifact.
CONFIGS["dedication-3"] = cfg(body_size=11.0, h2=False, lists=False)
PAGE_CLASSES[3] = "dedication-3"

# p4 acknowledgments: no masthead/running header on this page, so disable
# those artifact rules and let the bold-11 "ACKNOWLEDGMENTS" be the H1.
CONFIGS["acknowledgments-4"] = cfg(body_size=11.0, h2=False, lists=False,
                                   h1_min_size=10.5, masthead_band=(0.0, 0.0),
                                   artifact_bold_sizes=())
PAGE_CLASSES[4] = "acknowledgments-4"

# pp5-6 ToC: TOC/TOCI handler. Entry numbers are right-aligned at wx 78-85
# (p5) / 84-90 (p6); titles start at 105/111 -> num_xmax 100 separates
# entry-start lines from wrapped-title continuations. No footer on these
# pages and the LAST entry sits at y=44 -> footer_y 0 (p5 entry 26 was
# silently artifacted by the default 50 — caught in dump review).
CONFIGS["toc"] = cfg(handler="toc", toc_num_xmax=100.0, footer_y=0.0)
PAGE_CLASSES[5] = "toc"
PAGE_CLASSES[6] = "toc"

# pp95-96 NOTES: bold-17 head -> H1 via the plain sheet config; the ruled
# note lines are inter-BT painting -> Artifact.
PAGE_CLASSES[95] = "sheet"
PAGE_CLASSES[96] = "sheet"

# p9 residual fix: "Summary of the Psychiatric Rehabilitation Process" is a
# three-column flow DIAGRAM (boxes + double-headed arrows); linear sheet
# tagging interleaved cells sharing a baseline mid-sentence. p74 precedent:
# H1 + ONE Figure over all body text with prose Alt; arrows stay Artifact,
# their meaning carried by the Alt.
P9_PROCESS_ALT = (
    "Diagram of the psychiatric rehabilitation process in three columns: "
    "Diagnosis, Planning, and Interventions. Diagnosis column, connected "
    "top to bottom by double-headed arrows: Setting an Overall "
    "Rehabilitation Goal; Conducting a Functional Assessment; Conducting a "
    "Resource Assessment. Conducting a Functional Assessment connects "
    "across to Planning for Skill Development, which connects to Skill "
    "Development Interventions (Direct Skills Teaching, Skills "
    "Programming). Conducting a Resource Assessment connects across to "
    "Planning for Resource Development, which connects to Resource "
    "Development Interventions (Resource Coordination and Resource "
    "Modification, both Case Management)."
)
CONFIGS["process-9"] = cfg(h2=False, lists=False,
                           figure={"alt": P9_PROCESS_ALT})
PAGE_CLASSES[9] = "process-9"

# p12 residual fix: "How to Infer Need" is a real 2-col table (Behaviors |
# Steps) whose cells share baselines -> worksheet handler, orientation H.
# Wrapped step text indents to wx 287/323 -> tol 15 snaps 287 to the Steps
# anchor (273.5) while 323 off-anchor-joins the open cell. Behaviors-cell
# wraps land on the trigger anchor 14pt below -> trigger_gap 16.
CONFIGS["ws-howto-12"] = wcfg(orientation="H", anchors=(71.0, 273.5),
                              tol=15.0, trigger_gap=16.0, min_cell_gap=10.0,
                              form_wx=None, table_band=(-560, -160))
PAGE_CLASSES[12] = "ws-howto-12"

# --------------------------------------------------------------------------
# Bookmarks (Mission 4): generate the document outline from the ToC pages.
# The content-stream fonts carry a custom Quark encoding, so entry text is
# recovered via pymupdf (ToUnicode CMaps) — same decode path the coverage
# gate trusts. Printed page numbers match PDF page indices 1:1 (verified
# against footers on pp7/9/15/50). Parser mirrors plan_toc_page's geometry:
# entry-start = integer ref at x<100; right-aligned integer at x>430 = the
# target page; other rows are wrapped-title continuations. Outline writing
# is non-painting, so the visual-identity gate is unaffected.
# --------------------------------------------------------------------------

TOC_PAGES = (5, 6)


def toc_outline_entries(src):
    """Parse the ToC -> [(title, 1-based target page)]; raises if any
    entry lacks a target or refs aren't sequential (parser drift guard)."""
    import fitz
    doc = fitz.open(src)
    entries, open_e = [], None
    for pno in TOC_PAGES:
        rows = {}
        for x0, y0, x1, y1, w, *_ in doc[pno - 1].get_text("words"):
            rows.setdefault(round(y0), []).append((x0, w))
        for y in sorted(rows):
            ws = sorted(rows[y])
            if " ".join(w for _, w in ws) in ("CONTENTS", "References",
                                              "Page", "References Page"):
                continue
            pgno = None
            if re.fullmatch(r"\d+", ws[-1][1]) and ws[-1][0] > 430:
                pgno = int(ws[-1][1])
                ws = ws[:-1]
            if ws and re.fullmatch(r"\d+", ws[0][1]) and ws[0][0] < 100:
                open_e = {"ref": int(ws[0][1]), "page": pgno,
                          "title": " ".join(w for _, w in ws[1:])}
                entries.append(open_e)
            elif ws and open_e is not None:
                open_e["title"] += " " + " ".join(w for _, w in ws)
                if pgno is not None:
                    open_e["page"] = pgno
    assert all(e["page"] for e in entries), "ToC entry without target page"
    assert [e["ref"] for e in entries] == list(range(1, len(entries) + 1)), \
        "ToC refs not sequential — parser drift"
    return [(f"{e['ref']}. {e['title']}", e["page"]) for e in entries]


def add_bookmarks(pdf, src):
    items = [("Contents", TOC_PAGES[0])] + toc_outline_entries(src)
    with pdf.open_outline() as outline:
        for title, pgno in items:
            outline.root.append(pikepdf.OutlineItem(title, pgno - 1))
    return len(items)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--pages", help="subset, e.g. 7,9,26 (default: all configured)")
    ap.add_argument("--dump", action="store_true",
                    help="print per-page element plan for human review")
    args = ap.parse_args()
    only = ({int(p) for p in args.pages.split(",")} if args.pages else None)
    summary = build(args.src, args.dst, PAGE_CLASSES, CONFIGS,
                    doc_title=DOC_TITLE, doc_lang=DOC_LANG,
                    bookmarks_fn=add_bookmarks, only_pages=only, dump=args.dump)
    totals = {}
    for cen in summary.values():
        for k, v in cen.items():
            totals[k] = totals.get(k, 0) + v
    print(json.dumps({"pages_tagged": len(summary), "totals": totals,
                      "per_page": summary}, indent=1))


if __name__ == "__main__":
    main()
