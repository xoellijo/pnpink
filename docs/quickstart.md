# Basic Workflow
This chapter is a practical "first successful run" guide.
If you are starting from zero, complete it once end-to-end before reading full DSL reference pages.

## What you need
Before running DeckMaker, confirm these prerequisites:

- Inkscape installed.
- PnPInk extension installed and visible in the Inkscape menu.

## 1. Create a Template
This step defines the visual source that all generated instances will clone.

Draw one card (or tile) as a normal SVG group.

Recommended:

- Create a rect that defines the card size (this is the **template bbox**).
- Give IDs to all elements you want to drive from data.

Inkscape ID workflow:

- `Object > Objects...` (Shift+Ctrl+O): inspect hierarchy, groups, and Z-order.
- `Object > XML Editor` (Shift+Ctrl+X): edit SVG/XML attributes directly.

## 2. Prepare the Dataset
This step connects table columns to SVG IDs so each row can produce one variation.

Create a CSV or Google Sheet where:

- Column A contains the template bbox id.
- Column B+ contains IDs that match your SVG objects.

If using Google Sheets, set `Google Sheet Id` and optional `Range` in DeckMaker.
See [Dataset Format and Sources](dataset-overview.md).

Example:

```txt
card_bbox,title,cost,art
,Fireball,3,images/fireball.png
,Shield,2,images/shield.png
```

## 3. Define Page and Layout
This step controls where generated instances are placed and how many fit per page.

Put a page/layout preset in the first cell of column A:

```txt
{A4 b=[-5]} L{p=3x3 g=2}
```

This means:

- A4 page with a 5 mm inward margin.
- 3x3 grid with 2 mm gaps.

## 4. Run DeckMaker
This is the generation step: template + dataset + page/layout become output pages.

In Inkscape, run `Extensions > PnPInk DEV > DeckMaker` and select the dataset.

Result:

- PnPInk clones the template for each row.
- Applies Fit/Anchor and Sources.
- Creates pages and exports layouts ready for print.

## Useful Inkscape Panels for PnPInk
Inkscape provides right-side dock panels that are very useful when working with PnPInk.
The most relevant ones are:

- `Object > Layers and Objects...` (Shift+Ctrl+L): inspect object tree, layer/group hierarchy, and Z-order.
- `Object > Object Properties...` (Shift+Ctrl+O): edit object metadata and properties that affect matching and behavior.
- `Object > XML Editor` (Shift+Ctrl+X): inspect and edit low-level SVG/XML attributes exactly.
- `Object > Symbols` (Shift+Ctrl+Y): inspect symbol definitions used by Source and icon workflows.
- `Object > Fill and Stroke` (Shift+Ctrl+F): review visual style values that can influence readability and print output.
- `Object > Transform...` (Shift+Ctrl+M): apply deterministic transforms before generation if geometry is inconsistent.
- `Object > Align and Distribute` (Shift+Ctrl+A): enforce consistent alignment in templates before cloning.
