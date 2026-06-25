#!/usr/bin/env python3
"""Render Figure structure elements lacking /Alt to PNGs for visual review.

Usage:
  python render_figures.py document.pdf [--page N] [--dpi 150] [--out DIR]

Writes fig_p<page>_<n>.png files and prints a JSON manifest mapping each PNG
to its (page, bbox) key — use that key when injecting /Alt so the right
element gets the right text. Degenerate figures (bbox < 2pt in either
dimension) are listed but not rendered.
"""
import argparse, json, os, sys
import pikepdf
import fitz


def collect_figures(pdf):
    pagemap = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
    figs = []

    def bbox_of(e):
        a = e.get('/A', None)
        if isinstance(a, pikepdf.Dictionary):
            b = a.get('/BBox', None)
            if b is not None:
                return [float(x) for x in b]
        elif isinstance(a, pikepdf.Array):
            for x in a:
                if isinstance(x, pikepdf.Dictionary) and x.get('/BBox') is not None:
                    return [float(v) for v in x.get('/BBox')]
        return None

    def walk(e, d=0):
        if d > 60:
            return
        s = e.get('/S', None)
        if s is not None and str(s) == '/Figure':
            has_alt = e.get('/Alt', None) is not None
            pg = e.get('/Pg', None)
            page = pagemap.get(pg.objgen, -2) + 1 if pg is not None else -1
            figs.append({"page": page, "bbox": bbox_of(e), "has_alt": has_alt})
        k = e.get('/K', None)
        if k is None:
            return
        items = k if isinstance(k, pikepdf.Array) else [k]
        for i in items:
            if isinstance(i, pikepdf.Dictionary):
                walk(i, d + 1)

    root = pdf.Root.get('/StructTreeRoot', None)
    if root is None:
        return []
    k = root.get('/K', None)
    if k is not None:
        items = k if isinstance(k, pikepdf.Array) else [k]
        for el in items:
            if isinstance(el, pikepdf.Dictionary):
                walk(el)
    return figs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, help="only this 1-based page")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--out", default=".")
    ap.add_argument("--include-alted", action="store_true",
                    help="also render figures that already have alt")
    args = ap.parse_args()

    pdf = pikepdf.open(args.pdf)
    doc = fitz.open(args.pdf)
    figs = collect_figures(pdf)
    manifest = []
    counters = {}
    for f in figs:
        if args.page and f["page"] != args.page:
            continue
        if f["has_alt"] and not args.include_alted:
            continue
        entry = dict(f)
        bb = f["bbox"]
        if bb is None:
            entry["status"] = "no-bbox (cannot locate; inspect struct elem kids)"
        elif (bb[2] - bb[0] < 2) or (bb[3] - bb[1] < 2):
            entry["status"] = "degenerate (tagging artifact; consider Artifact role)"
        else:
            page = doc[f["page"] - 1]
            H = page.rect.height  # PDF y-up -> fitz y-down flip
            clip = fitz.Rect(bb[0] - 5, H - bb[3] - 5, bb[2] + 5, H - bb[1] + 5)
            n = counters[f["page"]] = counters.get(f["page"], 0) + 1
            name = f"fig_p{f['page']}_{n}.png"
            path = os.path.join(args.out, name)
            page.get_pixmap(dpi=args.dpi, clip=clip).save(path)
            entry["status"] = "rendered"
            entry["png"] = path
        manifest.append(entry)
    json.dump(manifest, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
