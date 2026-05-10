# Extensions and Tools

This page summarizes the user-facing extensions present in the current project version.

## Main generation tools

### DeckMaker
Visible in Inkscape as:

`Extensions > PnPInk > DeckMaker`

Purpose:

- read CSV or Google Sheets data,
- expand snippets,
- clone templates,
- apply page/layout/source/fit rules,
- generate an editable SVG output,
- open the generated SVG in Inkscape,
- export PDF, PDF/X, and other output formats.

The GUI title shows the current DeckMaker version and the template file name. See [DeckMaker GUI](deckmaker-gui.md) and [Export](export.md).

### Spritesheet
Visible in Inkscape as:

`Extensions > PnPInk > Spritesheet`

Purpose:

- open the interactive spritesheet GUI,
- define grid cuts and frame extraction workflows for atlas-like image sources.

## Project/package tools

### PnPInk ZVG Import / Export
File format: `.zvg`

Purpose:

- package an SVG project with local assets for reproducible sharing,
- reopen packaged work as a portable project.

### PnPInk PNP Import / Export
File format: `.pnp`

Purpose:

- package a lighter regeneration-oriented project,
- preserve the SVG + dataset workflow with smaller payloads when possible.

See also [Packages](dsl/packages.md).

## Utility tools

### Preferences
Visible in Inkscape as:

`Extensions > PnPInk > Preferences`

Purpose:

- configure console and file log levels,
- configure JSON logging behavior,
- configure SVG output splitting,
- configure export and advanced generation options,
- persist preferences in `preferences.ini`.

### Docs and Examples
Visible in Inkscape as:

`Extensions > PnPInk > Docs and Examples`

Purpose:

- open the installed PnPInk folder in the system browser,
- provide quick access to examples and local documentation.

## Operational notes

### Google Sheets authentication
The current codebase includes a PKCE-based Google Sheets client. The authentication flow is implemented in the project, but the operational setup is only lightly documented in the current docs set.

### Logging
The project writes runtime logs to `src/pnpink.log`. This is useful when validating parser behavior, measuring generation/export time, or investigating mismatches between dataset and output.

### Web-source caching
Remote web sources are cached locally and may later be included in ZVG packages depending on package mode and source type.
