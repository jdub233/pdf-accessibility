#!/usr/bin/env python3
"""table_audit.py — audit every Table in a tagged PDF against Acrobat
Full Check's two table rules:

  Regularity: every TR must sum to the same effective column count
              (cell count weighted by /ColSpan).
  Headers:    Acrobat's heuristic — a table passes with a full-TH first
              row OR a full-TH first column.

Run it before AND after a remediation pass; on a clean build both
sections report zero. Fix recipe: references/combined-tagger-guide.md
("Table regularity and headers"). Independent of any tagger — walks the
structure tree with pikepdf only.

Usage:
    python3 table_audit.py FILE.pdf [--json report.json] [--verbose]
"""
import argparse
import json
import sys
from collections import Counter

import pikepdf


def kids(el):
    k = el.get("/K")
    if k is None:
        return []
    return list(k) if isinstance(k, pikepdf.Array) else [k]


def struct_kids(el):
    return [k for k in kids(el)
            if isinstance(k, pikepdf.Dictionary) and "/S" in k]


def get_attr(el, name):
    a = el.get("/A")
    if a is None:
        return None
    for d in (list(a) if isinstance(a, pikepdf.Array) else [a]):
        if isinstance(d, pikepdf.Dictionary) and name in d:
            return d.get(name)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--json", help="write the full per-table report here")
    ap.add_argument("--verbose", action="store_true",
                    help="print per-row cell types for flagged tables")
    args = ap.parse_args()

    pdf = pikepdf.open(args.pdf)
    page_index = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}

    def page_of(el):
        pg = el.get("/Pg")
        if pg is not None:
            return page_index.get(pg.objgen)
        for k in struct_kids(el):
            r = page_of(k)
            if r:
                return r
        return None

    tables = []

    def walk(el):
        if str(el.get("/S")) == "/Table":
            tables.append(el)
            return
        for k in struct_kids(el):
            walk(k)

    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        print("no StructTreeRoot — nothing to audit")
        sys.exit(1)
    rk = root.K
    for k in (list(rk) if isinstance(rk, pikepdf.Array) else [rk]):
        if isinstance(k, pikepdf.Dictionary):
            walk(k)

    report = []
    for t in tables:
        rows = []
        for tr in struct_kids(t):
            types, width = [], 0
            for c in struct_kids(tr):
                cs = get_attr(c, "/ColSpan")
                w = int(cs) if cs is not None else 1
                width += w
                types.append((str(c.get("/S")).strip("/"), w))
            rows.append({"types": types, "width": width})
        widths = Counter(r["width"] for r in rows)
        report.append({
            "page": page_of(t),
            "nrows": len(rows),
            "widths": dict(widths),
            "irregular": len(widths) > 1,
            "first_row_all_th": bool(rows) and all(
                s == "TH" for s, _ in rows[0]["types"]),
            "col1_all_th": bool(rows) and all(
                r["types"][0][0] == "TH" for r in rows if r["types"]),
            "rows": [{"w": r["width"],
                      "t": "|".join(f"{s}{'' if w == 1 else 'x%d' % w}"
                                    for s, w in r["types"])}
                     for r in rows],
        })

    bad_reg = [r for r in report if r["irregular"]]
    bad_hdr = [r for r in report
               if not (r["first_row_all_th"] or r["col1_all_th"])]

    print(f"TOTAL TABLES: {len(report)}")
    print(f"\nIRREGULAR (row widths differ): {len(bad_reg)}")
    for r in bad_reg:
        print(f"  p{r['page']}: rows={r['nrows']} widths={r['widths']}")
    print(f"\nHEADER-HEURISTIC RISK (no full-TH first row AND no full-TH "
          f"first column): {len(bad_hdr)}")
    for r in bad_hdr:
        print(f"  p{r['page']}: rows={r['nrows']} widths={r['widths']}")
    if args.verbose:
        for r in bad_reg + bad_hdr:
            print(f"\n--- p{r['page']} ---")
            for row in r["rows"]:
                print(f"  w={row['w']}  {row['t']}")
    if args.json:
        json.dump(report, open(args.json, "w"), indent=1)
        print(f"\nfull report: {args.json}")

    sys.exit(1 if (bad_reg or bad_hdr) else 0)


if __name__ == "__main__":
    main()
