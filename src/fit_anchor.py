#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fit_anchor.py - PnPInk

Place a node onto a rect using dsl.py fit syntax.
There is NO legacy parsing here. dsl.py handles everything.
"""

import re
import hashlib
import inkex
import svg
import dsl as DSL
import log as LOG
import transform_fx as TFX
_l = LOG
LOG_PREFIX = "[fit_anchor]"

# ================================================================
# Minimal helpers (the same ones as before)
# ================================================================

def _fa_root_of(node):
    while node.getparent() is not None:
        node = node.getparent()
    return node

def _fa_find_in(scope, root, elem_id):
    if not elem_id:
        return None
    n = scope.find(".//*[@id='%s']" % elem_id)
    if n is None:
        n = root.find(".//*[@id='%s']" % elem_id)
    return n


def _is_long_fit_ops(ops: str) -> bool:
    s = (ops or "").strip()
    return bool(
        s.startswith(".Fit")
        or re.match(r"^[A-Za-z][\w\-.]*\s*\.Fit\s*\{", s)
        or (s.startswith("{") and s.endswith("}"))
    )


def _rect_intersection(a, b):
    if a is None:
        return b
    if b is None:
        return a
    try:
        ax, ay, aw, ah = [float(v) for v in a]
        bx, by, bw, bh = [float(v) for v in b]
    except Exception:
        return a
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)

def _bbox_with_transform(T: inkex.Transform, x, y, w, h):
    pts = [
        T.apply_to_point((x, y)),
        T.apply_to_point((x + w, y)),
        T.apply_to_point((x, y + h)),
        T.apply_to_point((x + w, y + h)),
    ]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lx, ly = min(xs), min(ys)
    return lx, ly, max(xs) - lx, max(ys) - ly

def _css_box_shorthand(lst):
    if not lst:
        return 0, 0, 0, 0
    if len(lst) == 1:
        return lst[0], lst[0], lst[0], lst[0]
    if len(lst) == 2:
        return lst[0], lst[1], lst[0], lst[1]
    if len(lst) == 3:
        return lst[0], lst[1], lst[2], lst[1]
    return lst[0], lst[1], lst[2], lst[3]


def _clip_def_id_from_shape(kind: str, shape_el) -> str:
    """Build a stable defs id from the effective clip geometry.

    We only dedupe clips whose geometry is self-contained inside the clipPath
    (rect/path/polygon/circle/ellipse in wrapper-local coords). Reference-based
    clips such as <use href="#..."> stay per-instance because the referenced id
    may differ across clones even if the visible outline looks the same.
    """
    if shape_el is None or kind == "use-shape":
        return ""
    try:
        payload = svg.etree.tostring(shape_el, encoding="unicode")
    except Exception:
        try:
            payload = repr(sorted((shape_el.attrib or {}).items()))
        except Exception:
            payload = kind
    digest = hashlib.sha1(f"{kind}|{payload}".encode("utf-8")).hexdigest()[:16]
    return f"clip_fa_{digest}"

# ================================================================
# Entry point used by DeckMaker / sources
# ================================================================

def apply_to_by_ids(scope, base_id, rect_id, ops_full, place_mode="clone", rect_elem=None, **kwargs):
    """Place `base_id` over `rect_id` using Fit/Anchor DSL operations."""
    # compat with deckmaker: it comes as `place=...`
    if "place" in kwargs and kwargs["place"]:
        place_mode = kwargs["place"]

    # overrides used by DeckMaker (overlays): parent/insert_after
    parent_elem = kwargs.get('parent_elem')
    insert_after_elem = kwargs.get('insert_after_elem')
    return_bbox = bool(kwargs.get('return_bbox', False))
    ignore_source_ancestors = bool(kwargs.get("ignore_source_ancestors", False))

    root = _fa_root_of(scope)
    svgdoc = root
    local_transform_spec = None

    # 1) resolver base
    base = _fa_find_in(scope, root, base_id)
    if base is None:
        raise inkex.AbortExtension(f"{LOG_PREFIX} base id='{base_id}' not found in scope/doc")

    # 2) resolver rect
    if rect_elem is not None:
        rect = rect_elem
    else:
        if not rect_id:
            raise inkex.AbortExtension(f"{LOG_PREFIX} rect id is empty and no rect_elem was provided")
        rect = _fa_find_in(scope, root, rect_id)
        if rect is None:
            raise inkex.AbortExtension(f"{LOG_PREFIX} rect id='{rect_id}' not found in scope/doc")

    # LOG: basic base/rect info
    base_href = base.get(inkex.addNS("href", "xlink")) or base.get("href")
    rect_tag = rect.tag if hasattr(rect, "tag") else None
    base_tag = base.tag if hasattr(base, "tag") else None
    _l.d(
        f"{LOG_PREFIX} FA start base_id='{base_id}' rect_id='{rect_id}' "
        f"base_tag='{base_tag}' rect_tag='{rect_tag}' "
        f"base_href='{base_href}' base_transform='{base.get('transform')}' "
        f"rect_transform='{rect.get('transform')}'"
    )

    # 3) normalize ops -> FitSpec using dsl.py
    fs = None
    if isinstance(ops_full, DSL.FitSpec):
        fs = ops_full
    else:
        ops_s = (ops_full or "").strip()
        try:
            if not _is_long_fit_ops(ops_s):
                ops_s, local_transform_spec = DSL.split_ops_fit_transform(ops_s)
            # cases with .Fit{...}
            if ops_s.startswith(".Fit"):
                cmd = DSL.parse(f"{base_id}{ops_s}")
                if cmd and getattr(cmd, "fit", None):
                    fs = cmd.fit
            elif ops_s and re.match(r"^[A-Za-z][\w\-.]*\s*\.Fit\s*\{", ops_s):
                # may carry another target inside
                cmd = DSL.parse(ops_s)
                if cmd and getattr(cmd, "target", None) and hasattr(cmd.target, "name"):
                    base_id = cmd.target.name
                    base = _fa_find_in(scope, root, base_id)
                    if base is None:
                        raise inkex.AbortExtension(f"{LOG_PREFIX} base id='{base_id}' (from DSL) not found")
                if cmd and getattr(cmd, "fit", None):
                    fs = cmd.fit
            elif ops_s.startswith("{") and ops_s.endswith("}"):
                cmd = DSL.parse(f"{base_id}.Fit{ops_s}")
                if cmd and getattr(cmd, "fit", None):
                    fs = cmd.fit
            else:
                # forma corta: "~i7", "~m7", "i7", "7", "~{ i7 }", etc.
                fs = DSL.fit_spec_from_ops(ops_s)
        except Exception as ex:
            _l.w(f"{LOG_PREFIX} DSL fit parse failed for ops='{ops_full}': {ex}")
            raise

    if fs is None:
        raise inkex.AbortExtension(f"{LOG_PREFIX} could not obtain FitSpec from ops='{ops_full}'")

    # =========================================================
    # From here on: ONLY geometry with what already exists in svg.py
    # =========================================================

    # 4) rect bbox (visual)
    rx, ry, rw, rh = svg.visual_bbox(rect)

    # 5) apply DSL border/pad (and possible mirror via WxH) using shared helpers
    pad_top = pad_right = pad_bottom = pad_left = 0.0
    border_mir_h = border_mir_v = False
    if getattr(fs, "border", None):
        try:
            pad_top, pad_right, pad_bottom, pad_left, border_mir_h, border_mir_v = svg.border_tokens_to_pad_px(
                svgdoc, float(rw), float(rh), fs.border
            )
        except Exception as ex:
            _l.w(f"{LOG_PREFIX} border parse failed: {ex}")
            pad_top = pad_right = pad_bottom = pad_left = 0.0
            border_mir_h = border_mir_v = False

    if pad_top or pad_right or pad_bottom or pad_left:
        final_scale = kwargs.get("final_scale")
        if final_scale:
            try:
                fsx, fsy = float(final_scale[0]), float(final_scale[1])
                if abs(fsx) > 1e-9:
                    pad_left /= abs(fsx)
                    pad_right /= abs(fsx)
                if abs(fsy) > 1e-9:
                    pad_top /= abs(fsy)
                    pad_bottom /= abs(fsy)
            except Exception:
                pass
        inner_x, inner_y, inner_w, inner_h = svg.rect_with_pad(
            rx, ry, rw, rh, (pad_top, pad_right, pad_bottom, pad_left)
        )
    else:
        inner_x, inner_y, inner_w, inner_h = rx, ry, rw, rh

    # 6) base bbox (visual)
    bx, by, bw, bh = svg.visual_bbox(base)

    # LOG: bboxes
    _l.d(
        f"{LOG_PREFIX} FA bbox rect='{rect_id}' "
        f"({rx:.2f},{ry:.2f},{rw:.2f},{rh:.2f}) "
        f"inner=({inner_x:.2f},{inner_y:.2f},{inner_w:.2f},{inner_h:.2f})"
    )
    _l.d(
        f"{LOG_PREFIX} FA bbox base='{base_id}' "
        f"({bx:.2f},{by:.2f},{bw:.2f},{bh:.2f})"
    )

    # 7) extract mode/anchor/rot/mirrors from FitSpec
    # Default fit mode when not specified: inside/contain ('i')
    # (Previously defaulted to 'n' = no scaling/original size)
    mode_code = getattr(fs, "mode", None) or "i"
    if mode_code in ("o", "n"):
        fit_mode = "n"
    else:
        fit_mode = mode_code

    anchor_key = getattr(fs, "anchor", None) or 5
    ax, ay = svg.keypad_to_anchor(anchor_key)

    rot_deg = 0.0
    mir_h = False
    mir_v = False
    # mirror induced by border WxH (negative width/height) composes with explicit mirror via XOR
    mir_h = bool(mir_h) ^ bool(border_mir_h)
    mir_v = bool(mir_v) ^ bool(border_mir_v)

    # 8) optional shift
    # Percent values are relative to placeholder size on each axis.
    shift_x = shift_y = 0.0
    def _shift_to_px(v, axis_base_px: float) -> float:
        if isinstance(v, str):
            s = v.strip()
            try:
                px_per_mm = float(svgdoc.unittouu("1mm"))
            except Exception:
                px_per_mm = float(getattr(svg, "PX_PER_MM", 1.0) or 1.0)
            try:
                base_mm = float(axis_base_px) / px_per_mm if px_per_mm else None
            except Exception:
                base_mm = None
            return float(svg.measure_to_px(svgdoc, s, base_mm=base_mm if "%" in s else None))
        try:
            return float(svg.measure_to_px(svgdoc, v, base_mm=None))
        except Exception:
            return float(v)
    if getattr(fs, "shift", None) and isinstance(fs.shift, (list, tuple)) and len(fs.shift) >= 1:
        sx_raw = fs.shift[0]
        sy_raw = fs.shift[1] if len(fs.shift) >= 2 else fs.shift[0]
        shift_x = _shift_to_px(sx_raw, float(rw))
        shift_y = _shift_to_px(sy_raw, float(rh))

    # 9) scale according to mode
    if fit_mode == "n":
        # no scale: use the base's original size
        sx = sy = 1.0
        fitted_w = bw
        fitted_h = bh
    else:
    # inner_w/inner_h = inner rect where we want to fit the base
        sx, sy = svg.compute_fit_scale(bw, bh, inner_w, inner_h, fit_mode)
        fitted_w = bw * sx
        fitted_h = bh * sy

    # 10) target point: the ANCHOR is ALWAYS computed over the ORIGINAL placeholder
    # (without applying border). Border only modifies the "usable" rect for fit/scale.
    # We do NOT subtract the object size: build_fit_transform already handles that.
    target_x_world = rx + ax * rw + shift_x
    target_y_world = ry + ay * rh + shift_y

    # 11) mapping de place
    mode_map = {
        "clone": "use",
        "copy": "deep",
        "clone+unlink": "use+unlink",
    }
    place = mode_map.get(place_mode, "use")
    if str(place_mode or "").strip().lower() not in ("copy", "deep", "use+unlink", "clone+unlink", "unlink"):
        try:
            preferred_place = str(base.get("data-place-mode") or "").strip().lower()
            if preferred_place in ("deep", "copy"):
                place = "deep"
            elif preferred_place in ("use", "clone"):
                place = "use"
            elif preferred_place in ("use+unlink", "clone+unlink", "unlink"):
                place = "use+unlink"
        except Exception:
            pass
    try:
        id_prefix = base.get("data-id-prefix") if "data-id-prefix" in dict(base.attrib or {}) else "af"
    except Exception:
        id_prefix = "af"

    # 12) parent where we place the clone. DeckMaker overlays can override it.
    parent = parent_elem if parent_elem is not None else (rect.getparent() if rect.getparent() is not None else root)

    if ignore_source_ancestors and place == "deep" and not getattr(fs, "clip", False):
        try:
            inv_parent = parent.composed_transform().inverse()
        except Exception:
            inv_parent = inkex.Transform()
        try:
            base_parent = base.getparent()
            inv_base_parent = base_parent.composed_transform().inverse() if base_parent is not None else inkex.Transform()
        except Exception:
            inv_base_parent = inkex.Transform()

        rx_l, ry_l, rw_l, rh_l = _bbox_with_transform(inv_parent, rx, ry, rw, rh)
        ix_l, iy_l, iw_l, ih_l = _bbox_with_transform(inv_parent, inner_x, inner_y, inner_w, inner_h)
        bx_l, by_l, bw_l, bh_l = _bbox_with_transform(inv_base_parent, bx, by, bw, bh)

        if fit_mode == "n":
            sx_l = sy_l = 1.0
            fitted_w_l, fitted_h_l = bw_l, bh_l
        else:
            sx_l, sy_l = svg.compute_fit_scale(bw_l, bh_l, iw_l, ih_l, fit_mode)
            fitted_w_l, fitted_h_l = bw_l * sx_l, bh_l * sy_l

        try:
            p0 = inv_parent.apply_to_point((rx, ry))
            p1 = inv_parent.apply_to_point((rx + shift_x, ry + shift_y))
            shift_lx, shift_ly = p1[0] - p0[0], p1[1] - p0[1]
        except Exception:
            shift_lx = shift_ly = 0.0

        target_x_l = rx_l + ax * rw_l + shift_lx
        target_y_l = ry_l + ay * rh_l + shift_ly
        T_l = svg.build_fit_transform(
            bx=bx_l, by=by_l, bw=bw_l, bh=bh_l,
            target_x=target_x_l, target_y=target_y_l,
            sx=sx_l, sy=sy_l, rot_deg=rot_deg,
            mir_h=mir_h, mir_v=mir_v, anchor=(ax, ay),
        )
        placed = svg.deepcopy_place_local(
            base,
            parent,
            T_l,
            insert_after=(insert_after_elem if insert_after_elem is not None else rect),
            id_prefix=id_prefix,
        )
        placed_bbox = (target_x_l - ax * fitted_w_l, target_y_l - ay * fitted_h_l, fitted_w_l, fitted_h_l)
        if local_transform_spec is not None and placed is not None:
            TFX.apply_transform_spec(root, placed, local_transform_spec, bbox=placed_bbox)
        return (placed, placed_bbox) if return_bbox else placed

    # LOG: final params before place_node
    parent_id = parent.get("id") if parent is not None else None
    _l.d(
        f"{LOG_PREFIX} FA place base='{base_id}' rect='{rect_id}' "
        f"parent='{parent_id}' mode='{place}' "
        f"anchor_key={anchor_key} anchor=({ax},{ay}) "
        f"fit_mode='{fit_mode}' "
        f"sx={sx:.4f} sy={sy:.4f} rot={rot_deg} "
        f"shift=({shift_x:.2f},{shift_y:.2f}) "
        f"target_world=({target_x_world:.2f},{target_y_world:.2f})"
    )

    # 13) Placement and clip
    #
    # For clipped placement we wrap the placed content in a local <g> anchored at the
    # placeholder origin. This keeps the content transform and clip geometry in the same
    # local space and avoids page/card offset drift.
    #
    # If there is NO clip ('!'), we keep the original pipeline with svg.place_node.
    if not getattr(fs, "clip", False):
        # Place using DOCUMENT-SPACE coords.
        # svg.place_node converts to parent local coords.
        placed = svg.place_node(
            base,
            parent,
            bx=bx,
            by=by,
            bw=bw,
            bh=bh,
            target_x=target_x_world,
            target_y=target_y_world,
            sx=sx,
            sy=sy,
            rot_deg=rot_deg,
            mir_h=mir_h,
            mir_v=mir_v,
            anchor=(ax, ay),
            insert_after=(insert_after_elem if insert_after_elem is not None else rect),
            mode=place,
            id_prefix=id_prefix,
        )
        placed_bbox = (target_x_world - ax * fitted_w, target_y_world - ay * fitted_h, fitted_w, fitted_h)
        if local_transform_spec is not None and placed is not None:
            TFX.apply_transform_spec(root, placed, local_transform_spec, bbox=placed_bbox)
        return (placed, placed_bbox) if return_bbox else placed

    # ======== CLIP path ('!'): <g> wrapper + local clip ========
    # In this mode we avoid applying clipPath directly to a transformed <use> (translate/matrix),
    # because in some Inkscape viewers/paths the clip may not "follow" the transformed element.
    # Instead, we create a <g> wrapper anchored to the placeholder origin and clip an inner <g>.
    from lxml import etree

    # Parent CTM (document <- parent) and inverse (parent <- document)
    parent_ctm = inkex.Transform()
    try:
        cur = parent
        chain = []
        while cur is not None:
            tr = cur.get('transform')
            if tr:
                try:
                    chain.append(inkex.Transform(tr))
                except Exception:
                    pass
            cur = cur.getparent()
        for t in reversed(chain):
            parent_ctm = parent_ctm @ t
    except Exception:
        parent_ctm = inkex.Transform()
    try:
        inv_parent = parent_ctm.inverse()
    except Exception:
        inv_parent = inkex.Transform()

    # Placeholder (original) and "inner" (with border) in parent LOCAL coords
    rx_l, ry_l, rw_l, rh_l = _bbox_with_transform(inv_parent, rx, ry, rw, rh)
    ix_l, iy_l, iw_l, ih_l = _bbox_with_transform(inv_parent, inner_x, inner_y, inner_w, inner_h)

    # Shift in parent local coords (vector, not bbox)
    try:
        p0 = inv_parent.apply_to_point((rx, ry))
        p1 = inv_parent.apply_to_point((rx + shift_x, ry + shift_y))
        shift_lx, shift_ly = (p1[0] - p0[0], p1[1] - p0[1])
    except Exception:
        shift_lx = shift_ly = 0.0

    # Target in wrapper LOCAL coords (wrapper = placeholder origin).
    # Keep anchor semantics consistent with the non-clip path:
    # border only affects the fit/scale area, not the anchor reference rect.
    target_x_local_base = ax * rw_l
    target_y_local_base = ay * rh_l
    clip_stage = str(getattr(fs, "clip_stage", "post") or "post").lower()
    clip_pre = bool(getattr(fs, "clip", False)) and clip_stage == "pre"
    # Shift ordering:
    # - post (default): place with shift, then clip (legacy behavior)
    # - pre: clip first, then shift the clipped result
    if clip_pre:
        target_x_local = target_x_local_base
        target_y_local = target_y_local_base
    else:
        target_x_local = target_x_local_base + shift_lx
        target_y_local = target_y_local_base + shift_ly

    # Base bbox: use full visual bbox (including offsets) for clip alignment.
    # visual_bbox(base) is in document coords, which match <defs> user space.
    # Keeping bx/by avoids losing the base's own transform translation.
    try:
        _bx, _by, _bw, _bh = svg.visual_bbox(base)
    except Exception:
        _bx = _by = _bw = _bh = 0.0
    bx_w, by_w, bw_w, bh_w = (float(_bx), float(_by), float(_bw), float(_bh))

    # Scale by mode, now in LOCAL units
    if fit_mode == "n":
        sx_l = sy_l = 1.0
    else:
        sx_l, sy_l = svg.compute_fit_scale(bw_w, bh_w, iw_l, ih_l, fit_mode)

    # Local transform for the <use> inside the wrapper
    T_local = svg.build_fit_transform(
        bx=bx_w, by=by_w, bw=bw_w, bh=bh_w,
        target_x=target_x_local,
        target_y=target_y_local,
        sx=sx_l, sy=sy_l,
        rot_deg=rot_deg,
        mir_h=mir_h,
        mir_v=mir_v,
        anchor=(ax, ay),
    )

    # Final fitted image bbox in wrapper-local coordinates. Transform soft relative
    # to the placed image itself, not to the placeholder/clip rect.
    try:
        _p1 = T_local.apply_to_point((bx_w, by_w))
        _p2 = T_local.apply_to_point((bx_w + bw_w, by_w))
        _p3 = T_local.apply_to_point((bx_w, by_w + bh_w))
        _p4 = T_local.apply_to_point((bx_w + bw_w, by_w + bh_w))
        _xs = [_p1[0], _p2[0], _p3[0], _p4[0]]
        _ys = [_p1[1], _p2[1], _p3[1], _p4[1]]
        image_soft_rect_local = (
            float(min(_xs)),
            float(min(_ys)),
            float(max(_xs) - min(_xs)),
            float(max(_ys) - min(_ys)),
        )
    except Exception:
        image_soft_rect_local = None

    # Create wrapper in parent, anchored to the placeholder ORIGIN (parent-local coords)
    wrapper = etree.Element(inkex.addNS('g', 'svg'))
    wrapper_id = f"fa_clipwrap_{rect_id}_{base_id}".replace('.', '_').replace(':', '_')
    wrapper.set('id', wrapper_id)
    wrapper.set('transform', f"translate({rx_l},{ry_l})")

    # Inner groups:
    # - soft_g hosts the future mask target in a stable local wrapper
    # - clip_g hosts the clip-path and actual placed content
    # For pre-clip stage, apply shift at a parent group so it happens AFTER clip.
    clip_parent = wrapper
    if clip_pre and (abs(shift_lx) > 1e-9 or abs(shift_ly) > 1e-9):
        post_shift_g = etree.SubElement(wrapper, inkex.addNS('g', 'svg'))
        post_shift_g.set('id', f"{wrapper_id}_postshift")
        post_shift_g.set('transform', f"translate({shift_lx},{shift_ly})")
        clip_parent = post_shift_g
    soft_g = etree.SubElement(clip_parent, inkex.addNS('g', 'svg'))
    soft_g_id = f"{wrapper_id}_soft"
    soft_g.set('id', soft_g_id)
    clip_g = etree.SubElement(soft_g, inkex.addNS('g', 'svg'))
    clip_g_id = f"{wrapper_id}_clip"
    clip_g.set('id', clip_g_id)

    # Insert wrapper in the tree (right after rect/insert_after)
    ia = insert_after_elem if insert_after_elem is not None else rect
    try:
        if ia is not None and ia.getparent() is parent:
            parent.insert(parent.index(ia) + 1, wrapper)
        else:
            parent.append(wrapper)
    except Exception:
        parent.append(wrapper)

    clip_use_inner = not (getattr(fs, "border", None) and getattr(fs, "clip", False))
    clip_shape = None
    clip_kind = "none"
    clip_rect_local = None
    tag = str(getattr(rect, "tag", "") or "")
    rid = (rect.get('id') or rect_id or "").strip()
    base_has_image = False
    try:
        if str(getattr(base, "tag", "")).endswith("symbol"):
            base_has_image = bool(base.xpath(".//svg:image", namespaces=svg.NSS))
    except Exception:
        base_has_image = False

    def _shape_transform_wrap_from_rect():
        rect_local = inkex.Transform()
        tr = rect.get('transform')
        if tr:
            try:
                rect_local = inkex.Transform(tr)
            except Exception:
                rect_local = inkex.Transform()
        T_wrap_from_parent = inkex.Transform(f"translate({-rx_l},{-ry_l})")
        return T_wrap_from_parent @ rect_local

    # 1) Prefer exact-placeholder clip via <use href="#id"> when clipping to the
    # full outline. For bitmap-backed symbols we avoid this path and fall through to
    # explicit geometry, which has proven more reliable under clipping.
    try:
        same_inner_as_rect = (
            abs(float(ix_l) - float(rx_l)) < 1e-6 and
            abs(float(iy_l) - float(ry_l)) < 1e-6 and
            abs(float(iw_l) - float(rw_l)) < 1e-6 and
            abs(float(ih_l) - float(rh_l)) < 1e-6
        )
    except Exception:
        same_inner_as_rect = False
    # Do not reuse non-shape placeholders such as <image> / <use> as clip geometry.
    # In practice that can yield an empty/invalid clip and the placed source disappears.
    can_reuse_placeholder_shape = (not base_has_image) and (not any(tag.endswith(t) for t in ('image', 'use')))
    if can_reuse_placeholder_shape and rid and ((not clip_use_inner) or same_inner_as_rect):
        try:
            u = etree.Element(inkex.addNS('use', 'svg'))
            svg.set_href(u, f"#{rid}")
            u.set('transform', f"translate({-rx_l},{-ry_l})")
            clip_shape = u
            clip_kind = "use-shape"
        except Exception:
            clip_shape = None

    # 2) Closed SVG shapes -> clip by geometry transformed from rect-local to wrapper-local.
    if any(tag.endswith(t) for t in ('path', 'polygon', 'circle', 'ellipse')):
        try:
            if clip_shape is None:
                T_wrap_from_rect = _shape_transform_wrap_from_rect()
                if tag.endswith('path'):
                    d = (rect.get('d') or "").strip()
                    if d:
                        s = etree.Element(inkex.addNS('path', 'svg'))
                        s.set('d', d)
                        s.set('transform', str(T_wrap_from_rect))
                        clip_shape = s
                        clip_kind = "path-xf"
                elif tag.endswith('polygon'):
                    pts = (rect.get('points') or "").strip()
                    if pts:
                        s = etree.Element(inkex.addNS('polygon', 'svg'))
                        s.set('points', pts)
                        s.set('transform', str(T_wrap_from_rect))
                        clip_shape = s
                        clip_kind = "polygon-xf"
                elif tag.endswith('circle'):
                    cx = (rect.get('cx') or "").strip()
                    cy = (rect.get('cy') or "").strip()
                    r0 = (rect.get('r') or "").strip()
                    if cx != "" and cy != "" and r0 != "":
                        s = etree.Element(inkex.addNS('circle', 'svg'))
                        s.set('cx', cx)
                        s.set('cy', cy)
                        s.set('r', r0)
                        s.set('transform', str(T_wrap_from_rect))
                        clip_shape = s
                        clip_kind = "circle-xf"
                elif tag.endswith('ellipse'):
                    cx = (rect.get('cx') or "").strip()
                    cy = (rect.get('cy') or "").strip()
                    rx0 = (rect.get('rx') or "").strip()
                    ry0 = (rect.get('ry') or "").strip()
                    if cx != "" and cy != "" and rx0 != "" and ry0 != "":
                        s = etree.Element(inkex.addNS('ellipse', 'svg'))
                        s.set('cx', cx)
                        s.set('cy', cy)
                        s.set('rx', rx0)
                        s.set('ry', ry0)
                        s.set('transform', str(T_wrap_from_rect))
                        clip_shape = s
                        clip_kind = "ellipse-xf"
        except Exception:
            clip_shape = None

    # 3) Rect placeholder -> clip by local rect preserving rounded corners when possible.
    elif tag.endswith('rect'):
        try:
            if clip_use_inner:
                cx = float(ix_l - rx_l)
                cy = float(iy_l - ry_l)
                cw = float(iw_l)
                ch = float(ih_l)
            else:
                cx = 0.0
                cy = 0.0
                cw = float(rw_l)
                ch = float(rh_l)

            s = etree.Element(inkex.addNS('rect', 'svg'))
            s.set('x', f"{cx}")
            s.set('y', f"{cy}")
            s.set('width', f"{cw}")
            s.set('height', f"{ch}")
            clip_rect_local = (float(cx), float(cy), float(cw), float(ch))

            # Preserve corner radii from placeholder rect.
            rx_attr = (rect.get('rx') or '').strip()
            ry_attr = (rect.get('ry') or '').strip()
            try:
                rr_x = float(rx_attr) if rx_attr != '' else None
            except Exception:
                rr_x = None
            try:
                rr_y = float(ry_attr) if ry_attr != '' else None
            except Exception:
                rr_y = None

            # If clipping inner rect, scale radii proportionally to keep visual shape.
            if clip_use_inner and (rw_l > 1e-9) and (rh_l > 1e-9):
                sx_r = max(0.0, cw / float(rw_l))
                sy_r = max(0.0, ch / float(rh_l))
            else:
                sx_r = sy_r = 1.0

            if rr_x is not None:
                s.set('rx', f"{rr_x * sx_r}")
            if rr_y is not None:
                s.set('ry', f"{rr_y * sy_r}")
            clip_shape = s
            clip_kind = "rect-local"
        except Exception:
            clip_shape = None

    # 4) Final fallback: rectangular clip in wrapper coords.
    if clip_shape is None:
        r = etree.Element(inkex.addNS('rect', 'svg'))
        if clip_use_inner:
            fx = float(ix_l - rx_l)
            fy = float(iy_l - ry_l)
            fw = float(iw_l)
            fh = float(ih_l)
            r.set('x', f"{fx}")
            r.set('y', f"{fy}")
            r.set('width', f"{fw}")
            r.set('height', f"{fh}")
        else:
            fx = 0.0
            fy = 0.0
            fw = float(rw_l)
            fh = float(rh_l)
            r.set('x', "0")
            r.set('y', "0")
            r.set('width', f"{fw}")
            r.set('height', f"{fh}")
        clip_rect_local = (fx, fy, fw, fh)
        clip_shape = r
        clip_kind = "rect-fallback"

    # Create/reuse LOCAL clipPath in defs after the final geometry is known.
    root2 = svgdoc.getroot() if hasattr(svgdoc, "getroot") else svgdoc
    defs = svg.ensure_defs(root2)
    clip_id = _clip_def_id_from_shape(clip_kind, clip_shape) or f"clip_{clip_g_id}"
    cp = root2.find(f".//svg:clipPath[@id='{clip_id}']", namespaces=svg.NSS)
    if cp is None:
        cp = etree.SubElement(defs, inkex.addNS('clipPath', 'svg'))
        cp.set('id', clip_id)
        cp.set('clipPathUnits', 'userSpaceOnUse')
        if clip_shape is not None:
            cp.append(clip_shape)
    clip_g.set('clip-path', f"url(#{clip_id})")
    effective_soft_rect_local = image_soft_rect_local
    if image_soft_rect_local is not None and clip_rect_local is not None:
        inter = _rect_intersection(image_soft_rect_local, clip_rect_local)
        if inter is not None:
            effective_soft_rect_local = inter

    if effective_soft_rect_local is not None:
        sx0, sy0, sw0, sh0 = effective_soft_rect_local
        soft_g.set('data-fx-rect', f"{sx0} {sy0} {sw0} {sh0}")
    elif clip_rect_local is not None:
        sx0, sy0, sw0, sh0 = clip_rect_local
        soft_g.set('data-fx-rect', f"{sx0} {sy0} {sw0} {sh0}")

    # Place base inside the clipped group, applying the local transform
    if place == "use":
        placed = svg.clone_as_use(base, clip_g, T_local, insert_after=None)
    elif place == "use+unlink":
        u = svg.clone_as_use(base, clip_g, T_local, insert_after=None)
        placed = svg.unlink_use(u)
    else:
        placed = svg.deepcopy_place(base, clip_g, T_local, insert_after=None, id_prefix=id_prefix)

    # Diagnostic logs
    _l.d(
        f"{LOG_PREFIX} FA clipwrap wrapper='{wrapper_id}' clip_id='{clip_id}' "
        f"clip_kind='{clip_kind}' clip_use_inner={clip_use_inner} clip_stage='{clip_stage}' "
        f"rect_local=({rx_l:.2f},{ry_l:.2f},{rw_l:.2f},{rh_l:.2f}) "
        f"inner_local=({ix_l:.2f},{iy_l:.2f},{iw_l:.2f},{ih_l:.2f}) "
        f"target_local=({target_x_local:.2f},{target_y_local:.2f}) "
        f"bwbh=({bw_w:.2f},{bh_w:.2f}) "
        f"sx={sx_l:.4f} sy={sy_l:.4f} rot={rot_deg}"
    )

    if local_transform_spec is not None and placed is not None:
        TFX.apply_transform_spec(root, placed, local_transform_spec)
    return (placed, None) if return_bbox else placed
