"""PDF-export optimization for raster spritesheet frames."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import app_paths as APPPATHS
import log as LOG


_l = LOG
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_TRANSLATE_RE = re.compile(r"translate\(\s*([-+0-9.eE]+)[, ]+\s*([-+0-9.eE]+)\s*\)")
_URL_REF_RE = re.compile(r"url\(\s*#([^\)\s]+)\s*\)")
_CACHE_VERSION = 1


def _local_name(node) -> str:
    tag = str(getattr(node, "tag", "") or "")
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _image_path(node, svg_path: str) -> Path | None:
    absref = str(node.get("{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}absref") or "").strip()
    if absref and Path(absref).is_file():
        return Path(absref).resolve()
    href = str(node.get("href") or node.get(_XLINK_HREF) or "").strip()
    if not href or href.startswith("data:"):
        return None
    if href.lower().startswith("file:"):
        parsed = urlparse(href)
        value = unquote(parsed.path or "")
        if os.name == "nt" and value.startswith("/") and len(value) > 3 and value[2] == ":":
            value = value[1:]
        path = Path(value)
    else:
        path = Path(unquote(href))
        if not path.is_absolute():
            path = Path(svg_path).parent / path
    return path.resolve() if path.is_file() else None


def _cache_path(source: Path, pixel_box: tuple[int, int, int, int], fallback_dir: Path) -> Path:
    stat = source.stat()
    payload = {
        "version": _CACHE_VERSION,
        "source": os.path.normcase(str(source.resolve())),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "box": list(pixel_box),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    directory = APPPATHS.data_path("cache", "spritesheet_crops", digest[:2])
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = fallback_dir / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.png"


def materialize_spritesheet_frames(svg_path: str, output_path: str) -> dict:
    """Replace clipped full-page images with cached physical frame crops."""
    import inkex
    from PIL import Image
    import svg as SVG
    import svg_chunks as SVGCHUNKS

    started = time.perf_counter()
    with open(svg_path, "rb") as handle:
        doc = inkex.load_svg(handle.read())
    root = doc.getroot()
    by_id = {
        str(el.get("id") or "").strip(): el
        for el in root.iter()
        if str(el.get("id") or "").strip()
    }
    opened_images: dict[str, object] = {}
    crop_paths: dict[tuple, Path] = {}
    rewritten = 0
    cache_hits = 0
    cache_misses = 0
    crop_bytes = 0
    fallback_cache_dir = Path(output_path).parent / "spritesheet-crops"
    try:
        for group in list(root.iter()):
            if not str(group.get("data-origid") or "").startswith("sp_"):
                continue
            uses = [child for child in list(group) if _local_name(child) == "use"]
            if len(uses) != 1:
                continue
            use = uses[0]
            href = str(use.get("href") or use.get(_XLINK_HREF) or "").strip()
            base_image = by_id.get(href[1:]) if href.startswith("#") else None
            clip_match = _URL_REF_RE.fullmatch(str(group.get("clip-path") or "").strip())
            clip = by_id.get(clip_match.group(1)) if clip_match else None
            rect = next((el for el in clip.iter() if _local_name(el) == "rect"), None) if clip is not None else None
            translate = _TRANSLATE_RE.fullmatch(str(use.get("transform") or "").strip())
            if base_image is None or rect is None or translate is None:
                continue
            source = _image_path(base_image, svg_path)
            if source is None:
                continue

            x0 = -float(translate.group(1))
            y0 = -float(translate.group(2))
            frame_width = float(rect.get("width") or 0)
            frame_height = float(rect.get("height") or 0)
            sheet_width = float(base_image.get("width") or 0)
            sheet_height = float(base_image.get("height") or 0)
            if min(frame_width, frame_height, sheet_width, sheet_height) <= 0:
                continue

            source_key = os.path.normcase(str(source))
            image = opened_images.get(source_key)
            if image is None:
                image = Image.open(source)
                image.load()
                opened_images[source_key] = image
            scale_x = image.width / sheet_width
            scale_y = image.height / sheet_height
            pixel_box = (
                max(0, math.floor(x0 * scale_x)),
                max(0, math.floor(y0 * scale_y)),
                min(image.width, math.ceil((x0 + frame_width) * scale_x)),
                min(image.height, math.ceil((y0 + frame_height) * scale_y)),
            )
            crop_key = (source_key, pixel_box)
            crop_path = crop_paths.get(crop_key)
            if crop_path is None:
                crop_path = _cache_path(source, pixel_box, fallback_cache_dir)
                if crop_path.is_file() and crop_path.stat().st_size > 0:
                    cache_hits += 1
                else:
                    temporary = crop_path.with_suffix(".tmp.png")
                    image.crop(pixel_box).save(temporary, format="PNG", compress_level=1)
                    os.replace(temporary, crop_path)
                    cache_misses += 1
                crop_paths[crop_key] = crop_path
                crop_bytes += int(crop_path.stat().st_size)

            replacement = SVG.etree.Element(inkex.addNS("image", "svg"))
            replacement.set("x", str((pixel_box[0] / scale_x) - x0))
            replacement.set("y", str((pixel_box[1] / scale_y) - y0))
            replacement.set("width", str((pixel_box[2] - pixel_box[0]) / scale_x))
            replacement.set("height", str((pixel_box[3] - pixel_box[1]) / scale_y))
            replacement.set("preserveAspectRatio", "none")
            SVG.set_file_href(replacement, crop_path, touch_plain=True, touch_absref=True)
            group.replace(use, replacement)
            rewritten += 1
    finally:
        for image in opened_images.values():
            try:
                image.close()
            except Exception:
                pass

    if rewritten <= 0:
        return {
            "svg_path": svg_path,
            "rewritten": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "crop_bytes": 0,
            "elapsed_s": time.perf_counter() - started,
        }

    prune_info = SVGCHUNKS.prune_unreferenced_generated_assets(root)
    removed_defs = int(prune_info.get("removed") or 0)
    try:
        doc.write(output_path, encoding="utf-8", xml_declaration=True)
    except TypeError:
        doc.write(output_path)
    elapsed = time.perf_counter() - started
    _l.i(
        "[spritesheet.export] materialized frames=%d unique=%d cache_hits=%d cache_misses=%d "
        "crop_bytes=%d removed_defs=%d elapsed=%.2fs svg='%s'",
        rewritten,
        len(crop_paths),
        cache_hits,
        cache_misses,
        crop_bytes,
        removed_defs,
        elapsed,
        output_path,
    )
    return {
        "svg_path": output_path,
        "rewritten": rewritten,
        "unique_crops": len(crop_paths),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "crop_bytes": crop_bytes,
        "removed_defs": removed_defs,
        "elapsed_s": elapsed,
    }
