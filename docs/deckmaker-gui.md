# DeckMaker GUI

DeckMaker is the user-facing PnPInk window used to generate decks from an SVG template and a dataset.

The window title shows the running DeckMaker version and the current template file name. The full template path is not shown in the form because the extension is launched from the active Inkscape document.

## Deck tab

The Deck tab contains the data source controls, the main actions, and the live activity log.

### Data source

| Field | Purpose |
| --- | --- |
| `GSheet ID` | Google Spreadsheet id. This is only needed for Google Sheet sources. |
| `Range / gid` | Optional sheet range or grid id. Leave empty when the default source selection is enough. |
| `Source` | Selects how the dataset is loaded. Supported values are empty/default, local CSV, Google Sheet OAuth, and Google Sheet public. |

For local CSV workflows, the spreadsheet fields can be left empty when the source is resolved from the template or from the selected file.

### Actions

| Control | Purpose |
| --- | --- |
| Auto checkbox before `Generate` | Starts generation automatically when the GUI opens. |
| `Generate` | Builds the output SVG from the current template and dataset. |
| Auto checkbox before `Open SVG` | Opens the generated SVG automatically after generation. |
| `Open SVG` | Opens the generated SVG through the same Inkscape launch path used by the extension. It does not use the operating system default application. |
| Auto checkbox before `Export` | Runs export automatically after generation. |
| `Export` | Runs the export pipeline configured in the Export tab. |

### Progress and log

The log area shows the current generation stage and relevant export stages. During generation, the progress text uses `Generating records` and shows the achieved speed in `records/min` when enough timing data is available.

For debugging, the full log is written to `src/pnpink.log` next to the extension sources.

## Export tab

The Export tab configures PDF, PDF/X, and additional file exports. See [Export](export.md) for the detailed export pipeline.

The most important controls are:

| Control | Purpose |
| --- | --- |
| `PDF export` | Enables standard PDF export. |
| `PDF output profiles` | Selects one or more PDF profiles: `default`, `screen`, `ebook`, `printer`, `prepress`. |
| `PDF/X export (CMYK)` | Enables CMYK PDF/X export through Ghostscript. |
| `Raster filters` | Chooses how filtered SVG content is handled before PDF export. |
| `Other formats` | Exports page images or alternate formats such as PNG, JPEG, TIFF, WebP, PS, EPS, EMF, or WMF. |
| `Cutting-plotter template` | Exports cut-only templates as `svg (vector, cricut)`, `dxf (vector, cameo)`, or `png (raster, all)`. For DXF/Cameo in Silhouette Studio, set DXF open mode to `"Center"` once, then select all, apply `"Simplify"`, and group each imported template. |
| `DPI` | Output resolution used by Inkscape exports. |
| `JPEG quality` | JPEG quality for JPEG-based outputs. |

## Preferences tab

The Preferences tab currently exposes SVG output splitting:

| Control | Purpose |
| --- | --- |
| `Split SVG` | Writes the generated output as multiple SVG parts. |
| `Part target MB` | Target size for each generated SVG part. |

Splitting is useful for very large decks because Inkscape export can be faster and more stable with smaller SVG files. Very small part sizes create many chunks and can increase overhead.

## Generated files

Generation writes an editable SVG output derived from the template name, usually using the `_output.svg` suffix.

When SVG splitting is enabled, the parts are written to a sibling chunks directory. Export reuses those existing chunks when available.

## Advanced preferences

Most preferences are stored in `src/preferences.ini`. The GUI writes explicit changes immediately, but it should not rewrite all preferences just because the window is closed.

Useful advanced keys:

| Key | Values | Purpose |
| --- | --- | --- |
| `template_engine` | `legacy`, `composed`, `composed-instance` | Template instantiation engine. `legacy` is the normal engine. `composed` is experimental and intentionally fails on unsupported templates. `composed-instance` keeps composed clones anchored to the first generated instance instead of `<defs>`. |
| `inline_icons_bbox_backend` | `query_all`, `shell_per_text` | Inline icon bbox measurement backend. Use `query_all` for normal work. `shell_per_text` is kept for investigation only. |
| `inkscape_shell_workers` | integer >= 1 | Parallel Inkscape shell workers used during export. |
| `split_svg_output` | `0`, `1` | Enables generated SVG parts. |
| `split_svg_chunk_mb` | integer >= 1 | Target part size in MB. |

## Troubleshooting

If generation appears to use old settings, close the DeckMaker window before editing `preferences.ini`. The GUI should not save preferences on close, but explicit GUI interactions still save the corresponding preference.

If Inkscape hangs while opening a very large generated SVG, check whether the Export panel is being restored by the Inkscape profile. On Windows this state is stored in `%APPDATA%\inkscape\dialogs-state-ex.ini`; closing the Export panel or resetting that dialog state avoids the hang. CLI export and isolated-profile launches are not affected.

If inline icons are missing or misaligned, check `inline_icons_bbox_backend`. The supported backend is `query_all`. The `shell_per_text` backend is experimental because Inkscape shell has shown inconsistent bbox results for inline text spacers.

If the composed template engine fails with an "unsupported template" error, switch `template_engine` back to `legacy`.
