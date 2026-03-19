# 1. Introduction
## 1.1 What is PnPInk
PnPInk is an open-source extension suite for **Inkscape** that turns it into a practical environment for producing **print-and-play** components such as cards, tiles, counters, boards, and player aids.

PnPInk works entirely *inside* Inkscape. You design visually, using normal inkscape objects, and PnPInk takes care of replicating, filling, positioning, and arranging those designs automatically. The result remains fully editable vector artwork, and you can export it to any format supported by Inkscape: PDF, PNG, JPEG, ... or SVG, (which is actually the native vector format that Inkscape works with).

## 1.2 The core idea
PnPInk is built around a very simple idea:

**Draw one component → describe variations in a table → let PnPInk generate the rest**

You start with a single visual design (a card, a tile, a token face), connect it to a dataset, and PnPInk produces as many instances as you need, placing them on pages and preparing them for printing.

## 1.3 What you can do immediately
With no advanced syntax and no configuration beyond a dataset, you can already:

- duplicate a template many times,

- change texts and images per instance,

- automatically fill pages,

- and generate multi-page documents ready to export.

Everything beyond that is optional and incremental.

# 2. First Contact: Template, IDs, Dataset
## 2.1 What is a template in PnPInk
A **template** is a normal Inkscape drawing that represents *one unit* you want to replicate: one card, one tile, one board section.

It is composed of regular SVG elements (rectangles, images,\
text objects, polygons or paths, …) grouped as needed. Every element in SVG (en the groups) is identified by an unique ID and from now on we will refer to them as **objects.**

In PnPInk, a template is defined as:

- a group of objects that together form the component,

- identified by the ID of **one internal object** whose outline visually wraps the whole template.

This object is called the **template bounding box** (or bbox).

PnPInk uses that bounding box to understand:

- how big the template is,

- how to place it on the page,

- how to replicate it consistently.

The bounding-box object can be a rect or any simple shape, and it may be nested inside other groups. What matters is that its outline correctly represents the size of the component you want to replicate.

## 2.2 IDs: how data connects to graphics
In SVG, every object have an ID. In PnPInk, **IDs are how the dataset talks to the drawing**.

When an ID appears as a column in the dataset, that element can be modified for each generated instance.

Typical usage:

- text IDs → replaced with text from the dataset,

- rectangle IDs → used as anchors or placeholders for images or icons,

- group IDs → used to control visibility or variants (later).

# 3. A minimal working example
## 3.1 Template structure (conceptual)
In Inkscape, you might have something like this:

\(g\) card_template

├─ (rect) card_bbox ← template bounding box

├─ (text) title

├─ (text) cost

└─ (rect) art ← image anchor

- card_bbox visually wraps the entire card.

- title, cost, and art are elements you want to vary per card.

## 3.2 Dataset: the simplest possible form
A dataset can be as simple as this: a CSV file (comma separated) or Google Sheet:

| (first column) |                |      |                     |          |
|----------------|----------------|------|---------------------|----------|
| card_bbox      | title          | cost | art                 | (header) |
|                | Fireball       | 3    | images/fireball.png |          |
|                | Shield         | 2    | images/shield.png   |          |
|                | Healing Potion | 1    | images/potion.png   | (cells)  |

card_bbox,title,cost,art

,Fireball,3,images/fireball.png

,Shield,2,images/shield.png

,Healing Potion,1,images/potion.png

How this is interpreted:

- The **first column** identifies the template by its bounding-box ID (card_bbox).

- Each subsequent row generates **one card**.

- Text fields (title, cost) are replaced by the content of cell

- The art field provides the image to place into the art rectangle.

With no further configuration:

- PnPInk places cards sequentially on the page,

- fills as many as fit,

- and automatically creates new pages when needed.

At this point, you already have a usable workflow.

# 4. Hinting at layout possibilities (first taste of the DSL)
So far, everything worked with defaults. Now, with a **single short expression**, you can start controlling page size and layout.

Example:

{A4 b=-5}.L{ g=4x3 k=2 }

What this means, at a glance:

- {A4 b=-5}\
  Use an A4 page, with a 5 mm margin inside the page.

- .L{ g=4x3 k=2 }\
  Arrange the generated cards in a grid of 4 columns by 3 rows, with 2 mm spacing between them.

With this:

- cards are neatly arranged,

- spacing is consistent,

- pagination still happens automatically when more cards are needed.

This short notation is part of the **PnPInk DSL**, which allows you to control placement, scaling, rotations, grids, gaps and bleeds, cutting marks, and many other aspects.

PnPInk is designed to be **simple by default**, and **powerful when you need** it.

**What can you do with PnPInk**

PnPInk turns Inkscape into a production tool for print-and-play games.

You draw a component once (a card, a tile, a token), describe variations in a table, and PnPInk generates the rest — fully editable SVG, ready to print.

Below is a quick tour of what that means in practice. Even if you don't fully understand it, you can get an idea of ​​how pnpink works and its potential.

**From simple repetition…**

Build many components from a single design and a dataset.

{A4}.L{3x4}

A single card design is automatically placed 12 times on an A4 page.

One design, many instances.

…to data-driven variation

Each row in the dataset produces a different result. Csv example:

name, value

Sword, 3

Shield, 2

Potion, 1

Text, images, icons, and properties change per row. Design once, customize through data.

**Precise layout control**

Control spacing and size explicitly.

L{3x4 gaps=4 shape=poker}

Cards are placed using a poker-card size, with 4 mm between them.

**Fronts and backs, automatically.** PnPInk handles the boring geometry.

Generate perfectly aligned backs for duplex printing.

{card_back @back}

Front and back pages are mirrored and ordered correctly.

No manual alignment, no guessing.

**Page-level elements.** Place elements once per page instead of once per card.

{page_title @page}

Useful for page numbers, titles, backgrounds, or frames.

Not everything belongs to the grid.

Advanced positioning with anchors

**Place elements relative to an anchor rectangle.**

icon.F{i a=9}

The icon fits inside its rect, aligned top-right.

**Describe intent, not coordinates.** Adaptive layouts. Let PnPInk decide how many items fit.

L{1x gaps=}

Items are stacked vertically until they fit, with spacing computed automatically.

Layouts adapt to available space.

**Explicit ID arrays.** Apply layouts to specific SVG elements, not just datasets.

\[id1 id2 - id3\].L{1x}

Empty slots (-) reserve space without rendering anything.

Layout is not limited to rows and columns.

Slot-level alignment

Align items inside each layout cell.

L{1x a=6}

All elements are aligned to the right inside their slots.

Internal alignment, external freedom.

Professional production features

Generate cut marks aligned to real positions.

.M{len=\[3 2\] d=2}

Marks follow the actual layout, including shapes, bleeds, spacing, rotations, hexgrid…

Work with external sources (images, PDFs, spritesheets, icon libraries).

Reuse layouts as sources themselves.

Keep everything structured in layers, ready for printing or export.

PnPInk bridges design and production. Ready for real-world printing.

A progressive system. PnPInk is designed so that:

- Beginners can get results quickly with minimal syntax.

- Advanced users can combine features to solve complex layout problems.

The same concepts (anchors, borders, spacing) apply everywhere.

\[list of items\].L{1x gap= a=6}.F{i7}

Simple first. Powerful when you need it.
