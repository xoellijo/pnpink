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

```txt
{3*A4} -> three A4 pages
{3}    -> shorthand for three pages
```

## Size (default)
Page size is usually set once and then reused by state.

The default parameter is the page size:

```txt
Page{A4}
Page{Letter}
Page{23.3x34.45}
```

## Landscape / Portrait
Orientation is part of page state and affects all following placements until changed.

```txt
Page{A4^}        -> landscape
Page{landscape}
Page{portrait}
```

## Border (b=)
`b` defines the usable inner area (or outward expansion) for layout planning.

`b=` defines padding or margin around the page.

```txt
b=[-2]      -> 2 mm inward margin
b=[2 3 4 5] -> top, right, bottom, left
```

Percentages and absolute `WxH` values are allowed (same grammar as Fit).

## Page Cursor (at / a / @)
Cursor control is for advanced pagination workflows, such as merging sections or forcing output positions.

`at` moves the global page cursor.

Accepted forms:

```txt
at=+3
a=-1
@5
A4@+2
{ @-1 }
```

Rules:

- `+n` / `-n` -> relative move.
- `n` -> absolute 1-based page (n -> index n-1).
- If the current page already has content, the engine jumps to a new page first.
