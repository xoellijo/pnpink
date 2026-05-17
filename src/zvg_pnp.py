# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import os
import posixpath
import re
import shutil
import zipfile
import csv
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

import inkex
import gsheets_client_pkce as GS
import dataset_state as DSTATE

import log as LOG
_l = LOG


XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
HREF_KEYS = ("href", XLINK_HREF)
NO_COMPRESS_EXT = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
CACHE_FILE_RE = re.compile(r"^web_[0-9a-f]{64}\.[a-z0-9]+$", re.IGNORECASE)


@dataclass
class PackageInfo:
    kind: str
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


def _iter_href_attrs(svg_root):
    for elem in svg_root.iter():
        for k in HREF_KEYS:
            try:
                v = elem.get(k)
            except Exception:
                v = None
            if v:
                yield elem, k, str(v)


def _resolve_local_asset(svg_dir: Path, raw_ref: str) -> Optional[Path]:
    s = (raw_ref or "").strip()
    if not _is_local_ref(s):
        return None
    # Keep it simple: do not try query/fragment here for local refs.
    if ("?" in s) or ("#" in s):
        s = s.split("?", 1)[0].split("#", 1)[0]
    p = Path(s)
    cand = p if p.is_absolute() else (svg_dir / p)
    try:
        cand = cand.resolve()
    except Exception:
        return None
    if not cand.is_file():
        return None
    return cand


def _should_include_in_pnp(path: Path) -> bool:
    name = path.name
    if CACHE_FILE_RE.match(name):
        return False
    return True


def _zip_add_file(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    ext = src.suffix.lower()
    ctype = zipfile.ZIP_STORED if ext in NO_COMPRESS_EXT else zipfile.ZIP_DEFLATED
    zf.write(str(src), arcname=arcname, compress_type=ctype)


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
        cells = (cells or "").strip() or "A1:Z999"
        return f"{sh}!{cells}"
    if re.fullmatch(r"\d+", rng or ""):
        # GID has no direct OAuth values.get equivalent. Keep prior oauth behavior.
        rng = ""
    if rng:
        return f"{rng}!A1:Z999"
    svg_stem = (doc_path.stem if doc_path else "Sheet1")
    titles = GS.list_sheet_titles(sheet_id)
    sheet_name = next((t for t in titles if t.strip().lower() == svg_stem.strip().lower()), (titles[0] if titles else "Sheet1"))
    return f"{sheet_name}!A1:Z999"


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
    svg_root = getattr(ext, "svg", None)
    if svg_root is None:
        doc = getattr(ext, "document", None)
        if doc is not None:
            try:
                svg_root = doc.getroot()
            except Exception:
                svg_root = None
    if svg_root is None:
        raise inkex.AbortExtension("Could not access current SVG document.")

    doc_path = _get_doc_path(ext)
    svg_name = (doc_path.name if doc_path else f"document.{kind}.svg")
    svg_dir = (doc_path.parent if doc_path else Path.cwd())

    # Work on a clone so we can rewrite hrefs for portable package layout.
    try:
        svg_clone = inkex.etree.fromstring(_as_bytes(svg_root))
    except Exception:
        svg_clone = svg_root

    asset_map: Dict[Path, str] = {}
    used_names: Dict[str, int] = {}

    for elem, key, raw in _iter_href_attrs(svg_clone):
        src = _resolve_local_asset(svg_dir, raw)
        if src is None:
            continue
        if kind == "pnp" and not _should_include_in_pnp(src):
            continue

        if src not in asset_map:
            name = src.name
            if name in used_names:
                used_names[name] += 1
                stem = Path(name).stem
                suf = Path(name).suffix
                name = f"{stem}_{used_names[Path(src).name]}{suf}"
            else:
                used_names[name] = 1
            asset_map[src] = f"assets/{name}"
        elem.set(key, asset_map[src])

    csv_name = None
    csv_abs = None
    csv_generated_bytes = None
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

    if doc_path is not None:
        csv_abs = doc_path.with_suffix(".csv")
        if csv_abs.is_file():
            csv_name = csv_abs.name

    if kind == "pnp" and not csv_name:
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
        sheet_range = str(
            (getattr(opt, "sheet_range", "") if opt is not None else "")
            or ext_man.get("gsheet_range")
            or state_srg
            or ""
        ).strip()
        if sheet_id:
            csv_generated_bytes = _fetch_gsheet_csv_bytes(doc_path, sheet_id, sheet_range)
            if csv_generated_bytes:
                csv_name = _default_csv_for_svg(svg_name)
                ext_man.setdefault("gsheet_id", sheet_id)
                if sheet_range:
                    ext_man.setdefault("gsheet_range", sheet_range)

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
        if csv_abs is not None and csv_abs.is_file():
            _zip_add_file(zf, csv_abs, csv_name or csv_abs.name)
        elif csv_generated_bytes is not None and csv_name:
            zf.writestr(csv_name, csv_generated_bytes, compress_type=zipfile.ZIP_DEFLATED)
        for src, arc in sorted(asset_map.items(), key=lambda kv: kv[1]):
            _zip_add_file(zf, src, arc)

    stream.write(out.getvalue())
    csv_ok = bool((csv_abs and csv_abs.is_file()) or (csv_generated_bytes is not None and csv_name))
    _l.i(f"[{kind}] export ok: svg='{svg_name}' assets={len(asset_map)} csv={'yes' if csv_ok else 'no'}")


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
    _l.i(f"[pkg] extracted into '{base_dir}'")
    return PackageInfo(kind=str(manifest.get("format") or ""), base_dir=base_dir, svg_abs=svg_abs, manifest=manifest)


def _run_deckmaker_if_requested(svg_root, info: PackageInfo):
    want = bool(info.manifest.get("run_deckmaker_on_import", False))
    if not want:
        return svg_root
    try:
        import engine as ENG
    except Exception as ex:
        _l.w(f"[pnp] deckmaker import requested, but engine import failed: {ex}")
        return svg_root

    csv_rel = str(info.manifest.get("csv") or "").strip()
    csv_path = str(_safe_join(info.base_dir, csv_rel)) if csv_rel else ""
    has_packaged_csv = bool(csv_path and os.path.isfile(csv_path))
    sheet_id = "" if has_packaged_csv else str(info.manifest.get("gsheet_id") or "").strip()
    sheet_range = "" if has_packaged_csv else str(info.manifest.get("gsheet_range") or "").strip()
    preset = str(info.manifest.get("preset") or "{A4}")
    preloaded_datasets = None
    if has_packaged_csv and str(info.manifest.get("gsheet_id") or "").strip():
        _l.i(f"[pnp] using packaged CSV on import, ignoring manifest gsheet_id for deterministic render: '{csv_rel}'")
    if has_packaged_csv:
        try:
            import dataset as DS

            preloaded_datasets = DS.load_csv_datasets(csv_path)
            _l.i(f"[pnp] preloaded packaged CSV datasets={len(preloaded_datasets or [])} path='{csv_rel}'")
        except Exception as ex:
            _l.w(f"[pnp] could not preload packaged CSV '{csv_rel}': {ex}")
            preloaded_datasets = None

    class _Runner:
        def __init__(self, root, path, datasets=None):
            self.svg = root
            self._path = str(path)
            self._dm_output_disabled = True
            if datasets is not None:
                self._dm_preloaded_datasets = datasets
            self.options = SimpleNamespace(
                tab="",
                csv_path=csv_path,
                sheet_id=sheet_id,
                sheet_range=sheet_range,
                prototypes_layer="Prototypes",
                preset=preset,
                stop_on_error=False,
                log_level="global",
            )

        def document_path(self):
            return self._path

        def _document_path_or_abort(self):
            p = self.document_path()
            if not p or not os.path.isabs(p) or not os.path.isfile(p):
                raise inkex.AbortExtension("Save document before running DeckMaker.")
            return os.path.normpath(p)

        def _find_or_create_layer(self, root, label: str):
            for child in list(root):
                try:
                    if not (hasattr(child, "tag") and isinstance(child.tag, str) and child.tag.endswith("g")):
                        continue
                    if child.get(inkex.addNS("groupmode", "inkscape")) == "layer":
                        if (child.get(inkex.addNS("label", "inkscape")) or "") == label:
                            return child
                except Exception:
                    continue
            layer = inkex.Group()
            layer.set(inkex.addNS("groupmode", "inkscape"), "layer")
            layer.set(inkex.addNS("label", "inkscape"), label)
            root.append(layer)
            return layer

    try:
        runner = _Runner(svg_root, info.svg_abs, preloaded_datasets)
        ENG.run(runner, "pnp-import")
        _l.i("[pnp] deckmaker auto-run on import: OK")
        return runner.svg
    except Exception as ex:
        _l.w(f"[pnp] deckmaker auto-run on import failed: {ex}")
        return svg_root


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
        root2 = _run_deckmaker_if_requested(root, info)
        if root2 is not root:
            try:
                doc._setroot(root2)
            except Exception:
                doc = inkex.etree.ElementTree(root2)

    _l.i(f"[{kind}] import ok: svg='{info.svg_abs}'")
    return doc
