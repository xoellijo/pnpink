# Dataset Reference
This chapter is the exact behavioral reference for dataset parsing and execution.
Use [Dataset Format and Sources](dataset-overview.md) first if you are new to the model.

## IDs and Naming
This section explains how table headers bind to SVG content.

Dataset headers are matched to SVG IDs.

For the Inkscape ID workflow (where to inspect and edit IDs), see
[Introduction -> How IDs connect data to graphics](intro.md#ids-workflow).

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
Split-board datasets may omit headers when they only need the template plus a leading-cell Page/Layout/Marks row.

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
card_bg[fill]
line[stroke]
bg=default_bg
art=art_placeholder~i5
```

Rules:

- `id+` keeps the original anchor rect visible (otherwise anchors are hidden).
- `id[prop]` sets a property. Default is `text`.
- `id=...` declares a **default value** or default Fit ops for that column.
- `id-*` in headers expands to all matching IDs by prefix.
- `id1 id2 id3` in a single header applies the same cell value to all listed IDs.

Examples:

```txt
id=default_id
id=~i5
id=default_id~i5
```

### Style Property Columns
Use `id[property]` to change SVG style properties from the dataset.

```txt
card_bg[fill]
card_bg[stroke]
card_bg[stroke-width]
title[fill]
title[font-size]
line[stroke]
```

Examples:

```csv
card_bg[fill],title[fill],line[stroke],line[stroke-width]
#f4ead2,#12110f,b8a300ff,2
```

Style columns update the target element's `style` attribute.
When a style property is changed, PnPInk keeps that element visible automatically; you do not need to add `+`.

Use `fill` for closed shapes and text color.
Use `stroke` for lines, open paths, outlines, and most visible path strokes.

Color values can be written as normal SVG values or compact hex:

```txt
#ff0000
ff0000
#ff000080
ff000080
red
url(#myGradient)
```

For `fill` and `stroke`, 8-digit hex colors are split into color plus opacity for SVG compatibility.
For example, `ff000080` becomes red with partial `fill-opacity` or `stroke-opacity`.

Property-only shorthand inherits the previous target id:

```csv
card_bg[fill],[stroke],[stroke-width]
#f4ead2,#222222,0.4
```

This is equivalent to:

```csv
card_bg[fill],card_bg[stroke],card_bg[stroke-width]
```

Special properties:

- `id[text]` or just `id` replaces text.
- `id[xml]` replaces rich XML content inside text-like objects.

### Header Fan-Out and Wildcards
Use this when one dataset column must feed several placeholders.

Examples:

```txt
ph-1 ph-2 ph-3
id1
```

`id1` is applied to all three placeholders.

Wildcard headers are also supported:

```txt
main_icon-*
Ic(heart-suit)
```

This applies the value to every placeholder whose ID starts with `main_icon-`.
All normal header modifiers (`+`, `[xml]`, `=...`) remain valid.

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
For duplex backs, see [@back -- Back-Side Templates](#back-side-templates).

## Comments and Directives (#)
Comments are processed **before any other operation**.
Rules are global: same behavior everywhere, with no inside/outside dataset distinction.

- `#` at line start (first non-space char in column A): comment/directive row.
- `##` at line start: comments out the entire row, for example to disable a directive or dataset row.
- `##` inside a cell: comments out the rest of that cell.
- `###` inside a row (not line start): comments out the rest of that cell and all cells to the right (rest of line).
- `####` at line start: starts/ends a disabled block.
- Single `#` inside a cell is normal text, for example color values like `#ffccbb`.

Disabled block:

```csv
####
disabled rows here
####
```

If the block is not closed, it disables everything until the end of the file/sheet (EOF):

```csv
####
disabled rows until the end of the file/sheet
```

Comments inside the dataset header row work slightly differently:

- `##header` disables all that column.
- `###header` disables that column and all columns to the right.
- `###first_header` at the beginning of the header row disables the entire dataset.

## Leading Cell (column A in data rows)
Leading-cell directives are row-level controls, not regular data fields.

Column A in data rows can carry row-level DSL:

- `{A4 ...}` page block
- `L{...}` layout tail
- `M{...}` marks tail
- trailing copies number, or `?` for automatic copies (current layout capacity)
- optional hole patterns in `[...]`
- optional iterator selection in `[...]` when using numeric ranges like `1..5 7..100`

Examples:

```txt
{A4 b=[-5]} L{p=3x3 g=2} M{mk_cut} 2
{} ?                  -> fill one page with the current row (e.g. backs)
[3 - 2-]            -> 3 copies, then 1 hole, then 2 holes
[1..5 7..100]       -> keep iterator items 1..5 and 7..100 (skip 6)
[1..4 3- 7..9]      -> keep 1..4, then 3 holes, then keep 7..9
```

Hole syntax in the final `[...]`:

- `-` = 1 hole after the current copy count
- `N-` = `N` holes after the current copy count
- plain numbers add copies before later holes are placed

Examples:

```txt
[3 - 2-]    -> 3 copies, then 1 hole, then 2 more holes
[2 3- 5]    -> 2 copies, then 3 holes, then 5 more copies
```

When the final `[...]` contains numeric ranges (`..`), it filters row iterators (`*[...]`).
Hole markers (`-`, `N-`) can still be mixed in the same block and are applied after the accumulated selected run.

This cell is **not** a normal dataset field; it controls row-level layout/flow.
Its directives apply before regular field replacements in that row.

For the full sequencing rules of column A with iterators, copies, holes, reordering and `?`,
see [Iterators](dsl/iterators.md), section **Row Sequencing from Column A**.

If a row uses only column-A controls and all payload cells (columns B+) are empty,
PnPInk applies the controls but does **not** generate a card/instance for that row.

```txt
card_bbox,title,cost
{A4},,
,Fireball,3
```

<a id="back-side-templates"></a>

## @back -- Back-Side Templates (Back Pass)
Use `@back` for duplex backs: card backs, tile backs, alternate reverse sides, and any back-side artwork that must align with the front layout.

An `@back` header is a **control column** and a **section boundary**:

```csv
rect1,front_art,{rect1 @back},rect1=~[1]a
{A4}.L{s=miniEuro g=2}.M{},*@{front_*.png},.M{},back.png
```

In this example:

- `rect1` in the first header cell is the main/front template bbox.
- `{rect1 @back}` does not place `back.png` by itself.
- `{rect1 @back}` starts the back-side column section.
- `rect1=~[1]a` after `{rect1 @back}` is a normal back-side field, with the same Fit/Anchor power as a front field.
- `.M{}` in the `{rect1 @back}` cell controls marks for the back side.

### Back Template and Layout
The bbox inside `{... @back}` selects the template used for backs.
It can be the same bbox as the front (`{rect1 @back}`) or a different back template (`{bbox_back @back}`).

Back pages are not driven by their own Page/Layout settings.
They are derived from the corresponding front pages:

- page size, margins, layout shape and gaps come from the front slot being backed,
- back pages are inserted after their matching front pages,
- each back slot is horizontally mirrored inside the page for duplex alignment,
- top/bottom position is preserved,
- card artwork is not mirrored; only the slot position is mirrored.

This means front and back use the same card-generation behavior, except for the mirrored layout placement.

### Back Column Section
Columns after `{bbox @back}` belong to that back template until another template-control header starts.

```csv
front_title,front_art,{bbox_back @back},back_bg=~a,back_icon~i5,back_text
```

Back-side columns support the same features as front columns:

- text replacement,
- source loading,
- Fit/Anchor defaults such as `rect1=~[1]a`,
- style property columns such as `id[fill]`,
- snippets,
- paths,
- transforms,
- iterated values.

For Fit/Anchor behavior, see [Fit and Anchor](dsl/fit-anchor.md).
For iterators, see [Iterators and Copies](dsl/iterators.md).

### Back Control Cell
The cell under `{bbox @back}` controls whether and how that back instance is generated.
It is not the content cell.

Accepted control values:

- empty: generate the back normally, with no extra back marks,
- `0` or `-`: skip that back instance,
- `.M{...}`: generate marks for the back side,
- `.M{...} [selector]`: generate marks and remap which iterator/card data is used for the back fields,
- `[selector]`: remap back data without marks.

Examples:

```csv
{rect1 @back},rect1=~[1]a
.M{},back.png
```

Same back image for every generated front, with back marks.

```csv
{rect1 @back},rect1=~[1]a
.M{} [2..? 1],*@{back_*.png}
```

Use the next iterator item as the back for each card, wrapping the last one to the first item in the same dataset row expansion.

The selector in the back control cell uses the same 1-based list/range grammar as column-A iterator selectors:

```txt
[1..5 5..1 8..3]
[2..? 1]
[5..1]
```

It is evaluated against the generated instances of the same dataset row expansion, not against unrelated rows.
For the selector grammar, see [Iterators and Copies -> Selector syntax in the final `[...]`](dsl/iterators.md#iterator-selector-syntax).

### Iterators and Specific Backs
Back sections work with iterators.
If the front row expands through `*@{...}`, the back section can also use iterator values.

```csv
rect1,{rect1 @back},rect1=~[1]a
{A4}.L{s=square g=2}.M{},.M{} [2..? 1],*@{embedded_*.png}
```

Here each front card uses its own front data from the row expansion.
The back field uses the control selector `[2..? 1]`, so backs are shifted by one position inside that same expanded row.

### Marks on Backs
Back marks are declared in the `{bbox @back}` control cell:

```csv
{rect1 @back},rect1=~[1]a
.M{ len=[3 0] d=2 },back.png
```

Back marks follow the mirrored back layout, so exterior side marks remain exterior after duplex mirroring.
For mark parameters, see [Marks](dsl/marks.md).

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
This mode is **not automatic**.

Enable it explicitly in the dataset marker:

```txt
{{t=board_bbox @split}}
```

Alias (short form):

```txt
{{t=board_bbox!}}
```

Then define page/layout/marks in the first cell as usual, for example:

```txt
{letter}.L{0x0 b=-10}.M{}
```

When split mode is enabled and the template is larger than the target page, the engine:

- the template is cut into tiles,
- each tile is placed on a page,
- layout and marks are applied to the tile bounds.
