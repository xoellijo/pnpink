# DSL Architecture
High-level flow of how PnPInk applies the DSL.

## Data to SVG Pipeline
This sequence explains where each DSL family acts in the generation process.

PnPInk processes in this order:

1. Read dataset (CSV or Google Sheets).
2. Parse dataset marker and header row.
3. Clone the template for each row (main template column).
4. Resolve sources (images/icons/URLs) into SVG symbols.
5. Apply Fit/Anchor to place each element.
6. Apply Layout to place instances into slots and pages.
7. Render Marks (cut marks) per placed slot.

## Front, Back, and Page Passes
These passes are execution phases, not just syntax flags.
They define when and where templates are rendered.

Header modifiers control when a template is rendered:

- `@page`: page-anchored, once per page, positioned against the page frame.
- `@back`: rendered in the back pass, aligned to front slots.
- `@page @back`: page-anchored but rendered on back pages.

These modifiers belong to template headers, not to data cells.

## Where Layout and Page Apply
This distinction prevents common mistakes when debugging placement.

- `Page{}` defines page size, margins, and cursor position.
- `Layout{}` defines the slot grid and how instances are placed.
- `Fit{}` defines how elements sit inside their anchor rects.

Page state is global. Layout state is per dataset. Fit is per element.

## Sources
Sources are resolved once and then treated as normal placement targets.

`Source{}` / `@{}` creates a reusable SVG symbol and then places it with Fit,
so sources behave like normal SVG targets.

## Inkscape Integration
These panels are the operational bridge between visual authoring and dataset-driven generation.

PnPInk relies on Inkscape objects and IDs:

- IDs and structure: `Object > Objects...` (Shift+Ctrl+O).
- Low-level XML/SVG: `Object > XML Editor` (Shift+Ctrl+X).
- Symbols: `Object > Symbols` (Shift+Ctrl+Y).
- Layers: `Layer > Layers` (Shift+Ctrl+L).
