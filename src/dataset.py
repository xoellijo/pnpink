# -*- coding: utf-8 -*-
import os, re, csv, io
import urllib.parse

import log as LOG
_l = LOG
import gsheets_client_pkce as GS
_gs = GS
from typing import List, Optional, Tuple
import dataset_state as DSTATE
import net as NET

import inkex
import dsl as DSL
import dataset_header as DH


def strip_bom(s: str) -> str:
    return s.lstrip("\ufeff") if isinstance(s, str) else s


def _row_is_comment(cells: List[str]) -> bool:
    """Line-start directive comment row: '#...' (but not '##...')."""
    if not cells:
        return False
    c0 = str(cells[0] or "")
    c0l = c0.lstrip()
    return c0l.startswith("#") and not c0l.startswith("##")


def _row_is_hard_comment(cells: List[str]) -> bool:
    """Line-start hard comment row: '##...' (but not '####...' EOF)."""
    if not cells:
        return False
    c0 = str(cells[0] or "")
    c0l = c0.lstrip()
    return c0l.startswith("##") and not c0l.startswith("####")


def _row_is_eof_comment(cells: List[str]) -> bool:
    """EOF marker row: '####...' at line start, stops further parsing."""
    if not cells:
        return False
    c0 = str(cells[0] or "")
    return c0.lstrip().startswith("####")

def _strip_cell_trailing_comment(text: str, enable: bool = True, marker: str = "##") -> str:
    """Cell-level comment marker: '##' comments out the rest of the cell."""
    if text is None:
        return ""
    if not enable:
        return str(text)
    s = str(text)
    mk = str(marker or "")
    if not mk:
        return s
    k = s.find(mk)
    if k < 0:
        return s
    return s[:k].rstrip()


def _apply_inline_row_comments(cells: List[str]) -> List[str]:
    """Apply inline dataset comments to a row.

    Rules (when row is NOT a line-start comment):
      - '##'  => comment rest of current cell
      - '###' => comment rest of current cell AND all remaining cells (rest of line)
    """
    out = [("" if c is None else str(c)) for c in (cells or [])]
    if not out:
        return out
    for i, s in enumerate(out):
        k3 = s.find("###")
        k2 = s.find("##")
        if k3 >= 0 and (k2 < 0 or k3 <= k2):
            out[i] = s[:k3].rstrip()
            for j in range(i + 1, len(out)):
                out[j] = ""
            break
        if k2 >= 0:
            out[i] = s[:k2].rstrip()
    return out

parse_template_header_cell = DH.parse_template_header_cell


def _is_nontext_dataset_field(header_key: str) -> bool:
    """Minimal heuristic to decide if a dataset cell can use '##' as a comment.

    Goal: avoid collisions of '#' inside real content (especially text).
    Therefore, **by default** we do NOT interpret '#' in data cells. We only
    allow it for fields that are clearly "non-text" (DSL / internal controls).

    Current rule:
      - Always: column A (leading cell) is commentable.
      - Dataset columns (B..): only if the header is internal/control.
        * internal keys "__dm_..." (includes template cols "__dm_tcol__...")
        * headers starting with '.' (convention: DSL inline)

    If in the future we want to detect "text" by actual SVG element type,
    it must be done in render (when we already have the node) and NOT here.
    """
    h = str(header_key or "")
    if not h:
        return False
    if h.startswith("__dm_"):
        return True
    if h.startswith("."):
        return True
    return False




def _apply_header_disabling(headers_raw):
    """Apply header '#' semantics:
       - '##col'   -> disable this column
       - '###col'  -> disable this and all columns to the right
       - '#' not at start already handled as trailing comment.
       Returns filtered headers list and active index list.
    """
    active = []
    disable_all_right = False
    for i, h in enumerate(headers_raw):
        hs = str(h or "").strip()
        if disable_all_right:
            continue
        if hs.startswith("###"):
            disable_all_right = True
            continue
        if hs.startswith("##"):
            continue
        active.append(i)
    headers = [headers_raw[i] for i in active]
    return headers, active


def _extract_header_target_id_for_inherit(header_key: str) -> str:
    s = str(header_key or "").strip()
    if not s:
        return ""
    left = s.partition("=")[0].strip()
    if left.endswith("+"):
        left = left[:-1].strip()
    m = re.match(r"^(?P<id>.+?)\[[A-Za-z_][A-Za-z0-9_-]*\]\s*$", left)
    if m:
        left = (m.group("id") or "").strip()
    if not left or any(ch.isspace() for ch in left):
        return ""
    return left


def _expand_property_only_headers(headers_raw):
    """Allow shorthand property columns: '[font-size]' inherits previous target id."""
    out = []
    last_target = ""
    for h in (headers_raw or []):
        hs = str(h or "").strip()
        if hs.startswith("[") and last_target:
            hs = f"{last_target}{hs}"
        out.append(hs)
        tid = _extract_header_target_id_for_inherit(hs)
        if tid:
            last_target = tid
    return out


def _normalize_row_cells_for_headers(cells, headers_len: int, active_idx) -> List[str]:
    """Pad/truncate a row to safely index all active header columns.

    `active_idx` contains indices in the *original* header row (before disabling),
    so it may be larger than `headers_len-1`.
    """
    out = list(cells or [])
    min_by_headers = int(headers_len or 0) + 1  # + leading column A
    min_by_active = (max(active_idx) + 2) if active_idx else 1
    need = max(min_by_headers, min_by_active)
    if len(out) < need:
        out += [""] * (need - len(out))
    return out


def _extract_template_columns(headers):
    return DH.extract_template_columns(headers)


def _matrix_to_datasets(matrix):
    """
    Convert matrix into 1+ datasets using the *modern* dataset format only.

    Rules:
      - A dataset "marker" exists ONLY in column A and uses {{...}} syntax.
        The marker row is also the header row; headers start at column B.
        Examples (equivalent):
          {{t=id}} , {{template_bbox=id}} , {{id}}
      - If no marker rows exist, the sheet/CSV is treated as a single dataset
        in shorthand form:
          A1 contains the template bbox id (or {{...}}); headers start at column B.

    Notes:
      - Column A is always "special": marker or leading-cell DSL. It never becomes
        a normal header field.
      - This function intentionally drops the old legacy-v1 dataset layout where
        column A was part of the header/data. User has opted out of that format.
    """
    def _norm_cell(c):
        return "" if c is None else str(c)

    def _is_blank_row(cells):
        return all(str(c or "").strip() == "" for c in cells)

    def _parse_lead_to_meta(lead_text: str):
        """Parse lead cell (column A in data rows): copies/page/layout/marks/holes."""
        try:
            lead = DSL.parse_leading_cell(lead_text)
        except Exception as ex:
            _l.w(f"parse_leading_cell failed on '{lead_text}': {ex}")
            lead = None

        copies = 1
        holes = []
        iter_select = []
        page_preset = None
        layout_tail = None
        marks_tail = None
        slot_select = None
        slot_select_mode = None
        copies_explicit = False
        if lead is not None:
            copies = int(getattr(lead, "copies", 1) or 1)
            holes = list(getattr(lead, "holes", []) or [])
            iter_select = getattr(lead, "iter_select_raw", None) or list(getattr(lead, "iter_select", []) or [])
            page_preset = getattr(lead, "page_block", None)
            layout_tail = getattr(lead, "layout_block", None)
            marks_tail = getattr(lead, "marks_block", None)
            slot_select = getattr(lead, "slot_select_raw", None)
            slot_select_mode = getattr(lead, "slot_select_mode", None)
            copies_explicit = bool(getattr(lead, "copies_explicit", False))
            # Defensive: ignore malformed page blocks in free text (column A),
            # so render does not abort on values like "{{t=...}".
            if page_preset:
                try:
                    DSL.parse_page_block(page_preset)
                except Exception:
                    _l.w(f"dataset.row_cell0: ignoring invalid page block '{page_preset}'")
                    page_preset = None

        _l.d(
            f"dataset.row_cell0='{lead_text}' → copies={copies} explicit={copies_explicit} "
            f"select={iter_select} slot={slot_select_mode}:{slot_select} page={page_preset} L={layout_tail} M={marks_tail}"
        )
        return copies, copies_explicit, holes, iter_select, page_preset, layout_tail, marks_tail, slot_select, slot_select_mode

    # --- Pre-scan to detect any explicit marker rows {{...}} in column A ---
    has_markers = False
    for r in (matrix or []):
        if not r:
            continue
        raw_cells = [_norm_cell(c) for c in r]
        if _row_is_eof_comment(raw_cells):
            break
        if _row_is_hard_comment(raw_cells) or _row_is_comment(raw_cells):
            continue
        cells = _apply_inline_row_comments(raw_cells)
        c0 = str(cells[0]).strip() if len(cells) > 0 else ""
        if DSL.parse_dataset_decl(c0, allow_bare=False) is not None:
            has_markers = True
            break

    datasets = []

    # --- Marker mode: multiple datasets in one sheet ---
    if has_markers:
        current = None
        # Preserve comment/directive rows before first marker, and attach
        # directive rows inside active datasets to current.comments.
        pending_comments: List[List[str]] = []
        for r in (matrix or []):
            if r is None or len(r) == 0:
                continue
            raw_cells = [_norm_cell(c) for c in r]
            if _row_is_eof_comment(raw_cells):
                break
            if _row_is_hard_comment(raw_cells):
                continue
            if _row_is_comment(raw_cells):
                rc = _apply_inline_row_comments(raw_cells)
                if current is not None and isinstance(current.get("comments"), list):
                    current["comments"].append(rc)
                else:
                    pending_comments.append(rc)
                continue

            cells = _apply_inline_row_comments(raw_cells)

            if _is_blank_row(cells):
                # Preserve blank rows *inside* a dataset as placeholder slots (do not shift subsequent cards).
                if current is not None and current.get('headers'):
                    headers = current.get('headers') or []
                    base = {headers[i]: '' for i in range(0, len(headers))}
                    base['__dm_copies__'] = 1
                    base['__dm_copies_explicit__'] = False
                    base['__dm_holes__'] = []
                    current['rows'].append(base)
                continue
            c0 = str(cells[0]).strip()
            decl = DSL.parse_dataset_decl(c0, allow_bare=False)
            if decl is not None:
                # close previous dataset
                if current is not None and current.get("headers"):
                    datasets.append(current)
                # open new dataset
                templates_bbox_ids = list((decl or {}).get("template_bbox") or [])
                if len(templates_bbox_ids) > 1:
                    raise inkex.AbortExtension(
                        "Multi-template por lista ya no está soportado.\n"
                        "Usa un único main template en el marker: {{t=MAIN_BBOX_ID}}\n"
                        "y declara templates adicionales con columnas de header {t=OTRO_BBOX_ID}."
                    )
                main_bbox_id = templates_bbox_ids[0] if templates_bbox_ids else None

                # Extra DSL tail after the dataset marker row (column A) is allowed:
                #   {{t=MAIN}} {A4}.L{...}.M{...}
                # We parse it using the same leading-cell parser used for data rows.
                tail_text = ""
                if c0.startswith("{{"):
                    end = c0.find("}}", 2)
                    if end >= 0:
                        tail_text = (c0[end+2:] or "").strip()
                lead0 = None
                if tail_text:
                    try:
                        lead0 = DSL.parse_leading_cell(tail_text)
                    except Exception:
                        lead0 = None

                headers_raw = [str(_strip_cell_trailing_comment(h or "", enable=True, marker="##")).strip() for h in cells[1:]]
                headers_raw = _expand_property_only_headers(headers_raw)
                headers_raw, active_idx = _apply_header_disabling(headers_raw)
                headers_norm, template_cols = _extract_template_columns(headers_raw)

                current = {
                    "meta": {
                        "templates_bbox_ids": ([main_bbox_id] if main_bbox_id else []),
                        "template_cols": template_cols,
                        # Legacy (slot-anchored overlays, front pass): kept for older code paths.
                        "overlay_template_cols": [c for c in (template_cols or []) if not (set(c.get('mods') or []) & {'@page','@back'})],
                        "overlay_templates_bbox_ids": [c.get('bbox_id') for c in (template_cols or []) if not (set(c.get('mods') or []) & {'@page','@back'})],

                        # Header presets (apply once for the dataset section)
                        "header_page_block": getattr(lead0, "page_block", None) if lead0 else None,
                        "header_layout_block": getattr(lead0, "layout_block", None) if lead0 else None,
                        "header_marks_block": getattr(lead0, "marks_block", None) if lead0 else None,
                    },
                    "headers": headers_norm,
                    "rows": [],
                    "comments": list(pending_comments),
                }
                pending_comments = []
                continue

            # normal data row
            if current is None or not current.get("headers"):
                # ignore junk until first marker/header row
                continue

            headers = current["headers"]
            cells = _normalize_row_cells_for_headers(cells, len(headers), active_idx)

            # Apply per-cell comments (##) uniformly.
            cells[0] = _strip_cell_trailing_comment(cells[0], enable=True, marker="##")
            for j in range(1, len(cells)):
                cells[j] = _strip_cell_trailing_comment(cells[j], enable=True, marker="##")

            lead_text = cells[0]
            copies, copies_explicit, holes, iter_select, page_preset, layout_tail, marks_tail, slot_select, slot_select_mode = _parse_lead_to_meta(lead_text)
            if copies <= 0:
                _l.i("row skipped due to copies <= 0")
                continue

            # Row payload is positional (cells aligned with headers). Headers may repeat.
            base = {"cells": [cells[i + 1] for i in active_idx]}
            base["__dm_copies__"] = copies
            base["__dm_copies_explicit__"] = bool(copies_explicit)
            base["__dm_holes__"] = holes
            base["__dm_iter_select__"] = iter_select
            if slot_select:
                base["__dm_slot_select__"] = slot_select
                base["__dm_slot_select_mode__"] = str(slot_select_mode or "").strip()
            if page_preset:
                base["__dm_page__"] = page_preset
            if layout_tail:
                base["__dm_layout__"] = layout_tail
            if marks_tail:
                base["__dm_marks__"] = marks_tail

            current["rows"].append(base)

        if current is not None and current.get("headers"):
            datasets.append(current)

        return datasets

    # --- Shorthand single dataset mode (no markers) ---
    # Find first non-blank, non-comment row as header row.
    # Keep '#...' rows as directives/comments before header.
    # '##...' rows are ignored. '####' stops parsing.
    header_row = None
    header_idx = None
    comments_shorthand: List[List[str]] = []
    for i, r in enumerate(matrix or []):
        if r is None:
            continue
        raw_cells = [_norm_cell(c) for c in r]
        if _row_is_eof_comment(raw_cells):
            break
        if _row_is_hard_comment(raw_cells):
            continue
        if _row_is_comment(raw_cells):
            rc = _apply_inline_row_comments(raw_cells)
            comments_shorthand.append(rc)
            continue

        cells = _apply_inline_row_comments(raw_cells)
        if _is_blank_row(cells):
            continue
        header_row = cells
        header_idx = i
        break

    if header_row is None:
        # Allow comment-only datasets (common for pnpink_ini.csv defaults):
        # keep comment directives even if there is no tabular dataset.
        return [{"meta": {}, "headers": [], "rows": [], "comments": list(comments_shorthand)}]

    # A1 declares template bbox id (shorthand), optionally wrapped as {{...}}
    c0 = str(header_row[0]).strip() if len(header_row) > 0 else ""
    decl = DSL.parse_dataset_decl(c0, allow_bare=True) or {}
    templates_bbox_ids = list((decl or {}).get("template_bbox") or [])

    # Robustness: if user uses the simplest shorthand (A1="id") and for any reason
    # the DSL decl parser doesn't recognize it, still treat it as template_bbox.
    # This keeps A1="id" and A1="{{id}}" equivalent to A1="{{t=id}}".
    if not templates_bbox_ids:
        _id_like = re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", c0 or "") is not None
        if _id_like and not (c0.endswith(".") or c0.endswith("_")):
            templates_bbox_ids = [c0]

    if len(templates_bbox_ids) > 1:
        raise inkex.AbortExtension(
            "Multi-template por lista ya no está soportado.\n"
            "Usa un único main template en A1: {{t=MAIN_BBOX_ID}}\n"
            "y declara templates adicionales con columnas de header {t=OTRO_BBOX_ID}."
        )
    main_bbox_id = templates_bbox_ids[0] if templates_bbox_ids else None

    headers_raw = [str(_strip_cell_trailing_comment(h or "", enable=True, marker="##")).strip() for h in header_row[1:]]
    headers_raw = _expand_property_only_headers(headers_raw)
    headers_raw, active_idx = _apply_header_disabling(headers_raw)
    headers, template_cols = _extract_template_columns(headers_raw)
    _l.d(f"dataset.headers={headers} (shorthand; main_bbox_id={main_bbox_id} templates={[c.get('bbox_id') for c in (template_cols or [])]})")

    ds = {
        "meta": {
            "templates_bbox_ids": ([main_bbox_id] if main_bbox_id else []),
            "template_cols": template_cols,
            "overlay_template_cols": [c for c in (template_cols or []) if not (set(c.get('mods') or []) & {'@page','@back'})],
            "overlay_templates_bbox_ids": [c.get('bbox_id') for c in (template_cols or []) if not (set(c.get('mods') or []) & {'@page','@back'})],
        },
        "headers": headers,
        "rows": [],
        "comments": list(comments_shorthand),
    }

    # Parse data rows after header
    for r in (matrix or [])[header_idx + 1:]:
        if r is None or len(r) == 0:
            continue
        raw_cells = [_norm_cell(c) for c in r]
        if _row_is_eof_comment(raw_cells):
            break
        if _row_is_hard_comment(raw_cells):
            continue
        if _row_is_comment(raw_cells):
            ds.setdefault("comments", []).append(_apply_inline_row_comments(raw_cells))
            continue

        cells = _apply_inline_row_comments(raw_cells)
        if _is_blank_row(cells):
            # Preserve blank rows as placeholder slots inside the dataset (do not shift subsequent cards).
            base = {"cells": ['' for _ in range(0, len(headers))]}
            base['__dm_copies__'] = 1
            base['__dm_copies_explicit__'] = False
            base['__dm_holes__'] = []
            ds.setdefault('rows', []).append(base)
            continue
        cells = _normalize_row_cells_for_headers(cells, len(headers), active_idx)

        # Apply per-cell comments (##) uniformly.
        cells[0] = _strip_cell_trailing_comment(cells[0], enable=True, marker="##")
        for j in range(1, len(cells)):
            cells[j] = _strip_cell_trailing_comment(cells[j], enable=True, marker="##")

        lead_text = cells[0]
        copies, copies_explicit, holes, iter_select, page_preset, layout_tail, marks_tail, slot_select, slot_select_mode = _parse_lead_to_meta(lead_text)
        if copies <= 0:
            _l.i("row skipped due to copies <= 0")
            continue

        # Row payload is positional (cells aligned with headers). Headers may repeat.
        base = {"cells": [cells[i + 1] for i in active_idx]}
        base["__dm_copies__"] = copies
        base["__dm_copies_explicit__"] = bool(copies_explicit)
        base["__dm_holes__"] = holes
        base["__dm_iter_select__"] = iter_select
        if slot_select:
            base["__dm_slot_select__"] = slot_select
            base["__dm_slot_select_mode__"] = str(slot_select_mode or "").strip()
        if page_preset:
            base["__dm_page__"] = page_preset
        if layout_tail:
            base["__dm_layout__"] = layout_tail
        if marks_tail:
            base["__dm_marks__"] = marks_tail

        ds["rows"].append(base)

    return [ds]



def _read_csv_matrix(path: str) -> List[List[str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        matrix = [[strip_bom(c) for c in row] for row in csv.reader(f, delimiter=",")]
    # Raw dataset log (user request)
    for i, r in enumerate(matrix, start=1):
        c0 = r[0] if r else ""
        _l.d(f"dataset.raw#{i}: cell0='{c0}' row={r}")
    return matrix


def _load_ini_datasets(base_dir: str, *, warn_if_missing: bool = False) -> Optional[List]:
    """Load pnpink_ini.csv from base_dir as default directives."""
    ini_path = os.path.join(base_dir, "pnpink_ini.csv")
    if not os.path.isfile(ini_path):
        if warn_if_missing:
            _l.w(f"pnpink_ini.csv not found at {ini_path} (optional; continuing without defaults)")
        else:
            _l.d(f"pnpink_ini.csv not found at {ini_path} (optional)")
        return None

    try:
        matrix = _read_csv_matrix(ini_path)
        if not matrix:
            _l.w("pnpink_ini.csv is empty")
            return None
        datasets = _matrix_to_datasets(matrix)
        if not datasets:
            _l.w("pnpink_ini.csv produced no datasets")
            return None
        _l.i(f"pnpink_ini.csv loaded: {len(datasets)} dataset(s)")
        return datasets
    except Exception as ex:
        _l.w(f"Error loading pnpink_ini.csv: {ex}")
        return None


def load_csv_datasets(csv_path: str):
    """Load a CSV file and split it into DeckMaker dataset sections."""
    return _matrix_to_datasets(_read_csv_matrix(csv_path))


def _split_selector(selector: Optional[str]) -> Tuple[str, str]:
    """Return (kind, value) for sheet selector.

    Kinds:
      - ""      : empty selector
      - "gid"   : numeric gid (public export CSV)
      - "range" : sheet/range selector, e.g. "Sheet1!A1:Z99"
      - "sheet" : plain sheet title
    """
    s = str(selector or "").strip()
    if not s:
        return "", ""
    if re.fullmatch(r"\d+", s):
        return "gid", s
    if "!" in s:
        return "range", s
    return "sheet", s


def _choose_sheet_and_range(effect, sheet_id: str, selector: Optional[str]) -> str:
    kind, val = _split_selector(selector)
    if kind == "range":
        # Keep current behavior: if "Sheet!" has empty cells part, default to A1:Z999.
        sh, cells = val.split("!", 1)
        sh = (sh or "").strip()
        cells = (cells or "").strip() or "A1:Z999"
        return f"{sh}!{cells}"
    if kind == "sheet":
        return f"{val}!A1:Z999"
    # gid / empty -> keep oauth behavior unchanged (by SVG name, else first sheet)
    doc_path = effect._document_path_or_abort()
    svg_stem = os.path.splitext(os.path.basename(doc_path))[0]
    titles = _gs.list_sheet_titles(sheet_id)
    sheet_name = next((t for t in titles if t.strip().lower()==svg_stem.strip().lower()), (titles[0] if titles else "Sheet1"))
    return f"{sheet_name}!A1:Z999"


def _fetch_gsheet_matrix_oauth(effect, sheet_id: str, selector: Optional[str], client_id_env: Optional[str]) -> List[List[str]]:
    rng = _choose_sheet_and_range(effect, sheet_id, selector)
    values = _gs.fetch_sheet(sheet_id, rng, client_id_env or None)
    matrix = [[("" if v is None else str(v)) for v in r] for r in values]
    # Raw dataset log (user request)
    for i, r in enumerate(matrix, start=1):
        c0 = r[0] if r else ""
        _l.d(f"dataset.raw(GSheet OAuth)#{i}: cell0='{c0}' row={r}")
    return matrix


def _fetch_gsheet_matrix_public(effect, sheet_id: str, selector: Optional[str]) -> Optional[List[List[str]]]:
    """Best-effort public fetch (no OAuth) for link-shared sheets.

    Strategy:
      - If range has explicit sheet name (Sheet!A1:Z999), use gviz CSV directly.
      - Otherwise, try with SVG stem as sheet name.
      - Finally, try first-sheet export by gid=0.
    Returns None when every public attempt fails.
    """
    sid = str(sheet_id or "").strip()
    if not sid:
        return None

    kind, val = _split_selector(selector)

    urls = []
    if kind == "gid":
        urls.append(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={val}")
    elif kind == "":
        # Requested behavior: public + blank selector => gid=0.
        urls.append(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=0")
    elif kind == "range":
        sheet_name, cells = val.split("!", 1)
        sheet_name = (sheet_name or "").strip()
        cells = (cells or "").strip() or "A1:Z999"
        q_sheet = urllib.parse.quote(sheet_name, safe="")
        q_cells = urllib.parse.quote(cells, safe="!:$")
        urls.append(f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={q_sheet}&range={q_cells}")
    elif kind == "sheet":
        # Keep compatibility when user provides sheet name only.
        q_sheet = urllib.parse.quote(val, safe="")
        q_cells = urllib.parse.quote("A1:Z999", safe="!:$")
        urls.append(f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={q_sheet}&range={q_cells}")
    else:
        urls.append(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=0")

    for u in urls:
        try:
            raw, _hdrs, _status = NET.fetch_text(
                u,
                headers={
                    "User-Agent": "PnPInk/gsheet-public",
                    "Accept": "text/csv,*/*;q=0.8",
                },
                timeout=12,
                retries=3,
                encoding="utf-8-sig",
                errors="replace",
                log_prefix="[datasets] gsheet public",
            )
            rows = list(csv.reader(io.StringIO(raw), delimiter=","))
            matrix = [[("" if v is None else str(v)) for v in r] for r in rows]
            if not matrix:
                continue
            for i, r in enumerate(matrix, start=1):
                c0 = r[0] if r else ""
                _l.d(f"dataset.raw(GSheet Public)#{i}: cell0='{c0}' row={r}")
            _l.i(f"[datasets] gsheet public fetch ok url='{u}' rows={len(matrix)}")
            return matrix
        except Exception as ex:
            _l.d(f"[datasets] gsheet public fetch failed url='{u}': {ex}")
            continue
    return None


def _fetch_gsheet_matrix(
    effect,
    sheet_id: str,
    selector: Optional[str],
    client_id_env: Optional[str],
    access_hint: str = "",
) -> Tuple[List[List[str]], str]:
    hint = str(access_hint or "").strip().lower()
    order = ["public", "oauth"] if hint != "oauth" else ["oauth", "public"]
    last_err = None
    for mode in order:
        try:
            if mode == "public":
                m = _fetch_gsheet_matrix_public(effect, sheet_id, selector)
                if m is not None:
                    return m, "public"
                continue
            m = _fetch_gsheet_matrix_oauth(effect, sheet_id, selector, client_id_env)
            return m, "oauth"
        except Exception as ex:
            last_err = ex
            _l.d(f"[datasets] gsheet {mode} fetch failed: {ex}")
            continue
    if last_err is not None:
        raise last_err
    return [], ""

# --------------------- pages ---------------------


def _headers_are_valid(headers):
    """A dataset marker row is only meaningful if it declares at least one non-empty header in columns B+."""
    if not headers:
        return False
    for h in headers:
        if str(h or "").strip() != "":
            return True
    return False


# --------------------- main extension ---------------------

def resolve_csv(options, base_dir: str, svg_stem: str) -> str:
    p = (getattr(options, 'csv_path', '') or '').strip()
    return p if os.path.isabs(p) else os.path.join(base_dir, (p or f"{svg_stem}.csv"))

def load_datasets(effect, doc_path: Optional[str] = None):
    """Compatibility wrapper: behavior extracted from DeckMaker._load_dataset().

    `pnpink_ini.csv` is loaded in this order:
      1) extension directory (where deckmaker.py resides)
      2) SVG/document directory
      3) main dataset source (CSV/GSheet)
    Directives are applied in that order, so later definitions override earlier ones.

    NOTE: we keep the original calling convention (pass the EffectExtension instance)
    because _choose_sheet_and_range needs access to effect._document_path_or_abort().
    """
    options = getattr(effect, 'options', effect)

    sheet_id = (getattr(options, 'sheet_id', '') or '').strip()
    range_a1 = (getattr(options, 'sheet_range', '') or '').strip()
    client_id = os.environ.get('PNPINK_GSHEETS_CLIENT_ID') or _gs.CLIENT_ID

    base_dir = None
    ini_datasets = []
    if doc_path:
        base_dir = os.path.dirname(doc_path)
    elif not sheet_id:
        doc_path = effect._document_path_or_abort()
        base_dir = os.path.dirname(doc_path)

    # A) defaults from extension directory (where deckmaker.py resides) - warn if missing
    ext_dir = os.path.dirname(os.path.abspath(__file__))
    ext_ini = _load_ini_datasets(ext_dir, warn_if_missing=True)
    if ext_ini:
        ini_datasets.extend(ext_ini)

    # B) defaults from SVG directory - no warning if missing
    if base_dir:
        svg_ini = _load_ini_datasets(base_dir, warn_if_missing=False)
        if svg_ini:
            ini_datasets.extend(svg_ini)

    # 1) Read matrix
    used_access_mode = ""
    if sheet_id:
        mode_hint = str(getattr(options, 'dataset_source_mode', '') or '').strip().lower()
        access_hint = "oauth" if mode_hint in {"oauth", "google_sheet_oauth"} else ""
        if mode_hint in {"public", "google_sheet_public"}:
            access_hint = "public"
        try:
            if not access_hint and doc_path:
                rec = DSTATE.get_gsheet_for_svg(doc_path) or {}
                sid0 = str(rec.get("sheet_id") or "").strip()
                if sid0 and sid0 == sheet_id:
                    access_hint = str(rec.get("access_mode") or "").strip().lower()
        except Exception:
            access_hint = ""
        matrix, used_access_mode = _fetch_gsheet_matrix(effect, sheet_id, range_a1, client_id, access_hint=access_hint)
        if used_access_mode:
            _l.i(f"[datasets] gsheet access mode='{used_access_mode}'")
            try:
                setattr(options, "_dataset_access_mode", used_access_mode)
            except Exception:
                pass
            try:
                if doc_path:
                    DSTATE.set_gsheet_for_svg(doc_path, sheet_id, range_a1 or "", used_access_mode)
            except Exception:
                pass
    else:
        # Keep original behavior: require a saved SVG and a CSV file (unless sheet_id is used).
        if not doc_path:
            doc_path = effect._document_path_or_abort()
        if not base_dir:
            base_dir = os.path.dirname(doc_path)
        svg_stem = os.path.splitext(os.path.basename(doc_path))[0]
        csv_path = resolve_csv(options, base_dir, svg_stem)
        if not os.path.isfile(csv_path):
            raise inkex.AbortExtension(
                f"CSV not found.\n  Tried: {csv_path}\nSet --csv_path or use Google Sheets."
            )
        matrix = _read_csv_matrix(csv_path)

    if not matrix:
        return []

    # 2) Normalize into datasets
    datasets = _matrix_to_datasets(matrix)
    if not datasets:
        return []

    # 3) Merge defaults from pnpink_ini.csv as first comment directives.
    if ini_datasets and datasets:
        ini_comments = []
        for ini_ds in ini_datasets:
            ini_comments.extend((ini_ds.get("comments", []) or []))
        if ini_comments:
            main_comments = datasets[0].get("comments", []) or []
            datasets[0]["comments"] = ini_comments + main_comments
            _l.i(f"[datasets] merged {len(ini_comments)} default comment lines from pnpink_ini.csv")

    # 4) Validate datasets (must have at least one non-empty header in columns B+)
    valid = [ds for ds in datasets if _headers_are_valid(ds.get('headers'))]
    if not valid:
        _l.e("Dataset has no valid header.")
        return []

    # 5) Diagnostic log (kept identical)
    src = "GSheet" if sheet_id else "CSV"
    _l.i(f"[datasets] src={src} detected={len(datasets)} valid={len(valid)}")
    for i, ds in enumerate(valid, start=1):
        meta = ds.get("meta", {}) or {}
        t = list(meta.get("templates_bbox_ids") or [])
        overlays = list(meta.get("overlay_templates_bbox_ids") or [])
        headers = ds.get("headers", []) or []
        rows = ds.get("rows", []) or []
        comments = ds.get("comments", []) or []
        _l.i(f"[datasets] #{i}: headers={len(headers)} rows={len(rows)} comments={len(comments)} t={t} overlays={overlays}")

    return valid

