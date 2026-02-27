# [2026-02-18] Chore: remove unused legacy %...% var regex.
# [2026-02-19] Add: split layout gaps into gaps + offset properties.
# [2026-02-20] Add: allow oversized templates to proceed with split-board fallback slots.
# [2026-02-20] Fix: allow declared templates without header-matching ids.
# -*- coding: utf-8 -*-
import log as LOG
_l = LOG
import os, sys, re
from copy import deepcopy
from types import SimpleNamespace
from typing import List


sys.path.append(os.path.dirname(__file__))

import inkex
import const as CONST
import prefs
import svg as SVG
import layouts as LYT
import dsl as DSL
import sources as SRC
import snippets as SNP
import text as TXT
import marks as MK

import dataset as DS
import render as REN

# --------------------- util / parsing ---------------------

TEXT_LIKE = {
    inkex.addNS('text','svg'), inkex.addNS('tspan','svg'),
    inkex.addNS('flowRoot','svg'), inkex.addNS('flowPara','svg'),
    inkex.addNS('textPath','svg'), 'text','tspan','flowRoot','flowPara','textPath'
}
_HEADER_RE      = None  # (PRUNE) legacy; no longer used

# (PRUNE) No se usan ya:
# _PRESET_WITH_TAIL_RE = re.compile(r"\{\s*[^}]*\s*\}")
# _TAIL_RE             = re.compile(r"\.\s*L\s*\{[^}]*\}\s*$", re.IGNORECASE)


class EngineContext(SimpleNamespace):
    """Shared mutable context across pipeline phases."""
    pass

def run(self, __version__):
    """Run the refactored DeckMaker pipeline (entrypoint called from deckmaker.py)."""
    prefs.reload()
    _l.get_logger(self, console_level=self.options.log_level, file_level='global', tag_override='deckmaker')
    _l.i(f"start DeckMaker {__version__} — {__file__}")
    _l.s("START DeckMaker")

    root = self.svg
    SVG.ensure_xlink_ns(root)

    try:
        _doc_path = self._document_path_or_abort()
    except Exception:
        _doc_path = None

    SVG.fix_all_paths(root)

    SM = SRC.SourceManager(root, _doc_path, project_root=None)

    _l.s("DATASET: load")
    datasets = DS.load_datasets(self, _doc_path)
    if not datasets:
        raise inkex.AbortExtension("Dataset sin cabecera válida.")
    _l.s("DATASET: loaded")

    # ---------------- Global comment directives (snippets + spritesheets) ----------------
    # Multi-section datasets: the loader can split a single sheet into multiple sections.
    # Only the first section typically carries the initial comment block (where users define
    # snippets like "# :TTS(...) = ..." and spritesheets like "# @sp1 = ...").
    #
    # Required behavior (engine-level): treat the first non-empty comment block as GLOBAL and
    # reuse it for all subsequent sections. Local comment blocks (if any) are merged on top.

    def _comments_nonempty(c_lines):
        if not c_lines:
            return False
        for rr in (c_lines or []):
            try:
                if isinstance(rr, (list, tuple)):
                    if not rr:
                        continue
                    s = str(rr[0] or "").strip()
                else:
                    s = str(rr or "").strip()
            except Exception:
                continue
            if s:
                return True
        return False

    def _expand_comment_lines_with_snips(c_lines, snip_reg):
        """Expand snippets inside comment lines, but never touch snippet definition lines."""
        if not c_lines:
            return []
        if not snip_reg:
            return list(c_lines)
        out = []
        for rr in (c_lines or []):
            if rr is None:
                continue
            if isinstance(rr, (list, tuple)):
                if not rr:
                    continue
                c0 = str(rr[0] or "")
                c0s = c0.lstrip()
                # DO NOT expand inside snippet definition lines.
                if c0s.startswith("#") and c0s[1:].lstrip().startswith(":"):
                    out.append(list(rr))
                    continue
                rr2 = list(rr)
                rr2[0] = SNP.expand_snippets_in_text(c0, snip_reg)
                out.append(rr2)
            else:
                s0 = str(rr)
                s0s = s0.lstrip()
                if s0s.startswith("#") and s0s[1:].lstrip().startswith(":"):
                    out.append(s0)
                    continue
                out.append(SNP.expand_snippets_in_text(s0, snip_reg))
        return out

    global_section_idx = None
    global_comment_lines = []
    for _i, _ds in enumerate(datasets, start=1):
        _cl = _ds.get("comments", []) or []
        if _comments_nonempty(_cl):
            global_section_idx = _i
            global_comment_lines = _cl
            break

    # Load global snippets once.
    try:
        global_snip_reg = SNP.load_definitions_from_comments(global_comment_lines or [])
        _l.i(f"[snippets.global] defs={len(global_snip_reg)} → {sorted(global_snip_reg.keys())}")
    except Exception as ex:
        _l.w(f"[snippets.global] error cargando defs: {ex}")
        global_snip_reg = {}

    # Register global spritesheets once.
    try:
        global_comment_lines_exp = _expand_comment_lines_with_snips(global_comment_lines or [], global_snip_reg)
        global_spritesheets = SM.register_spritesheets_from_comments(global_comment_lines_exp or [], px_per_mm=float(root.unittouu("1mm"))) or {}
    except Exception as ex:
        _l.w(f"[spritesheets.global] scan/register failed: {ex}")
        global_spritesheets = {}

    # Global counters across dataset sections (ids must remain unique in the SVG).
    use_seq = [0]
    next_n = SVG.scan_max_pnp_suffix(root) + 1
    placed_total = 0

    # ---------------- Page cursor (v0.9+) ----------------
    # We never start placing content on any pre-existing page of the input SVG.
    # By default, we append after the last existing page (respecting the original SVG).
    # The cursor is global across dataset sections and can be moved via Layout at=/a=/@.
    try:
        _px_per_mm0 = float(root.unittouu("1mm"))
    except Exception:
        _px_per_mm0 = 1.0
    nv0 = SVG.namedview(root)
    if nv0 is None:
        raise inkex.AbortExtension("No <sodipodi:namedview> found; cannot create pages")
    _pages0 = SVG.list_existing_pages_px(root)
    if not _pages0:
        w0, h0 = SVG.page_size_px(root)
        SVG.add_inkscape_page_mm(nv0, 0, 0, w0, h0, "page1", {})
        _pages0 = SVG.list_existing_pages_px(root)
    # Global page cursor is 0-based (like planner.page_index).
    start_page_index = int(len(_pages0))

    for ds_idx, ds0 in enumerate(datasets, start=1):
        ds_meta = ds0.get("meta", {}) or {}
        headers = ds0.get("headers", []) or []
        rows_data = ds0.get("rows", []) or []
        comment_lines = ds0.get("comments", []) or []

        if not headers:
            _l.w(f"[datasets] #{ds_idx}: sin cabecera válida; skip.")
            continue
        if not rows_data:
            _l.w(f"[datasets] #{ds_idx}: sin filas útiles; skip.")
            continue

        _l.i(f"----- DATASET SECTION #{ds_idx}/{len(datasets)} -----")
        placed = 0

        # Units needed early (e.g., spritesheets registration). Do not delay until after scans.
        px_per_mm = _px_per_mm0


        # ---- SNIPPETS (global + local) ----
        # Global snippet defs are loaded once (see above). Local defs (if any) override global.
        try:
            local_snip_reg = SNP.load_definitions_from_comments(comment_lines or [])
            if local_snip_reg:
                _l.i(f"[snippets.local] defs={len(local_snip_reg)} → {sorted(local_snip_reg.keys())}")
            else:
                _l.i("[snippets.local] defs=0")
        except Exception as ex:
            _l.w(f"[snippets.local] error cargando defs: {ex}")
            local_snip_reg = {}

        # Merge (local overrides global).
        snip_reg = {}
        try:
            if global_snip_reg:
                snip_reg.update(global_snip_reg)
            if local_snip_reg:
                snip_reg.update(local_snip_reg)
        except Exception:
            snip_reg = dict(local_snip_reg or {})

        if global_snip_reg:
            _l.i(f"[snippets] using merged: global={len(global_snip_reg)} local={len(local_snip_reg)} total={len(snip_reg)}")
        else:
            _l.i(f"[snippets] using local only: total={len(snip_reg)}")
        if snip_reg:
            for ridx, row in enumerate(rows_data, start=1):
                # Expand snippets in positional cells (and only in known string meta fields).
                try:
                    cells = row.get('cells') if isinstance(row, dict) else None
                except Exception:
                    cells = None
                if isinstance(cells, list):
                    for ci, v in enumerate(list(cells)):
                        if v is None:
                            continue
                        try:
                            cells[ci] = SNP.expand_snippets_in_text(str(v), snip_reg)
                        except Exception as ex:
                            _l.w(f"[snippets] fallo expand row#{ridx} cell[{ci}]: {ex}")
                # Meta fields (keep behavior conservative)
                for k in ('__dm_layout__','__dm_marks__'):
                    if isinstance(row, dict) and k in row and row.get(k) is not None:
                        try:
                            row[k] = SNP.expand_snippets_in_text(str(row.get(k)), snip_reg)
                        except Exception as ex:
                            _l.w(f"[snippets] fallo expand row#{ridx} meta '{k}': {ex}")
        else:
            _l.i("[snippets] sin definiciones; no hay expansión")

        # ---- WEB SOURCES PREFETCH (http/https) ----
        # Schedule downloads in background so render can proceed in parallel.
        try:
            SM.prefetch_dataset_rows(rows_data or [])
        except Exception as ex:
            _l.w(f"[sources.web] prefetch failed: {ex}")

        # ---- SPRITESHEETS (global + local) ----
        # Expand snippets inside local comment directives (but not snippet-definition lines).
        local_comment_lines_exp = _expand_comment_lines_with_snips(comment_lines or [], snip_reg)

        local_spritesheets = {}
        if ds_idx == (global_section_idx or -1):
            # This section is the source of the GLOBAL block; it was already registered globally.
            local_spritesheets = {}
        else:
            try:
                local_spritesheets = SM.register_spritesheets_from_comments(local_comment_lines_exp or [], px_per_mm=px_per_mm) or {}
            except Exception as ex:
                _l.w(f"[spritesheets.local] scan/register failed: {ex}")
                local_spritesheets = {}

        spritesheets = {}
        try:
            if global_spritesheets:
                spritesheets.update(global_spritesheets)
            if local_spritesheets:
                spritesheets.update(local_spritesheets)
        except Exception:
            spritesheets = dict(local_spritesheets or {})

        _l.i(f"[spritesheets] merged: global={len(global_spritesheets or {})} local={len(local_spritesheets or {})} total={len(spritesheets or {})}")

        _l.s("PROTOTYPE: detect")

        # Output container (inocuo): keep all generated content under a dedicated root-level container.
        #
        # User preference: the outer container should be a *layer* (not a generic <g>),
        # while the "Output" bucket can be a normal group inside that layer.
        #
        # IMPORTANT: we only ever create/find *top-level* containers under <svg>, never inside templates.
        def _find_or_create_root_group(group_id: str, label: str = None):
            g = root.find(".//*[@id='%s']" % group_id)
            if g is None:
                g = inkex.Group()
                g.set('id', group_id)
                if label is not None:
                    g.set(inkex.addNS('label', 'inkscape'), label)
                root.append(g)
            return g

        pnpink_root = self._find_or_create_layer(root, "PnPInk")
        out_layer   = _find_or_create_root_group('pnpink-output', 'PnPInk Output')

        # Ensure out_layer is a direct child of the PnPInk layer. This only re-parents PnPInk groups.
        if out_layer.getparent() is not pnpink_root:
            try:
                if out_layer.getparent() is not None:
                    out_layer.getparent().remove(out_layer)
            except Exception:
                pass
            pnpink_root.append(out_layer)
        px_per_mm    = float(root.unittouu("1mm"))
        page_gap_px  = float(root.unittouu("1cm"))

        # templates_bbox via dataset marker {{t=...}} in column A (handled in _matrix_to_datasets)
        # --- templates (main + declared columns) ---
        templates_bbox_ids = []
        template_cols = []
        overlay_template_cols = []  # legacy
        try:
            templates_bbox_ids = list((ds_meta or {}).get('templates_bbox_ids') or [])
            template_cols = list((ds_meta or {}).get('template_cols') or [])
            overlay_template_cols = list((ds_meta or {}).get('overlay_template_cols') or [])
        except Exception:
            templates_bbox_ids = []
            template_cols = []
            overlay_template_cols = []

        # Contract: main template bbox id (single) comes from {{t=...}}; overlays come from header columns {t=...}
        main_bbox_id = templates_bbox_ids[0] if templates_bbox_ids else None

        declared_template_root = None
        declared_bbox_node = None
        declared_bbox_id = None
        overlay_templates = []  # list of {bbox_id, template_root, bbox_node, control_key}

        def _find_template_root_for_bbox(bid: str):
            if not bid:
                return None, None
            n = root.find(".//*[@id='%s']" % bid)
            if n is None:
                return None, None

            # If the bbox is ungrouped (direct child of <svg>), do NOT scale it to the full document.
            # Treat it as a single-element template (the bbox itself). This avoids hangs.
            try:
                if n.getparent() is root:
                    _l.w(
                        f"[templates] bbox id '{bid}' está en root (no pertenece a un <g> de primer nivel). "
                        "PnPInk lo tratará como template de un único elemento. Consejo: agrupa el template con Ctrl+G "
                        "para evitar errores."
                    )
                    return n, n
            except Exception:
                pass

            # Find the template root (<g>) under the "stop boundary" (root or layer)
            cur = n
            tmpl = None
            while cur is not None:
                try:
                    par = cur.getparent()
                except Exception:
                    par = None

                if par is None:
                    break
                if par is root:
                    break
                try:
                    if (
                        hasattr(par, 'tag') and isinstance(par.tag, str) and par.tag.endswith('g')
                        and (par.get(CONST.INK_GROUPMODE) == 'layer')
                    ):
                        break
                except Exception:
                    pass

                if hasattr(cur, 'tag') and isinstance(cur.tag, str) and cur.tag.endswith('g'):
                    tmpl = cur
                cur = par

            if tmpl is None:
                try:
                    par = n.getparent()
                except Exception:
                    par = None
                if par is not None and hasattr(par, 'tag') and isinstance(par.tag, str) and par.tag.endswith('g'):
                    if par.get(CONST.INK_GROUPMODE) != 'layer':
                        tmpl = par

            return tmpl, n

        # 1) Resolve main template (if declared)
        if main_bbox_id:
            tmpl, n = _find_template_root_for_bbox(main_bbox_id)
            if tmpl is None or n is None:
                _l.w(f"[templates] main bbox id '{main_bbox_id}' not found in SVG or not under any <g>")
            else:
                declared_template_root = tmpl
                declared_bbox_node = n
                declared_bbox_id = main_bbox_id
                _l.i(f"[templates] main template_root='{tmpl.get('id') or '<noid>'}' bbox_id='{main_bbox_id}'")

        # 2) Resolve declared templates from header columns (left-to-right order)
        # If template_cols is missing (older dataset loader), fall back to legacy overlay_template_cols.
        if not template_cols and overlay_template_cols:
            template_cols = [dict(c, mods=[]) for c in (overlay_template_cols or [])]

        overlay_templates = []       # slot-anchored, front pass (legacy behavior)
        back_templates = []          # slot-anchored, back pass
        page_templates = []          # page-anchored, front pass
        page_back_templates = []     # page-anchored, back pass

        seen_templates = set()
        for col in (template_cols or []):
            bid = (col or {}).get('bbox_id')
            ckey = (col or {}).get('key')
            if not bid:
                continue
            tmpl, n = _find_template_root_for_bbox(bid)
            if tmpl is None or n is None:
                _l.w(f"[templates] overlay bbox id '{bid}' not found in SVG or not under any <g>")
                continue
            tid = tmpl.get('id') or '<noid>'
            if tid in seen_templates:
                _l.w(f"[templates] bbox id '{bid}' cae en template '{tid}' ya seleccionado; se descarta")
                continue
            seen_templates.add(tid)

            mods = set((col or {}).get('mods') or [])
            entry = {
                'bbox_id': bid,
                'bbox_node': n,
                'template_root': tmpl,
                'control_key': ckey,
                'mods': sorted(list(mods)),
            }

            if '@page' in mods and '@back' in mods:
                page_back_templates.append(entry)
                _l.i(f"[templates] page+back template_root='{tid}' bbox_id='{bid}' control_col='{ckey or ''}'")
            elif '@page' in mods:
                page_templates.append(entry)
                _l.i(f"[templates] page template_root='{tid}' bbox_id='{bid}' control_col='{ckey or ''}'")
            elif '@back' in mods:
                back_templates.append(entry)
                _l.i(f"[templates] back template_root='{tid}' bbox_id='{bid}' control_col='{ckey or ''}'")
            else:
                overlay_templates.append(entry)
                _l.i(f"[templates] overlay template_root='{tid}' bbox_id='{bid}' control_col='{ckey or ''}'")

        # Note: layout is always computed with the main template. Overlays are placed on top by default (~5).

        # detectar prototipo a partir de headers
        target_nodes: List[inkex.BaseElement] = []
        for h in headers:
            tid = (re.match(r"^([^\[]+)", h).group(1) if re.match(r"^([^\[]+)", h) else "").strip()
            if not tid or tid.startswith("clone_"): continue
            n = root.find(".//*[@id='%s']" % tid)
            if n is None:
                n = root.find(".//*[@data-field='%s']" % tid)
            if n is not None: target_nodes.append(n)
        proto_root = None
        if not target_nodes:
            if declared_template_root is not None:
                proto_root = declared_template_root
            else:
                raise inkex.AbortExtension("No header-matching elements found (id o data-field).")

        def _ancestors_inclusive(n):
            cur = n; chain=[]
            while cur is not None:
                chain.append(cur)
                try: cur = cur.getparent()
                except Exception: cur = None
            return chain
        def _is_ancestor_of(a, n):
            cur = n
            while cur is not None:
                if cur is a: return True
                try: cur = cur.getparent()
                except Exception: cur = None
            return False

        ancestors = _ancestors_inclusive(target_nodes[0])
        cand_groups = [a for a in ancestors
                       if hasattr(a, "tag") and isinstance(a.tag, str) and a.tag.endswith("g")]

        if proto_root is None:
            for g in cand_groups:
                if all(_is_ancestor_of(g, n) for n in target_nodes):
                    proto_root = g
                    break
            if proto_root is None and cand_groups:
                proto_root = cand_groups[0]
            if proto_root is None:
                raise inkex.AbortExtension("Agrupa los elementos de la carta bajo un mismo grupo.")



        # If a template was declared via {{t=...}}, it takes precedence over header-based prototype detection.
        # IMPORTANT (inocuo): do not move/reparent/mutate any user element.
        if declared_template_root is not None:
            proto_root = declared_template_root

        # Anti-hang heuristic (ungrouped templates):
        # If the main bbox ({{t=...}}) hangs directly from <svg>, the implicit "template" cannot
        # be the full document. For the copy/replace pipeline to work without hanging,
        # we build a synthetic in-memory wrapper that absorbs:
        #   - the main bbox, and
        #   - HEADER ids that are also in root,
        # excluyendo bboxes declarados en columnas de templates (overlays/@back/@page).
        _main_bbox_in_root = False
        try:
            _main_bbox_in_root = (declared_bbox_node is not None and declared_bbox_node.getparent() is root)
        except Exception:
            _main_bbox_in_root = False

        if _main_bbox_in_root and declared_bbox_id and declared_bbox_node is not None:
            header_ids = []
            for n in (target_nodes or []):
                try:
                    nid = n.get('id')
                except Exception:
                    nid = None
                if nid and nid not in header_ids:
                    header_ids.append(nid)

            other_bbox_ids = []
            try:
                for c in (template_cols or []):
                    bid = (c or {}).get('bbox_id')
                    if bid and bid not in other_bbox_ids:
                        other_bbox_ids.append(bid)
            except Exception:
                other_bbox_ids = []

            declared_bbox_set = set([declared_bbox_id] + other_bbox_ids)

            absorb_ids = set([declared_bbox_id])
            for hid in header_ids:
                if hid in declared_bbox_set and hid != declared_bbox_id:
                    continue
                try:
                    hn = root.find(".//*[@id='%s']" % hid)
                except Exception:
                    hn = None
                try:
                    if hn is not None and hn.getparent() is root:
                        absorb_ids.add(hid)
                except Exception:
                    pass

            # Preserve original order in root
            ordered = []
            for ch in list(root):
                try:
                    cid = ch.get('id')
                except Exception:
                    cid = None
                if cid and cid in absorb_ids:
                    ordered.append(ch)

            if len(ordered) > 1:
                tmp_group = inkex.Group()
                tmp_group.set('id', f"pnpink_tpl_{declared_bbox_id}")
                for ch in ordered:
                    # IMPORTANT: match historical behavior (fix_templates4)
                    # We must deep-copy the selected root-level nodes to build a synthetic template.
                    # Using element.copy() here can lose inkex element class/metadata in some envs,
                    # and later resolution by id/data-origid becomes unreliable.
                    try:
                        tmp_group.append(deepcopy(ch))
                    except Exception:
                        # As a last resort, try lxml copy.
                        try:
                            tmp_group.append(ch.copy())
                        except Exception:
                            pass

                declared_template_root = tmp_group
                proto_root = tmp_group

                absorbed_list = [n.get('id') for n in ordered if n is not None and n.get('id')]
                _l.w(
                    "[templates] bbox principal desagrupado: elementos del HEADER sueltos en root absorbidos "
                    f"en el template principal: {absorbed_list}. "
                    "Consejo: agrupa con Ctrl+G para evitar este warning."
                )
        # medir carta base
        template_anchor_x = None
        template_anchor_y = None
        if declared_bbox_node is not None:
            bbox_id = declared_bbox_node.get('id')

            # Special case (root): the template bbox must ALWAYS come from the declared element in the
            # original document. If we measure a wrapper with text, pick_anchor_in() may choose a <text>
            # and collapse size (grid "clumped").
            if _main_bbox_in_root:
                bbm = declared_bbox_node.bounding_box()
                aw, ah = float(bbm.width), float(bbm.height)
                template_anchor_x, template_anchor_y = float(bbm.left), float(bbm.top)

                try:
                    anc_id = declared_bbox_node.get('id') if hasattr(declared_bbox_node, 'get') else None
                except Exception:
                    anc_id = None
                _l.i(
                    f"[templates] measured_bbox_node_id='{anc_id}' aw_px={aw:.2f} ah_px={ah:.2f} "
                    f"left_px={template_anchor_x:.2f} top_px={template_anchor_y:.2f} (root_bbox_direct)"
                )

            else:
                # IMPORTANT:
                #   The instance cloning path flattens proto_root's group transform into children (see inst creation below).
                #   If we measure bbox on the original SVG node without applying the same flattening, anchor coords will differ
                #   and placement will drift (typically by a constant offset, sometimes a whole page).
                #
                # Therefore, measure on a temporary deep-copied + flattened proto_root, and locate the bbox element by id
                # inside that temp instance.
                temp = deepcopy(proto_root)
                REN._flatten_group_transform(temp)

                # IMPORTANT:
                # Inkex bbox resolution can become non-deterministic if duplicate IDs exist in the live document.
                # Because we temporarily append this clone into the SVG to measure it, we MUST uniquify IDs
                # inside the measuring clone. We keep a stable lookup via data-origid.
                try:
                    SVG.uniquify_all_ids_in_scope(temp, "_dmM", root.get_unique_id)
                except Exception:
                    pass

                measure_layer = self._find_or_create_layer(root, "_DeckMaker Measuring")
                measure_layer.append(temp)

                temp_bbox = None
                if bbox_id:
                    # Prefer data-origid mapping (survives uniquify)
                    try:
                        temp_bbox = temp.find(".//*[@data-origid='%s']" % bbox_id)
                    except Exception:
                        temp_bbox = None
                    if temp_bbox is None:
                        # Fallback: raw id lookup (if uniquify failed)
                        try:
                            temp_bbox = temp.find(".//*[@id='%s']" % bbox_id)
                        except Exception:
                            temp_bbox = None

                if temp_bbox is None:
                    # Fallback: measure whole temp (should not happen in normal use)
                    temp_anchor = SVG.pick_anchor_in(temp)
                else:
                    temp_anchor = SVG.pick_anchor_in(temp_bbox)

                bbm = temp_anchor.bounding_box()
                aw, ah = float(bbm.width), float(bbm.height)
                template_anchor_x, template_anchor_y = float(bbm.left), float(bbm.top)

                # DEBUG: log which node/group we actually measured for template sizing
                try:
                    anc_id = temp_anchor.get('id') if hasattr(temp_anchor, 'get') else None
                except Exception:
                    anc_id = None
                _l.i(
                    f"[templates] measured_bbox_node_id='{anc_id}' aw_px={aw:.2f} ah_px={ah:.2f} "
                    f"left_px={template_anchor_x:.2f} top_px={template_anchor_y:.2f}"
                )
                # Also log the bbox of the highest template group in the original SVG (may include text/filters)
                try:
                    if declared_template_root is not None:
                        gbb = declared_template_root.bounding_box()
                        gid = declared_template_root.get('id') if hasattr(declared_template_root, 'get') else None
                        _l.i(
                            f"[templates] template_root_bbox id='{gid}' w_px={float(gbb.width):.2f} h_px={float(gbb.height):.2f} "
                            f"left_px={float(gbb.left):.2f} top_px={float(gbb.top):.2f}"
                        )
                    if declared_bbox_node is not None:
                        nbb = declared_bbox_node.bounding_box()
                        nid = declared_bbox_node.get('id') if hasattr(declared_bbox_node, 'get') else None
                        _l.i(
                            f"[templates] declared_bbox_node_bbox id='{nid}' w_px={float(nbb.width):.2f} h_px={float(nbb.height):.2f} "
                            f"left_px={float(nbb.left):.2f} top_px={float(nbb.top):.2f}"
                        )
                except Exception as e:
                    _l.w(f"[templates] bbox debug failed: {e}")

                # cleanup
                try:
                    measure_layer.remove(temp)
                except Exception:
                    pass
                if len(measure_layer) == 0 and measure_layer.getparent() is not None:
                    measure_layer.getparent().remove(measure_layer)

            _l.d(
                f"card_size aw={aw:.2f}px ah={ah:.2f}px (templates_bbox='{bbox_id}', "
                f"anchor=({template_anchor_x:.2f},{template_anchor_y:.2f}))"
            )
        else:
            temp = deepcopy(proto_root)
            REN._flatten_group_transform(temp)

            # Same rationale as above: avoid duplicate IDs while measuring.
            try:
                SVG.uniquify_all_ids_in_scope(temp, "_dmM", root.get_unique_id)
            except Exception:
                pass
            measure_layer = self._find_or_create_layer(root, "_DeckMaker Measuring")
            measure_layer.append(temp)
            temp_anchor = SVG.pick_anchor_in(temp)
            bbm = temp_anchor.bounding_box()
            aw, ah = float(bbm.width), float(bbm.height)
            measure_layer.remove(temp)

            try:
                anc_id = temp_anchor.get('id') if hasattr(temp_anchor, 'get') else None
            except Exception:
                anc_id = None
            _l.i(f"[templates] measured_bbox_node_id='{anc_id}' aw_px={aw:.2f} ah_px={ah:.2f} (fallback_whole_template)")
            try:
                if declared_template_root is not None:
                    gbb = declared_template_root.bounding_box()
                    gid = declared_template_root.get('id') if hasattr(declared_template_root, 'get') else None
                    _l.i(f"[templates] template_root_bbox id='{gid}' w_px={float(gbb.width):.2f} h_px={float(gbb.height):.2f} left_px={float(gbb.left):.2f} top_px={float(gbb.top):.2f}")
            except Exception as e:
                _l.w(f"[templates] bbox debug failed (fallback): {e}")
            if len(measure_layer) == 0 and measure_layer.getparent() is not None:
                measure_layer.getparent().remove(measure_layer)
            _l.d(f"card_size aw={aw:.2f}px ah={ah:.2f}px (anchor='{temp_anchor.get('id') or temp_anchor.tag}')")

        _l.s("PROTOTYPE: measured")

        nv = SVG.namedview(root)
        if nv is None:
            raise inkex.AbortExtension("No <sodipodi:namedview> found; cannot create pages")
        pages = SVG.list_existing_pages_px(root)
        if not pages:
            w_px, h_px = SVG.page_size_px(root)
            SVG.add_inkscape_page_mm(nv, 0, 0, w_px, h_px, "page1", {})
            pages = SVG.list_existing_pages_px(root)

        doc_page_mm = (pages[0]["w"]/px_per_mm, pages[0]["h"]/px_per_mm)
        _l.s("PAGES: init")

        # Estado de layout
        page = LYT.PageSpec()
        card = LYT.CardSpec()
        layout = LYT.LayoutSpec()
        gaps = LYT.GapsMM()

        # apply initial preset if provided in --preset
        opt_preset = (self.options.preset or "").strip()
        if opt_preset:
            pg = LYT.parse_and_resolve_page(opt_preset, page, doc_page_mm)
            page = pg

        # Dataset-level presets from the marker row tail (column A)
        # (We keep this additive; if absent, nothing changes.)
        header_page_block = (ds_meta.get("header_page_block") or "").strip()
        if header_page_block:
            try:
                page = LYT.parse_and_resolve_page(header_page_block, page, doc_page_mm)
            except Exception as ex:
                _l.w(f"[marks] header Page parse failed: {ex}")

        header_layout_block = (ds_meta.get("header_layout_block") or "").strip()
        if header_layout_block:
            try:
                ls0 = DSL.parse_layout_block(header_layout_block)
                page, card, layout, gaps = LYT.apply_layout_spec((page, card, layout, gaps), ls0)
            except Exception as ex:
                _l.w(f"[marks] header Layout parse failed: {ex}")

        # Marks tail stored as raw DSL (M{...}); parsed to DSL.MarksSpec when needed.
        header_marks_block = (ds_meta.get("header_marks_block") or "").strip()
        marks_current = None
        if header_marks_block:
            try:
                marks_current = DSL.parse_marks_block(header_marks_block)
            except Exception as ex:
                _l.w(f"[marks] header Marks parse failed: {ex}")
                marks_current = None

        # ---------------------------------------------------------------
        # Marks: buffer per page to decide "external vs internal" by
        # physical adjacency (whether there is a card in the neighbor cell).
        # We must not infer this from coordinates/min-max/thresholds, because
        # the user may use negative d, holes, non-trivial ordering, etc.
        # We flush marks when leaving a page (jump_page), so each page has a
        # complete occupancy map.
        # ---------------------------------------------------------------
        _marks_pending_by_page = {}  # page_index -> list[job]

        def _slot_index_to_rc(within: int, plan_obj, layout_obj):
            """Map slot_index within page to (r,c) in the logical grid."""
            cols = int(getattr(plan_obj, 'cols', 0) or 0)
            rows = int(getattr(plan_obj, 'rows', 0) or 0)
            if cols <= 0 or rows <= 0:
                return 0, 0
            sweep_rows_first = bool(getattr(layout_obj, 'sweep_rows_first', True))
            if sweep_rows_first:
                r0 = within // cols
                c0 = within % cols
            else:
                c0 = within // rows
                r0 = within % rows

            # apply inversions to get physical adjacency correct
            if bool(getattr(layout_obj, 'invert_rows', False)):
                r0 = (rows - 1) - r0
            if bool(getattr(layout_obj, 'invert_cols', False)):
                c0 = (cols - 1) - c0
            return int(r0), int(c0)

        def _gaps_has_offsets(layout_obj) -> bool:
            """True only if gaps params 3..6 are non-zero."""
            try:
                k = getattr(layout_obj, 'gaps', None)
                if isinstance(k, (list, tuple)) and len(k) >= 6:
                    for t in list(k)[2:6]:
                        if t is None:
                            continue
                        # treat as measure; any non-zero means stagger/offset
                        v = float(SVG.measure_to_mm(t, base_mm=None))
                        if abs(v) > 1e-9:
                            return True
            except Exception:
                return False
            return False

        def _flush_marks_for_page(page_idx: int):
            jobs = _marks_pending_by_page.get(int(page_idx)) or []
            if not jobs:
                return

            # Special case: hextile/hextiles marks must be computed at the PAGE level.
            # Slot-based rectangular marks are geometrically wrong for hex tiles.
            try:
                any_hex = False
                for _j in jobs:
                    sshape = (_j.get('smart_shape') or '').strip().lower()
                    if sshape in ('hextile', 'hextiles'):
                        any_hex = True
                        break
                if any_hex:
                    ms0 = jobs[0].get('ms')
                    MK.render_hextiles_page_marks(
                        root,
                        jobs=jobs,
                        px_per_mm=float(px_per_mm),
                        style_id=getattr(ms0, 'style', None) if ms0 is not None else None,
                        layer_label=(getattr(ms0, 'layer', None) if ms0 is not None else None) or "marks",
                        b_tokens=getattr(ms0, 'b', None) if ms0 is not None else None,
                        length_tokens=getattr(ms0, 'length', None) if ms0 is not None else None,
                        d_tokens=getattr(ms0, 'd', None) if ms0 is not None else None,
                    )
                    try:
                        del _marks_pending_by_page[int(page_idx)]
                    except Exception:
                        pass
                    return
            except Exception as ex:
                _l.w(f"[marks] hextiles render failed: {ex}")

            # Build occupancy in (r,c)
            occ = set()
            for j in jobs:
                occ.add((j['r'], j['c']))

            for j in jobs:
                r, c = j['r'], j['c']
                rows = j['rows']
                cols = j['cols']

                has_up = (r - 1 >= 0) and ((r - 1, c) in occ)
                has_dn = (r + 1 < rows) and ((r + 1, c) in occ)
                has_lt = (c - 1 >= 0) and ((r, c - 1) in occ)
                has_rt = (c + 1 < cols) and ((r, c + 1) in occ)

                # "External" means no physical adjacent card in that direction.
                edge_top = (not has_up)
                edge_bottom = (not has_dn)
                edge_left = (not has_lt)
                edge_right = (not has_rt)

                try:
                    ms = j['ms']
                    MK.render_slot_marks(
                        root,
                        slot_bbox_px=j['bbox'],
                        px_per_mm=float(px_per_mm),
                        style_id=getattr(ms, 'style', None),
                        layer_label=getattr(ms, 'layer', None) or "marks",
                        b_tokens=getattr(ms, 'b', None),
                        d_tokens=getattr(ms, 'd', None),
                        length_tokens=getattr(ms, 'length', None),
                        gaps_has_offsets=bool(j.get('gaps_has_offsets', False)),
                        edge_top=edge_top,
                        edge_right=edge_right,
                        edge_bottom=edge_bottom,
                        edge_left=edge_left,
                    )
                except Exception as ex:
                    _l.w(f"[marks] render failed: {ex}")

            try:
                del _marks_pending_by_page[int(page_idx)]
            except Exception:
                pass

        def _compute_plan_for(resolved, page_w_px, page_h_px):
            mg = SVG.coerce_margins_mm(resolved.page.margins_mm())
            cx = mg.left  * px_per_mm
            cy = mg.top   * px_per_mm
            cw = page_w_px  - (mg.left + mg.right) * px_per_mm
            ch = page_h_px  - (mg.top  + mg.bottom) * px_per_mm

            # carta (px)
            if resolved.card and (resolved.card.name or resolved.card.width_mm or resolved.card.height_mm):
                cw_px, ch_px = LYT.resolve_card_size_px(resolved.card, aw, ah, px_per_mm)
            else:
                cw_px, ch_px = aw, ah

            # NOTE (Marks v0.1): Marks.b ONLY affects where the cut marks are drawn.
            # It must NOT alter card/shape sizing or grid planning.

            # gaps: mm → px
            gh_mm = float(resolved.gaps.h or 0.0)
            gv_mm = float(resolved.gaps.v or 0.0)
            gh_px = gh_mm * px_per_mm
            gv_px = gv_mm * px_per_mm

            _l.d(f"[plan.inspect] page_px=({page_w_px:.2f}×{page_h_px:.2f}) content_px=({cx:.2f},{cy:.2f},{cw:.2f},{ch:.2f})")
            _l.d(f"[plan.inspect] card_px=({cw_px:.2f}×{ch_px:.2f}) gaps=({gh_mm:.2f}mm,{gv_mm:.2f}mm) → ({gh_px:.2f}px,{gv_px:.2f}px)")

            gaps_px6 = None
            if LYT.layout_gaps_tokens(resolved.layout):
                # Full gaps6 (gx,gy,w1,h1,w2,h2) in px. Layouts owns the semantics and unit handling.
                gaps_px6 = LYT.gaps6_to_px(LYT.layout_gaps_tokens(resolved.layout), cw_px, ch_px, px_per_mm)

            plan = LYT.plan_grid(
                page_w_px, page_h_px,
                cw_px, ch_px,
                gaps_px=(gh_px, gv_px),
                gaps_px6=gaps_px6,
                layout=resolved.layout,
                content_origin_px=(cx, cy),
                content_wh_px=(cw, ch)
            )
            if getattr(plan, 'per_page', 0) <= 0:
                oversize = (cw_px > cw + 1e-6) or (ch_px > ch + 1e-6)
                if oversize:
                    _l.i("[split_boards] plan fallback: oversize card -> 1 slot")
                    try:
                        plan.slots = [(0.0, 0.0, float(cw), float(ch))]
                        plan.cols = 1
                        plan.rows = 1
                        plan.per_page = 1
                        plan.content_x = float(cx)
                        plan.content_y = float(cy)
                        plan.left = 0.0
                        plan.top = 0.0
                    except Exception:
                        pass
            slots = [(x, y, w, h) for (x, y, w, h) in plan.slots]
            return plan, slots

        def _ensure_card_base_mm(card: 'LYT.CardSpec', layout: 'LYT.LayoutSpec'):
            """Provide a *base* card size only when required to resolve gaps percentages.

            Kerf supports '%' tokens. Those must be resolved against a base card size in mm.

            Rules:
              1) If a preset is active (card.name), layouts.resolve() will inject the correct mm size.
                 Do not override.
              2) If an explicit size is already present (width_mm+height_mm), do nothing.
              3) Only if layout gaps/offset contain '%' and the card size is otherwise undefined, we fall back
                 to the measured template_bbox size (aw/ah in px).
            """
            try:
                if card is None or layout is None:
                    return

                # Preset size should win (and will be applied inside layouts.resolve()).
                if getattr(card, 'name', None):
                    return

                if getattr(card, 'width_mm', None) is not None and getattr(card, 'height_mm', None) is not None:
                    return

                gaps = getattr(layout, 'gaps', None) or []
                if not gaps:
                    return

                needs_percent_base = any(isinstance(tok, str) and ('%' in tok) for tok in gaps)
                if not needs_percent_base:
                    return

                if getattr(card, 'width_mm', None) is None:
                    card.width_mm = float(aw) / float(px_per_mm)
                if getattr(card, 'height_mm', None) is None:
                    card.height_mm = float(ah) / float(px_per_mm)

            except Exception:
                # Never fail here; layouts will still validate if it truly needs base sizes.
                return

        def _tok_str(tok):
            """Render a DSL token to a stable string (avoid '1.0' noise)."""
            if tok is None:
                return ""
            s = str(tok).strip()
            # Normalize trivial floats like '1.0' -> '1'
            m = re.match(r"^(-?\d+)\.0$", s)
            if m:
                return m.group(1)
            return s

        def _expr_add(base_tok: str, delta_tok: str) -> str:
            base_tok = _tok_str(base_tok)
            delta_tok = _tok_str(delta_tok)
            if not delta_tok or delta_tok == "0" or delta_tok == "+0" or delta_tok == "-0":
                return base_tok or "0"
            if not base_tok or base_tok == "0" or base_tok == "+0" or base_tok == "-0":
                return delta_tok
            if delta_tok.startswith(("+", "-")):
                return f"{base_tok}{delta_tok}"
            return f"{base_tok}+{delta_tok}"

        def _detect_hex_orientation(proto_root_node, bbox_node):
            """Return 'flat' | 'pointy' if we can detect a hex in the template."""
            # 1) Prefer bbox_node if it's a path.
            try:
                if bbox_node is not None and str(getattr(bbox_node, 'tag', '')).endswith('path'):
                    d = bbox_node.get('d') or ''
                    T = None
                    try:
                        T = bbox_node.composed_transform()
                    except Exception:
                        try:
                            T = inkex.Transform(bbox_node.get('transform') or '')
                        except Exception:
                            T = None
                    pts = SVG.path_characteristic_points(d, T)
                    if pts and len(pts) == 6:
                        ang = SVG.base_angle_deg(pts)
                        if ang is None:
                            return None
                        if abs(ang) <= 5.0:
                            return 'flat'
                        if abs(abs(ang) - 30.0) <= 5.0:
                            return 'pointy'
            except Exception:
                pass

            # 2) Fallback: scan the flattened proto group for a 6-vertex path.
            try:
                temp = deepcopy(proto_root_node)
                REN._flatten_group_transform(temp)
                # best effort: avoid duplicate ids while measuring; not required for geometry.
                try:
                    SVG.uniquify_all_ids_in_scope(temp, "_dmH", root.get_unique_id)
                except Exception:
                    pass
                for el in temp.iter():
                    try:
                        tag = str(getattr(el, 'tag', '') or '')
                    except Exception:
                        tag = ''
                    if not tag.endswith('path'):
                        continue
                    d = el.get('d') or ''
                    pts = SVG.path_characteristic_points(d, None)
                    if not pts or len(pts) != 6:
                        continue
                    ang = SVG.base_angle_deg(pts)
                    if ang is None:
                        continue
                    if abs(ang) <= 5.0:
                        return 'flat'
                    if abs(abs(ang) - 30.0) <= 5.0:
                        return 'pointy'
            except Exception:
                pass
            return None

        def _apply_smart_shape_gaps(card_obj, layout_obj):
            """Auto-adjust gaps for smart hex shapes (MVP)."""
            try:
                sp = (getattr(layout_obj, 'smart_shape', None) or '').strip().lower()
                if sp not in ('hexgrid', 'hextile', 'hextiles'):
                    return

                orient = _detect_hex_orientation(proto_root, declared_bbox_node)
                if orient is None:
                    # If we can't detect, default to pointy for hextile(s) and do nothing for hexgrid.
                    orient = 'pointy' if sp in ('hextile', 'hextiles') else None

                # Persist the detected orientation for downstream consumers (e.g. Marks{} hextiles).
                # This is intentionally simple (MVP): marks must not try to re-infer orientation
                # from noisy geometry when DeckMaker already determined it for smart gaps.
                try:
                    layout_obj.smart_hex_orient = orient or None
                except Exception:
                    pass

                # Avoid re-applying on every resolve(): apply_layout_spec() clears
                # _smart_applied_key whenever the raw gaps changes.
                already = getattr(layout_obj, '_smart_applied_key', None)
                if already and isinstance(already, tuple) and len(already) >= 2:
                    if already[0] == sp and already[1] == (orient or ''):
                        return

                user_seq = [ _tok_str(x) for x in LYT.layout_gaps_tokens(layout_obj) ]
                key = (sp, orient or '', tuple(user_seq))

                # Store original user gaps for debugging (optional)
                try:
                    layout_obj._smart_user_gaps = list(user_seq)
                except Exception:
                    pass

                # Ensure minimum length helpers
                def _pad(seq, n):
                    seq = list(seq)
                    if len(seq) < n:
                        seq += ['0'] * (n - len(seq))
                    return seq

                if sp == 'hexgrid':
                    if orient not in ('flat', 'pointy'):
                        return
                    seq4 = _pad(user_seq, 4)
                    if orient == 'flat':
                        # a += -25%, d += +50%
                        seq4[0] = _expr_add(seq4[0], '-25%')
                        seq4[3] = _expr_add(seq4[3], '+50%')
                    else:
                        # b += -25%, c += +50%
                        seq4[1] = _expr_add(seq4[1], '-25%')
                        seq4[2] = _expr_add(seq4[2], '+50%')
                    layout_obj.gaps = seq4[:2]
                    layout_obj.offset = seq4[2:4]

                else:
                    # hextile/hextiles: base gaps6 pattern + user deltas
                    if orient == 'flat':
                        base6 = ['-50%', '100%', '0', '-100%', '0', '100%']
                    else:
                        base6 = ['100%', '-50%', '-100%', '0', '100%', '0']

                    # Special case: user gaps expressed as a single distance A (k=A).
                    # For hextiles we must keep the existing "recortable" stagger lattice, and
                    # only add the derived terms from A:
                    #   B = A/(2*sqrt(3))
                    #   C = A/2
                    # This must be emitted as *simple* gaps tokens (no parentheses, no division),
                    # because SVG.measure_to_mm only supports +/- token expressions.
                    #
                    # Pointy-top (A horizontal):
                    #   k=[100%+A, -50%+B, -100%-C, 0]
                    # Flat-top (A vertical):
                    #   k=[-50%+B, 100%+A, 0, -100%-C]
                    # (w2/h2 are inferred later by layouts._gaps6_mm as -w1/-h1)
                    try:
                        us0 = list(user_seq)
                        if len(us0) == 2 and us0[1] == us0[0]:
                            us0 = [us0[0]]
                        if len(us0) == 1:
                            A_tok = _tok_str(us0[0])
                            if A_tok and ('%' not in A_tok):
                                import math
                                A_mm = float(SVG.measure_to_mm(A_tok, base_mm=None))
                                if abs(A_mm) > 1e-9:
                                    B_mm = A_mm / (2.0 * math.sqrt(3.0))
                                    C_mm = A_mm / 2.0
                                    # Format: stable compact decimals (no trailing zeros noise)
                                    B_tok = _tok_str(f"{B_mm:.6g}")
                                    C_tok = _tok_str(f"{C_mm:.6g}")
                                    if orient == 'flat':
                                        seq4 = [
                                            _expr_add('-50%', B_tok),
                                            _expr_add('100%', A_tok),
                                            '0',
                                            _expr_add('-100%', f"-{C_tok}"),
                                        ]
                                    else:
                                        seq4 = [
                                            _expr_add('100%', A_tok),
                                            _expr_add('-50%', B_tok),
                                            _expr_add('-100%', f"-{C_tok}"),
                                            '0',
                                        ]
                                        layout_obj.gaps = seq4[:2]
                                        layout_obj.offset = seq4[2:4]
                                        layout_obj._smart_applied_key = (sp, orient or '')
                                        return
                    except Exception:
                        # If anything about k=A parsing fails, fall through to the generic base+delta logic.
                        pass

                    # Expand user tokens to 6, matching spec:
                    #   len=2 => apply only to first two entries (gx,gy)
                    us = list(user_seq)
                    if len(us) == 0:
                        us6 = ['0', '0', '0', '0', '0', '0']
                    elif len(us) == 1:
                        us6 = [us[0], us[0], '0', '0', '0', '0']
                    elif len(us) == 2:
                        us6 = [us[0], us[1], '0', '0', '0', '0']
                    elif len(us) == 4:
                        us6 = [us[0], us[1], us[2], us[3], '0', '0']
                    else:
                        us6 = (us + ['0','0','0','0','0','0'])[:6]

                    out6 = []
                    for i in range(6):
                        out6.append(_expr_add(base6[i], us6[i]))
                    layout_obj.gaps = out6[:2]
                    layout_obj.offset = out6[2:]

                # Store only the identifying part; raw gaps changes will clear it.
                layout_obj._smart_applied_key = (sp, orient or '')

            except Exception as ex:
                _l.w(f"[smart-shape] failed: {ex}")
                return

        def _resolve_with_base(page, card, layout, gaps, doc_page_mm):
            _apply_smart_shape_gaps(card, layout)
            _ensure_card_base_mm(card, layout)
            return LYT.resolve(page, card, layout, gaps, doc_page_mm)

        current = _resolve_with_base(page, card, layout, gaps, doc_page_mm)
        planner = REN.CardPlanner(
            root=root, nv=nv, pages=pages,
            px_per_mm=px_per_mm, page_gap_px=page_gap_px,
            doc_page_mm=doc_page_mm,
            current_resolved=current,
            ensure_page_for_fn=REN.ensure_page_for,
            plan_fn=_compute_plan_for
        )
        planner.sync_page_attrs()
        # Multi-dataset: start this dataset on the current global page cursor.
        if start_page_index > 0:
            planner.page_index = int(start_page_index)
            planner.slot_index = 0
            # Ensure target page exists and recompute plan for that page.
            REN.ensure_page_for(planner.page_index, planner.pages, planner.nv, planner.current,
                                 planner.doc_page_mm, planner.page_gap_px, planner.px_per_mm)
            pw, ph = planner.page_size_px()
            planner.plan, planner.local_slots = planner._compute_plan_for(planner.current, pw, ph)
            planner.sync_page_attrs()
        _l.s("PLANNER: init")
        _l.i(f"Grid {planner.plan.cols}x{planner.plan.rows}, gaps {planner.current.gaps.h}×{planner.current.gaps.v} mm; slots/page {planner.slots_per_page()}")

        # ---- RENDER + MARKS (moved out of engine.py) ----
        _ctx = EngineContext(
            ext=self, root=root,
            SM=SM, datasets=datasets, ds_idx=ds_idx, ds_meta=ds_meta, headers=headers, rows_data=rows_data,
            use_seq=use_seq, next_n=next_n, placed_total=placed_total, start_page_index=start_page_index,
            planner=planner, proto_root=proto_root, out_layer=out_layer,
            declared_bbox_id=declared_bbox_id,
            overlay_templates=overlay_templates,
            back_templates=back_templates,
            page_templates=page_templates,
            page_back_templates=page_back_templates,
            declared_template_root=declared_template_root,
            declared_bbox_node=declared_bbox_node,
            # Legacy core locals used by the render tail. These must exist to preserve the original
            # control-flow/side-effects when render logic was extracted into render.py.
            page=page, card=card, layout=layout, gaps=gaps, doc_page_mm=doc_page_mm,
            resolve_with_base=_resolve_with_base,
            marks_pending_by_page=_marks_pending_by_page, flush_marks_for_page=_flush_marks_for_page,
            header_marks_current=marks_current,
            spritesheets=spritesheets,
        )
        REN.render_phase(_ctx)
        next_n = _ctx.next_n
        placed_total = _ctx.placed_total
        start_page_index = _ctx.start_page_index
        continue
    _l.i(f"[datasets] total placed={placed_total} across {len(datasets)} dataset section(s).")

    import traceback as _tb
    try:
        res = TXT.inline_place_icons(out_layer, show_debug_rects=False, source_manager=SM, doc_path=_doc_path)
        _l.i(
            f"[deckmaker.text] ONE-PASS placed={res.icons_placed} icons across output; "
            f"sources={sorted(res.used_sources)}"
        )
    except Exception as ex:
        _l.w(f"[deckmaker.text] inline_icons ONE-PASS failed: {ex}")
        _l.w("[deckmaker.text] traceback:\n" + _tb.format_exc())

    _l.s("END DeckMaker")
    _l.close()
