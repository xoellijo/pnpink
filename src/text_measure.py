from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import os
import queue
import tempfile
import threading
import time

import log as LOG
import svg as SVG
import inkscape_cli as INKSCAPE

_l = LOG


@dataclass
class ProbeTask:
    svg_bytes: bytes
    ids: set[str]
    offsets: dict
    bboxes: dict = field(default_factory=dict)
    query_ms: float = 0.0
    error: Exception | None = None
    done: threading.Event = field(default_factory=threading.Event)


class TextQueryService:
    def __init__(self, *, timeout_s: float = 20.0):
        self.timeout_s = float(timeout_s)
        self._queue: queue.Queue[ProbeTask | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pnpink-text-query", daemon=True)
        self._started = time.perf_counter()
        self._closed = False
        self._fatal_error: Exception | None = None
        self._ready = threading.Event()
        self._thread.start()

    def submit(self, probe_tree, ids, offsets) -> ProbeTask:
        if self._closed:
            raise RuntimeError("Text query service is closed")
        svg_bytes = SVG.etree.tostring(
            probe_tree.getroot(), encoding="UTF-8", xml_declaration=True
        )
        task = ProbeTask(svg_bytes, set(ids or set()), dict(offsets or {}))
        if self._fatal_error is not None:
            task.error = self._fatal_error
            task.done.set()
            return task
        self._queue.put(task)
        return task

    def _run(self) -> None:
        fd, temp_svg = tempfile.mkstemp(prefix="pnpink_text_probe_", suffix=".svg")
        os.close(fd)
        current_task = None
        try:
            exe = INKSCAPE.find_executable()
            if not exe:
                raise RuntimeError("Inkscape executable not found")
            shell_exe = INKSCAPE.shell_executable(exe) or exe
            env = INKSCAPE.clean_launch_env(isolated_profile=True)
            with INKSCAPE.ShellQuerySession(
                shell_exe,
                exe_dir=os.path.dirname(exe) or None,
                env=env,
            ) as shell:
                shell.wait_ready(timeout_s=self.timeout_s)
                self._ready.set()
                _l.i(
                    "[text_measure] shell_ready_ms=%.1f exe='%s'",
                    (time.perf_counter() - self._started) * 1000.0,
                    shell_exe,
                )
                while True:
                    current_task = self._queue.get()
                    if current_task is None:
                        break
                    started = time.perf_counter()
                    try:
                        with open(temp_svg, "wb") as handle:
                            handle.write(current_task.svg_bytes)
                        current_task.bboxes = shell.query_all(
                            temp_svg,
                            current_task.ids,
                            timeout_s=self.timeout_s,
                            log_query=False,
                        )
                        elapsed_ms = (time.perf_counter() - started) * 1000.0
                        current_task.query_ms = elapsed_ms
                        _l.d(
                            "[text_measure] ids=%d bboxes=%d query_ms=%.1f",
                            len(current_task.ids),
                            len(current_task.bboxes),
                            elapsed_ms,
                        )
                    except Exception as ex:
                        current_task.error = ex
                    finally:
                        current_task.done.set()
                        current_task = None
        except Exception as ex:
            self._fatal_error = ex
            self._ready.set()
            if current_task is not None:
                current_task.error = ex
                current_task.done.set()
            while True:
                try:
                    pending = self._queue.get_nowait()
                except queue.Empty:
                    break
                if pending is None:
                    continue
                pending.error = ex
                pending.done.set()
            _l.w("[text_measure] worker_failed: %s", ex)
        finally:
            try:
                os.unlink(temp_svg)
            except Exception:
                pass

    def collect(self, tasks) -> tuple[dict, dict]:
        bboxes = {}
        offsets = {}
        errors = []
        wait_started = time.perf_counter()
        for task in tasks or []:
            task.done.wait(timeout=self.timeout_s + 5.0)
            if not task.done.is_set():
                errors.append(RuntimeError("Timed out waiting for text probe"))
                continue
            if task.error is not None:
                errors.append(task.error)
                continue
            bboxes.update(task.bboxes)
            offsets.update(task.offsets)
        _l.i(
            "[text_measure] collect tasks=%d bboxes=%d errors=%d wait_ms=%.1f query_total_ms=%.1f query_max_ms=%.1f",
            len(tasks or []),
            len(bboxes),
            len(errors),
            (time.perf_counter() - wait_started) * 1000.0,
            sum(float(task.query_ms or 0.0) for task in (tasks or [])),
            max([float(task.query_ms or 0.0) for task in (tasks or [])] or [0.0]),
        )
        if errors:
            details = "; ".join(str(error) for error in errors[:3])
            raise RuntimeError(f"Text geometry measurement failed: {details}")
        return bboxes, offsets

    def wait_ready(self, timeout_s: float | None = None) -> None:
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        if not self._ready.wait(timeout=max(0.0, timeout)):
            raise RuntimeError("Timed out starting the text geometry helper")
        if self._fatal_error is not None:
            raise RuntimeError(f"Text geometry helper failed: {self._fatal_error}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=self.timeout_s + 5.0)


def extend_bbox_map(bboxes, element_id: str, bbox) -> None:
    if not element_id or not bbox:
        return
    current = bboxes.get(element_id)
    if not current:
        bboxes[element_id] = dict(bbox)
        return
    left = min(float(current["x"]), float(bbox["x"]))
    top = min(float(current["y"]), float(bbox["y"]))
    right = max(float(current["x"]) + float(current["width"]), float(bbox["x"]) + float(bbox["width"]))
    bottom = max(float(current["y"]) + float(current["height"]), float(bbox["y"]) + float(bbox["height"]))
    bboxes[element_id] = {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _uu_per_px_xy(doc_root) -> tuple[float, float]:
    try:
        viewbox = (doc_root.get("viewBox") or "").replace(",", " ").split()
        if len(viewbox) != 4:
            return 1.0, 1.0
        width_px = SVG.parse_len_px(doc_root, doc_root.get("width"))
        height_px = SVG.parse_len_px(doc_root, doc_root.get("height"))
        scale_x = float(viewbox[2]) / float(width_px) if float(width_px) > 0 else 1.0
        scale_y = float(viewbox[3]) / float(height_px) if float(height_px) > 0 else 1.0
        return scale_x, scale_y
    except Exception:
        return 1.0, 1.0


def _scale_bbox(bbox, scale_x: float, scale_y: float):
    out = dict(bbox or {})
    for key in ("x", "width"):
        out[key] = float(out.get(key) or 0.0) * scale_x
    for key in ("y", "height"):
        out[key] = float(out.get(key) or 0.0) * scale_y
    return out


class TextBBoxBatch:
    def __init__(self, doc_root):
        self.doc_root = doc_root
        self._ids_by_consumer = defaultdict(set)
        self._bboxes = {}

    def register(self, consumer: str, ids) -> None:
        self._ids_by_consumer[str(consumer or "text")].update(str(value) for value in (ids or ()) if str(value or "").strip())

    @property
    def ids(self) -> set[str]:
        out = set()
        for values in self._ids_by_consumer.values():
            out.update(values)
        return out

    @property
    def consumers(self) -> tuple[str, ...]:
        return tuple(self._ids_by_consumer)

    @property
    def missing_ids(self) -> set[str]:
        return self.ids - set(self._bboxes)

    def set_probe_bboxes(self, bboxes, offsets=None) -> None:
        scale_x, scale_y = _uu_per_px_xy(self.doc_root)
        self._bboxes = {
            element_id: _scale_bbox(bbox, scale_x, scale_y)
            for element_id, bbox in (bboxes or {}).items()
        }
        for element_id, offset in (offsets or {}).items():
            bbox = self._bboxes.get(element_id)
            if bbox is None:
                continue
            dx, dy = offset
            bbox["x"] = float(bbox["x"]) + float(dx)
            bbox["y"] = float(bbox["y"]) + float(dy)

    def bboxes_for(self, consumer: str):
        ids = self._ids_by_consumer.get(str(consumer or "text"), set())
        return {element_id: self._bboxes[element_id] for element_id in ids if element_id in self._bboxes}
