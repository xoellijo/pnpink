# -*- coding: utf-8 -*-
"""Build and render one cached dataset asset for DeckMaker Web preview."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shlex
from types import SimpleNamespace

import app_paths as APPPATHS
import dataset as DATASET
import dsl as DSL
import render_tokens as RENDER_TOKENS
import snippets as SNIPPETS
import sources as SOURCES
import temp_paths as TEMPPATHS


PREVIEW_CACHE_VERSION = 19
SYMBOL_CACHE_VERSION = 4

_PREVIEW_SPRITESHEETS = "__dm_preview_spritesheets__"
_ITERATOR_EXPRESSION_RE = re.compile(
    r"(?<!\S)(?P<stars>\*+)\s*(?P<expression>@[A-Za-z][\w.\-]*\[[^\]]+\]|\[[^\]]*\])"
)

_REFERENCE_RE = re.compile(r"url\(\s*#([^\s)]+)\s*\)")
_HREF_NAMES = {"href", "{http://www.w3.org/1999/xlink}href"}


def _node_references(node) -> set[str]:
    references: set[str] = set()
    for attribute, raw_value in node.attrib.items():
        value = str(raw_value or "").strip()
        references.update(_REFERENCE_RE.findall(value))
        if attribute in _HREF_NAMES and value.startswith("#"):
            references.add(value[1:])
    references.update(_REFERENCE_RE.findall(str(node.text or "")))
    return references


def _prune_preview_dependencies(document) -> None:
    """Discard template nodes and defs that the generated preview cannot reach."""
    from lxml import etree

    root = document.getroot()
    output = next((node for node in root.iter() if node.get("id") == "pnpink-output"), None)
    if output is None:
        return

    nodes_by_id = {
        str(node.get("id")): node
        for node in root.iter()
        if str(node.get("id") or "").strip()
    }
    required_ids: set[str] = set()
    pending = set()
    for node in output.iter():
        pending.update(_node_references(node))
    while pending:
        reference = pending.pop()
        if reference in required_ids:
            continue
        required_ids.add(reference)
        target = nodes_by_id.get(reference)
        if target is None:
            continue
        for node in target.iter():
            pending.update(_node_references(node) - required_ids)

    required_nodes = set(output.iter())
    required_nodes.update(output.iterancestors())
    for reference in required_ids:
        target = nodes_by_id.get(reference)
        if target is None:
            continue
        required_nodes.update(target.iterancestors())
        required_nodes.update(target.iter())

    for parent in list(root.iter()):
        for child in list(parent):
            if child in required_nodes:
                continue
            local_name = etree.QName(child).localname
            if local_name in {"namedview", "metadata", "style"}:
                continue
            parent.remove(child)


def _isolate_generated_output(effect) -> None:
    import svg_chunks as SVGCHUNKS

    SVGCHUNKS.prepare_full_output_doc(
        effect.document,
        source_svg_path=effect.document_path(),
        absolutize_images=False,
    )
    _prune_preview_dependencies(effect.document)


def _document_digest(effect) -> str:
    from lxml import etree

    root = deepcopy(effect.document.getroot())
    for child in list(root):
        if etree.QName(child).localname in {"namedview", "metadata"}:
            root.remove(child)
    xlink_href = "{http://www.w3.org/1999/xlink}href"
    for node in root.iter():
        local_name = etree.QName(node).localname
        if local_name == "tspan":
            node.attrib.pop("id", None)
        elif local_name == "image":
            absolute_href = str(node.get(xlink_href) or "").strip()
            if absolute_href.startswith("file:"):
                node.set("href", absolute_href)
    payload = etree.tostring(root, method="c14n")
    return hashlib.sha256(payload).hexdigest()


def _persistent_cache_dir(effect) -> str:
    document_path = os.path.normcase(os.path.abspath(str(effect.document_path() or "template.svg")))
    document_key = hashlib.sha256(document_path.encode("utf-8")).hexdigest()[:12]
    directory = APPPATHS.data_path(
        "cache",
        "deckmaker_preview",
        f"{TEMPPATHS.stem_for_path(document_path)}-{document_key}",
    )
    directory.mkdir(parents=True, exist_ok=True)
    return os.path.normpath(str(directory))


def clear_cache(effect) -> int:
    """Remove every generated preview for the active template."""
    directory = Path(_persistent_cache_dir(effect))
    removed = 0
    for pattern in ("asset-*.svg", "asset-*.png", "symbol-base-*.svg"):
        for path in directory.glob(pattern):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed


def datasets_signature(datasets: list[dict]) -> str:
    payload = json.dumps(datasets, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _render_signature_datasets(datasets: list[dict]) -> list[dict]:
    sanitized = []
    for source in datasets:
        dataset = dict(source)
        dataset["rows"] = [
            {key: value for key, value in row.items() if key != "__dm_sheet_row__"}
            for row in source.get("rows") or []
        ]
        sanitized.append(dataset)
    return sanitized


def asset_signatures(datasets: list[dict]) -> dict[str, str]:
    signatures = {}
    for entry in catalog(datasets):
        selected, _label, _selector = _selection(datasets, entry)
        payload = json.dumps(
            _render_signature_datasets(selected),
            sort_keys=True, default=str, ensure_ascii=False,
        ).encode("utf-8")
        signatures[entry["key"]] = hashlib.sha256(payload).hexdigest()
    return signatures


def _spritesheet_metadata(dataset: dict | None) -> dict:
    return dict((dataset or {}).get(_PREVIEW_SPRITESHEETS) or {})


def _spritesheet_definition(metadata: dict):
    return SimpleNamespace(**metadata)


def _parse_preview_spritesheets(comment_lines, resolver: SOURCES.PathResolver) -> dict:
    definitions = {}
    for row in comment_lines or []:
        first = str((row[0] if isinstance(row, (list, tuple)) and row else row) or "").lstrip()
        if not first.startswith("#") or first.startswith("##"):
            continue
        match = re.match(r"^#\s*@(?P<alias>[A-Za-z][\w.\-]*)\s*=\s*(?P<rhs>.+)$", first)
        if not match:
            continue
        chain = DSL.maybe_parse_chain(match.group("rhs").strip())
        if chain is None:
            continue
        layout = next(
            (
                module.spec
                for module in chain.modules
                if str(module.name or "").lower() in {"layout", "l"} and module.spec is not None
            ),
            None,
        )
        grid = getattr(layout, "grid", None)
        if grid is None or int(grid.cols or 0) <= 0 or int(grid.rows or 0) <= 0:
            continue
        source = str(getattr(chain.target, "src", "") or "").strip().strip("\"'")
        page_count = 1
        resolved = resolver.resolve_logical(source) if source else None
        if resolved is not None and resolved.suffix.lower() == ".pdf":
            try:
                from deckmaker_pdf_spritesheet import pdf_page_count
                page_count = max(1, int(pdf_page_count(resolved) or 1))
            except Exception:
                page_count = 1
        order = str(getattr(grid, "order", "") or "").lower()
        flip = str(getattr(grid, "flip", "") or "").lower()
        definitions[match.group("alias")] = {
            "cols": int(grid.cols),
            "rows": int(grid.rows),
            "page_count": page_count,
            "sweep_rows_first": order not in {"tb-lr", "tblr", "tb_lr"},
            "invert_rows": flip == "v",
            "invert_cols": flip == "h",
        }
    return definitions


def annotate_spritesheet_iterators(effect, datasets: list[dict]) -> None:
    """Attach lightweight selector metadata used by the preview catalog."""
    if not datasets:
        return
    if all(_PREVIEW_SPRITESHEETS in dataset for dataset in datasets):
        return
    resolver = SOURCES.PathResolver(effect.document_path())
    global_comments = next(
        (dataset.get("comments") or [] for dataset in datasets if dataset.get("comments")),
        [],
    )
    global_definitions = _parse_preview_spritesheets(global_comments, resolver)
    for dataset in datasets:
        definitions = dict(global_definitions)
        comments = dataset.get("comments") or []
        if comments is not global_comments:
            definitions.update(_parse_preview_spritesheets(comments, resolver))
        dataset[_PREVIEW_SPRITESHEETS] = definitions


def _iterator_tokens(value: str, spritesheets: dict | None = None) -> list[str]:
    body = str(value or "").strip()
    try:
        parsed_alias = DSL.parse_sprite_alias_token(body)
    except DSL.DSLError:
        parsed_alias = None
    if parsed_alias is not None:
        alias, selector, operations = parsed_alias
        definition_data = dict((spritesheets or {}).get(alias) or {})
        if not definition_data:
            return [body]
        tokens = RENDER_TOKENS.spritesheet_selector_tokens(
            alias,
            selector,
            _spritesheet_definition(definition_data),
        )
        suffix = f"~{operations}" if operations else ""
        return [f"{token}{suffix}" for token in tokens]
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1].strip()
    if not body:
        return [""]
    tokens = []
    current = []
    quote = ""
    depth = 0
    for character in body:
        if quote:
            current.append(character)
            if character == quote:
                quote = ""
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (character.isspace() or character == ","):
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(character)
    token = "".join(current).strip()
    if token:
        tokens.append(token)

    items = []
    for token in tokens:
        try:
            parsed = shlex.split(token, posix=True)
            token = parsed[0] if len(parsed) == 1 else token
        except ValueError:
            pass
        repetition = re.fullmatch(r"(\d+)\*(?:\((.*)\)|(.+))", token)
        if repetition:
            repeated = str(repetition.group(2) or repetition.group(3) or "").strip()
            items.extend([repeated] * int(repetition.group(1)))
            continue
        interval = re.fullmatch(r"([A-Za-z]+|\d+)\.\.([A-Za-z]+|\d+)", token)
        if interval:
            first, last = interval.groups()
            if first.isdigit() and last.isdigit():
                start, stop = int(first), int(last)
                step = 1 if stop >= start else -1
                items.extend(str(number) for number in range(start, stop + step, step))
                continue
            elif len(first) == 1 and len(last) == 1:
                start, stop = ord(first), ord(last)
                step = 1 if stop >= start else -1
                items.extend(chr(number) for number in range(start, stop + step, step))
                continue
        if token.startswith("(") and token.endswith(")"):
            token = token[1:-1].strip()
        items.append(str(token))
    return items or [""]


def _iterator_list_size(value: str, spritesheets: dict | None = None) -> int:
    return len(_iterator_tokens(value, spritesheets))


def _iterator_levels(row: dict, dataset: dict | None = None) -> dict[int, int]:
    levels: dict[int, int] = {}
    spritesheets = _spritesheet_metadata(dataset)
    for cell in row.get("cells") or []:
        text = str(cell or "")
        for match in _ITERATOR_EXPRESSION_RE.finditer(text):
            level = len(match.group("stars"))
            levels[level] = max(
                levels.get(level, 1),
                _iterator_list_size(match.group("expression"), spritesheets),
            )
    return levels


def _iterator_count(row: dict, dataset: dict | None = None) -> int:
    levels = _iterator_levels(row, dataset)
    count = 1
    for level in range(1, max(levels, default=0) + 1):
        count *= levels.get(level, 1)
    return max(1, count)


def _iterator_variant_indices(row: dict, variant: int, dataset: dict | None = None) -> list[int]:
    levels = _iterator_levels(row, dataset)
    if not levels:
        return []
    zero_based = max(0, int(variant) - 1)
    indices = []
    for level in range(1, max(levels) + 1):
        divisor = 1
        for nested in range(level + 1, max(levels) + 1):
            divisor *= levels.get(nested, 1)
        indices.append((zero_based // divisor) % levels.get(level, 1) + 1)
    return indices


def _title_column(dataset: dict) -> int | None:
    headers = list(dataset.get("headers") or [])
    for wanted in ("$title", "title", "$name", "name"):
        for index, header in enumerate(headers):
            if str(header or "").strip().lower() == wanted:
                return index

    rows = [
        row for row in dataset.get("rows") or []
        if any(str(cell or "").strip() for cell in row.get("cells") or [])
        and not str(row.get("__dm_symbol_id__") or "").strip()
    ]
    for index in range(len(headers)):
        values = [
            str((row.get("cells") or [])[index] or "").strip()
            if index < len(row.get("cells") or []) else ""
            for row in rows
        ]
        if values and all(values) and len({value.casefold() for value in values}) == len(values):
            return index
    return None


def _resolve_iterator_text(value: str, row: dict, variant: int, dataset: dict | None = None) -> str:
    text = str(value or "").strip()
    levels = _iterator_levels(row, dataset)
    if not levels:
        return text
    zero_based = max(0, int(variant) - 1)
    indices = {}
    for level in range(1, max(levels) + 1):
        divisor = 1
        for nested in range(level + 1, max(levels) + 1):
            divisor *= levels.get(nested, 1)
        indices[level] = (zero_based // divisor) % levels.get(level, 1)

    def replace(match) -> str:
        level = len(match.group("stars"))
        items = _iterator_tokens(
            match.group("expression"),
            _spritesheet_metadata(dataset),
        )
        return items[indices.get(level, 0) % len(items)] if items else ""

    return _ITERATOR_EXPRESSION_RE.sub(replace, text).strip()


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.lower() in {"br", "p", "div"} and self.parts:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div"}:
            self.parts.append(" ")


def _visible_title(value: str, registry: dict) -> str:
    expanded = SNIPPETS.expand_snippets_in_text(str(value or ""), registry)
    parser = _VisibleTextParser()
    try:
        parser.feed(expanded)
        parser.close()
        visible = "".join(parser.parts)
    except Exception:
        visible = expanded
    return " ".join(visible.split())


def _row_title(
    dataset: dict,
    row: dict,
    variant: int,
    title_column: int | None,
    snippet_registry: dict,
) -> str:
    cells = list(row.get("cells") or [])
    if title_column is None or title_column >= len(cells):
        return ""
    resolved = _resolve_iterator_text(cells[title_column], row, variant, dataset)
    return _visible_title(resolved, snippet_registry)


def _dataset_with_rows(source: dict, rows: list[dict]) -> dict:
    dataset = {
        key: deepcopy(value)
        for key, value in source.items()
        if key != "rows"
    }
    dataset["rows"] = rows
    return dataset


def catalog(datasets: list[dict]) -> list[dict]:
    """List renderable row variants, deliberately ignoring declared copies."""
    entries = []
    global_comments = next(
        (dataset.get("comments") or [] for dataset in datasets if dataset.get("comments")),
        [],
    )
    global_snippets = SNIPPETS.load_definitions_from_comments(global_comments)
    for dataset_index, dataset in enumerate(datasets, start=1):
        title_column = _title_column(dataset)
        snippet_registry = dict(global_snippets)
        snippet_registry.update(
            SNIPPETS.load_definitions_from_comments(dataset.get("comments") or [])
        )
        for row_index, row in enumerate(dataset.get("rows") or [], start=1):
            if not any(str(cell or "").strip() for cell in row.get("cells") or []):
                continue
            if str(row.get("__dm_symbol_id__") or "").strip():
                continue
            count = _iterator_count(row, dataset)
            selected = DSL.parse_index_selector_1based(str(row.get("__dm_iter_select__") or ""), size=count)
            variants = selected or list(range(1, count + 1))
            for variant in variants:
                sheet_row = int(row.get("__dm_sheet_row__") or row_index)
                label = str(sheet_row)
                if count > 1:
                    indices = _iterator_variant_indices(row, int(variant), dataset)
                    label += "." + ".".join(str(value) for value in indices)
                title = _row_title(
                    dataset, row, int(variant), title_column, snippet_registry,
                )
                if title:
                    label += f" - {title}"
                entries.append({
                    "key": f"{dataset_index}:{row_index}:{variant}",
                    "dataset": dataset_index,
                    "row": row_index,
                    "variant": int(variant),
                    "sheetRow": sheet_row,
                    "label": label,
                })
    return entries


def cell_selection(
    datasets: list[dict], sheet_row: int, source_column: int, current_key: str = "",
) -> dict | None:
    """Resolve one spreadsheet cell to its preview asset and SVG target ids."""
    candidates = [entry for entry in catalog(datasets) if int(entry["sheetRow"]) == int(sheet_row)]
    if not candidates:
        return None
    entry = next((item for item in candidates if item["key"] == current_key), candidates[0])
    dataset = datasets[int(entry["dataset"]) - 1]
    headers = list(dataset.get("headers") or [])
    source_columns = list(dataset.get("source_columns") or range(1, len(headers) + 1))
    try:
        header_index = source_columns.index(int(source_column))
    except ValueError:
        return {"entry": entry, "header": "", "targets": []}
    if header_index >= len(headers):
        return {"entry": entry, "header": "", "targets": []}
    header = str(headers[header_index] or "").strip()
    if not header or header.startswith("$") or header.startswith("__dm_"):
        return {"entry": entry, "header": header, "targets": []}
    import render_apply as RAP

    parsed = RAP.parse_header_key_full(header)
    targets = [str(value).strip() for value in parsed.get("target_ids") or [] if str(value).strip()]
    return {"entry": entry, "header": header, "targets": targets}


def selected_cell(datasets: list[dict], asset_key: str, selected_fields: list[str]) -> dict | None:
    """Resolve selected SVG source ids back to the current dataset cell."""
    import fnmatch
    import render_apply as RAP

    entry = next((item for item in catalog(datasets) if item["key"] == asset_key), None)
    if not entry:
        return None
    dataset = datasets[int(entry["dataset"]) - 1]
    headers = list(dataset.get("headers") or [])
    source_columns = list(dataset.get("source_columns") or range(1, len(headers) + 1))
    fields = {str(value).strip() for value in selected_fields if str(value).strip()}
    for index, header_value in enumerate(headers):
        header = str(header_value or "").strip()
        if not header or header.startswith("$") or header.startswith("__dm_"):
            continue
        parsed = RAP.parse_header_key_full(header)
        targets = [str(value).strip() for value in parsed.get("target_ids") or [] if str(value).strip()]
        if any(
            any(fnmatch.fnmatchcase(field, target) for field in fields)
            for target in targets
        ):
            return {
                "row": int(entry["sheetRow"]),
                "column": int(source_columns[index]),
                "header": header,
                "asset": entry["key"],
            }
    return None


def _selection(datasets: list[dict], entry: dict) -> tuple[list[dict], str, str]:
    dataset_index = int(entry["dataset"])
    row_index = int(entry["row"])
    variant = int(entry["variant"])
    source_dataset = datasets[dataset_index - 1]
    source_row = (source_dataset.get("rows") or [])[row_index - 1]
    row = deepcopy(source_row)
    selector = f"[{variant}]" if _iterator_count(source_row, source_dataset) > 1 else ""
    row["__dm_iter_select__"] = selector
    row["__dm_copies__"] = 1
    row["__dm_copies_explicit__"] = False
    row["__dm_holes__"] = []
    dataset = _dataset_with_rows(source_dataset, [row])
    if dataset_index > 1 and datasets:
        dataset["comments"] = list(datasets[0].get("comments") or []) + list(dataset.get("comments") or [])
    return [dataset], str(entry["label"]), selector


def _preview_items(svg_path: str) -> tuple[list[str], tuple[float, float, float, float]]:
    import inkex

    document = inkex.load_svg(Path(svg_path).read_bytes())
    root = document.getroot()
    try:
        dpi_scale = 1.0 / float(root.unittouu("1px"))
    except Exception:
        dpi_scale = 1.0
    boxes = []
    item_ids = []
    for node in root.xpath(".//*[@data-pnpink-face='front']"):
        node_id = str(node.get("id") or "").strip()
        if node_id:
            item_ids.append(node_id)
        values = str(node.get("data-pnpink-cut-bbox") or "").replace(",", " ").split()
        if len(values) != 4:
            continue
        x, y, width, height = (float(value) for value in values)
        if width > 0 and height > 0:
            boxes.append((x, y, width, height))
    if not boxes:
        raise ValueError("Preview render produced no visible items")
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return item_ids, (
        left * dpi_scale,
        top * dpi_scale,
        right * dpi_scale,
        bottom * dpi_scale,
    )


def prepare(
    effect,
    *,
    datasets: list[dict] | None = None,
    asset_key: str = "",
    base_signature: str = "",
) -> dict:
    datasets = datasets if datasets is not None else DATASET.load_datasets(effect, effect.document_path())
    annotate_spritesheet_iterators(effect, datasets)
    entries = catalog(datasets)
    if not entries:
        raise ValueError("The dataset contains no previewable assets")
    entry = next((item for item in entries if item["key"] == asset_key), entries[0])
    selected, label, selector = _selection(datasets, entry)
    signature_data = {
        "preview_cache": PREVIEW_CACHE_VERSION,
        "template": effect.document_path(),
        "document": _document_digest(effect),
        "base": str(base_signature or ""),
        "asset": entry["key"],
        "datasets": _render_signature_datasets(selected),
    }
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "datasets": selected,
        "source_datasets": datasets,
        "entries": entries,
        "entry": entry,
        "label": label,
        "selector": selector,
        "signature": signature,
    }


def symbol_base(effect, datasets: list[dict]) -> dict:
    selected = []
    symbol_count = 0
    for source in datasets:
        rows = [
            deepcopy(row)
            for row in source.get("rows") or []
            if str(row.get("__dm_symbol_id__") or "").strip()
        ]
        if not rows:
            continue
        dataset = _dataset_with_rows(source, rows)
        selected.append(dataset)
        symbol_count += len(rows)
    signature_data = {
        "symbol_cache": SYMBOL_CACHE_VERSION,
        "document": _document_digest(effect),
        "symbols": _render_signature_datasets(selected),
    }
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    target_dir = _persistent_cache_dir(effect)
    return {
        "datasets": selected,
        "count": symbol_count,
        "signature": signature,
        "path": os.path.join(target_dir, f"symbol-base-{signature[:20]}.svg"),
    }


def render_symbol_base(effect, version: str, prepared: dict, *, text_query_service=None) -> str:
    import engine as ENGINE
    import svg as SVG

    effect._dm_preloaded_datasets = prepared["datasets"]
    effect._dm_output_disabled = True
    effect._dm_preview_cache_only = True
    ENGINE.run(effect, version, text_query_service=text_query_service)
    # This document is an input for later preview renders, not a final preview.
    # Keep template/source layers renderable so referenced groups retain a real
    # bounding box (inline icons and Fit/Anchor may resolve them by id).  Linked
    # images must be absolute because the cache lives outside the project.
    SVG.absolutize_all_linked_images(effect.document, effect.document_path(), prefer="fileuri")
    ENGINE._write_svg_atomic(effect.document, prepared["path"])
    return prepared["path"]


def merge_symbol_base(effect, symbol_base_path: str) -> int:
    """Merge cached generated symbols into the current template document."""
    import inkex
    from lxml import etree

    import svg as SVG

    path = str(symbol_base_path or "").strip()
    if not path or not os.path.isfile(path):
        return 0

    with open(path, "rb") as source:
        cached_document = inkex.load_svg(source.read())
    cached_root = cached_document.getroot()
    current_root = effect.document.getroot()
    current_defs = SVG.ensure_defs(current_root)
    cached_defs = next(
        (
            child for child in list(cached_root)
            if etree.QName(child).localname == "defs"
        ),
        None,
    )
    if cached_defs is None:
        return 0

    current_by_id = {
        str(node.get("id")): node
        for node in current_root.iter()
        if str(node.get("id") or "").strip()
    }
    merged = 0
    for cached_node in list(cached_defs):
        node_id = str(cached_node.get("id") or "").strip()
        generated_symbol = cached_node.get("data-pnpink-symbol-row") == "1"
        if generated_symbol:
            existing = current_by_id.get(node_id)
            if existing is not None and existing.getparent() is not None:
                existing.getparent().remove(existing)
            copy = deepcopy(cached_node)
            current_defs.append(copy)
            if node_id:
                current_by_id[node_id] = copy
            merged += 1
        elif node_id and node_id not in current_by_id:
            copy = deepcopy(cached_node)
            current_defs.append(copy)
            current_by_id[node_id] = copy

    return merged


def cache_paths(effect, signature: str) -> dict:
    target_dir = _persistent_cache_dir(effect)
    stem = f"asset-{str(signature)[:20]}"
    return {
        "svg": os.path.join(target_dir, stem + ".svg"),
        "quick": os.path.join(target_dir, stem + "-96.png"),
        "high": os.path.join(target_dir, stem + "-600.png"),
    }


def render(
    effect,
    version: str,
    *,
    text_query_service=None,
    prepared: dict | None = None,
    force: bool = False,
) -> dict:
    import engine as ENGINE

    prepared = prepared or prepare(effect)
    paths = cache_paths(effect, prepared["signature"])
    target = paths["svg"]
    try:
        if force:
            raise FileNotFoundError(target)
        item_ids, fallback_area = _preview_items(target)
    except (FileNotFoundError, OSError, ValueError):
        effect._dm_preloaded_datasets = prepared["datasets"]
        effect._dm_output_disabled = True
        effect._dm_preview_cache_only = True
        effect._dm_preview_center_single = True
        effect._dm_preview_front_only = True
        ENGINE.run(effect, version, text_query_service=text_query_service)
        _isolate_generated_output(effect)
        ENGINE._write_svg_atomic(effect.document, target)
        item_ids, fallback_area = _preview_items(target)
    return {
        "path": target,
        "png_path": paths["high"],
        "quick_png_path": paths["quick"],
        "item_ids": item_ids,
        "fallback_area": fallback_area,
        "label": prepared["label"],
        "selector": prepared["selector"],
        "count": len(item_ids),
        "signature": prepared["signature"],
    }
