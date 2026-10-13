# -*- coding: utf-8 -*-
"""Persistent, isolated Inkscape window used as an SVG preview backend."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import fnmatch
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app_paths import data_path
import inkscape_cli as INKSCAPE
import log as LOG
import temp_paths as TEMPPATHS


_PROMPT = "> "
_RELOAD_ACTION = "file-rebase:true"
_INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
_SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
_WINDOW_SECTIONS = ("window", "fullscreen", "focus")
_WINDOW_VISIBILITY = {
    "menu": "0",
    "commands": "0",
    "snaptoolbox": "0",
    "toppanel": "0",
    "toolbox": "0",
    "statusbar": "0",
    "panels": "0",
    "rulers": "0",
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
    <group id="rulers" state="0"/>
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
    <group id="rulers" state="0"/>
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
    <group id="rulers" state="0"/>
    <group id="scrollbars" state="0"/>
  </group>
  <group id="dialogs">
    <group id="layers" maxDepth="20" w="180" h="500"/>
    <group id="objects" expand_to_layer="1"/>
  </group>
  <group id="options">
    <group id="savewindowgeometry" value="1"/>
    <group id="maxrecentdocuments" value="0"/>
    <group id="defaultwindowsize" value="2"/>
    <group id="dialogtype" value="1"/>
    <group id="dialogsskiptaskbar" value="1"/>
    <group id="autosave" enable="0" interval="10" path="" max="0"/>
  </group>
  <group id="geometry" width="1100" height="720" x="0" y="0" maximized="1" fullscreen="0"/>
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
        "savewindowgeometry": {"value": "1"},
        "maxrecentdocuments": {"value": "0"},
        "defaultwindowsize": {"value": "2"},
        "dialogtype": {"value": "1"},
        "dialogsskiptaskbar": {"value": "1"},
        "autosave": {"enable": "0", "interval": "10", "path": "", "max": "0"},
    }.items():
        _child_group(options, option_id).attrib.update(attributes)


def _parse_geometry(output: str) -> dict[str, int]:
    values = {key: int(value) for key, value in re.findall(r"^(x|y|w|h):(-?\d+)$", output, re.MULTILINE)}
    return values if all(key in values for key in ("x", "y", "w", "h")) else {}


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
        zoom = min(viewport_width / content_width, viewport_height / content_height) * 0.82
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
        self._geometry_path = self._profile_dir / "window-geometry.json"
        self._live_path = self._runtime_dir / "pnpink-preview.svg"
        self._app_tag = f"pnpinkpreview{os.getpid()}"
        self._profile_ready = False
        self._geometry_stop = threading.Event()
        self._geometry_thread: threading.Thread | None = None
        self._last_geometry: dict[str, int | bool] = {}
        self._field_to_ids: dict[str, list[str]] = {}
        self._id_to_fields: dict[str, list[str]] = {}

    @property
    def running(self) -> bool:
        with self._lock:
            shell_running = self._shell_process is not None and self._shell_process.poll() is None
            if not shell_running:
                return False
            if os.name == "nt":
                return bool(self._window_handle(timeout=0.0))
            return self._gui_process is not None and self._gui_process.poll() is None

    def show(self, svg_path: str, selected_fields: list[str] | None = None) -> None:
        source = Path(str(svg_path or "")).resolve()
        if not source.is_file():
            raise FileNotFoundError(str(source))
        with self._lock:
            starting = not self.running
            self._replace_live_svg(source)
            selection_action = self._selection_action(selected_fields)
            if starting:
                self._start()
                if selection_action:
                    self._action(selection_action, timeout=8.0)
            else:
                try:
                    actions = [_RELOAD_ACTION]
                    if selection_action:
                        actions.append(selection_action)
                    self._action(";".join(actions), timeout=12.0)
                except Exception:
                    self.close()
                    self._start()
                    if selection_action:
                        self._action(selection_action, timeout=8.0)

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
            self._remember_window_geometry()
            self._geometry_stop.set()
            geometry_thread = self._geometry_thread
            self._geometry_thread = None
            shell_process = self._shell_process
            gui_process = self._gui_process
            self._shell_process = None
            self._gui_process = None
        if geometry_thread is not None and geometry_thread is not threading.current_thread():
            geometry_thread.join(timeout=2.0)
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
        saved_geometry = self._load_window_geometry()
        if saved_geometry:
            geometry = _child_group(root, "geometry")
            geometry.attrib.update({
                "x": str(saved_geometry["x"]),
                "y": str(saved_geometry["y"]),
                "width": str(saved_geometry["w"]),
                "height": str(saved_geometry["h"]),
                "maximized": "1" if saved_geometry.get("maximized") else "0",
                "fullscreen": "0",
            })
        temporary = preferences.with_suffix(".tmp")
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, preferences)
        self._profile_ready = True

    def _replace_live_svg(self, source: Path) -> None:
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._index_fields(source)
        temporary = self._live_path.with_suffix(f".{os.getpid()}.tmp.svg")
        shutil.copyfile(source, temporary)
        geometry = self._load_window_geometry()
        viewport_size = (
            int(geometry.get("w", 1100)),
            int(geometry.get("h", 720)),
        )
        if not _center_initial_namedview(temporary, viewport_size):
            LOG.w("[inkscape_previewer] could not center initial viewport for '%s'", source)
        os.replace(temporary, self._live_path)

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
        executable = INKSCAPE.find_executable()
        shell = INKSCAPE.shell_executable(executable)
        if not shell:
            raise RuntimeError("Inkscape executable was not found")
        self._prepare_profile()
        env = INKSCAPE.clean_launch_env(
            isolated_profile=False,
            profile_stem="deckmaker-viewer",
        )
        env["INKSCAPE_PROFILE_DIR"] = str(self._profile_dir)
        self._output = queue.Queue()
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
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            gui_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            gui_kwargs["start_new_session"] = True
        self._gui_process = subprocess.Popen(**gui_kwargs)
        if not self._wait_for_gui(timeout=30.0):
            self.close()
            raise RuntimeError("Inkscape preview window did not become ready")
        time.sleep(0.75)

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
            "bufsize": 0,
        }
        if os.name == "nt":
            shell_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._shell_process = subprocess.Popen(**shell_kwargs)
        threading.Thread(
            target=self._read_output,
            args=(self._shell_process,),
            name="inkscape-previewer-output",
            daemon=True,
        ).start()
        try:
            self._read_prompt(timeout=30.0)
            self._restore_window_geometry()
            self._start_geometry_monitor()
        except Exception:
            self.close()
            raise
        LOG.i(
            "[inkscape_previewer] started gui_pid=%s shell_pid=%s svg='%s'",
            self._gui_process.pid,
            self._shell_process.pid,
            self._live_path,
        )

    def _load_window_geometry(self) -> dict[str, int | bool]:
        try:
            value = json.loads(self._geometry_path.read_text(encoding="utf-8"))
            geometry = {key: int(value[key]) for key in ("x", "y", "w", "h")}
            geometry["maximized"] = bool(value.get("maximized", False))
            return geometry
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def _remember_window_geometry(self) -> None:
        if os.name == "nt":
            geometry = self._native_window_geometry()
        else:
            if self._shell_process is None or self._shell_process.poll() is not None:
                return
            try:
                geometry = _parse_geometry(self._action("window-query-geometry", timeout=2.0))
            except Exception:
                return
        if not geometry:
            return
        geometry.setdefault("maximized", self._window_is_maximized())
        if geometry == self._last_geometry:
            return
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._geometry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(geometry, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self._geometry_path)
        self._last_geometry = geometry

    def _restore_window_geometry(self) -> None:
        geometry = self._load_window_geometry()
        if not geometry:
            return
        if os.name == "nt":
            self._apply_native_window_geometry(geometry)
        else:
            try:
                self._action(
                    f"window-set-geometry:{geometry['x']},{geometry['y']},{geometry['w']},{geometry['h']}",
                    timeout=4.0,
                )
            except Exception as error:
                LOG.w("[inkscape_previewer] could not restore window geometry: %s", error)
        self._last_geometry = geometry

    def _native_window_geometry(self) -> dict[str, int | bool]:
        handle = self._window_handle(timeout=0.0)
        if not handle:
            return {}
        rectangle = wintypes.RECT()
        user32 = ctypes.windll.user32
        if not user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            return {}
        return {
            "x": int(rectangle.left),
            "y": int(rectangle.top),
            "w": int(rectangle.right - rectangle.left),
            "h": int(rectangle.bottom - rectangle.top),
            "maximized": bool(user32.IsZoomed(handle)),
        }

    def _apply_native_window_geometry(self, geometry: dict[str, int | bool]) -> None:
        handle = self._window_handle(timeout=1.0)
        if not handle:
            return
        user32 = ctypes.windll.user32
        user32.ShowWindow(handle, 9)
        user32.MoveWindow(
            handle,
            int(geometry["x"]),
            int(geometry["y"]),
            max(1, int(geometry["w"])),
            max(1, int(geometry["h"])),
            True,
        )
        if geometry.get("maximized"):
            user32.ShowWindow(handle, 3)

    def _window_is_maximized(self) -> bool:
        if os.name != "nt":
            return bool(self._last_geometry.get("maximized", False))
        handle = self._window_handle(timeout=0.0)
        return bool(handle and ctypes.windll.user32.IsZoomed(handle))

    def _start_geometry_monitor(self) -> None:
        if self._geometry_thread is not None and self._geometry_thread.is_alive():
            return
        self._geometry_stop.clear()

        def monitor() -> None:
            while not self._geometry_stop.wait(1.5):
                with self._lock:
                    if not self.running:
                        return
                    self._remember_window_geometry()

        self._geometry_thread = threading.Thread(
            target=monitor,
            name="inkscape-previewer-geometry",
            daemon=True,
        )
        self._geometry_thread.start()

    def _read_output(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is None:
            return
        while True:
            value = stream.read(1)
            if not value:
                return
            self._output.put(value)

    def _read_prompt(self, timeout: float) -> str:
        return self._read_prompt_from(self._shell_process, timeout)

    def _read_prompt_from(self, process: subprocess.Popen | None, timeout: float) -> str:
        if process is None:
            raise RuntimeError("Inkscape previewer is not running")
        deadline = time.monotonic() + max(0.1, timeout)
        chunks: list[str] = []
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                chunks.append(self._output.get(timeout=0.02))
            except queue.Empty:
                continue
            text = "".join(chunks)
            if text.endswith(_PROMPT):
                return text
        message = "".join(chunks).strip()
        raise RuntimeError(f"Inkscape previewer did not become ready: {message}")

    def _action(self, action: str, *, timeout: float) -> str:
        process = self._shell_process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("Inkscape previewer is not running")
        process.stdin.write(f"{action}\n")
        process.stdin.flush()
        return self._read_prompt(timeout)

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
            found: list[int] = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def callback(hwnd, _parameter):
                length = user32.GetWindowTextLengthW(hwnd)
                if length:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value.lower()
                    if expected_name in title and "inkscape" in title:
                        found.append(int(hwnd))
                return True

            user32.EnumWindows(callback, 0)
            if found:
                return found[0]
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
