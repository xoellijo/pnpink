#!/usr/bin/env python3
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path


def _proto_content_bbox(page, fitz):
    bbox = None
    for dr in page.get_drawings():
        rr = dr.get("rect")
        if not rr:
            continue
        r = fitz.Rect(rr)
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        bbox = r if bbox is None else (bbox | r)
    for b in page.get_text("blocks"):
        r = fitz.Rect(b[:4])
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        bbox = r if bbox is None else (bbox | r)
    for im in page.get_images(full=True):
        try:
            xref = int(im[0])
        except Exception:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        for rr in rects:
            r = fitz.Rect(rr)
            if r.is_empty or r.width <= 0 or r.height <= 0:
                continue
            bbox = r if bbox is None else (bbox | r)
    if bbox is None:
        bbox = page.rect
    return bbox


def _layout_key(page_rect, rects: list) -> tuple:
    rounded_page = tuple(round(float(v), 3) for v in (page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1))
    rounded_rects = tuple(tuple(round(float(v), 3) for v in r) for r in rects)
    return (rounded_page, rounded_rects)


def _build_overlay_templates(fitz, bdoc, pdoc, all_rects_pt, src_bbox, supports_reuse: bool):
    templates = fitz.open()
    template_by_key = {}
    template_by_page: list[int | None] = []

    for i in range(bdoc.page_count):
        rects = all_rects_pt[i] if i < len(all_rects_pt) else []
        if not rects:
            template_by_page.append(None)
            continue

        page_rect = bdoc[i].rect
        key = _layout_key(page_rect, rects)
        template_index = template_by_key.get(key)
        if template_index is None:
            tpage = templates.new_page(width=page_rect.width, height=page_rect.height)
            reuse = 0
            for r in rects:
                rr = fitz.Rect(*r)
                if supports_reuse:
                    reuse = tpage.show_pdf_page(rr, pdoc, 0, keep_proportion=False, overlay=True, clip=src_bbox, reuse_xref=reuse)
                else:
                    tpage.show_pdf_page(rr, pdoc, 0, keep_proportion=False, overlay=True, clip=src_bbox)
            template_index = templates.page_count - 1
            template_by_key[key] = template_index
        template_by_page.append(template_index)

    return templates, template_by_page, len(template_by_key)


def run_overlay(job_dir: Path, out_pdf: Path) -> dict:
    import fitz

    placements = json.loads((job_dir / "placements.json").read_text(encoding="utf-8"))
    base_path = Path(str(placements.get("base_pdf") or "")).expanduser()
    if not base_path.is_file():
        base_path = job_dir / "base.pdf"
    base_pdf = base_path.read_bytes()
    proto_pdf = (job_dir / "proto.pdf").read_bytes()

    all_rects_pt = placements["all_rects_pt"]

    bdoc = fitz.open(stream=base_pdf, filetype="pdf")
    pdoc = fitz.open(stream=proto_pdf, filetype="pdf")
    supports_reuse = "reuse_xref" in inspect.signature(fitz.Page.show_pdf_page).parameters
    proto_src_rect_pt = placements.get("proto_src_rect_pt")
    if isinstance(proto_src_rect_pt, list) and len(proto_src_rect_pt) == 4:
        src_bbox = fitz.Rect(*proto_src_rect_pt)
    else:
        src_bbox = _proto_content_bbox(pdoc[0], fitz)

    templates, template_by_page, template_count = _build_overlay_templates(
        fitz, bdoc, pdoc, all_rects_pt, src_bbox, supports_reuse
    )

    applied = 0
    reuse_by_template: dict[int, int] = {}
    for i in range(bdoc.page_count):
        template_index = template_by_page[i] if i < len(template_by_page) else None
        if template_index is None:
            continue
        page = bdoc[i]
        if supports_reuse:
            reuse = reuse_by_template.get(template_index, 0)
            reuse_by_template[template_index] = page.show_pdf_page(
                page.rect, templates, template_index, keep_proportion=False, overlay=True, reuse_xref=reuse
            )
        else:
            page.show_pdf_page(page.rect, templates, template_index, keep_proportion=False, overlay=True)
        applied += len(all_rects_pt[i]) if i < len(all_rects_pt) else 0

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    bdoc.save(str(out_pdf), garbage=3, deflate=True)
    templates.close()
    bdoc.close()
    pdoc.close()

    return {"applied_instances": applied, "overlay_templates": template_count, "output_pdf": str(out_pdf)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Apply PyMuPDF overlay job")
    p.add_argument("job_dir")
    p.add_argument("output_pdf")
    args = p.parse_args(argv)

    job = Path(args.job_dir).expanduser().resolve()
    out = Path(args.output_pdf).expanduser().resolve()
    for n in ("proto.pdf", "placements.json"):
        if not (job / n).is_file():
            raise FileNotFoundError(f"Missing {n} in {job}")
    placements = json.loads((job / "placements.json").read_text(encoding="utf-8"))
    base_path = Path(str(placements.get("base_pdf") or "")).expanduser()
    if not base_path.is_file() and not (job / "base.pdf").is_file():
        raise FileNotFoundError(f"Missing base.pdf in {job} and base_pdf is not valid in placements.json")

    info = run_overlay(job, out)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
