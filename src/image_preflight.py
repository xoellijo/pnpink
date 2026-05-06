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

