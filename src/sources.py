# [2026-02-18] Fix: add missing re import for source token parsing.
# [2026-02-18] Log: track source cache hits/misses for inline-icons audit.
# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
"""
sources.py ? PnPInk v07 (Phase 1)
External source manager -> <symbol> in <defs> + later <use> clones.

Supports:
  - Local bitmaps (relative/absolute paths; heuristic folder+extension resolution).
  - data: URIs (like <image> embedded inside a <symbol>).
  - Cross-OS normalization and dedupe by (abs_path + mtime).

Roadmap (Phase 2):
  - External SVGs (with/without #fragment) -> clean import to <symbol>.
  - HTTP(S) with cache and ETag/Last-Modified.
  - PDF (rasterized to cache or <image> if runtime allows).
"""
from __future__ import annotations  # <-- MOVED ABOVE

__version__ = "v0.1"

from dataclasses import dataclass
from typing import Optional, Dict, Tuple, List, Set
import os, re, hashlib, threading, urllib.request, urllib.error, shutil, mimetypes, ssl
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from urllib.parse import urlparse, unquote

import inkex
import log as LOG
_l = LOG
import svg as SVG
import const as CONST
import sources_web as WEB

# --------------------------------------------------------------------------------------
# Iconify integration (icon://set/name)
# --------------------------------------------------------------------------------------
try:
    import iconify as ICON
except Exception:
    ICON = None
# ======================================================================================
# Datamodel / tipos
# ======================================================================================

@dataclass(frozen=True)
class SourceKey:
    scheme: str      # "file" | "data" | "logical"
    path: str        # abs path (normcase) OR data-uri head OR logical name
    mtime: float     # 0.0 para data/logical sin file backing
    fragment: str = ""
    page: int = 0

@dataclass
class SourceRef:
    symbol_id: str
    content_type: str               # "bitmap" | "svg" | "pdf" | "other"
    intrinsic_box: Tuple[float,float]  # (W, H) en unidades de usuario del documento
    preserve_aspect: str = "xMidYMid meet"
    canonical_key: Optional[SourceKey] = None


# ======================================================================================
# Spritesheets (composite sources)
# ======================================================================================

@dataclass
class SpriteSheetDef:
    """Runtime definition of a spritesheet (grid cut) over a bitmap source.

    Coordinates live in SVG document user units. Sizing is driven by Layout{shape,grid,gaps}
    (not by the bitmap pixel dimensions), so frames can be used like any other target and
    positioned via Fit/Anchor.
    """
    alias: str
    src_raw: str                      # original src string (e.g. 'img/sheet.png')
    abspath: Optional[Path]
    cols: int
    rows: int
    tile_w_px: float
    tile_h_px: float
    gap_x_px: float
    gap_y_px: float
    sweep_rows_first: bool = True     # LR-TB (rows first) vs TB-LR
    invert_rows: bool = False         # flip=v
    invert_cols: bool = False         # flip=h
    base_symbol_id: Optional[str] = None
    base_image_id: Optional[str] = None
    shared_clip_id: Optional[str] = None

    @property
    def sheet_w_px(self) -> float:
        return float(self.cols) * float(self.tile_w_px) + max(0, self.cols-1) * float(self.gap_x_px)

    @property
    def sheet_h_px(self) -> float:
        return float(self.rows) * float(self.tile_h_px) + max(0, self.rows-1) * float(self.gap_y_px)


# ======================================================================================
# Config / constantes
# ======================================================================================

# Extension priority order (no explicit extension in input)
EXT_PRIORITY = [
    "png", "jpeg", "jpg", "JPG", "svg", "svgz", "webp",
]

# Subcarpetas sugeridas (en orden) relativas al SVG
REL_DIRS = ["img", "imgs", "images", "assets"]

# Derived from SVG name
#   mydoc.svg → mydoc_img / mydoc_assets
DERIVED_SUFFIXES = ["_img", "_assets"]

# Default size (if we cannot inspect the image)
DEFAULT_W = 100.0
DEFAULT_H = 100.0

# Iconify default set (when using icon://name without a set)
DEFAULT_ICONIFY_SET = "noto"


# ======================================================================================
# Utilidades privadas
# ======================================================================================

def _norm_sep(s: str) -> str:
    # normalize separators to '/', for stable logs and comparisons
    return s.replace("\\", "/")

def _normcase_path(p: Path) -> str:
    # Case normalization per OS (Windows insensitive)
    try:
        s = str(p.resolve())
    except Exception:
        s = str(p)
    return os.path.normcase(s)

def _expand_user_env(s: str) -> str:
    if not s: return s
    try:
        s = os.path.expandvars(s)
    except Exception:
        pass
    try:
        s = os.path.expanduser(s)
    except Exception:
        pass
    return s

def _stat_mtime(p: Path) -> float:
    try:
        return float(p.stat().st_mtime)
    except Exception:
        return 0.0

def _make_placeholder_symbol(defs, key_str: str, reason: str) -> Tuple[str, Tuple[float,float]]:
    sid = _unique_symbol_id(defs, "src_missing_")
    sym = SVG.etree.SubElement(defs, inkex.addNS('symbol','svg'))
    sym.set('id', sid)
    sym.set('viewBox', f"0 0 {DEFAULT_W} {DEFAULT_H}")
    g = SVG.etree.SubElement(sym, inkex.addNS('g','svg'))
    r = SVG.etree.SubElement(g, inkex.addNS('rect','svg'))
    r.set('x',"0"); r.set('y',"0"); r.set('width', str(DEFAULT_W)); r.set('height', str(DEFAULT_H))
    r.set('fill', "#f8d7da"); r.set('stroke', "#721c24"); r.set('stroke-width', "1")
    t = SVG.etree.SubElement(g, inkex.addNS('text','svg'))
    t.set('x', "4"); t.set('y', "16"); t.set('font-size', "12")
    t.text = f"MISSING: {reason}"
    t2 = SVG.etree.SubElement(g, inkex.addNS('text','svg'))
    t2.set('x', "4"); t2.set('y', "32"); t2.set('font-size', "10")
    t2.text = key_str[:48]
    _l.w(f"[sources] placeholder '{sid}': {reason} — {key_str}")
    return sid, (DEFAULT_W, DEFAULT_H)

def _unique_symbol_id(defs, prefix: str) -> str:
    root = defs.getroottree().getroot()
    try:
        return root.get_unique_id(prefix)
    except Exception:
        # fallback, poco probable
        base = prefix.rstrip("_")
        i = 1
        while True:
            cand = f"{base}_{i}"
            if not root.xpath(f".//*[@id='{cand}']"):
                return cand
            i += 1

def _guess_bitmap_size_px(abspath: Path, default_dpi: float = SVG.DPI) -> Tuple[float,float]:
    """
    Return (W,H) in px for the bitmap. Try Pillow; if not, return default size.
    """
    try:
        from PIL import Image  # opcional
        with Image.open(str(abspath)) as im:
            w, h = im.size
            return float(w), float(h)
    except Exception:
        _l.w(f"[sources] Pillow no disponible o fallo leyendo '{abspath.name}'; usando {DEFAULT_W}x{DEFAULT_H}px")
        return (DEFAULT_W, DEFAULT_H)

def _is_data_uri(s: str) -> bool:
    return isinstance(s, str) and s.strip().lower().startswith("data:")

def _data_uri_head(s: str) -> str:
    # Canonical key for data: URIs (we do not store full content to avoid bloating keys)
    return (s[:80] + "...") if len(s) > 80 else s

def _build_key_for_path(p: Path, fragment: str = "", page: int = 0) -> SourceKey:
    return SourceKey(
        scheme="file",
        path=_normcase_path(p),
        mtime=_stat_mtime(p),
        fragment=fragment or "",
        page=page or 0,
    )

def _build_key_for_data(head: str) -> SourceKey:
    return SourceKey(scheme="data", path=head, mtime=0.0)


def _build_key_for_iconify(prefix: str, name: str) -> SourceKey:
    # Canonical key (no mtime backing)
    return SourceKey(scheme="icon", path=f"{(prefix or '').lower()}:{name}", mtime=0.0)

def _build_key_for_url(url: str) -> SourceKey:
    return SourceKey(scheme="url", path=(url or "").strip(), mtime=0.0)

def _build_key_for_wkmc(expr: str) -> SourceKey:
    return SourceKey(scheme="wkmc", path=(expr or "").strip(), mtime=0.0)

def _build_key_for_pxby(expr: str) -> SourceKey:
    return SourceKey(scheme="pxby", path=(expr or "").strip(), mtime=0.0)

def _build_key_for_oclp(expr: str) -> SourceKey:
    return SourceKey(scheme="oclp", path=(expr or "").strip(), mtime=0.0)

def _try_resolve_as_is(candidate: Path) -> Optional[Path]:
    try:
        if candidate.is_file():
            return candidate.resolve()
    except Exception:
        pass
    return None


# ======================================================================================
# Folder/extension resolution (heuristic)
# ======================================================================================

class PathResolver:
    def __init__(self, svg_path: Optional[str], project_root: Optional[str] = None,
                 relaxed_case: bool = (os.name == "nt")):
        self.svg_path = Path(svg_path).resolve() if svg_path else None
        self.project_root = Path(project_root).resolve() if project_root else None
        self.relaxed_case = bool(relaxed_case)

    # ---- exact folder order, as requested ----
    def candidate_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        if self.svg_path:
            base = self.svg_path.parent
            # 1) SVG folder
            dirs.append(base)
            # 2) standard subfolders
            for sub in REL_DIRS:
                d = (base / sub)
                if d.is_dir(): dirs.append(d)
            # 3) derived from SVG name
            stem = self.svg_path.stem
            for suf in DERIVED_SUFFIXES:
                d = (base / f"{stem}{suf}")
                if d.is_dir(): dirs.append(d)
        # 4) project_root/img, project_root/assets
        if self.project_root:
            for sub in ("img", "assets"):
                d = (self.project_root / sub)
                if d.is_dir(): dirs.append(d)
        # dedupe preservando orden
        seen = set(); out = []
        for d in dirs:
            key = _normcase_path(d)
            if key not in seen:
                seen.add(key); out.append(d)
        return out

    def ext_priority(self) -> List[str]:
        # En sistemas case-insensitive, colapsamos "jpg"/"JPG"
        if self.relaxed_case:
            return ["png", "jpeg", "jpg", "svg", "svgz", "webp"]
        return EXT_PRIORITY[:]

    def resolve_logical(self, name: str) -> Optional[Path]:
        """
        name can include subfolders (e.g. tiles/t01).
        If name includes an extension, try as-is in folder order.
        Otherwise, try extensions in self.ext_priority().
        """
        name = name.strip()
        name = _norm_sep(name)
        stem, ext = os.path.splitext(name)
        dirs = self.candidate_dirs()

        def _match_case(p: Path) -> Optional[Path]:
            if p.exists():
                return p.resolve()
            if not self.relaxed_case:
                return None
            # En Windows, prueba a encontrar un match case-insensitive
            parent = p.parent
            try:
                for child in parent.iterdir():
                    if child.name.lower() == p.name.lower():
                        return child.resolve()
            except Exception:
                pass
            return None

        if ext:
            for d in dirs:
                cand = d / name
                hit = _match_case(cand)
                if hit and hit.is_file():
                    return hit
            return None

        for d in dirs:
            for ex in self.ext_priority():
                cand = d / f"{name}.{ex}"
                hit = _match_case(cand)
                if hit and hit.is_file():
                    return hit
        return None


# ======================================================================================
# SourceManager (Fase 1: bitmaps + data:)
# ======================================================================================

class SourceManager:
    """
    Gestor por documento. Mantiene cache de símbolos, resuelve rutas y crea <symbol>.

    Uso típico:
      sm = SourceManager(svg_root, svg_real_path, project_root=..., default_dpi=96)
      ref = sm.register("@logo")              # o sm.register("img/logo") o sm.register("data:...")
      use = sm.ensure_use(parent_node, ref)   # inserts <use href="#symbol"> ready for Fit
    """
    def __init__(self,
                 svg_root: inkex.SvgDocumentElement,
                 svg_real_path: Optional[str],
                 *,
                 project_root: Optional[str] = None,
                 default_dpi: float = SVG.DPI,
                 relaxed_case: bool = (os.name == "nt"),
                 resolve_strict: bool = False):
        self.root = svg_root
        self.svg_real_path = svg_real_path
        self.project_root = project_root
        self.default_dpi = float(default_dpi or SVG.DPI)
        self.resolver = PathResolver(svg_real_path, project_root, relaxed_case)
        self.resolve_strict = bool(resolve_strict)
        self.defs = SVG.ensure_defs(svg_root)
        self._cache: Dict[SourceKey, SourceRef] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        SVG.ensure_xlink_ns(self.root)
        self.assets_dir = self._ensure_assets_dir()
        self.web = WEB.WebSources(self.assets_dir)
        self._dl_lock = threading.RLock()
        self._dl_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pnpink-src")
        self._dl_futures: Dict[str, Future] = {}
        self._dl_done: Dict[str, Optional[Path]] = {}

        # doc unit conversion helpers
        try:
            self._uu_per_px = float(self.root.unittouu('1px'))
        except Exception:
            # fallback: assume 96 dpi and mm user units if unknown
            self._uu_per_px = 25.4/96.0
        _l.d(f"[sources] uu_per_px={self._uu_per_px:.8f} (doc user units per px)")

        # spritesheets
        self._spritesheets: Dict[str, SpriteSheetDef] = {}
        self._sprite_frames: Dict[tuple, SourceRef] = {}

    def _ensure_assets_dir(self) -> Path:
        try:
            if self.svg_real_path:
                base = Path(self.svg_real_path).resolve().parent
            elif self.project_root:
                base = Path(self.project_root).resolve()
            else:
                base = Path.cwd().resolve()
        except Exception:
            base = Path.cwd()
        assets = base / "assets"
        try:
            assets.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return assets

    def resolve_wkmc_urls(self, expr: str) -> Optional[List[str]]:
        return self.web.resolve_wkmc_urls(expr)

    def resolve_pxby_urls(self, expr: str) -> Optional[List[str]]:
        return self.web.resolve_pxby_urls(expr)

    def resolve_oclp_urls(self, expr: str) -> Optional[List[str]]:
        return self.web.resolve_oclp_urls(expr)

    @staticmethod
    def _guess_ext_from_url_or_type(url: str, content_type: str = "") -> str:
        try:
            p = urlparse(url)
            sx = (Path(unquote(p.path or "")).suffix or "").lower()
            if sx in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".svgz"):
                return sx
        except Exception:
            pass
        ct = (content_type or "").split(";", 1)[0].strip().lower()
        ext = mimetypes.guess_extension(ct) if ct else None
        if ext and ext.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".svgz"):
            return ext.lower()
        return ".bin"

    def _http_cache_path(self, url: str, *, ext_hint: str = ".bin") -> Path:
        h = hashlib.sha256((url or "").encode("utf-8")).hexdigest()
        ext = ext_hint if (ext_hint or "").startswith(".") else f".{ext_hint}"
        return self.assets_dir / f"web_{h}{ext}"

    def _find_http_cached_any(self, url: str) -> Optional[Path]:
        try:
            h = hashlib.sha256((url or "").encode("utf-8")).hexdigest()
            for p in self.assets_dir.glob(f"web_{h}.*"):
                # Ignore in-progress temp files (e.g. *.jpg.part). Returning those
                # creates broken links once the downloader atomically renames them.
                if p.is_file() and (not p.name.lower().endswith(".part")):
                    return p.resolve()
        except Exception:
            pass
        return None

    def _download_http_to_cache(self, url: str) -> Optional[Path]:
        def _do_download(unverified: bool = False) -> Optional[Path]:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "pnpink/1.0 (+https://github.com/pnpink)",
                    "Accept": "image/*,*/*;q=0.8",
                },
            )
            ctx = ssl._create_unverified_context() if unverified else None
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                ctype = str(resp.headers.get("Content-Type") or "")
                ext = self._guess_ext_from_url_or_type(url, ctype)
                out = self._http_cache_path(url, ext_hint=ext)
                if out.is_file():
                    return out.resolve()
                tmp = out.with_suffix(out.suffix + ".part")
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f)
                os.replace(str(tmp), str(out))
                _l.i(f"[sources] web cached -> {out.name}")
                return out.resolve()

        try:
            ext_hint = self._guess_ext_from_url_or_type(url, "")
            out0 = self._http_cache_path(url, ext_hint=ext_hint)
            if out0.is_file():
                return out0.resolve()
            return _do_download(unverified=False)
        except urllib.error.URLError as ex:
            msg = str(ex)
            if "CERTIFICATE_VERIFY_FAILED" in msg:
                try:
                    _l.w(f"[sources] SSL verify failed for '{url}', retrying unverified TLS")
                    return _do_download(unverified=True)
                except Exception as ex2:
                    _l.w(f"[sources] web download failed '{url}' after unverified retry: {ex2}")
                    return None
            _l.w(f"[sources] web download failed '{url}': {ex}")
            return None
        except Exception as ex:
            _l.w(f"[sources] web download failed '{url}': {ex}")
            return None

    def _ensure_http_future(self, url: str) -> Future:
        u = (url or "").strip()
        with self._dl_lock:
            if u in self._dl_done:
                f = Future()
                f.set_result(self._dl_done.get(u))
                return f
            f0 = self._dl_futures.get(u)
            if f0 is not None:
                return f0

            def _job():
                p = self._download_http_to_cache(u)
                with self._dl_lock:
                    self._dl_done[u] = p
                    self._dl_futures.pop(u, None)
                return p

            f = self._dl_pool.submit(_job)
            self._dl_futures[u] = f
            return f

    def _resolve_http_cached_file(self, url: str, *, wait: bool) -> Optional[Path]:
        u = (url or "").strip()
        if not u:
            return None
        ext_hint = self._guess_ext_from_url_or_type(u, "")
        p0 = self._http_cache_path(u, ext_hint=ext_hint)
        if p0.is_file():
            return p0.resolve()
        p_any = self._find_http_cached_any(u)
        if p_any is not None:
            return p_any

        f = self._ensure_http_future(u)
        if not wait:
            if not f.done():
                return None
            try:
                return f.result()
            except Exception:
                return None
        try:
            return f.result(timeout=120)
        except Exception as ex:
            _l.w(f"[sources] web wait failed '{u}': {ex}")
            return None

    def prefetch_urls(self, urls: List[str]) -> int:
        n = 0
        for u in sorted(set((x or "").strip() for x in (urls or []) if x)):
            if not re.match(r"^https?://", u, re.I):
                continue
            self._ensure_http_future(u)
            n += 1
        if n:
            _l.i(f"[sources] web prefetch scheduled: {n} url(s)")
        return n

    def _extract_http_from_source_body(self, body: str) -> Optional[str]:
        b = (body or "").strip()
        if not b:
            return None
        if re.match(r"^https?://", b, re.I):
            return b
        try:
            import dsl as DSL
            cmd = DSL.maybe_parse(f"@{{{b}}}")
            if cmd and getattr(cmd, "name", "") == "Source" and getattr(cmd, "target", None) is not None:
                src = ""
                try:
                    src = str(getattr(cmd.target, "src", "") or "")
                except Exception:
                    src = ""
                if src and re.match(r"^https?://", src, re.I):
                    return src
        except Exception:
            pass
        return None

    def extract_web_urls_from_text(self, text: str) -> Set[str]:
        s = str(text or "")
        out: Set[str] = set()
        s_trim = s.strip()
        if re.match(r"^https?://\S+$", s_trim, re.I):
            out.add(s_trim)

        for m in re.finditer(r"@\{\s*([^}]*)\s*\}", s):
            u = self._extract_http_from_source_body(m.group(1))
            if u:
                out.add(u)

        for m in re.finditer(r"(?:^|[\s])(?:Source|S)\s*\{\s*([^}]*)\s*\}", s, re.I):
            u = self._extract_http_from_source_body(m.group(1))
            if u:
                out.add(u)
        return out

    def prefetch_dataset_rows(self, rows: List[dict]) -> int:
        urls: Set[str] = set()
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            cells = row.get("cells")
            if isinstance(cells, list):
                for v in cells:
                    urls.update(self.extract_web_urls_from_text("" if v is None else str(v)))
            for k, v in row.items():
                if k == "cells" or v is None:
                    continue
                if isinstance(v, str):
                    urls.update(self.extract_web_urls_from_text(v))
        return self.prefetch_urls(sorted(urls))

    # ---------------- high-level resolution ----------------

    def register(self, uri_or_name: str, *, hint_type: Optional[str] = None,
                 dpi: Optional[float] = None, fragment: Optional[str] = None,
                 page: Optional[int] = None) -> SourceRef:
        """
        uri_or_name:
          - "file:/.../logo.png", "C:/images/logo.png", "img/logo", "tiles/t01", "data:..."
          - logical name without extension: "@logo" -> pass "logo" (the @... parser already strips '@')
        """
        raw = (uri_or_name or "").strip()
        if not raw:
            sid, wh = _make_placeholder_symbol(self.defs, "(empty)", "empty source")
            return SourceRef(symbol_id=sid, content_type="other", intrinsic_box=wh, canonical_key=None)

        # Allow dataset tokens like "@icon://..." to be passed through directly.
        if raw.lower().startswith("@icon://"):
            raw = raw[1:]

        
        # Spritesheet frame shorthand inside @{...}: "alias[frame]" (1-based).
        # This allows inline_icons and generic sources to reference spritesheet frames
        # without going through @alias[...] (which is a different DSL token kind).
        m_sp = re.match(r"^(?P<alias>[A-Za-z][\w\-]*)\[(?P<frame>\d+)\]$", raw)
        if m_sp:
            a = m_sp.group("alias")
            if a in self._spritesheets:
                try:
                    fr = int(m_sp.group("frame"))
                except Exception:
                    fr = None
                if fr is not None:
                    ref_sp = self.register_spritesheet_frame(a, frame=fr)
                    if ref_sp is not None:
                        return ref_sp

# Iconify v2: icon://set/name  (PnPInk: ':' is not allowed as a separator)
        parsed0 = urlparse(raw)
        if parsed0.scheme.lower() in ("icon", "iconify"):
            if ICON is None:
                sid, wh = _make_placeholder_symbol(self.defs, raw, "iconify.py no disponible")
                return SourceRef(symbol_id=sid, content_type="svg", intrinsic_box=wh, canonical_key=None)

            # icon://prefix/name  (prefix in netloc, name in path)
            prefix = (parsed0.netloc or "").strip()
            name = (parsed0.path or "").lstrip("/")

            # Compat icon://prefix:name (desaconsejado)
            if (not prefix or not name) and (parsed0.netloc and ":" in parsed0.netloc) and not (parsed0.path or "").strip("/"):
                try:
                    prefix, name = parsed0.netloc.split(":", 1)
                except Exception:
                    prefix, name = "", ""

            prefix = (prefix or "").strip()
            name = (name or "").strip()

            # Supports icon://name (no set) -> uses DEFAULT_ICONIFY_SET
            if prefix and not name:
                name = prefix
                prefix = DEFAULT_ICONIFY_SET

            if (":" in prefix) or (":" in name):
                # PnPInk rule: ':' is not a valid separator inside the token
                sid, wh = _make_placeholder_symbol(self.defs, raw, "icon:// requires set/name (use '/')")
                return SourceRef(symbol_id=sid, content_type="svg", intrinsic_box=wh, canonical_key=None)

            if not prefix or not name:
                sid, wh = _make_placeholder_symbol(self.defs, raw, "icon:// requiere set/name")
                return SourceRef(symbol_id=sid, content_type="svg", intrinsic_box=wh, canonical_key=None)

            key = _build_key_for_iconify(prefix, name)
            if key in self._cache:
                self._cache_hits += 1
                return self._cache[key]
            self._cache_misses += 1

            try:
                sym, sym_id = ICON.ensure_icon_symbol(self.root, prefix, name)
                # intrinsic from viewBox (ya normalizado a cuadrado)
                vb = (sym.get("viewBox") or "").strip()
                parts = vb.replace(",", " ").split()
                if len(parts) == 4:
                    w = float(parts[2]); h = float(parts[3])
                else:
                    w = h = 24.0
                ref = SourceRef(symbol_id=sym_id, content_type="svg", intrinsic_box=(w, h),
                                preserve_aspect="xMidYMid meet", canonical_key=key)
                self._cache[key] = ref
                _l.i(f"[sources] iconify: registered → {sym_id} ({w}x{h})")
                return ref
            except Exception as ex:
                sid, wh = _make_placeholder_symbol(self.defs, raw, f"iconify failed: {ex}")
                return SourceRef(symbol_id=sid, content_type="svg", intrinsic_box=wh, canonical_key=None)

        # Wikimedia Commons virtual source (single-value fallback: first result).
        if raw.lower().startswith("wkmc://"):
            key_w = _build_key_for_wkmc(raw)
            if key_w in self._cache:
                self._cache_hits += 1
                return self._cache[key_w]
            self._cache_misses += 1
            urls = self.resolve_wkmc_urls(raw) or []
            if not urls:
                sid, wh = _make_placeholder_symbol(self.defs, raw, "wkmc no results")
                return SourceRef(symbol_id=sid, content_type="other", intrinsic_box=wh, canonical_key=None)
            if len(urls) > 1:
                _l.w(f"[sources] wkmc produced {len(urls)} results; using first (use selector outside source)")
            ref = self.register(urls[0], hint_type=hint_type, dpi=dpi, fragment=fragment, page=page)
            self._cache[key_w] = ref
            return ref

        # Pixabay virtual source (single-value fallback: first result).
        if raw.lower().startswith("pxby://"):
            key_p = _build_key_for_pxby(raw)
            if key_p in self._cache:
                self._cache_hits += 1
                return self._cache[key_p]
            self._cache_misses += 1
            urls = self.resolve_pxby_urls(raw) or []
            if not urls:
                sid, wh = _make_placeholder_symbol(self.defs, raw, "pxby no results")
                return SourceRef(symbol_id=sid, content_type="other", intrinsic_box=wh, canonical_key=None)
            if len(urls) > 1:
                _l.w(f"[sources] pxby produced {len(urls)} results; using first (use selector outside source)")
            ref = self.register(urls[0], hint_type=hint_type, dpi=dpi, fragment=fragment, page=page)
            self._cache[key_p] = ref
            return ref

        # Openclipart virtual source (single-value fallback: first result).
        if raw.lower().startswith("oclp://"):
            key_o = _build_key_for_oclp(raw)
            if key_o in self._cache:
                self._cache_hits += 1
                return self._cache[key_o]
            self._cache_misses += 1
            urls = self.resolve_oclp_urls(raw) or []
            if not urls:
                sid, wh = _make_placeholder_symbol(self.defs, raw, "oclp no results")
                return SourceRef(symbol_id=sid, content_type="other", intrinsic_box=wh, canonical_key=None)
            if len(urls) > 1:
                _l.w(f"[sources] oclp produced {len(urls)} results; using first (use selector outside source)")
            ref = self.register(urls[0], hint_type=hint_type, dpi=dpi, fragment=fragment, page=page)
            self._cache[key_o] = ref
            return ref

        # a) data: URI
        if _is_data_uri(raw):
            key = _build_key_for_data(_data_uri_head(raw))
            if key in self._cache:
                self._cache_hits += 1
                return self._cache[key]
            self._cache_misses += 1
            sid, wh = self._create_symbol_from_data_uri(raw)
            ref = SourceRef(symbol_id=sid, content_type="bitmap", intrinsic_box=wh,
                            preserve_aspect="xMidYMid meet", canonical_key=key)
            self._cache[key] = ref
            _l.i(f"[sources] data: registered → {sid} ({wh[0]}x{wh[1]}px)")
            return ref

        # b) file: URI o path absoluto
        parsed = urlparse(raw)
        resolved_file: Optional[Path] = None
        if parsed.scheme.lower() == "file":
            try:
                path = Path(unquote(parsed.path or ""))
                if os.name == "nt" and str(path).startswith("/") and len(str(path))>3 and str(path)[2]==":":
                    path = Path(str(path)[1:])
                resolved_file = _try_resolve_as_is(path)
            except Exception:
                resolved_file = None
        elif parsed.scheme.lower() in ("http", "https"):
            key_url = _build_key_for_url(raw)
            if key_url in self._cache:
                self._cache_hits += 1
                return self._cache[key_url]
            self._cache_misses += 1
            cached_file = self._resolve_http_cached_file(raw, wait=True)
            if cached_file is None:
                sid, wh = _make_placeholder_symbol(self.defs, raw, "web download failed")
                return SourceRef(symbol_id=sid, content_type="other", intrinsic_box=wh, canonical_key=None)
            key = _build_key_for_path(cached_file, fragment or "", int(page or 0))
            if key in self._cache:
                ref = self._cache[key]
            else:
                ref = self._create_symbol_from_bitmap_path(cached_file, dpi=dpi or self.default_dpi)
                ref.canonical_key = key
                self._cache[key] = ref
            self._cache[key_url] = ref
            _l.i(f"[sources] web: registered -> {ref.symbol_id} <- {cached_file.name}")
            return ref
        else:
            # No scheme -> relative/absolute path or logical name
            raw_fs = _expand_user_env(raw)
            candidate = Path(raw_fs)
            if candidate.is_absolute():
                resolved_file = _try_resolve_as_is(candidate)
            else:
                # logical name (no extension) or relative
                resolved_file = None
                if self.svg_real_path:
                    base = Path(self.svg_real_path).parent
                    hit = _try_resolve_as_is((base / candidate))
                    if hit: resolved_file = hit
                if resolved_file is None:
                    resolved_file = self.resolver.resolve_logical(_norm_sep(raw_fs))

        if resolved_file:
            key = _build_key_for_path(resolved_file, fragment or "", int(page or 0))
            if key in self._cache:
                self._cache_hits += 1
                return self._cache[key]
            self._cache_misses += 1
            ref = self._create_symbol_from_bitmap_path(resolved_file, dpi=dpi or self.default_dpi)
            ref.canonical_key = key
            self._cache[key] = ref
            _l.i(f"[sources] file: registered → {ref.symbol_id} ← {resolved_file.name} ({ref.intrinsic_box[0]}x{ref.intrinsic_box[1]}px)")
            return ref

        # No resuelto → placeholder
        sid, wh = _make_placeholder_symbol(self.defs, raw, "no encontrado")
        return SourceRef(symbol_id=sid, content_type="other", intrinsic_box=wh, canonical_key=None)

    # ---------------- symbol creation ----------------

    def _create_symbol_from_bitmap_path(self, abspath: Path, *, dpi: float) -> SourceRef:
        W, H = _guess_bitmap_size_px(abspath, default_dpi=dpi)

        sid = _unique_symbol_id(self.defs, "src_")
        sym = SVG.etree.SubElement(self.defs, inkex.addNS('symbol','svg'))
        sym.set('id', sid)
        # IMPORTANT: for now we do NOT set viewBox, to better mimic
        # how Inkscape generates symbols from linked images.
        # sym.set('viewBox', f"0 0 {W} {H}")

        im = SVG.etree.SubElement(sym, inkex.addNS('image','svg'))
        im.set('x', "0")
        im.set('y', "0")
        im.set('width',  str(W))
        im.set('height', str(H))

        # Prefer file URI on Linux/Mac; OS path on Windows (consistent with SVG.absolutize_all_linked_images)
        href_val = abspath.as_uri() if os.name != "nt" else str(abspath)
        im.set(inkex.addNS('href','xlink'), href_val)
        im.set('href', href_val)
        im.set(SVG.SODI_ABSREF, str(abspath))

        return SourceRef(
            symbol_id=sid,
            content_type="bitmap",
            intrinsic_box=(W, H),
            preserve_aspect="xMidYMid meet",
        )

    def _create_symbol_from_data_uri(self, data_uri: str) -> Tuple[str, Tuple[float,float]]:
        sid = _unique_symbol_id(self.defs, "src_")
        sym = SVG.etree.SubElement(self.defs, inkex.addNS('symbol','svg'))
        sym.set('id', sid)
        sym.set('viewBox', f"0 0 {DEFAULT_W} {DEFAULT_H}")

        im = SVG.etree.SubElement(sym, inkex.addNS('image','svg'))
        im.set('x', "0"); im.set('y', "0")
        im.set('width',  str(DEFAULT_W))
        im.set('height', str(DEFAULT_H))
        im.set(inkex.addNS('href','xlink'), data_uri)
        im.set('href', data_uri)
        return sid, (DEFAULT_W, DEFAULT_H)

    # ---------------- helpers de clon ----------------

    def ensure_use(self, parent_node, source_ref: SourceRef, *, set_id: Optional[str]=None) -> SVG.etree._Element:
        """
        Insert a <use xlink:href="#symbol_id"> with preserveAspectRatio in parent.
        Leave it without transform so the Fit (FA) pipeline positions/scales it.
        """
        u = SVG.etree.Element(inkex.addNS('use','svg'))
        u.set(inkex.addNS('href','xlink'), f"#{source_ref.symbol_id}")
        u.set('href', f"#{source_ref.symbol_id}")
        u.set('preserveAspectRatio', source_ref.preserve_aspect or "xMidYMid meet")
        if set_id:
            u.set('id', set_id)
        parent_node.append(u)
        return u




    # ----------- spritesheets ----------------

    def register_spritesheet(self, alias: str, *, chain, px_per_mm: float) -> Optional[SpriteSheetDef]:
        """Register a spritesheet alias from a parsed DSL chain.

        Expected chain form:
          @{src}.Layout{grid=COLxROW(^)? shape=PRESET gaps=[gx gy]}

        Notes:
          - We size the bitmap symbol to the computed sheet size (tile+gaps) in px.
          - We do NOT embed the image: we create <image href="..."> and later <use>.
        """
        if not alias:
            return None
        alias_key = str(alias).strip()
        if not alias_key:
            return None

        # Reuse if already registered
        if alias_key in self._spritesheets:
            return self._spritesheets[alias_key]

        # --- Extract src ---
        try:
            tgt = getattr(chain, 'target', None)
            if tgt is None or getattr(tgt, 'stype', None) is None:
                _l.w(f"[spritesheets] alias '{alias_key}': invalid chain target; expected SourceRef")
                return None
            src_raw = getattr(tgt, 'src', '') or ''
        except Exception as ex:
            _l.w(f"[spritesheets] alias '{alias_key}': cannot read src: {ex}")
            return None

        # --- Extract Layout spec ---
        layout_mod = None
        for mc in (getattr(chain, 'modules', None) or []):
            if (getattr(mc, 'name', '') or '').lower() == 'layout':
                layout_mod = mc
                break
        if layout_mod is None or getattr(layout_mod, 'spec', None) is None:
            _l.w(f"[spritesheets] alias '{alias_key}': missing Layout{{...}}")
            return None

        spec = getattr(layout_mod, 'spec', None)
        grid = getattr(spec, 'grid', None)
        shape = getattr(spec, 'shape', None)
        if grid is None:
            _l.w(f"[spritesheets] alias '{alias_key}': Layout needs grid")
            return None

        cols = int(getattr(grid, 'cols', 0) or 0)
        rows = int(getattr(grid, 'rows', 0) or 0)
        if cols <= 0 or rows <= 0:
            _l.w(f"[spritesheets] alias '{alias_key}': invalid grid {cols}x{rows}")
            return None

        # grid order + flip (same semantics as CardPlanner)
        order = (getattr(grid, 'order', None) or '').lower()
        sweep_rows_first = True
        if order in ('tb-lr', 'tblr', 'tb_lr'):
            sweep_rows_first = False
        flip = (getattr(grid, 'flip', None) or '').lower()
        invert_cols = (flip == 'h')
        invert_rows = (flip == 'v')

        # gaps / gap (global grammar already parses it as a numeric list)
        # - With shape preset: interpret as mm and convert with px_per_mm.
        # - Without shape: interpret as px (real gap between atlas cells).
        k = getattr(grid, 'gaps', None)
        gx_raw = gy_raw = 0.0
        try:
            if isinstance(k, (list, tuple)) and len(k) >= 1:
                gx_raw = float(k[0] or 0.0)
            if isinstance(k, (list, tuple)) and len(k) >= 2:
                gy_raw = float(k[1] or 0.0)
        except Exception:
            gx_raw = gy_raw = 0.0

        # resolve src path (bitmap only in this phase)
        # Expand runtime env/home tokens at filesystem-resolution time:
        # Windows: %USERPROFILE%
        # POSIX:   $HOME / ${HOME} / ~
        abspath = None
        src_for_path = str(src_raw or "").strip()
        try:
            # Virtual web sources inside spritesheets: resolve to a concrete URL first.
            sl = src_for_path.lower()
            if sl.startswith("wkmc://"):
                urls = list(self.resolve_wkmc_urls(src_for_path) or [])
                if urls:
                    if len(urls) > 1:
                        _l.w(f"[spritesheets] alias '{alias_key}': wkmc produced {len(urls)} results; using first")
                    src_for_path = str(urls[0] or "").strip()
            elif sl.startswith("pxby://"):
                urls = list(self.resolve_pxby_urls(src_for_path) or [])
                if urls:
                    if len(urls) > 1:
                        _l.w(f"[spritesheets] alias '{alias_key}': pxby produced {len(urls)} results; using first")
                    src_for_path = str(urls[0] or "").strip()
            elif sl.startswith("oclp://"):
                urls = list(self.resolve_oclp_urls(src_for_path) or [])
                if urls:
                    if len(urls) > 1:
                        _l.w(f"[spritesheets] alias '{alias_key}': oclp produced {len(urls)} results; using first")
                    src_for_path = str(urls[0] or "").strip()
        except Exception as ex:
            _l.w(f"[spritesheets] alias '{alias_key}': virtual src resolve failed: {ex}")

        try:
            # If it's a web URL, use the cached local file for spritesheet tiling.
            if re.match(r"^https?://", src_for_path, re.I):
                abspath = self._resolve_http_cached_file(src_for_path, wait=True)
            else:
                src_fs = _expand_user_env(str(src_for_path or ""))
                cand = Path(src_fs)
                if cand.is_absolute():
                    abspath = _try_resolve_as_is(cand)
                else:
                    abspath = self.resolver.resolve_logical(_norm_sep(src_fs))
        except Exception:
            abspath = None

        # --- Determine tile/gap sizing ---
        # Caso A) Con shape preset -> tile size en mm
        # Case B) No shape -> autodetect tile in px from bitmap size + grid + gap(px)
        if shape is None:
            if abspath is None:
                _l.w(f"[spritesheets] alias '{alias_key}': missing shape and cannot resolve src to autodetect tile")
                return None
            sheet_w_px, sheet_h_px = _guess_bitmap_size_px(abspath, default_dpi=self.default_dpi)
            gx = float(gx_raw)
            gy = float(gy_raw)
            try:
                tw = (float(sheet_w_px) - max(0, cols - 1) * gx) / float(cols)
                th = (float(sheet_h_px) - max(0, rows - 1) * gy) / float(rows)
            except Exception:
                tw = float(sheet_w_px) / float(cols)
                th = float(sheet_h_px) / float(rows)
            if tw <= 0 or th <= 0:
                _l.w(
                    f"[spritesheets] alias '{alias_key}': autodetect produced non-positive tile {tw:.3f}x{th:.3f}px; "
                    f"falling back to sheet/grid (no gaps)"
                )
                gx = 0.0
                gy = 0.0
                tw = float(sheet_w_px) / float(cols)
                th = float(sheet_h_px) / float(rows)
            _l.i(
                f"[spritesheet] autodetect tile for @{alias_key}: sheet={sheet_w_px:.2f}x{sheet_h_px:.2f}px "
                f"grid={cols}x{rows} gap_px={gx:.2f}x{gy:.2f} -> tile={tw:.2f}x{th:.2f}px"
            )
            # Convert pixel-based measurements to document user units.
            uu_per_px = float(getattr(self, '_uu_per_px', 1.0) or 1.0)
            tw *= uu_per_px
            th *= uu_per_px
            gx *= uu_per_px
            gy *= uu_per_px
            _l.i(
                f"[spritesheet] autodetect convert @{alias_key}: uu_per_px={uu_per_px:.8f} -> "
                f"tile_u={tw:.3f}x{th:.3f} gap_u={gx:.3f}x{gy:.3f}"
            )
            tw_mm = th_mm = None
            gx_mm = gy_mm = None
        else:
            # shape preset -> tile size (mm)
            preset = None
            if getattr(shape, 'kind', None) == 'preset':
                preset = getattr(shape, 'preset', None)
            if not preset:
                preset = (layout_mod.args.get('shape') if hasattr(layout_mod, 'args') else None) or 'Standard'

            tile_mm = CONST.get_card_size_preset(preset)
            if tile_mm is None:
                _l.w(f"[spritesheets] alias '{alias_key}': unknown card preset '{preset}' → using Standard")
                tile_mm = CONST.get_card_size_preset('Standard') or (63.0, 88.0)

            tw_mm, th_mm = float(tile_mm[0]), float(tile_mm[1])
            px_per_mm_f = float(px_per_mm or 1.0)
            tw = tw_mm * px_per_mm_f
            th = th_mm * px_per_mm_f
            gx_mm = float(gx_raw)
            gy_mm = float(gy_raw)
            gx = gx_mm * px_per_mm_f
            gy = gy_mm * px_per_mm_f

        # NOTE: measurements stored in SpriteSheetDef are in SVG document user units.
        # The field names keep the historical *_px suffix but the values are already converted
        # when shape is None (autodetect from bitmap pixels).
        d = SpriteSheetDef(
            alias=alias_key,
            src_raw=str(src_raw),
            abspath=abspath,
            cols=cols, rows=rows,
            tile_w_px=tw, tile_h_px=th,
            gap_x_px=gx, gap_y_px=gy,
            sweep_rows_first=sweep_rows_first,
            invert_rows=invert_rows,
            invert_cols=invert_cols,
            base_symbol_id=None,
        )
        self._spritesheets[alias_key] = d
        _l.i(
            f"[spritesheets] registered '@{alias_key}' src='{src_raw}' grid={cols}x{rows} "
            f"tile={tw_mm}x{th_mm}mm gap={gx_mm}x{gy_mm}mm order={'LR-TB' if sweep_rows_first else 'TB-LR'} "
            f"flip={'h' if invert_cols else ('v' if invert_rows else '-')}"
        )
        return d


    def register_spritesheets_from_comments(self, comment_lines, *, px_per_mm: float) -> Dict[str, SpriteSheetDef]:
        """Scan dataset comment lines for spritesheet alias definitions and register them.

        Expected comment syntax (in first cell of a comment row):
          # @sp1 = @{sheet.png}.Layout{grid=3x2 ...}

        Returns a dict {alias: SpriteSheetDef} for successfully registered aliases.
        """
        out: Dict[str, SpriteSheetDef] = {}
        if not comment_lines:
            return out

        # Local imports to avoid hard module coupling.
        import re
        try:
            import dsl as DSL
        except Exception:
            DSL = None

        for rr in (comment_lines or []):
            if not rr:
                continue
            try:
                c0 = str(rr[0] or "")
            except Exception:
                continue
            c0s = c0.lstrip()
            if not c0s.startswith("#"):
                continue
            body = c0s[1:].strip()
            if not body.startswith("@"):
                continue

            m = re.match(r"^@(?P<name>[A-Za-z][\w\-\.]*)\s*=\s*(?P<rhs>.+)$", body)
            if not m:
                continue
            alias = m.group("name")
            rhs = (m.group("rhs") or "").strip()
            if not rhs:
                continue

            if DSL is None:
                _l.w(f"[spritesheets] parse skipped for '@{alias}': dsl module not available")
                continue

            try:
                chain = DSL.maybe_parse_chain(rhs)
            except Exception as ex:
                _l.w(f"[spritesheets] parse failed for '@{alias}': {ex}  rhs='{rhs}'")
                continue

            try:
                _l.i(f"[spritesheet] registering @{alias} px_per_mm={px_per_mm:.6f}")
                ss = self.register_spritesheet(alias, chain=chain, px_per_mm=px_per_mm)
                if ss is not None:
                    out[alias] = ss
            except Exception as ex:
                _l.w(f"[spritesheets] register failed '@{alias}': {ex}")
                continue

        if out:
            _l.i(f"[spritesheets] defs={len(out)} → {sorted(out.keys())}")
        else:
            _l.i("[spritesheets] no defs")
        return out

    def _ensure_spritesheet_base_symbol(self, ss: SpriteSheetDef) -> str:
        """Create or reuse the base bitmap <symbol> sized to the sheet geometry."""
        if ss.base_symbol_id:
            return ss.base_symbol_id
        if ss.abspath is None:
            sid, _wh = _make_placeholder_symbol(self.defs, ss.src_raw, "spritesheet src not found")
            ss.base_symbol_id = sid
            return sid

        key = SourceKey(
            scheme='logical',
            path=f"spritesheet:{ss.alias}:{_normcase_path(ss.abspath)}:{ss.sheet_w_px:.3f}x{ss.sheet_h_px:.3f}",
            mtime=_stat_mtime(ss.abspath),
            fragment="",
            page=0
        )
        if key in self._cache:
            self._cache_hits += 1
            ref = self._cache[key]
            ss.base_symbol_id = ref.symbol_id
            return ref.symbol_id
        self._cache_misses += 1

        ref = self._create_symbol_from_bitmap_path(
            ss.abspath,
            dpi=self.default_dpi
        )
        ref.canonical_key = key
        self._cache[key] = ref
        ss.base_symbol_id = ref.symbol_id
        _l.i(f"[spritesheet] base symbol created id={ref.symbol_id} for @{ss.alias} size_u={ss.sheet_w_px:.2f}x{ss.sheet_h_px:.2f}")
        _l.d(f"[spritesheets] base symbol '{ref.symbol_id}' sized {ss.sheet_w_px:.2f}x{ss.sheet_h_px:.2f}px for @{ss.alias}")
        return ref.symbol_id


    def _ensure_spritesheet_base_image(self, ss: SpriteSheetDef) -> str:
        """Ensure a single <image> exists in <defs> for this spritesheet.

        We avoid wrapping the bitmap in an intermediate <symbol> to reduce defs/DOM.
        """
        if ss.base_image_id:
            return ss.base_image_id

        if ss.abspath is None:
            # Fall back to placeholder symbol (keeps old behavior for missing sources).
            sid, _wh = _make_placeholder_symbol(self.defs, ss.src_raw, "spritesheet src not found")
            # NOTE: placeholder is a <symbol>; we keep it here because it's used only on error paths.
            ss.base_image_id = sid
            return sid

        # Stable-ish id per alias
        base_id = f"img_{ss.alias}"
        # If id already exists in defs, reuse it
        try:
            existing = self.defs.xpath(f".//*[@id='{base_id}']")
        except Exception:
            existing = []
        if existing:
            ss.base_image_id = base_id
            return base_id

        im = SVG.etree.SubElement(self.defs, inkex.addNS('image','svg'))
        im.set('id', base_id)
        im.set('x', "0")
        im.set('y', "0")
        im.set('width',  str(float(ss.sheet_w_px)))
        im.set('height', str(float(ss.sheet_h_px)))
        # Preserve pixel mapping (no aspect auto-fit surprises)
        im.set('preserveAspectRatio', 'none')

        href_val = ss.abspath.as_uri() if os.name != "nt" else str(ss.abspath)
        im.set(inkex.addNS('href','xlink'), href_val)
        im.set('href', href_val)
        im.set(SVG.SODI_ABSREF, str(ss.abspath))

        ss.base_image_id = base_id
        _l.i(f"[spritesheet] base image created id={base_id} for @{ss.alias} size_u={ss.sheet_w_px:.2f}x{ss.sheet_h_px:.2f}")
        return base_id

    def _ensure_spritesheet_shared_clip(self, ss: SpriteSheetDef) -> str:
        """Ensure a single <clipPath> exists in <defs> for this spritesheet."""
        if ss.shared_clip_id:
            return ss.shared_clip_id

        clip_id = f"clip_{ss.alias}"
        try:
            existing = self.defs.xpath(f".//*[@id='{clip_id}']")
        except Exception:
            existing = []
        if not existing:
            cp = SVG.etree.SubElement(self.defs, inkex.addNS('clipPath','svg'))
            cp.set('id', clip_id)
            cp.set('clipPathUnits', 'userSpaceOnUse')
            rect = SVG.etree.SubElement(cp, inkex.addNS('rect','svg'))
            rect.set('x', "0"); rect.set('y', "0")
            rect.set('width',  str(float(ss.tile_w_px)))
            rect.set('height', str(float(ss.tile_h_px)))
            _l.d(f"[spritesheet] shared clipPath created id={clip_id} for @{ss.alias} tile_u={ss.tile_w_px:.2f}x{ss.tile_h_px:.2f}")
        ss.shared_clip_id = clip_id
        return clip_id
    def _linear_to_rc(self, n1: int, ss: SpriteSheetDef) -> tuple:
        """Map 1-based linear index to (col,row) 1-based, honoring order/flip."""
        cols = int(ss.cols); rows = int(ss.rows)
        if cols <= 0 or rows <= 0:
            return 1, 1
        within = max(0, int(n1) - 1)
        if ss.sweep_rows_first:
            r0 = within // cols
            c0 = within % cols
        else:
            c0 = within // rows
            r0 = within % rows
        if ss.invert_rows:
            r0 = (rows - 1) - r0
        if ss.invert_cols:
            c0 = (cols - 1) - c0
        r0 = max(0, min(rows-1, int(r0)))
        c0 = max(0, min(cols-1, int(c0)))
        return int(c0 + 1), int(r0 + 1)

    def register_spritesheet_frame(self, alias: str, *, frame: Optional[int] = None,
                                   page: Optional[int] = None, col: Optional[int] = None, row: Optional[int] = None) -> Optional[SourceRef]:
        """Return a SourceRef for a given spritesheet frame.

        Supported selectors (1-based):
          - frame=N (linear 1..N)
          - col=C,row=R
          - page=P,col=C,row=R  (page currently ignored; reserved for PDF spritesheets)
        """
        a = (alias or '').strip()
        if not a:
            return None
        ss = self._spritesheets.get(a)
        if ss is None:
            return None

        p = int(page or 1)
        if frame is not None:
            c, r = self._linear_to_rc(int(frame), ss)
        else:
            c = int(col or 1); r = int(row or 1)
        if c <= 0 or r <= 0:
            return None

        key = (a, int(p), int(c), int(r))
        if key in self._sprite_frames:
            return self._sprite_frames[key]

        base_img_id = self._ensure_spritesheet_base_image(ss)

        tw = float(ss.tile_w_px); th = float(ss.tile_h_px)
        gx = float(ss.gap_x_px);  gy = float(ss.gap_y_px)
        x0 = (c - 1) * (tw + gx)
        y0 = (r - 1) * (th + gy)

        # IMPORTANT (performance/UI): use <g> instead of <symbol> for each frame.
        # Inkscape's Symbols dialog scans <symbol> elements to generate thumbnails,
        # which becomes extremely slow with many raster+clip+transform frames.
        # A <g> referenced by <use href="#..."></use> keeps identical semantics
        # for our pipeline (fit_anchor uses the referenced element's bbox).
        sid = _unique_symbol_id(self.defs, f"sp_{a}_")
        grp = SVG.etree.SubElement(self.defs, inkex.addNS('g','svg'))
        grp.set('id', sid)

        clip_id = self._ensure_spritesheet_shared_clip(ss)

        # Apply the clip at the frame group level.
        # If clip-path is set on the translated <use>, Inkscape applies the clip in the
        # element's transformed user space, effectively moving the clip together with the
        # translation and yielding the same visible area for every frame.
        grp.set('clip-path', f"url(#{clip_id})")

        u = SVG.etree.SubElement(grp, inkex.addNS('use','svg'))
        u.set(inkex.addNS('href','xlink'), f"#{base_img_id}")
        u.set('href', f"#{base_img_id}")
        u.set('transform', f"translate({-x0},{-y0})")

        ref = SourceRef(symbol_id=sid, content_type="bitmap", intrinsic_box=(tw, th), preserve_aspect="xMidYMid meet", canonical_key=None)
        self._sprite_frames[key] = ref
        _l.i(f"[spritesheet] frame def created id={sid} alias=@{a} p={p} c={c} r={r} x0_u={x0:.2f} y0_u={y0:.2f} tw_u={tw:.2f} th_u={th:.2f}")
        _l.d(f"[spritesheets] frame @{a}[p={p} c={c} r={r}] -> {sid} (x0_u={x0:.2f} y0_u={y0:.2f})")
        return ref

# ======================================================================================
# Public API (export)
# ======================================================================================

__all__ = [
    "__version__",
    "SourceManager", "SourceRef",
    "EXT_PRIORITY", "REL_DIRS", "DERIVED_SUFFIXES",
]
