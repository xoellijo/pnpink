#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
# iconify.py — CLEAN (A' preload) + robust SVG parsing + SSL fallback (no warnings)
#
# Contract:
# - Resolve icon://prefix/name to <defs><symbol id="prefix--name">...</symbol>
# - Everything else (render / fit_anchor / ~i) stays intact.
#
# Notes:
# - Does NOT use /collection?info=true.
# - Does NOT cache.
# - Downloads SVGs in parallel (A').
# - Robust SVG parser: etree.fromstring() of full document, then copies children.
# - SVG namespace is always correct.
# - If SSLError due to broken CA store (Windows/Inkscape), fallback to verify=False
#   and suppress urllib3 warnings to keep stdout clean.

from __future__ import annotations

import os, re, warnings
from typing import Optional, Tuple, Set, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import inkex

import prefs
import svg as SVG
import log as LOG
_l = LOG

try:
    import urllib3
    from urllib3.exceptions import InsecureRequestWarning
except Exception:  # pragma: no cover
    urllib3 = None
    InsecureRequestWarning = None

API_BASE = "https://api.iconify.design"
ICON_RE = re.compile(r'icon://([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+)')

# ---- Trace (ALWAYS ON) -------------------------------------------------------

def _suppress_insecure_request_warnings_once() -> None:
    if urllib3 is None or InsecureRequestWarning is None:
        return
    try:
        urllib3.disable_warnings(InsecureRequestWarning)
    except Exception:
        pass

def url_svg(prefix: str, name: str) -> str:
    return f"{API_BASE}/{prefix}/{name}.svg"

def _http_get(url: str, *, session: Optional[requests.Session] = None, timeout_s: int = 15) -> requests.Response:
    s = session or requests.Session()
    try:
        return s.get(url, timeout=timeout_s)
    except requests.exceptions.SSLError:
        # CA store roto en algunos bundles -> fallback
        _suppress_insecure_request_warnings_once()
        return s.get(url, timeout=timeout_s, verify=False)

def open_url_bytes(url: str, *, session: Optional[requests.Session] = None, timeout_s: int = 15) -> bytes:
    r = _http_get(url, session=session, timeout_s=timeout_s)
    content = r.content or b""
    if r.status_code != 200:
        raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
    return content

def _parse_svg_document(svg_text: str):
    # Parse full <svg ...> document
    try:
        root = inkex.etree.fromstring(svg_text.encode("utf-8"))
        return root
    except Exception as e:
        raise ValueError(f"invalid SVG XML: {e}") from e

def _ensure_svg_symbol(symbol_id: str, *, title: Optional[str] = None):
    sym = inkex.etree.Element(inkex.addNS("symbol", "svg"))
    sym.set("id", symbol_id)
    if title:
        t = inkex.etree.Element(inkex.addNS("title", "svg"))
        t.text = title
        sym.append(t)
    return sym

def _copy_svg_children(svg_root, symbol):
    # viewBox: prefer explicit
    vb = svg_root.get("viewBox")
    if vb:
        symbol.set("viewBox", vb)
    # copy all element children (<path>, <g>, ...)
    for child in list(svg_root):
        symbol.append(child)

def _viewbox_values(node) -> Tuple[float,float,float,float]:
    vb = (node.get("viewBox") or "").strip()
    if not vb:
        return (0.0,0.0,0.0,0.0)
    parts = [p for p in re.split(r"[ ,]+", vb) if p]
    if len(parts) != 4:
        return (0.0,0.0,0.0,0.0)
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except Exception:
        return (0.0,0.0,0.0,0.0)

def _normalize_to_square(symbol, *, add_rect: bool = True):
    # Stable viewBox square based on the incoming viewBox.
    x,y,w,h = _viewbox_values(symbol)
    if w <= 0 or h <= 0:
        # leave minimal vb to avoid pathological sizing
        symbol.set("viewBox", "0 0 1 1")
        return

    S = float(max(w,h))
    symbol.set("viewBox", f"0 0 {S:g} {S:g}")

    # Wrap non-title children into a <g> translated to origin if needed
    g = inkex.etree.Element(inkex.addNS("g", "svg"))
    if x != 0.0 or y != 0.0:
        g.set("transform", f"translate({-x:g},{-y:g})")

    titles = []
    others = []
    for c in list(symbol):
        if c.tag == inkex.addNS("title", "svg"):
            titles.append(c)
        else:
            others.append(c)

    for c in list(symbol):
        symbol.remove(c)

    for t in titles:
        symbol.append(t)

    if add_rect:
        # invisible geometry to keep bbox deterministic in Inkscape
        r = inkex.etree.Element(inkex.addNS("rect", "svg"))
        r.set("x","0"); r.set("y","0"); r.set("width", f"{S:g}"); r.set("height", f"{S:g}")
        r.set("fill","#000"); r.set("fill-opacity","0")
        r.set("stroke","none"); r.set("stroke-opacity","0")
        r.set("pointer-events","none")
        symbol.append(r)

    for c in others:
        g.append(c)
    symbol.append(g)

def ensure_icon_symbol(svgdoc, prefix: str, name: str, *,
                       add_rect: bool = True,
                       session: Optional[requests.Session] = None,
                       skip_if_exists: bool = True):
    prefix = (prefix or "").strip().lower()
    name = (name or "").strip()
    defs = SVG.ensure_defs(svgdoc)
    sym_id = f"{prefix}--{name}"

    if skip_if_exists:
        hit = defs.xpath(f".//svg:symbol[@id='{sym_id}']", namespaces=SVG.NSS)
        if hit:
            return hit[0], sym_id

    url = url_svg(prefix, name)
    try:
        svg_bytes = open_url_bytes(url, session=session)
        svg_text = svg_bytes.decode("utf-8", errors="replace")

        svg_root = _parse_svg_document(svg_text)

        # normalize paths before moving nodes
        try:
            SVG.fix_all_paths(svg_root)
        except Exception:
            pass

        symbol = _ensure_svg_symbol(sym_id, title=f"{prefix}:{name}")
        _copy_svg_children(svg_root, symbol)

        _normalize_to_square(symbol, add_rect=bool(add_rect))

        defs.append(symbol)
        return symbol, sym_id
    except Exception as e:
        symbol = _ensure_svg_symbol(sym_id, title=f"{prefix}:{name}")
        symbol.set("viewBox", "0 0 1 1")
        defs.append(symbol)
        _l.w("[iconify] placeholder %s/%s (%s)", prefix, name, str(e))
        return symbol, sym_id

def ensure_icon_symbols_parallel(svgdoc,
                                 icons: List[Tuple[str, str]],
                                 *,
                                 add_rect: bool = True,
                                 max_workers: int = 12,
                                 session: Optional[requests.Session] = None) -> Dict[str, str]:
    if not icons:
        return {"ok": "0", "placeholder": "0", "skip": "0"}

    defs = SVG.ensure_defs(svgdoc)
    existing = set(el.get("id") for el in defs.xpath(".//svg:symbol[@id]", namespaces=SVG.NSS))

    uniq = []
    seen = set()
    for p, n in icons:
        p = (p or "").strip().lower()
        n = (n or "").strip()
        if not p or not n:
            continue
        sid = f"{p}--{n}"
        if sid in seen:
            continue
        seen.add(sid)
        uniq.append((p, n, sid))

    todo = [(p, n, sid) for (p, n, sid) in uniq if sid not in existing]
    if not todo:
        return {"ok": "0", "placeholder": "0", "skip": str(len(uniq))}

    def _worker(p: str, n: str, sid: str):
        url = url_svg(p, n)
        try:
            b = open_url_bytes(url, session=session)
            return (sid, p, n, b, None)
        except Exception as e:
            return (sid, p, n, None, e)

    ok = 0
    placeholder = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_worker, p, n, sid) for (p, n, sid) in todo]
        for fut in as_completed(futures):
            sid, p, n, b, err = fut.result()

            if b:
                try:
                    txt = b.decode("utf-8", errors="replace")
                    svg_root = _parse_svg_document(txt)
                    try:
                        SVG.fix_all_paths(svg_root)
                    except Exception:
                        pass
                    symbol = _ensure_svg_symbol(sid, title=f"{p}:{n}")
                    _copy_svg_children(svg_root, symbol)
                    _normalize_to_square(symbol, add_rect=bool(add_rect))
                    ok += 1
                except Exception as e:
                    symbol = _ensure_svg_symbol(sid, title=f"{p}:{n}")
                    symbol.set("viewBox", "0 0 1 1")
                    placeholder += 1
                    _l.w("[iconify] placeholder parse %s/%s (%s)", p, n, str(e))
            else:
                symbol = _ensure_svg_symbol(sid, title=f"{p}:{n}")
                symbol.set("viewBox", "0 0 1 1")
                placeholder += 1
                _l.w("[iconify] placeholder fetch %s/%s (%s)", p, n, str(err) if err else "none")

            defs.append(symbol)

    return {"ok": str(ok), "placeholder": str(placeholder), "skip": str(len(uniq) - len(todo))}
