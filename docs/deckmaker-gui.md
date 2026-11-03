# DeckMaker GUI

DeckMaker is the user-facing PnPInk window used to generate decks from an SVG template and a dataset.

PnPInk includes two interfaces over the same rendering and export engine:

- **DeckMaker** opens the classic resident desktop window.
- **DeckMaker Web** starts a local server and opens a lil-xgui interface in the default browser.

Both entries are available from **Extensions > PnPInk** in Inkscape and receive the current saved SVG plus a temporary snapshot containing unsaved document changes. DeckMaker Web can also run independently: execute `<inkscape-python> <pnpink-extension-dir>/deckmaker_web.py [--template template.svg]`. It remembers the last saved template, so later launches do not require `--template`. A desktop shortcut can execute the same command.

## Runtime and portability

DeckMaker Web starts its resident process with the same `sys.executable` that Inkscape used to run the extension. Official Windows builds therefore reuse Inkscape's bundled Python, and macOS app bundles use their own selected extension interpreter. Linux packages do not universally bundle a private Python; on Linux this may be the system Python selected by the Inkscape package. PnPInk does not switch the resident process to a project virtual environment.

The local HTTP application and its IPC channel use the Python standard library. The preview workers are persistent `inkscape --shell` child processes owned by that resident service. Windows uses `inkscape.com` when available; Linux uses the executable found on `PATH`; macOS additionally detects `Inkscape.app` in `/Applications` and `~/Applications`. Closing the web application ends the resident service and its Inkscape workers after the browser-session grace period. Select an installed browser under **Preferences > Web engine**; the change applies on the next launch. The value is stored as `deckmaker_web_browser` in `preferences.ini`, and the former Tkinter browser selector has been removed.

No Node.js, npm, pywebview, or separate application server is required at runtime. A Chromium-family browser provides the standalone app window when available; other browsers use a normal tab fallback.

The standalone launch uses the saved SVG. Launch from Inkscape only when DeckMaker must capture unsaved document changes or the current SVG selection, for example when preparing a spritesheet. DeckMaker Web listens only on `127.0.0.1`, requires a random session token, and does not require Node, npm, or pywebview. Its `material-symbols-light` icons are loaded from Iconify, so Internet access is used for the icon layer only.

Drop a Google Sheets URL onto the dataset pane to extract its spreadsheet ID and `gid` and link it to the current template automatically. Launch DeckMaker Web once from each SVG in Inkscape to register it. Afterwards, use the template selector in the menu title to switch between known SVG templates together with their associated datasets. The selected source mode (`auto`, local CSV, public Google Sheet, or OAuth Google Sheet) is remembered separately for every template.

When the active template uses its adjacent local CSV, the dataset pane displays an editable spreadsheet with Tabulator 6.5.3 loaded from UNPKG. Rows and columns use spreadsheet coordinates, and the visible row number is always the physical CSV row. PnPInk syntax receives semantic text colors for directives, comments, `####` blocks, dataset headers, and `##`/`###` disabled dataset columns. The active dataset header becomes a temporary floating row only after its real row scrolls above the viewport; it changes when a later dataset is reached without renumbering or moving CSV rows. Edits are batched into atomic CSV writes. Native range, row, and column selection support copying, pasting, clearing, keyboard navigation, cell-edit undo/redo, and coordinated resizing of selected columns. Text extends through adjacent empty cells, while double-clicking opens a larger high-contrast editor without changing row height. The editor keeps its source cell visible, `Enter` commits, and `Alt+Enter` inserts a line break. The lil-xgui context menu changes row height and inserts or deletes rows and columns. Click the top-left corner to select the complete sheet. Google Sheet sources continue to use the embedded Google editor.

The editor deliberately combines Tabulator's public table, range, edit, clipboard, history, menu, component, and Virtual DOM APIs instead of its rigid multi-sheet `spreadsheet` data model. That model requires matrix data to be loaded exclusively through `spreadsheetData`/`setSheetData`; PnPInk instead needs stable physical CSV row numbers, incremental atomic writes, and semantic metadata for directives, comments, dataset headers, disabled columns, and invalid rows. The custom code is limited to those PnPInk-specific concerns: semantic formatting, Excel-like text spill, the enlarged editor, revision polling, and CSV persistence.

Browser edits are sent to the resident PnPInk Python service and written atomically to the linked CSV. PnPInk remains the only component that parses the CSV for previews, generation, and export. While the local editor is visible, a lightweight revision check detects external file changes every second and reloads the table only when the CSV revision changes. The dataset can be docked below or to the right of the preview, and its splitter position is remembered.

If a template has no dataset, DeckMaker Web can create a commented starter CSV from the template bbox and field IDs. **GS->CSV** downloads the currently resolved Google Sheet through the same dataset-loading pipeline used by generation, writes the adjacent CSV, switches the template to local CSV mode, and keeps the previous file once as `<name>.csv.old`. **Open GSheet** always opens the linked spreadsheet directly when its ID is known.

Only one long-running operation can run at a time. Closing or refreshing the browser does not stop an active generation or export because the work belongs to the resident Python process.

The browser actions execute the same Python generation, Inkscape export, PDF/PDF-X, cutting-template, and PnPPlay pipelines as classic DeckMaker. Its compact menu is organized as **Preview**, **Create**, **Export**, **Dataset**, **Preferences**, and **Help**. **Create** generates the complete output, opens it in Inkscape, optionally auto-opens it after generation, and builds PnPPlay assets. **Export** shows the enabled output formats in its title and groups the detailed settings under PDF, PDF/X, Image, and Cut. **Preferences** selects the web engine and provides direct access to the complete INI file. **Help** provides examples, documentation, tutorials, and visual or text logs. Unavailable actions remain disabled until their template, dataset, or generated output is ready.

### Preview backends

The **Preview** panel selects an asset, moves to the previous or next generated instance, chooses a viewer, and optionally synchronizes dataset and preview selections. The viewer and synchronization modes are saved in the shared PnPInk preferences and restored in later sessions. Selecting a local CSV cell can open its asset and highlight the generated element for that column. Two-way synchronization can also follow a generated object selected in Inkscape without selecting hidden template objects.

**Browser** keeps the embedded 96/600 DPI PNG workflow. **Inkscape** instead opens the generated SVG in one isolated, persistent Inkscape window and reloads that document in place when the selected asset changes. This preserves the viewer window, zoom, pan, size, and position while avoiding both PNG exports. The viewer starts eagerly, uses an A5 landscape page with rulers and a rectangular millimetre grid, and labels the page with the current preview. The isolated profile hides editing toolbars, palette, status bar, and scrollbars, and disables autosave and recent documents. The native **Layers and Objects** panel remains available through `Ctrl+Shift+L`; automatic keyboard/window manipulation is deliberately avoided because it is not portable or reliable.

## Spritesheet editor

Drop an image or PDF onto the preview canvas to enter spritesheet mode. A separate **Spritesheet** menu opens at the upper right of the preview with rows, columns, linked or independent gaps and borders, presets, custom frame dimensions, and a copyable `SP#` definition. PDF page controls appear in its title only while editing a PDF. When a PDF with the same name already exists beside the SVG or under its project asset folders, DeckMaker reuses that file directly instead of uploading it through the browser; other dropped PDFs are copied into `assets/`. The copied definition continues to reference the PDF; PnPInk creates and reuses project-local page PNGs only when `@SP1[B2]`, `@SP1[3!B2]`, or another page reference needs them. PDF pages use the highest effective resolution of their placed raster images, with a minimum of 300 DPI; vector-only pages also use 300 DPI. Multipage selectors can combine cells, rectangles, and cross-page linear ranges, and `*@SP1[*]` creates one generated item per frame across the complete PDF. Closing the menu restores the previous preview layout.

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
