# Introduction
PnPInk is an Inkscape extension suite for data-driven print-and-play production.
You design visually in SVG, then generate many card/tile/board instances from a table.

## What PnPInk is for
PnPInk helps you produce:

- cards and decks,
- tiles and tokens,
- board sections and player aids.

It stays inside Inkscape, so the output remains editable SVG and can be exported to PDF/PNG/JPG/SVG.

## Core idea
PnPInk follows one simple pipeline:

1. Draw one template component.
2. Describe variations in a dataset.
3. Let PnPInk generate and place all instances.

You can start with defaults and add DSL controls only when needed.

## First contact: template, IDs, dataset
### What is a template
A template is a normal Inkscape drawing for one unit (one card, one tile, one board piece).
PnPInk identifies that template through a bounding object ID, usually called the template bbox.

That bbox defines:

- component size,
- slot placement reference,
- replication behavior.

### How IDs connect data to graphics
Dataset headers are matched to SVG IDs.
If a column is `title` and your template has `id="title"`, that element is updated per row.

In Inkscape:

- `Object > Objects...` (Shift+Ctrl+O): hierarchy, layers, groups, Z-order, IDs.
- `Object > XML Editor` (Shift+Ctrl+X): low-level SVG/XML attributes.

## Minimal working example
Template IDs:

- `card_bbox` (main bbox),
- `title`,
- `cost`,
- `art`.

Dataset example (same content as table and CSV):

| Column A | title | cost | art |
|---|---|---|---|
| `card_bbox` |  |  |  |
|  | Fireball | 3 | images/fireball.png |
|  | Shield | 2 | images/shield.png |
|  | Healing Potion | 1 | images/potion.png |

```csv
card_bbox,title,cost,art
,Fireball,3,images/fireball.png
,Shield,2,images/shield.png
,Healing Potion,1,images/potion.png
```

Interpretation:

- Column A binds rows to the main bbox/template.
- Each row produces one instance.
- `title` and `cost` update text.
- `art` can load a source into the `art` anchor.

## First DSL step (optional)
You can add page and layout in column A:

```txt
{A4 b=[-5]} .L{p=4x3 g=2}
```

Meaning:

- A4 page with 5 mm inward margin.
- 4x3 grid with 2 mm gap.

## What you can do next
Once the basics work, you can add:

- `@back` for duplex back sides,
- `@page` for page-level elements (titles/backgrounds),
- `Fit`/`Anchor` for precise placement,
- `Marks` for cutting marks,
- `Source` and spritesheet aliases for asset pipelines,
- snippets for reusable text macros.

## Recommended reading path
1. [Basic Workflow](quickstart.md)
2. [Dataset Format and Sources](dataset-overview.md)
3. [Dataset Reference](dataset.md)
4. [DSL Nomenclature](dsl/nomenclature.md)
5. [DSL Modules](dsl/index.md)
