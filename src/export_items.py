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


def parse_item_selector(spec: str, item_count: int) -> tuple[str, list[int]]:
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


def discover_items(svg_path: str) -> tuple[list[dict], dict[int, list[dict]]]:
    root = ET.parse(svg_path).getroot()
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
        item = {
            "id": node_id,
            "itemIndex": item_index,
            "dataset": int(str(node.get("data-pnpink-dataset-index") or 0)),
            "row": int(str(node.get("data-pnpink-source-row") or node.get("data-pnpink-row-index") or 0)),
            "variant": int(str(node.get("data-pnpink-variant-index") or 1)),
            "copy": int(str(node.get("data-pnpink-copy-index") or 1)),
            "template": str(node.get("data-pnpink-template-id") or "card"),
            "contentKey": str(node.get("data-pnpink-content-key") or ""),
            "bbox": _bbox(node),
        }
        if face == "front":
            fronts.append(item)
        else:
            backs.setdefault(item_index, []).append(item)
    fronts.sort(key=lambda item: item["itemIndex"])
    return fronts, backs


def _export_one(exe: str, svg_path: str, item: dict, target: str, export_type: str, dpi: int) -> tuple[int, str]:
    x, y, width, height = item["bbox"]
    argv = [
        exe,
        svg_path,
        f"--export-id={item['id']}",
        "--export-id-only",
        f"--export-area={x:.6f}:{y:.6f}:{x + width:.6f}:{y + height:.6f}",
        f"--export-type={'jpg' if export_type == 'jpeg' else export_type}",
        f"--export-filename={target}",
    ]
    if export_type == "png":
        argv.append(f"--export-dpi={max(1, int(dpi))}")
    return INKSCAPE.run(argv, exe_dir=os.path.dirname(exe) or None, env=INKSCAPE.clean_launch_env())


def export_items_via_inkscape(svg_path: str, out_path: str, *, export_type: str, item_spec: str, export_dpi: int = 300, on_item_created=None) -> tuple[bool, dict]:
    started = time.perf_counter()
    try:
        svg_path = os.path.abspath(svg_path)
        out_path = os.path.abspath(out_path)
        fronts, backs = discover_items(svg_path)
        prefix, selected_indices = parse_item_selector(item_spec, len(fronts))
        selected = [fronts[index - 1] for index in selected_indices]
        grouped: OrderedDict[tuple, list[dict]] = OrderedDict()
        for item in selected:
            key = (item["dataset"], item["row"], item["variant"], item["template"])
            grouped.setdefault(key, []).append(item)
        export_name = str(export_type or "png").strip().lower()
        extension = "jpg" if export_name == "jpeg" else export_name
        output_dir = Path(os.path.splitext(out_path)[0] + "_items")
        output_dir.mkdir(parents=True, exist_ok=True)
        exe = INKSCAPE.find_executable()
        if not exe:
            return False, {"error": "Inkscape executable not found"}
        records = []
        back_assets: dict[str, str] = {}
        unique_back_keys = {
            back["contentKey"] or back["id"]
            for front in selected for back in backs.get(front["itemIndex"], [])[:1]
        }
        shared_back = len(unique_back_keys) == 1
        for output_index, copies in enumerate(grouped.values(), start=1):
            representative = copies[0]
            front_name = f"{prefix}{output_index}.{extension}"
            front_path = output_dir / front_name
            rc, message = _export_one(exe, svg_path, representative, str(front_path), export_name, export_dpi)
            if rc != 0 or not front_path.is_file() or front_path.stat().st_size <= 0:
                return False, {"error": f"Item export failed for {representative['id']}: {message}"}
            matching_backs = backs.get(representative["itemIndex"], [])
            back_name = ""
            if matching_backs:
                back = matching_backs[0]
                back_key = back["contentKey"] or back["id"]
                if back_key in back_assets:
                    back_name = back_assets[back_key]
                else:
                    back_name = f"{prefix}_back.{extension}" if shared_back else f"{prefix}{output_index}_back.{extension}"
                    back_path = output_dir / back_name
                    rc, message = _export_one(exe, svg_path, back, str(back_path), export_name, export_dpi)
                    if rc != 0 or not back_path.is_file() or back_path.stat().st_size <= 0:
                        return False, {"error": f"Back export failed for {back['id']}: {message}"}
                    back_assets[back_key] = back_name
            record = {
                "name": f"{prefix}{output_index}",
                "front": front_name,
                "back": back_name or None,
                "copies": len(copies),
                "sourceItems": [item["itemIndex"] for item in copies],
                "dataset": representative["dataset"],
                "row": representative["row"],
                "variant": representative["variant"],
            }
            records.append(record)
            if on_item_created:
                on_item_created(str(front_path), record["name"])
        manifest_path = output_dir / "items.json"
        manifest_path.write_text(json.dumps({"schema": 1, "items": records}, indent=2), encoding="utf-8")
        return True, {
            "elapsed_s": time.perf_counter() - started,
            "item_count": len(records),
            "physical_count": len(selected),
            "output_path": str(output_dir),
            "manifest": str(manifest_path),
            "results": records,
        }
    except Exception as ex:
        return False, {"error": str(ex), "elapsed_s": time.perf_counter() - started}
