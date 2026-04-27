# -*- coding: utf-8 -*-
"""Ghostscript helpers for PDF assembly and optimization."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import log as LOG

_l = LOG

__all__ = [
    "GhostscriptResult",
    "GhostscriptNotFoundError",
    "build_pdfwrite_argv",
    "find_ghostscript_executable",
    "merge_pdfs",
]

_CUSTOM_PDF_PROFILES = {
    "prepress_600": {
        "base_pdf_settings": None,
        "extra_switches": [
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dDownsampleColorImages=false",
            "-dDownsampleGrayImages=false",
            "-dDownsampleMonoImages=false",
            "-dEncodeColorImages=true",
            "-dEncodeGrayImages=true",
            "-dEncodeMonoImages=true",
            "-c",
            (
                "<< "
                "/ColorImageFilter /DCTEncode "
                "/GrayImageFilter /DCTEncode "
                "/MonoImageFilter /CCITTFaxEncode "
                "/ColorImageResolution 300 "
                "/GrayImageResolution 300 "
                "/MonoImageResolution 1200 "
                "/ColorACSImageDict << /QFactor 0.18 /Blend 1 /ColorTransform 1 "
                "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
                "/GrayACSImageDict << /QFactor 0.18 /Blend 1 /ColorTransform 1 "
                "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
                ">> setdistillerparams"
            ),
        ],
    },
    "jpeg300": {
        "base_pdf_settings": None,
        "extra_switches": [
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dDownsampleColorImages=false",
            "-dDownsampleGrayImages=false",
            "-dDownsampleMonoImages=false",
            "-dEncodeColorImages=true",
            "-dEncodeGrayImages=true",
            "-dEncodeMonoImages=true",
            "-c",
            (
                "<< "
                "/ColorImageFilter /DCTEncode "
                "/GrayImageFilter /DCTEncode "
                "/MonoImageFilter /CCITTFaxEncode "
                "/ColorImageResolution 300 "
                "/GrayImageResolution 300 "
                "/MonoImageResolution 1200 "
                "/ColorACSImageDict << /QFactor 0.30 /Blend 1 /ColorTransform 1 "
                "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
                "/GrayACSImageDict << /QFactor 0.30 /Blend 1 /ColorTransform 1 "
                "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
                ">> setdistillerparams"
            ),
        ],
    },
    "subsample300": {
        "base_pdf_settings": None,
        "extra_switches": [
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dDownsampleColorImages=false",
            "-dDownsampleGrayImages=false",
            "-dDownsampleMonoImages=false",
            "-dEncodeColorImages=true",
            "-dEncodeGrayImages=true",
            "-dEncodeMonoImages=true",
            "-c",
            (
                "<< "
                "/ColorImageFilter /DCTEncode "
                "/GrayImageFilter /DCTEncode "
                "/MonoImageFilter /CCITTFaxEncode "
                "/ColorImageResolution 300 "
                "/GrayImageResolution 300 "
                "/MonoImageResolution 1200 "
                "/ColorACSImageDict << /QFactor 0.18 /Blend 1 /ColorTransform 1 "
                "/HSamples [2 1 1 2] /VSamples [2 1 1 2] >> "
                "/GrayACSImageDict << /QFactor 0.18 /Blend 1 /ColorTransform 1 "
                "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
                ">> setdistillerparams"
            ),
        ],
    },
    "balanced225": {
        "base_pdf_settings": None,
        "extra_switches": [
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dDownsampleColorImages=true",
            "-dDownsampleGrayImages=true",
            "-dDownsampleMonoImages=false",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dEncodeColorImages=true",
            "-dEncodeGrayImages=true",
            "-dEncodeMonoImages=true",
            "-c",
            (
                "<< "
                "/ColorImageFilter /DCTEncode "
                "/GrayImageFilter /DCTEncode "
                "/MonoImageFilter /CCITTFaxEncode "
                "/ColorImageResolution 225 "
                "/GrayImageResolution 225 "
                "/MonoImageResolution 1200 "
                "/ColorImageDownsampleThreshold 1.0 "
                "/GrayImageDownsampleThreshold 1.0 "
                "/ColorACSImageDict << /QFactor 0.22 /Blend 1 /ColorTransform 1 "
                "/HSamples [2 1 1 2] /VSamples [2 1 1 2] >> "
                "/GrayACSImageDict << /QFactor 0.18 /Blend 1 /ColorTransform 1 "
                "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
                ">> setdistillerparams"
            ),
        ],
    },
}


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


def build_pdfwrite_argv(
    input_pdfs: Sequence[str],
    output_pdf: str,
    *,
    ghostscript_exe: str | None = None,
    detect_duplicate_images: bool = True,
    compatibility_level: str | None = None,
    pdf_settings: str | None = None,
    color_conversion_strategy: str | None = None,
    process_color_model: str | None = None,
    extra_switches: Iterable[str] | None = None,
) -> list[str]:
    exe = find_ghostscript_executable(ghostscript_exe)
    out_pdf = _norm_path(output_pdf)
    srcs = [_norm_path(p) for p in input_pdfs or []]
    if not srcs:
        raise ValueError("input_pdfs is empty")
    profile_name = str(pdf_settings or "").strip().lower()
    custom_profile = _CUSTOM_PDF_PROFILES.get(profile_name)
    effective_pdf_settings = (
        custom_profile.get("base_pdf_settings") if custom_profile else pdf_settings
    )

    argv = [
        exe,
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER",
        "-sDEVICE=pdfwrite",
        f"-sOutputFile={out_pdf}",
    ]
    argv.append(f"-dDetectDuplicateImages={'true' if detect_duplicate_images else 'false'}")

    if compatibility_level:
        argv.append(f"-dCompatibilityLevel={str(compatibility_level).strip()}")
    pdf_settings_value = _slash_value(effective_pdf_settings)
    if pdf_settings_value:
        argv.append(f"-dPDFSETTINGS={pdf_settings_value}")
    if color_conversion_strategy:
        argv.append(f"-sColorConversionStrategy={str(color_conversion_strategy).strip()}")
    if process_color_model:
        argv.append(f"-sProcessColorModel={str(process_color_model).strip()}")
    for sw in (extra_switches or []):
        s = str(sw or "").strip()
        if s:
            argv.append(s)
    if custom_profile:
        for sw in custom_profile.get("extra_switches") or []:
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
    process_color_model: str | None = None,
    extra_switches: Iterable[str] | None = None,
    timeout_ms: int | None = None,
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
        process_color_model=process_color_model,
        extra_switches=extra_switches,
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
    proc = subprocess.run(**kwargs)
    message = (proc.stderr or proc.stdout or "").strip()
    out_pdf = _norm_path(output_pdf)
    ok = proc.returncode == 0 and os.path.isfile(out_pdf) and os.path.getsize(out_pdf) > 0
    if ok:
        _l.i(f"[gs] merge ok output='{out_pdf}'")
    else:
        _l.w(f"[gs] merge failed rc={proc.returncode} output='{out_pdf}'")
    return GhostscriptResult(
        ok=ok,
        executable=exe,
        argv=list(argv),
        output_pdf=out_pdf,
        returncode=int(proc.returncode),
        message=message,
    )
