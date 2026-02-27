# [2026-02-18] Log: add query-all audit logs for inline-icons pipeline.
# [2026-02-19] Fix: honor explicit data-bbox for stable group measurement.
# [2026-02-19] Chore: translate comments to English.

def coerce_margins_mm(mg):
    """Return an object with .left/.top/.right/.bottom from object/dict/tuple/list (mm units)."""
    from types import SimpleNamespace
    z = SimpleNamespace(left=0.0, top=0.0, right=0.0, bottom=0.0)
    if mg is None:
        return z
    if hasattr(mg, 'left') and hasattr(mg, 'top') and hasattr(mg, 'right') and hasattr(mg, 'bottom'):
        return mg
    if isinstance(mg, dict):
        try:
            return SimpleNamespace(
                left=float(mg.get('left', 0.0)),
                top=float(mg.get('top', 0.0)),
                right=float(mg.get('right', 0.0)),
                bottom=float(mg.get('bottom', 0.0)),
            )
        except Exception:
            return z
    if isinstance(mg, (tuple, list)) and len(mg) == 4:
        try:
            return SimpleNamespace(left=float(mg[0]), top=float(mg[1]), right=float(mg[2]), bottom=float(mg[3]))
        except Exception:
            return z
    return z

# svg.py — PnPInk SVG helpers (organizado, compat total)
# ------------------------------------------------------
__version__ = "v_7.3.0"

import os, sys, re, math, tempfile, subprocess
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse, unquote

sys.path.append(os.path.dirname(__file__))

import log as LOG
_l = LOG
import inkex
import const as CONST

# inkex namespace map (prefix -> uri). Canonical: build from inkex.NSS + CONST.NS_*
NSS = dict(getattr(inkex, 'NSS', {}) or {})
NSS.setdefault('svg', getattr(CONST, 'NS_SVG', 'http://www.w3.org/2000/svg'))
NSS.setdefault('xlink', getattr(CONST, 'NS_XLINK', 'http://www.w3.org/1999/xlink'))
NSS.setdefault('inkscape', getattr(CONST, 'NS_INKSCAPE', 'http://www.inkscape.org/namespaces/inkscape'))
NSS.setdefault('sodipodi', getattr(CONST, 'NS_SODIPODI', 'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd'))
NSS.setdefault('xml', getattr(CONST, 'NS_XML', 'http://www.w3.org/XML/1998/namespace'))
try:
    from inkex import etree
except ImportError:
    from lxml import etree
from collections import namedtuple


# ---------------------------------------------------------------------------
# Namespace convenience aliases (derived from NSS; keep API compatibility)
# ---------------------------------------------------------------------------

# Back-compat helper mapping used by older modules (do NOT treat as canonical)


# --------------------- measure parsing ---------------------
_MEASURE_TERM_RE = re.compile(r"""^\s*(?P<sign>[+-])?\s*(?P<num>(?:\d+(?:\.\d*)?|\.\d+)?)?\s*(?P<unit>%|mm|cm|in)?\s*$""", re.IGNORECASE)

def measure_to_mm(value, *, base_mm: float | None = None) -> float:
    """
    Convert a measure to millimeters.

    Supports:
      - numbers (int/float) -> mm
      - 'N', 'Nmm', 'Ncm', 'Nin'
      - 'N%' -> percentage of base_mm
      - simple expressions with + and - (e.g. '5mm+3%')

    If '%' is used and base_mm is None, returns 0.0 and emits a warning.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return 0.0

    # Tokenize by +/- preserving signs (no parentheses; simple case)
    tokens = []
    buf = ""
    for ch in s:
        if ch in "+-" and buf.strip():
            tokens.append(buf.strip())
            buf = ch
        else:
            buf += ch
    if buf.strip():
        tokens.append(buf.strip())

    total = 0.0
    for t in tokens:
        m = _MEASURE_TERM_RE.match(t)
        if not m:
            _l.w(f"[measure_to_mm] invalid token '{t}' in '{s}'")
            continue
        sign = -1.0 if (m.group('sign') == '-') else 1.0
        num_s = (m.group('num') or '').strip()
        unit = (m.group('unit') or 'mm').lower()
        if not num_s and unit == '%':
            num = 100.0
        elif not num_s:
            num = 0.0
        else:
            num = float(num_s)

        if unit == '%':
            if base_mm is None:
                _l.w(f"[measure_to_mm] percentage without base_mm: '{t}' (expr='{s}')")
                term = 0.0
            else:
                term = (num / 100.0) * float(base_mm)
        elif unit == 'mm':
            term = num
        elif unit == 'cm':
            term = num * 10.0
        elif unit == 'in':
            term = num * 25.4
        else:
            term = num
        total += sign * term

    return float(total)


_BORDER_WXH_SPLIT_RE = re.compile(r"^\s*(?P<w>[^x]+)x(?P<h>.+?)\s*$", re.IGNORECASE)

def _border_try_split_wxh(tok):
    if not tok:
        return None
    m = _BORDER_WXH_SPLIT_RE.match(str(tok))
    if not m:
        return None
    return (m.group('w').strip(), m.group('h').strip())

def border_tokens_to_mm4(tokens, *, base_w_mm: float, base_h_mm: float):
    """Parse border tokens into [top,right,bottom,left] in mm.

    Supports:
      - 1..4 CSS shorthand tokens (numbers, units, %, expressions)
      - 1 token in WxH form -> target absolute size centered; returns padding that achieves it.

    Percent semantics (legacy):
      - 1 token containing '%' => % refers to TOTAL of both sides, so /2 per side
      - 2 tokens with any '%' => first is vertical(total), second is horizontal(total), both /2 per side
      - 3/4 tokens => % is per-side (no /2)
    """
    toks = [str(t).strip() for t in (tokens or []) if str(t).strip() != '']
    if not toks:
        return [0.0, 0.0, 0.0, 0.0]

    # WxH absolute centered
    if len(toks) == 1:
        wxh = _border_try_split_wxh(toks[0])
        if wxh:
            w_tok, h_tok = wxh
            tw = float(measure_to_mm(w_tok, base_mm=base_w_mm))
            th = float(measure_to_mm(h_tok, base_mm=base_h_mm))
            tw = abs(tw)
            th = abs(th)
            dx = (tw - float(base_w_mm)) / 2.0
            dy = (th - float(base_h_mm)) / 2.0
            return [dy, dx, dy, dx]

    has_pct = any('%' in t for t in toks)

    if len(toks) == 1:
        t0 = toks[0]
        if has_pct:
            vt = float(measure_to_mm(t0, base_mm=base_h_mm)) / 2.0
            vh = float(measure_to_mm(t0, base_mm=base_w_mm)) / 2.0
            return [vt, vh, vt, vh]
        v = float(measure_to_mm(t0, base_mm=None))
        return [v, v, v, v]

    if len(toks) == 2:
        t_v, t_h = toks[0], toks[1]
        if has_pct:
            vt = float(measure_to_mm(t_v, base_mm=base_h_mm)) / 2.0
            vh = float(measure_to_mm(t_h, base_mm=base_w_mm)) / 2.0
            return [vt, vh, vt, vh]
        v0 = float(measure_to_mm(t_v, base_mm=None))
        v1 = float(measure_to_mm(t_h, base_mm=None))
        return [v0, v1, v0, v1]

    if len(toks) == 3:
        toks = [toks[0], toks[1], toks[2], toks[1]]
    else:
        toks = toks[:4]

    t, r, b, l = toks
    mm_t = float(measure_to_mm(t, base_mm=base_h_mm if '%' in t else None))
    mm_b = float(measure_to_mm(b, base_mm=base_h_mm if '%' in b else None))
    mm_r = float(measure_to_mm(r, base_mm=base_w_mm if '%' in r else None))
    mm_l = float(measure_to_mm(l, base_mm=base_w_mm if '%' in l else None))
    return [mm_t, mm_r, mm_b, mm_l]


def border_tokens_to_pad_px(svgdoc, rect_w_px: float, rect_h_px: float, tokens):
    """Border parsing for FitAnchor in px, returning (t,r,b,l, mirror_h, mirror_v).

    mirror_h => flip horizontally (mirror over vertical axis)  [width component negative in WxH]
    mirror_v => flip vertically   (mirror over horizontal axis)[height component negative in WxH]
    """
    try:
        px_per_mm = float(svgdoc.unittouu("1mm"))
    except Exception:
        px_per_mm = 1.0

    base_w_mm = float(rect_w_px) / px_per_mm if px_per_mm else 0.0
    base_h_mm = float(rect_h_px) / px_per_mm if px_per_mm else 0.0

    toks = [str(t).strip() for t in (tokens or []) if str(t).strip() != '']
    if not toks:
        return (0.0, 0.0, 0.0, 0.0, False, False)

    # WxH absolute centered (with optional mirroring via negative components)
    if len(toks) == 1:
        wxh = _border_try_split_wxh(toks[0])
        if wxh:
            w_tok, h_tok = wxh
            tw_mm = float(measure_to_mm(w_tok, base_mm=base_w_mm))
            th_mm = float(measure_to_mm(h_tok, base_mm=base_h_mm))
            mirror_h = (tw_mm < 0.0)
            mirror_v = (th_mm < 0.0)
            tw_mm = abs(tw_mm)
            th_mm = abs(th_mm)
            dx_px = ((tw_mm - base_w_mm) / 2.0) * px_per_mm
            dy_px = ((th_mm - base_h_mm) / 2.0) * px_per_mm
            return (dy_px, dx_px, dy_px, dx_px, mirror_h, mirror_v)

    # Legacy padding semantics
    has_pct = any('%' in t for t in toks)

    def _tok_to_px(tok: str, *, base_mm=None) -> float:
        s = (tok or '').strip()
        if not s:
            return 0.0
        if '%' in s:
            return float(measure_to_mm(s, base_mm=base_mm)) * px_per_mm
        return float(parse_len_px(svgdoc, s))

    if len(toks) == 1 and has_pct:
        t0 = toks[0]
        vt = _tok_to_px(t0, base_mm=base_h_mm) / 2.0
        vh = _tok_to_px(t0, base_mm=base_w_mm) / 2.0
        return (vt, vh, vt, vh, False, False)

    if len(toks) == 2 and has_pct:
        tv, th = toks[0], toks[1]
        vt = _tok_to_px(tv, base_mm=base_h_mm) / 2.0
        vh = _tok_to_px(th, base_mm=base_w_mm) / 2.0
        return (vt, vh, vt, vh, False, False)

    vals_px = [_tok_to_px(t, base_mm=None) for t in toks]
    # CSS shorthand
    if len(vals_px) == 1:
        t = r = b = l = vals_px[0]
    elif len(vals_px) == 2:
        t, r = vals_px[0], vals_px[1]
        b, l = t, r
    elif len(vals_px) == 3:
        t, r, b = vals_px[0], vals_px[1], vals_px[2]
        l = r
    else:
        t, r, b, l = vals_px[0], vals_px[1], vals_px[2], vals_px[3]
    return (t, r, b, l, False, False)




# ========= Namespaces / Constantes / Unidades =================================
# Canonical namespaces map: use `NSS` (prefix -> uri). Do NOT reintroduce NSMAP/CONST.NSMAP.

DPI       = 96.0
PX_PER_IN = DPI
IN_PER_PX = 1.0 / DPI
PX_PER_MM = DPI / 25.4
MM_PER_PX = 25.4 / DPI
PX_PER_CM = DPI / 2.54
CM_PER_PX = 2.54 / DPI

PAGE_ATTR_KEYS = {"margin","bleed","label","id",CONST.INK_PAGEOPACITY_KEY,CONST.INK_PAGECHECKERBOARD_KEY}
DEFAULT_PAGE_ATTRS = {"margin":"0","bleed":"0",CONST.INK_PAGEOPACITY_KEY:"0.0",CONST.INK_PAGECHECKERBOARD_KEY:"0"}
DEFAULT_PAGE_APPEND_GAP_PX = 20.0

LAYER_LABELS = {"root":"DeckMaker","cards":"Cards","guides":"Guides"}

XLINK_HREF  = inkex.addNS('href','xlink')
SODI_ABSREF = inkex.addNS('absref','sodipodi')

def get_href(node):
    """Return href value for <use>/<image> etc., preferring xlink:href but accepting href."""
    return node.get(XLINK_HREF) or node.get('href') or ""

def set_href(node, href_value: str, *, touch_plain: bool = True):
    """Set both xlink:href and (optionally) plain href for maximum compatibility."""
    node.set(XLINK_HREF, href_value)
    if touch_plain:
        node.set('href', href_value)


# ========= Units / Document Sizes ============================================

def parse_len_px(svg_root, s: str) -> float:
    if s is None: return 0.0
    if isinstance(s, (int, float)): return float(s)
    s = str(s).strip()
    if not s: return 0.0
    try: return float(s)
    except Exception: pass
    num = ''.join(ch for ch in s if (ch.isdigit() or ch in ".-+eE"))
    unit = s[len(num):].strip().lower()
    try: val = float(num)
    except Exception:
        _l.w(f"parse_len_px: cannot parse '{s}'"); return 0.0
    if unit in ("","px"): return val
    if unit == "in": return val * PX_PER_IN
    if unit == "cm": return val * PX_PER_CM
    if unit == "mm": return val * PX_PER_MM
    if unit == "pt": return val * (PX_PER_IN/72.0)
    if unit == "pc": return val * (PX_PER_IN/6.0)
    if unit == "em": return val * 16.0
    if unit == "ex": return val * 8.0
    _l.w(f"parse_len_px: unknown unit '{unit}' in '{s}'")
    return val

def namedview(svg_root):
    nv = getattr(svg_root, "namedview", None)
    if nv is not None: return nv
    for el in svg_root:
        if isinstance(el.tag, str) and el.tag.endswith("namedview"): return el
    return None


def ensure_defs(root: inkex.SvgDocumentElement) -> etree._Element:
    """Ensure there is a <defs> element and return it."""
    defs = root.find(".//svg:defs", namespaces=NSS)
    if defs is None:
        defs = etree.SubElement(root, inkex.addNS('defs','svg'))
    return defs


def apply_clip_from_rect(svgdoc, node, rect_or_bbox, *, stage: str = "post", clip_id: str | None = None):
    """Aplica un clipPath rectangular a `node`.

    `rect_or_bbox` puede ser:
      - un elemento SVG (p. ej. el placeholder <rect>) -> se usa visual_bbox(...)
      - una tupla/lista (x,y,w,h) en coords de documento

    Nota: `stage` se mantiene por compat (pre/post) pero aquí solo se usa para logs.
    """
    if node is None or svgdoc is None:
        return

    try:
        root = svgdoc.getroot() if hasattr(svgdoc, "getroot") else svgdoc
    except Exception:
        root = svgdoc

    # BBox (userSpaceOnUse)
    try:
        if isinstance(rect_or_bbox, (tuple, list)) and len(rect_or_bbox) == 4:
            rx, ry, rw, rh = [float(v) for v in rect_or_bbox]
        else:
            rx, ry, rw, rh = visual_bbox(rect_or_bbox)
    except Exception:
        rx = ry = rw = rh = 0.0

    if rw <= 0.0 or rh <= 0.0:
        _l.w(f"[apply_clip_from_rect] bbox inválido stage='{stage}' rx={rx} ry={ry} rw={rw} rh={rh}")
        return

    # ids
    try:
        node_id = node.get('id') or 'node'
    except Exception:
        node_id = 'node'

    if clip_id is None:
        clip_id = f"clip_{node_id}"

    defs = ensure_defs(root)

    # Reuse if it already exists
    cp = root.find(f".//svg:clipPath[@id='{clip_id}']", namespaces=NSS)
    if cp is None:
        cp = etree.SubElement(defs, inkex.addNS('clipPath', 'svg'))
        cp.set('id', clip_id)
        cp.set('clipPathUnits', 'userSpaceOnUse')
        r = etree.SubElement(cp, inkex.addNS('rect', 'svg'))
    else:
        # ensure the first child is a rect
        r = None
        for ch in list(cp):
            if hasattr(ch, 'tag') and str(ch.tag).endswith('rect'):
                r = ch
                break
        if r is None:
            # limpiar y recrear
            for ch in list(cp):
                try:
                    cp.remove(ch)
                except Exception:
                    pass
            r = etree.SubElement(cp, inkex.addNS('rect', 'svg'))

    r.set('x', f"{rx}")
    r.set('y', f"{ry}")
    r.set('width', f"{rw}")
    r.set('height', f"{rh}")

    node.set('clip-path', f"url(#{clip_id})")
    _l.d(f"[apply_clip_from_rect] stage='{stage}' node='{node_id}' clip_id='{clip_id}' bbox=({rx:.2f},{ry:.2f},{rw:.2f},{rh:.2f})")


def _safe_viewbox_values(svg_root):
    vbox = svg_root.get("viewBox")
    if not vbox: return None
    try:
        vals = [float(v) for v in vbox.replace(",", " ").split()]
        if len(vals) == 4: return tuple(vals)
    except Exception: return None
    return None

def _safe_doc_wh(svg_root):
    try:
        w = parse_len_px(svg_root, svg_root.get("width"))
        h = parse_len_px(svg_root, svg_root.get("height"))
        return (w, h)
    except Exception: return None

def page_size_px(svg_root):
    vb = _safe_viewbox_values(svg_root)
    if vb: return (vb[2], vb[3])
    wh = _safe_doc_wh(svg_root)
    if wh: return wh
    return (0.0, 0.0)

# ========= Pages / Layers =====================================================

def _make_inkscape_page_el(x_px, y_px, w_px, h_px, page_id, attrs=None):
    """Create an inkscape:page element with our canonical attributes.

    NOTE: keep behavior aligned with legacy v0.11+: fixed defaults, optional attrs.
    """
    el = etree.Element(inkex.addNS("page", "inkscape"))
    el.set("id", page_id)
    el.set("x", str(x_px)); el.set("y", str(y_px))
    el.set("width", str(w_px)); el.set("height", str(h_px))
    el.set("margin", "0"); el.set("bleed", "0")
    el.set(inkex.addNS("pageopacity", "inkscape"), "0.0")
    el.set(inkex.addNS("pagecheckerboard", "inkscape"), "0")
    if attrs:
        for k, v in attrs.items():
            if k in ("margin", "bleed", "label"):
                el.set(k, str(v))
            elif k in (CONST.INK_PAGEOPACITY_KEY, "pageopacity"):
                el.set(inkex.addNS("pageopacity", "inkscape"), str(v))
            elif k in (CONST.INK_PAGECHECKERBOARD_KEY, "pagecheckerboard"):
                el.set(inkex.addNS("pagecheckerboard", "inkscape"), str(v))
    return el

def add_inkscape_page_mm(nv, x_px, y_px, w_px, h_px, page_id, attrs=None):
    el = _make_inkscape_page_el(x_px, y_px, w_px, h_px, page_id, attrs=attrs)
    nv.add(el)
    return el

def add_inkscape_page_mm_after(nv, after_el, x_px, y_px, w_px, h_px, page_id, attrs=None):
    """Insert an inkscape:page right after another inkscape:page element.

    Used to interleave pages (e.g. front/back pairs) without moving the already
    placed artwork. We only insert the page box element in the namedview; the
    caller is responsible for choosing an (x,y) that does not overlap.
    """
    el = _make_inkscape_page_el(x_px, y_px, w_px, h_px, page_id, attrs=attrs)
    try:
        pos = list(nv).index(after_el) + 1
    except Exception:
        pos = len(nv)
    try:
        nv.insert(pos, el)
    except Exception:
        nv.add(el)
    return el
def update_page_geometry(pages, page_index, w_px, h_px):
    """Update width/height of an existing inkscape:page in-place and cache info."""
    try:
        info = pages[page_index]
    except Exception:
        return
    el = info.get("el")
    if el is None:
        return
    el.set("width", str(w_px))
    el.set("height", str(h_px))
    info["w"] = w_px
    info["h"] = h_px


def set_page_attrs(page_el, attrs):
    """Set attributes on an <inkscape:page> element (no-op if attrs is empty)."""
    if page_el is None or not attrs:
        return
    for k, v in (attrs or {}).items():
        if v is None:
            continue
        try:
            page_el.set(k, str(v))
        except Exception:
            pass


def set_page_attrs_by_id(nv, page_id: str, attrs):
    """Find an <inkscape:page> under namedview by id and apply attrs."""
    if nv is None or not page_id or not attrs:
        return
    try:
        pages = nv.xpath("./inkscape:page", namespaces=NSS)
    except Exception:
        pages = []
    for el in pages:
        try:
            if el.get("id") == page_id:
                set_page_attrs(el, attrs)
                break
        except Exception:
            continue


def list_existing_pages_px(svg_root):
    pages = []
    nv = namedview(svg_root)
    if nv is None:
        _l.w("list_existing_pages_px: no namedview"); return pages
    for el in nv:
        if not (isinstance(el.tag,str) and el.tag.endswith("page")): continue
        try:
            pages.append({
                "id": el.get("id"),
                "x": parse_len_px(svg_root, el.get("x")),
                "y": parse_len_px(svg_root, el.get("y")),
                "w": parse_len_px(svg_root, el.get("width")),
                "h": parse_len_px(svg_root, el.get("height")),
                "el": el,
                "margin": el.get("margin"),
                "bleed": el.get("bleed"),
            })
        except Exception as e:
            _l.w(f"Bad page element {el}: {e}")
    return pages

def rightmost_page(pages):
    if not pages: return None
    return max(pages, key=lambda p: p["x"] + p["w"])

def next_dm_page_id(nv, prefix="dm_page_"):
    try: used = {(p.get("id") or "") for p in nv.xpath("./inkscape:page", namespaces=NSS)}
    except Exception: used = set()
    i = 1
    while True:
        pid = f"{prefix}{i}"
        if pid not in used: return pid
        i += 1

def ensure_page_for(nv, pages, page_index, w_px, h_px,
                    gap_px=DEFAULT_PAGE_APPEND_GAP_PX, attrs=None,
                    y_baseline=None, id_prefix="dm_page_"):
    if page_index < len(pages): return None
    created = None
    while len(pages) <= page_index:
        rm = rightmost_page(pages)
        x = (rm["x"] + rm["w"] + gap_px) if rm else 0.0
        y = y_baseline if y_baseline is not None else (pages[0]["y"] if pages else 0.0)
        pid = next_dm_page_id(nv, id_prefix)
        el = add_inkscape_page_mm(nv, x, y, w_px, h_px, pid, attrs)
        info = {"id":pid,"x":x,"y":y,"w":w_px,"h":h_px,"el":el}
        pages.append(info); created = info
        try:
            _l.i(f"[svg.ensure_page_for] created page idx={len(pages)} id='{pid}' x={x:.2f} y={y:.2f} w={w_px:.2f} h={h_px:.2f} gap={gap_px:.2f}")
        except Exception:
            pass
    return created


def ensure_page_for_or_update(nv, pages, page_index, w_px, h_px,
                             gap_px=DEFAULT_PAGE_APPEND_GAP_PX, attrs=None,
                             y_baseline=None, id_prefix="dm_page_"):
    """Ensure a page exists. If it already exists, update geometry + attrs.

    Returns the created page info dict when a new page is created, else None.
    """
    if page_index < len(pages):
        try:
            if attrs:
                set_page_attrs_by_id(nv, pages[page_index]["id"], attrs)
        except Exception as ex:
            try: _l.w(f"[svg.ensure_page_for_or_update] set attrs failed idx={page_index}: {ex}")
            except Exception: pass
        try:
            update_page_geometry(pages, page_index, w_px, h_px)
        except Exception as ex:
            try: _l.w(f"[svg.ensure_page_for_or_update] update geometry failed idx={page_index}: {ex}")
            except Exception: pass
        return None
    return ensure_page_for(nv, pages, page_index, w_px, h_px,
                           gap_px=gap_px, attrs=attrs, y_baseline=y_baseline, id_prefix=id_prefix)

def find_or_create_layer(root, label):
    for el in root:
        if isinstance(el.tag,str) and el.tag.endswith("g") and el.get(inkex.addNS("groupmode","inkscape"))=="layer":
            if el.get(inkex.addNS("label","inkscape")) == label: return el
    g = etree.Element(inkex.addNS("g","svg"))
    g.set(inkex.addNS("groupmode","inkscape"), "layer")
    g.set(inkex.addNS("label","inkscape"), label)
    root.append(g)
    return g

# ========= Transforms (inkex) / Geometry =====================================

def _get_transform(el):
    t = el.get("transform")
    if not t: return inkex.Transform()
    try: return inkex.Transform(t)
    except Exception as e:
        _l.w(f"_get_transform: invalid transform '{t}': {e}")
        return inkex.Transform()

def _set_transform(el, T: inkex.Transform):
    s = str(T)
    if s and s != "matrix(1,0,0,1,0,0)": el.set("transform", s)
    else:
        if "transform" in el.attrib: del el.attrib["transform"]

def apply_translation(el, tx: float, ty: float):
    try:
        T = _get_transform(el) @ inkex.Transform(f"translate({tx},{ty})")
        _set_transform(el, T)
    except Exception as e:
        _l.e(f"apply_translation failed: {e}")

def composed_transform(el):
    try:
        return el.composed_transform()
    except Exception:
        T = inkex.Transform()
        cur = el; chain = []
        while cur is not None:
            chain.append(cur)
            cur = cur.getparent() if hasattr(cur, "getparent") else None
        for node in reversed(chain):
            try: T = T @ _get_transform(node)
            except Exception: pass
        return T

def pick_anchor_in(scope):
    n = scope.find(".//*[@id='main_rect']")
    if n is not None: return n
    r = scope.find(".//svg:rect", namespaces=NSS)
    if r is not None: return r
    t = scope.find(".//svg:text", namespaces=NSS)
    if t is not None: return t
    return scope

def find_id(root, element_id: str, *, include_defs: bool = True):
    """Devuelve el primer nodo con @id == element_id (o None).

    PnPInk rule: by default include_defs=True because Iconify and other pipelines
    referencian <symbol>/<defs>. Si include_defs=False, se filtran resultados
    que estén dentro de <defs>.
    """
    if root is None or not element_id:
        return None
    try:
        hits = root.xpath(f".//*[@id='{element_id}']")
    except Exception:
        # Fallback without xpath (very rare)
        try:
            hits = root.findall(f".//*[@id='{element_id}']")
        except Exception:
            hits = []
    if not hits:
        return None
    if include_defs:
        return hits[0]
    for n in hits:
        try:
            cur = n.getparent()
            inside_defs = False
            while cur is not None:
                if hasattr(cur, "tag") and str(cur.tag).endswith("defs"):
                    inside_defs = True
                    break
                cur = cur.getparent()
            if not inside_defs:
                return n
        except Exception:
            # if we cannot inspect, accept as a candidate
            return n
    return None

# ========= Texto / XML / Estilos ============================================



def find_target_exact_in(scope, target_id: str):
    """Busca un target dentro de un scope por:
    1) @id == target_id
    2) @data-origid == target_id
    3) @data-field == target_id
    4) fallback: ids que coincidan tras strip_pnp_suffix()

    Nota: este helper vive en svg.py para evitar duplicidades en render.
    """
    if scope is None or not target_id:
        return None
    # 1) id
    n = find_id(scope, target_id, include_defs=True)
    if n is not None:
        return n
    # 2) data-origid / 3) data-field
    for attr in ("data-origid", "data-field"):
        try:
            hits = scope.xpath(f".//*[@{attr}='{target_id}']")
            if hits:
                return hits[0]
        except Exception:
            try:
                n = scope.find(f".//*[@{attr}='{target_id}']")
                if n is not None:
                    return n
            except Exception:
                pass
    # 4) suffix fallback
    try:
        for el in scope.iter():
            cid = el.get('id')
            if not cid:
                continue
            if strip_pnp_suffix(cid) == target_id:
                return el
    except Exception:
        pass
    return None


def resolve_local_id(scope, maybe_old_id: str):
    """Resuelve un id 'declarado' a su id local actual dentro del scope.

    Reglas (compat con implementación previa en render.py):
    - si existe @id == maybe_old_id → devuelve maybe_old_id
    - si existe @data-origid == maybe_old_id → devuelve el @id actual del elemento
    - fallback: scan ids y match por strip_pnp_suffix()
    """
    if scope is None or not maybe_old_id:
        return None
    # exact id
    el = find_id(scope, maybe_old_id, include_defs=True)
    if el is not None:
        return maybe_old_id
    # data-origid
    try:
        hits = scope.xpath(f".//*[@data-origid='{maybe_old_id}']")
        if hits:
            return hits[0].get('id')
    except Exception:
        try:
            el = scope.find(f".//*[@data-origid='{maybe_old_id}']")
            if el is not None:
                return el.get('id')
        except Exception:
            pass
    # suffix fallback
    try:
        for n in scope.iter():
            cid = n.get('id')
            if not cid:
                continue
            if strip_pnp_suffix(cid) == maybe_old_id:
                return cid
    except Exception:
        pass
    return None

def clear_children(el):
    for child in list(el): el.remove(child)

def is_text_like(el) -> bool:
    t = el.tag
    return (t.endswith("text") or t.endswith("tspan") or t.endswith("textPath")
            or t.endswith("flowRoot") or t.endswith("flowPara"))

def replace_text(el, value: str):
    try:
        clear_children(el); el.text = "" if value is None else str(value)
    except Exception as e:
        _l.e(f"replace_text failed: {e}")

def _parse_fragment(fragment: str):
    if fragment is None: return None
    frag = fragment.strip()
    if not frag: return None
    wrapped = f"<g>{frag}</g>"
    try:
        node = etree.fromstring(wrapped.encode("utf-8")); return node
    except Exception as e:
        _l.e(f"_parse_fragment: invalid XML fragment: {e}"); return None

def replace_xml(el, xml_fragment: str):
    try:
        node = _parse_fragment(xml_fragment)
        clear_children(el)
        if node is None:
            el.text = xml_fragment; return
        if isinstance(node.tag,str) and node.tag.endswith("g"):
            for child in list(node):
                node.remove(child); el.append(child)
        else:
            el.append(node)
    except Exception as e:
        _l.e(f"replace_xml failed: {e}")

def style_map(node: etree._Element):
    out = {}
    if node is None:
        return out
    # In some versions/documents, the 'style' attribute may not exist
    st = node.get("style")
    if not st:      # None, "" o solo espacios → devolvemos dict vacío
        return out
    for d in st.split(";"):
        if ":" in d:
            k, v = d.split(":", 1)
            out[k.strip()] = v.strip()
    return out

def style_set(node: etree._Element, m: dict):
    node.set("style", ";".join(f"{k}:{v}" for k,v in m.items() if v!=""))

# ========= BBox / Query CLI ==================================================

BBox = namedtuple("BBox", "left top width height")

def _write_temp_svg(tree: etree._ElementTree) -> str:
    path = tempfile.mktemp(suffix=".svg")
    etree.register_namespace("xlink", NSS.get('xlink', getattr(CONST,'NS_XLINK','http://www.w3.org/1999/xlink')))
    tree.write(path, pretty_print=False, xml_declaration=True, encoding="UTF-8")
    return path

def _build_minimal_tree_for_ids(tree: etree._ElementTree, ids: set) -> etree._ElementTree:
    """
    Build a reduced SVG tree that keeps:
      - requested ids and their ancestor chain
      - all <defs> nodes and their ancestor chain
      - root
    This drastically reduces `inkscape --query-all` cost for small id sets.
    """
    root_src = tree.getroot()
    root = deepcopy(root_src)
    keep = {root}

    # Keep requested ids (if present) and ancestors.
    for _id in (ids or set()):
        hits = root.xpath(f".//*[@id='{_id}']")
        for n in hits:
            cur = n
            while cur is not None:
                keep.add(cur)
                cur = cur.getparent()

    # Keep defs and ancestors (references may depend on defs).
    for d in root.findall(".//svg:defs", namespaces=NSS):
        cur = d
        while cur is not None:
            keep.add(cur)
            cur = cur.getparent()

    # Keep global style nodes and ancestors (text metrics depend on CSS).
    for st in root.findall(".//svg:style", namespaces=NSS):
        cur = st
        while cur is not None:
            keep.add(cur)
            cur = cur.getparent()

    # Keep namedview/pages context: Inkscape query coordinates can depend on
    # document page setup (especially multi-page docs).
    for nv in root.findall(".//sodipodi:namedview", namespaces=NSS):
        cur = nv
        while cur is not None:
            keep.add(cur)
            cur = cur.getparent()
        for n in nv.iter():
            keep.add(n)

    def _lname(tag) -> str:
        if not isinstance(tag, str):
            return ""
        if "}" in tag:
            return tag.rsplit("}", 1)[1]
        return tag

    # Text layout depends on sibling tspans/runs. If a queried id is inside a text-like
    # container, keep that whole subtree to preserve measured positions.
    text_like_roots = set()
    for _id in (ids or set()):
        hits = root.xpath(f".//*[@id='{_id}']")
        for n in hits:
            cur = n
            while cur is not None:
                ln = _lname(cur.tag).lower()
                if ln in ("text", "flowroot"):
                    text_like_roots.add(cur)
                    break
                cur = cur.getparent()
    for troot in text_like_roots:
        for n in troot.iter():
            keep.add(n)

    # Keep referenced nodes used by kept content (e.g. shape-inside, textPath,
    # clipPath, masks, gradients, markers, href uses).
    _url_ref_re = re.compile(r"url\(#([^)]+)\)")

    def _add_with_ancestors(n):
        cur = n
        while cur is not None:
            keep.add(cur)
            cur = cur.getparent()

    def _find_by_id(_id: str):
        if not _id:
            return None
        hits = root.xpath(f".//*[@id='{_id}']")
        return hits[0] if hits else None

    changed = True
    while changed:
        changed = False
        for n in list(keep):
            try:
                attrs = dict(n.attrib or {})
            except Exception:
                attrs = {}
            refs = []
            for _, v in attrs.items():
                sv = str(v or "")
                refs.extend(_url_ref_re.findall(sv))
                if sv.startswith("#") and len(sv) > 1:
                    refs.append(sv[1:])
            for rid in refs:
                tgt = _find_by_id(str(rid).strip())
                if tgt is not None and tgt not in keep:
                    _add_with_ancestors(tgt)
                    # Keep full subtree of the referenced node.
                    for d in tgt.iter():
                        keep.add(d)
                    changed = True

    def _prune(node):
        for ch in list(node):
            if ch in keep:
                _prune(ch)
            else:
                node.remove(ch)

    _prune(root)
    return etree.ElementTree(root)

def _parse_query_all(stdout: str, want_ids: set) -> dict:
    out = {}
    for line in (stdout or "").splitlines():
        parts = [p for p in re.split(r"[, \t]+", line.strip()) if p]
        if len(parts) < 5: continue
        _id = parts[0]
        if want_ids and _id not in want_ids: continue
        try:
            x,y,w,h = map(float, parts[1:5])
            out[_id] = {"x":x,"y":y,"width":w,"height":h}
        except Exception: pass
    return out

def query_all(tree: etree._ElementTree, ids: set, inkscape_bin: str = None, *, minimize_for_ids: bool = False) -> dict:
    """Devuelve bboxes de `ids` usando solo stdout de `inkscape --query-all`."""
    if not ids:
        return {}

    work_tree = tree
    if minimize_for_ids:
        try:
            work_tree = _build_minimal_tree_for_ids(tree, ids)
        except Exception as ex:
            _l.w("[svg.query_all] minimize_for_ids failed, fallback to full tree: %s", ex)
            work_tree = tree

    tmp = _write_temp_svg(work_tree)
    try:
        _l.i("[svg.query_all] tmp_svg='%s' ids=%d minimized=%s", tmp, len(ids), bool(minimize_for_ids))
        cmd = [inkscape_bin or "inkscape", "--query-all", tmp]
        _l.i("[svg.query_all] cmd=%s", " ".join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True)

        # Use stdout only; ignore stderr and returncode
        bbs = _parse_query_all(p.stdout or "", ids)
        _l.i("[svg.query_all] parsed_bboxes=%d", len(bbs))
        if bbs:
            return bbs

        # If nothing is parseable, we do treat it as an error
        raise RuntimeError("inkscape --query-all produced no usable output")

    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


# ========= XLink / Cloning / Linked Images ===================================

def ensure_xlink_ns(root):
    try:
        if 'xmlns:xlink' not in root.attrib:
            root.set('xmlns:xlink', NSS.get('xlink', getattr(CONST,'NS_XLINK','http://www.w3.org/1999/xlink'))); _l.d("ensure_xlink_ns: added xmlns:xlink")
    except Exception: pass
    return root

def clone_node_transform(
    node, parent, *,
    bx, by, bw, bh,
    target_x, target_y,
    sx=1.0, sy=1.0,
    rot_deg=0.0,
    mir_h=False,    # '|' espejo vertical (scaleY -1)
    mir_v=False,    # '||' espejo horizontal (scaleX -1)
    anchor=(0.5, 0.5),
    insert_after=None,
    set_id=None,
):
    from inkex.transforms import Transform
    node_id = node.get_id() if hasattr(node,"get_id") else node.get("id")
    if not node_id:
        try:
            svgdoc = node.getroottree().getroot()
            node_id = (svgdoc.get_unique_id("item") if hasattr(svgdoc,"get_unique_id") else "item")
        except Exception: node_id = "item"
        node.set("id", node_id)

    ax,ay = anchor
    ax_local = ax * bw; ay_local = ay * bh
    sx_m = (-1.0 if mir_v else 1.0) * sx
    sy_m = (-1.0 if mir_h else 1.0) * sy

    T = Transform()
    T = T @ Transform(f"translate({target_x},{target_y})")
    if (rot_deg % 360) != 0: T = T @ Transform(f"rotate({rot_deg})")
    if (sx_m != 1.0) or (sy_m != 1.0): T = T @ Transform(f"scale({sx_m},{sy_m})")
    T = T @ Transform(f"translate({-ax_local - bx},{-ay_local - by})")

    use = etree.Element(inkex.addNS('use','svg'))
    set_href(use, f"#{node_id}", touch_plain=True)

    use.set('transform', str(T))
    if set_id: use.set("id", set_id)

    if insert_after is not None:
        try:
            idx = list(parent).index(insert_after) + 1
            parent.insert(idx, use)
        except ValueError:
            parent.append(use)
    else:
        parent.append(use)
    return use

def _resolve_image_path(href: str|None, absref: str|None, svg_real_path: str|None) -> Path|None:
    if not href and not absref: return None
    if href and href.startswith("data:"): return None
    if absref and Path(absref).is_file(): return Path(absref).resolve()
    if href and href.lower().startswith("file:"):
        try:
            p = urlparse(href); path = Path(unquote(p.path or ""))
            if os.name=="nt" and str(path).startswith("/") and len(str(path))>3 and str(path)[2]==":":
                path = Path(str(path)[1:])
            if path.is_file(): return path.resolve()
        except Exception: pass
    if href:
        p = Path(unquote(href))
        if p.is_absolute() and p.is_file(): return p.resolve()
    rel = Path(unquote(href)) if href else None
    bases = []
    if svg_real_path: bases.append(Path(svg_real_path).parent)
    bases.append(Path.cwd())
    for b in bases:
        if rel:
            guess = (b/rel).resolve()
            if guess.is_file(): return guess
    return None

def absolutize_all_linked_images(svgdoc, svg_real_path: str|None, *,
                                 prefer: str|None = None,
                                 touch_href: bool = True,
                                 clear_xml_base: bool = True) -> int:
    if prefer is None: prefer = 'ospath' if os.name=='nt' else 'fileuri'
    root = svgdoc.getroot() if hasattr(svgdoc,"getroot") else svgdoc
    if clear_xml_base and root.get(CONST.XML_BASE) is not None: del root.attrib[CONST.XML_BASE]
    images = root.xpath(".//svg:image", namespaces=inkex.NSS)
    fixed = 0
    for im in images:
        href0   = get_href(im)
        absref0 = im.get(SODI_ABSREF) or ""
        abs_path = _resolve_image_path(href0, absref0, svg_real_path)
        if not abs_path: continue
        new_href = abs_path.as_uri() if prefer=="fileuri" else str(abs_path)
        if href0 == new_href and absref0 == str(abs_path): continue
        set_href(im, new_href, touch_plain=touch_href)

        im.set(SODI_ABSREF, str(abs_path))
        fixed += 1
    return fixed

# ========= API unificada (tipo y bbox visual) ================================

_SHAPE_INSIDE_RE = re.compile(r"shape-inside\s*:\s*url\(\s*#([^)]+)\s*\)", re.I)

def node_kind(node) -> str:
    if node is None or not hasattr(node, "tag"): return "other"
    tag = node.tag or ""
    if tag.endswith("text") or tag.endswith("flowRoot"):
        st = (node.get('style') or '')
        try:
            st_inline = ' '.join(f"{k}:{v}" for k,v in getattr(node,'style',{}).items())
            if st_inline: st = f"{st} {st_inline}"
        except Exception: pass
        if _SHAPE_INSIDE_RE.search(st or ""): return "text-shape"
        if tag.endswith("flowRoot"): return "text-shape"
        return "text"
    if tag.endswith("image"):
        href = get_href(node)
        if (href or "").strip().lower().startswith("data:"): return "image-embedded"
        return "image-linked"
    if tag.endswith("g"): return "group"
    if tag.endswith("use"): return "use"
    if tag.endswith("symbol"): return "symbol"
    for b in ("rect","path","circle","ellipse","line","polyline","polygon"):
        if tag.endswith(b): return b
    return "other"

def _apply_point_xy__safe(T: inkex.Transform, x: float, y: float):
    pt = T.apply_to_point((x,y))
    try: return float(pt.x), float(pt.y)
    except Exception: return float(pt[0]), float(pt[1])

def _flowed_text_outer_box_from_shape_rect(node):
    root = node.getroottree().getroot()
    st = (node.get('style') or '')
    try:
        st_inline = ' '.join(f"{k}:{v}" for k,v in getattr(node,'style',{}).items())
        if st_inline: st = f"{st} {st_inline}"
    except Exception: pass

    rect_el = None
    m = _SHAPE_INSIDE_RE.search(st or "")
    if m:
        rid = m.group(1)
        rect_el = find_id(root, rid, include_defs=True)
        if rect_el is not None and (not hasattr(rect_el,'tag') or not str(rect_el.tag).endswith('rect')):
            rect_el = None
    if rect_el is None and str(node.tag).endswith("flowRoot"):
        try:
            reg = node.xpath(".//svg:flowRegion", namespaces=NSS)
            if reg:
                rs = reg[0].xpath(".//svg:rect", namespaces=NSS)
                if rs: rect_el = rs[0]
        except Exception: pass
    if rect_el is None: return None

    try:
        x = float(rect_el.get("x") or 0.0); y = float(rect_el.get("y") or 0.0)
        w = float(rect_el.get("width") or 0.0); h = float(rect_el.get("height") or 0.0)
    except Exception: return None

    try: T = node.composed_transform()
    except Exception: T = inkex.Transform(node.get("transform") or "")

    pts = [(x,y),(x+w,y),(x,y+h),(x+w,y+h)]
    tpts = [_apply_point_xy__safe(T, px,py) for (px,py) in pts]
    xs = [p[0] for p in tpts]; ys = [p[1] for p in tpts]
    return (float(min(xs)), float(min(ys)), float(max(xs)-min(xs)), float(max(ys)-min(ys)))




def visual_bbox(node):
    # If provided, data-bbox overrides computed bbox (used by array groups).
    try:
        bb_attr = (node.get('data-bbox') or '').strip()
        if bb_attr:
            parts = [p for p in re.split(r"[ ,]+", bb_attr) if p]
            if len(parts) == 4:
                bx, by, bw, bh = [float(p) for p in parts]
                return (bx, by, bw, bh)
    except Exception:
        pass
    # Caso especial: flowed text / text-shape
    if node_kind(node) == "text-shape":
        bb = _flowed_text_outer_box_from_shape_rect(node)
        if bb:
            return bb
    # Fallback general (inkex bbox can be very slow / unstable on malformed SVG)
    try:
        b = node.bounding_box()
        bx, by, bw, bh = float(b.left), float(b.top), float(b.width), float(b.height)
        return bx, by, bw, bh
    except Exception:
        # Best-effort geometric fallback for common primitives.
        try:
            ln = node_kind(node)
        except Exception:
            ln = ''

        try:
            # Rect-like
            if ln in ('rect', 'image', 'foreignObject') or getattr(node, 'tag', '').endswith('rect'):
                x = float(node.get('x') or 0)
                y = float(node.get('y') or 0)
                w = float(node.get('width') or 0)
                h = float(node.get('height') or 0)
                return (x, y, w, h)

            # Circle / ellipse
            if ln in ('circle', 'ellipse'):
                cx = float(node.get('cx') or 0)
                cy = float(node.get('cy') or 0)
                if ln == 'circle':
                    r = float(node.get('r') or 0)
                    return (cx - r, cy - r, 2*r, 2*r)
                rx = float(node.get('rx') or 0)
                ry = float(node.get('ry') or 0)
                return (cx - rx, cy - ry, 2*rx, 2*ry)

            # Line
            if ln == 'line':
                x1 = float(node.get('x1') or 0)
                y1 = float(node.get('y1') or 0)
                x2 = float(node.get('x2') or 0)
                y2 = float(node.get('y2') or 0)
                x = min(x1, x2); y = min(y1, y2)
                return (x, y, abs(x2-x1), abs(y2-y1))

            # Path: use endpoints-only bbox (fast, approximate)
            if ln == 'path':
                d = node.get('d')
                pts = path_characteristic_points(d, node.get('transform'))
                if pts:
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    x0, y0 = min(xs), min(ys)
                    return (x0, y0, max(xs)-x0, max(ys)-y0)
        except Exception:
            pass

        # Give up
        return (0.0, 0.0, 0.0, 0.0)

from inkex.paths import Path as SvgPath

# -----------------------------------------------------------------------------
# Geometry helpers (MVP for smart hex kerf)
# -----------------------------------------------------------------------------

_DM_NUM_RE = re.compile(r"[-+]?(?:(?:\d+\.\d+)|(?:\d+\.)|(?:\.\d+)|(?:\d+))(?:[eE][-+]?\d+)?")

def path_characteristic_points(d: str, transform=None):
    """Return a list of characteristic points for an SVG path.

    Rules (MVP):
      - If `d` contains curves, we use curve endpoints (not control points).
      - The input may have shorthand/relative commands; we try to normalize it
        using inkex Path, then parse.
      - `transform` may be:
          - None
          - an inkex.Transform
          - a transform string
    """
    if not d:
        return []

    # Normalize to absolute, no-shorthand when possible.
    try:
        nd = str(SvgPath(d).to_non_shorthand().to_absolute())
    except Exception:
        nd = str(d)

    # Tokenize into commands and numbers.
    toks = re.findall(r"[AaCcHhLlMmQqSsTtVvZz]|" + _DM_NUM_RE.pattern, nd)
    if not toks:
        return []

    # Prepare transform
    T = None
    try:
        if transform is None:
            T = None
        elif hasattr(transform, 'apply_to_point'):
            T = transform
        else:
            T = inkex.Transform(str(transform))
    except Exception:
        T = None

    def _apply(pt):
        if T is None:
            return (float(pt[0]), float(pt[1]))
        try:
            return _apply_point_xy__safe(T, float(pt[0]), float(pt[1]))
        except Exception:
            return (float(pt[0]), float(pt[1]))

    # Command param counts
    pc = {
        'M': 2, 'L': 2, 'T': 2,
        'H': 1, 'V': 1,
        'S': 4, 'Q': 4,
        'C': 6,
        'A': 7,
        'Z': 0,
    }

    pts = []
    i = 0
    cmd = None
    cx = cy = 0.0
    sx = sy = 0.0

    def _is_cmd(t):
        return len(t) == 1 and t.isalpha()

    while i < len(toks):
        t = toks[i]
        if _is_cmd(t):
            cmd = t.upper()
            i += 1
            if cmd == 'Z':
                # closepath: go back to subpath start
                cx, cy = sx, sy
                pts.append(_apply((cx, cy)))
                continue
        if cmd is None:
            i += 1
            continue

        need = pc.get(cmd, 0)
        if need == 0:
            continue

        # Collect as many full param sets as available for this command.
        while True:
            if i + need > len(toks):
                break
            chunk = toks[i:i+need]
            if any(_is_cmd(x) for x in chunk):
                break
            i += need

            # Convert numbers
            try:
                nums = [float(x) for x in chunk]
            except Exception:
                continue

            if cmd == 'M':
                cx, cy = nums[0], nums[1]
                sx, sy = cx, cy
                pts.append(_apply((cx, cy)))
                # Subsequent pairs after M are treated as L.
                cmd = 'L'
                need = pc['L']
                continue
            if cmd == 'L':
                cx, cy = nums[0], nums[1]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'H':
                cx = nums[0]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'V':
                cy = nums[0]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'C':
                cx, cy = nums[4], nums[5]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'S':
                cx, cy = nums[2], nums[3]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'Q':
                cx, cy = nums[2], nums[3]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'T':
                cx, cy = nums[0], nums[1]
                pts.append(_apply((cx, cy)))
                continue
            if cmd == 'A':
                cx, cy = nums[5], nums[6]
                pts.append(_apply((cx, cy)))
                continue

        # Next token likely a command.
        continue

    # Remove consecutive duplicates and a trailing point equal to the first.
    out = []
    for p in pts:
        if not out or (abs(p[0]-out[-1][0]) > 1e-9 or abs(p[1]-out[-1][1]) > 1e-9):
            out.append(p)
    if len(out) >= 2 and abs(out[0][0]-out[-1][0]) <= 1e-9 and abs(out[0][1]-out[-1][1]) <= 1e-9:
        out.pop()
    return out


def base_angle_deg(points):
    """Compute the angle (deg) of the bottom base of a closed contour.

    Definition:
      - Find point(s) with maximum Y.
      - If there are two or more, base is the segment between the leftmost and rightmost among them.
      - If there is only one, base is the segment between its two neighbors (circular indexing).
    """
    if not points:
        return None
    n = len(points)
    if n < 3:
        return None

    ys = [p[1] for p in points]
    y_max = max(ys)
    eps = 1e-6 * (abs(y_max) + 1.0)
    idxs = [i for i, y in enumerate(ys) if abs(y - y_max) <= eps]
    if not idxs:
        return None

    if len(idxs) >= 2:
        pts = [points[i] for i in idxs]
        left = min(pts, key=lambda p: p[0])
        right = max(pts, key=lambda p: p[0])
        x1, y1 = left
        x2, y2 = right
    else:
        i = idxs[0]
        p_prev = points[(i - 1) % n]
        p_next = points[(i + 1) % n]
        x1, y1 = p_prev
        x2, y2 = p_next

    dx = (x2 - x1)
    dy = (y2 - y1)
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return 0.0
    ang = math.degrees(math.atan2(dy, dx))
    # Normalize to [-180,180]
    while ang > 180.0:
        ang -= 360.0
    while ang <= -180.0:
        ang += 360.0
    return ang

def _normalize_path_d(d: str) -> str:
    """
    Versión 'inkscape-like':
    - no-shorthand
    - absoluta
    """
    if not d:
        return d
    try:
        p = SvgPath(d)
        p = p.to_non_shorthand().to_absolute()
        return str(p)
    except Exception as e:
        try:
            _l.w(f"[svg._normalize_path_d] fallo normalizando path: {e}")
        except Exception:
            pass
        return d


def _normalize_paths_in_subtree(node) -> int:
    count = 0
    try:
        it = node.iter()
    except Exception:
        return 0

    for el in it:
        tag = getattr(el, "tag", "") or ""
        if tag.endswith("path"):
            d = el.get("d")
            if not d:
                continue
            newd = _normalize_path_d(d)
            if newd != d:
                el.set("d", newd)
            count += 1
    return count

def fix_all_paths(node):
    _l.d("[fix_all_paths] init")
    try: count = _normalize_paths_in_subtree(node)
    except Exception: count = -1 
    _l.d("[fix_all_paths] end")
    return count

# ========= Generic Helpers (moved from anchor_fit) ===========================

def ensure_id(svgdoc, node, prefix="item"):
    nid = node.get("id")
    if not nid:
        try: nid = svgdoc.get_unique_id(prefix)
        except Exception: nid = prefix
        node.set("id", nid)
    return nid

def keypad_to_anchor(a:int):
    return {7:(0,0),8:(0.5,0),9:(1,0),
            4:(0,0.5),5:(0.5,0.5),6:(1,0.5),
            1:(0,1),2:(0.5,1),3:(1,1)}.get(a,(0.5,0.5))

def compute_fit_scale(bw,bh,iw,ih,mode:str):
    """Devuelve (sx, sy) según el modo de fit canónico.

    bw, bh = ancho/alto del bbox origen
    iw, ih = ancho/alto del rectángulo destino
    mode   = 'i','o','w','h','m','x','y','a','t','b'
    """
    mode = (mode or "n").lower()
    sx = sy = 1.0

    # if there is no bbox, we cannot scale meaningfully
    if not bw or not bh:
        return sx, sy

    # base scales to fit the target rect
    sw = iw / bw if bw else 1.0
    sh = ih / bh if bh else 1.0

    # all-stretch: rellena exacto, deformando X e Y
    if mode == "a":
        sx, sy = sw, sh
    # stretch only X / only Y (non-proportional)
    elif mode == "x":
        sx, sy = sw, 1.0
    elif mode == "y":
        sx, sy = 1.0, sh
    # modos proporcionales
    elif mode == "i":        # inside / contain
        s = min(sw, sh)
        sx = sy = s
    elif mode == "m":        # max / cover (el que te interesa)
        s = max(sw, sh)
        sx = sy = s
    elif mode == "w":        # fit width
        sx = sy = sw
    elif mode == "h":        # fit height
        sx = sy = sh
    # 'o','n','t','b' or others -> sx = sy = 1.0 (no scale)
    return sx, sy

# ========= Placement API (additive, with logs and fixes) =====================

def build_fit_transform(*,
                        bx, by, bw, bh,
                        target_x, target_y,
                        sx=1.0, sy=1.0, rot_deg=0.0,
                        mir_h=False, mir_v=False,
                        anchor=(0.5, 0.5)):
    """Construye un transform local (coords del parent) que:

    1) Aplica mirror/scale/rotate alrededor del *centro* del bbox del elemento.
    2) Recalcula el bbox resultante (axis-aligned) tras esa transformación.
    3) Traslada el resultado para que el punto de ancla (según `anchor`)
       del bbox transformado caiga en (target_x, target_y).

    Esto implementa el comportamiento lógico 'rotar primero y luego anclar'
    (opción A), evitando desplazamientos aparentes al rotar.
    """
    ax, ay = anchor

    # Center of base bbox in parent local coords
    cx = bx + bw * 0.5
    cy = by + bh * 0.5

    # Linear matrix (no translation) + rotation around origin
    L = inkex.Transform()
    if mir_h:
        L = L @ inkex.Transform(f"scale(-1,1)")
    if mir_v:
        L = L @ inkex.Transform(f"scale(1,-1)")
    if sx != 1.0 or sy != 1.0:
        L = L @ inkex.Transform(f"scale({sx},{sy})")
    if rot_deg:
        L = L @ inkex.Transform(f"rotate({rot_deg})")

    # Rotation/scale around center: Tc ∘ L ∘ T(-c)
    T_center = inkex.Transform(f"translate({cx},{cy})") @ L @ inkex.Transform(f"translate({-cx},{-cy})")

    # Transform the 4 corners to get the resulting bbox
    pts = [
        (bx, by),
        (bx + bw, by),
        (bx, by + bh),
        (bx + bw, by + bh),
    ]
    tpts = [T_center.apply_to_point(p) for p in pts]
    xs = [p[0] for p in tpts]
    ys = [p[1] for p in tpts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    # Anchor point of the *transformed* bbox
    anchor_tx = minx + ax * (maxx - minx)
    anchor_ty = miny + ay * (maxy - miny)

    # Final translation to move the anchor to the target
    dx = target_x - anchor_tx
    dy = target_y - anchor_ty

    return inkex.Transform(f"translate({dx},{dy})") @ T_center
def clone_as_use(base, parent, T: inkex.Transform, *, insert_after=None, set_id=None):
    try:
        use = inkex.Use.new()
        use.href = base
        try: _l.d("[svg.clone_as_use] creado con inkex.Use.new()")
        except Exception: pass
    except Exception:
        use = etree.Element(inkex.addNS('use','svg'))
        set_href(use,  f"#{base.get_id() if hasattr(base,'get_id') else base.get('id')}", touch_plain=True)

        try: _l.d("[svg.clone_as_use] fallback: <use> crudo")
        except Exception: pass


    # FIX (Iconify / <symbol>): Inkscape needs width/height on <use> to set the viewport.
    # If not provided, the symbol can render with a huge "default" size and the fit-anchor
    # it does not scale as expected.
    try:
        tag = getattr(base, 'tag', '') or ''
        if tag.endswith('symbol'):
            # Only if they are not already set.
            if not use.get('width') and not use.get('height'):
                vb = (base.get('viewBox') or '').strip()
                if vb:
                    parts = [p for p in re.split(r"[ ,]+", vb) if p]
                    if len(parts) == 4:
                        w = float(parts[2]); h = float(parts[3])
                        if w > 0 and h > 0:
                            use.set('width', f"{w:.6f}")
                            use.set('height', f"{h:.6f}")
                            # Reasonable default alignment (does not change units).
                            if not use.get('preserveAspectRatio'):
                                use.set('preserveAspectRatio', 'xMidYMid meet')
    except Exception:
        # We never break the pipeline for this; it only improves the <symbol> case.
        pass

    use.set('transform', str(T))
    if set_id:
        use.set('id', set_id)
    if insert_after is not None and insert_after.getparent() is parent:
        idx = parent.index(insert_after)
        parent.insert(idx+1, use)
    else:
        parent.append(use)
    return use


def unlink_use(use_el):
    try:
        if hasattr(use_el, "unlink") and callable(use_el.unlink):
            new_node = use_el.unlink()
            try: _l.d("[svg.unlink_use] inkex.Use.unlink() ejecutado")
            except Exception: pass
            if new_node is not None:
                return new_node
    except Exception as e:
        try: _l.w(f"[svg.unlink_use] unlink nativo falló: {e}")
        except Exception: pass

    try:
        ref = getattr(use_el, 'href', None)
    except Exception:
        ref = None
    if ref is None:
        href = get_href(use_el)
        if href and href.startswith('#'):
            ref_id = href[1:]
            root = use_el.getroottree().getroot()
            ref = find_id(root, ref_id, include_defs=True)

    if ref is None:
        try: _l.w("[svg.unlink_use] no se pudo resolver href del <use>; devolviendo tal cual")
        except Exception: pass
        return use_el

    try:
        dup  = ref.copy()
        baseT = inkex.Transform(ref.get('transform') or "")
        useT  = inkex.Transform(use_el.get('transform') or "")
        dup.set('transform', str(useT @ baseT))  # ORDEN CORRECTO
        p = use_el.getparent()
        idx = p.index(use_el)
        p.remove(use_el)
        p.insert(idx, dup)
        try: _l.d("[svg.unlink_use] fallback manual OK (useT@baseT)")
        except Exception: pass
        return dup
    except Exception as e:
        try: _l.e(f"[svg.unlink_use] fallback manual falló: {e}")
        except Exception: pass
        return use_el

def deepcopy_place(base, parent, T: inkex.Transform, *, insert_after=None, id_prefix="af"):
    """Deep-copy exacta del comportamiento de <use>: crear <use> con T y materializar."""
    use = clone_as_use(base, parent, T, insert_after=insert_after, set_id=None)
    dup = unlink_use(use)
    try:
        dup.set_random_ids(prefix=id_prefix)
    except Exception:
        pass
    return dup

def place_node(base, parent, *,
               bx, by, bw, bh,
               target_x, target_y,
               sx=1.0, sy=1.0, rot_deg=0.0,
               mir_h=False, mir_v=False,
               anchor=(0.5,0.5),
               insert_after=None,
               mode="use",           # "use" | "deep" | "use+unlink"
               id_prefix="af",
               set_id=None):
    """
    Coloca `base` dentro de `parent` usando coordenadas en ESPACIO DOCUMENTO.

    Parámetros:
      - bx,by,bw,bh: bbox del base en coords de documento (visual_bbox(base)).
      - target_x,target_y: punto DESTINO del ancla, también en coords de documento.
      - anchor: (ax,ay) relativo al bbox (0..1 en cada eje).
      - sx,sy,rot_deg,mir_h,mir_v: escala/rot/espajos a aplicar.

    Internamente:
      - Convierte anchor y destino a coords locales del parent usando el CTM.
      - Construye una transform local con build_fit_transform.
      - Inserta el nodo según `mode`.
    """
    ax, ay = anchor

    # 1) Current base anchor point IN DOCUMENT
    anchor_world_x = bx + ax * bw
    anchor_world_y = by + ay * bh

    # 2) Parent CTM (document <- parent)
    try:
        parent_ctm = parent.composed_transform()
    except Exception:
        parent_ctm = inkex.Transform()

    # 3) Inversa para pasar de documento → local del parent
    try:
        inv_parent = parent_ctm.inverse()
    except Exception:
        inv_parent = inkex.Transform()

    # 4) Move points and bbox to parent local coords
    target_local_x, target_local_y = inv_parent.apply_to_point((target_x, target_y))
    # base bbox (document) -> axis-aligned bbox in parent local coords
    p1 = inv_parent.apply_to_point((bx, by))
    p2 = inv_parent.apply_to_point((bx + bw, by))
    p3 = inv_parent.apply_to_point((bx, by + bh))
    p4 = inv_parent.apply_to_point((bx + bw, by + bh))
    xs = [p1[0], p2[0], p3[0], p4[0]]
    ys = [p1[1], p2[1], p3[1], p4[1]]
    bx_l, by_l = min(xs), min(ys)
    bw_l, bh_l = max(xs) - bx_l, max(ys) - by_l

    # 5) Local transform: rotate/scale first (around center), then anchor
    T = build_fit_transform(
        bx=bx_l, by=by_l, bw=bw_l, bh=bh_l,
        target_x=target_local_x,
        target_y=target_local_y,
        sx=sx,
        sy=sy,
        rot_deg=rot_deg,
        mir_h=mir_h,
        mir_v=mir_v,
        anchor=anchor,
    )

    # 6) Cloning / actual placement
    if mode == "use":
        return clone_as_use(base, parent, T, insert_after=insert_after, set_id=set_id)
    elif mode == "use+unlink":
        u = clone_as_use(base, parent, T, insert_after=insert_after, set_id=set_id)
        return unlink_use(u)
    else:
        return deepcopy_place(base, parent, T, insert_after=insert_after, id_prefix=id_prefix)

# ========= Rect/Anchor helpers (new, generic) ===============================

def rect_with_pad(x: float, y: float, w: float, h: float, pad) -> tuple[float,float,float,float]:
    """Return a rect expanded/shrunk by pad.
    pad: single number (applies to all) or (top,right,bottom,left)
    Positive expands; negative shrinks.
    """
    try:
        if isinstance(pad, (list,tuple)):
            if len(pad)==2:
                t=r=b=l=float(pad[0]), float(pad[1]), float(pad[0]), float(pad[1])
            elif len(pad)==4:
                t,r,b,l = (float(pad[0]), float(pad[1]), float(pad[2]), float(pad[3]))
            else:
                t=r=b=l=float(pad[0]) if len(pad)>0 else 0.0
        else:
            t=r=b=l=float(pad or 0.0)
    except Exception:
        t=r=b=l=0.0
    return (x - l, y - t, max(0.0, w + l + r), max(0.0, h + t + b))

def anchor_point_in_rect(x: float, y: float, w: float, h: float, ax: float, ay: float) -> tuple[float,float]:
    """Map anchor (ax,ay in 0..1) to absolute point in rect."""
    return (x + ax * w, y + ay * h)

def transform_bbox_to_rect(*, bx, by, bw, bh, dst_x, dst_y, dst_w, dst_h,
                           fit: str = "i", anchor=(0.5,0.5), shift=(0.0,0.0),
                           rot_deg: float = 0.0, mir_h: bool=False, mir_v: bool=False) -> inkex.Transform:
    """Build a transform that fits a bbox (bx,by,bw,bh) into a destination rect.
    fit: 'n' (none), 'i' (inside/contain), 'c' (cover), 'w' (fit width), 'h' (fit height),
         'a' (auto=min), 'x' (stretch x), 'y' (stretch y), 'a' (all (x+y) stretch)
    anchor: (ax,ay) in 0..1 relative to src bbox and dst rect simultaneously.
    shift: pixels added after anchoring, in destination space.
    """
    mode = (fit or 'n').lower()
    if mode == 'n':  # original / none
        sx = sy = 1.0
    else:
        sx, sy = compute_fit_scale(bw, bh, dst_w, dst_h, mode)

    ax, ay = anchor
    # anchor point in destination rect
    tx, ty = anchor_point_in_rect(dst_x, dst_y, dst_w, dst_h, ax, ay)
    try:
        dx = float(shift[0] if isinstance(shift, (list,tuple)) and len(shift)>0 else (shift or 0))
        dy = float(shift[1] if isinstance(shift, (list,tuple)) and len(shift)>1 else 0)
    except Exception:
        dx = dy = 0.0
    tx += dx; ty += dy
    return build_fit_transform(
        bx=bx, by=by, bw=bw, bh=bh,
        target_x=tx, target_y=ty,
        sx=sx, sy=sy,
        rot_deg=rot_deg,
        mir_h=mir_h, mir_v=mir_v,
        anchor=anchor,
    )

# ========= Export ============================================================

# ========= Unique ID helpers for deepcopies (minimal, no ref rewrites) ======

_PNP_SUFFIX_RX = re.compile(r"^(?P<base>.+?)_pnp(?P<num>\d+)$")

def strip_pnp_suffix(id_str: str) -> str:
    """Remove trailing _pnp{n} once, if present."""
    if not id_str:
        return id_str
    m = _PNP_SUFFIX_RX.match(id_str)
    return m.group('base') if m else id_str

def scan_max_pnp_suffix(svg_root) -> int:
    """Scan whole document for ids ending with _pnp{n} and return max n (0 if none)."""
    maxn = 0
    try:
        nodes = svg_root.xpath('.//*[@id]')
    except Exception:
        nodes = []
    for el in nodes:
        _id = el.get('id')
        if not _id:
            continue
        m = _PNP_SUFFIX_RX.match(_id)
        if m:
            try:
                n = int(m.group('num'))
                if n > maxn:
                    maxn = n
            except Exception:
                pass
    return maxn

def uniquify_all_ids_in_scope(scope, suffix: str, get_unique_id):
    """
    Minimal, safe uniquifier:
      - For every element WITH @id in 'scope' subtree:
         * If no 'data-origid', set data-origid = strip_pnp_suffix(current_id).
         * Compute new_id = strip_pnp_suffix(current_id) + suffix,
           then pass through get_unique_id(new_id) to ensure global uniqueness.
         * Set element @id = that unique id.
      - DOES NOT touch any references.
    """
    if scope is None:
        return
    for el in scope.iter():
        if not hasattr(el, 'tag') or not isinstance(el.tag, str):
            continue
        cur = el.get('id')
        if not cur:
            continue
        base = strip_pnp_suffix(cur)
        if el.get('data-origid') is None:
            el.set('data-origid', base)
        proposed = f"{base}{suffix}"
        try:
            unique = get_unique_id(proposed)
        except Exception:
            unique = proposed
        if unique != cur:
            el.set('id', unique)

def common_group_ancestor(nodes):
    """
    Devuelve el ancestro común de menor profundidad que sea <g>.
    Si no hay común, devuelve el primer ancestro <g> del primer nodo.
    Si tampoco existe, None.
    """
    def ancestors_inclusive(n):
        cur = n
        chain = []
        while cur is not None:
            chain.append(cur)
            try:
                cur = cur.getparent()
            except Exception:
                cur = None
        return chain

    nodes = [n for n in nodes if n is not None]
    if not nodes:
        return None

    # Ancestor chain (including the node itself) of the first
    base_chain = ancestors_inclusive(nodes[0])

    # Keep those that are groups
    base_groups = [a for a in base_chain if hasattr(a, "tag") and isinstance(a.tag, str) and a.tag.endswith("g")]
    if not base_groups:
        # fallback: any ancestor of the first that is a <g>
        return next((a for a in base_chain if hasattr(a, "tag") and isinstance(a.tag, str) and a.tag.endswith("g")), None)

    # For each candidate group (from closest to farthest), check that it contains all
    for g in base_groups:
        ok = True
        for n in nodes:
            cur = n
            found = False
            while cur is not None:
                if cur is g:
                    found = True
                    break
                try:
                    cur = cur.getparent()
                except Exception:
                    cur = None
            if not found:
                ok = False
                break
        if ok:
            return g

    # If no common, return the first <g> of the first node as a last fallback
    return base_groups[0]

__all__ = [
    "__version__",
    "NSS",
    "SVG_NS","XLINK_NS","INKSCAPE_NS","SODIPODI_NS","XML_NS","NS",
    "DPI","PX_PER_IN","IN_PER_PX","PX_PER_MM","MM_PER_PX","PX_PER_CM","CM_PER_PX",
    "PAGE_ATTR_KEYS","DEFAULT_PAGE_ATTRS","DEFAULT_PAGE_APPEND_GAP_PX","LAYER_LABELS",
    "parse_len_px","namedview","page_size_px","add_inkscape_page_mm","list_existing_pages_px",
    "rightmost_page","next_dm_page_id","ensure_page_for","find_or_create_layer",
    "apply_translation","composed_transform","pick_anchor_in",
    "clear_children","is_text_like","replace_text","replace_xml",
    "style_map","style_set","BBox","query_all",
    "ensure_xlink_ns","clone_node_transform","absolutize_all_linked_images",
    "node_kind","visual_bbox",
    "ensure_id","keypad_to_anchor","compute_fit_scale",
    "build_fit_transform","clone_as_use","unlink_use","deepcopy_place","place_node",
    "apply_clip_from_rect",
    "rect_with_pad","anchor_point_in_rect","transform_bbox_to_rect",
    "strip_pnp_suffix","scan_max_pnp_suffix","uniquify_all_ids_in_scope",
    "common_group_ancestor", "fix_all_paths",
]
