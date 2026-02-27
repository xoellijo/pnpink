# Layout
Defines how a group of elements are arranged in a grid (e.g. cards on pages).
Use this module when you want deterministic pagination and slot planning.

## Syntax

```txt
Layout{ p=nxm g=[x y] o=[w1 h1 w2 h2] shape=... } or L{nxm g=... o=... s=...}
```

Can be applied to any object:

```txt
rect.L{}
```

```txt
[rect1 rect2 ...]`.L{}`
```

```txt
{A4}`.L{p=5x8 s=hexgrid}`
```

## Pattern (p=)
`p` is the structural core of layout.
It defines how many slots exist per page before page breaks occur.

`p=` (or positional token) defines columns x rows.

```txt
p=3x2
p=0x0
```

`0x0` means auto-fit based on available area.

### Order and Flips
Order and flips matter when instance numbering and print order must follow a specific physical workflow.

The grid token supports modifiers:

- `^` switches order to top-to-bottom, then left-to-right.
- `|` flips the axis (use after the number you want to flip).
- Negative numbers also flip that axis.

Examples:

```txt
p=3x2        -> left-to-right, top-to-bottom
p=3x2^       -> top-to-bottom first
p=3|x2       -> flip columns
p=3x-2       -> flip rows
p=-3x-2      -> flip both axes
```

## Gaps (g=)
Use gaps to control spacing between slots without changing card/template size.

`g=` (or `gaps=`) defines spacing between slots.

```txt
g=[x y]
g=2
```

Rules:

- 1 value means `x=y`.
- 2 values mean `x` (horizontal) and `y` (vertical).
- Units and percentages are allowed.

Examples:

```txt
g=2
g=[2 3]
g=[1% 3%]
```

Percentages require a known card size (shape preset or template size).

## Offset (o=)
Offsets are useful for staggered layouts, including hex-like placement.
They modify slot position patterns, not slot dimensions.

`o=` (or `offset=`) defines staggered offsets for alternating slots.

```txt
o=[w1 h1 w2 h2]
```

Notes:

- If only `w1 h1` are provided, `w2 h2` defaults to `-w1 -h1`.
- Offsets are applied after slot sizing and before final placement.

This is how hex-like or staggered grids are achieved without changing card size.

## Shape (s=)
Shape presets provide normalized card/tile sizes so layouts remain consistent across projects.

Defines the shape preset of each grid cell.

Examples:

```txt
s=poker
s=minieuro
s=55.3x77.1
s=rect<55.3x77.1>
s=hex<24x33>
s=polygon<[5 23x32]>
```

See the Presets page for the full list of named sizes.

### Hex Shapes
Hex shapes are specialized layout modes for maps and tile production.
They automate spacing behavior that would otherwise require manual offset tuning.

Smart shapes adjust gaps and offsets without changing card size:

- `s=hexgrid` for maps, boards, overlays.
- `s=hextiles` for cuttable tiles with shared edges.

Inkscape tip: use `Tools > Stars and Polygons` (Shift+F9), set corners to 6,
and hold Ctrl while resizing to keep alignment.
