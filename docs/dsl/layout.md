# Layout
Defines how a group of elements are arranged in a grid (e.g. cards on pages).
Use this module when you want deterministic pagination and slot planning.

## Syntax

```txt
Layout{ p=nxm g=[x y] o=[w1 h1 w2 h2] shape=... } or L{nxm g=... o=... s=...}
```

Layout is used in three places:

In the first dataset column, Page/Layout settings are inherited by following rows until another first-column cell changes them.

```txt
{A4}.L{p=5x8 s=hexgrid}       ## Page module: impose dataset rows into page slots
```

Inside any cell, Layout can place a set of objects in a grid, using the placeholder rectangle defined by that dataset column header:

```txt
placeholder_rect
[obj1 obj2 ...].L{}           ## local array layout inside placeholder_rect (dataset header)
```

For spritesheet definitions, usually declared in comment lines before the dataset, Layout works like the inverse of page imposition: it cuts pieces from an image, page, PDF, etc.

```txt
# @cards = @{cards.png}.L{p=4x3 g=2}   ## split image source into a 4 columns x 3 rows spritesheet
```

Then frames can be referenced as source objects, fitted, or transformed:

```txt
@cards[B3]                    ## frame at column B, row 3
```

## Pattern (p=)
`p` is the structural core of layout.
It defines how elements are arranged inside a rectangular space, or how many slots exist per page before page breaks occur when applied to a Page module.

`p=` defines columns x rows. It is the default layout parameter, so `p=` can be omitted.

Default pattern is `0x0` (equivalent to `?x?`).
`0` and `?` are equivalent in pattern: auto-fit based on available area.

```txt
p=3x2        ## left-to-right, top-to-bottom (default)
              -> A B C
                 D E F
3x2          ## same as p=3x2
p=0x0        ## as many columns and rows as fit
p=?x?        ## same as p=0x0
p=3x?        ## 3 columns and as many rows as fit
p=?x4        ## as many columns as fit and 4 rows
p=-?x-?      ## auto-fit, reversed: right-to-left and bottom-to-top
```

## Slot Selectors
Layout slots can be addressed with spreadsheet-style cell references.

```txt
{A4}.L{4x3} [A3 B2 7]      ## explicit unordered slot list
{A4}.L{4x3} [A3 7]         ## place A3, then 7 more slots in layout order
{A4}.L{4x3} [A6 2- 5]      ## place A6, skip 2 slots, then place 5 more
```

Use `:` for rectangular ranges and `..` for linear walks following layout order.

### Order and Flips
Order and flips matter when instance numbering and print order must follow a specific physical workflow.

The grid token supports modifiers:

- `^` switches order to top-to-bottom, then left-to-right.
- `|` or negative numbers flip the axis (use after the number you want to flip).

Examples:

```txt
p=3x2        ## left-to-right, top-to-bottom (default)
p=3x2^       ## top-to-bottom first (like a rotation)
              > A D
                B E
                C F
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
g=?
```

Rules:

- 2 values mean `x` (horizontal) and `y` (vertical).
- 1 value means `x=y`; brackets can be omitted.
- Units, percentages, and mixed expressions are allowed, e.g. `g=[2+3% 0]`.
- `?` means auto gap (distribute remaining space evenly to fit the final content area on that axis).

Examples:

```txt
g=2             ## same horizontal and vertical gap
g=[2 3]         ## 2 mm horizontal, 3 mm vertical
g=[2+3% 0]      ## expressions can mix absolute values and percentages
g=?             ## auto-distribute remaining space on both axes
```

Percentages require a known card size (shape preset or template size).

## Offset (o=)
Offsets shift each row/column relative to the previous one.
They modify slot positions, not slot dimensions.

`o=` (or `offset=`) defines staggered offsets for alternating slots.

```txt
o=[w1 h1 w2 h2]
```

Notes:

- `w1` shifts every second row horizontally relative to the previous row.
- `h1` shifts every second column vertically relative to the previous column.
- `w2` and `h2` define the next alternating shift.
- If only `w1 h1` are provided, `w2 h2` defaults to `-w1 -h1`.
- As with gaps, absolute values, percentages, and mixed expressions are allowed.

Examples:

```txt
o=[50% 0]       ## each odd row starts half a slot to the right
o=[0 50%]       ## each odd column starts half a slot down
o=[2+25% 0]     ## offsets can combine absolute and percentage values
```

`hexgrid` and `hextiles` are built from automatically calculated offsets.
You can still add explicit offsets to a hex layout; they are added on top of the generated hex offsets.

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

Shape suffixes rotate the placed item; they do not swap preset width/height.

Explicit item rotation:

```txt
s=poker^       ## rotate item +90 degrees
s=poker^^      ## rotate item 180 degrees
s=poker^^^     ## rotate item -90 degrees (270)
```

Without an explicit suffix, PnPInk compares the template bbox orientation with the destination shape orientation and rotates only when it reduces deformation.

```txt
s=creditcard   ## portrait template -> landscape shape: auto-rotate -90 degrees
s=poker        ## landscape template -> portrait shape: auto-rotate +90 degrees
s=square       ## square template or square shape: no auto-rotation
s=55.3x77.1^   ## explicit rotation disables auto-rotation
```

See the Presets page for the full list of named sizes.

### Hex Shapes
Hex shapes are specialized layout modes for maps and tile production.
They automate spacing behavior that would otherwise require manual offset tuning.

Smart shapes adjust gaps and offsets without changing card size:

- `s=hexgrid` for maps, boards, overlays.
- `s=hextiles` for cuttable tiles with shared edges.

Shape item rotation does not apply to these smart hex shape modes.

Inkscape tip: use `Tools > Stars and Polygons` (Shift+F9), set corners to 6,
and hold Ctrl while resizing to keep alignment.

For practical examples and the differences between `hexgrid`, `hextiles`, and hex cut lines, see [Hexes](hexes.md).
