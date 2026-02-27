# Dataset Reference
This chapter is the exact behavioral reference for dataset parsing and execution.
Use [Dataset Format and Sources](dataset-overview.md) first if you are new to the model.

## IDs and Naming
This section explains how table headers bind to SVG content.

Dataset headers are matched to SVG IDs.

To inspect or edit IDs in Inkscape:

- `Object > XML Editor` (Shift+Ctrl+X)
- `Object > Objects...` (Shift+Ctrl+O)

IDs must be unique and follow XML rules (letters first, no spaces).

## Dataset Structure
Dataset structure defines where parsing starts and how sections are separated.

PnPInk supports two forms:

- **Marker mode** (recommended, supports multiple datasets).
- **Shorthand mode** (single dataset only).

For source selection (CSV vs Google Sheets and tab lookup), see
[Dataset Format and Sources](dataset-overview.md).

### Marker Mode (column A)
Marker mode is the robust format for production datasets and multi-section files.

A dataset marker exists **only in column A** and uses `{{...}}`.
The marker row is also the header row. Headers start in column B.

Examples (equivalent):

```txt
{{t=card_bbox}}
{{template_bbox=card_bbox}}
{{card_bbox}}
```

Notes:

- Only one main template bbox is supported per dataset marker.
- Extra DSL tails are allowed after the marker (see Leading Cell below).

### Main Template BBox and Z-Order
This is a core concept: one main bbox drives slot logic for the section.

The marker `t=...` defines the **main template bbox** for that dataset section.
This main template controls:

- slot planning (`Layout{}` / `Page{}`),
- per-slot marks (`Marks{}`),
- front/back slot pairing (`@back`),
- page membership for page-anchored templates (`@page` selectors).

Placement order (Z-order) follows dataset row order. Later rows are rendered above earlier rows.

### Shorthand Mode (single dataset)
Shorthand mode is a convenience form for small, single-section sheets.

If there is no marker row, the first non-empty, non-comment row is treated as the header row.

In shorthand mode, column A contains the template bbox id:

```txt
card_bbox, title, cost, art
```

This is equivalent to:

```txt
{{t=card_bbox}}, title, cost, art
```

## Header Types
Header type determines whether a column edits fields or instantiates templates.

Headers in column B+ can be:

1. **Normal data fields**: match SVG IDs and replace text or sources.
2. **Template columns**: declare extra templates with `{...}`.

### Normal Data Field Syntax
Use this syntax for the most common per-row updates (text, source, and defaults).

Headers can include modifiers:

```txt
title
art+
price[xml]
bg=default_bg
art=art_placeholder~i5
```

Rules:

- `id+` keeps the original anchor rect visible (otherwise anchors are hidden).
- `id[prop]` sets the property (`text` or `xml`). Default is `text`.
- `id=...` declares a **default value** or default Fit ops for that column.

Examples:

```txt
id=default_id
id=~i5
id=default_id~i5
```

### Template Column Syntax
Use template columns when you need extra template instances, back passes, or page-level elements.

Template columns declare template bbox IDs and modifiers:

```txt
{card_back @back}
{page_title @page}
{back_bg @page @back}
```

Supported modifiers:

- `@page` page-anchored templates
- `@back` back-pass templates

Template columns are rendered as additional instances; they do not replace the main template column logic.

## Comments and Directives (#)
Comment behavior is context-dependent (outside dataset vs inside dataset).
This section prevents accidental data loss from misinterpreted `#`.

Comments are processed **before any other operation**.

### Outside the dataset
- `#` at line start -> comment line (kept for directives like snippets).
- `##` at line start -> hard comment (ignored).
- `##` after a directive/comment -> everything after `##` is ignored.

### Inside the dataset
- `##` at the start of column A -> the entire row is ignored.
- `#` in a cell comments out the rest of that cell **only for non-text fields**.
- `#col` in a header disables that column.
- `##col` in a header disables that column and all columns to the right.

Non-text fields are detected by header conventions:

- internal keys (`__dm_...`)
- headers starting with `.`

## Leading Cell (column A in data rows)
Leading-cell directives are row-level controls, not regular data fields.

Column A in data rows can carry row-level DSL:

- `{A4 ...}` page block
- `L{...}` layout tail
- `M{...}` marks tail
- trailing copies number
- optional hole patterns in `[...]`

Examples:

```txt
{A4 b=[-5]} L{p=3x3 g=2} M{mk_cut} 2
[3 - 2-]            -> 3 copies, then 1 hole, then 2 holes
```

This cell is **not** a normal dataset field; it controls row-level layout/flow.
Its directives apply before regular field replacements in that row.

## @back -- Back-Side Templates (Back Pass)
Use `@back` for duplex output where back faces must align with front slot geometry.

A template column marked with `@back` is rendered **only on back pages**.

```txt
{card_back @back}
```

Back templates:

- reuse the same slot sequence from the front layout,
- mirror slot placement within the page for duplex alignment,
- preserve row order as Z-order.
- back pages are inserted right after each front page (interleaved).

If a dataset cell for an `@back` column is empty or contains `-` or `0`, that back instance is skipped.

## @page -- Page-Anchored Templates (One Per Page)
Use `@page` for elements that belong to the page frame, not to per-card slots.

A template column marked with `@page` is anchored to the **page frame**, not to the slot grid.

```txt
{page_title @page}
```

Page-anchored templates:

- are positioned relative to the page frame after `Page{}` margins,
- are rendered once per page,
- use Fit/Anchor for placement.

Each dataset row provides a **slot selector** in the cell value (e.g. `~8[-5]`).
The slot determines which page the template belongs to; the rest is Fit/Anchor ops.

Selectors can be a single slot index, range, or list:

```txt
~1
~[1 3 5]
~[2..4]
```

If multiple rows target the same page, only the first is rendered (a warning is logged).

## Combining @page and @back
Combining both modifiers is common for back-page backgrounds and page-level back labels.

```txt
{back_bg @page @back}
```

This places a page-anchored element on back pages, aligned to the front page sequence.

## Advanced: Split Boards
This mode is activated automatically when template dimensions exceed target page inner size.

If the template is larger than the target page, the engine switches to split-board mode:

- the template is cut into tiles,
- each tile is placed on a page,
- layout and marks are applied to the tile bounds.
