# Dataset Format and Sources

PnPInk separates visual design from variable data. The SVG contains the objects, styles, and geometry of the template; the dataset says which values must be placed into those objects for every generated instance. Most projects use either a local CSV file or a Google Sheet, but both sources produce the same table before rendering begins.

If this is your first project, complete [Basic Workflow](quickstart.md) before using this page. The workflow shows how to create the SVG objects and IDs referred to below.

## Local CSV Projects

A CSV is a plain-text representation of a table. Commas separate columns, the first relevant row defines the column headers, and subsequent rows contain the values used to generate cards, labels, tiles, or other records. CSV is convenient for self-contained projects because it works without an internet connection and can be edited by spreadsheet applications as well as text editors.

When no Google Sheet ID is configured, DeckMaker looks beside the active SVG for a CSV with the same base name. An SVG saved as `cards.svg` therefore uses `cards.csv`:

```txt
project/
|-- cards.svg
|-- cards.csv
`-- images/
    |-- fireball.png
    `-- shield.png
```

The SVG must be saved before DeckMaker runs. An unnamed document has no folder or base name, so PnPInk cannot infer where `cards.csv` or relative assets should be found.

A minimal dataset looks like this:

```csv
card_bbox,title,cost,art
,Fireball,3,images/fireball.png
,Shield,2,images/shield.png
```

`card_bbox` identifies the rectangle that encloses the main template. The headers `title`, `cost`, and `art` match object IDs in the SVG. Every following row creates one instance and supplies the values for those objects. A text value replaces text, while a file or source expression placed under a rectangular ID is fitted into that rectangle.

Normal CSV quoting rules apply. A value containing a comma must be enclosed in quotes, and a literal quote inside that value must be doubled:

```csv
,"Draw two cards, then discard one",2,images/example.png
,"The card says ""Ready""",1,images/other.png
```

Relative paths are resolved from the project location. Keeping images, fonts, and supporting files below the project folder makes the project easier to copy, archive, or share.

## Google Sheets Projects

Google Sheets is useful when several people edit the data, when a project changes frequently, or when the dataset must be maintained without opening local files. In `Extensions > PnPInk > DeckMaker`, enter the spreadsheet identifier in `GSheet ID`. It is the part of the Google Sheets URL between `/d/` and `/edit`:

```txt
https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit#gid=0
```

The optional `Sheet!range/gid` field selects a particular tab or range. Public access normally uses the numeric `gid` shown in the URL; an empty selector means `gid=0`. Authenticated OAuth access can use a sheet name or an explicit expression such as `Cards!A1:Z99`. If an authenticated selector is empty, PnPInk first looks for a tab whose name matches the SVG filename and then falls back to the first sheet.

Choose the source mode in DeckMaker according to the spreadsheet's access. Public mode is simple for deliberately public sheets, while OAuth keeps private project data behind the authenticated Google account. Once loaded, both modes follow the same dataset grammar as a CSV.

## How the Table Connects to SVG Objects

PnPInk matches dataset headers to SVG IDs. If the SVG contains a text object whose ID is `title`, a dataset column headed `title` supplies its content. A rectangle named `art` can receive an image or another SVG object, and a header such as `card_bg[fill]` changes a property instead of replacing content.

The first column has a special structural role. In the compact single-dataset form, its header contains the main template bbox ID. In larger projects it can contain dataset markers, page and layout instructions, copy counts, comments, and other row-level controls. The remaining columns normally address template objects or declare additional templates.

| Column position | Typical purpose |
| --- | --- |
| Column A | Main template declaration and row-level controls |
| Columns B onward | SVG IDs, property headers, template declarations, and their values |

## Compact and Marker Formats

The compact format is ideal for a first project and supports one dataset section. Its first header cell is the main template bbox:

```csv
card_bbox,title,cost
,Fireball,3
,Shield,2
```

Marker format is more explicit and supports several independent sections in one CSV or sheet. A marker appears only in column A and uses `{{...}}`; the same row contains the headers for that section:

```csv
{{t=card_bbox}},title,cost
,Fireball,3
,Shield,2
```

The declarations `{{t=card_bbox}}`, `{{template_bbox=card_bbox}}`, and `{{card_bbox}}` identify the same main bbox. Marker format becomes preferable when a project contains fronts and backs, page-level artwork, several template families, or unrelated datasets in one workbook.

## Page and Layout Control Rows

Column A can also change pagination without generating an instance. In this example, the first data row selects an A4 page and a 3-by-3 layout; because the remaining cells are empty, it acts only as a control row:

```csv
card_bbox,title,cost
{A4 b=[-5]} L{p=3x3 g=2},,
,Fireball,3
,Shield,2
```

The main template bbox is important beyond measuring the card. It drives slot planning, pagination, cutting marks, duplex front/back pairing, and membership of page-level templates. For that reason, each dataset section supports one main bbox; projects that need another main template should begin another marker section.

## Headers Are Instructions, Not Labels

Dataset headers are parsed as PnPInk expressions. A plain header such as `title` replaces content in the SVG object with that ID. Modifiers can keep placeholders visible, set style properties, establish defaults, address several IDs, or declare extra templates:

```txt
title
art+
card_bg[fill]
line[stroke-width]
bg=default_background
```

This power also means that spelling matters. If a value does not reach the expected object, compare the header with the SVG ID exactly and check for accidental spaces. Use `Object > Layers and Objects` in Inkscape to inspect the actual IDs instead of relying on visible labels.

## Continue with the Reference

This page explains the data model and source selection. [Dataset Reference](dataset.md) documents the complete grammar for markers, headers, properties, iterators, copies, fronts and backs, page templates, and comments. [Snippets](snippets.md) explains reusable `# :Name(...) = ...` declarations, while [DSL Nomenclature](dsl/nomenclature.md) defines token and suffix rules shared across the language.
