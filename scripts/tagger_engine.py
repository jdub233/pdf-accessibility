#!/usr/bin/env python3
"""tagger_engine.py — reusable from-scratch PDF tagging engine (Pattern 6).

Document-AGNOSTIC machinery extracted from the Assessing-RR-RH pilot:
content-stream line recovery (parse_page), the PagePlan mark/emit
intermediate representation, the three structural handlers (sheet linear
flow, worksheet banded tables, toc TOC/TOCI), the struct-element factory,
and build(). Nothing here knows about any specific document.

To tag a NEW document you write a CONFIG module: a PAGE_CLASSES map (1-based
page -> class name) and a CONFIGS dict (class name -> handler config built
with cfg()/wcfg()), then call build(src, dst, PAGE_CLASSES, CONFIGS, ...).
See example_tagger_assessing_rr_rh.py for a complete worked example, and
references/combined-tagger-guide.md for the architecture and the per-design
judgment calls. Verify every build with scripts/verify_tags.py (three-check
gate) and, for tables, scripts/table_audit.py.

Base config templates SHEET (horizontal text pages) and WORKSHEET (rotated
banded tables) carry sensible defaults; per-design configs override only
what differs. The QuarkXPress-era defaults (font/size bands) are a starting
point — confirm them against your own producer.
"""

import argparse
import json
import math
import re
import sys

import pikepdf

SHEET = {
    "handler": "sheet",
    "body_size": 13.0,     # body face size; H2 = all-bold line at this size
    "h1_min_size": 16.0,
    "h2": True,            # False: no H2 role; bold body lines become P
    "masthead_band": (11.0, 12.5),   # bold sizes -> Artifact (masthead only)
    "footer_y": 50.0,      # y <= this -> Artifact (page no, (c)-line)
    "artifact_fonts": ("StempelGaramond",),
    "artifact_bold_sizes": (9.0,),   # running header/footer face
    "leading_max": 18.0,   # same-paragraph max line gap (scaled pt)
    "lists": True,         # bullet detection (off for diagram pages)
    "figure": None,        # {"alt": str} -> all P lines join ONE Figure
    "h1_stack_max": 22.0,  # max line gap when stacking multi-line headings
                           # (cover title blocks are set on 21-22pt leading)
}


def cfg(**over):
    c = dict(SHEET)
    c.update(over)
    return c


PAINT_OPS = {"re", "f", "f*", "F", "m", "l", "c", "v", "y",
             "S", "s", "B", "b", "B*", "b*", "sh"}

# --------------------------------------------------------------------------
# Content-stream parsing (shared): recover BT objects -> lines -> runs.
# Tm sets baseline + scale (hypot(a,b)); Td/TD with dy != 0 starts a line;
# TD also sets leading; T* is a newline by leading. CRITICAL: Td/TD/TL
# operands are TEXT-SPACE and must be scaled by the Tm scale.
# --------------------------------------------------------------------------

def basefonts(page):
    return {str(k): str(v.get("/BaseFont", ""))
            for k, v in page["/Resources"]["/Font"].items()}


def parse_page(ops, fonts):
    """Return list of BT dicts: {start, end, rot, lines:[line]}.
    line = {first, last, font(first run), fonts(set), size, x, y, runs}.

    Coordinate model (Mission 2 generalization):
    - rot per BT: "H" (identity-ish Tm) or "R90" (Tm = (0,s,-s,0,e,f)).
    - line["row"]: position ACROSS the writing direction in user space —
      stacks reading lines. H: row = f + dy (== y). R90: row = e - dy.
    - line["wx"] / run["wx"]: ABSOLUTE position ALONG the writing
      direction in user space. H: e + dx (== x). R90: f + dx. Absolute
      coordinates matter because worksheet cell BTs start at different
      columns (p18/p20), so BT-relative x cannot anchor cells.
    - x/y keys preserved for the sheet handler (H pages: x==wx, y==row).
    """
    bts = []
    cur_bt = None
    line = None
    tf, tfsize = None, 0.0
    scale = 1.0
    leading = 0.0          # text-space, like Td operands
    size = 0.0
    row, wx = 0.0, 0.0
    rot_cur = "H"          # rotation of the LAST Tm seen — BTs can mix
                           # orientations (p66 masthead + rotated title in
                           # one BT), so rot is tracked per line

    def close_line():
        nonlocal line
        if line is not None and line["first"] is not None:
            cur_bt["lines"].append(line)
        line = None

    def new_line():
        nonlocal line
        close_line()
        line = {"first": None, "last": None, "font": None, "fonts": set(),
                "size": None, "x": wx, "y": row, "row": row, "wx": wx,
                "rot": rot_cur, "runs": []}

    for i, inst in enumerate(ops):
        op = str(inst.operator)
        if op == "BT":
            cur_bt = {"start": i, "end": None, "rot": None, "lines": []}
            rot_cur, row, wx = "H", 0.0, 0.0   # BT resets Tm to identity
        elif op == "ET":
            close_line()
            cur_bt["end"] = i
            bts.append(cur_bt)
            cur_bt = None
        elif cur_bt is None:
            continue
        elif op == "Tf":
            tf = fonts.get(str(inst.operands[0]), str(inst.operands[0]))
            tfsize = float(inst.operands[1])
            size = tfsize * scale
        elif op == "Tm":
            a, b, c, d, e, f = [float(v) for v in inst.operands]
            scale = math.hypot(a, b)
            size = tfsize * scale
            rot_cur = "R90" if abs(a) < 0.01 and b > 0 else "H"
            if cur_bt["rot"] is None:
                cur_bt["rot"] = rot_cur
            if rot_cur == "R90":
                row, wx = e, f
            else:
                row, wx = f, e
            new_line()
        elif op in ("Td", "TD", "T*"):
            if op == "T*":
                tdx, tdy = 0.0, -leading
            else:
                tdx = float(inst.operands[0])
                tdy = float(inst.operands[1])
                if op == "TD":
                    leading = -tdy
            dx, dy = tdx * scale, tdy * scale
            if abs(dy) > 0.01:
                row += -dy if rot_cur == "R90" else dy
                wx += dx
                new_line()
            else:
                if line and line["runs"]:
                    line["runs"][-1]["tdx_after"] = dx
                wx += dx
        elif op == "TL":
            leading = float(inst.operands[0])
        elif op in ("Tj", "TJ"):
            assert line is not None, f"text show outside a line at op {i}"
            if line["first"] is None:
                line["first"] = i
                line["font"] = tf
                line["size"] = round(size, 1)
            line["last"] = i
            line["fonts"].add(tf)
            runs = line["runs"]
            if not runs or runs[-1].get("tdx_after") is not None:
                runs.append({"start": i, "end": i, "glyphs": 0, "wx": wx,
                             "font": tf, "tdx_after": None})
            runs[-1]["glyphs"] += 1
            runs[-1]["end"] = i
        elif op in ("'", '"'):
            raise AssertionError(f"unhandled text op {op} at {i}")
    return bts


def painting_gaps(ops, bts):
    """(start,end) op ranges outside any BT that contain painting ops."""
    gaps, prev = [], -1
    for bt in bts:
        if bt["start"] > prev + 1:
            gaps.append((prev + 1, bt["start"] - 1))
        prev = bt["end"]
    if prev < len(ops) - 1:
        gaps.append((prev + 1, len(ops) - 1))
    return [(a, b) for a, b in gaps
            if any(str(ops[k].operator) in PAINT_OPS for k in range(a, b + 1))]


# --------------------------------------------------------------------------
# Sheet handler: classify lines -> group into logical elements.
# --------------------------------------------------------------------------

def sheet_classify(line, conf):
    f, s, y = line["font"], line["size"], line["y"]
    if any(af in fn for af in conf["artifact_fonts"] for fn in line["fonts"]):
        return "Artifact"
    if y <= conf["footer_y"]:
        return "Artifact"
    bold_first = "Bold" in f
    if bold_first and any(abs(s - a) < 0.3 for a in conf["artifact_bold_sizes"]):
        return "Artifact"
    lo, hi = conf["masthead_band"]
    if bold_first and lo <= s < hi:
        return "Artifact"
    if bold_first and s >= conf["h1_min_size"]:
        return "H1"
    all_bold = all("Bold" in fn for fn in line["fonts"])
    if (conf["h2"] and all_bold
            and abs(s - conf["body_size"]) < 0.6):
        return "H2"
    return "P"


def is_bullet_line(line, conf):
    """Single-glyph first run followed by a >6pt (scaled) x-shift."""
    if not conf["lists"]:
        return False
    r = line["runs"]
    return (len(r) >= 2 and r[0]["glyphs"] == 1
            and r[0].get("tdx_after") is not None and r[0]["tdx_after"] > 6)


def sheet_elements(lines, conf):
    """Group classified lines into logical elements, stream order.
    -> [('Artifact'|'H1'|'H2'|'P', [lines])] or ('LI', bullet_line, [cont])."""
    out, i = [], 0
    lead = conf["leading_max"]
    while i < len(lines):
        ln = lines[i]
        cls = sheet_classify(ln, conf)
        if cls == "Artifact":
            out.append(("Artifact", [ln]))
            i += 1
        elif cls in ("H1", "H2"):
            grp = [ln]
            # stack only lines BELOW (multi-line headings); same-baseline
            # lines are column heads and stay separate (known punt: pp9, 12)
            while (i + 1 < len(lines)
                   and sheet_classify(lines[i + 1], conf) == cls
                   and 5 < (lines[i]["y"] - lines[i + 1]["y"])
                   < conf["h1_stack_max"]):
                i += 1
                grp.append(lines[i])
            out.append((cls, grp))
            i += 1
        elif is_bullet_line(ln, conf):
            cont = []
            last = ln
            while (i + 1 < len(lines)
                   and sheet_classify(lines[i + 1], conf) == "P"
                   and not is_bullet_line(lines[i + 1], conf)
                   and abs(lines[i + 1]["y"] - last["y"]) < lead):
                i += 1
                cont.append(lines[i])
                last = lines[i]
            out.append(("LI", ln, cont))
            i += 1
        else:
            grp = [ln]
            while (i + 1 < len(lines)
                   and sheet_classify(lines[i + 1], conf) == "P"
                   and not is_bullet_line(lines[i + 1], conf)
                   and abs(lines[i + 1]["y"] - grp[-1]["y"]) < lead):
                i += 1
                grp.append(lines[i])
            out.append(("P", grp))
            i += 1
    return out


# --------------------------------------------------------------------------
# Marked-content planning + stream rebuild (shared, all handlers).
# --------------------------------------------------------------------------

def mc_open(tag, mcid=None):
    if mcid is None:
        oper = [pikepdf.Name("/Artifact"),
                pikepdf.Dictionary(Type=pikepdf.Name("/Pagination"))]
    else:
        oper = [pikepdf.Name("/" + tag), pikepdf.Dictionary(MCID=mcid)]
    return pikepdf.ContentStreamInstruction(oper, pikepdf.Operator("BDC"))


MC_CLOSE = pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))


class PagePlan:
    """Collects BDC/EMC insertions + the logical element list for one page."""

    def __init__(self):
        self.inserts = []      # (op_index, order 0=open/1=close, instruction)
        self.mcid = 0
        self.items = []        # ('P'|'H1'|..., [mcids], attrs) or
                               # ('LI', [('Lbl',m),('LBody',m)])
        self.dump = []         # human-readable plan lines

    def artifact(self, first_op, last_op):
        self.inserts.append((first_op, 0, mc_open("Artifact")))
        self.inserts.append((last_op + 1, 1, MC_CLOSE))

    def mark(self, tag, first_op, last_op):
        m = self.mcid
        self.mcid += 1
        self.inserts.append((first_op, 0, mc_open(tag, m)))
        self.inserts.append((last_op + 1, 1, MC_CLOSE))
        return m

    def rebuild(self, ops):
        by_pos = {}
        for pos, order, instr in self.inserts:
            by_pos.setdefault(pos, []).append((order, instr))
        new_ops = []
        for i, inst in enumerate(ops):
            # closes (order 1) before opens (order 0) at the same boundary
            for order, instr in sorted(by_pos.get(i, []), key=lambda t: -t[0]):
                new_ops.append(instr)
            new_ops.append(inst)
        for order, instr in sorted(by_pos.get(len(ops), []), key=lambda t: -t[0]):
            new_ops.append(instr)
        return new_ops


def plan_sheet_page(ops, fonts, conf):
    plan = PagePlan()
    bts = parse_page(ops, fonts)

    # inter-BT painting (rules, boxes, header bars) -> Artifact
    for a, b in painting_gaps(ops, bts):
        plan.artifact(a, b)

    # group WITHIN each BT object only: a BDC/EMC pair must never straddle
    # an ET/BT boundary (caught on p74, where flow boxes are separate BTs
    # at the same y and would otherwise merge across objects)
    elements = [el for bt in bts for el in sheet_elements(bt["lines"], conf)]

    fig_mcids = []
    for el in elements:
        kind = el[0]
        if kind == "Artifact":
            ln = el[1][0]
            # from the op that starts the line's positioning (Tm) is not
            # tracked here; first..last show ops are sufficient and safe
            plan.artifact(ln["first"], ln["last"])
            plan.dump.append(f"  Artifact  y={ln['y']:.0f} "
                             f"{ln['font'].split('+')[-1]}@{ln['size']}")
        elif kind == "LI":
            bl, cont = el[1], el[2]
            r0 = bl["runs"][0]
            lbl = plan.mark("Lbl", r0["start"], r0["end"])
            body_start = bl["runs"][1]["start"]
            body_end = cont[-1]["last"] if cont else bl["last"]
            lbody = plan.mark("LBody", body_start, body_end)
            plan.items.append(("LI", [("Lbl", lbl), ("LBody", lbody)], None))
            plan.dump.append(f"  LI        y={bl['y']:.0f} "
                             f"(+{len(cont)} cont lines)")
        else:
            grp = el[1]
            m = plan.mark(kind, grp[0]["first"], grp[-1]["last"])
            if kind == "P" and conf["figure"]:
                fig_mcids.append(m)
            else:
                plan.items.append((kind, [m], None))
            plan.dump.append(f"  {kind:<9} y={grp[0]['y']:.0f} "
                             f"{len(grp)} line(s) "
                             f"{grp[0]['font'].split('+')[-1]}@{grp[0]['size']}")
    if fig_mcids:
        plan.items.append(("Figure", fig_mcids,
                           {"Alt": conf["figure"]["alt"]}))
        plan.dump.append(f"  Figure    {len(fig_mcids)} text boxes, Alt set")
    return plan


# --------------------------------------------------------------------------
# Worksheet handler (Mission 2): rotated (and a few horizontal) worksheet
# pages. Cells are found by runs landing on ABSOLUTE writing-direction
# anchors (run["wx"]); logical table rows (TR) open when a line lands on a
# trigger anchor; ZapfDingbats glyphs become Span elements with ActualText.
#
# Emits NODE items: {"S": struct type, "attrs": {...}|None,
#                    "kids": [mcid | node, ...]} — build() recurses.
# --------------------------------------------------------------------------

DINGBAT_ACTUALTEXT = {2: "✗",   # /a23  BALLOT X (rating check)
                      3: "➔"}   # /a160 heavy rightwards arrow

ZAPF = "ZapfDingbats"


def dingbat_actualtext(ops, run):
    """ActualText for a ZapfDingbats run, from its first show operand."""
    inst = ops[run["start"]]
    opnds = (inst.operands[0] if str(inst.operator) == "TJ"
             else [inst.operands[0]])
    for o in opnds:
        if isinstance(o, pikepdf.String):
            b = bytes(o)
            if b:
                assert b[0] in DINGBAT_ACTUALTEXT, f"unmapped dingbat {b!r}"
                return DINGBAT_ACTUALTEXT[b[0]]
    raise AssertionError("dingbat run with no string operand")


def node(s, kids=None, attrs=None):
    return {"S": s, "kids": kids if kids is not None else [], "attrs": attrs}


def th_attrs(scope, colspan=None):
    d = pikepdf.Dictionary(O=pikepdf.Name("/Table"),
                           Scope=pikepdf.Name("/" + scope))
    if colspan is not None and colspan > 1:
        d.ColSpan = colspan
    return {"A": d}


WORKSHEET = {
    "handler": "worksheet",
    "orientation": "R90",
    "h1_min_size": 16.0,
    "anchors": (),          # absolute wx of column starts — per design
    "tol": 8.0,
    "trigger": (0,),        # anchor indices whose landing opens a new TR
    "trigger_bold": False,  # True: only ALL-BOLD landing lines open a TR
                            # (level-rating pages, where labels are bold and
                            # wrapped label/desc lines land near anchors)
    "trigger_gap": 0.0,     # min rrow distance from the last TR for a trigger
                            # to open a NEW one — wrapped bold labels land on
                            # the trigger anchor one leading (~12pt) below
                            # their first line (commitment pages 29-32)
    "min_cell_gap": 0.0,    # see ws_split: glyph-run gap required to open a
                            # higher-anchor cell (0 = off, M1/M2a behavior)
    "bold_cell_th": False,  # True: an all-bold FIRST cell in a mixed-font
                            # data row is a row header (p64: "Physical" |
                            # roman explanation columns) -> TH Scope=Row
    "row_th": False,        # True: FIRST cell of EVERY data row is a row
                            # header regardless of font (p72: headerless
                            # condition->conclusion table; the condition
                            # labels its row) -> TH Scope=Row. Mission 5:
                            # satisfies Acrobat's "Tables should have
                            # headers" on pages with no visual header row
    "paint_figure": None,   # {"alt", "rows": (lo,hi), "wx": (lo,hi),
                            #  "text_rows": (lo,hi)}: painting SUBPATHS whose
                            # points all fall in rows×wx become ONE Figure
                            # (with Alt) instead of Artifact, as do text
                            # lines in text_rows (chart scale labels).
                            # Subpath-level because one painting gap mixes
                            # chart strokes with form underlines (p71).
    "header_anchors": None, # separate anchors for the HEADER row: bullets
                            # offset the item column (176 vs 185 on p29), so
                            # the data anchor misses the header's "Item"
    "table_band": None,     # (row_lo, row_hi) in reading rows — per design
    "header": True,         # first bold multi-anchor line -> TH Scope=Column
    "form_wx": 337.0,       # pre-band lines split into P cells at this wx
                            # (margin + 280pt; 333–341 across designs, tol 8)
    "leading_max": 18.0,    # paragraph grouping outside the table band
    # artifact rules for horizontal-orientation worksheet pages only:
    "masthead_band": (11.0, 12.5),
    "footer_y": 50.0,
    "artifact_fonts": ("StempelGaramond",),
    "artifact_bold_sizes": (9.0,),
}


def wcfg(**over):
    c = dict(WORKSHEET)
    c.update(over)
    return c


def ws_split(runs, anchors, tol, dump, min_gap=0.0):
    """[(anchor_idx, [runs])...]: new cell when a run lands on a HIGHER
    anchor; off-anchor runs join the current cell; an off-anchor FIRST run
    snaps to the nearest anchor at/below it (logged for human review).

    min_gap > 0: a HIGHER-anchor landing only opens a cell when the run sits
    a real gap (> min_gap pt) past the previous run — long wrapped text is
    per-glyph runs and individual glyphs land on later anchors mid-word
    (commitment pages: item text reaching the Yes column anchor)."""
    cells, cur = [], None
    prev = None
    for r in runs:
        a = next((ci for ci, ax in enumerate(anchors)
                  if abs(r["wx"] - ax) < tol), None)
        if (a is not None and cur is not None and a > cur[0]
                and min_gap > 0.0 and prev is not None
                and r["wx"] - prev["wx"] <= min_gap):
            a = None        # mid-text glyph, not a column start
        prev = r
        if a is not None and (cur is None or a > cur[0]):
            cur = (a, [r])
            cells.append(cur)
        elif cur is None:
            snap = max((ci for ci, ax in enumerate(anchors)
                        if r["wx"] >= ax - tol), default=0)
            dump.append(f"    !off-anchor first run wx={r['wx']:.0f} "
                        f"snapped to col {snap}")
            cur = (snap, [r])
            cells.append(cur)
        else:
            cur[1].append(r)
    return cells


PAINT_EXEC = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}


def split_paint_runs(ops, a, b, in_rect):
    """Split gap a..b into maximal runs of (start, end, is_figure) at
    subpath granularity: coordinate ops accumulate points, the paint
    executor closes the subpath, state ops (w/d/gs/k...) travel with the
    FOLLOWING subpath. A subpath is figure iff ALL its points are in_rect."""
    runs, pts, seg_start = [], [], a
    for i in range(a, b + 1):
        op = str(ops[i].operator)
        try:
            o = [float(v) for v in ops[i].operands]
        except (TypeError, ValueError):
            o = []
        if o:
            if op == "re":
                pts += [(o[0], o[1]), (o[0] + o[2], o[1] + o[3])]
            elif op == "c":
                pts += [(o[0], o[1]), (o[2], o[3]), (o[4], o[5])]
            elif op in ("m", "l", "v", "y"):
                pts.append((o[-2], o[-1]))
        if op in PAINT_EXEC:
            inside = bool(pts) and all(in_rect(p) for p in pts)
            runs.append([seg_start, i, inside])
            seg_start, pts = i + 1, []
    if seg_start <= b:
        runs.append([seg_start, b, False])
    merged = [runs[0]]
    for r in runs[1:]:
        if r[2] == merged[-1][2]:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return merged


def plan_worksheet_page(ops, fonts, conf):
    plan = PagePlan()
    bts = parse_page(ops, fonts)
    rot = conf["orientation"]

    fig = conf["paint_figure"]
    fig_kids = []           # MCIDs joining the single Figure element

    def fig_rect(pt):
        # R90: reading row == user-space x, writing direction == y
        x, y = pt
        return (fig["rows"][0] <= x <= fig["rows"][1]
                and fig["wx"][0] <= y <= fig["wx"][1])

    for a, b in painting_gaps(ops, bts):
        if fig is None:
            plan.artifact(a, b)
            continue
        for s, e, inside in split_paint_runs(ops, a, b, fig_rect):
            if inside:
                fig_kids.append(plan.mark("Figure", s, e))
                plan.dump.append(f"  Figure paint ops {s}-{e}")
            else:
                plan.artifact(s, e)

    # off-orientation LINES are decoration (masthead/footer/reference block
    # on R90 pages — note BTs can MIX orientations, p66); on H pages apply
    # the sheet artifact rules per line.
    work = []
    for bt in bts:
        for ln in bt["lines"]:
            if ln["rot"] != rot:
                plan.artifact(ln["first"], ln["last"])
                continue
            if rot == "H":
                f, s = ln["font"], ln["size"]
                bold = "Bold" in f
                if (any(af in fn for af in conf["artifact_fonts"]
                        for fn in ln["fonts"])
                        or ln["y"] <= conf["footer_y"]
                        or (bold and any(abs(s - z) < 0.3 for z in
                                         conf["artifact_bold_sizes"]))
                        or (bold and conf["masthead_band"][0] <= s
                            < conf["masthead_band"][1])):
                    plan.artifact(ln["first"], ln["last"])
                    continue
            ln["bt"] = bt
            ln["rrow"] = ln["row"] if rot == "R90" else -ln["row"]
            work.append(ln)

    work.sort(key=lambda l: (round(l["rrow"], 1), l["runs"][0]["wx"]))

    # one page may carry SEVERAL independent tables (closeness pages 59-63:
    # two ruled grids, each with its own header). conf["tables"] is a list
    # of override dicts (table_band + any per-table keys); absent, the page
    # is the single table described by the config itself.
    specs = []
    for over in (conf.get("tables") or [{}]):
        s = dict(conf)
        s.update(over)
        specs.append(s)
    tol = conf["tol"]

    def is_dingbat(run):
        return ZAPF in run["font"]

    def span_node(run):
        m = plan.mark("Span", run["start"], run["end"])
        return node("Span", [m], {"ActualText": dingbat_actualtext(ops, run)})

    # ---- pass A: H1 + pre/post-band paragraphs; collect band lines ----
    pre_items, post_items = [], []
    band_lines = [[] for _ in specs]   # per table spec
    para = None          # open paragraph: [node, last_line] outside band

    def close_para():
        nonlocal para
        para = None

    for ln in work:
        if ln["size"] >= conf["h1_min_size"]:
            close_para()
            # heading: one mark over the whole BT-contiguous title group
            if pre_items and pre_items[-1]["S"] == "H1" \
                    and pre_items[-1]["_bt"] is ln["bt"]:
                m = plan.mark("H1", ln["first"], ln["last"])
                pre_items[-1]["kids"].append(m)
            else:
                h = node("H1", [plan.mark("H1", ln["first"], ln["last"])])
                h["_bt"] = ln["bt"]
                pre_items.append(h)
            plan.dump.append(f"  H1   rrow={ln['rrow']:.0f}")
            continue
        if fig and fig["text_rows"][0] <= ln["rrow"] <= fig["text_rows"][1]:
            close_para()
            fig_kids.append(plan.mark("Figure", ln["first"], ln["last"]))
            plan.dump.append(f"  Figure text rrow={ln['rrow']:.0f}")
            continue
        si = next((i for i, s in enumerate(specs)
                   if s["table_band"][0] <= ln["rrow"] <= s["table_band"][1]),
                  None)
        if si is not None:
            close_para()
            band_lines[si].append(ln)
            continue
        # non-band, non-heading: pre if above the FIRST band, else post
        # (nothing sits between bands on the known multi-table pages)
        pre = ln["rrow"] < specs[0]["table_band"][0]
        sink = pre_items if pre else post_items
        # form-style line: split cells at form_wx. Only PRE-band, and only
        # when a real column gap (>60pt from the previous run) precedes the
        # landing — dense word runs land near form_wx by accident.
        text_runs = [r for r in ln["runs"] if not is_dingbat(r)]
        ding_runs = [r for r in ln["runs"] if is_dingbat(r)]
        form_split = False
        if pre and conf["form_wx"] is not None and len(text_runs) > 1:
            for i, r in enumerate(text_runs):
                if (abs(r["wx"] - conf["form_wx"]) < tol and i > 0
                        and r["wx"] - text_runs[i - 1]["wx"] > 60):
                    form_split = True
                    break
        if form_split:
            close_para()
            split = next(i for i, r in enumerate(text_runs)
                         if abs(r["wx"] - conf["form_wx"]) < tol)
            for seg in (text_runs[:split], text_runs[split:]):
                if seg:
                    m = plan.mark("P", seg[0]["start"], seg[-1]["end"])
                    sink.append(node("P", [m]))
            plan.dump.append(f"  P|P  rrow={ln['rrow']:.0f} form line")
        else:
            # group by row proximity only — marks are per line, so grouping
            # across BTs cannot make a BDC straddle an ET/BT boundary
            if (para is None or abs(ln["rrow"] - para[1]["rrow"])
                    > conf["leading_max"]):
                p = node("P", [])
                sink.append(p)
                para = [p, ln]
            if text_runs:
                m = plan.mark("P", text_runs[0]["start"],
                              text_runs[-1]["end"])
                para[0]["kids"].append(m)
            para[1] = ln
            plan.dump.append(f"  P    rrow={ln['rrow']:.0f} "
                             f"{ln['font'].split('+')[-1]}@{ln['size']}")
        for r in ding_runs:
            (para[0]["kids"] if para else sink).append(span_node(r))
            plan.dump.append(f"  Span rrow={ln['rrow']:.0f} (dingbat)")

    # ---- pass B: table rows, one pass per table spec ----
    def build_table(sconf, blines):
        anchors = sconf["anchors"]
        trs = []           # [{row, last, cells:{anchor: [segments]}, kind}]
        ding_pend = []     # (rrow, run)
        header_seen = False

        def lands(ln, idxs):
            return any(abs(r["wx"] - anchors[t]) < tol
                       for t in idxs for r in ln["runs"] if not is_dingbat(r))

        for ln in blines:
            text_runs = [r for r in ln["runs"] if not is_dingbat(r)]
            for r in ln["runs"]:
                if is_dingbat(r):
                    ding_pend.append((ln["rrow"], r))
            if not text_runs:
                continue
            all_bold = all("Bold" in fn for fn in ln["fonts"]
                           if ZAPF not in fn)
            # trigger_gap measures from the TR's LAST line ("last"), not its
            # trigger line ("row" — which dingbat row-assignment still uses):
            # three-line header wraps (p40) sit 24pt below the header's
            # first line but only 12pt below its last
            trig = lands(ln, sconf["trigger"]) and \
                (all_bold or not sconf["trigger_bold"]) and \
                (not trs
                 or ln["rrow"] - trs[-1]["last"] >= sconf["trigger_gap"])
            if trig or not trs:
                cells = ws_split(text_runs, anchors, tol, plan.dump,
                                 sconf["min_cell_gap"])
                kind = None
                if all_bold and sconf["header"] and not header_seen:
                    # the header row may only resolve against its OWN anchor
                    # set (p73: header text sits 9pt left of every data
                    # anchor) — try header_anchors when provided
                    hcells = (ws_split(text_runs, sconf["header_anchors"],
                                       tol, plan.dump, sconf["min_cell_gap"])
                              if sconf["header_anchors"] else cells)
                    if len(hcells) > 1:
                        kind, cells, header_seen = "head", hcells, True
                if kind is None:
                    kind = "group" if (all_bold and len(cells) == 1) \
                        else "data"
                trs.append({"row": ln["rrow"], "last": ln["rrow"],
                            "cells": {}, "kind": kind})
                plan.dump.append(f"  TR({kind}) rrow={ln['rrow']:.0f} "
                                 f"cols {[c[0] for c in cells]}")
            else:
                anc = (sconf["header_anchors"]
                       if sconf["header_anchors"]
                       and trs[-1]["kind"] == "head"
                       else anchors)
                cells = ws_split(text_runs, anc, tol, plan.dump,
                                 sconf["min_cell_gap"])
                plan.dump.append(f"   +cont rrow={ln['rrow']:.0f} "
                                 f"cols {[c[0] for c in cells]}")
            tr = trs[-1]
            tr["last"] = ln["rrow"]
            for ci, rr in cells:
                tr["cells"].setdefault(ci, []).append(
                    (rr[0]["start"], rr[-1]["end"]))
                bold = all("Bold" in r["font"] for r in rr)
                tr.setdefault("bold", {})[ci] = \
                    tr.get("bold", {}).get(ci, True) and bold

        # assign dingbats to rows (nearest TR at/above) and cells (by wx)
        ding_cells = {}
        for rrow, run in ding_pend:
            ti = max((i for i, t in enumerate(trs)
                      if t["row"] <= rrow + 2.0), default=0)
            # checks sit ~8–11pt LEFT of the column they mark: wider tol
            ci = max((i for i, ax in enumerate(anchors)
                      if run["wx"] >= ax - 12.0), default=0)
            ding_cells.setdefault((ti, ci), []).append(run)
            plan.dump.append(f"  Span->TR{ti} col{ci} "
                             f"(dingbat wx={run['wx']:.0f})")

        if not trs:
            return None
        # Mission 5 (Acrobat Regularity): every TR must sum to the SAME
        # column count. The grid width is len(anchors); cells are already
        # keyed by anchor index, so walk ALL grid columns per row:
        #   - occupied column -> the real cell (marks emitted in the same
        #     order as before, so MCID numbering is unchanged);
        #   - a group row's TH spans the empty run up to the next occupied
        #     column -> ColSpan (p15 "People:" spans all 3; p58 "Physical
        #     Closeness Items:" spans 4 with the comments TD in col 4);
        #   - any other empty column -> an EMPTY cell (TD; empty TH Scope=
        #     Column in header rows — p71's corner stub over "Conclusions:",
        #     p76/77's unlabeled arrow column). Empty struct cells carry no
        #     MCID, so the three-check gate is unaffected.
        ncols = len(anchors)
        table = node("Table")
        for ti, tr in enumerate(trs):
            trn = node("TR")
            cols = sorted(set(tr["cells"]) |
                          {c for (t, c) in ding_cells if t == ti})
            span_from = span_to = None
            if tr["kind"] == "group" and cols:
                span_from = cols[0]
                span_to = next((c for c in cols if c > cols[0]), ncols)
            for ci in range(ncols):
                if ci not in cols:
                    if span_from is not None and span_from < ci < span_to:
                        continue        # covered by the group TH's ColSpan
                    pad = node("TH", attrs=th_attrs("Column")) \
                        if tr["kind"] == "head" else node("TD")
                    trn["kids"].append(pad)
                    plan.dump.append(f"   pad {pad['S']} TR{ti} col{ci}")
                    continue
                if tr["kind"] == "head":
                    cell = node("TH", attrs=th_attrs("Column"))
                elif tr["kind"] == "group" and ci == cols[0]:
                    # only the LANDED cell is the row header; trailing cells
                    # (level-rating descriptions) are data
                    cell = node("TH",
                                attrs=th_attrs("Row", span_to - span_from))
                elif (ci == cols[0] and tr["kind"] == "data"
                      and (sconf["row_th"]
                           or (sconf["bold_cell_th"]
                               and tr.get("bold", {}).get(ci)))):
                    cell = node("TH", attrs=th_attrs("Row"))
                else:
                    cell = node("TD")
                for seg in tr["cells"].get(ci, ()):
                    cell["kids"].append(plan.mark(cell["S"], seg[0], seg[1]))
                for run in ding_cells.get((ti, ci), ()):
                    cell["kids"].append(span_node(run))
                trn["kids"].append(cell)
            table["kids"].append(trn)
        return table

    tables = []
    for si, sconf in enumerate(specs):
        if len(specs) > 1:
            plan.dump.append(f"  -- table {si + 1} "
                             f"band={sconf['table_band']} --")
        t = build_table(sconf, band_lines[si])
        if t is not None:
            tables.append(t)

    for it in pre_items:
        it.pop("_bt", None)
    figure = [node("Figure", fig_kids, {"Alt": fig["alt"]})] if fig_kids \
        else []
    for it in pre_items + figure + tables + post_items:
        plan.items.append(("NODE", it, None))
    return plan


# --------------------------------------------------------------------------
# ToC handler (Mission 3, pp5-6): TOC > TOCI per entry. Each entry line has
# the reference number (right-aligned, first run wx < toc_num_xmax) -> Lbl,
# then title runs + page number -> one P per entry (continuation lines for
# wrapped titles join the open entry's P). sheet_classify supplies the
# artifact/H1 rules; the bold "References ... Page" column-head line splits
# into two Ps at the Page run. NOTE the ToC spans pp5-6 and build() is
# per-page, so it emits TWO sibling TOC elements (logged residual).
# --------------------------------------------------------------------------

def plan_toc_page(ops, fonts, conf):
    plan = PagePlan()
    bts = parse_page(ops, fonts)
    for a, b in painting_gaps(ops, bts):
        plan.artifact(a, b)

    toc = node("TOC")
    cur_p = None           # the open entry's P node (wrapped titles)
    for bt in bts:
        for ln in bt["lines"]:
            cls = sheet_classify(ln, conf)
            if cls == "Artifact":
                plan.artifact(ln["first"], ln["last"])
                plan.dump.append(f"  Artifact  y={ln['y']:.0f}")
                continue
            if cls == "H1":
                m = plan.mark("H1", ln["first"], ln["last"])
                plan.items.append(("NODE", node("H1", [m]), None))
                plan.dump.append(f"  H1        y={ln['y']:.0f}")
                continue
            if cls == "H2":
                # "References ... Page" column heads: two Ps, split before
                # the right-aligned Page run (wx > 450)
                runs = ln["runs"]
                split = next((i for i, r in enumerate(runs)
                              if i > 0 and r["wx"] > 450), len(runs))
                for seg in (runs[:split], runs[split:]):
                    if seg:
                        m = plan.mark("P", seg[0]["start"], seg[-1]["end"])
                        plan.items.append(("NODE", node("P", [m]), None))
                plan.dump.append(f"  P|P       y={ln['y']:.0f} column heads")
                continue
            runs = ln["runs"]
            if runs[0]["wx"] < conf["toc_num_xmax"]:
                # entry start: number -> Lbl, rest of line -> P
                lbl = node("Lbl", [plan.mark("Lbl", runs[0]["start"],
                                             runs[0]["end"])])
                cur_p = node("P", [])
                if len(runs) > 1:
                    cur_p["kids"].append(plan.mark("P", runs[1]["start"],
                                                   runs[-1]["end"]))
                toc["kids"].append(node("TOCI", [lbl, cur_p]))
                plan.dump.append(f"  TOCI      y={ln['y']:.0f}")
            else:
                assert cur_p is not None, \
                    f"ToC continuation line before any entry at y={ln['y']}"
                cur_p["kids"].append(plan.mark("P", ln["first"], ln["last"]))
                plan.dump.append(f"   +cont    y={ln['y']:.0f}")
    if toc["kids"]:
        plan.items.append(("NODE", toc, None))
    return plan


HANDLERS = {
    "sheet": plan_sheet_page,
    "worksheet": plan_worksheet_page,
    "toc": plan_toc_page,
}


def build(src, dst, page_classes, configs, *, doc_title=None,
          doc_lang=None, bookmarks_fn=None, only_pages=None, dump=False):
    pdf = pikepdf.open(src)
    struct_root = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name("/StructTreeRoot")))
    doc_elem = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"), S=pikepdf.Name("/Document"),
        P=struct_root, K=pikepdf.Array()))
    struct_root.K = pikepdf.Array([doc_elem])
    nums = pikepdf.Array()
    next_key = 0
    summary = {}

    def se(stype, parent, page_obj, attrs=None):
        d = pikepdf.Dictionary(
            Type=pikepdf.Name("/StructElem"), S=pikepdf.Name("/" + stype),
            P=parent, Pg=page_obj, K=pikepdf.Array())
        if attrs:
            for k, v in attrs.items():
                if k == "A":
                    d.A = v
                else:
                    d[pikepdf.Name("/" + k)] = pikepdf.String(v) \
                        if isinstance(v, str) else v
        return pdf.make_indirect(d)

    pages = sorted(page_classes)
    if only_pages:
        pages = [p for p in pages if p in only_pages]

    for pno in pages:
        conf = configs[page_classes[pno]]
        page = pdf.pages[pno - 1]
        fonts = basefonts(page)
        ops = list(pikepdf.parse_content_stream(page))
        plan = HANDLERS[conf["handler"]](ops, fonts, conf)

        if dump:
            print(f"--- page {pno} [{page_classes[pno]}] ---")
            for ln in plan.dump:
                print(ln)

        page.Contents = pdf.make_stream(
            pikepdf.unparse_content_stream(plan.rebuild(ops)))

        # per-page structure elements under the single /Document
        page_obj = page.obj
        mcid_to_elem = {}
        open_list = None
        census = {}
        def emit(nd, parent):
            attrs = nd.get("attrs")
            sattrs = None
            if attrs:
                sattrs = {}
                for k, v in attrs.items():
                    sattrs[k] = v
            elem = se(nd["S"], parent, page_obj, sattrs)
            for kid in nd["kids"]:
                if isinstance(kid, int):
                    elem.K.append(kid)
                    mcid_to_elem[kid] = elem
                else:
                    elem.K.append(emit(kid, elem))
                    census[kid["S"]] = census.get(kid["S"], 0) + 1
            return elem

        for item in plan.items:
            kind, payload, attrs = item
            if kind == "NODE":
                open_list = None
                doc_elem.K.append(emit(payload, doc_elem))
                census[payload["S"]] = census.get(payload["S"], 0) + 1
            elif kind == "LI":
                if open_list is None:
                    open_list = se("L", doc_elem, page_obj)
                    doc_elem.K.append(open_list)
                li = se("LI", open_list, page_obj)
                open_list.K.append(li)
                for stype, m in payload:
                    leaf = se(stype, li, page_obj)
                    leaf.K.append(m)
                    li.K.append(leaf)
                    mcid_to_elem[m] = leaf
                census["LI"] = census.get("LI", 0) + 1
            else:
                open_list = None
                elem = se(kind, doc_elem, page_obj, attrs)
                for m in payload:
                    elem.K.append(m)
                    mcid_to_elem[m] = elem
                doc_elem.K.append(elem)
                census[kind] = census.get(kind, 0) + 1

        page.StructParents = next_key
        arr = pikepdf.Array([mcid_to_elem[m] for m in range(plan.mcid)])
        nums.append(next_key)
        nums.append(pdf.make_indirect(arr))
        next_key += 1
        summary[pno] = census

    struct_root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=nums))
    struct_root.ParentTreeNextKey = next_key
    pdf.Root.StructTreeRoot = struct_root
    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)

    # Document-level fixes, re-applied on every full rebuild (the tagger
    # always runs from the PRISTINE original, so these must live in the
    # build or they vanish — M1/M2 builds shipped without them). The caller
    # supplies title/lang; Tab order is universal good practice.
    if doc_lang:
        pdf.Root.Lang = pikepdf.String(doc_lang)
    if doc_title:
        pdf.Root.ViewerPreferences = pikepdf.Dictionary(DisplayDocTitle=True)
    # Tab order follows structure order (Acrobat checker: "Tab order").
    # Page-dict key only — non-painting, gate unaffected.
    for pg in pdf.pages:
        pg.obj.Tabs = pikepdf.Name("/S")
    if doc_title:
        with pdf.open_metadata() as meta:
            meta["dc:title"] = doc_title
        pdf.docinfo["/Title"] = doc_title

    # Bookmarks (Mission 4): outline generated from the ToC text. Only on
    # full builds — a --pages subset isn't a deliverable.
    if bookmarks_fn and not only_pages:
        n = bookmarks_fn(pdf, src)
        print(f"bookmarks: {n} outline items", file=sys.stderr)

    pdf.save(dst)
    return summary
