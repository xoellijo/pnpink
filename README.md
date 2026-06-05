<img src="docs/assets/images/pnpink_logo.png" alt="PnPInk logo" align="left" width="120" hspace="10" />

# PnPInk
[![Latest release](https://img.shields.io/github/v/release/xoellijo/pnpink?label=latest%20release)](https://github.com/xoellijo/pnpink/releases)

PnPInk is a free, open-source, cross-platform data-merge engine for Inkscape.

PnPInk is inspired by [CounterSheets](https://github.com/lifelike/countersheetsextension).

It turns Inkscape into a visual composition system where one SVG template plus structured data (CSV/Google Sheets/web sources) can generate rich, editable outputs at scale.

PnPInk automates print-and-play production (cards, boards, punchboards, counters, tiles), but it also works for broader publishing tasks such as labels, data-driven sheets, and vector-heavy PDF compositions.

It combines Inkscape's full graphic power (gradients, filters, paths, symbols, and layers) with a visual GUI and a data-driven workflow (Google Sheets/CSV plus internet resources), producing editable, print-ready layouts with pixel-precise results.

PnPInk also includes a simple but powerful DSL (domain-specific language) that enables advanced layouts without complex scripting.

The key workflow is iterative: update the template or dataset parameters, re-run composition, and get refreshed layouts in seconds.

The DSL is designed to match how designers think, not how programmers write code: compact notation, visual intent first, and minimal cognitive load.

<br clear="left" />

## Key Principles

1. `Open Source`: fully transparent, community-driven, and free to use.
2. `Cross-Platform`: works on Windows, macOS, and Linux.
3. `Non-Destructive`: everything remains fully editable SVG; nothing is baked or lost.
4. `Accessible & Visual`: no programming required; GUI-based and intuitive.
5. `Full Production Pipeline`: supports the full cycle, from quick prototypes to open distribution (PDF) and online sharing.

## Features

- Standalone DeckMaker GUI for generation and export, with live progress, auto-generate, auto-open, and auto-export controls.
- Fast data-driven generation from CSV or Google Sheets.
- Placeholder-driven templates: map dataset columns to SVG IDs and regenerate entire sets instantly.
- Precise `Fit` + `Anchor` controls for predictable placement and scaling inside target frames.
- Front/back workflows (`@back`) for duplex cards and mirrored layouts.
- Built-in bleed and margin controls for print-safe compositions.
- Cutting and registration marks (`Marks`) generated directly from layout logic.
- Cut-only plotter templates for Cricut and Silhouette Cameo workflows.
- Reusable presets for page, grid, and component sizing.
- Inline text icons: type icon names in text flow and render vector icons in-place.
- Source catalogs: resolve assets by name from the dataset, including large free libraries (200K+ icons and 2M+ images, depending on source).
- Hot SVG map generation from source expressions such as `osm://...` and `ofm://...`.
- Spritesheet workflows for atlas-based assets and high-volume content pipelines.
- Package formats (`.zvg` / `.pnp`) for portable projects with SVG, dataset, manifest, and assets.
- Fully editable SVG output after generation, not flattened exports.
- Professional export pipeline: PDF profiles, PDF/X CMYK, ICC profile handling, smart rasterization of filtered SVG content, SVG chunking for large decks, and additional formats such as PNG, JPEG, TIFF, WebP, PS, EPS, EMF, and WMF.
- VDP-oriented performance optimizations capable of generating more than 60K items/minute on suitable templates and hardware.
- Native Inkscape workflow: design, compose, iterate, inspect, and export without leaving the editor.

One template and one dataset can produce hundreds of print-ready components in seconds, and every generated piece remains editable.

## Recent Additions

- `DeckMaker GUI`: a dedicated window for dataset selection, generation, SVG opening, export configuration, progress reporting, and throughput feedback in records/min.
- `Export module`: professional PDF output with presets (`default`, `screen`, `ebook`, `printer`, `prepress`), PDF/X CMYK export, ICC selection, pure-black text handling, filter rasterization modes, parallel Inkscape shell workers, and chunk-aware processing for large SVG outputs.
- `Additional export formats`: page-based export to PNG, JPEG, JPEG2000, TIFF, WebP, PDF, SVG, PS, EPS, EMF, and WMF.
- `Cutting-plotter templates`: cut-only SVG/DXF/PNG templates for Cricut, Silhouette Cameo, and raster fallback workflows.
- `Map sources`: on-demand SVG map generation using `osm://...` and `ofm://...` source expressions, usable like other PnPInk assets.
- `Large-output handling`: optional SVG splitting into reusable parts so very large decks can be exported more reliably.
- `VDP performance`: optimized generation paths for high-volume "Variable Data Printing", with measured throughput above 85K records/minute on suitable projects.
- `Template optimization work`: generated outputs can reuse hoisted template images and shared text styles to reduce repeated SVG content.

## Installation

1. Install [Inkscape](https://inkscape.org/release/) (container builds such as Flatpak are not supported).
2. Download the latest installer ZIP directly: [pnpink_latest.zip](https://github.com/xoellijo/pnpink/releases/latest/download/pnpink_latest.zip).
   If that direct link does not work, open [All Releases](https://github.com/xoellijo/pnpink/releases) and download `pnpink_latest.zip` from the `Assets` section.
3. Extract the ZIP.
4. Run:
   - Windows: `install_windows.bat`
   - macOS/Linux: `chmod +x install.sh && ./install.sh`
   - Any OS: `python install.py` (or `python3 install.py`)
5. Restart Inkscape and confirm `Extensions > PnPInk ...` appears.

## First Recommended Run

1. Start by opening the examples from `Extensions > PnPInk > Deckmaker > About > Open examples folder`.
2. Try the prepared SVG examples with their companion CSV datasets, or open `.PNP` files, which self-contain the SVG template and CSV dataset and auto-generate when opened.
3. Build your own template SVG with IDs (`title`, `cost`, `art`, etc.).
4. Prepare a CSV/Sheet with matching columns.
5. Run DeckMaker from `Extensions > PnPInk ...`.
6. Regenerate as you iterate: tweak template or dataset settings, then compose again.

## Documentation

- Start here (searchable docs site): [Introduction](https://xoellijo.github.io/pnpink/intro/)
- Full PDF guide: [pnpink.pdf](https://xoellijo.github.io/pnpink/pnpink.pdf)
- DeckMaker GUI: [DeckMaker GUI](https://xoellijo.github.io/pnpink/deckmaker-gui/)
- Export pipeline: [Export](https://xoellijo.github.io/pnpink/export/)
- SVG maps: [Maps](https://xoellijo.github.io/pnpink/dsl/maps/)

## Project Status

This repository is currently in `beta` release stage.
It has been widely tested with Inkscape 1.2 through 1.5 on Windows and Linux environments.
Until stabilization is explicitly announced, DSL changes may break backward compatibility and older datasets/templates may require updates.

Join the community on the BGG Guild to follow progress, share use cases, and influence the roadmap:

- [`PnPInk BGG Guild`](https://boardgamegeek.com/guild/4569)

## Long-Term Roadmap

Potential long-term directions (no guarantees): exports for virtual tabletop platforms, local AI-assisted bulk production tools, deeper map workflows, and professional integrations for QR/barcode and labeling standards.
