# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import base64
import json
import os
import posixpath
import re
import shutil
import urllib.parse
import zipfile
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

import inkex
import gsheets_client_pkce as GS
import dataset_state as DSTATE
import sources as SRC
import svg as SVG

import log as LOG
_l = LOG


XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
HREF_KEYS = ("href", XLINK_HREF)
NO_COMPRESS_EXT = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
CACHE_FILE_RE = re.compile(r"^web_[0-9a-f]{64}\.[a-z0-9]+$", re.IGNORECASE)
LOCAL_SOURCE_EXT_RE = re.compile(r"(?<![\w:/\\.-])([~%$A-Za-z0-9_. /\\-]+\.(?:png|jpg|jpeg|gif|bmp|webp|svg|svgz|pdf|tif|tiff))(?![\w.-])", re.IGNORECASE)
DATA_IMAGE_RE = re.compile(r"^data:image/([^;,]+)([^,]*),(.*)$", re.IGNORECASE | re.DOTALL)


@dataclass
class PackageInfo:
    base_dir: Path
    svg_abs: Path
    manifest: Dict[str, object]


def _as_bytes(svg_root) -> bytes:
    return inkex.etree.tostring(svg_root, encoding="utf-8", xml_declaration=True)


def _get_doc_path(ext) -> Optional[Path]:
    try:
        p = ext.document_path()
    except Exception:
        p = None
    if not p:
        return None
    try:
        pp = Path(str(p)).resolve()
    except Exception:
        return None
    return pp


def _template_path_for_export(doc_path: Optional[Path]) -> Optional[Path]:
    if doc_path is None:
        return None
    if doc_path.suffix.lower() != ".svg":
        return None
    if doc_path.stem.endswith("_output"):
        template = doc_path.with_name(doc_path.stem[:-7] + doc_path.suffix)
        if template.is_file():
            _l.i(f"[pnp] exporting source template instead of generated output: '{template}'")
            return template
    return doc_path


def _pnp_extracted_svg_path(pnp_path: Optional[Path]) -> Optional[Path]:
    if pnp_path is None or pnp_path.suffix.lower() != ".pnp":
        return None
    base_dir = pnp_path.with_suffix("")
    man_path = base_dir / "manifest.json"
    if not man_path.is_file():
        return None
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(manifest, dict):
            return None
        svg_name = str(manifest.get("svg") or "").strip()
        if not svg_name:
            return None
        svg_path = _safe_join(base_dir, svg_name)
        return svg_path if svg_path.is_file() else None
    except Exception:
        return None


def _source_path_for_export(doc_path: Optional[Path]) -> Optional[Path]:
    return _template_path_for_export(doc_path) or _pnp_extracted_svg_path(doc_path)


def _export_svg_root(ext, doc_path: Optional[Path]):
    if doc_path is not None:
        try:
            return inkex.load_svg(str(doc_path)).getroot()
        except Exception as ex:
            _l.w(f"[pnp] could not reload export source '{doc_path}': {ex}")
    svg_root = getattr(ext, "svg", None)
    if svg_root is not None:
        return svg_root
    doc = getattr(ext, "document", None)
    if doc is not None:
        try:
            return doc.getroot()
        except Exception:
            pass
    raise inkex.AbortExtension("Could not access current SVG document.")


def _svg_docname(svg_root) -> str:
    if svg_root is None:
        return ""
    for key in ("{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}docname", "sodipodi:docname"):
        try:
            value = str(svg_root.get(key) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _stream_name(stream) -> str:
    try:
        return str(getattr(stream, "name", "") or "").strip()
    except Exception:
        return ""


def _package_svg_name(kind: str, doc_path: Optional[Path], svg_root, stream) -> str:
    candidates = []
    if doc_path is not None:
        candidates.append(doc_path.name)
    candidates.append(_svg_docname(svg_root))
    candidates.append(_stream_name(stream))
    for raw in candidates:
        name = Path(str(raw or "").replace("\\", "/")).name.strip()
        if not name:
            continue
        stem = Path(name).stem
        if not stem or stem.lower() == "document":
            continue
        if stem.lower().endswith(f".{kind}"):
            stem = stem[:-(len(kind) + 1)] or stem
        return f"{_safe_asset_stem(stem)}.svg"
    return f"document.svg"


def _is_local_ref(v: str) -> bool:
    s = (v or "").strip()
    if not s:
        return False
    sl = s.lower()
    if s.startswith("#"):
        return False
    if sl.startswith(("http://", "https://", "data:", "icon://", "@{", "wkmc://", "pxby://", "oclp://", "pnp://")):
        return False
    return True


def _runtime_python_dir() -> str:
    try:
        entry = (os.sys.argv[0] or "").strip()
        if entry and os.path.isfile(entry):
            return os.path.dirname(os.path.abspath(entry))
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def _iter_href_attrs(svg_root):
    for elem in svg_root.iter():
        for k in HREF_KEYS:
            try:
                v = elem.get(k)
            except Exception:
                v = None
            if v:
                yield elem, k, str(v)


def _resolve_local_asset(svg_dir: Path, raw_ref: str, *, svg_path: Optional[Path] = None) -> Optional[Path]:
    s = (raw_ref or "").strip()
    if not _is_local_ref(s):
        return None
    # Keep it simple: do not try query/fragment here for local refs.
    if ("?" in s) or ("#" in s):
        s = s.split("?", 1)[0].split("#", 1)[0]
    s = urllib.parse.unquote(os.path.expanduser(os.path.expandvars(s)))
    try:
        p = Path(s)
        cand = p if p.is_absolute() else (svg_dir / p)
        cand = cand.resolve()
        if cand.is_file():
            return cand
    except Exception:
        pass
    try:
        resolver = SRC.PathResolver(str(svg_path or (svg_dir / "_pnpink.svg")), project_root=_runtime_python_dir())
        hit = resolver.resolve_logical(s)
        return hit if hit and hit.is_file() else None
    except Exception:
        return None


def _should_include_in_pnp(path: Path) -> bool:
    name = path.name
    if CACHE_FILE_RE.match(name):
        return False
    return True


def _iter_local_refs_from_text(text: str) -> Iterable[str]:
    data = text or ""
    for m in re.finditer(r"@\{\s*([^}]*)\s*\}", data):
        yield m.group(1)
    for m in re.finditer(r"(?:^|[\s,])(?:Source|S)\s*\{\s*([^}]*)\s*\}", data, re.IGNORECASE):
        yield m.group(1)
    for m in LOCAL_SOURCE_EXT_RE.finditer(data):
        yield m.group(1)


def _asset_arc_for_ref(raw_ref: str, src: Path, used_arcs: Dict[str, int]) -> str:
    raw = (raw_ref or "").strip()
    if ("?" in raw) or ("#" in raw):
        raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = urllib.parse.unquote(raw).replace("\\", "/").strip()
    if raw and not Path(os.path.expanduser(os.path.expandvars(raw))).is_absolute():
        rel = posixpath.normpath(raw).lstrip("/")
        if rel and not rel.startswith("../") and rel != "..":
            arc = rel if "/" in rel else f"assets/{rel}"
        else:
            arc = f"assets/{src.name}"
    else:
        arc = f"assets/{src.name}"
    if arc not in used_arcs:
        used_arcs[arc] = 1
        return arc
    used_arcs[arc] += 1
    stem, ext = posixpath.splitext(arc)
    return f"{stem}_{used_arcs[arc]}{ext}"


def _add_asset(asset_map: Dict[Path, str], used_arcs: Dict[str, int], src: Path, raw_ref: str) -> None:
    if src in asset_map:
        return
    asset_map[src] = _asset_arc_for_ref(raw_ref, src, used_arcs)


def _unique_arc(base_arc: str, used_arcs: Dict[str, int]) -> str:
    arc = posixpath.normpath(str(base_arc or "").replace("\\", "/")).lstrip("/")
    if not arc or arc == ".":
        arc = "assets/asset"
    if arc not in used_arcs:
        used_arcs[arc] = 1
        return arc
    used_arcs[arc] += 1
    stem, ext = posixpath.splitext(arc)
    return f"{stem}_{used_arcs[arc]}{ext}"


def _data_image_ext(media_subtype: str) -> str:
    sub = str(media_subtype or "").strip().lower()
    if sub in {"jpeg", "pjpeg"}:
        return ".jpg"
    if sub == "svg+xml":
        return ".svg"
    if sub in {"png", "gif", "bmp", "webp", "tiff", "x-icon"}:
        return ".ico" if sub == "x-icon" else f".{sub}"
    return ".bin"


def _decode_data_image(raw: str) -> tuple[str, bytes] | None:
    m = DATA_IMAGE_RE.match(str(raw or "").strip())
    if not m:
        return None
    ext = _data_image_ext(m.group(1))
    meta = str(m.group(2) or "").lower()
    payload = str(m.group(3) or "")
    try:
        data = base64.b64decode(payload, validate=False) if ";base64" in meta else urllib.parse.unquote_to_bytes(payload)
    except Exception:
        return None
    return (ext, data) if data else None


def _safe_asset_stem(name: str) -> str:
    stem = Path(str(name or "embedded")).stem or "embedded"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "embedded"


def _externalize_embedded_package_images(svg_root, used_arcs: Dict[str, int], *, svg_name: str = "") -> list[tuple[str, bytes]]:
    assets: list[tuple[str, bytes]] = []
    if svg_root is None:
        return assets
    seen: Dict[str, str] = {}
    stem = _safe_asset_stem(svg_name)
    index = 0
    for elem, key, raw in list(_iter_href_attrs(svg_root)):
        decoded = _decode_data_image(raw)
        if decoded is None:
            continue
        ext, data = decoded
        if raw in seen:
            arc = seen[raw]
        else:
            index += 1
            arc = _unique_arc(f"assets/{stem}_{index:03d}{ext}", used_arcs)
            assets.append((arc, data))
            seen[raw] = arc
        elem.set(key, arc)
        for other_key in HREF_KEYS:
            if other_key != key and str(elem.get(other_key) or "").strip() == str(raw).strip():
                elem.set(other_key, arc)
    if assets:
        _l.i(f"[pkg] externalized embedded image assets: {len(assets)}")
    return assets


def _include_dataset_assets(asset_map: Dict[Path, str], used_arcs: Dict[str, int], svg_path: Optional[Path], csv_bytes: bytes | None) -> int:
    if not csv_bytes or svg_path is None:
        return 0
    try:
        text = csv_bytes.decode("utf-8-sig", errors="replace")
    except Exception:
        return 0
    n0 = len(asset_map)
    for raw in _iter_local_refs_from_text(text):
        src = _resolve_local_asset(svg_path.parent, raw, svg_path=svg_path)
        if src is None or not _should_include_in_pnp(src):
            continue
        _add_asset(asset_map, used_arcs, src, raw)
    return len(asset_map) - n0


def _read_file_bytes(path: Optional[Path]) -> bytes | None:
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except Exception:
        return None


def _csv_for_export(ext, doc_path: Optional[Path], svg_name: str, ext_man: Dict[str, object]) -> tuple[Optional[str], Optional[bytes]]:
    if doc_path is not None:
        local_csv = doc_path.with_suffix(".csv")
        data = _read_file_bytes(local_csv)
        if data is not None:
            return local_csv.name, data

    opt = getattr(ext, "options", None)
    state_sid = ""
    state_srg = ""
    if doc_path is not None:
        try:
            rec = DSTATE.get_gsheet_for_svg(str(doc_path))
            if rec:
                state_sid = str(rec.get("sheet_id") or "").strip()
                state_srg = str(rec.get("sheet_range") or "").strip()
        except Exception:
            pass

    sheet_id = str(
        (getattr(opt, "sheet_id", "") if opt is not None else "")
        or ext_man.get("gsheet_id")
        or state_sid
        or ""
    ).strip()
    if not sheet_id:
        return None, None

    sheet_range = str(
        (getattr(opt, "sheet_range", "") if opt is not None else "")
        or ext_man.get("gsheet_range")
        or state_srg
        or ""
    ).strip()
    data = _fetch_gsheet_csv_bytes(doc_path, sheet_id, sheet_range)
    if data is None:
        return None, None
    ext_man.setdefault("gsheet_id", sheet_id)
    if sheet_range:
        ext_man.setdefault("gsheet_range", sheet_range)
    return _default_csv_for_svg(svg_name), data


def _zip_add_file(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    ext = src.suffix.lower()
    ctype = zipfile.ZIP_STORED if ext in NO_COMPRESS_EXT else zipfile.ZIP_DEFLATED
    zf.write(str(src), arcname=arcname, compress_type=ctype)


def _zip_add_bytes(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    ext = Path(str(arcname or "")).suffix.lower()
    ctype = zipfile.ZIP_STORED if ext in NO_COMPRESS_EXT else zipfile.ZIP_DEFLATED
    zf.writestr(arcname, data, compress_type=ctype)


def _safe_join(base: Path, rel: str) -> Path:
    rel_posix = (rel or "").replace("\\", "/")
    rel_clean = posixpath.normpath("/" + rel_posix).lstrip("/")
    out = (base / rel_clean).resolve()
    base_res = base.resolve()
    if out != base_res and base_res not in out.parents:
        raise ValueError(f"unsafe path in package: {rel}")
    return out


def _find_manifest(zf: zipfile.ZipFile) -> Dict[str, object]:
    for n in zf.namelist():
        if n.lower().endswith("manifest.json"):
            try:
                data = json.loads(zf.read(n).decode("utf-8", errors="replace"))
                if isinstance(data, dict):
                    return data
            except Exception:
                return {}
    return {}


def _find_primary_svg(zf: zipfile.ZipFile, manifest: Dict[str, object]) -> str:
    m_svg = str(manifest.get("svg") or "").strip()
    if m_svg and m_svg in zf.namelist():
        return m_svg
    svgs = [n for n in zf.namelist() if n.lower().endswith(".svg") and (not n.endswith("/"))]
    if not svgs:
        raise inkex.AbortExtension("Package has no SVG file.")
    if len(svgs) == 1:
        return svgs[0]
    if m_svg:
        raise inkex.AbortExtension(f"Manifest SVG not found: {m_svg}")
    raise inkex.AbortExtension("Package contains multiple SVG files and no manifest 'svg' field.")


def _default_csv_for_svg(svg_name: str) -> str:
    p = Path(svg_name)
    return str(p.with_suffix(".csv")).replace("\\", "/")


def _build_manifest(kind: str, svg_name: str, csv_name: Optional[str], deckmaker: bool) -> Dict[str, object]:
    out = {
        "format": kind,
        "version": 1,
        "svg": svg_name,
        "run_deckmaker_on_import": bool(deckmaker),
    }
    if csv_name:
        out["csv"] = csv_name
    out["assets_dir"] = "assets"
    return out


def _choose_sheet_and_range_for_export(doc_path: Optional[Path], sheet_id: str, range_a1: Optional[str]) -> str:
    rng = str(range_a1 or "").strip()
    if rng.startswith("!") and rng.count("!") >= 2:
        rng = rng[1:]
    if "!" in rng:
        sh, cells = rng.split("!", 1)
        sh = (sh or "").strip()
        cells = (cells or "").strip()
        return f"{sh}!{cells}" if cells else sh
    if re.fullmatch(r"\d+", rng or ""):
        # GID has no direct OAuth values.get equivalent. Keep prior oauth behavior.
        rng = ""
    if rng:
        return rng
    svg_stem = (doc_path.stem if doc_path else "Sheet1")
    titles = GS.list_sheet_titles(sheet_id)
    sheet_name = next((t for t in titles if t.strip().lower() == svg_stem.strip().lower()), (titles[0] if titles else "Sheet1"))
    return sheet_name


def _fetch_gsheet_csv_bytes(doc_path: Optional[Path], sheet_id: str, range_a1: Optional[str]) -> Optional[bytes]:
    sid = (sheet_id or "").strip()
    if not sid:
        return None
    client_id = os.environ.get("PNPINK_GSHEETS_CLIENT_ID") or GS.CLIENT_ID
    try:
        rng = _choose_sheet_and_range_for_export(doc_path, sid, range_a1)
        values = GS.fetch_sheet(sid, rng, client_id or None)
        matrix = [[("" if v is None else str(v)) for v in r] for r in (values or [])]
        sio = io.StringIO(newline="")
        w = csv.writer(sio, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        for r in matrix:
            w.writerow(r)
        out = sio.getvalue().encode("utf-8")
        _l.i(f"[pnp] generated CSV from GSheet id='{sid}' range='{rng}' rows={len(matrix)}")
        return out
    except Exception as ex:
        _l.w(f"[pnp] could not generate CSV from GSheet id='{sid}': {ex}")
        return None


def export_package(ext, stream, *, kind: str) -> None:
    doc_path = _source_path_for_export(_get_doc_path(ext))
    svg_root = _export_svg_root(ext, doc_path)
    svg_name = _package_svg_name(kind, doc_path, svg_root, stream)
    svg_dir = (doc_path.parent if doc_path else Path.cwd())

    # Work on a clone so we can rewrite hrefs for portable package layout.
    try:
        svg_clone = inkex.etree.fromstring(_as_bytes(svg_root))
    except Exception:
        svg_clone = svg_root

    asset_map: Dict[Path, str] = {}
    used_arcs: Dict[str, int] = {}
    embedded_assets = _externalize_embedded_package_images(svg_clone, used_arcs, svg_name=svg_name)

    for elem, key, raw in _iter_href_attrs(svg_clone):
        src = _resolve_local_asset(svg_dir, raw, svg_path=doc_path)
        if src is None:
            continue
        if kind == "pnp" and not _should_include_in_pnp(src):
            continue

        _add_asset(asset_map, used_arcs, src, raw)
        elem.set(key, asset_map[src])

    ext_man: Dict[str, object] = {}

    if doc_path is not None:
        try:
            man_path = doc_path.with_suffix(".manifest.json")
            if man_path.is_file():
                x = json.loads(man_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(x, dict):
                    ext_man = x
        except Exception:
            ext_man = {}

    csv_name, csv_bytes = (None, None)
    if kind == "pnp":
        csv_name, csv_bytes = _csv_for_export(ext, doc_path, svg_name, ext_man)
        added = _include_dataset_assets(asset_map, used_arcs, doc_path, csv_bytes)
        if added:
            _l.i(f"[pnp] included dataset local assets: {added}")

    manifest = _build_manifest(kind, svg_name, csv_name, deckmaker=(kind == "pnp"))
    if doc_path is not None:
        try:
            if ext_man:
                manifest.update(ext_man)
        except Exception:
            pass

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(svg_name, _as_bytes(svg_clone), compress_type=zipfile.ZIP_DEFLATED)
        if csv_bytes is not None and csv_name:
            zf.writestr(csv_name, csv_bytes, compress_type=zipfile.ZIP_DEFLATED)
        for src, arc in sorted(asset_map.items(), key=lambda kv: kv[1]):
            _zip_add_file(zf, src, arc)
        for arc, data in embedded_assets:
            _zip_add_bytes(zf, arc, data)

    stream.write(out.getvalue())
    csv_ok = bool(csv_bytes is not None and csv_name)
    _l.i(f"[{kind}] export ok: svg='{svg_name}' assets={len(asset_map) + len(embedded_assets)} csv={'yes' if csv_ok else 'no'}")


def _extract_package(stream, package_path: Path) -> PackageInfo:
    raw = stream.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    if not raw:
        raise inkex.AbortExtension("Empty package file.")
    # Extract into a dedicated sibling folder named after the package file
    # (without extension), to avoid mixing assets from different packages.
    base_dir = package_path.with_suffix("").resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        manifest = _find_manifest(zf)
        svg_name = _find_primary_svg(zf, manifest)
        for info in zf.infolist():
            if info.is_dir():
                continue
            dst = _safe_join(base_dir, info.filename)
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src_f, open(dst, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)

    svg_abs = _safe_join(base_dir, svg_name)
    try:
        doc = inkex.load_svg(str(svg_abs))
        fixed_images = SVG.absolutize_all_linked_images(doc, str(svg_abs), prefer="fileuri")
        if fixed_images:
            svg_abs.write_bytes(_as_bytes(doc.getroot()))
            _l.i(f"[{manifest.get('format') or 'pkg'}] absolutized linked images on import: {fixed_images}")
    except Exception as ex:
        _l.w(f"[pkg] could not absolutize linked images on import: {ex}")
    _l.i(f"[pkg] extracted into '{base_dir}'")
    return PackageInfo(base_dir=base_dir, svg_abs=svg_abs, manifest=manifest)


def _launch_deckmaker_if_requested(info: PackageInfo) -> None:
    if not bool(info.manifest.get("run_deckmaker_on_import", False)):
        return
    try:
        import deckmaker_app as DMAPP
    except Exception as ex:
        _l.w(f"[pnp] deckmaker app import failed: {ex}")
        return

    csv_rel = str(info.manifest.get("csv") or "").strip()
    has_packaged_csv = bool(csv_rel and _safe_join(info.base_dir, csv_rel).is_file())
    sheet_id = "" if has_packaged_csv else str(info.manifest.get("gsheet_id") or "").strip()
    sheet_range = "" if has_packaged_csv else str(info.manifest.get("gsheet_range") or "").strip()
    source_mode = "local_csv" if has_packaged_csv else ""
    if has_packaged_csv and str(info.manifest.get("gsheet_id") or "").strip():
        _l.i(f"[pnp] using packaged CSV on import, ignoring manifest gsheet_id for deterministic render: '{csv_rel}'")

    if DMAPP.notify_or_launch(str(info.svg_abs), "", sheet_id, sheet_range, "global", source_mode, autorun=True):
        _l.i(f"[pnp] deckmaker app launched for '{info.svg_abs}'")
    else:
        _l.w("[pnp] could not launch DeckMaker App")


def import_package(ext, stream, *, kind: str):
    p = None
    try:
        p = Path(str(getattr(ext.options, "input_file", "") or "")).resolve()
    except Exception:
        p = None
    if p is None or not p.is_file():
        raise inkex.AbortExtension("Input package path not available.")

    info = _extract_package(stream, p)
    if not info.svg_abs.is_file():
        raise inkex.AbortExtension(f"Extracted SVG not found: {info.svg_abs}")

    try:
        doc = inkex.load_svg(str(info.svg_abs))
    except Exception:
        doc = inkex.etree.parse(str(info.svg_abs))
    root = doc.getroot()

    if kind == "pnp":
        _launch_deckmaker_if_requested(info)

    _l.i(f"[{kind}] import ok: svg='{info.svg_abs}'")
    return doc
