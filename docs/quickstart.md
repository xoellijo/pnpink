# Basic Workflow

This chapter walks through a complete first project: drawing a small card template, connecting it to a local CSV file, and generating an editable SVG with DeckMaker. Follow the example once from beginning to end before moving on to the reference chapters.

## Before You Start

DeckMaker runs inside Inkscape, so both Inkscape and PnPInk must already be installed. If they are not, follow the [installation instructions in the GitHub README](https://github.com/xoellijo/pnpink#install-pnpink), restart Inkscape, and confirm that `Extensions > PnPInk` is available.

## 1. Create a Project Folder

Start with an empty folder on a local disk, for example `my-first-deck`. Keeping the SVG template, CSV dataset, and project assets together makes relative paths predictable and allows DeckMaker to find the dataset automatically.

For this first project, the folder will eventually contain:

```txt
my-first-deck/
|-- cards.svg
|-- cards.csv
`-- images/
    `-- fireball.png
```

## 2. Draw the Template in Inkscape

Open a new Inkscape document and draw a rectangle with the final dimensions of one card. This rectangle defines the template bounding box: PnPInk uses it to determine what one record looks like, how large it is, and how it must be placed on the output pages. Open `Object > Object Properties`, change its ID to `card_bbox`, and keep that name unique within the document.

!!! inkscape "Inkscape tip: name and find objects"
    Open Object Properties with `Object > Object Properties` (`Shift+Ctrl+O`) to change the selected object's ID. Keep `Object > Layers and Objects` (`Shift+Ctrl+L`) open to see the complete object tree, IDs and stacking order while building the template.

Build a simple card around that rectangle. For example, add a text object for the title, another text object for the cost, and a rectangle that will receive the artwork. Give them the IDs `title`, `cost`, and `art`. These names are the connection between the drawing and the dataset: a CSV column named `title` changes the object whose SVG ID is also `title`.

Select the card elements and group them with `Ctrl+G`. Grouping is not a substitute for IDs, but it keeps the template together and makes its stacking order easier to understand. The Layers and Objects panel shows the group, the elements inside it, their order, and the IDs that PnPInk will use.

Your template can be visually simple. A first version only needs:

- one rectangle named `card_bbox` that encloses the complete card;
- editable objects such as `title`, `cost`, and `art` inside or alongside that rectangle;
- one group containing the complete template in the intended Z-order.

Save the document to the project folder with `File > Save As`. The file must have a real name and location before DeckMaker can resolve a local dataset; for this example, save it as `cards.svg`.

For a more detailed explanation of object names, see [How IDs connect data to graphics](intro.md#ids-workflow).

## 3. Create the CSV Dataset

A CSV is a plain-text file that describes a table. Commas separate its columns, the first row contains the headers, and each following row contains the values for one generated card. You can create it with a spreadsheet application and export it as CSV, or edit it directly with a text editor.

For automatic local discovery, place the CSV beside the SVG and give both files the same base name:

```txt
cards.svg
cards.csv
```

Create `cards.csv` with this content:

<div class="csv-dataset" markdown>

| card_bbox | title | cost | art |
| --- | --- | --- | --- |
|  | Fireball | 3 | images/fireball.png |
|  | Shield | 2 | images/shield.png |

</div>

The first cell of the header row, `card_bbox`, tells PnPInk which rectangle defines the main template. The remaining headers match the SVG IDs created in Inkscape. Each data row generates one card: `Fireball` is written into the `title` text, `3` into `cost`, and the image is fitted into `art`. Column A is empty in the data rows because its main purpose in this simple format is to identify the template and carry optional row-level controls.

CSV values containing commas must be quoted according to normal CSV rules, for example `"Draw two cards, then discard one"`. Relative asset paths such as `images/fireball.png` are resolved from the project location, so keeping assets below the project folder makes the project easier to move or share.

This example uses the compact single-dataset format. PnPInk also supports several dataset sections, template declarations, comments, snippets, Google Sheets, and control rows; those are introduced in [Dataset Format and Sources](dataset-overview.md) and specified in [Dataset Reference](dataset.md).

## 4. Add a Page Layout

Without an explicit layout, DeckMaker can use its configured defaults. To make the first output predictable, add a control-only row immediately after the headers:

<div class="csv-dataset" markdown>

| card_bbox | title | cost | art |
| --- | --- | --- | --- |
| {A4 b=[-5]} L{p=3x3 g=2} |  |  |  |
|  | Fireball | 3 | images/fireball.png |
|  | Shield | 2 | images/shield.png |

</div>

`{A4 b=[-5]}` selects an A4 page with a 5 mm internal border, while `L{p=3x3 g=2}` creates a 3-by-3 grid with 2 mm gaps. Because the other cells in that control row are empty, the row changes the page and layout but does not generate a card.

Layout notation becomes useful when projects need different paper sizes, margins, grids, duplex backs, or cutting marks. For now, it is enough to understand that the control row describes the output sheet, while the following rows provide the card data.

## 5. Generate the Output

Keep `cards.svg` open in Inkscape and run `Extensions > PnPInk > DeckMaker`. For this local example, leave the Google Sheets fields empty and use the local/default source. DeckMaker resolves `cards.csv` from the location and name of the active SVG.

Click `Generate`. DeckMaker reads the rows, clones the template, replaces the matching fields, fits the artwork into its placeholder, and arranges the cards on one or more pages. Enable the option beside `Open SVG`, or click `Open SVG` after generation, to inspect the result immediately.

The generated file is normally written beside the project as `cards_output.svg`. It remains an editable SVG, but it is an output artifact rather than the master template. Make design changes in `cards.svg`, update data in `cards.csv`, and generate again instead of maintaining separate manual edits in the output.

## 6. Check the Result and Iterate

Verify that the titles and costs differ between the two cards, that the artwork appears inside `art`, and that the cards fit the requested page grid. If a value does not appear, compare the CSV header with the SVG ID character by character. If an object is behind another one, inspect its order in `Object > Layers and Objects`; PnPInk preserves the template's visual hierarchy when it creates each instance.

Once this simple cycle works, extend it gradually. Add another text column, replace a color through an `id[property]` header, try an inline icon, or move the dataset to Google Sheets. Small changes make it easier to understand which part belongs to Inkscape, which part belongs to the dataset, and which part is controlled by PnPInk syntax.

## Where to Go Next

Continue with [Dataset Format and Sources](dataset-overview.md) to learn how local CSV and Google Sheets projects are selected. Use [DeckMaker GUI](deckmaker-gui.md) when you need an explanation of generation and export controls, and open [Fit and Anchor](dsl/fit-anchor.md) when artwork or icons must be positioned relative to placeholders.
