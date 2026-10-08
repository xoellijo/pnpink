# -*- coding: utf-8 -*-
"""Selection inspection and preview rendering for DeckMaker's web spritesheet editor."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

import inkex

import deckmaker_paths as DMPATHS
import inkscape_cli as INKSCAPE
import svg
import temp_paths as TEMPPATHS


PREVIEW_DPI = 96


@dataclass(frozen=True)
class SpriteSelection:
    node_id: str
    width_mm: float
    height_mm: float
    preview_source: str

    def public(self, revision: int) -> dict:
        return {
            "available": True,
            "nodeId": self.node_id,
            "widthMm": round(self.width_mm, 6),
            "heightMm": round(self.height_mm, 6),
            "previewUrl": f"/api/spritesheet/preview?rev={int(revision)}",
            "message": "",
        }


def unavailable(message: str) -> dict:
    return {
        "available": False,
        "nodeId": "",
        "widthMm": 0,
        "heightMm": 0,
        "previewUrl": "",
        "message": str(message or "Select exactly one SVG object in Inkscape."),
    }


def _node(svg_path: str, node_id: str):
    try:
        with open(svg_path, "rb") as handle:
            document = inkex.load_svg(handle.read())
        root = document.getroot()
        matches = [node for node in root.xpath(".//*[@id]") if str(node.get("id") or "") == node_id]
        return root, matches[0] if matches else None
    except Exception:
        return None, None


def inspect_selection(
    template: str,
    snapshot: str,
    selected_ids: tuple[str, ...],
) -> tuple[SpriteSelection | None, str]:
    ids = tuple(value for value in selected_ids if value)
    if len(ids) != 1:
        return None, "Select exactly one image, group or object in Inkscape, then reopen DeckMaker Web."
    node_id = ids[0]
    candidates = [path for path in (snapshot, template) if path and os.path.isfile(path)]
    measured = None
    for source in candidates:
        root, node = _node(source, node_id)
        if node is None:
            continue
        try:
            _x, _y, width, height = svg.visual_bbox(node)
            px_per_mm = float(root.unittouu("1mm"))
            if width > 0 and height > 0 and px_per_mm > 0:
                measured = (float(width) / px_per_mm, float(height) / px_per_mm)
                break
        except Exception:
            continue
    if not measured:
        return None, f"The selected object '{node_id}' has no measurable visual bounds."

    preview_source = ""
    for source in (template, snapshot):
        if source and os.path.isfile(source) and _node(source, node_id)[1] is not None:
            preview_source = source
            break
    if not preview_source:
        return None, f"The selected object '{node_id}' is not available in the SVG snapshot."
    return SpriteSelection(node_id, measured[0], measured[1], preview_source), ""


def render_preview(selection: SpriteSelection) -> str:
    source = DMPATHS.normalize(selection.preview_source)
    stamp = os.stat(source).st_mtime_ns
    digest = hashlib.sha1(f"{source}|{selection.node_id}|{stamp}|{PREVIEW_DPI}".encode("utf-8")).hexdigest()[:16]
    directory = Path(TEMPPATHS.named_dir("deckmaker_spritesheet", stem=TEMPPATHS.stem_for_path(source)))
    output = directory / f"{digest}.png"
    if output.is_file() and output.stat().st_size > 16:
        return str(output)
    executable = INKSCAPE.find_executable()
    shell = INKSCAPE.shell_executable(executable)
    if not shell:
        raise RuntimeError("Inkscape executable not found")
    commands = INKSCAPE.build_shell_export_id_png_commands(
        source, [(selection.node_id, str(output), PREVIEW_DPI)],
    )
    code, message = INKSCAPE.run_shell_commands(
        shell,
        commands,
        exe_dir=INKSCAPE.executable_dir(executable),
        env=INKSCAPE.clean_launch_env(),
        timeout_s=20.0,
    )
    if code != 0 or not output.is_file() or output.stat().st_size <= 16:
        raise RuntimeError(f"Could not render spritesheet preview: {str(message or '').strip()}")
    return str(output)
