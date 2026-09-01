# Page
Defines page format, margins, and the global page cursor.
Use `Page{}` whenever print format, orientation, or pagination position must change.

## Syntax
```txt
Page{ size landscape border at }   or   P{A4}   or   {A4}
```

When `Page{}` appears in the first column, the keyword `Page/P` can be omitted.
A bare `{}` means "continue using the previous page size".

Multipliers are allowed:

<div class="csv-dataset dataset-body-only dataset-first-column dataset-comments" markdown>

|  | Meaning |
| --- | --- |
| {3*A4} | # Three A4 pages |
| {3} | # Shorthand for three pages |

</div>

## Size (default)
Page size is usually set once and then reused by state.

The default parameter is the page size:

<div class="csv-dataset dataset-body-only dataset-first-column" markdown>

|  |
| --- |
| Page{A4} |
| Page{Letter} |
| Page{23.3x34.45} |

</div>

## Landscape / Portrait
Orientation is part of page state and affects all following placements until changed.

<div class="csv-dataset dataset-body-only dataset-first-column dataset-comments" markdown>

|  | Meaning |
| --- | --- |
| Page{A4^} | # Landscape |
| Page{landscape} | # Landscape |
| Page{portrait} | # Portrait |

</div>

## Border (b=)
`b` defines the usable inner area (or outward expansion) for layout planning.

`b=` defines padding or margin around the page.

<div class="csv-dataset dataset-body-only dataset-first-column dataset-comments" markdown>

|  | Meaning |
| --- | --- |
| b=[-2] | # 2 mm inward margin |
| b=[2 3 4 5] | # Top, right, bottom, left |

</div>

Percentages and absolute `WxH` values are allowed (same grammar as Fit).

For `%` borders in 1/2-token forms, percentages define absolute target scale:

- `b=[50%]` -> final size x0.5
- `b=[125%]` -> final size x1.25
- `b=[50% %]` -> height x0.5, width x1.0
- `b=[23x?]` / `b=[?x12]` are valid in `WxH` absolute-size mode (`?` = 100% on that axis).

## Page Cursor (at / a / @)
Cursor control is for advanced pagination workflows, such as merging sections or forcing output positions.

`at` moves the global page cursor.

Accepted forms:

<div class="csv-dataset dataset-body-only dataset-first-column" markdown>

|  |
| --- |
| at=+3 |
| a=-1 |
| @5 |
| A4@+2 |
| { @-1 } |

</div>

Rules:

- `+n` / `-n` -> relative move.
- `n` -> absolute 1-based page (n -> index n-1).
- If the current page already has content, the engine jumps to a new page first.
