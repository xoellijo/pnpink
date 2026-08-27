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
For detailed duplex/back-side behavior, see [@back -- Back-Side Templates](../dataset.md#back-side-templates).

## Where Layout and Page Apply
This distinction prevents common mistakes when debugging placement.

- `Page{}` defines page size, margins, and cursor position.
- `Layout{}` defines the slot grid and how instances are placed.
- `Fit{}` defines how elements sit inside their anchor rects.

Page state is global. Layout state is per dataset. Fit is per element.

## Shared Text Measurement

Text features that need rendered geometry register their element IDs in a shared measurement batch. Inline-icon spacers, `Transform{inside=...}` shape-inside text, and path decorations on rich-text `tspan` elements all use this batch. DeckMaker starts one persistent `inkscape --shell` helper for the lifetime of the application and reuses it across every Generate run. While cards are generated, PnPInk queues small per-card probe SVGs in that process. The final stage only collects results, converts them to document units, and returns each consumer its requested bboxes. Standalone extension runs own a temporary helper for that run. Future text-geometry consumers should join this pipeline instead of launching another Inkscape process.

## Sources
Sources are resolved once and then treated as normal placement targets.

`Source{}` / `@{}` creates a reusable SVG symbol and then places it with Fit,
so sources behave like normal SVG targets.

## Inkscape Integration
These panels are the operational bridge between visual authoring and dataset-driven generation.

PnPInk relies on Inkscape objects and IDs:

- IDs and structure workflow: [Introduction -> How IDs connect data to graphics](../intro.md#ids-workflow).
- Symbols: `Object > Symbols` (Shift+Ctrl+Y).
- Layers: `Layer > Layers` (Shift+Ctrl+L).
