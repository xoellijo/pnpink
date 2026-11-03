# -*- coding: utf-8 -*-
"""Temporary PDF page previews for DeckMaker's spritesheet editor."""

from __future__ import annotations

import os
from pathlib import Path
import math
import re
import shutil
import struct
import subprocess
import threading
import time

import gs as GS
import inkscape_cli as INKSCAPE
import log as LOG
import project_assets as PROJECT_ASSETS
import temp_paths as TEMPPATHS


MIN_PDF_DPI = 300
_COUNT_RE = re.compile(rb"/Count\s+(\d+)")
_IMAGE_OBJECT_RE = re.compile(r"(?ms)^\s*(\d+)\s+\d+\s+obj\b(.*?)^\s*endobj\b")
_RESOURCE_RE = re.compile(r"^\s*/([^\s/]+)\s+(\d+)\s+\d+\s+R\s*$", re.MULTILINE)
_MATRIX_RE = re.compile(
    r"^\s*([-+.\dEe]+)\s+([-+.\dEe]+)\s+([-+.\dEe]+)\s+"
    r"([-+.\dEe]+)\s+([-+.\dEe]+)\s+([-+.\dEe]+)\s+cm\s*$"
)
_DRAW_RE = re.compile(r"^\s*/([^\s/]+)\s+Do\s*$")
_DPI_CACHE: dict[tuple[str, int, int], int] = {}
_PAGE_COUNT_CACHE: dict[tuple[str, int], int] = {}


def _matrix_multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    )


def _ghostscript_pdf_debug(source: Path, page: int) -> str:
    executable = GS.find_ghostscript_executable()
    kwargs = {
        "args": [
            executable,
            "-q",
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            f"-dFirstPage={int(page)}",
            f"-dLastPage={int(page)}",
            "-dPDFDEBUG",
            "-sDEVICE=nullpage",
            str(source),
        ],
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 90.0,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(**kwargs)
    if result.returncode != 0:
        raise RuntimeError(str(result.stdout or "").strip() or "Ghostscript PDF inspection failed")
    return str(result.stdout or "")


def _effective_image_dpis(debug_output: str, page: int) -> list[float]:
    images: dict[int, tuple[int, int]] = {}
    for object_match in _IMAGE_OBJECT_RE.finditer(debug_output):
        body = object_match.group(2)
        if not re.search(r"/Subtype\s*/Image\b", body):
            continue
        width_match = re.search(r"/Width\s+(\d+)", body)
        height_match = re.search(r"/Height\s+(\d+)", body)
        if width_match and height_match:
            images[int(object_match.group(1))] = (
                int(width_match.group(1)),
                int(height_match.group(1)),
            )
    if not images:
        return []

    resources = {
        name: int(object_number)
        for name, object_number in _RESOURCE_RE.findall(debug_output)
    }
    marker = f"% Processing Page {int(page)} content stream"
    trace = debug_output.split(marker, 1)[-1] if marker in debug_output else debug_output
    current = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack: list[tuple[float, ...]] = []
    values: list[float] = []
    for raw_line in trace.splitlines():
        line = raw_line.strip()
        if line == "q":
            stack.append(current)
            continue
        if line == "Q":
            if stack:
                current = stack.pop()
            continue
        matrix_match = _MATRIX_RE.match(line)
        if matrix_match:
            matrix = tuple(float(value) for value in matrix_match.groups())
            current = _matrix_multiply(current, matrix)
            continue
        draw_match = _DRAW_RE.match(line)
        if not draw_match:
            continue
        dimensions = images.get(resources.get(draw_match.group(1), -1))
        if not dimensions:
            continue
        width_inches = math.hypot(current[0], current[1]) / 72.0
        height_inches = math.hypot(current[2], current[3]) / 72.0
        if width_inches > 0 and height_inches > 0:
            values.extend((dimensions[0] / width_inches, dimensions[1] / height_inches))
    return [value for value in values if math.isfinite(value) and value > 0]


def pdf_page_render_dpi(source: str | Path, page: int, *, minimum: int = MIN_PDF_DPI) -> int:
    source_path = Path(source).expanduser().resolve()
    number = max(1, int(page or 1))
    cache_key = (str(source_path), int(source_path.stat().st_mtime_ns), number)
    cached = _DPI_CACHE.get(cache_key)
    if cached is not None:
        return max(int(minimum), cached)
    detected = 0
    image_count = 0
    try:
        values = _effective_image_dpis(_ghostscript_pdf_debug(source_path, number), number)
        image_count = len(values) // 2
        if values:
            detected = int(round(max(values)))
    except Exception as ex:
        LOG.w(f"[deckmaker.pdf-spritesheet] DPI detection failed; using {int(minimum)} DPI: {ex}")
    resolved = max(int(minimum), detected)
    _DPI_CACHE[cache_key] = resolved
    LOG.i(
        "[deckmaker.pdf-spritesheet] page=%d images=%d detected_dpi=%d render_dpi=%d source='%s'",
        number,
        image_count,
        detected,
        resolved,
        source_path.name,
    )
    return resolved


def pdf_page_count(source: str | Path) -> int:
    source_path = Path(source).expanduser().resolve()
    cache_key = (str(source_path), int(source_path.stat().st_mtime_ns))
    cached = _PAGE_COUNT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    count = 0
    try:
        executable = GS.find_ghostscript_executable()
        kwargs = {
            "args": [
                executable,
                "-q",
                "-dNODISPLAY",
                "-dSAFER",
                f"--permit-file-read={source_path}",
                f"-sPDFname={source_path}",
                "-c",
                "PDFname (r) file runpdfbegin pdfpagecount = quit",
            ],
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 30.0,
            "check": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(**kwargs)
        if result.returncode == 0:
            matches = re.findall(r"(?m)^\s*(\d+)\s*$", str(result.stdout or ""))
            if matches:
                count = int(matches[-1])
    except Exception as ex:
        LOG.w(f"[deckmaker.pdf-spritesheet] page count inspection failed: {ex}")

    if count <= 0:
        try:
            values = [int(value) for value in _COUNT_RE.findall(source_path.read_bytes()) if int(value) > 0]
            count = max(values) if values else 0
        except OSError:
            count = 0
    count = max(1, count)
    _PAGE_COUNT_CACHE[cache_key] = count
    return count


def png_dpi(path: str | Path) -> tuple[float, float] | None:
    try:
        with Path(path).open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            while True:
                size_raw = stream.read(4)
                if len(size_raw) != 4:
                    return None
                size = struct.unpack(">I", size_raw)[0]
                chunk_type = stream.read(4)
                data = stream.read(size)
                stream.read(4)
                if chunk_type == b"pHYs" and len(data) == 9 and data[8] == 1:
                    x_ppm, y_ppm = struct.unpack(">II", data[:8])
                    return x_ppm * 0.0254, y_ppm * 0.0254
                if chunk_type == b"IEND":
                    return None
    except OSError:
        return None


def png_has_dpi(path: str | Path, required: int) -> bool:
    resolution = png_dpi(path)
    return bool(resolution and min(resolution) >= max(MIN_PDF_DPI, int(required)) - 1.0)


def render_pdf_page(source: str | Path, output: str | Path, page: int, *, dpi: int | None = None) -> int:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    render_dpi = max(MIN_PDF_DPI, int(dpi or 0), pdf_page_render_dpi(source_path, page))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    executable = INKSCAPE.find_executable()
    shell = INKSCAPE.shell_executable(executable)
    if not shell:
        raise RuntimeError("Inkscape executable not found")
    code, message = INKSCAPE.run([
        shell,
        str(source_path),
        f"--pages={int(page)}",
        "--pdf-poppler",
        "--export-area-page",
        "--export-type=png",
        f"--export-filename={output_path}",
        f"--export-dpi={render_dpi}",
    ], exe_dir=INKSCAPE.executable_dir(executable),
        env=INKSCAPE.clean_launch_env(profile_stem="pdf-spritesheet"),
        timeout_s=90.0)
    invalid_page = "no pages selected" in str(message or "").lower()
    if invalid_page:
        try:
            output_path.unlink()
        except OSError:
            pass
        raise ValueError(f"PDF page {int(page)} does not exist")
    if code != 0 or not output_path.is_file() or output_path.stat().st_size <= 16:
        raise RuntimeError(f"Could not render PDF page {int(page)}: {str(message or '').strip()}")
    return render_dpi


class PdfSpritesheet:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._directory = ""
        self._template = ""
        self._source = ""
        self._filename = ""
        self._source_path = ""
        self._page_source_path = ""
        self._copied = False
        self._page = 0
        self._page_count: int | None = None
        self._dpi = MIN_PDF_DPI
        self._preview = ""
        self._revision = 0

    @staticmethod
    def _page_count_hint(content: bytes) -> int | None:
        counts = [int(value) for value in _COUNT_RE.findall(content) if int(value) > 0]
        return max(counts) if counts else None

    def load(self, template: str, filename: str, content: bytes) -> dict:
        if not content.lstrip().startswith(b"%PDF-"):
            raise ValueError("The dropped file is not a valid PDF")
        safe_name = Path(str(filename or "spritesheet.pdf")).name or "spritesheet.pdf"
        source, source_path, copied = PROJECT_ASSETS.import_asset(template, safe_name, content)
        return self._activate(
            template, safe_name, source, source_path, copied,
            self._page_count_hint(content),
        )

    def load_project(self, template: str, filename: str) -> dict | None:
        """Load a dropped PDF already available beside the template or under its asset folders."""
        safe_name = Path(str(filename or "spritesheet.pdf")).name or "spritesheet.pdf"
        source = PROJECT_ASSETS.find_named_asset(template, safe_name)
        if source is None:
            return None
        try:
            with source.open("rb") as stream:
                header = stream.read(1024)
        except OSError as error:
            raise RuntimeError(f"Could not read project PDF: {source}") from error
        if not header.lstrip().startswith(b"%PDF-"):
            raise ValueError("The dropped file is not a valid PDF")
        template_dir = Path(str(template or "")).expanduser().resolve().parent
        source_path = os.path.relpath(source, template_dir).replace(os.sep, "/")
        return self._activate(
            template, safe_name, source, source_path, False,
            pdf_page_count(source),
        )

    def _activate(
        self,
        template: str,
        safe_name: str,
        source: str | Path,
        source_path: str,
        copied: bool,
        page_count: int | None,
    ) -> dict:
        directory = TEMPPATHS.make_work_dir("deckmaker_pdf_spritesheet", stem=Path(safe_name).stem)
        LOG.i(
            "[deckmaker.pdf-spritesheet] %s source='%s'",
            "copied" if copied else "reused",
            source_path,
        )
        with self._lock:
            previous = self._directory
            self._directory = directory
            self._template = str(template)
            self._source = str(source)
            self._filename = safe_name
            self._source_path = source_path
            self._page_source_path = ""
            self._copied = copied
            self._page = 0
            self._page_count = page_count
            self._dpi = MIN_PDF_DPI
            self._preview = ""
        if previous:
            shutil.rmtree(previous, ignore_errors=True)
        return self.page(1)

    def page(self, page: int) -> dict:
        number = max(1, int(page or 1))
        with self._lock:
            if not self._source or not os.path.isfile(self._source):
                raise RuntimeError("Drop a PDF before selecting a page")
            if self._page_count is not None and number > self._page_count:
                raise ValueError(f"PDF page {number} does not exist")
            source = self._source
            template = self._template
            directory = self._directory
            generated_name = f"{Path(self._filename).stem}.page-{number}.png"
            existing = PROJECT_ASSETS.find_named_asset(template, generated_name)
            source_mtime = os.path.getmtime(source)
            target_dpi = pdf_page_render_dpi(source, number)
            existing_current = bool(
                existing
                and existing.stat().st_mtime >= source_mtime
                and png_has_dpi(existing, target_dpi)
            )
            output = str(existing) if existing_current else os.path.join(directory, f"page-{number:05d}.png")
            if not os.path.isfile(output) or os.path.getsize(output) <= 16 or os.path.getmtime(output) < source_mtime:
                started = time.perf_counter()
                try:
                    target_dpi = render_pdf_page(source, output, number, dpi=target_dpi)
                except ValueError:
                    self._page_count = number - 1
                    raise
                LOG.i(
                    "[deckmaker.pdf-spritesheet] page=%d ms=%.1f source='%s'",
                    number,
                    (time.perf_counter() - started) * 1000.0,
                    self._filename,
                )
            if existing is None or Path(output).resolve() != existing.resolve():
                generated, page_source_path = PROJECT_ASSETS.store_generated_asset(template, generated_name, output)
                output = str(generated)
            else:
                page_source_path = os.path.relpath(existing, Path(template).resolve().parent).replace(os.sep, "/")
            self._page = number
            self._preview = output
            self._page_source_path = page_source_path
            self._dpi = target_dpi
            self._revision += 1
            return self.public()

    def public(self) -> dict:
        with self._lock:
            if not self._preview:
                raise RuntimeError("No PDF page has been rendered")
            return {
                "fileName": self._filename,
                "sourcePath": self._source_path,
                "pageSourcePath": self._page_source_path,
                "copied": self._copied,
                "page": self._page,
                "pageCount": self._page_count,
                "hasPrevious": self._page > 1,
                "hasNext": self._page_count is None or self._page < self._page_count,
                "dpi": self._dpi,
                "revision": self._revision,
                "previewUrl": f"/api/spritesheet/pdf-preview?rev={self._revision}",
            }

    def preview(self) -> str:
        with self._lock:
            if not self._preview or not os.path.isfile(self._preview):
                raise RuntimeError("No PDF page has been rendered")
            return self._preview

    def close(self) -> None:
        with self._lock:
            directory = self._directory
            self._directory = ""
            self._template = ""
            self._source = ""
            self._source_path = ""
            self._page_source_path = ""
            self._preview = ""
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
