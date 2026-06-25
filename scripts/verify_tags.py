#!/usr/bin/env python3
"""
verify_tags.py — mandatory verification after any remediation or tagging pass.

Three independent, document-agnostic proofs:

1. VISUAL IDENTITY (pymupdf): render original vs modified at the same dpi and
   compare pixel hashes page by page. Tag-tree surgery and marked-content
   insertion are both non-painting; every page must be pixel-identical.

2. TAG READ-BACK (pikepdf + pdfplumber): walk the modified file's structure
   tree with pikepdf to collect every (page, MCID, struct type), then use a
   SEPARATE parser (pdfplumber) to recover the text actually painted under
   each MCID in the content stream. This catches what a structure-only
   checker cannot: struct elements pointing at MCIDs absent from the stream,
   stream MCIDs no struct element claims, and elements whose marked content
   paints no text (e.g. a mis-scaled geometry pass that wrapped nothing —
   a real bug this check caught).

3. TEXT COVERAGE (pdfplumber): on every page the structure tree touches,
   every painted character must sit inside EITHER a tagged MCID OR an
   /Artifact marked-content section. Characters in neither are "uncovered"
   and fail the gate — a tagged page with unmarked text means the tagger
   skipped content. The per-page tagged/artifact counts are also reported:
   a page whose artifact count is wildly out of family with its siblings is
   the signature of real content silently artifacted (the p66 failure mode,
   which is pixel-identical AND read-back-clean, so checks 1–2 cannot see
   it). Review artifact outliers with --dump, which prints artifacted text.

The independence is the point: the code that wrote the tags must never be
the code that proves them.

Usage:
    python verify_tags.py original.pdf modified.pdf
    python verify_tags.py original.pdf modified.pdf --dump        # human review
    python verify_tags.py original.pdf modified.pdf --pages 7-9,12,13
    python verify_tags.py original.pdf modified.pdf --dpi 150
    python verify_tags.py modified.pdf --skip-visual              # read-back only

--pages limits BOTH checks (1-based). Default: all pages for visual identity,
all pages referenced by the structure tree for read-back.

Exit 0 = every check passed. Exit 1 = at least one failure — stop and
investigate before shipping. Non-text struct types (Figure, Formula, Form,
Link, Annot) are exempt from the empty-text failure and reported separately.

When the original is also tagged, read-back defects already present in the
original are reported under "pre_existing_defects" (honest residuals for the
report) and do NOT fail the gate — only defects the pass introduced do.
With --skip-visual there is no baseline, so all defects fail.
"""
import argparse
import hashlib
import json
import sys

import pikepdf

NON_TEXT_TYPES = {"Figure", "Formula", "Form", "Link", "Annot"}
EXCERPT = 70


def parse_pages(spec):
    """'1,5,9-12' -> {0, 4, 8, 9, 10, 11} (0-based)."""
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a) - 1, int(b)))
        else:
            out.add(int(part) - 1)
    return out


def visual_identity(orig_path, mod_path, dpi, pages):
    import fitz

    # MuPDF prints struct-tree warnings to stdout, corrupting our JSON output.
    # Rendering is unaffected; the pixel hash is the verdict.
    fitz.TOOLS.mupdf_display_errors(False)
    a, b = fitz.open(orig_path), fitz.open(mod_path)
    result = {"dpi": dpi, "pages_checked": 0, "mismatched_pages": []}
    if len(a) != len(b):
        result["error"] = f"page count differs: {len(a)} vs {len(b)}"
        return result, False
    check = sorted(pages) if pages else range(len(a))
    for p in check:
        ha = hashlib.md5(a[p].get_pixmap(dpi=dpi).samples).hexdigest()
        hb = hashlib.md5(b[p].get_pixmap(dpi=dpi).samples).hexdigest()
        result["pages_checked"] += 1
        if ha != hb:
            result["mismatched_pages"].append(p + 1)
    a.close(); b.close()
    return result, not result["mismatched_pages"]


def collect_struct_leaves(pdf):
    """Walk StructTreeRoot -> [(page_index, mcid, struct_type), ...].
    Handles integer-MCID kids and /MCR dicts; resolves /Pg by inheritance."""
    pagemap = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
    leaves = []

    def walk(elem, page, depth=0):
        if depth > 80:
            return
        pg = elem.get("/Pg", None)
        if pg is not None:
            page = pagemap.get(pg.objgen, page)
        stype = str(elem.get("/S", "/?"))[1:]
        kids = elem.get("/K", None)
        if kids is None:
            return
        items = kids if isinstance(kids, pikepdf.Array) else [kids]
        for item in items:
            if isinstance(item, pikepdf.Dictionary):
                t = str(item.get("/Type", ""))
                if t == "/MCR":
                    ipg = item.get("/Pg", None)
                    ipage = pagemap.get(ipg.objgen, page) if ipg is not None else page
                    leaves.append((ipage, int(item["/MCID"]), stype))
                elif t == "/OBJR":
                    continue
                elif item.get("/S", None) is not None:
                    walk(item, page, depth + 1)
            else:
                try:
                    leaves.append((page, int(item), stype))
                except (TypeError, ValueError):
                    pass

    root = pdf.Root.get("/StructTreeRoot", None)
    if root is None:
        return None
    k = root.get("/K", None)
    if k is not None:
        items = k if isinstance(k, pikepdf.Array) else [k]
        for el in items:
            if isinstance(el, pikepdf.Dictionary):
                walk(el, None)
    return leaves


def stream_mcid_text(mod_path, page_indices, coverage=None):
    """pdfplumber pass: {(page_index, mcid): text} for the given pages.
    Requires pdfplumber >= 0.10 (chars carry 'mcid'/'tag').

    If `coverage` is a dict, it is filled per page with
    {page_index: {"tagged": n, "artifact": n, "uncovered": n,
                  "uncovered_text": str, "artifact_text": str}}
    counting non-whitespace characters by marked-content status."""
    import pdfplumber

    texts = {}
    with pdfplumber.open(mod_path) as pp:
        for pno in sorted(page_indices):
            if pno >= len(pp.pages):
                continue
            page = pp.pages[pno]
            if page.chars and "mcid" not in page.chars[0]:
                raise SystemExit(
                    "pdfplumber too old: chars lack 'mcid' (need >= 0.10)")
            if coverage is not None:
                cov = coverage.setdefault(pno, {
                    "tagged": 0, "artifact": 0, "uncovered": 0,
                    "uncovered_text": [], "artifact_text": []})
            for ch in page.chars:
                mcid = ch.get("mcid")
                if coverage is not None and ch["text"].strip():
                    if mcid is not None:
                        cov["tagged"] += 1
                    elif ch.get("tag") == "Artifact":
                        cov["artifact"] += 1
                        cov["artifact_text"].append(ch["text"])
                    else:
                        cov["uncovered"] += 1
                        cov["uncovered_text"].append(ch["text"])
                if mcid is None:
                    continue
                texts.setdefault((pno, mcid), []).append(ch["text"])
            page.flush_cache()  # keep memory flat on big documents
    if coverage is not None:
        for cov in coverage.values():
            cov["uncovered_text"] = "".join(cov["uncovered_text"])
            cov["artifact_text"] = "".join(cov["artifact_text"])
    return {k: "".join(v) for k, v in texts.items()}


def baseline_defects(path, scan):
    """(page, mcid) defect set for the ORIGINAL file, same checks as read_back.
    Returns None if the original is untagged (nothing to baseline against)."""
    pdf = pikepdf.open(path)
    leaves = collect_struct_leaves(pdf)
    pdf.close()
    if not leaves:
        return None
    leaves = [(p, m, s) for p, m, s in leaves if p in scan]
    texts = stream_mcid_text(path, scan)
    seen = {(p, m) for p, m, _ in leaves}
    defects = {(p, m) for p, m, s in leaves
               if s not in NON_TEXT_TYPES and not texts.get((p, m), "").strip()}
    defects |= {pm for pm in texts if pm not in seen}
    return defects


def read_back(mod_path, pages, dump, baseline_path=None):
    pdf = pikepdf.open(mod_path)
    leaves = collect_struct_leaves(pdf)
    result = {}
    if leaves is None:
        result["error"] = "no StructTreeRoot — nothing to verify"
        return result, False
    if not leaves:
        result["error"] = "StructTreeRoot present but no MCIDs reachable"
        return result, False

    struct_pages = {p for p, _, _ in leaves if p is not None}
    scan = struct_pages & pages if pages else struct_pages
    leaves = [(p, m, s) for p, m, s in leaves if p in scan]
    coverage = {}
    texts = stream_mcid_text(mod_path, scan, coverage)

    census = {}
    empty, non_text, missing = [], [], []
    seen = set()
    for p, m, s in leaves:
        census[s] = census.get(s, 0) + 1
        seen.add((p, m))
        t = texts.get((p, m))
        if t is None:
            (non_text if s in NON_TEXT_TYPES else missing).append(
                {"page": p + 1, "mcid": m, "type": s})
        elif not t.strip():
            (non_text if s in NON_TEXT_TYPES else empty).append(
                {"page": p + 1, "mcid": m, "type": s})
    orphans = [{"page": p + 1, "mcid": m} for (p, m) in texts if (p, m) not in seen]
    dupes = len(leaves) - len(seen)

    # Baseline against the original: defects already present there are
    # pre-existing residuals (report, don't fail); only defects the
    # remediation pass INTRODUCED fail the gate.
    pre_existing = {}
    if baseline_path is not None and (missing or empty or orphans):
        base = baseline_defects(baseline_path, scan)
        if base is not None:
            def split(items):
                intro = [d for d in items if (d["page"] - 1, d["mcid"]) not in base]
                pre = [d for d in items if (d["page"] - 1, d["mcid"]) in base]
                return intro, pre
            missing, pre_existing["mcids_missing_from_stream"] = split(missing)
            empty, pre_existing["mcids_with_empty_text"] = split(empty)
            orphans, pre_existing["orphan_stream_mcids"] = split(orphans)
            pre_existing = {k: v for k, v in pre_existing.items() if v}

    # CHECK 3: text coverage. On a tagged page every painted character must
    # be inside a tagged MCID or an /Artifact section. Uncovered chars fail —
    # unless the original was ALSO tagged on that page with at least as many
    # uncovered chars (repair-of-tagged-doc case: pre-existing, report only).
    uncovered = [
        {"page": p + 1, "uncovered_chars": c["uncovered"],
         "text": (c["uncovered_text"][:EXCERPT * 2] + "…"
                  if len(c["uncovered_text"]) > EXCERPT * 2
                  else c["uncovered_text"])}
        for p, c in sorted(coverage.items()) if c["uncovered"]]
    pre_uncovered = []
    if uncovered and baseline_path is not None:
        base_cov = {}
        try:
            stream_mcid_text(baseline_path,
                             {u["page"] - 1 for u in uncovered}, base_cov)
        except Exception:
            base_cov = {}
        intro = []
        for u in uncovered:
            b = base_cov.get(u["page"] - 1)
            if b and b["tagged"] and u["uncovered_chars"] <= b["uncovered"]:
                pre_uncovered.append(u)
            else:
                intro.append(u)
        uncovered = intro

    result["text_coverage"] = {
        "pages_checked": len(coverage),
        "chars_tagged": sum(c["tagged"] for c in coverage.values()),
        "chars_artifact": sum(c["artifact"] for c in coverage.values()),
        "chars_uncovered": sum(c["uncovered"] for c in coverage.values()),
        "per_page": {str(p + 1): [c["tagged"], c["artifact"], c["uncovered"]]
                     for p, c in sorted(coverage.items())},
        "uncovered_text_ops": uncovered,
    }
    if pre_uncovered:
        result["text_coverage"]["pre_existing_uncovered"] = pre_uncovered

    result.update({
        "pages_scanned": sorted(p + 1 for p in scan),
        "struct_mcids": len(seen),
        "census": dict(sorted(census.items(), key=lambda kv: -kv[1])),
        "mcids_missing_from_stream": missing,
        "mcids_with_empty_text": empty,
        "orphan_stream_mcids": orphans,
        "non_text_elements": non_text,
        "duplicate_mcid_refs": dupes,
    })
    if pre_existing:
        result["pre_existing_defects"] = pre_existing
    ok = not (missing or empty or orphans or dupes or uncovered)

    if dump:
        bytype = {(p, m): s for p, m, s in leaves}
        print("--- MCID read-back dump (independent parser) ---", file=sys.stderr)
        for (p, m) in sorted(seen):
            t = texts.get((p, m), "")
            t = t if len(t) <= EXCERPT else t[:EXCERPT] + "…"
            print(f"p{p + 1:<4} mcid {m:<4} [{bytype[(p, m)]:<6}] {t!r}",
                  file=sys.stderr)
        print("--- per-page coverage: tagged/artifact/uncovered chars ---",
              file=sys.stderr)
        for p, c in sorted(coverage.items()):
            at = c["artifact_text"]
            at = at if len(at) <= EXCERPT * 2 else at[:EXCERPT * 2] + "…"
            print(f"p{p + 1:<4} {c['tagged']:>5}/{c['artifact']:>4}"
                  f"/{c['uncovered']:>3}  artifact: {at!r}", file=sys.stderr)
        print("--- end dump ---", file=sys.stderr)

    pdf.close()
    return result, ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("original", nargs="?", help="pre-remediation file (omit with --skip-visual)")
    ap.add_argument("modified", nargs="?", help="post-remediation file")
    ap.add_argument("--dpi", type=int, default=100)
    ap.add_argument("--pages", help="1-based, e.g. 7-9,12,13 (default: all)")
    ap.add_argument("--dump", action="store_true",
                    help="print full MCID->text listing to stderr for human review")
    ap.add_argument("--skip-visual", action="store_true",
                    help="read-back only (single file argument)")
    args = ap.parse_args()

    if args.skip_visual:
        mod = args.modified or args.original
        if not mod or (args.original and args.modified):
            ap.error("--skip-visual takes exactly one file")
    else:
        if not (args.original and args.modified):
            ap.error("need original and modified (or --skip-visual with one file)")
        mod = args.modified

    pages = parse_pages(args.pages) if args.pages else None
    out, ok = {}, True

    if not args.skip_visual:
        out["visual_identity"], v_ok = visual_identity(
            args.original, mod, args.dpi, pages)
        out["visual_identity"]["pass"] = v_ok
        ok &= v_ok

    out["tag_readback"], r_ok = read_back(
        mod, pages, args.dump,
        baseline_path=None if args.skip_visual else args.original)
    out["tag_readback"]["pass"] = r_ok
    ok &= r_ok

    out["verdict"] = "PASS" if ok else "FAIL — do not ship; investigate"
    print(json.dumps(out, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
