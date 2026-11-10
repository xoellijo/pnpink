# -*- coding: utf-8 -*-
"""Persistent, isolated Inkscape window used as an SVG preview backend."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import deque
import fnmatch
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app_paths import data_path
import inkscape_cli as INKSCAPE
import log as LOG
import process_io as PROCESS_IO
import temp_paths as TEMPPATHS


_PROMPT = "> "
_RELOAD_ACTION = "file-rebase:true"
_INITIAL_VIEW_ACTIONS = ("zoom-page",)
_INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
_SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_PREVIEW_PAGE_WIDTH_MM = 210.0
_PREVIEW_PAGE_HEIGHT_MM = 148.0
_WINDOW_SECTIONS = ("window", "fullscreen", "focus")
_WINDOW_VISIBILITY = {
    "menu": "0",
    "commands": "0",
    "snaptoolbox": "0",
    "toppanel": "0",
    "toolbox": "0",
    "statusbar": "0",
    "panels": "0",
    "rulers": "1",
    "scrollbars": "0",
}
_PROFILE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<inkscape version="1">
  <group id="window">
    <group id="menu" state="0"/>
    <group id="commands" state="0"/>
    <group id="snaptoolbox" state="0"/>
    <group id="toppanel" state="0"/>
    <group id="toolbox" state="0"/>
    <group id="statusbar" state="0"/>
    <group id="panels" state="0"/>
    <group id="rulers" state="1"/>
    <group id="scrollbars" state="0"/>
  </group>
  <group id="fullscreen">
    <group id="menu" state="0"/>
    <group id="commands" state="0"/>
    <group id="snaptoolbox" state="0"/>
    <group id="toppanel" state="0"/>
    <group id="toolbox" state="0"/>
    <group id="statusbar" state="0"/>
    <group id="panels" state="0"/>
    <group id="rulers" state="1"/>
    <group id="scrollbars" state="0"/>
  </group>
  <group id="focus">
    <group id="menu" state="0"/>
    <group id="commands" state="0"/>
    <group id="snaptoolbox" state="0"/>
    <group id="toppanel" state="0"/>
    <group id="toolbox" state="0"/>
    <group id="statusbar" state="0"/>
    <group id="panels" state="0"/>
    <group id="rulers" state="1"/>
    <group id="scrollbars" state="0"/>
  </group>
  <group id="dialogs">
    <group id="layers" maxDepth="20" w="180" h="500"/>
    <group id="objects" expand_to_layer="1"/>
  </group>
  <group id="options">
    <group id="filterquality" value="2"/>
    <group id="savewindowgeometry" value="2"/>
    <group id="maxrecentdocuments" value="0"/>
    <group id="defaultwindowsize" value="-1"/>
    <group id="dialogtype" value="1"/>
    <group id="dialogsskiptaskbar" value="1"/>
    <group id="boot" showsplash="0" showwelcome="0"/>
    <group id="autosave" enable="0" interval="10" path="" max="0"/>
  </group>
  <group id="geometry" width="1100" height="720" x="80" y="80" maximized="0" fullscreen="0"/>
</inkscape>
"""

def _child_group(parent: ET.Element, group_id: str) -> ET.Element:
    group = parent.find(f"./group[@id='{group_id}']")
    return group if group is not None else ET.SubElement(parent, "group", {"id": group_id})


def _configure_profile(root: ET.Element) -> None:
    for section_id in _WINDOW_SECTIONS:
        section = _child_group(root, section_id)
        for control_id, state in _WINDOW_VISIBILITY.items():
            _child_group(section, control_id).set("state", state)

    options = _child_group(root, "options")
    for option_id, attributes in {
        "filterquality": {"value": "2"},
        "savewindowgeometry": {"value": "2"},
        "maxrecentdocuments": {"value": "0"},
        "defaultwindowsize": {"value": "-1"},
        "dialogtype": {"value": "1"},
        "dialogsskiptaskbar": {"value": "1"},
        "autosave": {"enable": "0", "interval": "10", "path": "", "max": "0"},
    }.items():
        _child_group(options, option_id).attrib.update(attributes)
    _child_group(options, "boot").attrib.update({
        "showsplash": "0",
        "showwelcome": "0",
    })


def _length_to_css_px(value: str) -> float | None:
    match = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(px|mm|cm|in|pt|pc)?\s*",
        str(value or ""),
    )
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "px"
    factors = {
        "px": 1.0,
        "mm": 96.0 / 25.4,
        "cm": 96.0 / 2.54,
        "in": 96.0,
        "pt": 96.0 / 72.0,
        "pc": 16.0,
    }
    return number * factors[unit]


def _center_initial_namedview(
    svg_path: Path, viewport_size: tuple[int, int] = (1100, 720),
) -> bool:
    """Fit the rendered asset in the initial viewport without changing its geometry."""
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        viewbox = [float(value) for value in str(root.get("viewBox") or "").replace(",", " ").split()]
        if len(viewbox) != 4 or viewbox[2] <= 0 or viewbox[3] <= 0:
            return False
        page_width = _length_to_css_px(root.get("width") or "") or viewbox[2]
        page_height = _length_to_css_px(root.get("height") or "") or viewbox[3]
        scale_x = page_width / viewbox[2]
        scale_y = page_height / viewbox[3]
        boxes = []
        for node in root.iter():
            values = str(node.get("data-pnpink-cut-bbox") or "").replace(",", " ").split()
            if len(values) != 4:
                continue
            box = tuple(float(value) for value in values)
            if box[2] > 0 and box[3] > 0:
                boxes.append(box)
        if boxes:
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
        else:
            left, top = viewbox[0], viewbox[1]
            right, bottom = left + viewbox[2], top + viewbox[3]
        center_x = ((left + right) * 0.5) * scale_x
        center_y = ((top + bottom) * 0.5) * scale_y
        content_width = max((right - left) * scale_x, 1e-9)
        content_height = max((bottom - top) * scale_y, 1e-9)
        viewport_width = max(320, int(viewport_size[0]))
        viewport_height = max(240, int(viewport_size[1]))
        zoom = min(viewport_width / content_width, viewport_height / content_height) * 0.62
        zoom = min(max(zoom, 0.05), 8.0)
        namedview = root.find(f"{{{_SODIPODI_NS}}}namedview")
        if namedview is None:
            namedview = ET.Element(f"{{{_SODIPODI_NS}}}namedview", {"id": "pnpink-preview-view"})
            root.insert(0, namedview)
        namedview.set(f"{{{_INKSCAPE_NS}}}cx", f"{center_x:.8g}")
        namedview.set(f"{{{_INKSCAPE_NS}}}cy", f"{center_y:.8g}")
        namedview.set(f"{{{_INKSCAPE_NS}}}zoom", f"{zoom:.8g}")
        tree.write(svg_path, encoding="utf-8", xml_declaration=True)
        return True
    except (ET.ParseError, OSError, TypeError, ValueError):
        return False


def _configure_preview_document(svg_path: Path, page_label: str) -> bool:
    """Prepare the disposable live SVG with a page matching its generated bbox."""
    try:
        ET.register_namespace("", _SVG_NS)
        ET.register_namespace("inkscape", _INKSCAPE_NS)
        ET.register_namespace("sodipodi", _SODIPODI_NS)
        ET.register_namespace("xlink", _XLINK_NS)
        tree = ET.parse(svg_path)
        root = tree.getroot()
        output = next((node for node in root.iter() if node.get("id") == "pnpink-output"), None)
        boxes = []
        if output is not None:
            for node in output.iter():
                values = str(node.get("data-pnpink-cut-bbox") or "").replace(",", " ").split()
                if len(values) != 4:
                    continue
                box = tuple(float(value) for value in values)
                if box[2] > 0 and box[3] > 0:
                    boxes.append((node, box))
        if boxes:
            left = min(box[0] for _node, box in boxes)
            top = min(box[1] for _node, box in boxes)
            right = max(box[0] + box[2] for _node, box in boxes)
            bottom = max(box[1] + box[3] for _node, box in boxes)
            page_width = right - left
            page_height = bottom - top
        else:
            left = top = 0.0
            page_width = _PREVIEW_PAGE_WIDTH_MM
            page_height = _PREVIEW_PAGE_HEIGHT_MM

        root.set("width", f"{page_width:.8g}mm")
        root.set("height", f"{page_height:.8g}mm")
        root.set("viewBox", f"0 0 {page_width:.8g} {page_height:.8g}")

        namedview = root.find(f"{{{_SODIPODI_NS}}}namedview")
        if namedview is None:
            namedview = ET.Element(f"{{{_SODIPODI_NS}}}namedview", {"id": "pnpink-preview-view"})
            root.insert(0, namedview)
        namedview.set("showgrid", "true")
        namedview.set("showborder", "false")
        namedview.set("labelstyle", "below")
        namedview.set(f"{{{_INKSCAPE_NS}}}showpageshadow", "false")
        namedview.set(f"{{{_INKSCAPE_NS}}}document-units", "mm")

        for grid in list(namedview.findall(f"{{{_INKSCAPE_NS}}}grid")):
            namedview.remove(grid)
        ET.SubElement(namedview, f"{{{_INKSCAPE_NS}}}grid", {
            "type": "xygrid",
            "id": "pnpink-preview-grid",
            "units": "mm",
            "originx": "0",
            "originy": "0",
            "spacingx": "1",
            "spacingy": "1",
            "empspacing": "10",
            "visible": "true",
            "enabled": "true",
        })

        pages = list(namedview.findall(f"{{{_INKSCAPE_NS}}}page"))
        page = pages[0] if pages else ET.SubElement(namedview, f"{{{_INKSCAPE_NS}}}page")
        for extra in pages[1:]:
            namedview.remove(extra)
        page.attrib.update({
            "x": "0",
            "y": "0",
            "width": f"{page_width:.8g}",
            "height": f"{page_height:.8g}",
            f"{{{_INKSCAPE_NS}}}label": str(page_label or "PnPInk preview"),
        })

        if output is not None and boxes:
            dx = -left
            dy = -top
            previous_transform = str(output.get("transform") or "").strip()
            translation = f"translate({dx:.8g},{dy:.8g})"
            output.set("transform", f"{translation} {previous_transform}".strip())
            for node, (x, y, width, height) in boxes:
                node.set(
                    "data-pnpink-cut-bbox",
                    f"{x + dx:.8f} {y + dy:.8f} {width:.8f} {height:.8f}",
                )

        tree.write(svg_path, encoding="utf-8", xml_declaration=True)
        return True
    except (ET.ParseError, OSError, TypeError, ValueError):
        return False


class InkscapePreviewer:
    """Display generated SVGs in one persistent Inkscape GUI window."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gui_process: subprocess.Popen | None = None
        self._shell_process: subprocess.Popen | None = None
        self._output: queue.Queue[str] = queue.Queue()
        self._runtime_dir = Path(TEMPPATHS.named_dir("inkscape_previewer", stem="deckmaker-web"))
        self._legacy_profile_dir = Path(TEMPPATHS.named_dir("inkscape_profile", stem="deckmaker-viewer"))
        self._profile_dir = data_path("inkscape-previewer")
        self._live_path = self._runtime_dir / "pnpink-preview.svg"
        self._app_tag = f"pnpinkpreview{os.getpid()}"
        self._profile_ready = False
        self._prewarm_pending = False
        self._prewarm_generation = 0
        self._has_preview_content = False
        self._field_to_ids: dict[str, list[str]] = {}
        self._id_to_fields: dict[str, list[str]] = {}
        self._restart_on_reload = False
        self._rebase_failed = False
        self._combined_stop = threading.Event()
        self._diagnostic_lock = threading.Lock()
        self._output_tail: deque[str] = deque(maxlen=12)
        self._expected_exits: set[int] = set()
        self._last_action = ""
        self._runtime_logged = False

    @property
    def running(self) -> bool:
        with self._lock:
            shell_running = self._shell_process is not None and self._shell_process.poll() is None
            if not shell_running:
                return False
            if os.name == "nt":
                return bool(self._window_handle(timeout=0.0))
            return self._gui_process is not None and self._gui_process.poll() is None

    def show(
        self,
        svg_path: str,
        selected_fields: list[str] | None = None,
        page_label: str = "",
    ) -> None:
        source = Path(str(svg_path or "")).resolve()
        if not source.is_file():
            raise FileNotFoundError(str(source))
        with self._lock:
            started = time.perf_counter()
            starting = not self.running
            LOG.i(
                "[inkscape_previewer.request] source=%r bytes=%d starting=%s selected_fields=%r label=%r",
                str(source), source.stat().st_size, starting, selected_fields, page_label,
            )
            fit_view = not self._has_preview_content or self._restart_on_reload
            self._replace_live_svg(
                source,
                center_viewport=False,
                page_label=page_label or source.stem,
            )
            replaced = time.perf_counter()
            selection_action = self._selection_action(selected_fields)
            if starting:
                self._start()
                actions = (list(_INITIAL_VIEW_ACTIONS) if fit_view else [])
                if selection_action:
                    actions.append(selection_action)
                if actions:
                    self._action(";".join(actions), timeout=8.0)
                action_description = ";".join(actions)
            elif self._restart_on_reload:
                # Restart only if file-rebase is absent or failed in this viewer.
                self.close()
                self._start()
                actions = list(_INITIAL_VIEW_ACTIONS)
                if selection_action:
                    actions.append(selection_action)
                self._action(";".join(actions), timeout=8.0)
                action_description = "restart"
            else:
                try:
                    actions = [_RELOAD_ACTION]
                    if fit_view:
                        actions.extend(_INITIAL_VIEW_ACTIONS)
                    if selection_action:
                        actions.append(selection_action)
                    self._action(";".join(actions), timeout=12.0)
                    action_description = ";".join(actions)
                except Exception as error:
                    # Some builds advertise rebase but crash on pages/namedviews.
                    # Do not repeat that failing operation on every preview update.
                    self._rebase_failed = True
                    self._restart_on_reload = True
                    LOG.w("[inkscape_previewer] file-rebase failed; using restart for this session: %s", error)
                    self.close()
                    self._start()
                    actions = list(_INITIAL_VIEW_ACTIONS)
                    if selection_action:
                        actions.append(selection_action)
                    self._action(";".join(actions), timeout=8.0)
                    action_description = "restart"
            self._has_preview_content = True
            finished = time.perf_counter()
            LOG.i(
                "[inkscape_previewer.timing] starting=%s replace_ms=%.1f action_ms=%.1f "
                "total_ms=%.1f action='%s' bytes=%d",
                starting,
                (replaced - started) * 1000.0,
                (finished - replaced) * 1000.0,
                (finished - started) * 1000.0,
                action_description,
                source.stat().st_size,
            )

    def show_empty(self, page_label: str = "PnPInk preview") -> None:
        """Start the preview window immediately with an empty A5 document."""
        with self._lock:
            if self.running:
                return
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            empty = self._runtime_dir / "pnpink-preview-empty.svg"
            empty.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
                'width="210mm" height="148mm" viewBox="0 0 210 148">'
                '<sodipodi:namedview id="pnpink-preview-view"/>'
                '</svg>',
                encoding="utf-8",
            )
            self.show(str(empty), page_label=page_label)
            self._has_preview_content = False

    def prewarm(self, page_label: str = "PnPInk preview") -> None:
        """Start the empty viewer in the background before rendering an asset."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self.running or self._prewarm_pending:
                return
            self._prewarm_pending = True
            self._prewarm_generation += 1
            generation = self._prewarm_generation
        finally:
            self._lock.release()
        threading.Thread(
            target=self._prewarm_worker,
            args=(page_label, generation),
            name="inkscape-previewer-prewarm",
            daemon=True,
        ).start()

    def _prewarm_worker(self, page_label: str, generation: int) -> None:
        try:
            with self._lock:
                if generation != self._prewarm_generation:
                    return
                self.show_empty(page_label)
        except Exception as error:
            LOG.w("[inkscape_previewer] prewarm failed: %s", error)
        finally:
            with self._lock:
                if generation == self._prewarm_generation:
                    self._prewarm_pending = False

    def _selection_action(self, fields: list[str] | None) -> str:
        if fields is None:
            return ""
        wanted = [str(value).strip() for value in fields if str(value).strip()]
        selected_ids: list[str] = []
        for pattern in wanted:
            for field, node_ids in self._field_to_ids.items():
                if not fnmatch.fnmatchcase(field, pattern):
                    continue
                for node_id in node_ids:
                    if node_id not in selected_ids:
                        selected_ids.append(node_id)
        actions = ["select-clear"]
        if selected_ids:
            actions.append(f"select-by-id:{','.join(selected_ids[:200])}")
        return ";".join(actions)

    def select_fields(self, fields: list[str]) -> None:
        """Select generated SVG nodes associated with dataset header target ids."""
        with self._lock:
            if not self.running:
                return
            self._action(self._selection_action(fields), timeout=8.0)

    def selected_fields(self) -> list[str]:
        """Return source ids represented by the current manual Inkscape selection."""
        with self._lock:
            if not self.running:
                return []
            output = self._action("select-list", timeout=2.0)
            selected_ids = re.findall(r"^([^\s]+)\s+cloned:", output, re.MULTILINE)
            fields: list[str] = []
            for node_id in selected_ids:
                for field in self._id_to_fields.get(node_id, []):
                    if field not in fields:
                        fields.append(field)
            return fields

    def close(self) -> None:
        with self._lock:
            self._combined_stop.set()
            self._prewarm_generation += 1
            self._prewarm_pending = False
            shell_process = self._shell_process
            gui_process = self._gui_process
            with self._diagnostic_lock:
                for process in (shell_process, gui_process):
                    if process is not None and process.poll() is None:
                        self._expected_exits.add(process.pid)
            if shell_process is not None or gui_process is not None:
                LOG.i(
                    "[inkscape_previewer.close] gui_pid=%s shell_pid=%s last_action=%r",
                    getattr(gui_process, "pid", None), getattr(shell_process, "pid", None), self._last_action,
                )
            self._shell_process = None
            self._gui_process = None
        try:
            if shell_process is not None and shell_process.poll() is None and shell_process.stdin is not None:
                try:
                    shell_process.stdin.write("window-close\n")
                    shell_process.stdin.flush()
                except Exception:
                    pass
                shell_process.stdin.write("quit\n")
                shell_process.stdin.flush()
                shell_process.wait(timeout=4)
        except Exception:
            try:
                shell_process.terminate()
                shell_process.wait(timeout=2)
            except Exception:
                try:
                    shell_process.kill()
                except Exception:
                    pass
        finally:
            streams = () if shell_process is None else (shell_process.stdin, shell_process.stdout)
            for stream in streams:
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        if gui_process is not None and gui_process.poll() is None:
            try:
                gui_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                gui_process.terminate()
                try:
                    gui_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    gui_process.kill()

    def _prepare_profile(self) -> None:
        if self._profile_ready:
            return
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        preferences = self._profile_dir / "preferences.xml"
        legacy_preferences = self._legacy_profile_dir / "preferences.xml"
        if not preferences.is_file() and legacy_preferences.is_file():
            shutil.copyfile(legacy_preferences, preferences)
        if preferences.is_file():
            try:
                tree = ET.parse(preferences)
                root = tree.getroot()
            except (ET.ParseError, OSError):
                root = ET.fromstring(_PROFILE_XML)
                tree = ET.ElementTree(root)
        else:
            root = ET.fromstring(_PROFILE_XML)
            tree = ET.ElementTree(root)
        _configure_profile(root)
        temporary = preferences.with_suffix(".tmp")
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, preferences)
        self._profile_ready = True

    def _replace_live_svg(
        self,
        source: Path,
        *,
        center_viewport: bool,
        page_label: str,
    ) -> None:
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._index_fields(source)
        temporary = self._live_path.with_suffix(f".{os.getpid()}.tmp.svg")
        shutil.copyfile(source, temporary)
        if not _configure_preview_document(temporary, page_label):
            LOG.w("[inkscape_previewer] could not configure preview document '%s'", source)
        if center_viewport:
            viewport_size = self._saved_viewport_size()
            if not _center_initial_namedview(temporary, viewport_size):
                LOG.w("[inkscape_previewer] could not center initial viewport for '%s'", source)
        os.replace(temporary, self._live_path)

    def _saved_viewport_size(self) -> tuple[int, int]:
        try:
            root = ET.parse(self._profile_dir / "preferences.xml").getroot()
            geometry = root.find("./group[@id='geometry']")
            width = int(float(geometry.get("width", "1100")))
            height = int(float(geometry.get("height", "720")))
            if width >= 320 and height >= 240:
                return width, height
        except (AttributeError, ET.ParseError, OSError, TypeError, ValueError):
            pass
        return 1100, 720

    def _index_fields(self, source: Path) -> None:
        field_to_ids: dict[str, list[str]] = {}
        id_to_fields: dict[str, list[str]] = {}
        label_name = "{http://www.inkscape.org/namespaces/inkscape}label"
        try:
            root = ET.parse(source).getroot()
        except (ET.ParseError, OSError):
            self._field_to_ids = {}
            self._id_to_fields = {}
            return

        def visit(node: ET.Element, inherited: tuple[str, ...] = ()) -> None:
            own = []
            for attribute in ("data-field", "data-origid", label_name):
                value = str(node.get(attribute) or "").strip()
                if value and value not in own:
                    own.append(value)
            aliases = tuple(own) + tuple(value for value in inherited if value not in own)
            node_id = str(node.get("id") or "").strip()
            if node_id:
                id_to_fields[node_id] = list(aliases)
                for field in own:
                    field_to_ids.setdefault(field, []).append(node_id)
            for child in node:
                visit(child, aliases)

        output_root = next(
            (node for node in root.iter() if str(node.get("id") or "") == "pnpink-output"),
            root,
        )
        visit(output_root)
        self._field_to_ids = field_to_ids
        self._id_to_fields = id_to_fields

    def _start(self) -> None:
        startup_started = time.perf_counter()
        executable = INKSCAPE.find_executable()
        shell = INKSCAPE.shell_executable(executable)
        if not shell:
            raise RuntimeError("Inkscape executable was not found")
        self._prepare_profile()
        profile_ready = time.perf_counter()
        env = INKSCAPE.clean_launch_env(
            isolated_profile=False,
            profile_stem="deckmaker-viewer",
        )
        env["INKSCAPE_PROFILE_DIR"] = str(self._profile_dir)
        if not self._runtime_logged:
            LOG.i(
                "[inkscape_previewer.runtime] platform=%s os=%r machine=%s python=%r python_version=%s "
                "inkscape_exe=%r inkscape_version=%r shell_exe=%r",
                sys.platform, platform.platform(), platform.machine(), sys.executable, sys.version.split()[0],
                executable, INKSCAPE.get_version(shell), shell,
            )
            LOG.i(
                "[inkscape_previewer.environment] profile=%r live_svg=%r env=%r",
                str(self._profile_dir), str(self._live_path),
                {key: env[key] for key in (
                    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "LC_NUMERIC",
                    "GTK_PATH", "GTK_THEME", "INKSCAPE_PROFILE_DIR", "INKSCAPE_APP_ID_TAG",
                ) if key in env},
            )
            self._runtime_logged = True
        self._output = queue.Queue()
        use_active_window = sys.platform != "darwin" and INKSCAPE.supports_cli_option(shell, "--active-window")
        has_rebase = INKSCAPE.supports_action(shell, "file-rebase")
        self._restart_on_reload = not has_rebase or self._rebase_failed
        LOG.i(
            "[inkscape_previewer.capabilities] platform=%s active_window=%s file_rebase=%s reload=%s",
            sys.platform, use_active_window, has_rebase, "restart" if self._restart_on_reload else "rebase",
        )
        if not use_active_window:
            LOG.i("[inkscape_previewer] using combined GUI+shell compatibility mode")
            self._start_combined(shell, env, startup_started, profile_ready)
            return
        gui_kwargs = {
            "args": [
                executable,
                f"--app-id-tag={self._app_tag}",
                "--with-gui",
                str(self._live_path),
            ],
            "cwd": INKSCAPE.executable_dir(executable),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 0,
            "close_fds": True,
        }
        if os.name == "nt":
            gui_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            gui_kwargs["start_new_session"] = True
        LOG.i("[inkscape_previewer.spawn] role=gui argv=%r cwd=%r", gui_kwargs["args"], gui_kwargs["cwd"])
        self._gui_process = subprocess.Popen(**gui_kwargs)
        threading.Thread(
            target=self._read_output,
            args=(self._gui_process, None, "gui", False),
            name="inkscape-previewer-gui-output",
            daemon=True,
        ).start()
        if not self._wait_for_gui(timeout=30.0):
            self.close()
            raise RuntimeError("Inkscape preview window did not become ready")
        window_ready = time.perf_counter()
        time.sleep(0.25)

        shell_kwargs = {
            "args": [
                shell,
                f"--app-id-tag={self._app_tag}",
                "-q",
                "--shell",
            ],
            "cwd": INKSCAPE.executable_dir(executable),
            "env": env,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 0,
        }
        if os.name == "nt":
            shell_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        shell_deadline = time.monotonic() + 20.0
        while True:
            self._output = queue.Queue()
            LOG.i("[inkscape_previewer.spawn] role=shell argv=%r cwd=%r", shell_kwargs["args"], shell_kwargs["cwd"])
            self._shell_process = subprocess.Popen(**shell_kwargs)
            output_queue = self._output
            threading.Thread(
                target=self._read_output,
                args=(self._shell_process, output_queue),
                name="inkscape-previewer-output",
                daemon=True,
            ).start()
            try:
                self._read_prompt_from(self._shell_process, timeout=8.0, output_queue=output_queue)
                break
            except Exception as error:
                shell_process = self._shell_process
                self._shell_process = None
                if shell_process is not None and shell_process.poll() is None:
                    shell_process.terminate()
                    try:
                        shell_process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        shell_process.kill()
                retryable = "No active desktop to run" in str(error)
                gui_ready = (
                    self._wait_for_gui(timeout=0.0)
                    if os.name == "nt"
                    else self._gui_process is not None and self._gui_process.poll() is None
                )
                if not retryable or time.monotonic() >= shell_deadline or not gui_ready:
                    self.close()
                    raise
                time.sleep(0.25)
        shell_ready = time.perf_counter()
        LOG.i(
            "[inkscape_previewer] started gui_pid=%s shell_pid=%s svg='%s'",
            self._gui_process.pid,
            self._shell_process.pid,
            self._live_path,
        )
        LOG.i(
            "[inkscape_previewer.startup] profile_ms=%.1f window_ms=%.1f shell_ms=%.1f total_ms=%.1f",
            (profile_ready - startup_started) * 1000.0,
            (window_ready - profile_ready) * 1000.0,
            (shell_ready - window_ready) * 1000.0,
            (shell_ready - startup_started) * 1000.0,
        )

    def _start_combined(
        self,
        executable: str,
        env: dict[str, str],
        startup_started: float,
        profile_ready: float,
    ) -> None:
        """Control the GUI directly when active-window is unavailable."""
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            kwargs["start_new_session"] = True
        argv = [executable, f"--app-id-tag={self._app_tag}", "--with-gui", "--shell", str(self._live_path)]
        LOG.i("[inkscape_previewer.spawn] role=combined argv=%r cwd=%r", argv, INKSCAPE.executable_dir(executable))
        process = subprocess.Popen(
            argv,
            cwd=INKSCAPE.executable_dir(executable),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
            close_fds=True,
            **kwargs,
        )
        self._gui_process = process
        self._shell_process = process
        threading.Thread(
            target=self._read_output,
            args=(process, self._output, "combined"),
            name="inkscape-previewer-output",
            daemon=True,
        ).start()
        try:
            self._read_prompt(timeout=30.0)
            # Older interactive shells process GUI events only between commands.
            self._action("", timeout=5.0)
            if not self._wait_for_gui(timeout=5.0):
                raise RuntimeError("Inkscape preview window did not become ready")
        except Exception:
            self.close()
            raise
        shell_ready = time.perf_counter()
        self._combined_stop = stop = threading.Event()
        threading.Thread(
            target=self._pump_combined_gui,
            args=(process, stop),
            name="inkscape-previewer-gui-events",
            daemon=True,
        ).start()
        LOG.i(
            "[inkscape_previewer] started combined gui+shell pid=%s svg='%s'",
            process.pid,
            self._live_path,
        )
        LOG.i(
            "[inkscape_previewer.startup] profile_ms=%.1f combined_ms=%.1f total_ms=%.1f",
            (profile_ready - startup_started) * 1000.0,
            (shell_ready - profile_ready) * 1000.0,
            (shell_ready - startup_started) * 1000.0,
        )

    def _pump_combined_gui(self, process: subprocess.Popen, stop: threading.Event) -> None:
        """Keep legacy GUI shells responsive without interleaving real commands."""
        while not stop.wait(0.1):
            if not self._lock.acquire(blocking=False):
                continue
            try:
                if stop.is_set() or self._shell_process is not process or process.poll() is not None:
                    return
                self._action("", timeout=2.0)
            except Exception as error:
                LOG.w("[inkscape_previewer] GUI event loop stopped: %s", error)
                return
            finally:
                self._lock.release()

    def _output_summary(self) -> str:
        with self._diagnostic_lock:
            return " | ".join(list(self._output_tail)[-4:])

    def _read_output(
        self, process: subprocess.Popen, output_queue: queue.Queue | None = None,
        role: str = "shell", forward: bool = True,
    ) -> None:
        stream = process.stdout
        if stream is None:
            return
        destination = (output_queue or self._output) if forward else None
        try:
            for value in PROCESS_IO.iter_text_chunks(stream):
                if destination is not None:
                    destination.put(value)
                detail = re.sub(
                    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
                    lambda match: f"\\x{ord(match[0]):02x}", value,
                ).strip()
                if not detail.strip("> \r\n\t"):
                    continue
                with self._diagnostic_lock:
                    self._output_tail.append(f"{role} pid={process.pid}: {detail[-512:]}")
                emit = LOG.w if re.search(
                    r"error|warning|critical|assertion|could not|unknown|failed|segmentation|abort", detail, re.IGNORECASE,
                ) else LOG.d
                emit("[inkscape_previewer.output] role=%s pid=%s chars=%d text=%r", role, process.pid, len(value), detail[-2000:])
        except (OSError, ValueError) as error:
            LOG.d("[inkscape_previewer.output] role=%s pid=%s reader closed: %s", role, process.pid, error)
        finally:
            return_code = process.wait()
            with self._diagnostic_lock:
                expected = process.pid in self._expected_exits
                self._expected_exits.discard(process.pid)
            exit_signal = ""
            if return_code < 0:
                try:
                    exit_signal = signal.Signals(-return_code).name
                except ValueError:
                    pass
            emit = LOG.i if expected or return_code == 0 else LOG.w
            emit(
                "[inkscape_previewer.exit] role=%s pid=%s rc=%s expected=%s signal=%s last_action=%r tail=%r",
                role, process.pid, return_code, expected, exit_signal, self._last_action, self._output_summary(),
            )

    def _read_prompt(self, timeout: float) -> str:
        return self._read_prompt_from(self._shell_process, timeout)

    def _read_prompt_from(
        self,
        process: subprocess.Popen | None,
        timeout: float,
        output_queue: queue.Queue | None = None,
    ) -> str:
        if process is None:
            raise RuntimeError("Inkscape previewer is not running")
        source = output_queue or self._output
        deadline = time.monotonic() + max(0.1, timeout)
        chunks: list[str] = []
        while time.monotonic() < deadline:
            if process.poll() is not None:
                while True:
                    try:
                        chunks.append(source.get_nowait())
                    except queue.Empty:
                        break
                break
            try:
                chunks.append(source.get(timeout=0.02))
            except queue.Empty:
                continue
            text = "".join(chunks)
            if text.endswith(_PROMPT):
                return text
            if "No active desktop to run" in text:
                raise RuntimeError("Inkscape previewer did not become ready: No active desktop to run")
        message = "".join(chunks).strip()[-1500:]
        LOG.w(
            "[inkscape_previewer.prompt] pid=%s rc=%s timeout=%.1fs last_action=%r output=%r tail=%r",
            process.pid, process.poll(), timeout, self._last_action, message, self._output_summary(),
        )
        raise RuntimeError(
            f"Inkscape previewer did not become ready (pid={process.pid}, rc={process.poll()}): {message}"
        )

    def _action(self, action: str, *, timeout: float) -> str:
        process = self._shell_process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("Inkscape previewer is not running")
        started = time.perf_counter()
        verbose = bool(action and action != "select-list")
        if verbose:
            self._last_action = action
            LOG.i("[inkscape_previewer.action] pid=%s command=%r timeout=%.1fs", process.pid, action, timeout)
        process.stdin.write(f"{action}\n")
        process.stdin.flush()
        output = self._read_prompt(timeout)
        if "Error on rebase_doc:" in output or re.search(
            r"(?:could not find action for:|Unknown action name:)\s*(?:app\.)?file-rebase\b", output,
        ):
            raise RuntimeError(f"Inkscape preview action failed: {output.strip()[-1000:]}")
        if verbose:
            LOG.i(
                "[inkscape_previewer.action.done] pid=%s ms=%.1f rc=%s output=%r",
                process.pid, (time.perf_counter() - started) * 1000.0, process.poll(), output.strip()[-1000:],
            )
        return output

    def _window_handle(self, timeout: float = 0.0) -> int:
        if os.name != "nt":
            return 0
        user32 = ctypes.windll.user32
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        expected_name = self._live_path.name.lower()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            found: list[tuple[int, int]] = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def callback(hwnd, _parameter):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value.lower()
                    if expected_name in title and "inkscape" in title:
                        rectangle = wintypes.RECT()
                        area = 0
                        if user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
                            area = max(0, rectangle.right - rectangle.left) * max(
                                0, rectangle.bottom - rectangle.top,
                            )
                        found.append((area, int(hwnd)))
                return True

            user32.EnumWindows(callback, 0)
            if found:
                return max(found)[1]
            if time.monotonic() >= deadline:
                return 0
            time.sleep(0.05)

    def _wait_for_gui(self, timeout: float) -> bool:
        if os.name == "nt":
            return bool(self._window_handle(timeout=timeout))
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            process = self._gui_process
            if process is None or process.poll() is not None:
                return False
            if time.monotonic() + 1.0 < deadline:
                time.sleep(1.0)
                return process.poll() is None
        return False
