# -*- coding: utf-8 -*-
"""Bitmap image preflight helpers for generated SVG outputs."""

from __future__ import annotations

from pathlib import Path

import deckmaker_paths as DMPATHS
import svg_chunks as SVGCHUNKS


def bitmap_size_px(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(str(path)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def effective_image_dpi_report(svg_path: str) -> dict:
    try:
        import inkex
        import svg as SVG

        source_info = SVGCHUNKS.resolve_chunked_output(svg_path)
        if source_info.get("svg_path"):
            svg_inputs = [str(source_info.get("svg_path") or "")]
        else:
            svg_inputs = [str(p) for p in (source_info.get("chunk_paths") or []) if str(p or "").strip()]
        if not svg_inputs:
            raise FileNotFoundError(svg_path)

        rows = []
        unresolved = 0
        unreadable = 0
        idx = 0
        for svg_input in svg_inputs:
            with open(svg_input, "rb") as fh:
                doc = inkex.load_svg(fh.read())
            root = doc.getroot()
            images = root.xpath(".//svg:image", namespaces=inkex.NSS)
            for im in images:
                idx += 1
                href = SVG.get_href(im)
                absref = im.get(SVG.SODI_ABSREF) or ""
                path = SVG._resolve_image_path(href, absref, DMPATHS.normalize(svg_input))
                if not path:
                    unresolved += 1
                    continue
                bitmap = bitmap_size_px(Path(path))
                if not bitmap:
                    unreadable += 1
                    continue
                w_px, h_px = bitmap
                placed_w = SVG.parse_len_px(root, im.get("width") or "0")
                placed_h = SVG.parse_len_px(root, im.get("height") or "0")
                if placed_w <= 0 or placed_h <= 0:
                    continue
                try:
                    t = SVG.composed_transform(im)
                    pts = [
                        t.apply_to_point((0, 0)),
                        t.apply_to_point((placed_w, 0)),
                        t.apply_to_point((0, placed_h)),
                        t.apply_to_point((placed_w, placed_h)),
                    ]
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1]) for p in pts]
                    placed_w = max(xs) - min(xs)
                    placed_h = max(ys) - min(ys)
                except Exception:
                    pass
                if placed_w <= 0 or placed_h <= 0:
                    continue
                dpi_x = w_px / (placed_w / 96.0)
                dpi_y = h_px / (placed_h / 96.0)
                dpi = min(dpi_x, dpi_y)
                rows.append({
                    "index": idx,
                    "id": im.get("id") or f"image-{idx}",
                    "path": str(path),
                    "file": Path(path).name,
                    "dpi": float(dpi),
                    "dpi_x": float(dpi_x),
                    "dpi_y": float(dpi_y),
                    "px": (w_px, h_px),
                    "placed_mm": (placed_w * 25.4 / 96.0, placed_h * 25.4 / 96.0),
                })
        rows.sort(key=lambda item: item["dpi"])
        return {
            "ok": True,
            "count": len(rows),
            "unresolved": unresolved,
            "unreadable": unreadable,
            "rows": rows,
            "low": [r for r in rows if r["dpi"] < 150.0],
            "high": [r for r in rows if r["dpi"] > 900.0],
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex), "count": 0, "rows": []}


def default_report_path(svg_path: str) -> str:
    p = Path(str(svg_path or ""))
    if p.suffix:
        return str(p.with_suffix(".image-preflight.txt"))
    return str(p) + ".image-preflight.txt"


def format_text_report(report: dict, *, source_svg: str = "") -> str:
    lines: list[str] = []
    lines.append("PnPInk image preflight")
    if source_svg:
        lines.append(f"Source SVG: {source_svg}")
    lines.append("")
    if not report.get("ok"):
        lines.append(f"ERROR: {report.get('error') or 'unknown error'}")
        return "\n".join(lines).rstrip() + "\n"

    rows = list(report.get("rows") or [])
    unresolved = int(report.get("unresolved") or 0)
    unreadable = int(report.get("unreadable") or 0)
    low = list(report.get("low") or [])
    high = list(report.get("high") or [])
    lines.append(f"Bitmap images: {len(rows)}")
    lines.append(f"Unresolved references: {unresolved}")
    lines.append(f"Unreadable files: {unreadable}")
    if rows:
        dpis = [float(r.get("dpi") or 0.0) for r in rows]
        lines.append(f"Effective DPI: min={min(dpis):.0f}, median={dpis[len(dpis)//2]:.0f}, max={max(dpis):.0f}")
    lines.append(f"Below 150 dpi: {len(low)}")
    lines.append(f"Above 900 dpi: {len(high)}")
    lines.append("")

    if not rows:
        lines.append("No linked bitmap images found.")
        return "\n".join(lines).rstrip() + "\n"

    lines.append("Images sorted by effective DPI:")
    lines.append("dpi\tdpi_x\tdpi_y\tpixels\tplaced_mm\tid\tfile\tpath")
    for item in rows:
        px = item.get("px") or (0, 0)
        mm = item.get("placed_mm") or (0.0, 0.0)
        lines.append(
            f"{float(item.get('dpi') or 0.0):.0f}\t"
            f"{float(item.get('dpi_x') or 0.0):.0f}\t"
            f"{float(item.get('dpi_y') or 0.0):.0f}\t"
            f"{int(px[0])}x{int(px[1])}\t"
            f"{float(mm[0]):.1f}x{float(mm[1]):.1f}\t"
            f"{item.get('id') or ''}\t"
            f"{item.get('file') or ''}\t"
            f"{item.get('path') or ''}"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_text_report(svg_path: str, out_path: str | None = None) -> str:
    report = effective_image_dpi_report(svg_path)
    target = out_path or default_report_path(svg_path)
    Path(target).write_text(format_text_report(report, source_svg=str(svg_path or "")), encoding="utf-8")
    return str(target)
