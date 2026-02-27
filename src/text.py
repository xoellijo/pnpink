# [2026-02-18 | v0.21] Fix: keep hole center stable when padding expands inline icon rect.
# [2026-02-18 | v0.21] Fix: preserve text order around inline icon tokens.
# [2026-02-18 | v0.21] Fix: avoid undefined DEFAULT_H in inline icon bbox fallback.
# [2026-02-18 | v0.21] Log: add inline-icons audit counters per stage.
# [2026-02-16 | v0.21] Cleanup: early-exit when no inline-icons; compute uu scaling only for mini pass-B.
# [2026-02-19] Chore: translate comments to English.
# [2026-02-16 | v0.21] Fix: convert mini pass-B query-all bboxes from px to viewBox units (uu) using root size.
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text.py ? inline icons with in-place ?I? spacer (no rich text rebuild)

- ALWAYS reads from <text> (not label).
- Converts rich-visible -> DOM in *all* <text> nodes in scope (with or without :icon:),
  sanitizing unquoted attributes and EMITTING WARNING if conversion fails.
- Inserts in-place <tspan id=...> spacers where :icon: appears (does not touch the rest).
- Single query (SVG.query_all) to measure spacer bboxes.
- Icon centered in the hole [I + letter-spacing]; baseline and center computed
  in local <text> axes (robust against rotations).
"""

from __future__ import annotations
import os, sys, re, math, copy
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Set

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
_l = LOG
__version__ = "text.py v7.51 (in-place; 1-query; baseline I; rich-visible→DOM all+warnings; vector placement; sin label)"

# --------- tweaks ----------
NBSP = "\u00A0"
EPS = 1e-12

EXTRA_RATIO = 0.10   # margen extra (fracción de H) que añadimos al hueco
OVERSHOOT   = 0.98   # apoyo óptico sobre baseline (1.00 = exacto)

DEFAULT_SPACER_GLYPH = "I"  # configurable vía --spacer_glyph
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
        if _INLINE_ID_RX.fullmatch(inner or ""):
            id_hit = (j_id, end_colon+1, inner)
            break
        j_id = s.find(":", j_id + 1)

    hits = [h for h in (src_hit, src2_hit, id_hit) if h is not None]
    if not hits:
        return None
    return sorted(hits, key=lambda h: h[0])[0]

def _parse_source_inner_token(inner: str):
    """Parse inline source inner token (@{...} / S{...} / Source{...}) into (src_uri, suffix)."""
    s = (inner or "").strip()
    if s.startswith("@{"):
        dsl_src, suffix = DSL.split_source_token(s)
        return (dsl_src.src or "").strip(), suffix

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
class TokenItem:
    spacer_id: str
    src_expr: str              # full DSL source token string, e.g. "@{icon://noto/cat}~^15"
    src_uri: str               # resolved URI passed to SourceManager.register (e.g. "icon://noto/cat", "img/a.png", "sp1[2]")
    suffix: "DSL.SourceSuffix" # parsed suffix (none|fit|ops)
    text_id: str
    H_local: float
    is_doc_id: bool = False

    # resolved runtime (filled later)
    symbol_id: Optional[str] = None
    intrinsic_wh: Optional[Tuple[float,float]] = None
    hole_fit: Optional["DSL.FitSpec"] = None    # only border/shift used
    icon_fit: Optional["DSL.FitSpec"] = None    # border/shift stripped; used by fit_anchor
    hole_pad_trbl: Optional[Tuple[float,float,float,float]] = None  # (t,r,b,l) in doc uu
    hole_wh_doc: Optional[Tuple[float,float]] = None               # (W,H) in doc uu
    hole_wh_base_doc: Optional[Tuple[float,float]] = None          # (W,H) before padding, doc uu
@dataclass
class ProcessResult:
    icons_placed: int
    used_sources: Set[str]

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

# ----------------- unidades ----------
def _uu_per_px(doc_root: SVG.etree._Element) -> float:
    try:
        vb = (doc_root.get("viewBox") or "").replace(",", " ").split()
        if len(vb) != 4: return 1.0
        vb_w = float(vb[2])
        w_px = SVG.parse_len_px(doc_root, doc_root.get("width"))
        if vb_w > 0 and w_px > 0:
            px_per_uu = w_px / vb_w
            return 1.0 / px_per_uu
    except Exception:
        pass
    return 1.0


def _uu_per_px_xy(doc_root: SVG.etree._Element) -> tuple[float,float]:
    """Return (uu_per_px_x, uu_per_px_y) based on viewBox and width/height.
    Inkscape --query-all reports in px; we need viewBox-units (uu) to match the document geometry.
    """
    try:
        vb = (doc_root.get("viewBox") or "").replace(",", " ").split()
        if len(vb) != 4:
            return (1.0, 1.0)
        vb_w = float(vb[2]); vb_h = float(vb[3])
        w_px = SVG.parse_len_px(doc_root, doc_root.get("width"))
        h_px = SVG.parse_len_px(doc_root, doc_root.get("height"))
        ux = (vb_w / w_px) if (vb_w > 0 and w_px > 0) else 1.0
        uy = (vb_h / h_px) if (vb_h > 0 and h_px > 0) else 1.0
        return (ux, uy)
    except Exception:
        return (1.0, 1.0)


def _scale_bboxes_px_to_uu_xy(bbs: Dict[str,Dict[str,float]], uu_per_px_xy: tuple[float,float]) -> Dict[str,Dict[str,float]]:
    if not bbs:
        return bbs
    ux, uy = uu_per_px_xy
    if abs(ux - 1.0) < 1e-12 and abs(uy - 1.0) < 1e-12:
        return bbs
    out: Dict[str,Dict[str,float]] = {}
    for k, bb in bbs.items():
        nb = dict(bb)
        if "x" in nb: nb["x"] = float(nb["x"]) * ux
        if "width" in nb: nb["width"] = float(nb["width"]) * ux
        if "y" in nb: nb["y"] = float(nb["y"]) * uy
        if "height" in nb: nb["height"] = float(nb["height"]) * uy
        out[k] = nb
    return out

def _scale_bboxes_px_to_uu(bbs: Dict[str,Dict[str,float]], uu_per_px: float) -> Dict[str,Dict[str,float]]:
    if not bbs or abs(uu_per_px - 1.0) < 1e-12:
        return bbs
    out: Dict[str,Dict[str,float]] = {}
    for k, bb in bbs.items():
        nb = dict(bb)
        for key in ("x","y","width","height"):
            if key in nb:
                nb[key] = float(nb[key]) * uu_per_px
        out[k] = nb
    return out

# -------- icon bbox via SVG.visual_bbox / inkex --------
def _icon_bbox_uu(doc_root: SVG.etree._Element, icon_id: str) -> Dict[str, float]:
    node = (doc_root.xpath(f".//*[@id='{icon_id}']") or [None])[0]
    if node is None:
        _l.w("icon id '%s' no encontrado — uso 1×1", icon_id)
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
def _maybe_parse_rich_visible_into_dom(text_el: SVG.etree._Element) -> bool:
    """Convierte literal '<tspan ...>' en nodos <tspan> reales dentro del <text>, con saneo y warnings."""
    try:
        # If there are already real <tspan> nodes, do nothing
        if text_el.find(".//{%s}tspan" % NS['svg']) is not None:
            return False

        visible = text_el.text or ""
        if "<tspan" not in visible:
            return False

        # Intento de saneo
        visible_sane = _sanitize_rich_visible(visible)
        if visible_sane != visible:
            _l.w("id=%s — se detectaron atributos sin comillas en <tspan> (saneados automáticamente)", text_el.get("id"))
        
        # Escape text outside tags so any stray '&' does not break XML
        visible_sane = _escape_text_nodes_only(visible_sane)

        wrapper = f"<svg xmlns='{NS['svg']}'><text xmlns='{NS['svg']}'>{visible_sane}</text></svg>"
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
        _l.i("normalized rich-visible→DOM en %d <text>(s)", count)
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

def _process_text_fragment(text_el: SVG.etree._Element, node: SVG.etree._Element, attr_name: str, seq_next: int, spacer_glyph: str, out_items: List[TokenItem]) -> int:
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

        # texto previo al token
        acc += s[pos:t0]

        # try parsing :@{...}: / :S{...}: / :Source{...}:  or :id:
        if inner.startswith("@{") or inner.lower().startswith("s{") or inner.lower().startswith("source{"):
            try:
                src_uri, suffix = _parse_source_inner_token(inner)
            except Exception as ex:
                # token mal formado → dejar literal (mejor UX)
                acc += s[t0:t1]
                _l.w(f"[inline_icons] token inválido (se deja literal): {s[t0:t1]!r}  ({ex})")
                pos = t1
                continue

            if not src_uri:
                acc += s[t0:t1]
                _l.w(f"[inline_icons] token sin src (se deja literal): {s[t0:t1]!r}")
                pos = t1
                continue
            is_doc_id = False
        else:
            if not _INLINE_ID_RX.fullmatch(inner or ""):
                acc += s[t0:t1]
                _l.w(f"[inline_icons] token inválido (se deja literal): {s[t0:t1]!r}")
                pos = t1
                continue
            src_uri = inner
            suffix = None
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
            is_doc_id=is_doc_id
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

    # remanente
    setattr(node, attr_name, acc)
    return seq_next
def _inject_spacers_in_place(text_el: SVG.etree._Element, spacer_glyph: str) -> List[TokenItem]:
    seq = 0
    items: List[TokenItem] = []

    # Process the <text>.text itself
    seq = _process_text_fragment(text_el, text_el, "text", seq, spacer_glyph, items)

    # Recorrer descendientes: sus .text y .tail
    for n in list(text_el.iterdescendants()):
        seq = _process_text_fragment(text_el, n, "text", seq, spacer_glyph, items)
        seq = _process_text_fragment(text_el, n, "tail", seq, spacer_glyph, items)

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
            # Buscar hasta ';' o separador
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

# ----------------- symbols -----------------
def _ensure_wrap_symbol(doc_root: SVG.etree._Element, icon_id: str, bb: Dict[str,float]) -> str:
    defs = doc_root.find(".//svg:defs", namespaces={"svg": NS["svg"]})
    if defs is None:
        defs = SVG.etree.SubElement(doc_root, f"{{{NS['svg']}}}defs")
    wrap_id = f"wrap_{icon_id}"
    if doc_root.xpath(f".//*[@id='{wrap_id}']"):
        return wrap_id

    bw = max(1e-6, bb.get("width", 1.0))
    bh = max(1e-6, bb.get("height", 1.0))
    bx = bb.get("x", 0.0)
    by = bb.get("y", 0.0)

    sym = SVG.etree.SubElement(defs, f"{{{NS['svg']}}}symbol", id=wrap_id)
    sym.set("viewBox", f"0 0 {bw} {bh}")
    sym.set("preserveAspectRatio", "none")

    inner = SVG.etree.SubElement(sym, f"{{{NS['svg']}}}use")
    inner.set(f"{{{NS['xlink']}}}href", f"#{icon_id}")
    inner.set("href", f"#{icon_id}")
    if bx or by:
        inner.set("transform", f"translate({-bx:.6f},{-by:.6f})")
    return wrap_id

# ----------------- main -----------------
def inline_place_icons(root_scope: SVG.etree._Element, show_debug_rects: bool=False, spacer_glyph: Optional[str]=None, *, source_manager: Optional[SRC.SourceManager]=None, doc_path: Optional[str]=None) -> ProcessResult:
    spacer_glyph = spacer_glyph or DEFAULT_SPACER_GLYPH

    doc_root = root_scope.getroottree().getroot()
    tree = root_scope.getroottree()
    SVG.ensure_xlink_ns(doc_root)

    _l.i("scope=%s — in-place pipeline (spacer glyph=%r)", root_scope.get('id'), spacer_glyph)

    # 0) **NORMALIZAR RICH-VISIBLE → DOM PARA TODOS LOS <text>**
    normalized = _normalize_rich_visible_for_all_texts(root_scope)
    if normalized:
        _l.i("normalized=%d texts (rich-visible→DOM)", normalized)

    # 1) localizar SOLO los <text> que contengan :@{...}:
    texts_with_icons: List[SVG.etree._Element] = []
    for t in root_scope.findall(".//svg:text", namespaces={"svg":NS["svg"]}):
        try:
            vis = t.xpath("string(.)") or ""
        except Exception:
            vis = ""
        if (":" in vis) and _find_inline_token(vis, 0):
            texts_with_icons.append(t)

    if not texts_with_icons:
        _l.i("No <text> with :@{...}: tokens found.")
        return ProcessResult(0, set())

    # ensure unique id
    used_ids: Set[str] = set(x.get("id") for x in doc_root.xpath(".//*[@id]"))
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
        items = _inject_spacers_in_place(t, spacer_glyph)
        for it in items:
            used_sources.add(it.src_uri)
            spacers.add(it.spacer_id)
        all_items.extend(items)

    if not all_items:
        _l.i("[inline_icons] stage=parse_tokens texts=%d tokens=0", len(texts_with_icons))
        _l.i("Found texts but zero tokens after insert.")
        return ProcessResult(0, set())
    _l.i("[inline_icons] stage=parse_tokens texts=%d tokens=%d", len(texts_with_icons), len(all_items))
    _l.i("[inline_icons] stage=holes holes=%d", len(spacers))

    # compute sizes and APPLY HOLES (letter-spacing in the spacer)
    # Resolve sources with the same behavior as @{...}: placeholders and logs in sources.py
    if source_manager is None:
        source_manager = SRC.SourceManager(doc_root, doc_path, project_root=None)

    opened = 0
    placeholder_count = 0
    for it in all_items:
        # 1) parse Fit/ops suffixes from token
        fs_all = DSL.FitSpec()
        try:
            if it.suffix and getattr(it.suffix, "kind", None) == "fit":
                fs_all = it.suffix.fit or DSL.FitSpec()
            elif it.suffix and getattr(it.suffix, "kind", None) == "ops":
                fs_all = DSL._parse_fit_shorthand(it.suffix.ops or "")
        except Exception as ex:
            _l.w(f"[inline_icons] fit suffix parse failed for {it.src_expr!r}: {ex}")
            fs_all = DSL.FitSpec()

        # 2) split hole (border/shift) vs icon fit
        hole_fs = DSL.FitSpec(border=getattr(fs_all, "border", None), shift=getattr(fs_all, "shift", None))
        icon_fs = copy.deepcopy(fs_all)
        icon_fs.border = None
        icon_fs.shift = None
        if icon_fs.mode is None:
            icon_fs.mode = "i"
        if icon_fs.anchor is None:
            icon_fs.anchor = 5

        it.hole_fit = hole_fs
        it.icon_fit = icon_fs

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

        iw, ih = it.intrinsic_wh
        iw = max(1e-6, float(iw)); ih = max(1e-6, float(ih))
        ratio = iw / ih

        # item's <text>
        t_el = (doc_root.xpath(f".//*[@id='{it.text_id}']") or [None])[0]
        if t_el is None:
            _l.w("text element missing for id=%s", it.text_id); continue

        aT,bT,cT,dT,eT,fT = _matrix6_from_transform(SVG.composed_transform(t_el))
        Sx_text, Sy_text = _scale_from_matrix(aT,bT,cT,dT)

        # Sizes in DOC (uu)
        H_doc = it.H_local * Sy_text
        W_icon_d = H_doc * ratio
        extra_d = EXTRA_RATIO * H_doc

        # Hole base (before padding expansion)
        hole_base_w = W_icon_d + extra_d
        hole_base_h = H_doc
        hole_w, hole_h = hole_base_w, hole_base_h

        # Hole border/outset (uses standard border parser)
        pad_t = pad_r = pad_b = pad_l = 0.0
        if hole_fs and getattr(hole_fs, "border", None):
            try:
                pad_t, pad_r, pad_b, pad_l, _, _ = SVG.border_tokens_to_pad_px(doc_root, float(hole_w), float(hole_h), hole_fs.border)
                # Interpretation for inline_icons: border INCREASES the hole (outset)
                hole_w = hole_w + pad_l + pad_r
                hole_h = hole_h + pad_t + pad_b
            except Exception as ex:
                _l.w(f"[inline_icons] hole border parse failed for {it.src_expr!r}: {ex}")
                pad_t = pad_r = pad_b = pad_l = 0.0

        it.hole_pad_trbl = (float(pad_t), float(pad_r), float(pad_b), float(pad_l))
        it.hole_wh_doc = (float(hole_w), float(hole_h))
        it.hole_wh_base_doc = (float(hole_base_w), float(hole_base_h))

        # letter-spacing en LOCAL(<text>)
        W_hole_loc = hole_w / max(EPS, Sx_text)

        spacer = (doc_root.xpath(f".//*[@id='{it.spacer_id}']") or [])
        if spacer:
            smap = SVG.style_map(spacer[0])
            smap["letter-spacing"] = f"{W_hole_loc:.4f}px"
            SVG.style_set(spacer[0], smap)
            opened += 1
        else:
            _l.w("spacer not found for id=%s", it.spacer_id)

    cache_hits = getattr(source_manager, "_cache_hits", None)
    cache_misses = getattr(source_manager, "_cache_misses", None)
    if cache_hits is not None and cache_misses is not None:
        _l.i("[inline_icons] stage=cache placeholders=%d cache_hits=%d cache_misses=%d", placeholder_count, cache_hits, cache_misses)
    else:
        _l.i("[inline_icons] stage=cache placeholders=%d cache_hits=? cache_misses=?", placeholder_count)
    _l.i("huecos aplicados=%d", opened)

# PASO B: medir spacers (1 query)
    ids_text = sorted(spacers)
    uu_xy = _uu_per_px_xy(doc_root)
    _l.i("[inline_icons] stage=passB_query ids=%d", len(ids_text))
    # Reduced query scope for speed; keeps text/defs/style/page context.
    bbs_doc = SVG.query_all(tree, ids_text, minimize_for_ids=True)
    bbs_doc = _scale_bboxes_px_to_uu_xy(bbs_doc, uu_xy)
    _l.i("[inline_icons] stage=passB_bboxes bboxes=%d", len(bbs_doc))

    # Icon placement (FitAnchor over a hole-rect in a <g> rotated like the text)
    placed = 0
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

        # --- geometry in <text> axes (robust against rotations) ---
        mag_u = math.hypot(aT, bT) or 1.0
        mag_v = math.hypot(cT, dT) or 1.0
        ux, uy = aT / mag_u, bT / mag_u         # +X local → DOC
        vx, vy = cT / mag_v, dT / mag_v         # +Y local → DOC

        # Center of spacer bbox (DOC)
        cx = xI_left + wI * 0.5
        cy = yI_top  + hI * 0.5

        # Center of hole [I + hole] along the text flow
        # (use base size to keep center stable when padding expands the rect)
        x_center_doc = cx + ux * (hole_base_w * 0.5)
        y_center_doc = cy + uy * (hole_base_w * 0.5)

        # Baseline point (lower edge of the “I”)
        baseline_x = x_center_doc + vx * (H_doc * 0.5)
        baseline_y = y_center_doc + vy * (H_doc * 0.5)

        # Hole top-left in DOC (before shift)
        x_left_doc = baseline_x - vx * (OVERSHOOT * H_doc) - ux * (hole_base_w * 0.5)
        y_top_doc  = baseline_y - vy * (OVERSHOOT * H_doc) - uy * (hole_base_w * 0.5)

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

        # Apply FitAnchor to insert the icon inside the rect
        try:
            FA.apply_to_by_ids(doc_root, it.symbol_id, "", it.icon_fit, rect_elem=rect, parent_elem=g, place_mode="clone")
        except Exception as ex:
            _l.w(f"[inline_icons] fit_anchor failed for {it.src_expr!r}: {ex}")

        placed += 1

        if not show_debug_rects:
            try:
                g.remove(rect)
            except Exception:
                pass

        if show_debug_rects:
            rect.set("style", "fill:none;stroke:red;stroke-width:0.4")

    _l.i("inline_icons placed=%d", placed)
    _l.i("[inline_icons] stage=insert_use use_count=%d", placed)
    return ProcessResult(placed, used_sources)

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
