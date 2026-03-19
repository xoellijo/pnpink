# Dataset Format and Sources
PnPInk reads tabular data from CSV files or Google Sheets and converts it into one or more dataset sections.

## Supported Inputs
- Local CSV file.
- Google Sheet (`sheet_id` in DeckMaker).

If `sheet_id` is empty, DeckMaker loads CSV from the same folder as the SVG:

```txt
<svg_name>.csv
```

## Google Sheets Setup
In `Extensions > PnPInk DEV > DeckMaker v0.24dev`, fill:

- `Google Sheet Id (optional)`
- `Range (A1, optional)`

How to get `sheet_id`:

- From a Google Sheets URL like:
  `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit#gid=0`
- Use the part between `/d/` and `/edit`.

## Sheet Selection Logic
When `sheet_id` is set:

1. If `Range` includes a sheet name (`Sheet2!A1:Z999`), that sheet is used.
2. Otherwise, PnPInk searches a sheet whose title matches the SVG filename (without extension), case-insensitive.
3. If no match is found, it uses the first sheet.

Default range is `A1:Z999` if empty.

This lets one Google Spreadsheet host multiple projects (one tab per SVG).

## Dataset Section Structure
Each dataset section has:

- column A: section marker and row-level control cell,
- columns B+: headers and data fields.

Two modes are supported:

- Marker mode: `{{...}}` in column A (recommended, supports multiple dataset sections).
- Shorthand mode: single dataset; first row uses A1 as main bbox id.

See full syntax in [Dataset Reference](dataset.md).

### Minimal Dataset Example
| Column A | title | cost | art |
|---|---|---|---|
| `card_bbox` |  |  |  |
|  | Fireball | 3 | images/fireball.png |
|  | Shield | 2 | images/shield.png |

```csv
card_bbox,title,cost,art
,Fireball,3,images/fireball.png
,Shield,2,images/shield.png
```

## Why Column A Is Critical
The first bbox id (`t=...` or equivalent) in column A defines the main template for that section.
It drives:

- layout slots and pagination,
- marks generation,
- front/back slot pairing,
- page-membership selectors for `@page`.

## Headers, Comments, and Directives
Headers and comments are part of the dataset grammar, not free text.
Before authoring complex sheets, review:

- [Dataset Reference](dataset.md) for exact rules,
- [Snippets](snippets.md) for `# :Name(...) = ...` directives,
- [DSL Nomenclature](dsl/nomenclature.md) for shared token rules.

## Multi-Dataset and Multi-Template
- Multiple datasets in one sheet are supported via repeated marker rows (`{{...}}`).
- Multiple template columns are supported via header declarations (`{template_id ...}`).
- Multiple main templates in one marker list are not supported; use one main template per dataset section.
