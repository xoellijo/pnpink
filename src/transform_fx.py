# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable

import inkex
import log as LOG
import svg as SVG

try:
    import dsl as DSL
except Exception:
    DSL = None

_l = LOG


def merge_specs(specs: Iterable[object]) -> object | None:
    items = [s for s in (specs or []) if s is not None]
    if not items:
        return None
    if DSL is None:
        return items[-1]
    out = DSL.TransformSpec()
    for sp in items:
        if getattr(sp, "rotate", None) not in (None, 0, 0.0):
            out.rotate = (out.rotate or 0.0) + float(getattr(sp, "rotate") or 0.0)
        if getattr(sp, "mirror", None):
            out.mirror = str(getattr(sp, "mirror") or "").strip().lower()
        if getattr(sp, "opacity", None):
            out.opacity = str(getattr(sp, "opacity") or "").strip()
        if getattr(sp, "soft", None):
            out.soft = [str(v).strip() for v in (getattr(sp, "soft") or []) if str(v).strip()]
        if getattr(sp, "filter_ref", None):
            out.filter_ref = str(getattr(sp, "filter_ref") or "").strip()
    return out


def _parse_percent_value(raw, *, name: str) -> float:
    s = str(raw or "").strip()
    if not s.endswith("%"):
        raise ValueError(f"{name} requires percentage values")
    try:
        v = float(s[:-1].strip()) / 100.0
    except Exception as ex:
        raise ValueError(f"{name} invalid percentage '{s}'") from ex
    if v < 0.0:
        v = 0.0
    if v > 1.0:
        v = 1.0
    return float(v)


def _normalize_soft_values(raw_vals) -> tuple[float, float, float, float] | None:
    vals = [str(v).strip() for v in (raw_vals or []) if str(v).strip()]
    if not vals:
        return None
    if len(vals) == 1:
        v = _parse_percent_value(vals[0], name="soft")
        return (v, v, v, v)
    if len(vals) == 2:
        # [horizontal vertical]
        hx = _parse_percent_value(vals[0], name="soft")
        vy = _parse_percent_value(vals[1], name="soft")
        return (vy, hx, vy, hx)
    if len(vals) == 4:
        t = _parse_percent_value(vals[0], name="soft")
        r = _parse_percent_value(vals[1], name="soft")
        b = _parse_percent_value(vals[2], name="soft")
        l = _parse_percent_value(vals[3], name="soft")
        return (t, r, b, l)
    raise ValueError("soft requires 1, 2 or 4 percentage values")


def _opacity_value(raw) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    v = _parse_percent_value(s, name="opacity")
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _num_sig(v: float) -> str:
    return f"{v * 100.0:.3f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")


def _fmt_num(v: float) -> str:
    return f"{float(v):.6f}".rstrip("0").rstrip(".")


def _resolve_filter_value(root, raw_ref) -> str | None:
    ref = str(raw_ref or "").strip()
    if not ref or root is None:
        return None
    if ref.startswith("url(#") and ref.endswith(")"):
        return ref

    try:
        node = SVG.find_target_exact_in(root, ref)
    except Exception:
        node = root.find(f".//*[@id='{ref}']")
    if node is None:
        return f"url(#{ref})"

    try:
        if str(getattr(node, "tag", "")).endswith("filter"):
            return f"url(#{ref})"
    except Exception:
        pass

    direct = str(node.get("filter") or "").strip()
    if direct:
        return direct

    style = str(node.get("style") or "").strip()
    if style:
        for part in style.split(";"):
            k, sep, v = part.partition(":")
            if sep and k.strip().lower() == "filter":
                vv = v.strip()
                if vv:
                    return vv
    return None


def _resolve_fx_rect(target) -> tuple[float, float, float, float] | None:
    if target is None:
        return None
    try:
        raw = str(target.get("data-fx-rect") or "").strip()
        if not raw:
            return None
        parts = [float(p) for p in raw.replace(",", " ").split() if p]
        if len(parts) == 4 and parts[2] > 0.0 and parts[3] > 0.0:
            return (parts[0], parts[1], parts[2], parts[3])
    except Exception:
        return None
    return None


def _apply_visual_matrix(node, *, rotate=None, mirror=None) -> bool:
    if node is None:
        return False
    rot = float(rotate or 0.0)
    mir = str(mirror or "").strip().lower()
    if rot == 0.0 and mir not in ("h", "v"):
        return False
    parent = node.getparent() if hasattr(node, "getparent") else None
    if parent is None:
        return False
    try:
        x, y, w, h = SVG.visual_bbox(node)
    except Exception:
        return False
    if w <= 0.0 or h <= 0.0:
        return False
    try:
        inv_parent = parent.composed_transform().inverse()
    except Exception:
        inv_parent = inkex.Transform()
    cx, cy = inv_parent.apply_to_point((float(x) + float(w) * 0.5, float(y) + float(h) * 0.5))
    L = inkex.Transform()
    if mir == "h":
        L = L @ inkex.Transform("scale(-1,1)")
    elif mir == "v":
        L = L @ inkex.Transform("scale(1,-1)")
    if rot:
        L = L @ inkex.Transform(f"rotate({rot})")
    extra = inkex.Transform(f"translate({cx},{cy})") @ L @ inkex.Transform(f"translate({-cx},{-cy})")
    try:
        old = inkex.Transform(node.get("transform") or "")
    except Exception:
        old = inkex.Transform()
    node.set("transform", str(extra @ old))
    return True


def _ensure_soft_gradients(root) -> dict[str, str]:
    defs = SVG.ensure_defs(root)

    def _mk_grad(gid: str, attrs: dict[str, str], stops: list[tuple[str, str, str | None]]):
        old = root.find(f".//svg:linearGradient[@id='{gid}']", namespaces=SVG.NSS)
        if old is not None:
            try:
                p = old.getparent()
                if p is not None:
                    p.remove(old)
            except Exception:
                pass
        grad = SVG.etree.SubElement(defs, inkex.addNS("linearGradient", "svg"))
        grad.set("id", gid)
        grad.set("gradientUnits", "objectBoundingBox")
        for k, v in attrs.items():
            grad.set(k, v)
        for offset, color, opacity in stops:
            st = SVG.etree.SubElement(grad, inkex.addNS("stop", "svg"))
            st.set("offset", offset)
            st.set("stop-color", color)
            if opacity is not None:
                st.set("stop-opacity", opacity)
        return gid

    out = {}
    out["left"] = _mk_grad(
        "tf_soft_grad_left",
        {"x1": "0", "y1": "0", "x2": "1", "y2": "0"},
        [("0%", "#000000", "1"), ("100%", "#808080", "0")],
    )
    out["right"] = _mk_grad(
        "tf_soft_grad_right",
        {"x1": "0", "y1": "0", "x2": "1", "y2": "0"},
        [("0%", "#808080", "0"), ("100%", "#000000", "1")],
    )
    out["top"] = _mk_grad(
        "tf_soft_grad_top",
        {"x1": "0", "y1": "0", "x2": "0", "y2": "1"},
        [("0%", "#000000", "1"), ("100%", "#808080", "0")],
    )
    out["bottom"] = _mk_grad(
        "tf_soft_grad_bottom",
        {"x1": "0", "y1": "0", "x2": "0", "y2": "1"},
        [("0%", "#808080", "0"), ("100%", "#000000", "1")],
    )
    return out


def _ensure_soft_mask(root, geom_node, soft_vals: tuple[float, float, float, float]) -> str | None:
    t, r, b, l = [max(0.0, min(1.0, float(v))) for v in soft_vals]
    sig = f"{_num_sig(t)}_{_num_sig(r)}_{_num_sig(b)}_{_num_sig(l)}"
    rect = _resolve_fx_rect(geom_node)
    if rect is not None:
        x, y, w, h = rect
        rsig = f"{_num_sig(x)}_{_num_sig(y)}_{_num_sig(w)}_{_num_sig(h)}"
        mid = f"tf_soft_mask_{sig}_{rsig}"
    else:
        x = y = 0.0
        w = h = 1.0
        mid = f"tf_soft_mask_{sig}"
    old = root.find(f".//svg:mask[@id='{mid}']", namespaces=SVG.NSS)
    if old is not None:
        try:
            p = old.getparent()
            if p is not None:
                p.remove(old)
        except Exception:
            pass

    defs = SVG.ensure_defs(root)
    grads = _ensure_soft_gradients(root)
    mask = SVG.etree.SubElement(defs, inkex.addNS("mask", "svg"))
    mask.set("id", mid)
    if rect is not None:
        mask.set("maskUnits", "userSpaceOnUse")
        mask.set("maskContentUnits", "userSpaceOnUse")
    else:
        mask.set("maskUnits", "objectBoundingBox")
        mask.set("maskContentUnits", "objectBoundingBox")
    mask.set("mask-type", "luminance")
    base = SVG.etree.SubElement(mask, inkex.addNS("rect", "svg"))
    base.set("x", _fmt_num(x))
    base.set("y", _fmt_num(y))
    base.set("width", _fmt_num(w))
    base.set("height", _fmt_num(h))
    base.set("fill", "#ffffff")
    base.set("fill-opacity", "1")

    def _edge_rect(x, y, w, h, gid):
        if w <= 0.0 or h <= 0.0:
            return
        rr = SVG.etree.SubElement(mask, inkex.addNS("rect", "svg"))
        rr.set("x", _fmt_num(x))
        rr.set("y", _fmt_num(y))
        rr.set("width", _fmt_num(w))
        rr.set("height", _fmt_num(h))
        rr.set("fill", f"url(#{gid})")

    _edge_rect(x, y, w * l, h, grads["left"])
    _edge_rect(x + w * (1.0 - r), y, w * r, h, grads["right"])
    _edge_rect(x, y, w, h * t, grads["top"])
    _edge_rect(x, y + h * (1.0 - b), w, h * b, grads["bottom"])
    return mid


def apply_transform_spec(root, node, spec) -> bool:
    if root is None or node is None or spec is None:
        return False
    opacity_target = node
    soft_target = node
    filter_target = node
    try:
        parent = node.getparent() if hasattr(node, "getparent") else None
        pid = (parent.get("id") or "").strip() if parent is not None else ""
        gp = parent.getparent() if parent is not None and hasattr(parent, "getparent") else None
        gpid = (gp.get("id") or "").strip() if gp is not None else ""
        ggp = gp.getparent() if gp is not None and hasattr(gp, "getparent") else None
        ggpid = (ggp.get("id") or "").strip() if ggp is not None else ""
        if parent is not None and parent.get("clip-path") and pid.endswith("_clip"):
            if gp is not None and gpid.endswith("_soft"):
                soft_target = gp
                filter_target = gp
                if ggp is not None and (ggpid.startswith("fa_clipwrap_") or ggpid.endswith("_postshift")):
                    opacity_target = ggp
                else:
                    opacity_target = gp
            elif gp is not None and gpid.startswith("fa_clipwrap_"):
                soft_target = parent
                filter_target = parent
                opacity_target = gp
            else:
                soft_target = parent
                filter_target = parent
                opacity_target = parent
    except Exception:
        opacity_target = node
        soft_target = node
        filter_target = node
    changed = False

    try:
        if _apply_visual_matrix(opacity_target, rotate=getattr(spec, "rotate", None), mirror=getattr(spec, "mirror", None)):
            changed = True
    except Exception as ex:
        _l.w(f"[transform] rotate/mirror failed on id='{opacity_target.get('id') or ''}': {ex}")

    try:
        if getattr(spec, "opacity", None):
            op = _opacity_value(getattr(spec, "opacity"))
            if op is not None:
                opacity_target.set("opacity", op)
                if opacity_target is not node:
                    try:
                        node.attrib.pop("opacity", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] opacity failed on id='{opacity_target.get('id') or ''}': {ex}")

    try:
        raw_filter = getattr(spec, "filter_ref", None)
        if raw_filter:
            filt = _resolve_filter_value(root, raw_filter)
            if filt:
                filter_target.set("filter", filt)
                if filter_target is not node:
                    try:
                        node.attrib.pop("filter", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] filter failed on id='{filter_target.get('id') or ''}': {ex}")

    try:
        soft_vals = _normalize_soft_values(getattr(spec, "soft", None))
        if soft_vals is not None:
            mid = _ensure_soft_mask(root, soft_target, soft_vals)
            if mid:
                soft_target.set("mask", f"url(#{mid})")
                if soft_target is not node:
                    try:
                        node.attrib.pop("mask", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] soft failed on id='{soft_target.get('id') or ''}': {ex}")

    try:
        if changed:
            _l.d(
                f"[transform] applied opacity_target='{opacity_target.get('id') or ''}' "
                f"filter_target='{filter_target.get('id') or ''}' "
                f"soft_target='{soft_target.get('id') or ''}' "
                f"node='{node.get('id') or ''}' soft={getattr(spec, 'soft', None)} "
                f"filter={getattr(spec, 'filter_ref', None)} "
                f"opacity={getattr(spec, 'opacity', None)}"
            )
    except Exception:
        pass

    return changed


__all__ = ["merge_specs", "apply_transform_spec"]
