# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Dict, List, Optional, Tuple

import inkex
import log as LOG
import svg as SVG

_l = LOG


@dataclass
class GradientStopSpec:
    pos: float
    left: float
    right: float
    light: Optional[float] = None
    color: Optional[Tuple[int, int, int, int]] = None


@dataclass
class GradientSpec:
    name: str
    base: Tuple[int, int, int, int]
    angle: float
    stops: List[GradientStopSpec]


_GRAD_DEF_RE = re.compile(
    r"^\s*(?:#\s*)?(?P<name>[A-Za-z_][-A-Za-z0-9_:.]*)\s*=\s*(?P<rhs>.+?)\s*$"
)


def _to_percent(v: str) -> float:
    s = str(v or "").strip()
    if s.endswith("%"):
        s = s[:-1].strip()
    return float(s)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _parse_hex_color(token: str) -> Optional[Tuple[int, int, int, int]]:
    s = str(token or "").strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) not in (3, 4, 6, 8):
        return None
    if not re.fullmatch(r"[0-9A-Fa-f]+", s):
        return None
    if len(s) == 3:
        s = "".join([c * 2 for c in s]) + "FF"
    elif len(s) == 4:
        s = "".join([c * 2 for c in s])
    elif len(s) == 6:
        s = s + "FF"
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    a = int(s[6:8], 16)
    return (r, g, b, a)


def _mix_to_white_black(base: Tuple[int, int, int, int], light: float) -> Tuple[int, int, int, int]:
    r, g, b, a = base
    t = _clamp(abs(float(light)) / 100.0, 0.0, 1.0)
    if light >= 0:
        rr = int(round(r + (255 - r) * t))
        gg = int(round(g + (255 - g) * t))
        bb = int(round(b + (255 - b) * t))
    else:
        rr = int(round(r * (1.0 - t)))
        gg = int(round(g * (1.0 - t)))
        bb = int(round(b * (1.0 - t)))
    return (rr, gg, bb, a)


def _split_top_level(s: str) -> List[str]:
    out: List[str] = []
    cur: List[str] = []
    depth = 0
    for ch in str(s or ""):
        if ch == "[":
            depth += 1
            cur.append(ch)
            continue
        if ch == "]":
            depth = max(0, depth - 1)
            cur.append(ch)
            continue
        if ch.isspace() and depth == 0:
            tok = "".join(cur).strip()
            if tok:
                out.append(tok)
            cur = []
            continue
        cur.append(ch)
    tok = "".join(cur).strip()
    if tok:
        out.append(tok)
    return out


def _extract_balanced_block(text: str, start_ch: str, end_ch: str, start_at: int = 0) -> Tuple[Optional[str], int]:
    s = str(text or "")
    i0 = s.find(start_ch, int(start_at or 0))
    if i0 < 0:
        return None, -1
    d = 0
    for i in range(i0, len(s)):
        ch = s[i]
        if ch == start_ch:
            d += 1
        elif ch == end_ch:
            d -= 1
            if d == 0:
                return s[i0:i + 1], i + 1
    return None, -1


def _parse_stop_group(group_text: str, default_width: float = 10.0) -> Optional[GradientStopSpec]:
    g = str(group_text or "").strip()
    if g.startswith("[") and g.endswith("]"):
        g = g[1:-1].strip()
    if not g:
        return None
    toks = [t for t in re.split(r"[\s,]+", g) if t]
    if len(toks) < 2:
        return None

    c0 = _parse_hex_color(toks[0])
    if c0 is not None:
        peak_color = c0
        pos = _to_percent(toks[1])
        a = _to_percent(toks[2]) if len(toks) >= 3 else float(default_width)
        b = _to_percent(toks[3]) if len(toks) >= 4 else float(a)
        return GradientStopSpec(pos=pos, left=float(a), right=float(b), color=peak_color)

    light = float(_to_percent(toks[0]))
    pos = _to_percent(toks[1])
    a = _to_percent(toks[2]) if len(toks) >= 3 else float(default_width)
    b = _to_percent(toks[3]) if len(toks) >= 4 else float(a)
    return GradientStopSpec(pos=pos, left=float(a), right=float(b), light=light)


def _parse_stops_groups(text: str) -> List[GradientStopSpec]:
    s = str(text or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    out: List[GradientStopSpec] = []
    i = 0
    while i < len(s):
        blk, i2 = _extract_balanced_block(s, "[", "]", i)
        if not blk:
            break
        sp = _parse_stop_group(blk)
        if sp is not None:
            out.append(sp)
        i = i2
    return out


def _parse_gradient_rhs(name: str, rhs: str) -> Optional[GradientSpec]:
    s = str(rhs or "").strip()
    m = re.match(r"^(?:Gradient|G)\s*(\{.*\})\s*$", s, re.IGNORECASE)
    if not m:
        return None
    body = (m.group(1) or "").strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1].strip()
    if not body:
        return None

    # Long form: basecolor=... rotate=... stops=[ [...], ... ]
    if "=" in body:
        m_base = re.search(r"\b(?:basecolor|base|color)\s*=\s*([#A-Za-z0-9]+)", body, re.IGNORECASE)
        if not m_base:
            return None
        base = _parse_hex_color(m_base.group(1))
        if base is None:
            return None
        m_ang = re.search(r"\b(?:rotate|angle|r)\s*=\s*([-+]?\d+(?:\.\d+)?)", body, re.IGNORECASE)
        angle = float(m_ang.group(1)) if m_ang else 0.0
        m_st = re.search(r"\bstops\s*=", body, re.IGNORECASE)
        if not m_st:
            return None
        blk, _ = _extract_balanced_block(body, "[", "]", m_st.end())
        if not blk:
            return None
        stops = _parse_stops_groups(blk)
        return GradientSpec(name=name, base=base, angle=angle, stops=stops)

    # Short form: base[^angle] [stop] [stop] ...
    toks = _split_top_level(body)
    if not toks:
        return None
    t0 = toks[0]
    angle = 0.0
    if "^" in t0:
        b0, _sep, a0 = t0.partition("^")
        t0 = b0.strip()
        try:
            angle = float(a0.strip())
        except Exception:
            angle = 0.0
    else:
        # Also accept angle as an explicit standalone token: '^90'
        if len(toks) > 1 and str(toks[1]).strip().startswith("^"):
            try:
                angle = float(str(toks[1]).strip()[1:].strip())
                toks = [toks[0]] + toks[2:]
            except Exception:
                angle = 0.0
    base = _parse_hex_color(t0)
    if base is None:
        return None
    stops: List[GradientStopSpec] = []
    for tok in toks[1:]:
        sp = _parse_stop_group(tok)
        if sp is not None:
            stops.append(sp)
    return GradientSpec(name=name, base=base, angle=angle, stops=stops)


def _rgba_to_svg(c: Tuple[int, int, int, int]) -> Tuple[str, Optional[str]]:
    r, g, b, a = c
    col = f"#{int(r):02X}{int(g):02X}{int(b):02X}"
    op = None if int(a) >= 255 else f"{(float(a) / 255.0):.4f}".rstrip("0").rstrip(".")
    return col, op


def _gradient_line_from_angle_deg(angle: float) -> Tuple[float, float, float, float]:
    rad = math.radians(float(angle or 0.0))
    dx = math.cos(rad)
    dy = math.sin(rad)
    x1 = 0.5 - 0.5 * dx
    y1 = 0.5 - 0.5 * dy
    x2 = 0.5 + 0.5 * dx
    y2 = 0.5 + 0.5 * dy
    return x1, y1, x2, y2


def _offset_to_str(pct: float) -> str:
    o = _clamp(float(pct), 0.0, 100.0) / 100.0
    return f"{o:.6f}".rstrip("0").rstrip(".")


def _build_points(spec: GradientSpec) -> List[Tuple[float, Tuple[int, int, int, int]]]:
    base = spec.base
    pts: List[Tuple[float, Tuple[int, int, int, int]]] = [(0.0, base), (100.0, base)]
    for st in (spec.stops or []):
        pos = float(st.pos)
        lft = float(st.left)
        rgt = float(st.right)
        if st.color is not None:
            peak = st.color
        else:
            peak = _mix_to_white_black(base, float(st.light or 0.0))
        pts.append((pos - lft, base))
        pts.append((pos, peak))
        pts.append((pos + rgt, base))
    pts.sort(key=lambda x: x[0])
    dedup: Dict[str, Tuple[float, Tuple[int, int, int, int]]] = {}
    for p, c in pts:
        k = _offset_to_str(p)
        dedup[k] = (_clamp(p, 0.0, 100.0), c)
    return list(dedup.values())


def _upsert_linear_gradient(defs_parent, spec: GradientSpec):
    root = defs_parent.getroottree().getroot()
    gid = str(spec.name)
    try:
        old = root.xpath(f".//svg:linearGradient[@id='{gid}']", namespaces=SVG.NSS)
    except Exception:
        old = []
    for n in (old or []):
        p = n.getparent()
        if p is not None:
            try:
                p.remove(n)
            except Exception:
                pass

    grad = SVG.etree.SubElement(defs_parent, inkex.addNS("linearGradient", "svg"))
    grad.set("id", gid)
    grad.set("gradientUnits", "objectBoundingBox")
    x1, y1, x2, y2 = _gradient_line_from_angle_deg(spec.angle)
    grad.set("x1", f"{x1:.6f}".rstrip("0").rstrip("."))
    grad.set("y1", f"{y1:.6f}".rstrip("0").rstrip("."))
    grad.set("x2", f"{x2:.6f}".rstrip("0").rstrip("."))
    grad.set("y2", f"{y2:.6f}".rstrip("0").rstrip("."))

    for p, c in _build_points(spec):
        st = SVG.etree.SubElement(grad, inkex.addNS("stop", "svg"))
        col, op = _rgba_to_svg(c)
        st.set("offset", _offset_to_str(p))
        st.set("stop-color", col)
        if op is not None:
            st.set("stop-opacity", op)


def register_gradients_from_comments(comment_lines, defs_parent) -> Dict[str, GradientSpec]:
    """Scan comment directives and register linear gradients in <defs>.

    Supported directives:
      # gradientX=G{b88326ff^25 [-25 12 5] [#BA8726FF 35 20 10]}
      # gradientX=Gradient{basecolor=b88326ff rotate=25 stops=[[-25 12 5] [55 35 20 10]]}
    """
    out: Dict[str, GradientSpec] = {}
    if not comment_lines or defs_parent is None:
        return out
    for rr in (comment_lines or []):
        try:
            s0 = str(rr[0] if isinstance(rr, (list, tuple)) and rr else rr or "")
        except Exception:
            continue
        s = (s0 or "").strip()
        if not s:
            continue
        m = _GRAD_DEF_RE.match(s)
        if not m:
            continue
        name = m.group("name")
        rhs = m.group("rhs")
        spec = _parse_gradient_rhs(name, rhs)
        if spec is None:
            continue
        try:
            _upsert_linear_gradient(defs_parent, spec)
            out[name] = spec
            _l.d(f"[gradients] registered '{name}' angle={spec.angle}")
        except Exception as ex:
            _l.w(f"[gradients] register failed '{name}': {ex}")
    if out:
        _l.i(f"[gradients] defs={len(out)} -> {sorted(out.keys())}")
    else:
        _l.i("[gradients] no defs")
    return out


__all__ = ["GradientSpec", "GradientStopSpec", "register_gradients_from_comments"]
