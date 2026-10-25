# -*- coding: utf-8 -*-
"""Temporary PDF page previews for DeckMaker's spritesheet editor."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import threading
import time

import inkscape_cli as INKSCAPE
import log as LOG
import project_assets as PROJECT_ASSETS
import temp_paths as TEMPPATHS


PREVIEW_DPI = 96
_COUNT_RE = re.compile(rb"/Count\s+(\d+)")


def render_pdf_page(source: str | Path, output: str | Path, page: int, *, dpi: int = PREVIEW_DPI) -> None:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
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
        f"--export-dpi={int(dpi)}",
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
            self._page_count = self._page_count_hint(content)
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
            output = str(existing) if existing and existing.stat().st_mtime >= source_mtime else os.path.join(directory, f"page-{number:05d}.png")
            if not os.path.isfile(output) or os.path.getsize(output) <= 16 or os.path.getmtime(output) < source_mtime:
                started = time.perf_counter()
                try:
                    render_pdf_page(source, output, number, dpi=PREVIEW_DPI)
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
                "dpi": PREVIEW_DPI,
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
