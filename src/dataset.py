# -*- coding: utf-8 -*-
# [2026-02-18 | v0.20+] Align comment semantics with documented rules.
# [2026-02-16 | v0.20+] Header '#' and '##' column disabling semantics added.
import os, re, csv

import log as LOG
_l = LOG
import gsheets_client_pkce as GS
_gs = GS
from typing import List, Optional, Tuple

import inkex
import dsl as DSL
import header_flags as HF


def strip_bom(s: str) -> str:
    return s.lstrip("\ufeff") if isinstance(s, str) else s


def _row_is_comment(cells: List[str]) -> bool:
    """Comment row (only outside the dataset).

    - '#...'  -> comment (kept for directives like snippets)
    - '##...' -> hard comment: ignored completely (not kept)

    Note: inside the dataset this function is NOT used; there '#' is per-cell.
    """
    if not cells:
        return False
    # Allow indentation in sheets/CSV without introducing dataset ambiguity,
    # we consider it a comment if the first *non-whitespace* char is '#'.
    c0 = str(cells[0] or "")
    c0l = c0.lstrip()
    return c0l.startswith("#") and not c0l.startswith("##")


def _row_is_hard_comment(cells: List[str]) -> bool:
    """Hard comment (only outside the dataset): '##' at the start of the line."""
    if not cells:
        return False
    c0 = str(cells[0] or "")
    return c0.lstrip().startswith("##")

def _strip_line_trailing_hard_comment(text: str) -> str:
    """Outside dataset: if a comment/directive line has trailing '##', strip from there."""
    s = str(text or "")
    s_l = s.lstrip()
    if not s_l.startswith("#") or s_l.startswith("##"):
        return s
    k = s.find("##")
    if k < 0:
        return s
    return s[:k].rstrip()


def _strip_cell_trailing_comment(text: str, enable: bool = True) -> str:
    """Dataset cell comments (pre-DSL).

    New semantics (simplified):
      - A '#' in any position comments out the rest of the cell.
        (Keep what comes before '#', with rstrip.)

    Note: the special case of '##' inside the dataset is NOT resolved here.
    The active rule is: if '##' appears at the start of the first cell (column A),
    the row is fully suppressed (see parse loops in _matrix_to_datasets()).
    """
    if text is None:
        return ""
    if not enable:
        return str(text)
    s = str(text)
    # If the cell starts with '##' we do not cut here; the row rule handles it.
    if s.lstrip().startswith("##"):
        return s
    k = s.find("#")
    if k < 0:
        return s
    return s[:k].rstrip()

parse_template_header_cell = HF.parse_template_header_cell


def _is_nontext_dataset_field(header_key: str) -> bool:
    """Minimal heuristic to decide if a dataset cell can use '#' as a comment.

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
       - '#col'   -> disable this column
       - '##col'  -> disable this and all columns to the right
       - '#' not at start already handled as trailing comment.
       Returns filtered headers list and active index list.
    """
    active = []
    disable_all_right = False
    for i, h in enumerate(headers_raw):
        hs = str(h or "").strip()
        if disable_all_right:
            continue
        if hs.startswith("##"):
            disable_all_right = True
            continue
        if hs.startswith("#"):
            continue
        active.append(i)
    headers = [headers_raw[i] for i in active]
    return headers, active


def _extract_template_columns(headers):
    return HF.extract_template_columns(headers)


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
        page_preset = None
        layout_tail = None
        marks_tail = None
        copies_explicit = False
        if lead is not None:
            copies = int(getattr(lead, "copies", 1) or 1)
            holes = list(getattr(lead, "holes", []) or [])
            page_preset = getattr(lead, "page_block", None)
            layout_tail = getattr(lead, "layout_block", None)
            marks_tail = getattr(lead, "marks_block", None)
            copies_explicit = bool(getattr(lead, "copies_explicit", False))

        _l.d(f"dataset.row_cell0='{lead_text}' → copies={copies} explicit={copies_explicit} page={page_preset} L={layout_tail} M={marks_tail}")
        return copies, copies_explicit, holes, page_preset, layout_tail, marks_tail

    # --- Pre-scan to detect any explicit marker rows {{...}} in column A ---
    has_markers = False
    for r in (matrix or []):
        if not r:
            continue
        # Column A may contain DSL/marker; we apply '#' comment semantics here
        # to minimize noise in the marker parser.
        c0 = str(_strip_cell_trailing_comment(_norm_cell(r[0]), enable=True)).strip() if len(r) > 0 else ""
        if DSL.parse_dataset_decl(c0, allow_bare=False) is not None:
            has_markers = True
            break

    datasets = []

    # --- Marker mode: multiple datasets in one sheet ---
    if has_markers:
        current = None
        # Preserve comment rows *only before the first marker* so they can be used for
        # snippet definitions / globals / spritesheets.
        #
        # Outside-dataset semantics:
        #   - '#...'  -> preserved in pending_comments
        #   - '##...' -> ignored completely ("comment directives too")
        pending_comments: List[List[str]] = []
        for r in (matrix or []):
            if r is None or len(r) == 0:
                continue
            raw_cells = [_norm_cell(c) for c in r]

            # --- Outside dataset (before first marker): row-level comments/directives ---
            if current is None:
                if _row_is_hard_comment(raw_cells):
                    continue
                if _row_is_comment(raw_cells):
                    rc = list(raw_cells)
                    if rc:
                        rc[0] = _strip_line_trailing_hard_comment(rc[0])
                    pending_comments.append(rc)
                    continue

            # --- Inside dataset ---
            # Hard rule: if the first cell starts with '##', the row is suppressed
            # fully (as if it did not exist).
            lead0 = _norm_cell(r[0]) if len(r) > 0 else ""
            if str(lead0).lstrip().startswith("##"):
                continue

            # 1) always allow '#...' as a comment in column A (leading cell)
            # 2) in the rest of cells, ONLY if they are non-text fields (header heuristic)
            #    and only once we have headers.
            cells_raw = [_norm_cell(c) for c in r]
            cells = list(cells_raw)

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
            # Inside the dataset there are no longer "comment rows" (col A starting with '#'):
            # this now means an empty cell. Directives go BEFORE the marker.

            # Apply '#' comments (only where applicable) before parsing marker or lead.
            # Note: here we still do not know if this is a marker or data row.
            cells[0] = _strip_cell_trailing_comment(cells[0], enable=True)
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

                # In headers we do not expect literal '#'; allow comments for cleanup.
                headers_raw = [str(_strip_cell_trailing_comment(h or "", enable=True)).strip() for h in cells[1:]]
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
            # normalize row length to header width (+ lead col)
            if len(cells) < len(headers) + 1:
                cells += [""] * ((len(headers) + 1) - len(cells))
            elif len(cells) > len(headers) + 1:
                cells = cells[:len(headers) + 1]

            # Apply per-cell comments conservatively.
            cells[0] = _strip_cell_trailing_comment(cells[0], enable=True)
            for j in range(1, len(cells)):
                h = headers[j - 1] if (j - 1) < len(headers) else ""
                cells[j] = _strip_cell_trailing_comment(cells[j], enable=_is_nontext_dataset_field(h))

            lead_text = cells[0]
            copies, copies_explicit, holes, page_preset, layout_tail, marks_tail = _parse_lead_to_meta(lead_text)
            if copies <= 0:
                _l.i("row skipped due to copies <= 0")
                continue

            # Row payload is positional (cells aligned with headers). Headers may repeat.
            base = {"cells": [cells[i + 1] for i in active_idx]}
            base["__dm_copies__"] = copies
            base["__dm_copies_explicit__"] = bool(copies_explicit)
            base["__dm_holes__"] = holes
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
    # Keep comment rows *before header* so they can be used for snippet definitions, etc.
    # '##' rows are ignored completely.
    header_row = None
    header_idx = None
    comments_shorthand: List[List[str]] = []
    for i, r in enumerate(matrix or []):
        if r is None:
            continue
        raw_cells = [_norm_cell(c) for c in r]
        if _row_is_hard_comment(raw_cells):
            continue
        if _row_is_comment(raw_cells):
            rc = list(raw_cells)
            if rc:
                rc[0] = _strip_line_trailing_hard_comment(rc[0])
            comments_shorthand.append(rc)
            continue

        # Outside dataset (shorthand): we still do not know if this row is the header.
        # Here we do apply '#' as a comment (directive compatibility),
        # but we do NOT apply the dataset '##' rule (that only applies *inside*).
        cells = [_strip_cell_trailing_comment(_norm_cell(c), enable=True) for c in r]
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

    headers_raw = [str(_strip_cell_trailing_comment(h or "")).strip() for h in header_row[1:]]
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
        # Inside the dataset (after header):
        #   - '##' at the start of the first cell => suppress full row.
        lead0 = _norm_cell(r[0]) if len(r) > 0 else ""
        if str(lead0).lstrip().startswith("##"):
            continue

        cells = [_norm_cell(c) for c in r]
        if _is_blank_row(cells):
            # Preserve blank rows as placeholder slots inside the dataset (do not shift subsequent cards).
            base = {"cells": ['' for _ in range(0, len(headers))]}
            base['__dm_copies__'] = 1
            base['__dm_copies_explicit__'] = False
            base['__dm_holes__'] = []
            ds.setdefault('rows', []).append(base)
            continue
        # Inside the dataset (after the header) we do not treat rows as "comment rows".

        # normalize row length to header width (+ lead col)
        if len(cells) < len(headers) + 1:
            cells += [""] * ((len(headers) + 1) - len(cells))
        elif len(cells) > len(headers) + 1:
            cells = cells[:len(headers) + 1]

        # Apply per-cell comments conservatively.
        cells[0] = _strip_cell_trailing_comment(cells[0], enable=True)
        for j in range(1, len(cells)):
            h = headers[j - 1] if (j - 1) < len(headers) else ""
            cells[j] = _strip_cell_trailing_comment(cells[j], enable=_is_nontext_dataset_field(h))

        lead_text = cells[0]
        copies, copies_explicit, holes, page_preset, layout_tail, marks_tail = _parse_lead_to_meta(lead_text)
        if copies <= 0:
            _l.i("row skipped due to copies <= 0")
            continue

        # Row payload is positional (cells aligned with headers). Headers may repeat.
        base = {"cells": [cells[i + 1] for i in active_idx]}
        base["__dm_copies__"] = copies
        base["__dm_copies_explicit__"] = bool(copies_explicit)
        base["__dm_holes__"] = holes
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


def _choose_sheet_and_range(effect, sheet_id: str, range_a1: Optional[str]) -> str:
    rng = (range_a1 or "").strip()
    if "!" in rng:
        return rng if rng.split("!",1)[1] else (rng + "A1:Z999")
    doc_path = effect._document_path_or_abort()
    svg_stem = os.path.splitext(os.path.basename(doc_path))[0]
    titles = _gs.list_sheet_titles(sheet_id)
    sheet_name = next((t for t in titles if t.strip().lower()==svg_stem.strip().lower()), (titles[0] if titles else "Sheet1"))
    cells = "A1:Z999" if not rng else rng
    return f"{sheet_name}!{cells}"


def _fetch_gsheet_matrix(effect, sheet_id: str, range_a1: Optional[str], client_id_env: Optional[str]) -> List[List[str]]:
    rng = _choose_sheet_and_range(effect, sheet_id, range_a1)
    values = _gs.fetch_sheet(sheet_id, rng, client_id_env or None)
    matrix = [[("" if v is None else str(v)) for v in r] for r in values]
    # Raw dataset log (user request)
    for i, r in enumerate(matrix, start=1):
        c0 = r[0] if r else ""
        _l.d(f"dataset.raw(GSheet)#{i}: cell0='{c0}' row={r}")
    return matrix

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
    if sheet_id:
        matrix = _fetch_gsheet_matrix(effect, sheet_id, range_a1, client_id)
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
                f"CSV no encontrado...\n  intenté: {csv_path}\nIndica --csv_path o usa Google Sheet."
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
        _l.e("Dataset sin cabecera válida.")
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

