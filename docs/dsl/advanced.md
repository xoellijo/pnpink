# Advanced
Advanced DSL features that are implemented in code and useful in larger projects.

## Alias Definitions
Aliases reduce repetition in large datasets and make rows easier to read.

You can define aliases and reuse them later:

<div class="csv-dataset dataset-first-column" markdown>

| Comment/directive row |
| --- |
| @hero = @{assets/hero.png} |
| @icons = [icon_hp icon_atk icon_def] |

</div>

Then reference by index:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| @hero |
| @icons[2] |
| @icons[1..3] |
| @icons[*] |
| @icons[1 4 7] |

</div>

## Source Suffixes
Suffixes are useful when source resolution and fit behavior must be expressed in one token.

Source expressions support long Fit and compact `~` ops:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| @{assets/token.svg}.Fit{mode=i anchor=5} |
| @{assets/token.svg}~i5^15\| |

</div>

The compact form can mix Fit and Transform: `i5` is Fit/Anchor, while `^15|` is Transform.

## Page Cursor Control (`at`)
Use cursor control when output must start at a specific page position.

`Page{}` supports page cursor movement with `at` / `a` / `@`:

<div class="csv-dataset dataset-body-only dataset-first-column" markdown>

|  |
| --- |
| {A4 @+3} |
| Page{A4 at=-1} |
| Page{A4^@5} |

</div>

This controls where the next generated content starts in the global page sequence.

## Page Break Blocks
These forms are useful for explicit pagination control between logical sections.

Standalone page blocks are valid and useful:

<div class="csv-dataset dataset-body-only dataset-first-column dataset-comments" markdown>

|  | Meaning |
| --- | --- |
| {} | # Break to next page |
| {3} | # Advance by 3 pages |
| {3*A4} | # Multiplier + size |

</div>

## Leading Cell Composition
Combining directives in column A allows row-level orchestration without extra columns.

Column A data rows can combine multiple directives in one cell:

<div class="csv-dataset dataset-body-only dataset-first-column" markdown>

|  |
| --- |
| {A4 b=[-5]} .L{p=3x3 g=2} .M{mk_default d=2} [10 3- 5] |

</div>

Supported order is flexible, but the parser expects:

1. Optional `Page{}` block.
2. Optional `.M{}` marks block.
3. Optional `.L{}` layout block.
4. Optional copy/hole tail (`[10 3- 5]` or trailing number).

## Inline Icon Tokens in Text
Inline icons accept existing SVG IDs, local and web sources, Fit-Anchor suffixes, text transforms and several layered icons sharing one typographic hole. See [Text](../text.md#inline-icons) for the complete user-facing syntax and examples.

## Forced BBox for Groups with Text

Inkex versions shipped with Inkscape 1.2 through 1.4.x can measure groups containing text incorrectly. The failure is not limited to returning a zero-sized text bbox: the resulting group bbox can also be unpredictable.

As a workaround, add a direct child `<rect>` whose SVG id or Inkscape label starts with `force_bbox`, `force-bbox`, or `forcebbox` (case-insensitive). PnPInk uses that rectangle as the group's bbox even when it is fully transparent. An explicit `data-bbox` still has higher priority.

## Path Decorations for Rich Text
Rich-text `<tspan>` elements can generate styled artwork behind or in front of their rendered text through the `pnp:decoration` attributes. See [Text](../text.md#decorating-part-of-a-text) for layers, padding, style sources, limitations and the `:TM(...)` helper.
