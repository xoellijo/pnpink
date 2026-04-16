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
        if getattr(sp, "opacity", None):
            out.opacity = str(getattr(sp, "opacity") or "").strip()
        if getattr(sp, "soft", None):
            out.soft = [str(v).strip() for v in (getattr(sp, "soft") or []) if str(v).strip()]
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


def _ensure_soft_gradients(root) -> dict[str, str]:
    defs = SVG.ensure_defs(root)

    def _mk_grad(gid: str, attrs: dict[str, str], stops: list[tuple[str, str, str | None]]):
        old = root.find(f".//svg:linearGradient[@id='{gid}']", namespaces=SVG.NSS)
        if old is not None:
            return gid
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
        [("0%", "#ffffff", "0"), ("100%", "#ffffff", "1")],
    )
    out["right"] = _mk_grad(
        "tf_soft_grad_right",
        {"x1": "0", "y1": "0", "x2": "1", "y2": "0"},
        [("0%", "#ffffff", "1"), ("100%", "#ffffff", "0")],
    )
    out["top"] = _mk_grad(
        "tf_soft_grad_top",
        {"x1": "0", "y1": "0", "x2": "0", "y2": "1"},
        [("0%", "#ffffff", "0"), ("100%", "#ffffff", "1")],
    )
    out["bottom"] = _mk_grad(
        "tf_soft_grad_bottom",
        {"x1": "0", "y1": "0", "x2": "0", "y2": "1"},
        [("0%", "#ffffff", "1"), ("100%", "#ffffff", "0")],
    )
    return out


def _ensure_soft_mask(root, soft_vals: tuple[float, float, float, float]) -> str:
    t, r, b, l = [max(0.0, min(1.0, float(v))) for v in soft_vals]
    sig = f"{_num_sig(t)}_{_num_sig(r)}_{_num_sig(b)}_{_num_sig(l)}"
    mid = f"tf_soft_mask_{sig}"
    old = root.find(f".//svg:mask[@id='{mid}']", namespaces=SVG.NSS)
    if old is not None:
        return mid

    defs = SVG.ensure_defs(root)
    grads = _ensure_soft_gradients(root)
    mask = SVG.etree.SubElement(defs, inkex.addNS("mask", "svg"))
    mask.set("id", mid)
    mask.set("maskUnits", "objectBoundingBox")
    mask.set("maskContentUnits", "objectBoundingBox")
    mask.set("mask-type", "alpha")

    cx = l
    cy = t
    cw = max(0.0, 1.0 - l - r)
    ch = max(0.0, 1.0 - t - b)
    if cw > 0.0 and ch > 0.0:
        base = SVG.etree.SubElement(mask, inkex.addNS("rect", "svg"))
        base.set("x", f"{cx:.6f}".rstrip("0").rstrip("."))
        base.set("y", f"{cy:.6f}".rstrip("0").rstrip("."))
        base.set("width", f"{cw:.6f}".rstrip("0").rstrip("."))
        base.set("height", f"{ch:.6f}".rstrip("0").rstrip("."))
        base.set("fill", "#ffffff")
        base.set("fill-opacity", "1")

    def _edge_rect(x, y, w, h, gid):
        if w <= 0.0 or h <= 0.0:
            return
        rr = SVG.etree.SubElement(mask, inkex.addNS("rect", "svg"))
        rr.set("x", f"{x:.6f}".rstrip("0").rstrip("."))
        rr.set("y", f"{y:.6f}".rstrip("0").rstrip("."))
        rr.set("width", f"{w:.6f}".rstrip("0").rstrip("."))
        rr.set("height", f"{h:.6f}".rstrip("0").rstrip("."))
        rr.set("fill", f"url(#{gid})")

    _edge_rect(0.0, t, l, ch, grads["left"])
    _edge_rect(max(0.0, 1.0 - r), t, r, ch, grads["right"])
    _edge_rect(l, 0.0, cw, t, grads["top"])
    _edge_rect(l, max(0.0, 1.0 - b), cw, b, grads["bottom"])
    return mid


def apply_transform_spec(root, node, spec) -> bool:
    if root is None or node is None or spec is None:
        return False
    target = node
    try:
        parent = node.getparent() if hasattr(node, "getparent") else None
        pid = (parent.get("id") or "").strip() if parent is not None else ""
        gp = parent.getparent() if parent is not None and hasattr(parent, "getparent") else None
        gpid = (gp.get("id") or "").strip() if gp is not None else ""
        if parent is not None and parent.get("clip-path") and pid.endswith("_clip"):
            # Apply transforms to the outer clip wrapper when possible.
            # Inkscape is less reliable when a reusable mask sits directly on
            # the same group that already carries clip-path.
            if gp is not None and gpid.startswith("fa_clipwrap_"):
                target = gp
            else:
                target = parent
    except Exception:
        target = node
    changed = False

    try:
        if getattr(spec, "opacity", None):
            op = _opacity_value(getattr(spec, "opacity"))
            if op is not None:
                target.set("opacity", op)
                if target is not node:
                    try:
                        node.attrib.pop("opacity", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] opacity failed on id='{target.get('id') or ''}': {ex}")

    try:
        soft_vals = _normalize_soft_values(getattr(spec, "soft", None))
        if soft_vals is not None:
            mid = _ensure_soft_mask(root, soft_vals)
            target.set("mask", f"url(#{mid})")
            if target is not node:
                try:
                    node.attrib.pop("mask", None)
                except Exception:
                    pass
            changed = True
    except Exception as ex:
        _l.w(f"[transform] soft failed on id='{target.get('id') or ''}': {ex}")

    try:
        if changed:
            _l.d(
                f"[transform] applied target='{target.get('id') or ''}' "
                f"node='{node.get('id') or ''}' soft={getattr(spec, 'soft', None)} "
                f"opacity={getattr(spec, 'opacity', None)}"
            )
    except Exception:
        pass

    return changed


__all__ = ["merge_specs", "apply_transform_spec"]
