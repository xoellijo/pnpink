# Extensions and Tools

This page summarizes the user-facing extensions present in the current project version.

## Main generation tools

### Deckmaker
Visible in Inkscape as:

`Extensions > PnPInk > Deckmaker v0.83`

Purpose:

- read CSV or Google Sheets data,
- expand snippets,
- clone templates,
- apply page/layout/source/fit rules,
- generate an editable SVG output,
- open the generated SVG in Inkscape,
- export PDF, PDF/X, and other output formats.

The GUI title shows the current Deckmaker version and the template file name. The former desktop interface remains temporarily available as `Deckmaker v0.83 [OLD gui]`. Spritesheet editing is integrated into Deckmaker and no longer has a separate Inkscape menu entry. See [Deckmaker](deckmaker-gui.md) and [Export](export.md).

## Project/package tools

### PnPInk ZVG Import / Export
File format: `.zvg`

ZVG packages an SVG project together with its local assets so another user can reopen the work reproducibly as a portable project.

### PnPInk PNP Import / Export
File format: `.pnp`

PNP is a lighter, regeneration-oriented package that preserves the SVG and dataset workflow while keeping the payload smaller when the project does not need every source asset embedded.

See also [Packages](dsl/packages.md).

## Utility tools

PnPInk preferences are stored in `preferences.ini`. Browser selection no longer
uses a separate Tkinter extension; it is configured with the
`deckmaker_web_browser` preference until the web preference editor is available.

### Docs and Examples
Visible in Inkscape as:

`Extensions > PnPInk > Docs and Examples`

This command opens the installed PnPInk folder and provides quick access to bundled examples and local documentation. It is a useful starting point when you want to inspect a working project rather than build one from an empty file.

## Operational notes

### Google Sheets authentication
The current codebase includes a PKCE-based Google Sheets client. The authentication flow is implemented in the project, but the operational setup is only lightly documented in the current docs set.

### Logging
The project writes runtime logs to `src/pnpink.log`. This is useful when validating parser behavior, measuring generation/export time, or investigating mismatches between dataset and output.

### Web-source caching
Remote web sources are cached locally and may later be included in ZVG packages depending on package mode and source type.
