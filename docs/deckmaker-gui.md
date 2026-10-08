# DeckMaker GUI

DeckMaker is the user-facing PnPInk window used to generate decks from an SVG template and a dataset.

PnPInk includes two interfaces over the same rendering and export engine:

- **DeckMaker** opens the classic resident desktop window.
- **DeckMaker Web** starts a local server and opens a lil-xgui interface in the default browser.

Both entries are available from **Extensions > PnPInk** in Inkscape and receive the current saved SVG plus a temporary snapshot containing unsaved document changes. DeckMaker Web can also run independently: execute `<inkscape-python> <pnpink-extension-dir>/deckmaker_web.py [--template template.svg]`. It remembers the last saved template, so later launches do not require `--template`. A desktop shortcut can execute the same command.

## Runtime and portability

DeckMaker Web starts its resident process with the same `sys.executable` that Inkscape used to run the extension. Official Windows builds therefore reuse Inkscape's bundled Python, and macOS app bundles use their own selected extension interpreter. Linux packages do not universally bundle a private Python; on Linux this may be the system Python selected by the Inkscape package. PnPInk does not switch the resident process to a project virtual environment.

The local HTTP application and its IPC channel use the Python standard library. The preview workers are persistent `inkscape --shell` child processes owned by that resident service. Windows uses `inkscape.com` when available; Linux uses the executable found on `PATH`; macOS additionally detects `Inkscape.app` in `/Applications` and `~/Applications`. Closing the web application ends the resident service and its Inkscape workers after the browser-session grace period.

No Node.js, npm, pywebview, or separate application server is required at runtime. A Chromium-family browser provides the standalone app window when available; other browsers use a normal tab fallback.

The standalone launch uses the saved SVG. Launch from Inkscape only when DeckMaker must capture unsaved document changes or the current SVG selection, for example when preparing a spritesheet. DeckMaker Web listens only on `127.0.0.1`, requires a random session token, and does not require Node, npm, or pywebview. Its `material-symbols-light` icons are loaded from Iconify, so Internet access is used for the icon layer only.

Drop a Google Sheets URL anywhere onto DeckMaker Web to extract its spreadsheet ID and `gid` and link it to the current template automatically. Launch DeckMaker Web once from each SVG in Inkscape to register it. Afterwards, use the template selector and preview in the web menu title to switch between known SVG templates together with their associated datasets.

Only one long-running operation can run at a time. Closing or refreshing the browser does not stop an active generation or export because the work belongs to the resident Python process.

The browser actions execute the same Python generation, Inkscape export, PDF/PDF-X, cutting-template, and PnPPlay pipelines as classic DeckMaker. The lower log keeps recent activity and remains scrollable while jobs run; unavailable actions remain disabled until their template, dataset, or generated output is ready. DeckMaker Web provides `Auto Open` and `Auto Export`; automatic generation remains exclusive to classic DeckMaker.

## Spritesheet editor

Select one image, group, or SVG object in Inkscape and open DeckMaker Web. The **Spritesheet** folder provides a live browser preview with grid, preset-size, and custom-size modes. It supports independent gaps and borders, row-first or column-first ordering, zoom, pan, frame addressing, and copyable `.Layout{...}` and `@alias[cell]` expressions. Reopen DeckMaker Web after changing the Inkscape selection.

The window title shows the running DeckMaker version and the current template file name. The full template path is not shown in the form because the extension is launched from the active Inkscape document.

Before opening DeckMaker for a local project, save the SVG to disk and place a CSV with the same base name beside it. For example, `cards.svg` and `cards.csv` form one automatically discoverable project. Keep the SVG template open when launching the extension; DeckMaker always uses the active document as its visual source.

## Deck tab

The Deck tab contains the data source controls, the main actions, and the live activity log.

### Data source

| Field | Purpose |
| --- | --- |
| `GSheet ID` | Google Spreadsheet id. This is only needed for Google Sheet sources. |
| `Range / gid` | Optional sheet range or grid id. Leave empty when the default source selection is enough. |
| `Source` | Selects how the dataset is loaded. Supported values are empty/default, local CSV, Google Sheet OAuth, and Google Sheet public. |

For local CSV workflows, the spreadsheet fields can be left empty when the source is resolved from the template or from the selected file.

For a first run, choose the local/default source, leave the Google fields empty, and click `Generate`. The generated SVG is written beside the template, normally with `_output.svg` appended to the base name. Use `Open SVG` to inspect that file, then return to the original SVG or CSV for corrections and generate again.

### Actions

| Control | Purpose |
| --- | --- |
| `Generate` | Builds the output SVG from the current template and dataset. |
| `Open SVG` | Opens the generated SVG through the same Inkscape launch path used by the extension. It does not use the operating system default application. |
| Checkbox after `Open SVG` | Opens the generated SVG automatically after generation. |
| `Export` | Runs the export pipeline configured in the Export tab. |
| Checkbox after `Export` | Runs export automatically after generation. |

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
| `inline_icons_extra_ratio` | decimal | Extra inline icon text advance as a fraction of text height. Negative values bring surrounding text closer without resizing the icon, for example `-0.10`. Default: `0.00`. |
| `inkscape_shell_workers` | integer >= 1 | Parallel Inkscape shell workers used during export. |
| `split_svg_output` | `0`, `1` | Enables generated SVG parts. |
| `split_svg_chunk_mb` | integer >= 1 | Target part size in MB. |

## Troubleshooting

If generation appears to use old settings, close the DeckMaker window before editing `preferences.ini`. The GUI should not save preferences on close, but explicit GUI interactions still save the corresponding preference.

If Inkscape hangs while opening a very large generated SVG, check whether the Export panel is being restored by the Inkscape profile. On Windows this state is stored in `%APPDATA%\inkscape\dialogs-state-ex.ini`; closing the Export panel or resetting that dialog state avoids the hang. CLI export and isolated-profile launches are not affected.

If the composed template engine fails with an "unsupported template" error, switch `template_engine` back to `legacy`.
