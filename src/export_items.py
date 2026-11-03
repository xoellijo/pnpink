# -*- coding: utf-8 -*-
"""Export generated card instances as cropped, reusable assets."""

from __future__ import annotations

import json
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree as ET

import inkscape_cli as INKSCAPE
import semantic_metadata as SEMANTIC
import svg_chunks as SVGCHUNKS
import temp_paths as TEMPPATHS


_CSS_PX_PER_UNIT = {
    "": 1.0,
    "px": 1.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
}

_ASSET_CHUNK_MAX_PAGES = 4
_ASSET_CHUNK_MAX_RECORDS = 32
_ASSET_CHUNK_MAX_BYTES = 64 * 1024 * 1024


def parse_item_selector(spec: str, item_count: int) -> tuple[str, list[int]]:
    if item_count <= 0:
        raise ValueError("No exportable items found")
    text = str(spec or "").strip()
    match = re.fullmatch(r"(.*?)(?:\[([^]]*)\]|(\*))?", text)
    if not match:
        raise ValueError(f"Invalid item selector: {spec}")
    prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", (match.group(1) or "items").strip()).strip(".") or "items"
    body = match.group(2)
    if body is None:
        return prefix, list(range(1, item_count + 1))
    selected: list[int] = []
    seen: set[int] = set()
    for token in [part.strip() for part in body.split(",") if part.strip()]:
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        values = []
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            step = 1 if end >= start else -1
            values = range(start, end + step, step)
        elif token.isdigit():
            values = [int(token)]
        else:
            raise ValueError(f"Invalid item index: {token}")
        for value in values:
            if not 1 <= value <= item_count:
                raise ValueError(f"Item index {value} outside 1-{item_count}")
            if value not in seen:
                seen.add(value)
                selected.append(value)
    if not selected:
        raise ValueError("No items selected")
    return prefix, selected


def _bbox(node) -> tuple[float, float, float, float]:
    parts = str(node.get("data-pnpink-cut-bbox") or "").replace(",", " ").split()
    if len(parts) != 4:
        raise ValueError(f"Generated item {node.get('id')!r} has no valid cut bbox")
    x, y, width, height = (float(value) for value in parts)
    if width <= 0 or height <= 0:
        raise ValueError(f"Generated item {node.get('id')!r} has an empty cut bbox")
    return x, y, width, height


def _length_css_px(value: str | None) -> float | None:
    match = re.fullmatch(
        r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)\s*",
        str(value or ""),
    )
    if not match:
        return None
    factor = _CSS_PX_PER_UNIT.get(match.group(2).lower())
    return float(match.group(1)) * factor if factor is not None else None


def _export_scale(root) -> tuple[float, float, float, float]:
    viewbox = str(root.get("viewBox") or "").replace(",", " ").split()
    if len(viewbox) != 4:
        return 0.0, 0.0, 1.0, 1.0
    view_x, view_y, view_width, view_height = (float(value) for value in viewbox)
    width_px = _length_css_px(root.get("width"))
    height_px = _length_css_px(root.get("height"))
    scale_x = width_px / view_width if width_px and view_width > 0 else 1.0
    scale_y = height_px / view_height if height_px and view_height > 0 else 1.0
    return view_x, view_y, scale_x, scale_y


def _export_area(
    bbox: tuple[float, float, float, float],
    scale: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    x, y, width, height = bbox
    view_x, view_y, scale_x, scale_y = scale
    x0 = (x - view_x) * scale_x
    y0 = (y - view_y) * scale_y
    return x0, y0, x0 + width * scale_x, y0 + height * scale_y


def discover_items(svg_path: str) -> tuple[list[dict], dict[int, list[dict]]]:
    root = ET.parse(svg_path).getroot()
    export_scale = _export_scale(root)
    fronts: list[dict] = []
    backs: dict[int, list[dict]] = {}
    for node in root.iter():
        face = str(node.get("data-pnpink-face") or "").strip().lower()
        if face not in {"front", "back"}:
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        item_index = int(str(node.get("data-pnpink-item-index") or len(fronts) + 1))
        bbox = _bbox(node)
        try:
            metadata = json.loads(str(node.get("data-pnpink-metadata") or "{}"))
            if not isinstance(metadata, dict):
                metadata = {}
        except (TypeError, ValueError):
            metadata = {}
        item = {
            "id": node_id,
            "itemIndex": item_index,
            "dataset": int(str(node.get("data-pnpink-dataset-index") or 0)),
            "row": int(str(node.get("data-pnpink-source-row") or node.get("data-pnpink-row-index") or 0)),
            "variant": int(str(node.get("data-pnpink-variant-index") or 1)),
            "copy": int(str(node.get("data-pnpink-copy-index") or 1)),
            "template": str(node.get("data-pnpink-template-id") or "card"),
            "contentKey": str(node.get("data-pnpink-content-key") or ""),
            "hasSemanticId": str(node.get("data-pnpink-has-semantic-id") or "") == "1",
            "bbox": bbox,
            "exportArea": _export_area(bbox, export_scale),
            "metadata": metadata,
        }
        if face == "front":
            fronts.append(item)
        else:
            backs.setdefault(item_index, []).append(item)
    fronts.sort(key=lambda item: item["itemIndex"])
    return fronts, backs


def _semantic_id(item: dict) -> str:
    return str((item.get("metadata") or {}).get("id") or "").strip()


def _automatic_id(item: dict) -> str:
    template = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(item.get("template") or "item")).strip(".-") or "item"
    return f"{template}-d{item.get('dataset', 0)}-r{item.get('row', 0)}-v{item.get('variant', 1)}"


def _partition_semantic_items(fronts: list[dict], backs: dict[int, list[dict]]) -> tuple[list[dict], dict[str, dict], bool]:
    all_items = list(fronts) + [item for values in backs.values() for item in values]
    has_id_column = any(item.get("hasSemanticId") for item in all_items)
    if not has_id_column:
        has_id_column = any("id" in (item.get("metadata") or {}) for item in all_items)
    if not has_id_column:
        return fronts, {}, False

    exportable: list[dict] = []
    back_rows: dict[str, dict] = {}
    back_sources: dict[str, tuple] = {}
    for item in fronts:
        semantic_id = _semantic_id(item)
        if not semantic_id:
            continue
        if not semantic_id.lower().endswith("@back"):
            exportable.append(item)
            continue
        base_id = semantic_id[:-5].strip()
        if not base_id:
            raise ValueError("$id '@back' needs a component ID or prefix before @back")
        source = (item.get("dataset"), item.get("row"), item.get("variant"), item.get("template"))
        if base_id in back_sources and back_sources[base_id] != source:
            raise ValueError(f"Duplicate back definition for $id prefix '{base_id}'")
        back_sources[base_id] = source
        back_rows.setdefault(base_id, item)
    return exportable, back_rows, True


def _id_back_for(front: dict, back_rows: dict[str, dict]) -> dict | None:
    front_id = _semantic_id(front)
    if not front_id:
        return None
    if front_id in back_rows:
        return back_rows[front_id]
    prefixes = [prefix for prefix in back_rows if front_id.startswith(prefix)]
    return back_rows[max(prefixes, key=len)] if prefixes else None


def _export_one(exe: str, svg_path: str, item: dict, target: str, export_type: str, dpi: int) -> tuple[int, str]:
    x0, y0, x1, y1 = item["exportArea"]
    argv = [
        exe,
        svg_path,
        f"--export-area={x0:.6f}:{y0:.6f}:{x1:.6f}:{y1:.6f}",
        f"--export-type={'jpg' if export_type == 'jpeg' else export_type}",
        f"--export-filename={target}",
    ]
    if export_type == "png":
        argv.append(f"--export-dpi={max(1, int(dpi))}")
    return INKSCAPE.run(argv, exe_dir=os.path.dirname(exe) or None, env=INKSCAPE.clean_launch_env())


def _preview_key(item: dict) -> str:
    return f'{item["dataset"]}:{item["row"]}:{item["variant"]}'


def _item_source_key(item: dict) -> tuple:
    return (
        int(item.get("itemIndex") or 0),
        int(item.get("dataset") or 0),
        int(item.get("row") or 0),
        int(item.get("variant") or 0),
        int(item.get("copy") or 0),
        str(item.get("template") or ""),
        str(item.get("contentKey") or ""),
    )


def _chunk_item_index(
    svg_path: str,
    inkscape_exe: str,
) -> tuple[dict[tuple[str, str | tuple], tuple[str, dict]], int]:
    work_dir = TEMPPATHS.make_work_dir("asset_export", stem=Path(svg_path).stem)
    plan = SVGCHUNKS.write_svg_chunks(
        svg_path,
        str(Path(work_dir) / f"{Path(svg_path).stem}.pdf"),
        inkscape_exe=inkscape_exe,
        artifact_dir=work_dir,
        target_chunk_bytes=_ASSET_CHUNK_MAX_BYTES,
        target_pages=_ASSET_CHUNK_MAX_PAGES,
        target_records=_ASSET_CHUNK_MAX_RECORDS,
    )
    chunk_paths = [chunk.svg_path for chunk in plan.get("chunks") or ()]

    if len(chunk_paths) <= 1 and chunk_paths and os.path.samefile(chunk_paths[0], svg_path):
        return {}, 1

    index: dict[tuple[str, str | tuple], tuple[str, dict]] = {}
    for chunk_path in chunk_paths:
        chunk_fronts, chunk_backs = discover_items(chunk_path)
        for item in chunk_fronts:
            index[("id", item["id"])] = (chunk_path, item)
            index[("front", _item_source_key(item))] = (chunk_path, item)
        for back_items in chunk_backs.values():
            for item in back_items:
                index[("id", item["id"])] = (chunk_path, item)
                index[("back", _item_source_key(item))] = (chunk_path, item)
    return index, max(1, len(chunk_paths))


def _chunk_item(
    item: dict,
    *,
    face: str,
    index: dict[tuple[str, str | tuple], tuple[str, dict]],
    default_svg: str,
) -> tuple[str, dict]:
    if not index:
        return default_svg, item
    match = index.get(("id", item["id"])) or index.get((face, _item_source_key(item)))
    if match is None:
        raise ValueError(f"Generated item {item['id']!r} was not found in any SVG partition")
    return match


def _resize_cached_png(source: str, target: Path, source_dpi: int, target_dpi: int) -> bool:
    try:
        from PIL import Image

        with Image.open(source) as image:
            ratio = max(1, int(source_dpi)) / max(1, int(target_dpi))
            size = (
                max(1, round(image.width / ratio)),
                max(1, round(image.height / ratio)),
            )
            if size != image.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            image.save(target, format="PNG", optimize=True, dpi=(target_dpi, target_dpi))
        return target.is_file() and target.stat().st_size > 0
    except (ImportError, OSError, ValueError):
        return False


def _rendered_png_error(path: Path) -> str | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            if "A" in image.getbands() and image.getchannel("A").getextrema() == (0, 0):
                return "image is fully transparent"
    except (ImportError, OSError, ValueError) as ex:
        return f"image cannot be decoded: {ex}"
    return None


def export_items_via_inkscape(
    svg_path: str,
    out_path: str,
    *,
    export_type: str,
    item_spec: str,
    export_dpi: int = 300,
    output_dir: str | None = None,
    on_item_created=None,
    on_progress=None,
    cached_pngs: dict[str, str] | None = None,
    cached_png_dpi: int = 600,
) -> tuple[bool, dict]:
    started = time.perf_counter()
    try:
        svg_path = os.path.abspath(svg_path)
        out_path = os.path.abspath(out_path)
        fronts, backs = discover_items(svg_path)
        exportable_fronts, id_back_rows, has_id_column = _partition_semantic_items(fronts, backs)
        prefix, selected_indices = parse_item_selector(item_spec, len(exportable_fronts))
        selected = [exportable_fronts[index - 1] for index in selected_indices]
        grouped: OrderedDict[tuple, list[dict]] = OrderedDict()
        for item in selected:
            key = (item["dataset"], item["row"], item["variant"], item["template"])
            grouped.setdefault(key, []).append(item)
        export_name = str(export_type or "png").strip().lower()
        extension = "jpg" if export_name == "jpeg" else export_name
        output_directory = Path(output_dir) if output_dir else Path(os.path.splitext(out_path)[0] + "_items")
        output_directory.mkdir(parents=True, exist_ok=True)
        records = []
        png_jobs: list[tuple[dict, Path, bool, str]] = []
        asset_chunk_count = 1
        cached_pngs = cached_pngs or {}
        back_assets: dict[str, str] = {}
        def matching_back(front: dict) -> dict | None:
            id_back = _id_back_for(front, id_back_rows)
            if id_back is not None:
                return id_back
            generated_backs = backs.get(front["itemIndex"], [])
            return generated_backs[0] if generated_backs else None

        def back_asset_key(back: dict) -> str:
            semantic_id = _semantic_id(back)
            if semantic_id.lower().endswith("@back"):
                return semantic_id
            return back.get("contentKey") or semantic_id or back["id"]

        unique_back_keys = {
            back_asset_key(back)
            for front in selected
            for back in [matching_back(front)]
            if back is not None
        }
        shared_back = len(unique_back_keys) == 1
        used_component_ids: dict[str, tuple] = {}
        for output_index, copies in enumerate(grouped.values(), start=1):
            representative = copies[0]
            component_id = _semantic_id(representative) if has_id_column else _automatic_id(representative)
            source_key = (
                representative["dataset"], representative["row"],
                representative["variant"], representative["template"],
            )
            if component_id in used_component_ids and used_component_ids[component_id] != source_key:
                raise ValueError(f"Duplicate component $id '{component_id}'")
            used_component_ids[component_id] = source_key
            front_name = f"{prefix}{output_index}.{extension}"
            front_path = output_directory / front_name
            if export_name == "png":
                png_jobs.append((representative, front_path, True, "front"))
            else:
                exe = INKSCAPE.find_executable()
                if not exe:
                    return False, {"error": "Inkscape executable not found"}
                rc, message = _export_one(exe, svg_path, representative, str(front_path), export_name, export_dpi)
                if rc != 0 or not front_path.is_file() or front_path.stat().st_size <= 0:
                    return False, {"error": f"Item export failed for {representative['id']}: {message}"}
            matching_backs = [matching_back(representative)]
            matching_backs = [back for back in matching_backs if back is not None]
            back_name = ""
            if matching_backs:
                back = matching_backs[0]
                back_key = back_asset_key(back)
                if back_key in back_assets:
                    back_name = back_assets[back_key]
                else:
                    back_name = f"{prefix}_back.{extension}" if shared_back else f"{prefix}{output_index}_back.{extension}"
                    back_path = output_directory / back_name
                    if export_name == "png":
                        semantic_back = _semantic_id(back).lower().endswith("@back")
                        png_jobs.append((back, back_path, semantic_back, "back"))
                    else:
                        exe = INKSCAPE.find_executable()
                        if not exe:
                            return False, {"error": "Inkscape executable not found"}
                        rc, message = _export_one(exe, svg_path, back, str(back_path), export_name, export_dpi)
                        if rc != 0 or not back_path.is_file() or back_path.stat().st_size <= 0:
                            return False, {"error": f"Back export failed for {back['id']}: {message}"}
                    back_assets[back_key] = back_name
            record = {
                "id": component_id,
                "name": f"{prefix}{output_index}",
                "front": front_name,
                "back": back_name or None,
                "copies": len(copies),
                "source": {
                    "dataset": representative["dataset"],
                    "row": representative["row"],
                    "variant": representative["variant"],
                },
            }
            record.update(SEMANTIC.manifest_fields(representative.get("metadata") or {}))
            records.append(record)
        if png_jobs:
            completed_jobs = 0
            total_jobs = len(png_jobs)
            if on_progress:
                on_progress("Rendering Play assets", completed_jobs, total_jobs)
            remaining_jobs = []
            for item, target, allow_cache, face in png_jobs:
                try:
                    target.unlink(missing_ok=True)
                except Exception:
                    pass
                cached = cached_pngs.get(_preview_key(item)) if allow_cache else None
                if not cached or not _resize_cached_png(cached, target, cached_png_dpi, export_dpi):
                    remaining_jobs.append((item, target, face))
                else:
                    completed_jobs += 1
                    if on_progress:
                        on_progress("Rendering Play assets", completed_jobs, total_jobs)
            if remaining_jobs:
                exe = INKSCAPE.find_executable()
                if not exe:
                    return False, {"error": "Inkscape executable not found"}
                chunk_index, asset_chunk_count = _chunk_item_index(svg_path, exe)
                jobs_by_svg: OrderedDict[str, list[tuple[dict, Path]]] = OrderedDict()
                for item, target, face in remaining_jobs:
                    source_svg, source_item = _chunk_item(
                        item,
                        face=face,
                        index=chunk_index,
                        default_svg=svg_path,
                    )
                    jobs_by_svg.setdefault(source_svg, []).append((source_item, target))

                for source_svg, source_jobs in jobs_by_svg.items():
                    commands = INKSCAPE.build_shell_export_area_png_commands([
                        (source_svg, item["exportArea"], str(target), max(1, int(export_dpi)))
                        for item, target in source_jobs
                    ])
                    rc, message = INKSCAPE.run_shell_commands(
                        exe,
                        commands,
                        exe_dir=os.path.dirname(exe) or None,
                        env=INKSCAPE.clean_launch_env(),
                    )
                    failed = next(
                        (item for item, target in source_jobs if not target.is_file() or target.stat().st_size <= 0),
                        None,
                    )
                    invalid = next(
                        (
                            (item, error)
                            for item, target in source_jobs
                            if target.is_file() and (error := _rendered_png_error(target))
                        ),
                        None,
                    )
                    if rc != 0 or failed is not None:
                        failed_id = failed["id"] if failed is not None else source_jobs[0][0]["id"]
                        return False, {"error": f"Item export failed for {failed_id}: {message}"}
                    if invalid is not None:
                        invalid_item, invalid_error = invalid
                        return False, {"error": f"Item export failed for {invalid_item['id']}: {invalid_error}"}
                    completed_jobs += len(source_jobs)
                    if on_progress:
                        on_progress("Rendering Play assets", completed_jobs, total_jobs)
        if on_item_created:
            for record in records:
                on_item_created(str(output_directory / record["front"]), record["name"])
        manifest_path = output_directory / "items.json"
        manifest_path.write_text(json.dumps({"schema": 2, "items": records}, indent=2, ensure_ascii=False), encoding="utf-8")
        return True, {
            "elapsed_s": time.perf_counter() - started,
            "item_count": len(records),
            "physical_count": len(selected),
            "chunk_count": asset_chunk_count,
            "output_path": str(output_directory),
            "manifest": str(manifest_path),
            "results": records,
        }
    except Exception as ex:
        return False, {"error": str(ex), "elapsed_s": time.perf_counter() - started}
