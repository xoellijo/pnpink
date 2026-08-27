# -*- coding: utf-8 -*-
import log as LOG
_l = LOG
import os, sys, re, time
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
import gradients as GRD
import text as TXT
import marks as MK
import dataset_header as DHEAD

import dataset as DS
import gui as PROGRESS
import render as REN

# --------------------- util / parsing ---------------------

def _runtime_python_dir() -> str:
    """Folder containing the Python entry point currently running PnPInk."""
    try:
        entry = (sys.argv[0] or "").strip()
        if entry and os.path.isfile(entry):
            return os.path.dirname(os.path.abspath(entry))
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


class EngineContext(SimpleNamespace):
    """Shared mutable context across pipeline phases."""
    pass


DM_OUTPUT_CHUNK_THRESHOLD_PAGES = 24


def _resolve_dm_output_path(dm_output_raw: str, doc_path: str | None) -> str | None:
    out = str(dm_output_raw or "").strip()
    if not out:
        if not doc_path:
            return None
        try:
            abs_doc = os.path.abspath(doc_path)
            base_dir = os.path.dirname(abs_doc)
            stem, ext = os.path.splitext(os.path.basename(abs_doc))
            ext = ext or ".svg"
            return os.path.normpath(os.path.join(base_dir, f"{stem}_output{ext}"))
        except Exception:
            return None
    if os.path.isabs(out):
        return os.path.normpath(out)
    if doc_path:
        try:
            base = os.path.dirname(os.path.abspath(doc_path))
            return os.path.normpath(os.path.join(base, out))
        except Exception:
            pass
    return os.path.normpath(out)


def _write_svg_atomic(doc, out_path: str) -> None:
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(out_path)
    tmp = os.path.join(out_dir, f".{base}.tmp")
    try:
        SVG.ensure_xlink_ns(doc.getroot())
    except Exception:
        pass
    try:
        raw = inkex.etree.tostring(doc.getroot(), encoding="UTF-8", xml_declaration=True)
        raw = re.sub(br'\s+ns\d+:xlink="http://www\.w3\.org/1999/xlink"', b"", raw)
        raw = re.sub(br'\s+xmlns:ns\d+="xmlns"', b"", raw)
        with open(tmp, "wb") as fh:
            fh.write(raw)
    except Exception:
        doc.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, out_path)


def run(self, __version__, *, text_query_service=None):
    """Run the DeckMaker render pipeline for the Tkinter app flow."""
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

    fixed_imgs = SVG.absolutize_all_linked_images(root, _doc_path, prefer="fileuri")
    if fixed_imgs:
        _l.i(f"[images] absolutized linked images: {fixed_imgs}")

    SVG.fix_all_paths(root)

    # SourceManager is created after reading dataset directives (_DM_*), so it can
    # place run-scoped symbols under a per-run defs bucket.
    SM = None

    _l.s("DATASET: load")
    dataset_load_started = time.perf_counter()
    preloaded_datasets = getattr(self, "_dm_preloaded_datasets", None)
    if preloaded_datasets is not None:
        datasets = preloaded_datasets
        _l.i("[datasets] using preloaded dataset for output render")
    else:
        datasets = DS.load_datasets(self, _doc_path)
    if not datasets:
        raise inkex.AbortExtension("Dataset has no valid header.")
    _l.i("[datasets] load_ms=%.1f", (time.perf_counter() - dataset_load_started) * 1000.0)
    try:
        translated_headers = DHEAD.translate_datasets(datasets, root)
        if translated_headers:
            _l.i(f"[datasets] translated {translated_headers} header label alias(es) to SVG id(s)")
    except Exception as ex:
        _l.w(f"[datasets] header label alias translation skipped: {ex}")
    _l.s("DATASET: loaded")

    def _iter_comment_first_cells(dss):
        for ds in (dss or []):
            for rr in (ds.get("comments", []) or []):
                try:
                    if isinstance(rr, (list, tuple)):
                        if not rr:
                            continue
                        s = str(rr[0] or "")
                    else:
                        s = str(rr or "")
                except Exception:
                    continue
                if s:
                    yield s

    def _parse_dm_directives(dss):
        out = {}
        rx = re.compile(r"(_DM_[A-Za-z0-9_]+)\s*=\s*([^\s#]+)")
        for s0 in _iter_comment_first_cells(dss):
            s = str(s0).strip()
            if not s:
                continue
            if s.startswith("#"):
                s = s[1:].strip()
            for m in rx.finditer(s):
                out[m.group(1)] = m.group(2)
        return out

    def _dm_scan_existing(root0):
        out = set()
        rx = re.compile(r"^_DM(\d{2})_(?:output|defs)$")
        for n in root0.iter():
            i = (n.get('id') or '').strip()
            m = rx.match(i)
            if not m:
                continue
            try:
                out.add(int(m.group(1)))
            except Exception:
                pass
        try:
            nv = SVG.namedview(root0)
            if nv is not None:
                for pg in list(nv.xpath("./inkscape:page", namespaces=SVG.NSS)):
                    tag = str(pg.get("pnpink_dm_gen") or "")
                    m = re.match(r"^_DM(\d{2})$", tag)
                    if m:
                        out.add(int(m.group(1)))
        except Exception:
            pass
        return out

    def _dm_remove_generation(root0, n: int):
        ids = [f"_DM{n:02d}_output", f"_DM{n:02d}_defs"]
        for rid in ids:
            try:
                hits = root0.xpath(f".//*[@id='{rid}']")
            except Exception:
                hits = []
            for e in (hits or []):
                p = e.getparent()
                if p is not None:
                    try:
                        p.remove(e)
                    except Exception:
                        pass
        try:
            target = f"_DM{n:02d}"
            hits = root0.xpath(f".//*[@pnpink_dm_gen='{target}']")
        except Exception:
            hits = []
        for e in (hits or []):
            p = e.getparent()
            if p is not None:
                try:
                    p.remove(e)
                except Exception:
                    pass
        # Remove pages tagged as generated by this DM iteration.
        try:
            nv = SVG.namedview(root0)
            if nv is not None:
                target = f"_DM{n:02d}"
                pages = list(nv.xpath("./inkscape:page", namespaces=SVG.NSS))
                for pg in pages:
                    try:
                        if (pg.get("pnpink_dm_gen") or "") == target:
                            par = pg.getparent()
                            if par is not None:
                                par.remove(pg)
                    except Exception:
                        continue
        except Exception:
            pass

    dm_directives = _parse_dm_directives(datasets)
    if dm_directives:
        try:
            _l.i(f"[dm] directives: {dm_directives}")
        except Exception:
            pass
    dm_reset_to_raw = str(dm_directives.get("_DM_reset_to", "") or "").strip()
    dm_output_raw = str(dm_directives.get("_DM_output", "") or "").strip()
    dm_reset_to = None
    if dm_reset_to_raw != "":
        try:
            dm_reset_to = max(0, min(99, int(dm_reset_to_raw)))
        except Exception:
            dm_reset_to = None
            _l.w(f"[dm] invalid _DM_reset_to='{dm_reset_to_raw}' (ignored)")

    if not bool(getattr(self, "_dm_output_disabled", False)):
        out_path = _resolve_dm_output_path(dm_output_raw, _doc_path)
        if not out_path:
            _l.w(f"[dm_output] could not resolve output path (directive={dm_output_raw!r}, doc_path={_doc_path!r})")
        else:
            try:
                if dm_output_raw:
                    _l.i(f"[dm_output] using explicit output: '{out_path}'")
                else:
                    _l.i(f"[dm_output] using default output: '{out_path}'")
                out_dir = os.path.dirname(out_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                raw_svg = inkex.etree.tostring(self.document)
                clone_doc = inkex.load_svg(raw_svg)
                old_doc = self.document
                old_svg = self.svg
                _missing = object()
                old_preloaded = getattr(self, "_dm_preloaded_datasets", _missing)
                self.document = clone_doc
                self.svg = clone_doc.getroot()
                self._dm_output_disabled = True
                self._dm_preloaded_datasets = datasets
                try:
                    run(self, __version__, text_query_service=text_query_service)
                finally:
                    if old_preloaded is _missing:
                        try:
                            delattr(self, "_dm_preloaded_datasets")
                        except Exception:
                            pass
                    else:
                        self._dm_preloaded_datasets = old_preloaded
                    self._dm_output_disabled = False
                    self.document = old_doc
                    self.svg = old_svg
                try:
                    clone_root = clone_doc.getroot()
                    clone_root.set(inkex.addNS("docname", "sodipodi"), os.path.basename(out_path))
                except Exception:
                    pass
                try:
                    import svg_chunks as SVGCHUNKS

                    force_chunk_output = bool(prefs.get_split_svg_output(False))
                    split_mode = prefs.get_split_svg_mode("limits")
                    split_parts = prefs.get_split_svg_parts() if split_mode == "parts" else None
                    split_limit_pages = prefs.get_split_svg_limit_pages() if split_mode == "limits" else None
                    split_limit_records = prefs.get_split_svg_limit_records() if split_mode == "limits" else None
                    split_limit_mb = prefs.get_split_svg_chunk_mb_optional() if split_mode == "limits" else None
                    chunk_target_bytes = (int(split_limit_mb) * 1024 * 1024) if split_limit_mb else 0
                    analysis = SVGCHUNKS.analyze_output_doc(
                        clone_doc,
                        source_svg_path=_doc_path,
                        analysis_label=out_path,
                        absolutize_images=False,
                    )
                    page_slices = list(analysis.get("pages") or [])
                    total_est_bytes = sum(int(item.est_bytes or 0) for item in page_slices)
                    page_count = len(page_slices)
                    total_records = sum(int(getattr(item, "record_count", 0) or 0) for item in page_slices)
                    split_triggered = (
                        (split_parts is not None and int(split_parts or 0) > 1)
                        or page_count >= int(DM_OUTPUT_CHUNK_THRESHOLD_PAGES)
                        or (chunk_target_bytes > 0 and total_est_bytes >= int(chunk_target_bytes))
                        or (split_limit_pages is not None and page_count > int(split_limit_pages))
                        or (split_limit_records is not None and total_records > int(split_limit_records))
                    )
                    use_chunk_output = force_chunk_output and split_triggered
                    _l.i(
                        f"[dm_output] analyzed external render pages={page_count} "
                        f"fixed_images={int(analysis.get('fixed_images') or 0)} "
                        f"est_bytes={total_est_bytes} chunked={'yes' if use_chunk_output else 'no'} "
                        f"forced={'yes' if force_chunk_output else 'no'} split_mode={split_mode} "
                        f"target_bytes={chunk_target_bytes} target_pages={int(split_limit_pages or 0)} "
                        f"target_records={int(split_limit_records or 0)} target_parts={int(split_parts or 0)}"
                    )
                    if use_chunk_output:
                        try:
                            full_doc = inkex.load_svg(inkex.etree.tostring(clone_doc.getroot()))
                            full_info = SVGCHUNKS.prepare_full_output_doc(full_doc, source_svg_path=_doc_path, absolutize_images=False)
                            _write_svg_atomic(full_doc, out_path)
                            _l.i(
                                f"[dm_output] wrote full external render: '{out_path}' "
                                f"pages={page_count} "
                                f"fixed_images={int(full_info.get('fixed_images') or 0)}"
                            )
                        except Exception as ex:
                            _l.w(f"[dm_output] write full output failed for '{out_path}': {ex}")
                        chunk_info = SVGCHUNKS.write_output_chunks_from_doc(
                            clone_doc,
                            out_path,
                            source_svg_path=_doc_path,
                            target_chunk_bytes=chunk_target_bytes,
                            target_pages=split_limit_pages,
                            target_records=split_limit_records,
                            target_parts=split_parts,
                            analysis=analysis,
                            absolutize_images=False,
                        )
                        chunk_count = int(chunk_info.get("chunk_count") or 0)
                        chunk_dir = str(chunk_info.get("chunk_dir") or "")
                        manifest_path = str(chunk_info.get("manifest_path") or "")
                        _l.i(
                            f"[dm_output] wrote chunked external render chunks={chunk_count} "
                            f"dir='{chunk_dir}' manifest='{manifest_path}'"
                        )
                        return False
                    norm_info = SVGCHUNKS.normalize_output_doc(clone_doc, source_svg_path=_doc_path, absolutize_images=False)
                    _l.i(
                        f"[dm_output] normalized external render pages={int(norm_info.get('page_count') or 0)} "
                        f"fixed_images={int(norm_info.get('fixed_images') or 0)}"
                    )
                except Exception as ex:
                    _l.w(f"[dm_output] normalize output failed for '{out_path}': {ex}")
                try:
                    import svg_chunks as SVGCHUNKS
                    SVGCHUNKS.cleanup_output_chunks(out_path)
                except Exception:
                    pass
                _write_svg_atomic(clone_doc, out_path)
                _l.i(f"[dm_output] wrote external render: '{out_path}'")
                return False
            except Exception as ex:
                try:
                    import traceback
                    _l.w(f"[dm_output] external render failed for '{out_path}': {ex}\n{traceback.format_exc()}")
                except Exception:
                    _l.w(f"[dm_output] external render failed for '{out_path}': {ex}")

    existing_dm = _dm_scan_existing(root)
    if dm_reset_to is not None:
        for n in sorted(existing_dm):
            if n > dm_reset_to:
                _dm_remove_generation(root, n)
        existing_dm = _dm_scan_existing(root)
        _l.i(f"[dm] reset_to={dm_reset_to} applied; existing={sorted(existing_dm)}")

    run_n = None
    for cand in range(1, 100):
        if cand not in existing_dm:
            run_n = cand
            break
    if run_n is None:
        run_n = 99
    dm_tag = f"_DM{run_n:02d}"
    dm_defs_id = f"{dm_tag}_defs"
    _l.i(f"[dm] run tag={dm_tag}")

    SM = SRC.SourceManager(root, _doc_path, project_root=_runtime_python_dir(), defs_group_id=dm_defs_id)
    owns_text_query_service = text_query_service is None
    if owns_text_query_service:
        text_query_service = TXT.TM.TextQueryService()
    deferred_text_geometry = TXT.DeferredTextGeometry(root)
    _l.d("[text_measure] persistent Inkscape shell owner=%s", "render" if owns_text_query_service else "deckmaker")

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

    def _iter_dataset_texts(dss):
        for ds in (dss or []):
            for rr in (ds.get("comments", []) or []):
                if isinstance(rr, (list, tuple)):
                    for c in rr:
                        if c is not None:
                            yield str(c)
                elif rr is not None:
                    yield str(rr)
            for h in (ds.get("headers", []) or []):
                if h is not None:
                    yield str(h)
            for row in (ds.get("rows", []) or []):
                if isinstance(row, dict):
                    for c in (row.get("cells") or []):
                        if c is not None:
                            yield str(c)
                    for k in ("__dm_layout__", "__dm_marks__"):
                        if row.get(k) is not None:
                            yield str(row.get(k))

    def _iter_source_bodies(text):
        s = str(text or "")
        i = 0
        while True:
            a = s.find("@{", i)
            if a < 0:
                break
            b = s.find("}", a + 2)
            if b < 0:
                break
            yield s[a + 2:b].strip()
            i = b + 1

    def _build_wkmcc_vars(sm, dss):
        out = {}
        seen = set()
        for txt in _iter_dataset_texts(dss):
            for body in _iter_source_bodies(txt):
                if "${" in body:
                    continue
                if not body.lower().startswith("wkmc://"):
                    continue
                try:
                    parsed = sm.web._parse_wkmc_expr(body)
                    if not parsed:
                        continue
                    query, _size = parsed
                    mode, _qv = sm.web._wkmc_query_mode(query)
                    if mode != "category":
                        continue
                except Exception:
                    continue
                key = body.strip()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    items = list(sm.resolve_wkmc_items(key) or [])
                except Exception as ex:
                    _l.w(f"[wkmcc] failed resolving '{key}': {ex}")
                    items = []
                if not items:
                    continue
                name = f"_wkmcc{len(out) + 1}"
                out[name] = items
                _l.i(f"[wkmcc] {name} query='{key}' n={len(items)}")
        return out

    wkmcc_vars = _build_wkmcc_vars(SM, datasets)

    def _expand_comment_lines_with_snips(c_lines, snip_reg, variables=None):
        """Expand snippets inside comment lines, but never touch snippet definition lines."""
        if not c_lines:
            return []
        if not snip_reg and not variables:
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
                rr2[0] = SNP.expand_snippets_in_text(c0, snip_reg, variables=variables)
                out.append(rr2)
            else:
                s0 = str(rr)
                s0s = s0.lstrip()
                if s0s.startswith("#") and s0s[1:].lstrip().startswith(":"):
                    out.append(s0)
                    continue
                out.append(SNP.expand_snippets_in_text(s0, snip_reg, variables=variables))
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
        global_comment_lines_exp = _expand_comment_lines_with_snips(global_comment_lines or [], global_snip_reg, wkmcc_vars)
        global_spritesheets = SM.register_spritesheets_from_comments(global_comment_lines_exp or [], px_per_mm=float(root.unittouu("1mm"))) or {}
    except Exception as ex:
        _l.w(f"[spritesheets.global] scan/register failed: {ex}")
        global_spritesheets = {}
    # Register global gradients once.
    try:
        global_gradients = GRD.register_gradients_from_comments(global_comment_lines_exp or [], SM.defs_root) or {}
    except Exception as ex:
        _l.w(f"[gradients.global] scan/register failed: {ex}")
        global_gradients = {}

    # Global counters across dataset sections (ids must remain unique in the SVG).
    use_seq = [0]
    next_n = SVG.scan_max_pnp_suffix(root) + 1
    placed_total = 0

    # Start external-source discovery for every section before rendering the first
    # one, so slow folder listings overlap symbol/template generation.
    for dataset in datasets:
        try:
            SM.prefetch_dataset_rows((dataset or {}).get("rows", []) or [])
        except Exception as ex:
            _l.w(f"[sources.web] early prefetch failed: {ex}")

    # ---------------- Page cursor ----------------
    # Start placing on the first existing page (index 0), so output content
    # can occupy page 1 instead of always appending after template pages.
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
    start_page_index = 0
    for ds_idx, ds0 in enumerate(datasets, start=1):
        ds_meta = ds0.get("meta", {}) or {}
        headers = ds0.get("headers", []) or []
        rows_data = ds0.get("rows", []) or []
        comment_lines = ds0.get("comments", []) or []

        if not headers and not bool((ds_meta or {}).get("split_enabled", False)):
            _l.w(f"[datasets] #{ds_idx}: no valid header; skipping.")
            continue
        if not rows_data:
            _l.w(f"[datasets] #{ds_idx}: no usable rows; skipping.")
            continue

        _l.i(f"----- DATASET SECTION #{ds_idx}/{len(datasets)} -----")
        _l.i(f"[datasets] #{ds_idx}: rows={len(rows_data)}")
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

        # Expand snippets in headers too (for default expressions like:
        #   back_art-5=:backpattern(7)~a! ).
        if (snip_reg or wkmcc_vars) and isinstance(headers, list):
            for hi, hv in enumerate(list(headers)):
                try:
                    headers[hi] = SNP.expand_snippets_in_text(str(hv or ""), snip_reg, variables=wkmcc_vars)
                except Exception as ex:
                    _l.w(f"[snippets] header expand failed idx={hi}: {ex}")

        if snip_reg or wkmcc_vars:
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
                            cells[ci] = SNP.expand_snippets_in_text(str(v), snip_reg, variables=wkmcc_vars)
                        except Exception as ex:
                            _l.w(f"[snippets] expand failed row#{ridx} cell[{ci}]: {ex}")
                # Meta fields (keep behavior conservative)
                for k in ('__dm_layout__','__dm_marks__'):
                    if isinstance(row, dict) and k in row and row.get(k) is not None:
                        try:
                            row[k] = SNP.expand_snippets_in_text(str(row.get(k)), snip_reg, variables=wkmcc_vars)
                        except Exception as ex:
                            _l.w(f"[snippets] expand failed row#{ridx} meta '{k}': {ex}")
        else:
            _l.i("[snippets] no definitions; expansion skipped")

        # ---- WEB SOURCES PREFETCH (http/https) ----
        # Schedule downloads in background so render can proceed in parallel.
        try:
            SM.prefetch_dataset_rows(rows_data or [])
        except Exception as ex:
            _l.w(f"[sources.web] prefetch failed: {ex}")

        # ---- SPRITESHEETS (global + local) ----
        # Expand snippets inside local comment directives (but not snippet-definition lines).
        local_comment_lines_exp = _expand_comment_lines_with_snips(comment_lines or [], snip_reg, wkmcc_vars)

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
        local_gradients = {}
        if ds_idx == (global_section_idx or -1):
            local_gradients = {}
        else:
            try:
                local_gradients = GRD.register_gradients_from_comments(local_comment_lines_exp or [], SM.defs_root) or {}
            except Exception as ex:
                _l.w(f"[gradients.local] scan/register failed: {ex}")
                local_gradients = {}

        spritesheets = {}
        try:
            if global_spritesheets:
                spritesheets.update(global_spritesheets)
            if local_spritesheets:
                spritesheets.update(local_spritesheets)
        except Exception:
            spritesheets = dict(local_spritesheets or {})

        _l.i(f"[spritesheets] merged: global={len(global_spritesheets or {})} local={len(local_spritesheets or {})} total={len(spritesheets or {})}")
        _l.i(f"[gradients] merged: global={len(global_gradients or {})} local={len(local_gradients or {})} total={len((global_gradients or {})) + len((local_gradients or {}))}")

        _l.s("PROTOTYPE: detect")

        # Output container: keep all generated content under a dedicated root-level container.
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
        out_root    = _find_or_create_root_group('pnpink-output', 'PnPInk Output')

        # Ensure out_root is a direct child of the PnPInk layer.
        if out_root.getparent() is not pnpink_root:
            try:
                if out_root.getparent() is not None:
                    out_root.getparent().remove(out_root)
            except Exception:
                pass
            pnpink_root.append(out_root)

        # Visible output is kept flat under the shared PnPInk Output group.
        out_layer = out_root
        px_per_mm    = float(root.unittouu("1mm"))
        page_gap_px  = float(root.unittouu("1cm"))

        # templates_bbox via dataset marker {{t=...}} in column A (handled in _matrix_to_datasets)
        # --- templates (main + declared columns) ---
        templates_bbox_ids = []
        template_cols = []
        overlay_template_cols = []
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
        overlay_templates = []

        def _parent_of(node):
            try:
                return node.getparent()
            except Exception:
                return None

        def _is_non_layer_group(node) -> bool:
            tag = str(getattr(node, 'tag', '') or '')
            return tag.endswith('g') and node.get(CONST.INK_GROUPMODE) != 'layer'

        def _node_child_text(node, local_name: str) -> str:
            for ch in list(node or []):
                if str(getattr(ch, 'tag', '') or '').endswith(local_name):
                    return str(ch.text or '').strip()
            return ''

        def _find_node_by_template_ref(ref: str):
            """Resolve dataset template refs by id, Inkscape label, SVG title, or SVG desc."""
            needle = str(ref or '').strip()
            if not needle:
                return None
            n = SVG.find_id(root, needle, include_defs=False)
            if n is not None:
                return n
            nodes = list(root.iter())
            for n in nodes:
                try:
                    if str(n.get(inkex.addNS('label', 'inkscape')) or '').strip() == needle:
                        return n
                except Exception:
                    pass
            for n in nodes:
                if _node_child_text(n, 'title') == needle:
                    return n
                if _node_child_text(n, 'desc') == needle:
                    return n
            return None

        def _find_template_root_for_bbox(bid: str):
            if not bid:
                return None, None, None
            n = _find_node_by_template_ref(bid)
            if n is None:
                return None, None, None
            resolved_id = (n.get('id') or str(bid or '')).strip()

            # If the bbox is ungrouped (direct child of <svg>), do NOT scale it to the full document.
            # Treat it as a single-element template (the bbox itself). This avoids hangs.
            if _parent_of(n) is root:
                _l.w(
                    f"[templates] bbox ref '{bid}' is a root-level element. "
                    "PnPInk will treat it as a single-element template; group the template with Ctrl+G to avoid this."
                )
                return n, n, resolved_id

            # Find the template root (<g>) under the "stop boundary" (root or layer)
            cur = n
            tmpl = None
            while cur is not None:
                par = _parent_of(cur)

                if _is_non_layer_group(cur):
                    tmpl = cur

                if par is None:
                    break
                if par is root:
                    break
                if str(getattr(par, 'tag', '') or '').endswith('g') and par.get(CONST.INK_GROUPMODE) == 'layer':
                    break

                cur = par

            if tmpl is None:
                par = _parent_of(n)
                if _is_non_layer_group(par):
                    tmpl = par

            return tmpl, n, resolved_id

        # 1) Resolve main template (if declared)
        if main_bbox_id:
            tmpl, n, resolved_bbox_id = _find_template_root_for_bbox(main_bbox_id)
            if tmpl is None or n is None:
                _l.w(f"[templates] main bbox ref '{main_bbox_id}' not found in SVG or not under any <g>")
            else:
                declared_template_root = tmpl
                declared_bbox_node = n
                declared_bbox_id = resolved_bbox_id
                _l.i(f"[templates] main template_root='{tmpl.get('id') or '<noid>'}' bbox_ref='{main_bbox_id}' bbox_id='{declared_bbox_id}'")

        # 2) Resolve declared templates from header columns (left-to-right order)
        # Compatibility for datasets loaded by older code paths.
        if not template_cols and overlay_template_cols:
            template_cols = [dict(c, mods=[]) for c in (overlay_template_cols or [])]

        overlay_templates = []       # slot-anchored, front pass
        back_templates = []          # slot-anchored, back pass
        page_templates = []          # page-anchored, front pass
        page_back_templates = []     # page-anchored, back pass

        seen_templates = set()
        for col in (template_cols or []):
            bid = (col or {}).get('bbox_id')
            ckey = (col or {}).get('key')
            if not bid:
                continue
            tmpl, n, resolved_bbox_id = _find_template_root_for_bbox(bid)
            if tmpl is None or n is None:
                _l.w(f"[templates] overlay bbox ref '{bid}' not found in SVG or not under any <g>")
                continue
            tid = tmpl.get('id') or '<noid>'
            if tid in seen_templates:
                _l.w(f"[templates] bbox ref '{bid}' resolves to already selected template '{tid}'; skipped")
                continue
            seen_templates.add(tid)

            mods = set((col or {}).get('mods') or [])
            entry = {
                'bbox_id': resolved_bbox_id,
                'bbox_ref': bid,
                'bbox_node': n,
                'template_root': tmpl,
                'control_key': ckey,
                'col_index': (col or {}).get('col_index'),
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

        # Detect prototype from header targets.
        target_nodes: List[inkex.BaseElement] = []
        _seen_tn_ids = set()

        def _wildcard_candidate_names(el):
            names = []

            def add(value):
                v = (value or "").strip()
                if v and v not in names:
                    names.append(v)

            cid = (el.get('id') or '').strip()
            if cid:
                add(cid)
                add(SVG.strip_pnp_suffix(cid) or cid)
            add(el.get('data-origid'))
            add(el.get('data-field'))
            return names

        for h in headers:
            tid = (re.match(r"^([^\[]+)", h).group(1) if re.match(r"^([^\[]+)", h) else "").strip()
            if not tid or tid.startswith("clone_"): continue
            toks = [x for x in re.split(r"\s+", tid) if x]
            for tok in toks:
                # Header wildcard support in prototype detection: main_icon-* -> all ids with that prefix.
                if tok.endswith("*") and re.match(r"^[A-Za-z_][-A-Za-z0-9_:.]*\*$", tok):
                    pref = tok[:-1]
                    try:
                        for _el in root.iter():
                            if not any((_name or "").startswith(pref) for _name in _wildcard_candidate_names(_el)):
                                continue
                            _id = (_el.get('id') or f"@obj:{id(_el)}")
                            if _id and _id not in _seen_tn_ids:
                                target_nodes.append(_el)
                                _seen_tn_ids.add(_id)
                    except Exception:
                        pass
                    continue

                n = root.find(".//*[@id='%s']" % tok)
                if n is None:
                    n = root.find(".//*[@data-field='%s']" % tok)
                if n is not None:
                    try:
                        _nid = (n.get('id') or f"@obj:{id(n)}")
                    except Exception:
                        _nid = f"@obj:{id(n)}"
                    if _nid not in _seen_tn_ids:
                        target_nodes.append(n)
                        _seen_tn_ids.add(_nid)
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

        cand_groups = []
        if target_nodes:
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
                raise inkex.AbortExtension("Group card template elements under a single group.")



        # If a template was declared via {{t=...}}, it takes precedence over header-based prototype detection.
        # IMPORTANT: do not move/reparent/mutate any user element.
        if declared_template_root is not None:
            proto_root = declared_template_root

        # Anti-hang heuristic (ungrouped templates):
        # If the main bbox ({{t=...}}) hangs directly from <svg>, the implicit "template" cannot
        # be the full document. For the copy/replace pipeline to work without hanging,
        # we build a synthetic in-memory wrapper that absorbs:
        #   - the main bbox, and
        #   - HEADER ids that are also in root,
        # excluding bboxes declared by template columns (overlays/@back/@page).
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
                    "[templates] root-level main bbox: absorbed root-level header elements "
                    f"into the synthetic main template: {absorbed_list}. "
                    "Group the template with Ctrl+G to avoid this warning."
                )

        # Externalize embedded data: images once into <defs> so repeated template instances
        # reuse shared symbols instead of duplicating the bitmap payload in every deepcopy.
        try:
            _tpl_roots = []
            if proto_root is not None:
                _tpl_roots.append(proto_root)
            if declared_template_root is not None:
                _tpl_roots.append(declared_template_root)
            for _te in (overlay_templates or []):
                _tr = (_te or {}).get('template_root')
                if _tr is not None:
                    _tpl_roots.append(_tr)
            for _te in (back_templates or []):
                _tr = (_te or {}).get('template_root')
                if _tr is not None:
                    _tpl_roots.append(_tr)
            for _te in (page_templates or []):
                _tr = (_te or {}).get('template_root')
                if _tr is not None:
                    _tpl_roots.append(_tr)
            for _te in (page_back_templates or []):
                _tr = (_te or {}).get('template_root')
                if _tr is not None:
                    _tpl_roots.append(_tr)

            _seen_tpl_roots = set()
            _ext_total = 0
            for _tr in _tpl_roots:
                _oid = id(_tr)
                if _oid in _seen_tpl_roots:
                    continue
                _seen_tpl_roots.add(_oid)
                _ext_total += int(SVG.externalize_embedded_images_in_subtree(root, _tr) or 0)
            if _ext_total:
                _l.i(f"[templates] externalized {_ext_total} embedded template image(s) into shared defs symbols")
        except Exception as _ex:
            _l.w(f"[templates] embedded image externalization failed: {_ex}")

        # Prepare template roots once. Instances are generated from these prepared
        # roots, so per-card code must not repeat full-subtree normalization.
        try:
            _seen_tpl_roots = set()
            _prep_flatten = 0
            _prep_absolutize = 0
            for _tr in _tpl_roots:
                _oid = id(_tr)
                if _oid in _seen_tpl_roots:
                    continue
                _seen_tpl_roots.add(_oid)
                REN._flatten_group_transform(_tr)
                _prep_flatten += 1
                _prep_absolutize += int(SVG.absolutize_all_linked_images(_tr, _doc_path, prefer="fileuri") or 0)
            _l.i(f"[templates] prepared template roots={_prep_flatten} absolutized_images={_prep_absolutize}")
        except Exception as _ex:
            _l.w(f"[templates] template preparation failed: {_ex}")
        # Measure base card/template size.
        template_anchor_x = None
        template_anchor_y = None

        def _visual_bbox_with_inherited_transform(node):
            tag = str(getattr(node, "tag", "") or "")
            if tag.endswith("rect") or tag.endswith("image"):
                x = float(node.get("x") or 0.0)
                y = float(node.get("y") or 0.0)
                w = float(node.get("width") or 0.0)
                h = float(node.get("height") or 0.0)
                T = SVG.composed_transform(node)
                pts = [
                    T.apply_to_point((x, y)),
                    T.apply_to_point((x + w, y)),
                    T.apply_to_point((x, y + h)),
                    T.apply_to_point((x + w, y + h)),
                ]
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
            bb = node.bounding_box()
            return float(bb.left), float(bb.top), float(bb.width), float(bb.height)

        def _safe_node_id(node):
            try:
                return node.get('id') if hasattr(node, 'get') else None
            except Exception:
                return None

        def _measure_flattened_template_bbox(template_root_node, bbox_id=None):
            temp = deepcopy(template_root_node)
            REN._flatten_group_transform(temp)
            try:
                SVG.uniquify_all_ids_in_scope(temp, "_dmM", root.get_unique_id)
            except Exception:
                pass

            measure_layer = self._find_or_create_layer(root, "_DeckMaker Measuring")
            measure_layer.append(temp)
            try:
                target = None
                if bbox_id:
                    target = temp.find(".//*[@data-origid='%s']" % bbox_id)
                    if target is None:
                        target = temp.find(".//*[@id='%s']" % bbox_id)
                if target is None:
                    target = SVG.pick_anchor_in(temp)
                    bb = target.bounding_box()
                    bbox = (float(bb.left), float(bb.top), float(bb.width), float(bb.height))
                else:
                    bbox = _visual_bbox_with_inherited_transform(target)
                return bbox, target
            finally:
                try:
                    measure_layer.remove(temp)
                except Exception:
                    pass
                try:
                    if len(measure_layer) == 0 and measure_layer.getparent() is not None:
                        measure_layer.getparent().remove(measure_layer)
                except Exception:
                    pass

        if declared_bbox_node is not None:
            bbox_id = declared_bbox_node.get('id')

            # Special case (root): the template bbox must ALWAYS come from the declared element in the
            # original document. If we measure a wrapper with text, pick_anchor_in() may choose a <text>
            # and collapse size (grid "clumped").
            if _main_bbox_in_root:
                template_anchor_x, template_anchor_y, aw, ah = _visual_bbox_with_inherited_transform(declared_bbox_node)

                anc_id = _safe_node_id(declared_bbox_node)
                _l.i(
                    f"[templates] measured_bbox_node_id='{anc_id}' aw_px={aw:.2f} ah_px={ah:.2f} "
                    f"left_px={template_anchor_x:.2f} top_px={template_anchor_y:.2f} (root_bbox_direct)"
                )

            else:
                # Measure exactly like generated instances: on a flattened, uniquified copy.
                (template_anchor_x, template_anchor_y, aw, ah), temp_anchor = _measure_flattened_template_bbox(proto_root, bbox_id)
                if (aw <= 1e-6 or ah <= 1e-6) and declared_bbox_node is not None:
                    _l.w(f"[templates] measured bbox for '{bbox_id}' is empty; using declared bbox")
                    template_anchor_x, template_anchor_y, aw, ah = _visual_bbox_with_inherited_transform(declared_bbox_node)
                    temp_anchor = declared_bbox_node
                anc_id = _safe_node_id(temp_anchor)
                _l.i(
                    f"[templates] measured_bbox_node_id='{anc_id}' aw_px={aw:.2f} ah_px={ah:.2f} "
                    f"left_px={template_anchor_x:.2f} top_px={template_anchor_y:.2f}"
                )
                try:
                    if declared_template_root is not None:
                        gbb = declared_template_root.bounding_box()
                        gid = _safe_node_id(declared_template_root)
                        _l.i(
                            f"[templates] template_root_bbox id='{gid}' w_px={float(gbb.width):.2f} h_px={float(gbb.height):.2f} "
                            f"left_px={float(gbb.left):.2f} top_px={float(gbb.top):.2f}"
                        )
                    if declared_bbox_node is not None:
                        nbb = declared_bbox_node.bounding_box()
                        nid = _safe_node_id(declared_bbox_node)
                        _l.i(
                            f"[templates] declared_bbox_node_bbox id='{nid}' w_px={float(nbb.width):.2f} h_px={float(nbb.height):.2f} "
                            f"left_px={float(nbb.left):.2f} top_px={float(nbb.top):.2f}"
                        )
                except Exception as e:
                    _l.w(f"[templates] bbox debug failed: {e}")

            _l.d(
                f"card_size aw={aw:.2f}px ah={ah:.2f}px (templates_bbox='{bbox_id}', "
                f"anchor=({template_anchor_x:.2f},{template_anchor_y:.2f}))"
            )
        else:
            (template_anchor_x, template_anchor_y, aw, ah), temp_anchor = _measure_flattened_template_bbox(proto_root)
            anc_id = _safe_node_id(temp_anchor)
            _l.i(f"[templates] measured_bbox_node_id='{anc_id}' aw_px={aw:.2f} ah_px={ah:.2f} (whole_template)")
            try:
                if declared_template_root is not None:
                    gbb = declared_template_root.bounding_box()
                    gid = _safe_node_id(declared_template_root)
                    _l.i(f"[templates] template_root_bbox id='{gid}' w_px={float(gbb.width):.2f} h_px={float(gbb.height):.2f} left_px={float(gbb.left):.2f} top_px={float(gbb.top):.2f}")
            except Exception as e:
                _l.w(f"[templates] bbox debug failed: {e}")
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

        def _flush_marks_for_page(page_idx: int):
            jobs = _marks_pending_by_page.get(int(page_idx)) or []
            if not jobs:
                return
            marks_parent = jobs[0].get("parent")
            if marks_parent is None:
                marks_parent = out_layer

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
                        parent=marks_parent,
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
                        parent=marks_parent,
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

            # Card size in px.
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
                    _l.i("[layout] plan fallback: oversize card -> 1 unscaled slot")
                    # Keep the requested card/template size. Using the page inner
                    # size here would force non-uniform scaling in render.py.
                    slot_x = float(cx) + (float(cw) - float(cw_px)) * 0.5
                    slot_y = float(cy) + (float(ch) - float(ch_px)) * 0.5
                    plan.slots = [(slot_x, slot_y, float(cw_px), float(ch_px))]
                    plan.cols = 1
                    plan.rows = 1
                    plan.per_page = 1
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

        def _half_abs_gap(tok: str) -> str:
            s = _tok_str(tok)
            if not s or '%' in s:
                return '0'
            try:
                return _tok_str(float(SVG.measure_to_mm(s, base_mm=None)) * 0.5)
            except Exception:
                return '0'

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
            """Auto-adjust gaps for smart hex shapes."""
            try:
                sp = (getattr(layout_obj, 'smart_shape', None) or '').strip().lower()
                if sp not in ('hexgrid', 'hextile', 'hextiles'):
                    return

                orient = _detect_hex_orientation(proto_root, declared_bbox_node)
                if orient is None:
                    # For map/overlay workflows there may be no hex path in the template to inspect.
                    # Fall back to pointy instead of silently degrading to a rectangular grid.
                    orient = 'pointy' if sp in ('hexgrid', 'hextile', 'hextiles') else None

                # Persist the detected orientation for downstream consumers (e.g. Marks{} hextiles).
                # Marks must not re-infer orientation from noisy geometry when
                # DeckMaker already determined it for smart gaps.
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
                        seq4[3] = _expr_add(_expr_add(seq4[3], '+50%'), _half_abs_gap(seq4[1]))
                    else:
                        # b += -25%, c += +50%
                        seq4[1] = _expr_add(seq4[1], '-25%')
                        seq4[2] = _expr_add(_expr_add(seq4[2], '+50%'), _half_abs_gap(seq4[0]))
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
            r = LYT.resolve(page, card, layout, gaps, doc_page_mm)
            try:
                setattr(r, "_dm_tag", dm_tag)
            except Exception:
                pass
            return r

        current = _resolve_with_base(page, card, layout, gaps, doc_page_mm)
        try:
            setattr(current, "_dm_tag", dm_tag)
        except Exception:
            pass
        planner = REN.CardPlanner(
            root=root, nv=nv, pages=pages,
            px_per_mm=px_per_mm, page_gap_px=page_gap_px,
            doc_page_mm=doc_page_mm,
            current_resolved=current,
            ensure_page_for_fn=REN.ensure_page_for,
            plan_fn=_compute_plan_for
        )
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
        else:
            planner.sync_page_attrs()
        _l.s("PLANNER: init")
        _l.i(f"Grid {planner.plan.cols}x{planner.plan.rows}, gaps {planner.current.gaps.h}×{planner.current.gaps.v} mm; slots/page {planner.slots_per_page()}")

        # ---- RENDER + MARKS (moved out of engine.py) ----
        _ctx = EngineContext(
            ext=self, root=root,
            SM=SM, datasets=datasets, ds_idx=ds_idx, ds_meta=ds_meta, headers=headers, rows_data=rows_data,
            use_seq=use_seq, next_n=next_n, placed_total=placed_total, start_page_index=start_page_index,
            dataset_count=len(datasets or []),
            planner=planner, proto_root=proto_root, out_layer=out_layer,
            doc_path=_doc_path,
            declared_bbox_id=declared_bbox_id,
            overlay_templates=overlay_templates,
            back_templates=back_templates,
            page_templates=page_templates,
            page_back_templates=page_back_templates,
            declared_template_root=declared_template_root,
            declared_bbox_node=declared_bbox_node,
            measured_template_bbox=(template_anchor_x, template_anchor_y, aw, ah),
            # Legacy core locals used by the render tail. These must exist to preserve the original
            # control-flow/side-effects when render logic was extracted into render.py.
            page=page, card=card, layout=layout, gaps=gaps, doc_page_mm=doc_page_mm,
            resolve_with_base=_resolve_with_base,
            marks_pending_by_page=_marks_pending_by_page, flush_marks_for_page=_flush_marks_for_page,
            header_marks_current=marks_current,
            spritesheets=spritesheets,
            text_query_service=text_query_service,
            deferred_text_geometry=deferred_text_geometry,
        )
        REN.render_phase(_ctx)
        next_n = _ctx.next_n
        placed_total = _ctx.placed_total
        start_page_index = _ctx.start_page_index
        continue
    _l.i(f"[datasets] total placed={placed_total} across {len(datasets)} dataset section(s).")

    try:
        if deferred_text_geometry.has_work:
            res = TXT.process_text_geometry(
                out_layer,
                show_debug_rects=False,
                source_manager=SM,
                doc_path=_doc_path,
                query_service=text_query_service,
                prepared_geometry=deferred_text_geometry,
            )
        else:
            res = TXT.ProcessResult(0, set())
        _l.i(
            f"[deckmaker.text] ONE-PASS placed={res.icons_placed} icons across output; "
            f"sources={sorted(res.used_sources)}"
        )
    finally:
        if owns_text_query_service:
            text_query_service.close()
        try:
            SM.log_web_summary()
        except Exception:
            pass
    try:
        SVG.apply_paste_style_rules(root, root)
    except Exception as ex:
        _l.w(f"[paste-style] skipped: {ex}")

    _l.s("END DeckMaker")
