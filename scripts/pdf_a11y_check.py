#!/usr/bin/env python3
"""
PDF accessibility structural checker — Matterhorn/PDF-UA subset.
Deterministic checks only; no LLM judgment. Inspired by veraPDF checkpoints.
"""
import sys, json, collections
import pikepdf
import fitz  # pymupdf

STD_STRUCT_TYPES = {
    "Document","Part","Art","Sect","Div","BlockQuote","Caption","TOC","TOCI",
    "Index","NonStruct","Private","P","H","H1","H2","H3","H4","H5","H6",
    "L","LI","Lbl","LBody","Table","TR","TH","TD","THead","TBody","TFoot",
    "Span","Quote","Note","Reference","BibEntry","Code","Link","Annot",
    "Ruby","RB","RT","RP","Warichu","WT","WP","Figure","Formula","Form",
    "Document","Aside","Title","FENote","Sub","Em","Strong",
}

def walk_struct(elem, stats, depth=0, max_depth=80):
    """Recursively walk structure tree collecting stats."""
    if depth > max_depth:
        return
    try:
        kids = elem.get("/K", None)
    except Exception:
        return
    s = elem.get("/S", None)
    if s is not None:
        name = str(s)[1:]  # strip leading /
        stats["types"][name] += 1
        if name == "Figure":
            alt = elem.get("/Alt", None)
            actual = elem.get("/ActualText", None)
            if alt is None and actual is None:
                stats["figures_no_alt"] += 1
            elif alt is not None and str(alt).strip() == "":
                stats["figures_empty_alt"] += 1
        if name == "Link":
            if elem.get("/Alt", None) is None:
                stats["links_no_alt"] += 1
        if name in ("TH",):
            attrs = elem.get("/A", None)
            scope_found = False
            def check_attr(a):
                nonlocal scope_found
                try:
                    if a.get("/O", None) is not None and str(a.get("/O")) == "/Table":
                        if a.get("/Scope", None) is not None:
                            scope_found = True
                except Exception:
                    pass
            if attrs is not None:
                if isinstance(attrs, pikepdf.Array):
                    for a in attrs:
                        if isinstance(a, pikepdf.Dictionary):
                            check_attr(a)
                elif isinstance(attrs, pikepdf.Dictionary):
                    check_attr(attrs)
            if not scope_found:
                stats["th_no_scope"] += 1
    if kids is None:
        return
    if isinstance(kids, pikepdf.Array):
        items = kids
    else:
        items = [kids]
    for k in items:
        if isinstance(k, pikepdf.Dictionary) and k.get("/S", None) is not None:
            walk_struct(k, stats, depth + 1)
        # integers are MCIDs; dicts w/o /S may be MCR/OBJR — leaf content

def heading_analysis(types):
    headings = {f"H{i}": types.get(f"H{i}", 0) for i in range(1, 7)}
    generic_h = types.get("H", 0)
    present = [i for i in range(1, 7) if headings[f"H{i}"] > 0]
    issues = []
    if generic_h and any(headings.values()):
        issues.append("mixes generic H with numbered Hn")
    if present:
        if present[0] != 1:
            issues.append(f"first heading level used is H{present[0]} (no H1)")
        for a, b in zip(present, present[1:]):
            if b - a > 1:
                issues.append(f"level skip H{a}->H{b}")
    return headings, generic_h, issues

def check(path):
    r = {"file": path.split("/")[-1], "checks": {}}
    pdf = pikepdf.open(path)
    root = pdf.Root
    c = r["checks"]

    # --- Document-level (Matterhorn 06, 07, 11) ---
    mi = root.get("/MarkInfo", None)
    marked = bool(mi is not None and mi.get("/Marked", False))
    c["marked_tagged"] = marked
    c["suspects"] = bool(mi.get("/Suspects", False)) if mi is not None else None
    c["struct_tree_root"] = root.get("/StructTreeRoot", None) is not None
    lang = root.get("/Lang", None)
    c["doc_language"] = str(lang) if lang is not None else None
    # Title
    title = None
    with pdf.open_metadata() as meta:
        title = meta.get("dc:title", None)
    if not title and pdf.docinfo.get("/Title", None):
        title = str(pdf.docinfo["/Title"])
    c["title"] = (title[:120] + "…") if title and len(title) > 120 else title
    vp = root.get("/ViewerPreferences", None)
    c["display_doc_title"] = bool(vp.get("/DisplayDocTitle", False)) if vp is not None else False
    # Outlines / bookmarks
    outlines = root.get("/Outlines", None)
    n_bm = 0
    if outlines is not None:
        def count_bm(node, d=0):
            nonlocal n_bm
            if d > 40: return
            kid = node.get("/First", None)
            while kid is not None and n_bm < 5000:
                n_bm += 1
                count_bm(kid, d+1)
                kid = kid.get("/Next", None)
        count_bm(outlines)
    c["bookmarks"] = n_bm
    c["pages"] = len(pdf.pages)

    # --- Structure tree walk (Matterhorn 01, 09, 13, 14, 15, 28) ---
    stats = {"types": collections.Counter(), "figures_no_alt": 0,
             "figures_empty_alt": 0, "links_no_alt": 0, "th_no_scope": 0}
    if c["struct_tree_root"]:
        st = root["/StructTreeRoot"]
        k = st.get("/K", None)
        if k is not None:
            items = k if isinstance(k, pikepdf.Array) else [k]
            for el in items:
                if isinstance(el, pikepdf.Dictionary):
                    walk_struct(el, stats)
        nonstd = {t: n for t, n in stats["types"].items() if t not in STD_STRUCT_TYPES}
        # role map may legitimize nonstd types
        rolemap = st.get("/RoleMap", None)
        mapped = set()
        if rolemap is not None:
            for key in rolemap.keys():
                mapped.add(str(key)[1:])
        c["tag_types"] = dict(stats["types"].most_common(25))
        c["total_tags"] = sum(stats["types"].values())
        c["nonstandard_unmapped_types"] = {t: n for t, n in nonstd.items() if t not in mapped}
        c["figures_no_alt"] = stats["figures_no_alt"]
        c["figures_empty_alt"] = stats["figures_empty_alt"]
        c["links_no_alt"] = stats["links_no_alt"]
        c["th_no_scope"] = stats["th_no_scope"]
        h, generic, h_issues = heading_analysis(stats["types"])
        c["headings"] = {k2: v for k2, v in h.items() if v}
        c["generic_H"] = generic
        c["heading_issues"] = h_issues
        c["tables"] = stats["types"].get("Table", 0)
        c["th_count"] = stats["types"].get("TH", 0)

    # --- Content-level via pymupdf: real text vs scanned, fonts, annots ---
    doc = fitz.open(path)
    sample = list(range(min(c["pages"], 12)))
    if c["pages"] > 12:  # add some middle/end pages
        sample += [c["pages"]//2, c["pages"]-1]
    text_chars, img_count, pages_no_text = 0, 0, 0
    for pno in sample:
        page = doc[pno]
        t = page.get_text("text")
        text_chars += len(t.strip())
        if len(t.strip()) < 20:
            pages_no_text += 1
        img_count += len(page.get_images())
    c["sampled_pages"] = len(sample)
    c["sampled_pages_without_text"] = pages_no_text
    c["sampled_image_count"] = img_count
    c["likely_scanned"] = pages_no_text == len(sample) and img_count > 0
    # fonts: embedding + ToUnicode (Matterhorn 31)
    fonts_not_embedded, fonts_no_tounicode, fonts_seen = set(), set(), set()
    for pno in sample:
        for f in doc[pno].get_fonts(full=True):
            xref, _, ftype, name, refname, enc = f[:6]
            fonts_seen.add(name)
    c["fonts"] = sorted(fonts_seen)[:15]
    c["n_fonts"] = len(fonts_seen)
    # XMP PDF/UA identifier (Matterhorn 06-002)
    with pdf.open_metadata() as meta:
        c["pdfua_id"] = meta.get("pdfuaid:part", None)

    doc.close(); pdf.close()
    return r

if __name__ == "__main__":
    out = [check(p) for p in sys.argv[1:]]
    print(json.dumps(out, indent=1, default=str))
