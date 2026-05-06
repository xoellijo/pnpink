# -*- coding: utf-8 -*-
"""Ghostscript helpers for PDF assembly and optimization."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import log as LOG

_l = LOG

__all__ = [
    "GhostscriptResult",
    "GhostscriptNotFoundError",
    "build_pdfwrite_argv",
    "discover_icc_profiles",
    "find_ghostscript_executable",
    "get_pdf_profile_names",
    "get_default_cmyk_icc_profile",
    "write_pdfx_def",
    "merge_pdfs",
]

_PDF_PROFILE_NAMES = ("default", "screen", "ebook", "printer", "prepress", "cmyk")

class GhostscriptNotFoundError(FileNotFoundError):
    """Raised when Ghostscript cannot be found on the current system."""


@dataclass
class GhostscriptResult:
    ok: bool
    executable: str
    argv: list[str]
    output_pdf: str
    returncode: int
    message: str


def _norm_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _existing_file(path: str | os.PathLike[str] | None) -> str | None:
    if not path:
        return None
    p = _norm_path(path)
    try:
        return p if os.path.isfile(p) else None
    except Exception:
        return None


def _slash_value(value: str | None) -> str | None:
    s = str(value or "").strip()
    if not s:
        return None
    return s if s.startswith("/") else f"/{s}"


def _device_name(value: str | None) -> str | None:
    s = str(value or "").strip()
    if not s:
        return None
    return s if s.startswith("/") else f"/{s}"


def _version_key(path: str) -> tuple:
    name = Path(path).name.lower()
    nums: list[int] = []
    current = ""
    for ch in name:
        if ch.isdigit():
            current += ch
            continue
        if current:
            nums.append(int(current))
            current = ""
    if current:
        nums.append(int(current))
    return tuple(nums or [0])


def _windows_program_files_candidates() -> list[str]:
    out: list[str] = []
    roots = []
    for env_key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LocalAppData"):
        val = str(os.environ.get(env_key) or "").strip()
        if val:
            roots.append(val)
    seen = set()
    for root in roots:
        gs_root = os.path.join(root, "gs")
        if not os.path.isdir(gs_root):
            continue
        try:
            entries = sorted(
                (os.path.join(gs_root, name) for name in os.listdir(gs_root)),
                key=_version_key,
                reverse=True,
            )
        except Exception:
            continue
        for entry in entries:
            for exe_name in ("gswin64c.exe", "gswin32c.exe", "gs.exe"):
                cand = os.path.join(entry, "bin", exe_name)
                norm = os.path.normcase(os.path.normpath(cand))
                if norm in seen:
                    continue
                seen.add(norm)
                if os.path.isfile(cand):
                    out.append(os.path.normpath(cand))
    return out


def _platform_candidates() -> list[str]:
    out: list[str] = []

    for env_key in ("GS_EXECUTABLE", "GHOSTSCRIPT_PATH"):
        cand = _existing_file(os.environ.get(env_key))
        if cand:
            out.append(cand)

    names: list[str]
    if os.name == "nt":
        names = ["gswin64c.exe", "gswin32c.exe", "gs.exe", "gs"]
    else:
        names = ["gs"]

    for name in names:
        exe = shutil.which(name)
        if exe:
            out.append(_norm_path(exe))

    if os.name == "nt":
        out.extend(_windows_program_files_candidates())
    elif sys.platform == "darwin":
        for cand in (
            "/opt/homebrew/bin/gs",
            "/usr/local/bin/gs",
            "/opt/local/bin/gs",
            "/usr/bin/gs",
        ):
            hit = _existing_file(cand)
            if hit:
                out.append(hit)
    else:
        for cand in (
            "/usr/bin/gs",
            "/usr/local/bin/gs",
            "/snap/bin/gs",
        ):
            hit = _existing_file(cand)
            if hit:
                out.append(hit)

    seen = set()
    deduped = []
    for cand in out:
        key = os.path.normcase(os.path.normpath(cand))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cand)
    return deduped


def find_ghostscript_executable(explicit_path: str | None = None) -> str:
    cand = _existing_file(explicit_path)
    if cand:
        return cand
    for path in _platform_candidates():
        if os.path.isfile(path):
            return path
    raise GhostscriptNotFoundError("Ghostscript executable not found")


def get_pdf_profile_names() -> tuple[str, ...]:
    return _PDF_PROFILE_NAMES


def discover_icc_profiles(ghostscript_exe: str | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(path: str | None):
        hit = _existing_file(path)
        if not hit:
            return
        key = os.path.normcase(os.path.normpath(hit))
        if key in seen:
            return
        seen.add(key)
        out.append(hit)

    try:
        exe = find_ghostscript_executable(ghostscript_exe)
        gs_root = os.path.dirname(os.path.dirname(exe))
        icc_dir = os.path.join(gs_root, "iccprofiles")
        if os.path.isdir(icc_dir):
            for name in sorted(os.listdir(icc_dir)):
                if str(name or "").lower().endswith((".icc", ".icm")):
                    _add(os.path.join(icc_dir, name))
    except Exception:
        pass

    if os.name == "nt":
        color_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "spool", "drivers", "color")
        if os.path.isdir(color_dir):
            try:
                for name in sorted(os.listdir(color_dir)):
                    if str(name or "").lower().endswith((".icc", ".icm")):
                        _add(os.path.join(color_dir, name))
            except Exception:
                pass
    return out


def get_default_cmyk_icc_profile(ghostscript_exe: str | None = None) -> str:
    for path in discover_icc_profiles(ghostscript_exe):
        if Path(path).name.lower() == "default_cmyk.icc":
            return path
    return ""


def _ps_string(value: str) -> str:
    text = str(value or "").replace("\\", "/")
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({text})"


def write_pdfx_def(
    path: str,
    *,
    icc_profile: str,
    title: str = "PnPInk PDF/X export",
    output_condition: str = "Commercial printing",
    output_condition_identifier: str = "Custom",
    registry_name: str = "http://www.color.org",
    pdfx_version: int = 3,
) -> str:
    """Write a Ghostscript PDF/X definition file with the selected output intent."""
    target = _norm_path(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    icc = _norm_path(icc_profile)
    if not os.path.isfile(icc):
        raise FileNotFoundError(f"ICC profile not found: {icc}")
    version = int(pdfx_version or 4)
    if version not in {1, 3, 4}:
        version = 4
    if version == 1:
        gts = "PDF/X-1a:2001"
    elif version == 3:
        gts = "PDF/X-3:2002"
    else:
        gts = "PDF/X-4"
    content = f"""%!
[/GTS_PDFXVersion ({gts})
 /Title {_ps_string(title)}
 /Trapped /False
 /DOCINFO pdfmark

/ICCProfile {_ps_string(icc)} def
[/_objdef {{icc_PDFX}} /type /stream /OBJ pdfmark
[{{icc_PDFX}} << /N 4 >> /PUT pdfmark
[{{icc_PDFX}} ICCProfile (r) file /PUT pdfmark

[/_objdef {{OutputIntent_PDFX}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFX}} <<
  /Type /OutputIntent
  /S /GTS_PDFX
  /OutputCondition {_ps_string(output_condition)}
  /Info {_ps_string(output_condition_identifier)}
  /OutputConditionIdentifier {_ps_string(output_condition_identifier)}
  /RegistryName {_ps_string(registry_name)}
  /DestOutputProfile {{icc_PDFX}}
>> /PUT pdfmark
[{{Catalog}} << /OutputIntents [ {{OutputIntent_PDFX}} ] >> /PUT pdfmark
"""
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return target


def build_pdfwrite_argv(
    input_pdfs: Sequence[str],
    output_pdf: str,
    *,
    ghostscript_exe: str | None = None,
    detect_duplicate_images: bool = True,
    compatibility_level: str | None = None,
    pdf_settings: str | None = None,
    color_conversion_strategy: str | None = None,
    color_conversion_strategy_for_images: str | None = None,
    process_color_model: str | None = None,
    output_icc_profile: str | None = None,
    default_cmyk_profile: str | None = None,
    override_icc: bool | None = None,
    text_k_preserve: int | None = None,
    extra_switches: Iterable[str] | None = None,
    safer: bool = True,
) -> list[str]:
    exe = find_ghostscript_executable(ghostscript_exe)
    out_pdf = _norm_path(output_pdf)
    srcs = [_norm_path(p) for p in input_pdfs or []]
    if not srcs:
        raise ValueError("input_pdfs is empty")
    argv = [
        exe,
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER" if safer else "-dNOSAFER",
        "-sDEVICE=pdfwrite",
        f"-sOutputFile={out_pdf}",
    ]
    argv.append(f"-dDetectDuplicateImages={'true' if detect_duplicate_images else 'false'}")

    if compatibility_level:
        argv.append(f"-dCompatibilityLevel={str(compatibility_level).strip()}")
    pdf_settings_value = _slash_value(pdf_settings)
    if pdf_settings_value:
        argv.append(f"-dPDFSETTINGS={pdf_settings_value}")
    if color_conversion_strategy:
        argv.append(f"-sColorConversionStrategy={str(color_conversion_strategy).strip()}")
    if color_conversion_strategy_for_images:
        argv.append(f"-sColorConversionStrategyForImages={str(color_conversion_strategy_for_images).strip()}")
    if process_color_model:
        model = str(process_color_model or "").strip().lstrip("/")
        argv.append(f"-sProcessColorModel={model}")
    if output_icc_profile:
        argv.append(f"-sOutputICCProfile={_norm_path(output_icc_profile)}")
    if default_cmyk_profile:
        argv.append(f"-sDefaultCMYKProfile={_norm_path(default_cmyk_profile)}")
    if override_icc is not None:
        argv.append(f"-dOverrideICC={'true' if bool(override_icc) else 'false'}")
    if text_k_preserve is not None:
        argv.append(f"-dTextKPreserve={int(text_k_preserve)}")
    for sw in (extra_switches or []):
        s = str(sw or "").strip()
        if s:
            argv.append(s)
    argv.append("-f")
    argv.extend(srcs)
    return argv


def merge_pdfs(
    input_pdfs: Sequence[str],
    output_pdf: str,
    *,
    ghostscript_exe: str | None = None,
    detect_duplicate_images: bool = True,
    compatibility_level: str | None = None,
    pdf_settings: str | None = None,
    color_conversion_strategy: str | None = None,
    color_conversion_strategy_for_images: str | None = None,
    process_color_model: str | None = None,
    output_icc_profile: str | None = None,
    default_cmyk_profile: str | None = None,
    override_icc: bool | None = None,
    text_k_preserve: int | None = None,
    extra_switches: Iterable[str] | None = None,
    safer: bool = True,
    timeout_ms: int | None = None,
    on_output=None,
) -> GhostscriptResult:
    srcs = [_norm_path(p) for p in input_pdfs or []]
    missing = [p for p in srcs if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError(f"Missing input PDF(s): {', '.join(missing)}")

    argv = build_pdfwrite_argv(
        srcs,
        output_pdf,
        ghostscript_exe=ghostscript_exe,
        detect_duplicate_images=detect_duplicate_images,
        compatibility_level=compatibility_level,
        pdf_settings=pdf_settings,
        color_conversion_strategy=color_conversion_strategy,
        color_conversion_strategy_for_images=color_conversion_strategy_for_images,
        process_color_model=process_color_model,
        output_icc_profile=output_icc_profile,
        default_cmyk_profile=default_cmyk_profile,
        override_icc=override_icc,
        text_k_preserve=text_k_preserve,
        extra_switches=extra_switches,
        safer=safer,
    )
    exe = argv[0]
    cwd = (os.path.dirname(exe) or None) if os.name == "nt" else None
    kwargs = {
        "args": argv,
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "timeout": None if timeout_ms is None else max(1, int(timeout_ms)) / 1000.0,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    _l.i(
        f"[gs] merge start inputs={len(srcs)} output='{_norm_path(output_pdf)}' "
        f"detect_duplicate_images={'yes' if detect_duplicate_images else 'no'}"
    )
    _l.i(f"[gs] argv={' '.join(argv)}")
    if on_output is None:
        proc = subprocess.run(**kwargs)
        returncode = int(proc.returncode)
        message = "\n".join(
            part.strip()
            for part in (proc.stderr or "", proc.stdout or "")
            if str(part or "").strip()
        ).strip()
    else:
        kwargs.pop("timeout", None)
        proc = subprocess.Popen(**kwargs)
        output_q: queue.Queue[str | None] = queue.Queue()

        def _reader(stream):
            try:
                while True:
                    chunk = stream.read(1)
                    if not chunk:
                        break
                    output_q.put(chunk)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        readers = [
            threading.Thread(target=_reader, args=(proc.stdout,), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()
        chunks: list[str] = []
        while proc.poll() is None or any(reader.is_alive() for reader in readers) or not output_q.empty():
            try:
                chunk = output_q.get(timeout=0.05)
            except queue.Empty:
                continue
            if chunk is None:
                continue
            chunks.append(chunk)
            try:
                on_output(chunk)
            except Exception:
                pass
        for reader in readers:
            try:
                reader.join(timeout=0.2)
            except Exception:
                pass
        returncode = int(proc.returncode or 0)
        message = "".join(chunks).strip()
    out_pdf = _norm_path(output_pdf)
    ok = returncode == 0 and os.path.isfile(out_pdf) and os.path.getsize(out_pdf) > 0
    if ok:
        _l.i(f"[gs] merge ok output='{out_pdf}'")
    else:
        output_size = 0
        try:
            output_size = os.path.getsize(out_pdf) if os.path.isfile(out_pdf) else 0
        except Exception:
            output_size = 0
        _l.w(f"[gs] merge failed rc={returncode} output='{out_pdf}' output_size={output_size}")
        if message:
            _l.w(f"[gs] message {message[:1200]}")
        try:
            if os.path.isfile(out_pdf):
                os.remove(out_pdf)
        except Exception as ex:
            _l.w(f"[gs] cannot remove failed output '{out_pdf}': {ex}")
    return GhostscriptResult(
        ok=ok,
        executable=exe,
        argv=list(argv),
        output_pdf=out_pdf,
        returncode=returncode,
        message=message,
    )
