#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
"""
fit_anchor.py — PnPInk (v1.2.2)

Place a node onto a rect using dsl.py fit syntax.
There is NO legacy parsing here. dsl.py handles everything.
"""
# Changelog: clip to original rect when border expands inner fit area.

import re
import inkex
import svg
import dsl as DSL
import log as LOG
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

# ================================================================
# Entry point used by DeckMaker / sources
# ================================================================

def apply_to_by_ids(scope, base_id, rect_id, ops_full, place_mode="clone", rect_elem=None, **kwargs):
    """
    Coloca `base_id` sobre `rect_id` aplicando las ops de fit del DSL.

    DeckMaker suele llamar así:
        apply_to_by_ids(scope, base_id, rect_id, ops, place="clone", rect_elem=rect)
    """
    # compat with deckmaker: it comes as `place=...`
    if "place" in kwargs and kwargs["place"]:
        place_mode = kwargs["place"]

    # overrides used by DeckMaker (overlays): parent/insert_after
    parent_elem = kwargs.get('parent_elem')
    insert_after_elem = kwargs.get('insert_after_elem')

    root = _fa_root_of(scope)
    svgdoc = root

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

    # 3) normalizar ops → FitSpec usando dsl.py
    fs = None
    if isinstance(ops_full, DSL.FitSpec):
        fs = ops_full
    else:
        ops_s = (ops_full or "").strip()
        try:
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

# we could use rect_with_pad, but there the order is (t,r,b,l) as well
    if pad_top or pad_right or pad_bottom or pad_left:
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

    # DEBUG: alternate bbox via symbol
    sb = None # = svg.bbox_use_symbol(base)
    if sb is not None:
        sbx, sby, sbw, sbh = sb
        _l.d(
            f"[fit_anchor] DEBUG base='{base_id}' "
            f"use_bbox=({bx:.2f},{by:.2f},{bw:.2f},{bh:.2f}) "
            f"sym_bbox=({sbx:.2f},{sby:.2f},{sbw:.2f},{sbh:.2f})"
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

    rot_deg = float(getattr(fs, "rotate", 0.0) or 0.0)
    mir = getattr(fs, "mirror", None)
    mir_h = (mir == "h")
    mir_v = (mir == "v")
    # mirror induced by border WxH (negative width/height) composes with explicit mirror via XOR
    mir_h = bool(mir_h) ^ bool(border_mir_h)
    mir_v = bool(mir_v) ^ bool(border_mir_v)

    # 8) optional shift
    shift_x = shift_y = 0.0
    if getattr(fs, "shift", None) and isinstance(fs.shift, (list, tuple)) and len(fs.shift) >= 2:
        sx_raw, sy_raw = fs.shift[0], fs.shift[1]
        shift_x = svg.parse_len_px(svgdoc, str(sx_raw)) if isinstance(sx_raw, str) else float(sx_raw)
        shift_y = svg.parse_len_px(svgdoc, str(sy_raw)) if isinstance(sy_raw, str) else float(sy_raw)

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

    # 12) parent where we place the clone
    #     - By default: same parent as the rect
    #     - Override (DeckMaker overlays): parent_elem
    # DEBUG (Phase 1 headers-dup/multivalue): detect when rect is orphan and parent falls back to root
    try:
        if parent_elem is None and rect is not None and rect.getparent() is None:
            _l.w(f"[dbg.fa_fallback_root] rect orphan: base_id='{base_id}' rect_id='{rect_id}' rect_elem_id='{rect.get('id')}'")
    except Exception:
        pass
    parent = parent_elem if parent_elem is not None else (rect.getparent() if rect.getparent() is not None else root)

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
    # HISTORICAL ISSUE: applying clipPath directly to a <use> with translate/matrix
    # can misalign the clip (clip in one space, node in another). The robust solution
    # for '!' we clip inside a <g> wrapper anchored to the placeholder.
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
        )
        return placed

    # ======== CLIP path ('!'): <g> wrapper + local clip ========
    # In this mode we avoid applying clipPath directly to a transformed <use> (translate/matrix),
    # because in some Inkscape viewers/paths the clip may not "follow" the transformed element.
    # Instead, we create a <g> wrapper anchored to the placeholder origin and clip an inner <g>.
    from lxml import etree

    # Helpers: doc/world bbox -> parent local bbox using inverse parent CTM
    def _bbox_world_to_local(inv_T: inkex.Transform, x, y, w, h):
        p1 = inv_T.apply_to_point((x, y))
        p2 = inv_T.apply_to_point((x + w, y))
        p3 = inv_T.apply_to_point((x, y + h))
        p4 = inv_T.apply_to_point((x + w, y + h))
        xs = [p1[0], p2[0], p3[0], p4[0]]
        ys = [p1[1], p2[1], p3[1], p4[1]]
        lx, ly = min(xs), min(ys)
        return (lx, ly, max(xs) - lx, max(ys) - ly)

    # Parent CTM (document <- parent) and inverse (parent <- document)
    # Full-walk: compose ancestor transforms to avoid inconsistencies.
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
    rx_l, ry_l, rw_l, rh_l = _bbox_world_to_local(inv_parent, rx, ry, rw, rh)
    ix_l, iy_l, iw_l, ih_l = _bbox_world_to_local(inv_parent, inner_x, inner_y, inner_w, inner_h)

    # Shift in parent local coords (vector, not bbox)
    try:
        p0 = inv_parent.apply_to_point((rx, ry))
        p1 = inv_parent.apply_to_point((rx + shift_x, ry + shift_y))
        shift_lx, shift_ly = (p1[0] - p0[0], p1[1] - p0[1])
    except Exception:
        shift_lx = shift_ly = 0.0

    # Target in wrapper LOCAL coords (wrapper = placeholder origin).
    # Use the "inner" (with border) to position inside the effective area.
    inner_off_x = ix_l - rx_l
    inner_off_y = iy_l - ry_l
    target_x_local = inner_off_x + (ax * iw_l) + shift_lx
    target_y_local = inner_off_y + (ay * ih_l) + shift_ly

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

    # Create wrapper in parent, anchored to the placeholder ORIGIN (parent-local coords)
    wrapper = etree.Element(inkex.addNS('g', 'svg'))
    wrapper_id = f"fa_clipwrap_{rect_id}_{base_id}".replace('.', '_').replace(':', '_')
    wrapper.set('id', wrapper_id)
    wrapper.set('transform', f"translate({rx_l},{ry_l})")

    # Inner group to which we apply the clip
    clip_g = etree.SubElement(wrapper, inkex.addNS('g', 'svg'))
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

    # Create LOCAL clipPath: rect inside wrapper
    root2 = svgdoc.getroot() if hasattr(svgdoc, "getroot") else svgdoc
    defs = svg.ensure_defs(root2)
    clip_id = f"clip_{clip_g_id}"
    cp = root2.find(f".//svg:clipPath[@id='{clip_id}']", namespaces=svg.NSS)
    if cp is None:
        cp = etree.SubElement(defs, inkex.addNS('clipPath', 'svg'))
        cp.set('id', clip_id)
        cp.set('clipPathUnits', 'userSpaceOnUse')
        r = etree.SubElement(cp, inkex.addNS('rect', 'svg'))
    else:
        r = None
        for ch in list(cp):
            if hasattr(ch, 'tag') and str(ch.tag).endswith('rect'):
                r = ch
                break
        if r is None:
            for ch in list(cp):
                try:
                    cp.remove(ch)
                except Exception:
                    pass
            r = etree.SubElement(cp, inkex.addNS('rect', 'svg'))

    # Clip rect in wrapper coords (0,0 at placeholder origin)
    clip_use_inner = not (getattr(fs, "border", None) and getattr(fs, "clip", False))
    if clip_use_inner:
        r.set('x', f"{ix_l - rx_l}")
        r.set('y', f"{iy_l - ry_l}")
        r.set('width', f"{iw_l}")
        r.set('height', f"{ih_l}")
    else:
        r.set('x', "0")
        r.set('y', "0")
        r.set('width', f"{rw_l}")
        r.set('height', f"{rh_l}")
    clip_g.set('clip-path', f"url(#{clip_id})")

    # Place base inside the clipped group, applying the local transform
    if place == "use":
        placed = svg.clone_as_use(base, clip_g, T_local, insert_after=None)
    elif place == "use+unlink":
        u = svg.clone_as_use(base, clip_g, T_local, insert_after=None)
        placed = svg.unlink_use(u)
    else:
        placed = svg.deepcopy_place(base, clip_g, T_local, insert_after=None, id_prefix="af")

    # Diagnostic logs
    _l.d(
        f"{LOG_PREFIX} FA clipwrap wrapper='{wrapper_id}' clip_id='{clip_id}' "
        f"rect_local=({rx_l:.2f},{ry_l:.2f},{rw_l:.2f},{rh_l:.2f}) "
        f"inner_local=({ix_l:.2f},{iy_l:.2f},{iw_l:.2f},{ih_l:.2f}) "
        f"target_local=({target_x_local:.2f},{target_y_local:.2f}) "
        f"bwbh=({bw_w:.2f},{bh_w:.2f}) "
        f"sx={sx_l:.4f} sy={sy_l:.4f} rot={rot_deg}"
    )

    return placed

