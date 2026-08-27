#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text.py ? inline icons with in-place ?I? spacer (no rich text rebuild)

- ALWAYS reads from <text> (not label).
- Converts rich-visible -> DOM in *all* <text> nodes in scope (with or without :icon:),
  sanitizing unquoted attributes and EMITTING WARNING if conversion fails.
- Inserts in-place <tspan id=...> spacers where :icon: appears (does not touch the rest).
- Measures inline spacers and dynamic text frames through one persistent Inkscape shell.
- Icon centered in the hole [I + letter-spacing]; baseline and center computed
  in local <text> axes (robust against rotations).
"""

from __future__ import annotations
import os, sys, re, math, copy
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Set, Callable
from pathlib import Path

import inkex
import svg as SVG
import const as CONST
from inkex.transforms import Transform

# local modules
sys.path.append(os.path.dirname(__file__))
import log as LOG
import dsl as DSL
import sources as SRC
import fit_anchor as FA
import transform_fx as TFX
import text_measure as TM
import text_decoration as TDEC
import render_tokens as RTK
import prefs
_l = LOG
__version__ = "text.py v7.52 (in-place; persistent-shell measurement; baseline I; vector placement)"

# --------- tweaks ----------
NBSP = "\u00A0"
EPS = 1e-12

EXTRA_RATIO = 0.00   # Extra margin as a fraction of text height.
OVERSHOOT   = 0.98   # Optical baseline support (1.00 = exact).

DEFAULT_SPACER_GLYPH = "I"  # Configurable via --spacer_glyph.
INHERIT_TEXT_ROTATION = True

PX_PER_MM = SVG.PX_PER_MM
NS = SVG.NSS

# Token :icon: with optional props [k=v]
# Inline token: :@{...}...:  (delimited; preferred) — content is a full Source token with optional Fit suffix
# Examples:
#   :@{icon://noto/cat}:
#   :@{icon://noto/cat}~^15:
#   :@{sp1[2]}~o7!:
INLINE_START = ":@{"
INLINE_START_S = ":S{"
INLINE_START_SOURCE = ":Source{"
_INLINE_ID_RX = re.compile(r"^[A-Za-z_][\w\-.]*$")
_INLINE_LOCAL_SOURCE_RX = re.compile(
    r'^\s*(?P<sigil>@)?(?P<path>[^\s\[\]~]+?\.(?:png|jpe?g|gif|bmp|webp|svgz?|pdf|tiff?))\s*'
    r'(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*)))?\s*$',
    re.IGNORECASE,
)
def _bal_find(s: str, i_open: int, ch_open: str, ch_close: str) -> int:
    """Return index of matching closing char for a balanced pair, or -1."""
    depth = 0
    i = i_open
    N = len(s)
    while i < N:
        ch = s[i]
        if ch == ch_open:
            depth += 1
        elif ch == ch_close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def _find_inline_token(s: str, pos: int) -> Optional[Tuple[int,int,str]]:
    """Find next :@{...}...: token starting at pos.
    Returns (start_idx, end_idx_exclusive, inner_expr) where inner_expr starts with '@{'
    and excludes the surrounding ':'.
    """
    # 1) full source token :@{...}...:
    j_src = s.find(INLINE_START, pos)
    src_hit = None
    if j_src >= 0:
        # find matching '}' for the '@{...}'
        i_brace = j_src + 2  # points to '{'
        k = _bal_find(s, i_brace, '{', '}')
        if k >= 0:
            # token ends at the next ':' after the closing brace (suffix allowed between)
            end_colon = s.find(':', k+1)
            if end_colon >= 0:
                inner = s[j_src+1:end_colon]  # starts with "@{"
                src_hit = (j_src, end_colon+1, inner)

    # 2) source token :S{...}...: or :Source{...}...:
    src2_hit = None
    candidates = []
    j_s = s.find(INLINE_START_S, pos)
    if j_s >= 0:
        candidates.append((j_s, "S"))
    j_src_kw = s.find(INLINE_START_SOURCE, pos)
    if j_src_kw >= 0:
        candidates.append((j_src_kw, "Source"))
    if candidates:
        j2, kw = sorted(candidates, key=lambda x: x[0])[0]
        i_brace2 = j2 + (2 if kw == "S" else len(":Source"))
        k2 = _bal_find(s, i_brace2, '{', '}')
        if k2 >= 0:
            end_colon2 = s.find(':', k2 + 1)
            if end_colon2 >= 0:
                inner2 = s[j2 + 1:end_colon2]  # starts with "S{" or "Source{"
                src2_hit = (j2, end_colon2 + 1, inner2)

    # 3) id token :id:
    j_id = s.find(":", pos)
    id_hit = None
    while j_id >= 0:
        if s.startswith(INLINE_START, j_id):
            j_id = s.find(":", j_id + 1)
            continue
        end_colon = s.find(":", j_id + 1)
        if end_colon < 0:
            break
        inner = (s[j_id+1:end_colon] or "").strip()
        if _parse_inline_multi_id_token(inner) is not None:
            id_hit = (j_id, end_colon+1, inner)
            break
        j_id = s.find(":", j_id + 1)

    hits = [h for h in (src_hit, src2_hit, id_hit) if h is not None]
    if not hits:
        return None
    return sorted(hits, key=lambda h: h[0])[0]

def _split_inline_transform_suffixes(token: str):
    s = str(token or "").strip()
    if not s:
        return "", None
    tail = ""
    m_tail = re.match(
        r"^(?P<core>.*?)(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*|[\^!\|].*))?\s*$",
        s,
        re.IGNORECASE,
    )
    if m_tail:
        s = (m_tail.group("core") or "").strip()
        tail = (m_tail.group("tail") or "").strip()
    specs = []
    rx = re.compile(r"^(?P<base>.*?)(?P<mod>\.(?:Transform|T)\s*\{[^{}]*\})\s*$", re.IGNORECASE)
    while True:
        m = rx.match(s)
        if not m:
            break
        try:
            ch = DSL.maybe_parse_chain("X" + (m.group("mod") or ""))
            mod = next((mm for mm in (getattr(ch, "modules", None) or []) if str(getattr(mm, "name", "")).lower() in ("transform", "t")), None)
        except Exception:
            mod = None
        if mod is None or getattr(mod, "spec", None) is None:
            break
        specs.insert(0, getattr(mod, "spec"))
        s = (m.group("base") or "").strip()
    return ((s + tail).strip() if tail else s), TFX.merge_specs(specs)

def _parse_inline_id_token(inner: str):
    s, tr = _split_inline_transform_suffixes(inner)
    m = re.match(
        r"^\s*(?P<id>[A-Za-z_][\w\-.]*)\s*(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$",
        s or "",
        re.IGNORECASE,
    )
    if not m:
        return None
    fit_text = m.group("fit")
    ops_text = m.group("ops")
    compact_ops = m.group("ops_compact")
    if fit_text:
        fit_cmd = DSL.parse(f"X.{fit_text}")
        fs = getattr(fit_cmd, "fit", None)
        suffix = DSL.SourceSuffix(kind="fit", fit=fs, raw_fit_text=fit_text[fit_text.find('{'):])
    elif ops_text is not None:
        suffix = DSL.SourceSuffix(kind="ops", ops=DSL.normalize_ops_suffix(ops_text))
    elif compact_ops:
        suffix = DSL.SourceSuffix(kind="ops", ops=DSL.normalize_ops_suffix(compact_ops))
    else:
        suffix = DSL.SourceSuffix(kind="none")
    return (m.group("id") or "").strip(), suffix, tr

def _parse_inline_multi_id_token(inner: str):
    parts = RTK.split_multivalue(inner)
    if not parts:
        return None
    parsed = [_parse_inline_id_token(part) for part in parts]
    return parsed if all(item is not None for item in parsed) else None

def _parse_source_inner_token(inner: str):
    """Parse inline source inner token (@{...} / S{...} / Source{...}) into (src_uri, suffix)."""
    s = (inner or "").strip()
    if s.startswith("@{"):
        dsl_src, suffix = DSL.split_source_token(s)
        src_uri = (dsl_src.src or "").strip()
        args = dict(getattr(dsl_src, "args", {}) or {})
        if src_uri.lower().startswith(("osm://", "ofm://")):
            view_val = args.get("view") if args.get("view") not in (None, "") else args.get("v")
            if view_val not in (None, ""):
                src_uri = f"{src_uri} view={view_val}"
        return src_uri, suffix

    m = re.match(
        r'^\s*(?:Source|S)\s*\{\s*(?P<body>[^}]*)\s*\}\s*(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*)))?\s*$',
        s,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError("invalid source token")
    body = (m.group("body") or "").strip()
    dsl_src, _suffix0 = DSL.split_source_token(f"@{{{body}}}")
    src_uri = (dsl_src.src or "").strip()
    args = dict(getattr(dsl_src, "args", {}) or {})
    if src_uri.lower().startswith(("osm://", "ofm://")):
        view_val = args.get("view") if args.get("view") not in (None, "") else args.get("v")
        if view_val not in (None, ""):
            src_uri = f"{src_uri} view={view_val}"
    fit_text = m.group("fit")
    legacy_ops = m.group("ops")
    if fit_text:
        fit_cmd = DSL.parse(f"X.{fit_text}")
        fs = getattr(fit_cmd, "fit", None)
        return src_uri, DSL.SourceSuffix(kind="fit", fit=fs, raw_fit_text=fit_text[fit_text.find('{'):])
    if legacy_ops:
        return src_uri, DSL.SourceSuffix(kind="ops", ops=DSL.normalize_ops_suffix(legacy_ops))
    return src_uri, DSL.SourceSuffix(kind="none")

def _parse_inline_local_source_token(inner: str):
    """Parse inline local source shorthand (file.ext / @file.ext)."""
    s = (inner or "").strip()
    m = _INLINE_LOCAL_SOURCE_RX.match(s)
    if not m:
        return None
    src_uri = (m.group("path") or "").strip()
    fit_text = m.group("fit")
    legacy_ops = m.group("ops")
    if fit_text:
        fit_cmd = DSL.parse(f"X.{fit_text}")
        fs = getattr(fit_cmd, "fit", None)
        return src_uri, DSL.SourceSuffix(kind="fit", fit=fs, raw_fit_text=fit_text[fit_text.find('{'):])
    if legacy_ops:
        return src_uri, DSL.SourceSuffix(kind="ops", ops=DSL.normalize_ops_suffix(legacy_ops))
    return src_uri, DSL.SourceSuffix(kind="none")



_ATTR_PAIR_RX = re.compile(r"""(?ix)
    ([a-z_][\w\-]*)\s*=\s*
    (?:
        "([^"]*)" | '([^']*)' | ([^\s,\]]+)
    )""")

_SCALE_RX = re.compile(r"""(?ix)
    scale\(\s*
      ([+\-]?[\d]*\.?[\d]+(?:[eE][+\-]?\d+)?)      
      (?: [,\s]+
          ([+\-]?[\d]*\.?[\d]+(?:[eE][+\-]?\d+)?)
      )?
    \s*\)
""")

# --- sanitize unquoted attributes in <tspan ...> ---
_UNQUOTED_ATTR_RX = re.compile(
    r'(<tspan\b[^>]*?\s)([a-zA-Z_:\-][\w:\-\.]*)(=)([^"\'>\s/][^\s/>]*)',
    re.IGNORECASE
)

def _sanitize_rich_visible(s: str) -> str:
    """Convierte k=v sin comillas dentro de <tspan ...> en k="v"."""
    if "<tspan" not in s:
        return s
    prev = None
    cur = s
    while cur != prev:
        prev = cur
        cur = _UNQUOTED_ATTR_RX.sub(r'\1\2="\4"', cur)
    return cur


@dataclass
class InlineIconItem:
    src_uri: str
    suffix: "DSL.SourceSuffix"
    parsed_transform: Optional["DSL.TransformSpec"] = None
    symbol_id: Optional[str] = None
    intrinsic_wh: Optional[Tuple[float,float]] = None

@dataclass
class TokenItem:
    spacer_id: str
    src_expr: str              # full DSL source token string, e.g. "@{icon://noto/cat}~^15"
    src_uri: str               # resolved URI passed to SourceManager.register (e.g. "icon://noto/cat", "img/a.png", "sp1[2]")
    suffix: "DSL.SourceSuffix" # parsed suffix (none|fit|ops)
    text_id: str
    H_local: float
    is_doc_id: bool = False
    parsed_transform: Optional["DSL.TransformSpec"] = None
    extra_icons: Optional[List[InlineIconItem]] = None

    # resolved runtime (filled later)
    symbol_id: Optional[str] = None
    intrinsic_wh: Optional[Tuple[float,float]] = None
    hole_fit: Optional["DSL.FitSpec"] = None    # only border/shift used
    icon_fit: Optional["DSL.FitSpec"] = None    # border/shift stripped; used by fit_anchor
    icon_transform: Optional["DSL.TransformSpec"] = None
    hole_pad_trbl: Optional[Tuple[float,float,float,float]] = None  # (t,r,b,l) in doc uu
    hole_wh_doc: Optional[Tuple[float,float]] = None               # (W,H) in doc uu
    hole_wh_base_doc: Optional[Tuple[float,float]] = None          # (W,H) before padding, doc uu
    text_advance_w_doc: Optional[float] = None                     # text flow advance; does not resize icon rect
@dataclass
class ProcessResult:
    icons_placed: int
    used_sources: Set[str]


@dataclass
class DeferredTextGeometry:
    doc_root: SVG.etree._Element
    all_items: List[TokenItem] = None
    spacers: Set[str] = None
    used_sources: Set[str] = None
    tasks: list = None
    measured_ids: Set[str] = None
    decorations: list = None
    needs_inside_cleanup: bool = False
    id_index: Dict[str, SVG.etree._Element] = None

    def __post_init__(self):
        self.all_items = []
        self.spacers = set()
        self.used_sources = set()
        self.tasks = []
        self.measured_ids = set()
        self.decorations = []
        self.id_index = {
            str(node.get("id")): node
            for node in self.doc_root.xpath(".//*[@id]")
            if node.get("id")
        }

    def add(self, all_items, spacers, used_sources, tasks, decorations=None) -> None:
        self.all_items.extend(all_items or [])
        self.spacers.update(spacers or set())
        self.used_sources.update(used_sources or set())
        self.tasks.extend(tasks or [])
        self.decorations.extend(decorations or [])
        for task in tasks or []:
            self.measured_ids.update(task.ids)

    def index_scope(self, scope) -> None:
        for node in scope.iter():
            element_id = str(node.get("id") or "").strip()
            if element_id:
                self.id_index[element_id] = node

    @property
    def has_work(self) -> bool:
        return bool(self.tasks or self.all_items or self.decorations or self.needs_inside_cleanup)


def scope_needs_text_geometry(root_scope: SVG.etree._Element) -> bool:
    if TDEC.scope_has_decorations(root_scope):
        return True
    for node in root_scope.iter():
        if node.get("data-dm-inside-text") == "1" or node.get("data-dm-inside-owner") in ("x", "y", "a"):
            return True
        tag = str(getattr(node, "tag", "") or "")
        if not tag.endswith("text"):
            continue
        visible = "".join(node.itertext())
        if ":" in visible and _find_inline_token(visible, 0):
            return True
    return False

def _prepare_inline_fit(suffix, parsed_transform, src_expr):
    fs_all = DSL.FitSpec()
    tr_all = None
    try:
        if suffix and getattr(suffix, "kind", None) == "fit":
            fs_all = suffix.fit or DSL.FitSpec()
        elif suffix and getattr(suffix, "kind", None) == "ops":
            ops_fit, tr_all = DSL.split_ops_fit_transform(suffix.ops or "")
            fs_all = DSL.fit_spec_from_ops(ops_fit)
    except Exception as ex:
        _l.w(f"[inline_icons] fit suffix parse failed for {src_expr!r}: {ex}")
        fs_all = DSL.FitSpec()
        tr_all = None

    icon_fit = copy.deepcopy(fs_all)
    hole_fit = DSL.FitSpec(border=getattr(fs_all, "border", None), shift=getattr(fs_all, "shift", None))
    icon_fit.border = None
    icon_fit.shift = None
    if icon_fit.mode is None:
        icon_fit.mode = "i"
    if icon_fit.anchor is None:
        icon_fit.anchor = 5
    return hole_fit, icon_fit, TFX.merge_specs([parsed_transform, tr_all])

def _inline_placement_ops(suffix):
    if suffix and getattr(suffix, "kind", None) == "fit":
        return suffix.fit or DSL.FitSpec()
    if suffix and getattr(suffix, "kind", None) == "ops":
        return suffix.ops or ""
    return ""

# ----------------- helpers estilo / fuente -----------------
def _read_effective_fontsize(el: SVG.etree._Element) -> float:
    cur = el
    while cur is not None and isinstance(cur.tag, str):
        sm = SVG.style_map(cur)
        fs = sm.get("font-size")
        if fs:
            s = fs.strip().lower()
            if s.endswith("px"):
                try: return float(s[:-2])
                except Exception: pass
        cur = cur.getparent()
    return 16.0

def _parse_dy_to_px(dy: Optional[str], H: float) -> float:
    if not dy: return 0.0
    s = str(dy).strip().lower()
    try: return float(s)
    except Exception: pass
    if s.endswith("em"):
        try: return float(s[:-2] or "0") * H
        except Exception: return 0.0
    if s.endswith("%"):
        try: return (float(s[:-1] or "0")/100.0) * H
        except Exception: return 0.0
    if s.endswith("px"):
        try: return float(s[:-2] or "0")
        except Exception: return 0.0
    if s.endswith("mm"):
        try: return float(s[:-2] or "0") * PX_PER_MM
        except Exception: return 0.0
    try: return float(s)
    except Exception: return 0.0

# ----------------- matrices / transforms -----------------
def _matrix6_from_transform(t):
    if isinstance(t, Transform):
        if all(hasattr(t, k) for k in ("a","b","c","d","e","f")):
            return float(t.a), float(t.b), float(t.c), float(t.d), float(t.e), float(t.f)
        m = getattr(t, "matrix", None)
        if m is not None and all(hasattr(m, k) for k in ("a","b","c","d","e","f")):
            return float(m.a), float(m.b), float(m.c), float(m.d), float(m.e), float(m.f)
    if isinstance(t, (tuple, list)) and len(t)==6:
        a,b,c,d,e,f = t; return float(a),float(b),float(c),float(d),float(e),float(f)
    try:
        s = str(t)
        m = re.search(r"matrix\(\s*([^\)]+)\)", s)
        if m:
            parts = [float(p) for p in re.split(r"[,\s]+", m.group(1).strip()) if p]
            if len(parts)==6: a,b,c,d,e,f = parts; return a,b,c,d,e,f
    except Exception: pass
    return (1.0,0.0,0.0,1.0,0.0,0.0)

def _scale_from_matrix(a: float,b: float,c: float,d: float) -> Tuple[float,float]:
    sx = math.hypot(a, b); sy = math.hypot(c, d)
    return (sx if sx>EPS else 1.0, sy if sy>EPS else 1.0)

def _apply_inverted_affine(M6, x, y):
    a,b,c,d,e,f = M6
    det = a*d - b*c
    if abs(det) < EPS:
        return x, y
    inv_a =  d / det
    inv_b = -b / det
    inv_c = -c / det
    inv_d =  a / det
    inv_e = -(inv_a*e + inv_c*f)
    inv_f = -(inv_b*e + inv_d*f)
    return float(inv_a*x + inv_c*y + inv_e), float(inv_b*x + inv_d*y + inv_f)


# -------- icon bbox via SVG.visual_bbox / inkex --------
def _icon_bbox_uu(doc_root: SVG.etree._Element, icon_id: str) -> Dict[str, float]:
    node = (doc_root.xpath(f".//*[@id='{icon_id}']") or [None])[0]
    if node is None:
        _l.w("icon id '%s' not found; using 1x1", icon_id)
        return {"x":0.0,"y":0.0,"width":1.0,"height":1.0}
    try:
        if hasattr(SVG, "visual_bbox"):
            x,y,w,h = SVG.visual_bbox(node)  # UU del documento
            w = float(w); h = float(h)
            if w>EPS and h>EPS:
                return {"x":float(x), "y":float(y), "width":w, "height":h}
    except Exception as ex:
        _l.w("visual_bbox falló para '%s': %s", icon_id, ex)
    try:
        if hasattr(node, "bounding_box"):
            bb = node.bounding_box()  # UU del documento
            w = float(getattr(bb, "width", 0.0)); h = float(getattr(bb, "height", 0.0))
            x = float(getattr(bb, "left", 0.0)); y = float(getattr(bb, "top", 0.0))
            if w>EPS and h>EPS:
                return {"x":x, "y":y, "width":w, "height":h}
    except Exception as ex:
        _l.w("inkex bounding_box falló para '%s': %s", icon_id, ex)
    _l.w("no se pudo medir '%s' — uso 1×1", icon_id)
    return {"x":0.0,"y":0.0,"width":1.0,"height":1.0}

# ------------- parsing y utilidades -------------
def _parse_token_attrs(s: Optional[str]) -> Dict[str,str]:
    out: Dict[str,str] = {}
    if not s: return out
    for m in _ATTR_PAIR_RX.finditer(s):
        k = m.group(1).lower()
        v = m.group(2) or m.group(3) or m.group(4) or ""
        out[k] = v
    return out


def _extract_scale(tf_raw: Optional[str]) -> Tuple[float,float,Optional[str]]:
    if not tf_raw: return 1.0, 1.0, None
    m = _SCALE_RX.search(tf_raw)
    if not m: return 1.0, 1.0, tf_raw.strip() if tf_raw.strip() else None
    try:
        sx = float(m.group(1) or 1.0); sy = float(m.group(2) or sx)
    except Exception:
        sx = sy = 1.0
    rest = (tf_raw[:m.start()] + tf_raw[m.end():]).strip() or None
    return sx, sy, rest

# ---------- rich-visible → DOM ----------
def _rich_visible_fragment_nodes(fragment: str, owner_id: Optional[str]) -> Optional[List[SVG.etree._Element]]:
    if "<tspan" not in (fragment or ""):
        return None
    visible_sane = _sanitize_rich_visible(fragment)
    if visible_sane != fragment:
        _l.w("id=%s â€” se detectaron atributos sin comillas en <tspan> (saneados automÃ¡ticamente)", owner_id)
    visible_sane = _escape_text_nodes_only(visible_sane)
    wrapper = f"<svg xmlns='{NS['svg']}' xmlns:pnp='{NS['pnp']}'><text xmlns='{NS['svg']}'>{visible_sane}</text></svg>"
    try:
        doc = SVG.etree.fromstring(wrapper.encode("utf-8"))
    except Exception as ex:
        _l.w("id=%s â€” fallo al parsear rich-text: %s", owner_id, ex)
        return None
    new_text = doc.find("{%s}text" % NS['svg'])
    if new_text is None:
        return None

    nodes: List[SVG.etree._Element] = []
    if new_text.text:
        t0 = SVG.etree.Element("{%s}tspan" % NS['svg'])
        t0.set(CONST.XML_SPACE, "preserve")
        t0.text = new_text.text
        nodes.append(t0)
    for node in list(new_text):
        if isinstance(node.tag, str) and (node.tag.endswith('tspan') or node.tag == "{%s}tspan" % NS['svg']):
            out = copy.deepcopy(node)
            out.set(CONST.XML_SPACE, "preserve")
            nodes.append(out)
        elif node.text:
            out = SVG.etree.Element("{%s}tspan" % NS['svg'])
            out.set(CONST.XML_SPACE, "preserve")
            out.text = node.text
            nodes.append(out)
        if node.tail:
            tail = SVG.etree.Element("{%s}tspan" % NS['svg'])
            tail.set(CONST.XML_SPACE, "preserve")
            tail.text = node.tail
            nodes.append(tail)
    return nodes


def _parse_rich_visible_fragments(text_el: SVG.etree._Element) -> int:
    count = 0
    for node in list(text_el.iter()):
        if node is text_el:
            continue
        owner_id = text_el.get("id")
        if node.text and "<tspan" in node.text:
            nodes = _rich_visible_fragment_nodes(node.text, owner_id)
            if nodes:
                node.text = None
                for idx, child in enumerate(nodes):
                    node.insert(idx, child)
                count += 1
        if node.tail and "<tspan" in node.tail:
            parent = node.getparent()
            nodes = _rich_visible_fragment_nodes(node.tail, owner_id)
            if parent is not None and nodes:
                node.tail = None
                pos = list(parent).index(node) + 1
                for child in nodes:
                    parent.insert(pos, child)
                    pos += 1
                count += 1
    return count


def _maybe_parse_rich_visible_into_dom(text_el: SVG.etree._Element) -> bool:
    """Convierte literal '<tspan ...>' en nodos <tspan> reales dentro del <text>, con saneo y warnings."""
    try:
        # Existing Inkscape text usually already contains tspans; only parse
        # literal rich fragments generated inside their text/tail.
        if text_el.find(".//{%s}tspan" % NS['svg']) is not None:
            changed = _parse_rich_visible_fragments(text_el)
            if changed:
                _l.d("parsed rich-visible fragments id=%s count=%d", text_el.get("id"), changed)
            return bool(changed)

        visible = text_el.text or ""
        if "<tspan" not in visible:
            return False

        # Intento de saneo
        visible_sane = _sanitize_rich_visible(visible)
        if visible_sane != visible:
            _l.w("id=%s — se detectaron atributos sin comillas en <tspan> (saneados automáticamente)", text_el.get("id"))
        
        # Escape text outside tags so any stray '&' does not break XML
        visible_sane = _escape_text_nodes_only(visible_sane)

        wrapper = f"<svg xmlns='{NS['svg']}' xmlns:pnp='{NS['pnp']}'><text xmlns='{NS['svg']}'>{visible_sane}</text></svg>"
        try:
            doc = SVG.etree.fromstring(wrapper.encode("utf-8"))
        except Exception as ex:
            _l.w("id=%s — fallo al parsear rich-text: %s", text_el.get("id"), ex)
            return False

        new_text = doc.find("{%s}text" % NS['svg'])
        if new_text is None:
            _l.w("id=%s — <tspan> detectado pero no pudo convertirse (estructura inválida)", text_el.get("id"))
            return False

        # clear target <text>
        text_el.text = None
        for c in list(text_el): text_el.remove(c)
        text_el.set(CONST.XML_SPACE, "preserve")

        # copy nodes inside
        if new_text.text:
            t0 = SVG.etree.Element("{%s}tspan" % NS['svg'])
            t0.set(CONST.XML_SPACE, "preserve")
            t0.text = new_text.text
            text_el.append(t0)
        for node in list(new_text):
            if isinstance(node.tag, str) and (node.tag.endswith('tspan') or node.tag == "{%s}tspan" % NS['svg']):
                tspan = SVG.etree.Element("{%s}tspan" % NS['svg'])
                for k, v in node.attrib.items():
                    tspan.set(k, v)
                tspan.set(CONST.XML_SPACE, "preserve")
                tspan.text = node.text
                for sub in list(node): tspan.append(sub)
                text_el.append(tspan)
            else:
                if node.text:
                    t = SVG.etree.Element("{%s}tspan" % NS['svg'])
                    t.set(CONST.XML_SPACE, "preserve")
                    t.text = node.text
                    text_el.append(t)
            if node.tail:
                tail_t = SVG.etree.Element("{%s}tspan" % NS['svg'])
                tail_t.set(CONST.XML_SPACE, "preserve")
                tail_t.text = node.tail
                text_el.append(tail_t)

        _l.d("parsed rich-visible → tspans id=%s", text_el.get("id"))
        return True

    except Exception as ex:
        _l.w("id=%s — error inesperado en rich-visible parse: %s", text_el.get("id"), ex)
        return False

def _normalize_rich_visible_for_all_texts(root_scope: SVG.etree._Element) -> int:
    """Aplica rich-visible→DOM a *todos* los <text> del scope, tengan o no :icon:."""
    count = 0
    for t in root_scope.findall(".//svg:text", namespaces={"svg":NS["svg"]}):
        try:
            if _maybe_parse_rich_visible_into_dom(t):
                count += 1
        except Exception as ex:
            _l.w("normalize failed id=%s: %s", t.get("id"), ex)
    if count:
        _l.d("normalized rich-visible→DOM en %d <text>(s)", count)
    return count

# ---------- in-place: insert spacers without rebuilding ----------
def _insert_spacer_sibling(parent: SVG.etree._Element, ref_node: SVG.etree._Element, spacer_id: str, spacer_glyph: str):
    tspan = SVG.etree.Element(f"{{{NS['svg']}}}tspan")
    tspan.set(CONST.XML_SPACE, "preserve")
    tspan.set("id", spacer_id)
    tspan.text = spacer_glyph
    sm = SVG.style_map(tspan)
    sm["fill-opacity"] = "0"
    sm["stroke-opacity"] = "0"
    SVG.style_set(tspan, sm)
    children = list(parent)
    idx = children.index(ref_node)
    parent.insert(idx + 1, tspan)
    return tspan

def _process_text_fragment(
    text_el: SVG.etree._Element,
    node: SVG.etree._Element,
    attr_name: str,
    seq_next: int,
    spacer_glyph: str,
    out_items: List[TokenItem],
    source_exists: Optional[Callable[[str], bool]] = None,
) -> int:
    s = getattr(node, attr_name)
    if not s or (":" not in s):
        return seq_next

    acc = ""
    pos = 0
    while True:
        hit = _find_inline_token(s, pos)
        if not hit:
            acc += s[pos:]
            break

        t0, t1, inner = hit

        # Literal text before the token.
        acc += s[pos:t0]

        # try parsing :@{...}: / :S{...}: / :Source{...}:  or :id:
        extra_icons = None
        if inner.startswith("@{") or inner.lower().startswith("s{") or inner.lower().startswith("source{"):
            try:
                src_uri, suffix = _parse_source_inner_token(inner)
                parsed_transform = None
            except Exception as ex:
                # Malformed token: keep the literal text for better UX.
                acc += s[t0:t1]
                _l.w(f"[inline_icons] invalid token kept as literal: {s[t0:t1]!r}  ({ex})")
                pos = t1
                continue

            if not src_uri:
                acc += s[t0:t1]
                _l.w(f"[inline_icons] token sin src (se deja literal): {s[t0:t1]!r}")
                pos = t1
                continue
            is_doc_id = False
        else:
            try:
                parsed_local = _parse_inline_local_source_token(inner)
            except Exception:
                parsed_local = None
            if parsed_local:
                src_uri, suffix = parsed_local
                parsed_transform = None
                if callable(source_exists) and (not source_exists(src_uri)):
                    acc += s[t0:t1]
                    pos = t1
                    continue
                is_doc_id = False
            else:
                parsed_ids = _parse_inline_multi_id_token(inner)
                if parsed_ids is None:
                    acc += s[t0:t1]
                    _l.w(f"[inline_icons] invalid token kept as literal: {s[t0:t1]!r}")
                    pos = t1
                    continue
                if callable(source_exists) and any(not source_exists(icon_id) for icon_id, _suffix, _transform in parsed_ids):
                    acc += s[t0:t1]
                    pos = t1
                    continue
                src_uri, suffix, parsed_transform = parsed_ids[0]
                if len(parsed_ids) > 1:
                    extra_icons = [
                        InlineIconItem(icon_id, icon_suffix, icon_transform)
                        for icon_id, icon_suffix, icon_transform in parsed_ids[1:]
                    ]
                is_doc_id = True

        # volcar acc al atributo actual y “cerrar”
        setattr(node, attr_name, acc)
        acc = ""

        # crear spacer tras 'node'
        seq_next += 1
        spacer_id = f"{text_el.get('id') or 'text'}__hole__{seq_next}"

        parent = node.getparent() if attr_name == "tail" else (node.getparent() if node.tag.endswith("tspan") else node)
        if parent is None:
            parent = text_el

        if attr_name == "text" and (not node.tag.endswith("tspan")):
            # case: <text>.text -> wrap in a prior tspan
            t_before = SVG.etree.Element(f"{{{NS['svg']}}}tspan")
            t_before.set(CONST.XML_SPACE, "preserve")
            t_before.text = getattr(node, attr_name) or ""
            node.text = ""
            node.insert(0, t_before)
            # insert spacer after that tspan
            tspan_ref = t_before
        else:
            tspan_ref = node

        tspan_sp = _insert_spacer_sibling(parent, tspan_ref, spacer_id, spacer_glyph)

        out_items.append(TokenItem(
            spacer_id=spacer_id,
            src_expr=inner,
            src_uri=src_uri,
            suffix=suffix,
            text_id=text_el.get('id') or '',
            H_local=_read_effective_fontsize(node),
            is_doc_id=is_doc_id,
            parsed_transform=parsed_transform,
            extra_icons=extra_icons,
        ))

        # continue after token: move remainder to a new tspan to preserve order
        remainder = s[t1:]
        if remainder:
            t_after = SVG.etree.Element(f"{{{NS['svg']}}}tspan")
            t_after.set(CONST.XML_SPACE, "preserve")
            t_after.text = remainder
            parent.insert(list(parent).index(tspan_sp) + 1, t_after)
            node = t_after
            attr_name = "text"
            s = remainder
            pos = 0
            acc = ""
            continue
        pos = t1

        # If the token was in '.text', the rest of the string is already consumed,
        # because we split the attribute at the exact point.
        # Keep scanning the original string for later tokens.
        # Note: setattr(node, attr_name, acc) already set the previous text without the token.

    # Remaining literal text after the last processed token.
    # Important: if `acc` is empty, do not overwrite already-written node text
    # (that would drop text like " and 1" between inline icons).
    if acc:
        setattr(node, attr_name, acc)
    return seq_next
def _inject_spacers_in_place(
    text_el: SVG.etree._Element,
    spacer_glyph: str,
    source_exists: Optional[Callable[[str], bool]] = None,
) -> List[TokenItem]:
    seq = 0
    items: List[TokenItem] = []

    # Process the <text>.text itself
    seq = _process_text_fragment(text_el, text_el, "text", seq, spacer_glyph, items, source_exists=source_exists)

    # Recorrer descendientes: sus .text y .tail
    for n in list(text_el.iterdescendants()):
        seq = _process_text_fragment(text_el, n, "text", seq, spacer_glyph, items, source_exists=source_exists)
        seq = _process_text_fragment(text_el, n, "tail", seq, spacer_glyph, items, source_exists=source_exists)

    _l.t("inplace id=%s spacers=%d", text_el.get("id"), seq)
    return items

def _escape_text_nodes_only(s: str) -> str:
    """Escapa solo el texto fuera de etiquetas XML (<tspan>, etc.), para que sea XML válido.

    - De momento solo escapa '&' que no forma parte de una entidad (&amp;, &#123;, etc.).
    - Las etiquetas generadas por snippets se mantienen intactas.
    """
    out = []
    inside_tag = False
    i = 0
    n = len(s)

    while i < n:
        ch = s[i]
        if ch == "<":
            inside_tag = True
            out.append(ch)
            i += 1
            continue
        if ch == ">":
            inside_tag = False
            out.append(ch)
            i += 1
            continue

        if inside_tag:
            out.append(ch)
            i += 1
            continue

        # Fuera de etiqueta: vigilar '&' sueltos
        if ch == "&":
            j = i + 1
            # Scan until ';' or a separator.
            while j < n and s[j] not in " \t\r\n<>":
                if s[j] == ";":
                    break
                j += 1

            if j < n and s[j] == ";" and j > i + 1:
                body = s[i+1:j]
                # Entidades: &amp;  &nombre;  &#123;  &#x1F60A;
                if body[0].isalpha() or (body[0] == "#" and len(body) > 1):
                    out.append(s[i:j+1])
                    i = j + 1
                    continue

            # No parece una entidad → escapar
            out.append("&amp;")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)

_URL_REF_RE = re.compile(r"url\(\s*#([^)]+)\s*\)")


def _probe_refs_in_subtree(node) -> Set[str]:
    refs: Set[str] = set()
    for n in node.iter():
        for _, value in (n.attrib or {}).items():
            sv = str(value or "")
            refs.update(r.strip() for r in _URL_REF_RE.findall(sv) if r.strip())
            if sv.startswith("#") and len(sv) > 1:
                refs.add(sv[1:].strip())
    return refs


def _append_inline_probe_terminal_sentinels(root: SVG.etree._Element) -> None:
    # Inkscape shell sometimes fails to report bbox for a terminal inline-hole tspan
    # when it is the last text run. Add a probe-only invisible sentinel after it so
    # the copied text subtree keeps a trailing run during measurement.
    try:
        for n in list(root.iter()):
            if not isinstance(getattr(n, "tag", None), str) or not n.tag.endswith("tspan"):
                continue
            nid = str(n.get("id") or "")
            if "__hole__" not in nid:
                continue
            parent = n.getparent()
            if parent is None:
                continue
            siblings = list(parent)
            idx = siblings.index(n)
            if idx != (len(siblings) - 1):
                continue
            sentinel = SVG.etree.Element(f"{{{NS['svg']}}}tspan")
            sentinel.set(CONST.XML_SPACE, "preserve")
            sm = SVG.style_map(sentinel)
            sm["fill-opacity"] = "0"
            sm["stroke-opacity"] = "0"
            sm["font-size"] = "0.001px"
            SVG.style_set(sentinel, sm)
            sentinel.text = "."
            parent.insert(idx + 1, sentinel)
    except Exception:
        pass


def _populate_inline_probe_defs(
    root: SVG.etree._Element,
    defs: SVG.etree._Element,
    id_index: Dict[str, SVG.etree._Element],
) -> None:
    added: Set[str] = {
        str(node.get("id"))
        for node in root.xpath(".//*[@id]")
        if node.get("id")
    }
    pending = list(_probe_refs_in_subtree(root))
    while pending:
        rid = pending.pop()
        if not rid or rid in added:
            continue
        src = id_index.get(rid)
        if src is None:
            continue
        clone = copy.deepcopy(src)
        defs.append(clone)
        added.add(rid)
        for subref in _probe_refs_in_subtree(clone):
            if subref not in added:
                pending.append(subref)


def _build_probe_tree(
    doc_root: SVG.etree._Element,
    text_els: List[SVG.etree._Element],
    id_index: Dict[str, SVG.etree._Element],
):
    """Build one compact SVG with text probes in translation-free coordinates."""
    nsmap = getattr(doc_root, "nsmap", None) or None
    root = SVG.etree.Element(doc_root.tag, nsmap=nsmap)
    for k, v in (doc_root.attrib or {}).items():
        root.set(k, v)

    defs = SVG.etree.SubElement(root, f"{{{NS['svg']}}}defs")

    try:
        for nv in doc_root.xpath("./sodipodi:namedview", namespaces=SVG.NSS):
            root.append(copy.deepcopy(nv))
    except Exception:
        pass
    try:
        for st in doc_root.xpath(".//svg:style", namespaces=SVG.NSS):
            defs.append(copy.deepcopy(st))
    except Exception:
        pass

    probe_roots: List[SVG.etree._Element] = []
    seen_probe_roots: Set[SVG.etree._Element] = set()
    for text_el in text_els:
        if text_el is None:
            continue
        probe_root = text_el
        cur = text_el.getparent()
        while cur is not None and cur is not doc_root:
            if cur.get("data-dm-shape-inside-owner") == "1":
                probe_root = cur
                break
            cur = cur.getparent()
        if probe_root in seen_probe_roots:
            continue
        seen_probe_roots.add(probe_root)
        probe_roots.append(probe_root)

    offsets: Dict[str, Tuple[float, float]] = {}
    for probe_root in probe_roots:
        chain = []
        cur = probe_root
        while cur is not None and cur is not doc_root:
            chain.append(cur)
            cur = cur.getparent()
        chain.reverse()
        if not chain:
            continue
        parent = probe_root.getparent()
        try:
            a, b, c, d, e, f = _matrix6_from_transform(SVG.composed_transform(parent))
        except Exception:
            a, b, c, d, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
        wrapper = SVG.etree.SubElement(root, f"{{{NS['svg']}}}g")
        wrapper.set("transform", f"matrix({a},{b},{c},{d},0,0)")
        probe_parent = wrapper
        for ancestor in chain[:-1]:
            shallow = SVG.etree.SubElement(probe_parent, ancestor.tag)
            for key, value in (ancestor.attrib or {}).items():
                if key not in ("id", "transform"):
                    shallow.set(key, value)
            probe_parent = shallow
        cloned_root = copy.deepcopy(probe_root)
        probe_parent.append(cloned_root)
        for node in cloned_root.iter():
            element_id = str(node.get("id") or "").strip()
            if element_id:
                offsets[element_id] = (float(e), float(f))

    _append_inline_probe_terminal_sentinels(root)
    _populate_inline_probe_defs(root, defs, id_index)
    return SVG.etree.ElementTree(root), offsets


def _build_text_probe(tree, ids: Set[str], id_index=None):
    doc_root = tree.getroot()
    if id_index is None:
        id_index = {str(node.get("id")): node for node in doc_root.xpath(".//*[@id]") if node.get("id")}
    text_els = []
    seen = set()
    for element_id in ids or set():
        node = id_index.get(str(element_id or ""))
        current = node
        while current is not None:
            tag = str(getattr(current, "tag", "") or "")
            if tag.endswith("text") or tag.endswith("flowRoot"):
                if current not in seen:
                    seen.add(current)
                    text_els.append(current)
                break
            current = current.getparent() if hasattr(current, "getparent") else None
    probe_tree, offsets = _build_probe_tree(doc_root, text_els, id_index)
    return probe_tree, len(text_els), {element_id: offsets[element_id] for element_id in ids if element_id in offsets}


# ----------------- main -----------------
def process_text_geometry(root_scope: SVG.etree._Element, show_debug_rects: bool=False, spacer_glyph: Optional[str]=None, *, source_manager: Optional[SRC.SourceManager]=None, doc_path: Optional[str]=None, query_service=None, defer_apply: bool=False, prepared_geometry: Optional[DeferredTextGeometry]=None) -> ProcessResult:
    spacer_glyph = spacer_glyph or DEFAULT_SPACER_GLYPH

    doc_root = root_scope.getroottree().getroot()
    tree = root_scope.getroottree()
    SVG.ensure_xlink_ns(doc_root)
    if defer_apply:
        inside_ids = set()
        has_inside_owner = False
        for node in root_scope.iter():
            if node.get("data-dm-inside-owner") in ("x", "y", "a"):
                has_inside_owner = True
            if node.get("data-dm-inside-text") == "1" and "".join(node.itertext()).strip():
                inside_ids.add(SVG.ensure_id(doc_root, node, "dm_inside_text"))
        if prepared_geometry is not None and has_inside_owner:
            prepared_geometry.needs_inside_cleanup = True
    else:
        empty_inside_changed = TFX.discard_empty_inside(doc_root)
        if empty_inside_changed:
            _l.i("[text_measure] transform_inside empty_removed=%d", empty_inside_changed)
        inside_ids = TFX.pending_inside_text_ids(doc_root)

    _l.d("scope=%s — in-place pipeline (spacer glyph=%r)", root_scope.get('id'), spacer_glyph)

    # Normalize rich-visible content to DOM for every <text>.
    normalized = _normalize_rich_visible_for_all_texts(root_scope)
    if normalized:
        _l.d("normalized=%d texts (rich-visible→DOM)", normalized)

    decorations = TDEC.collect(
        root_scope,
        doc_root,
        mark_prepared=defer_apply,
    )

    # Process only <text> nodes containing inline source tokens.
    texts_with_icons: List[SVG.etree._Element] = []
    for t in root_scope.findall(".//svg:text", namespaces={"svg":NS["svg"]}):
        try:
            vis = t.xpath("string(.)") or ""
        except Exception:
            vis = ""
        if (":" in vis) and _find_inline_token(vis, 0):
            texts_with_icons.append(t)

    has_prepared = bool(
        not defer_apply
        and prepared_geometry is not None
        and prepared_geometry.has_work
    )
    if not texts_with_icons and not inside_ids and not decorations and not has_prepared:
        _l.d("No <text> with :@{...}: tokens found.")
        return ProcessResult(0, set())
    if query_service is None:
        raise RuntimeError("Text geometry requires TextQueryService")

    if texts_with_icons and source_manager is None:
        source_manager = SRC.SourceManager(doc_root, doc_path, project_root=None)
    pref_debug_rects = prefs.get_inline_icons_show_debug_rects(False)
    show_debug_rects = bool(show_debug_rects or pref_debug_rects)
    extra_ratio = prefs.get_inline_icons_extra_ratio(EXTRA_RATIO)

    def _inline_local_source_exists(src_uri: str) -> bool:
        s = (src_uri or "").strip()
        if not s:
            return False
        if _INLINE_ID_RX.fullmatch(s or ""):
            try:
                return bool(doc_root.xpath(f".//*[@id='{s}']"))
            except Exception:
                return False
        if re.match(r"^(?:https?://|data:|icon://|wkmc://|pxby://|oclp://|pnp://)", s, re.IGNORECASE):
            return True
        s2 = os.path.expanduser(os.path.expandvars(s))
        p = Path(s2)
        try:
            if p.is_absolute():
                return p.is_file()
        except Exception:
            pass
        try:
            if doc_path:
                base_hit = Path(doc_path).resolve().parent / p
                if base_hit.is_file():
                    return True
        except Exception:
            pass
        try:
            return source_manager.resolver.resolve_logical(s.replace("\\", "/")) is not None
        except Exception:
            return False

    # ensure unique id
    if prepared_geometry is not None:
        prepared_geometry.index_scope(root_scope)
        used_ids: Set[str] = set(prepared_geometry.id_index)
    else:
        used_ids = set(x.get("id") for x in doc_root.xpath(".//*[@id]"))
    for t in texts_with_icons:
        if not t.get("id"):
            base = "text"
            i = 1
            cand = base
            while cand in used_ids:
                i += 1; cand = f"{base}_{i}"
            t.set("id", cand)
        used_ids.add(t.get("id"))

    # PASO A: insertar espaciadores in-place y calcular huecos
    used_sources: Set[str] = set()
    all_items: List[TokenItem] = []
    spacers: Set[str] = set()

    for t in texts_with_icons:
        items = _inject_spacers_in_place(t, spacer_glyph, source_exists=_inline_local_source_exists)
        for it in items:
            if it.src_uri:
                used_sources.add(it.src_uri)
            for icon in (it.extra_icons or []):
                if icon.src_uri:
                    used_sources.add(icon.src_uri)
            spacers.add(it.spacer_id)
        all_items.extend(items)

    if not all_items:
        _l.d("[inline_icons] stage=parse_tokens texts=%d tokens=0", len(texts_with_icons))
        if not inside_ids and not decorations and not has_prepared:
            _l.d("Found texts but zero tokens after insert.")
            return ProcessResult(0, set())
    _l.d("[inline_icons] stage=parse_tokens texts=%d tokens=%d", len(texts_with_icons), len(all_items))
    _l.d("[inline_icons] stage=holes holes=%d", len(spacers))

    # compute sizes and APPLY HOLES (letter-spacing in the spacer)
    # Resolve sources with the same behavior as @{...}: placeholders and logs in sources.py

    opened = 0
    placeholder_count = 0
    query_tasks = []
    ids_by_text: Dict[str, Set[str]] = {}
    remaining_by_text: Dict[str, int] = {}
    if prepared_geometry is not None:
        prepared_geometry.index_scope(root_scope)
        id_index = prepared_geometry.id_index
    else:
        id_index = {
            str(node.get("id")): node
            for node in doc_root.xpath(".//*[@id]")
            if node.get("id")
        }
    for item in all_items:
        ids_by_text.setdefault(item.text_id, set()).add(item.spacer_id)
        remaining_by_text[item.text_id] = remaining_by_text.get(item.text_id, 0) + 1
    previously_measured = prepared_geometry.measured_ids if prepared_geometry is not None else set()
    for text_id in inside_ids:
        if text_id not in previously_measured:
            ids_by_text.setdefault(text_id, set()).add(text_id)
    for decoration in decorations:
        if decoration.tspan_id not in previously_measured:
            ids_by_text.setdefault(decoration.text_id, set()).add(decoration.tspan_id)

    def _submit_text_probe(text_id: str) -> None:
        ids = ids_by_text.pop(text_id, set())
        if not ids:
            return
        probe_tree, _probe_texts, probe_offsets = _build_text_probe(tree, ids, id_index)
        query_tasks.append(query_service.submit(probe_tree, ids, probe_offsets))

    for it in all_items:
        shared_hole = bool(it.extra_icons)
        if shared_hole:
            it.hole_fit = DSL.FitSpec()
            it.icon_fit = None
            it.icon_transform = getattr(it, "parsed_transform", None)
        else:
            it.hole_fit, it.icon_fit, it.icon_transform = _prepare_inline_fit(
                it.suffix, getattr(it, "parsed_transform", None), it.src_expr,
            )
        hole_fs = it.hole_fit

        # 3) resolver source → symbol_id + intrinsic size
        if it.is_doc_id:
            it.symbol_id = it.src_uri
            bb = _icon_bbox_uu(doc_root, it.symbol_id)
            it.intrinsic_wh = (float(bb.get("width", 1.0)), float(bb.get("height", 1.0)))
        else:
            try:
                ref = source_manager.register(it.src_uri)
            except Exception as ex:
                _l.w(f"[inline_icons] source_manager.register failed for {it.src_uri!r}: {ex}")
                ref = source_manager.register("")  # placeholder seguro
            it.symbol_id = ref.symbol_id
            if ref.symbol_id and str(ref.symbol_id).startswith("src_missing_"):
                placeholder_count += 1
            it.intrinsic_wh = tuple(ref.intrinsic_box or (DEFAULT_H, DEFAULT_H))

        for icon in (it.extra_icons or []):
            icon.symbol_id = icon.src_uri
            bb = _icon_bbox_uu(doc_root, icon.symbol_id)
            icon.intrinsic_wh = (float(bb.get("width", 1.0)), float(bb.get("height", 1.0)))

        intrinsic_sizes = [it.intrinsic_wh] + [icon.intrinsic_wh for icon in (it.extra_icons or [])]
        ratios = []
        for iw, ih in intrinsic_sizes:
            iw = max(1e-6, float(iw)); ih = max(1e-6, float(ih))
            ratios.append(iw / ih)
        ratio = max(ratios)

        # item's <text>
        t_el = (doc_root.xpath(f".//*[@id='{it.text_id}']") or [None])[0]
        if t_el is None:
            _l.w("text element missing for id=%s", it.text_id); continue

        aT,bT,cT,dT,eT,fT = _matrix6_from_transform(SVG.composed_transform(t_el))
        Sx_text, Sy_text = _scale_from_matrix(aT,bT,cT,dT)

        # Sizes in DOC (uu)
        H_doc = it.H_local * Sy_text
        W_icon_d = H_doc * ratio

        # Hole base (before padding expansion)
        hole_base_w = W_icon_d
        hole_base_h = H_doc
        hole_w, hole_h = max(EPS, hole_base_w), max(EPS, hole_base_h)

        # Hole border/outset (uses standard border parser)
        pad_t = pad_r = pad_b = pad_l = 0.0
        if hole_fs and getattr(hole_fs, "border", None):
            try:
                pad_t, pad_r, pad_b, pad_l, _, _ = SVG.border_tokens_to_pad_px(doc_root, float(hole_w), float(hole_h), hole_fs.border)
                # Interpretation for inline_icons: border INCREASES the hole (outset)
                hole_w = max(EPS, hole_w + pad_l + pad_r)
                hole_h = max(EPS, hole_h + pad_t + pad_b)
            except Exception as ex:
                _l.w(f"[inline_icons] hole border parse failed for {it.src_expr!r}: {ex}")
                pad_t = pad_r = pad_b = pad_l = 0.0

        it.hole_pad_trbl = (float(pad_t), float(pad_r), float(pad_b), float(pad_l))
        it.hole_wh_doc = (float(hole_w), float(hole_h))
        it.hole_wh_base_doc = (float(hole_base_w), float(hole_base_h))
        it.text_advance_w_doc = max(EPS, float(hole_w) + extra_ratio * H_doc)

        # Text advance is independent from the Fit/Anchor rect used to size the icon.
        W_hole_loc = it.text_advance_w_doc / max(EPS, Sx_text)

        spacer = (doc_root.xpath(f".//*[@id='{it.spacer_id}']") or [])
        if spacer:
            smap = SVG.style_map(spacer[0])
            smap["letter-spacing"] = f"{W_hole_loc:.4f}px"
            SVG.style_set(spacer[0], smap)
            opened += 1
        else:
            _l.w("spacer not found for id=%s", it.spacer_id)

        remaining = remaining_by_text.get(it.text_id, 0) - 1
        remaining_by_text[it.text_id] = remaining
        if remaining <= 0 and not defer_apply:
            _submit_text_probe(it.text_id)

    if defer_apply:
        scope_ids = set()
        for values in ids_by_text.values():
            scope_ids.update(values)
        if scope_ids:
            probe_tree, _probe_texts, probe_offsets = _build_text_probe(
                tree, scope_ids, id_index
            )
            query_tasks.append(query_service.submit(probe_tree, scope_ids, probe_offsets))
            ids_by_text.clear()
    else:
        for text_id in list(ids_by_text):
            _submit_text_probe(text_id)

    if defer_apply:
        if prepared_geometry is None:
            raise RuntimeError("defer_apply requires prepared_geometry")
        prepared_geometry.add(
            all_items,
            spacers,
            used_sources,
            query_tasks,
            decorations,
        )
        return ProcessResult(0, used_sources)

    if prepared_geometry is not None:
        all_items = list(prepared_geometry.all_items) + all_items
        spacers.update(prepared_geometry.spacers)
        used_sources.update(prepared_geometry.used_sources)
        query_tasks = list(prepared_geometry.tasks) + query_tasks
        decorations = list(prepared_geometry.decorations) + decorations

    cache_hits = getattr(source_manager, "_cache_hits", None)
    cache_misses = getattr(source_manager, "_cache_misses", None)
    if cache_hits is not None and cache_misses is not None:
        _l.i("[inline_icons] stage=cache placeholders=%d cache_hits=%d cache_misses=%d", placeholder_count, cache_hits, cache_misses)
    else:
        _l.i("[inline_icons] stage=cache placeholders=%d cache_hits=? cache_misses=?", placeholder_count)
    _l.i("huecos aplicados=%d", opened)

# PASO B: collect geometry measured by the persistent Inkscape shell.
    batch = TM.TextBBoxBatch(doc_root)
    batch.register("inline_icons", spacers)
    batch.register("transform_inside", inside_ids)
    batch.register("text_decorations", [item.tspan_id for item in decorations])
    ids_text = sorted(batch.ids)
    _l.i("[text_measure] stage=collect ids=%d consumers=%s", len(ids_text), ",".join(batch.consumers))
    raw_bboxes, probe_offsets = query_service.collect(query_tasks)
    batch.set_probe_bboxes(raw_bboxes, probe_offsets)
    if batch.missing_ids:
        _l.w(
            "[text_measure] missing=%d ids=%s",
            len(batch.missing_ids),
            sorted(batch.missing_ids),
        )
    bbs_doc = batch.bboxes_for("inline_icons")
    _l.i("[text_measure] stage=bboxes inline_icons=%d transform_inside=%d", len(bbs_doc), len(batch.bboxes_for("transform_inside")))

    # Icon placement (FitAnchor over a hole-rect in a <g> rotated like the text)
    placed = 0
    placement_sequence = FA.PlacementSequence(doc_root)
    hole_bboxes_by_text = {}
    for it in all_items:
        if not it.symbol_id or not it.hole_wh_doc:
            continue

        t_el = (doc_root.xpath(f".//*[@id='{it.text_id}']") or [None])[0]
        if t_el is None:
            continue

        # Matrices / scales of <text>
        M_text = _matrix6_from_transform(SVG.composed_transform(t_el))
        aT,bT,cT,dT,eT,fT = M_text
        Sx_text, Sy_text = _scale_from_matrix(aT,bT,cT,dT)

        # Hole dimensions (DOC uu)
        hole_w, hole_h = it.hole_wh_doc
        hole_base_w, hole_base_h = it.hole_wh_base_doc or (hole_w, hole_h)
        H_doc = it.H_local * Sy_text

        # Spacer bbox (DOC)
        bb_s_doc = bbs_doc.get(it.spacer_id)
        if not bb_s_doc:
            _l.w("no bbox for spacer=%s", it.spacer_id); continue

        xI_left = bb_s_doc["x"]
        wI      = bb_s_doc["width"]
        yI_top  = bb_s_doc["y"]
        hI      = bb_s_doc["height"]

        hI_eff = float(hI)

        # --- geometry in <text> axes (robust against rotations) ---
        mag_u = math.hypot(aT, bT) or 1.0
        mag_v = math.hypot(cT, dT) or 1.0
        ux, uy = aT / mag_u, bT / mag_u         # +X local → DOC
        vx, vy = cT / mag_v, dT / mag_v         # +Y local → DOC

        # Center of spacer bbox (DOC)
        cx = xI_left + wI * 0.5
        cy = yI_top  + hI_eff * 0.5

        # Center of hole [I + hole] along the text flow
        # (use base size to keep center stable when padding expands the rect)
        x_center_doc = cx + ux * (hole_base_w * 0.5)
        y_center_doc = cy + uy * (hole_base_w * 0.5)

        # Original placement model: baseline derived from the nominal text height.
        baseline_x = x_center_doc + vx * (H_doc * 0.5)
        baseline_y = y_center_doc + vy * (H_doc * 0.5)

        # Hole top-left in DOC (before shift)
        x_left_doc = baseline_x - vx * (OVERSHOOT * H_doc) - ux * (hole_base_w * 0.5)
        y_top_doc  = baseline_y - vy * (OVERSHOOT * H_doc) - uy * (hole_base_w * 0.5)

        # Center the visual icon rect inside the text advance. Negative advances
        # intentionally make the icon overlap surrounding text symmetrically.
        advance_w = it.text_advance_w_doc if it.text_advance_w_doc is not None else hole_w
        dx_advance = (float(advance_w) - float(hole_w)) * 0.5
        if abs(dx_advance) > EPS:
            x_left_doc += ux * dx_advance
            y_top_doc  += uy * dx_advance

        # Re-center if the hole was expanded (symmetric padding)
        dy_center = max(0.0, (hole_h - hole_base_h) * 0.5)
        if dy_center:
            x_left_doc -= vx * dy_center
            y_top_doc  -= vy * dy_center

        # Hole shift (t=[dx dy]) in text axes
        dx = dy = 0.0
        if it.hole_fit and getattr(it.hole_fit, "shift", None):
            sh = it.hole_fit.shift or []
            if len(sh) >= 2:
                def _shift_to_uu(v, base):
                    if v is None:
                        return 0.0
                    if isinstance(v, (int, float)):
                        return float(v)
                    s = str(v).strip()
                    if not s:
                        return 0.0
                    if s.endswith('%'):
                        try:
                            return (float(s[:-1]) / 100.0) * float(base)
                        except Exception:
                            return 0.0
                    try:
                        return float(doc_root.unittouu(s))
                    except Exception:
                        try:
                            return float(s)
                        except Exception:
                            return 0.0
                dx = _shift_to_uu(sh[0], hole_w)
                dy = _shift_to_uu(sh[1], hole_h)

        x_left_doc += ux * dx + vx * dy
        y_top_doc  += uy * dx + vy * dy

        hole_points = (
            (x_left_doc, y_top_doc),
            (x_left_doc + ux * hole_w, y_top_doc + uy * hole_w),
            (x_left_doc + vx * hole_h, y_top_doc + vy * hole_h),
            (x_left_doc + ux * hole_w + vx * hole_h, y_top_doc + uy * hole_w + vy * hole_h),
        )
        hole_xs = [point[0] for point in hole_points]
        hole_ys = [point[1] for point in hole_points]
        TM.extend_bbox_map(
            hole_bboxes_by_text,
            it.text_id,
            {
                "x": min(hole_xs),
                "y": min(hole_ys),
                "width": max(hole_xs) - min(hole_xs),
                "height": max(hole_ys) - min(hole_ys),
            },
        )

        # DOC → LOCAL(parent_g)
        parent_g = t_el.getparent() if t_el.getparent() is not None else doc_root
        Mg = _matrix6_from_transform(SVG.composed_transform(parent_g))
        aG,bG,cG,dG,eG,fG = Mg

        x_loc, y_loc = _apply_inverted_affine(Mg, x_left_doc, y_top_doc)
        Sx_g, Sy_g = _scale_from_matrix(aG,bG,cG,dG)
        if abs(Sx_g) < EPS: Sx_g = 1.0
        if abs(Sy_g) < EPS: Sy_g = 1.0

        W_loc = hole_w / Sx_g
        H_loc = hole_h / Sy_g

        # Relative rotation (text vs parent)
        theta_parent = math.degrees(math.atan2(bG, aG))
        theta_text   = math.degrees(math.atan2(bT, aT))
        theta_rel    = theta_text - theta_parent

        # Group oriented like the text
        g = SVG.etree.SubElement(parent_g, f"{{{NS['svg']}}}g")
        if INHERIT_TEXT_ROTATION:
            g.set("transform", f"translate({x_loc:.6f},{y_loc:.6f}) rotate({theta_rel:.9f})")
        else:
            g.set("transform", f"translate({x_loc:.6f},{y_loc:.6f})")

        # Hole rect in group coords
        rect = SVG.etree.SubElement(g, f"{{{NS['svg']}}}rect")
        rect.set("x", "0"); rect.set("y", "0")
        rect.set("width", f"{W_loc:.6f}")
        rect.set("height", f"{H_loc:.6f}")
        rect.set("style", "fill:none;stroke:none")

        if it.extra_icons:
            icons_to_place = [
                (it.src_uri, it.symbol_id, _inline_placement_ops(it.suffix), it.parsed_transform, it.is_doc_id),
                *[
                    (icon.src_uri, icon.symbol_id, _inline_placement_ops(icon.suffix), icon.parsed_transform, True)
                    for icon in it.extra_icons
                ],
            ]
        else:
            icons_to_place = [(it.src_uri, it.symbol_id, it.icon_fit, it.icon_transform, it.is_doc_id)]
        for icon_expr, symbol_id, icon_fit, icon_transform, is_doc_id in icons_to_place:
            if not symbol_id:
                continue
            try:
                placement_sequence.apply(
                    doc_root, symbol_id, "", icon_fit,
                    rect_elem=rect, parent_elem=g,
                    transform_spec=icon_transform,
                    ignore_source_ancestors=bool(is_doc_id),
                )
            except Exception as ex:
                _l.w(f"[inline_icons] fit_anchor failed for {icon_expr!r}: {ex}")

        placed += 1

        if not show_debug_rects:
            try:
                g.remove(rect)
            except Exception:
                pass

        if show_debug_rects:
            rect.set("style", "fill:none;stroke:#00bcd4;stroke-width:0.18")

    inside_bboxes = batch.bboxes_for("transform_inside")
    for text_id, hole_bbox in hole_bboxes_by_text.items():
        if text_id in inside_bboxes:
            TM.extend_bbox_map(inside_bboxes, text_id, hole_bbox)
    inside_changed = TFX.apply_deferred_inside(doc_root, inside_bboxes)
    decorations_changed = TDEC.apply(
        doc_root,
        decorations,
        batch.bboxes_for("text_decorations"),
    )
    _l.i("[text_measure] transform_inside changed=%d", inside_changed)
    _l.i("[text_decoration] paths=%d", decorations_changed)
    _l.i("inline_icons placed=%d", placed)
    _l.i("[inline_icons] stage=insert_use use_count=%d", placed)
    return ProcessResult(placed, used_sources)


def inline_place_icons(root_scope: SVG.etree._Element, show_debug_rects: bool=False, spacer_glyph: Optional[str]=None, *, source_manager: Optional[SRC.SourceManager]=None, doc_path: Optional[str]=None) -> ProcessResult:
    query_service = TM.TextQueryService()
    try:
        return process_text_geometry(
            root_scope,
            show_debug_rects=show_debug_rects,
            spacer_glyph=spacer_glyph,
            source_manager=source_manager,
            doc_path=doc_path,
            query_service=query_service,
        )
    finally:
        query_service.close()

# ------------- CLI / effect -------------
class TextEffect(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab", default="run")
        pars.add_argument("--debug", type=inkex.Boolean, default=False)
        pars.add_argument("--console_level", type=str, default="global")
        pars.add_argument("--file_level", type=str, default="global")
        pars.add_argument("--spacer_glyph", type=str, default=DEFAULT_SPACER_GLYPH)
    def effect(self):
        _l.get_logger(self, console_level=self.options.console_level, file_level=self.options.file_level, tag_override="text")
        _l.i("LOADED %s — %s", __file__, __version__)
        root = self.document.getroot()
        res = inline_place_icons(
            root,
            show_debug_rects=bool(self.options.debug),
            spacer_glyph=(self.options.spacer_glyph or DEFAULT_SPACER_GLYPH)
        )
        _l.i("placed=%d, icons=%s", res.icons_placed, sorted(res.used_sources))

InlineIconsEffect = TextEffect

if __name__ == "__main__":
    TextEffect().run()
