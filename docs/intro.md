# Introduction

This documentation is also available as a [single PDF guide](https://xoellijo.github.io/pnpink/pnpink.pdf).

## What is PnPInk?
PnPInk is an open-source extension suite for Inkscape that turns it into a practical production environment for print-and-play components: cards, tiles, counters, boards, and player aids.

If you are new to Inkscape: Inkscape is a free, open-source vector editor based on the SVG standard.
If you do not have it installed yet, you can download it for Windows, macOS, and Linux from the official website:
<https://inkscape.org>

PnPInk works inside Inkscape. You design with normal SVG objects, save that design as a template, and connect its object IDs to columns in a CSV or Google Sheet. PnPInk then handles replication, data filling, placement, and pagination automatically.

The generated document remains editable SVG. From there you can continue working in Inkscape or export PDF, PDF/X, PNG, JPEG, SVG, and other production formats supported by PnPInk's export pipeline.

## The core idea
PnPInk is built around a simple workflow:

1. Draw one component.
2. Describe variations in a dataset.
3. Let PnPInk generate the rest.

You start with a single visual design (a card, a tile, a token face), connect it to a dataset, and PnPInk produces as many instances as you need, placing them on pages and preparing them for printing.

You can begin with defaults. Advanced controls are optional and can be added gradually when you need more precision.

## What you can do immediately
Without advanced syntax, you can already:

- duplicate one template many times,
- change texts and images per instance,
- auto-fill pages,
- generate multi-page output ready to export.

Everything beyond that is incremental.

## First contact: template, IDs, dataset

### What is a template in PnPInk?
A template is a normal Inkscape drawing that represents one unit to replicate: one card, one tile, one board section, and so on.

In practice, it is a group of regular SVG objects (text, rects, paths, images, groups), identified by IDs.

PnPInk uses one internal object as template bounding box (`bbox`) to understand:

- template size,
- placement reference,
- replication behavior.

The `bbox` can be a rect or another simple shape. What matters is that its outline correctly wraps the component you want to replicate.

### How IDs connect data to graphics {#ids-workflow}
In PnPInk, IDs are the connection between dataset and drawing.

If a dataset column is named `title` and your SVG contains `id="title"`, that object can be updated for each generated row.

Typical usage:

- text IDs: dynamic text replacement,
- rect IDs: image/icon anchors,
- group IDs: visibility or variant control (advanced workflows).

!!! inkscape "Inkscape tip: inspect the template"
    The most useful panel for normal template work is `Object > Layers and Objects` (`Shift+Ctrl+L`), where you can inspect hierarchy, groups, layers, IDs, and Z-order. Open `Object > XML Editor` (`Shift+Ctrl+X`) only when you need direct access to SVG attributes that are not exposed by the normal object controls.

## Minimal working example

### Dataset notation used in this documentation
To avoid confusion, this guide uses a consistent visual notation:

- <span class="ds-header">header cells</span>: dataset column names (first row),
- <span class="ds-cell">regular cells</span>: normal data values,
- <span class="ds-col1">first-column cells</span>: cells in column 1 (template/page/layout control).

In dataset tables, headers and first-column cells are also color-highlighted.

### Conceptual template structure
Use a basic Inkscape file named `hello_world.svg`. Save it to disk before creating the local CSV so DeckMaker can resolve both files from the same project location.

In Inkscape, the structure can look like this:

```txt
(g) hello_word_template
 |- (rect) card_bbox        <- template bounding box
 |- (text) title
 |- (text) cost
 |- (rect) art              <- image/icon anchor
 `- (text) text
```

Key points:

- `card_bbox` visually wraps the full component.
- `title`, `cost`, `art`, and `text` are the objects you vary per row.

### Dataset example
Spreadsheet-like table view:

<div class="csv-dataset" markdown>

| card_bbox | title | cost | art | text |
| --- | --- | --- | --- | --- |
| {A4 b=[-5]}.L{p=4x3 g=2} | Tomatoes | 3 | tomato | You win 1 tomato |
|  | Mushrooms | 5 | brown-mushroom | You win 2 mushrooms |
|  | Lemons | 2 | lemon | Win 1 lemon for every tomato you own |

</div>

Interpretation:

- The <span class="ds-header">header</span> of the first column is <span class="ds-header">card_bbox</span>.
- The <span class="ds-col1">first-column cell</span> <span class="ds-col1">{A4 b=[-5]}.L{p=4x3 g=2}</span> sets page/layout context for this dataset block.
- Empty <span class="ds-col1">first-column cells</span> continue using the same template/page context.
- Each row generates one component instance.
- <span class="ds-header">title</span> and <span class="ds-header">cost</span> update text fields.
- <span class="ds-header">art</span> provides the image source for the `art` anchor.
- <span class="ds-header">text</span> updates the text object with `id="text"`.

With defaults only, PnPInk places instances sequentially, fills the page, and creates additional pages automatically when needed.

## A first taste of the DSL
Once basics work, you can start controlling page and layout with a short expression:
<div class="csv-dataset dataset-fragment dataset-body-only dataset-first-column" markdown>
|  |  |
| --- | --- |
| {A4 b=[-5]}.L{p=4x3 g=2} | ... |
</div>
In this expression, `{A4 b=[-5]}` selects an A4 page with a 5 mm inner margin on every side, and `.L{p=4x3 g=2}` arranges the cards in a 4-by-3 grid with 2 mm gaps.

This short notation is part of the PnPInk DSL (Domain Language). It lets you control placement, scaling, rotations, grids, gaps, bleeds, marks, and more.

PnPInk is designed to be simple by default, and powerful when you need it.

## What PnPInk can do (quick tour)
Even if you do not understand every syntax detail yet, this gives you a practical map of what is possible.

### From simple repetition...
Build many components from one design and one dataset:

<div class="csv-dataset dataset-fragment dataset-body-only dataset-first-column" markdown>

|  |  |
| --- | --- |
| {A4}.L{3x4} | ... |

</div>

One template can be placed 12 times on an A4 page.
See [Layout](dsl/layout.md) and [Page](dsl/page.md).

### ...to data-driven variation
Each dataset row can produce a different result.
Texts, images, icons, and properties can vary per instance.
See [Dataset Reference](dataset.md).

### Precise layout control
Control spacing and sizing explicitly:

<div class="csv-dataset dataset-fragment dataset-body-only dataset-first-column" markdown>

|  |  |
| --- | --- |
| L{3x4 gaps=4 shape=poker} | ... |

</div>

See [Layout](dsl/layout.md).

### Fronts and backs, automatically
Generate aligned duplex backs with `@back`:

<div class="csv-dataset dataset-fragment dataset-header-only" markdown>

| ... | {card_back @back} | ... |
| --- | --- | --- |

</div>

The `@back` control column starts the back-side fields, mirrors slots for duplex alignment, and can use iterators and marks.
See [@back -- Back-Side Templates](dataset.md#back-side-templates).

### Page-level elements
Place objects once per page, not once per card:

<div class="csv-dataset dataset-fragment dataset-header-only" markdown>

| ... | {page_title @page} | ... |
| --- | --- | --- |

</div>

Useful for titles, page numbers, frames, or static page backgrounds.
See [Page](dsl/page.md).

### Fit and Anchor (single concept)
Position objects relative to target rectangles without manual coordinates:

<div class="csv-dataset dataset-fragment dataset-body-only" markdown>

|  |  |  |
| --- | --- | --- |
| ... | icon.F{i a=9} | ... |

</div>

The element is fitted and anchored by intent, not by absolute measurements.
See [Fit and Anchor](dsl/fit-anchor.md).

### Adaptive layouts
Let layout adapt to available space:

<div class="csv-dataset dataset-fragment dataset-body-only dataset-first-column" markdown>

|  |  |
| --- | --- |
| L{1x? gaps=?} | ... |

</div>

Items stack and spacing is computed automatically.
See [Layout](dsl/layout.md).

### Explicit ID arrays and slots
Apply layout directly to selected IDs:

```txt
[id1 id2 - id3].L{1x?}
```

`-` reserves an empty slot without rendering.
See [Core Syntax](dsl/nomenclature.md).

### Slot-level alignment
Align content inside each layout slot:

```txt
L{1x? a=6}
```

See [Layout](dsl/layout.md) and [Fit and Anchor](dsl/fit-anchor.md).

### Production features
Generate cut marks aligned with final geometry:

```txt
.M{len=[3 2] d=2}
```

Marks follow real layout, spacing, bleeds, and rotations.
See [Marks](dsl/marks.md).

Use external sources (images, PDFs, spritesheets, icon libraries), and reuse generated assets in pipelines.
See [Source](dsl/source.md).

## A key practical advantage
With PnPInk you do not need to work with absolute coordinates for most production tasks.

You can express intent, for example: bring an image from a source, rotate it, fit it to a top-right anchor, scale it, and crop overflow.

If the source image changes, the same rule still works.
If you switch card size (for example from poker to tarot), layout and fitting scale with the template logic, without manually re-measuring everything.

This is one of the main differences between PnPInk and many manual or coordinate-heavy workflows.

## Recommended reading path
1. [Basic Workflow](quickstart.md)
2. [Dataset Format and Sources](dataset-overview.md)
3. [Dataset Reference](dataset.md)
4. [DSL Nomenclature](dsl/nomenclature.md)
5. [DSL Modules](dsl/nomenclature.md)
6. [Fit and Anchor](dsl/fit-anchor.md)
