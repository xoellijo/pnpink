# -*- coding: utf-8 -*-
"""Curated ICC profile catalog for PDF/X CMYK export."""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import gs as GS
import log as LOG
import net

_l = LOG


@dataclass(frozen=True)
class IccProfileSpec:
    key: str
    label: str
    filename: str
    urls: tuple[str, ...]
    member_candidates: tuple[str, ...] = ()
    source: str = ""


ECI_DOWNLOAD_BASE = "https://eci.org/lib/exe/"
ECI_FETCH_BASE = "https://eci.org/lib/exe/fetch.php?media=downloads:icc_profiles_from_eci:"
ECI_DIRECT_2009_ZIP = ECI_DOWNLOAD_BASE + "eci_offset_2009.zip"
ECI_DIRECT_PSO_COATED_V3_ZIP = ECI_DOWNLOAD_BASE + "pso-coated_v3.zip"
ECI_DIRECT_PSO_UNCOATED_V3_ZIP = ECI_DOWNLOAD_BASE + "pso-uncoated_v3_fogra52.zip"
ECI_2009_ZIP = ECI_FETCH_BASE + "eci_offset_2009.zip"
ECI_PSO_COATED_V3_ZIP = ECI_FETCH_BASE + "pso-coated_v3.zip"
ECI_PSO_UNCOATED_V3_ZIP = ECI_FETCH_BASE + "pso-uncoated_v3_fogra52.zip"
ADOBE_CMYK_ZIP = "https://download.adobe.com/pub/adobe/iccprofiles/win/AdobeICCProfilesCS4Win_end-user.zip"


_CATALOG: tuple[IccProfileSpec, ...] = (
    IccProfileSpec(
        key="iso_coated_v2_fogra39",
        label="ISO Coated v2 (FOGRA39)",
        filename="ISOcoated_v2_eci.icc",
        urls=(ECI_DIRECT_2009_ZIP, ECI_2009_ZIP),
        member_candidates=("ISOcoated_v2_eci.icc",),
        source="ECI offset 2009",
    ),
    IccProfileSpec(
        key="pso_coated_v3_fogra51",
        label="PSO Coated v3 (FOGRA51)",
        filename="PSOcoated_v3.icc",
        urls=(ECI_DIRECT_PSO_COATED_V3_ZIP, ECI_PSO_COATED_V3_ZIP),
        member_candidates=("PSOcoated_v3.icc",),
        source="ECI offset 2015",
    ),
    IccProfileSpec(
        key="pso_coated_v3_300",
        label="PSO Coated v3 300%",
        filename="PSOcoated_v3_300.icc",
        urls=(ECI_DIRECT_PSO_COATED_V3_ZIP, ECI_PSO_COATED_V3_ZIP),
        member_candidates=("PSOcoated_v3_300.icc",),
        source="ECI offset 2015",
    ),
    IccProfileSpec(
        key="pso_uncoated_v3_fogra52",
        label="PSO Uncoated v3 (FOGRA52)",
        filename="PSOuncoated_v3_FOGRA52.icc",
        urls=(ECI_DIRECT_PSO_UNCOATED_V3_ZIP, ECI_PSO_UNCOATED_V3_ZIP),
        member_candidates=("PSOuncoated_v3_FOGRA52.icc",),
        source="ECI offset 2015",
    ),
    IccProfileSpec(
        key="iso_uncoated_yellowish_fogra29",
        label="ISO Uncoated Yellowish (FOGRA29)",
        filename="ISOuncoatedyellowish.icc",
        urls=(ECI_DIRECT_2009_ZIP, ECI_2009_ZIP),
        member_candidates=("ISOuncoatedyellowish.icc",),
        source="ECI offset 2009",
    ),
    IccProfileSpec(
        key="gracol_2006_coated1",
        label="GRACoL 2006 Coated1",
        filename="GRACoL2006_Coated1v2.icc",
        urls=("https://www.color.org/registry/profiles/GRACoL2006_Coated1v2.icc",),
        source="ICC profile registry",
    ),
    IccProfileSpec(
        key="gracol_2013_crpc6",
        label="GRACoL 2013 (CRPC6)",
        filename="GRACoL2013_CRPC6.icc",
        urls=("https://www.color.org/registry/profiles/GRACoL2013_CRPC6.icc",),
        source="ICC profile registry",
    ),
    IccProfileSpec(
        key="us_web_coated_swop_v2",
        label="US Web Coated (SWOP) v2",
        filename="USWebCoatedSWOP.icc",
        urls=(ADOBE_CMYK_ZIP,),
        member_candidates=("USWebCoatedSWOP.icc",),
        source="Adobe ICC profiles",
    ),
)


def catalog() -> tuple[IccProfileSpec, ...]:
    return _CATALOG


def _norm_label(value: str) -> str:
    text = re.sub(r"\s+\(\*\)\s*$", "", str(value or "").strip())
    return re.sub(r"\s+", " ", text).casefold()


def _local_app_icc_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "PnPInk", "iccprofiles")
    os.makedirs(path, exist_ok=True)
    return os.path.normpath(path)


def ghostscript_icc_dir(ghostscript_exe: str | None = None) -> str:
    try:
        exe = GS.find_ghostscript_executable(ghostscript_exe)
    except Exception:
        return ""
    path = os.path.join(os.path.dirname(os.path.dirname(exe)), "iccprofiles")
    return os.path.normpath(path) if os.path.isdir(path) else ""


def install_dirs(ghostscript_exe: str | None = None) -> list[str]:
    out: list[str] = []
    gs_dir = ghostscript_icc_dir(ghostscript_exe)
    if gs_dir:
        out.append(gs_dir)
    fallback = _local_app_icc_dir()
    if os.path.normcase(fallback) not in {os.path.normcase(p) for p in out}:
        out.append(fallback)
    return out


def installed_path(spec: IccProfileSpec, ghostscript_exe: str | None = None) -> str:
    for directory in install_dirs(ghostscript_exe):
        path = os.path.join(directory, spec.filename)
        if os.path.isfile(path):
            return os.path.normpath(path)
    return ""


def display_label(spec: IccProfileSpec, ghostscript_exe: str | None = None) -> str:
    suffix = "" if installed_path(spec, ghostscript_exe) else " (*)"
    return f"{spec.label}{suffix}"


def display_choices(ghostscript_exe: str | None = None) -> list[str]:
    return [display_label(spec, ghostscript_exe) for spec in _CATALOG]


def spec_from_value(value: str) -> IccProfileSpec | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    by_label = _norm_label(raw)
    path_name = Path(raw).name.casefold()
    for spec in _CATALOG:
        if raw == spec.key:
            return spec
        if by_label == _norm_label(spec.label):
            return spec
        if path_name == spec.filename.casefold():
            return spec
    return None


def preference_value(value: str) -> str:
    spec = spec_from_value(value)
    return spec.key if spec else str(value or "").strip()


def display_value(value: str, ghostscript_exe: str | None = None) -> str:
    spec = spec_from_value(value)
    if spec is not None:
        return display_label(spec, ghostscript_exe)
    return display_label(_CATALOG[0], ghostscript_exe) if _CATALOG else ""


def _is_zip(raw: bytes, url: str) -> bool:
    if str(url or "").lower().endswith(".zip"):
        return True
    return len(raw) >= 4 and raw[:4] == b"PK\x03\x04"


def _member_matches(name: str, spec: IccProfileSpec) -> bool:
    base = Path(name).name.casefold()
    candidates = tuple(spec.member_candidates or ()) + (spec.filename,)
    return any(base == Path(candidate).name.casefold() for candidate in candidates)


def _extract_profile(raw: bytes, url: str, spec: IccProfileSpec) -> bytes:
    if not _is_zip(raw, url):
        if str(url or "").lower().endswith(".zip"):
            head = raw[:240].decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
            raise zipfile.BadZipFile(f"File is not a zip file; first bytes: {head}")
        return raw
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        members = zf.namelist()
        for name in members:
            if _member_matches(name, spec):
                return zf.read(name)
    names = ", ".join(Path(name).name for name in members[:30])
    raise RuntimeError(f"ICC profile '{spec.filename}' not found in archive; members: {names}")


def _write_profile(raw: bytes, spec: IccProfileSpec, ghostscript_exe: str | None = None) -> str:
    last_error: Exception | None = None
    for directory in install_dirs(ghostscript_exe):
        target = os.path.join(directory, spec.filename)
        try:
            os.makedirs(directory, exist_ok=True)
            tmp = target + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, target)
            return os.path.normpath(target)
        except Exception as ex:
            last_error = ex
            _l.w(f"[icc] cannot install '{spec.label}' into '{directory}': {ex}")
            try:
                if os.path.exists(target + ".tmp"):
                    os.remove(target + ".tmp")
            except Exception:
                pass
    if last_error is not None:
        raise last_error
    raise RuntimeError("No ICC profile install directory is available")


def ensure_profile(value: str, ghostscript_exe: str | None = None) -> str:
    spec = spec_from_value(value)
    if spec is None:
        raw = str(value or "").strip()
        return raw if os.path.isfile(raw) else ""
    hit = installed_path(spec, ghostscript_exe)
    if hit:
        return hit
    errors: list[str] = []
    for url in spec.urls:
        try:
            _l.i(f"[icc] downloading '{spec.label}' from {url}")
            raw, headers, status = net.fetch_bytes(url, timeout=8, retries=3, log_prefix="[icc]")
            _l.i(
                "[icc] fetched status=%s content_type='%s' bytes=%d",
                status,
                str((headers or {}).get("Content-Type") or (headers or {}).get("content-type") or ""),
                len(raw or b""),
            )
            profile = _extract_profile(raw, url, spec)
            path = _write_profile(profile, spec, ghostscript_exe)
            _l.i(f"[icc] installed '{spec.label}' at '{path}'")
            return path
        except Exception as ex:
            errors.append(f"{url}: {ex}")
            _l.w(f"[icc] download/install failed for '{spec.label}' from {url}: {ex}")
    raise RuntimeError(f"Unable to install ICC profile '{spec.label}': {'; '.join(errors)}")
