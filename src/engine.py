# [2026-02-18] Chore: remove unused legacy %...% var regex.
# [2026-02-19] Add: split layout gaps into gaps + offset properties.
# [2026-02-20] Add: allow oversized templates to proceed with split-board fallback slots.
# [2026-02-20] Fix: allow declared templates without header-matching ids.
# -*- coding: utf-8 -*-
import log as LOG
_l = LOG
import os, sys, re, subprocess, shutil
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

import dataset as DS
import gui as PROGRESS
import render as REN

# --------------------- util / parsing ---------------------


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
    doc.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, out_path)


def _find_inkscape_executable() -> str | None:
    names = ["inkscape.exe", "inkscape"] if os.name == "nt" else ["inkscape"]
    for name in names:
        exe = shutil.which(name)
        if exe:
            return exe
    pyexe = os.path.abspath(sys.executable)
    bin_dir = os.path.dirname(pyexe)
    candidates = [
        os.path.join(bin_dir, "inkscape.exe"),
        os.path.join(os.path.dirname(bin_dir), "inkscape.exe"),
        os.path.join(os.path.dirname(bin_dir), "bin", "inkscape.exe"),
        os.path.join(bin_dir, "inkscape"),
        os.path.join(os.path.dirname(bin_dir), "inkscape"),
        os.path.join(os.path.dirname(bin_dir), "bin", "inkscape"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _clean_inkscape_launch_env() -> dict[str, str]:
    env = dict(os.environ)
    exact_keys = {
        "SELF_CALL",
        "DOCUMENT_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONIOENCODING",
    }
    prefix_keys = (
        "INKEX_",
        "INKSCAPE_",
        "GDK_",
        "GTK_",
        "XDG_",
    )
    for key in list(env.keys()):
        sk = str(key or "")
        if not sk or sk.startswith("="):
            continue
        if sk in exact_keys or any(sk.startswith(prefix) for prefix in prefix_keys):
            env.pop(sk, None)
    return env


def _launch_inkscape(svg_path: str) -> bool:
    if not svg_path or not os.path.isfile(svg_path):
        return False
    exe = _find_inkscape_executable()
    if not exe:
        _l.w(f"[dm_output] inkscape executable not found; output saved at '{svg_path}'")
        return False
    try:
        env = _clean_inkscape_launch_env()
        argv = [exe, svg_path]
        kwargs = {
            "args": argv,
            "cwd": os.path.dirname(exe) or None,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            creationflags = 0
            for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
                creationflags |= int(getattr(subprocess, flag_name, 0))
            kwargs["creationflags"] = creationflags
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(**kwargs)
        # Detached launch: we intentionally do not wait on this child.
        # Mark the local Popen object as finalized so Python does not emit
        # ResourceWarning when it gets garbage-collected while the process
        # is still running.
        try:
            proc.returncode = 0
        except Exception:
            pass
        _l.i(f"[dm_output] launched Inkscape: '{svg_path}'")
        return True
    except Exception as ex:
        _l.w(f"[dm_output] failed launching Inkscape for '{svg_path}': {ex}")
        return False

def run(self, __version__):
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
    preloaded_datasets = getattr(self, "_dm_preloaded_datasets", None)
    if preloaded_datasets is not None:
        datasets = preloaded_datasets
        _l.i("[datasets] using preloaded dataset for output render")
    else:
        datasets = DS.load_datasets(self, _doc_path)
    if not datasets:
        raise inkex.AbortExtension("Dataset sin cabecera válida.")
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
                    run(self, __version__)
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

                    chunk_target_bytes = max(1, int(prefs.get_split_svg_chunk_mb(64))) * 1024 * 1024
                    force_chunk_output = bool(prefs.get_split_svg_output(False))
                    analysis = SVGCHUNKS.analyze_output_doc(
                        clone_doc,
                        source_svg_path=_doc_path,
                        analysis_label=out_path,
                        absolutize_images=False,
                    )
                    page_slices = list(analysis.get("pages") or [])
                    total_est_bytes = sum(int(item.est_bytes or 0) for item in page_slices)
                    page_count = len(page_slices)
                    use_chunk_output = (
                        force_chunk_output
                        or page_count >= int(DM_OUTPUT_CHUNK_THRESHOLD_PAGES)
                        or total_est_bytes >= int(chunk_target_bytes)
                    )
                    _l.i(
                        f"[dm_output] analyzed external render pages={page_count} "
                        f"fixed_images={int(analysis.get('fixed_images') or 0)} "
                        f"est_bytes={total_est_bytes} chunked={'yes' if use_chunk_output else 'no'} "
                        f"forced={'yes' if force_chunk_output else 'no'} target_bytes={chunk_target_bytes}"
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
                    stem = os.path.splitext(os.path.basename(out_path))[0]
                    chunk_dir = os.path.join(os.path.dirname(os.path.abspath(out_path)) or ".", f"{stem}_chunks")
                    if os.path.isdir(chunk_dir):
                        shutil.rmtree(chunk_dir, ignore_errors=True)
                except Exception:
                    pass
                _write_svg_atomic(clone_doc, out_path)
                _l.i(f"[dm_output] wrote external render: '{out_path}'")
                _launch_inkscape(out_path)
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

    SM = SRC.SourceManager(root, _doc_path, project_root=None, defs_group_id=dm_defs_id)

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
                            _l.w(f"[snippets] fallo expand row#{ridx} cell[{ci}]: {ex}")
                # Meta fields (keep behavior conservative)
                for k in ('__dm_layout__','__dm_marks__'):
                    if isinstance(row, dict) and k in row and row.get(k) is not None:
                        try:
                            row[k] = SNP.expand_snippets_in_text(str(row.get(k)), snip_reg, variables=wkmcc_vars)
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
        _seen_tn_ids = set()
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
                            _id = (_el.get('id') or '').strip()
                            if _id and _id.startswith(pref) and _id not in _seen_tn_ids:
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

        _gaps_has_offsets = LYT.gaps_has_offsets

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
                    # For map/overlay workflows there may be no hex path in the template to inspect.
                    # Fall back to pointy instead of silently degrading to a rectangular grid.
                    orient = 'pointy' if sp in ('hexgrid', 'hextile', 'hextiles') else None

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
