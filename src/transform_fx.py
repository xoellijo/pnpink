# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from copy import deepcopy
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
        if getattr(sp, "scale", None):
            out.scale = [str(v).strip() for v in (getattr(sp, "scale") or []) if str(v).strip()]
        if getattr(sp, "soft", None):
            out.soft = [str(v).strip() for v in (getattr(sp, "soft") or []) if str(v).strip()]
        if getattr(sp, "filter_ref", None):
            out.filter_ref = str(getattr(sp, "filter_ref") or "").strip()
        if getattr(sp, "text", None) is not None:
            out.text = [str(v) for v in (getattr(sp, "text") or [])]
    return out


def has_text(spec) -> bool:
    return bool(spec is not None and getattr(spec, "text", None) is not None)


def _is_text_root(node) -> bool:
    tag = str(getattr(node, "tag", "") or "")
    return tag.endswith("text") or tag.endswith("flowRoot")


def _iter_text_roots(node):
    if node is None:
        return
    if _is_text_root(node):
        yield node
        return
    for child in node.iter():
        if child is node:
            continue
        if _is_text_root(child):
            yield child


def _copy_use_ref(root, use_el):
    href = (SVG.get_href(use_el) or "").strip()
    ref = SVG.find_id(root, href[1:], include_defs=True) if href.startswith("#") else None
    if ref is None:
        return None
    if str(getattr(ref, "tag", "") or "").endswith("symbol"):
        out = SVG.etree.Element(inkex.addNS("g", "svg"), nsmap=getattr(ref, "nsmap", None))
        for child in ref:
            out.append(deepcopy(child))
    else:
        out = deepcopy(ref)
    try:
        out.attrib.pop("id", None)
        use_t = inkex.Transform(use_el.get("transform") or "")
        x = float(use_el.get("x") or 0.0)
        y = float(use_el.get("y") or 0.0)
        if x or y:
            use_t = use_t @ inkex.Transform(f"translate({x},{y})")
        base_t = inkex.Transform(out.get("transform") or "")
        out.set("transform", str(use_t @ base_t))
    except Exception:
        pass
    return out


def _expand_uses(root, node):
    for _ in range(16):
        changed = False
        for use_el in list(node.iter()):
            if not str(getattr(use_el, "tag", "") or "").endswith("use"):
                continue
            parent = use_el.getparent()
            repl = _copy_use_ref(root, use_el)
            if parent is None or repl is None:
                continue
            idx = parent.index(use_el)
            parent.remove(use_el)
            parent.insert(idx, repl)
            if use_el is node:
                node = repl
            changed = True
        if not changed:
            break
    return node


def _apply_text(root, node, values) -> bool:
    vals = [str(v) for v in (values or [])]
    if not vals:
        return False
    changed = False
    for text_el, value in zip(_iter_text_roots(node), vals):
        _replace_text_preserving_runs(text_el, value)
        changed = True
    return changed


def _text_run_nodes(text_el):
    for n in text_el.iter():
        tag = str(getattr(n, "tag", "") or "")
        if n is text_el or tag.endswith("tspan") or tag.endswith("textPath") or tag.endswith("flowPara"):
            yield n


def _replace_text_preserving_runs(text_el, value: str) -> None:
    runs = list(_text_run_nodes(text_el))
    if len(runs) <= 1:
        SVG.replace_text(text_el, value)
        return

    first = None
    for n in runs:
        if n is text_el:
            continue
        if len(list(n)) == 0:
            first = n
            break
    if first is None:
        SVG.replace_text(text_el, value)
        return

    text_el.text = None
    for n in runs:
        n.tail = None
        if n is first:
            n.text = "" if value is None else str(value)
        elif n is not text_el:
            n.text = None


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


def _abs_to_uu(root, raw: str) -> float:
    s = str(raw or "").strip()
    if not s:
        return 0.0
    try:
        if any(ch.isalpha() for ch in s):
            return float(root.unittouu(s))
        return float(root.unittouu(s + "mm"))
    except Exception:
        return float(s or 0.0)


def _target_size_from_scale_token(root, token: str, base: float) -> float:
    s = str(token or "").strip().replace(" ", "")
    if not s:
        return float(base)
    terms = [m.group(0) for m in re.finditer(r"[+-]?[^+-]+", s)]
    pct = 0.0
    abs_uu = 0.0
    has_pct = False
    for term in terms:
        sign = -1.0 if term.startswith("-") else 1.0
        body = term[1:] if term[:1] in "+-" else term
        if not body:
            continue
        if body.endswith("%"):
            pct += sign * (float(body[:-1] or "0") / 100.0)
            has_pct = True
        else:
            abs_uu += sign * _abs_to_uu(root, body)
    return (float(base) * pct + abs_uu) if has_pct else (float(base) + abs_uu)


def _normalize_scale_values(raw_vals) -> tuple[str, str] | None:
    vals = [str(v).strip() for v in (raw_vals or []) if str(v).strip()]
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0], vals[0]
    if len(vals) == 2:
        return vals[0], vals[1]
    raise ValueError("scale requires 1 or 2 values")


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

    def _find_by_inkscape_label(label: str):
        want = str(label or "").strip()
        if not want:
            return None
        label_keys = (
            inkex.addNS("label", "inkscape"),
            "inkscape:label",
        )
        try:
            for el in root.iter():
                if any(str(el.get(k) or "").strip() == want for k in label_keys):
                    return el
        except Exception:
            return None
        return None

    try:
        node = SVG.find_target_exact_in(root, ref)
    except Exception:
        node = root.find(f".//*[@id='{ref}']")
    if node is None:
        node = _find_by_inkscape_label(ref)
    if node is None:
        return f"url(#{ref})"

    try:
        if str(getattr(node, "tag", "")).endswith("filter"):
            fid = str(node.get("id") or "").strip()
            return f"url(#{fid})" if fid else None
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


def _normalize_bbox(bbox) -> tuple[float, float, float, float] | None:
    try:
        if bbox is None or len(bbox) != 4:
            return None
        x, y, w, h = [float(v) for v in bbox]
        if w > 0.0 and h > 0.0:
            return (x, y, w, h)
    except Exception:
        return None
    return None


def _apply_visual_matrix(root, node, *, rotate=None, mirror=None, scale=None, bbox=None) -> bool:
    if node is None:
        return False
    rot = float(rotate or 0.0)
    mir = str(mirror or "").strip().lower()
    scale_vals = _normalize_scale_values(scale)
    if rot == 0.0 and mir not in ("h", "v") and scale_vals is None:
        return False
    parent = node.getparent() if hasattr(node, "getparent") else None
    if parent is None:
        return False
    bb = _normalize_bbox(bbox)
    if bb is None:
        try:
            bb = SVG.visual_bbox(node)
        except Exception:
            return False
    x, y, w, h = bb
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
    if scale_vals is not None:
        target_w = max(1e-9, _target_size_from_scale_token(root, scale_vals[0], w))
        target_h = max(1e-9, _target_size_from_scale_token(root, scale_vals[1], h))
        L = L @ inkex.Transform(f"scale({target_w / w},{target_h / h})")
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


def apply_transform_spec(root, node, spec, *, bbox=None) -> bool:
    if root is None or node is None or spec is None:
        return False
    if has_text(spec):
        try:
            node = _expand_uses(root, node)
        except Exception as ex:
            _l.w(f"[transform] text use expansion failed on id='{node.get('id') or ''}': {ex}")
        try:
            import text as TXT
            TXT._normalize_rich_visible_for_all_texts(node)
            if _is_text_root(node):
                TXT._maybe_parse_rich_visible_into_dom(node)
        except Exception:
            pass
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
        if has_text(spec):
            changed = _apply_text(root, node, getattr(spec, "text", None)) or changed
    except Exception as ex:
        _l.w(f"[transform] text failed on id='{node.get('id') or ''}': {ex}")

    try:
        if _apply_visual_matrix(root, opacity_target, rotate=getattr(spec, "rotate", None), mirror=getattr(spec, "mirror", None), scale=getattr(spec, "scale", None), bbox=bbox):
            changed = True
    except Exception as ex:
        _l.w(f"[transform] visual matrix failed on id='{opacity_target.get('id') or ''}': {ex}")

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
                f"scale={getattr(spec, 'scale', None)} "
                f"filter={getattr(spec, 'filter_ref', None)} "
                f"opacity={getattr(spec, 'opacity', None)} "
                f"text={getattr(spec, 'text', None)}"
            )
    except Exception:
        pass

    return changed


__all__ = ["merge_specs", "has_text", "apply_transform_spec"]
