# Marks

The Marks module generates cut marks aligned to the **Layout grid slots** of the **main template** (first dataset column).
Use it when output is intended for physical cutting and alignment consistency matters.

Marks are defined in the **first header cell** (same place as `Page{}` and `Layout{}`), for example:

```txt
{A4}`.L{p=3x3 g=2}`.M{ mk_style len=[3 2] d=2 }
```

## Slot-Based Behavior
This behavior explains why marks remain correct even when pages, gaps, or offsets change.

Marks are generated **per slot** (per placed instance in the grid), using the transformed bounding box of the main template for that slot.

This means:

- Marks follow the real grid placement (including gaps, offsets, page breaks, rotations, etc.).
- Marks are not tied to a specific template column; they follow the main layout slots.

## Syntax
The syntax is short, but each parameter controls a different geometric aspect of cut lines.

```txt
Marks{ style len distance border layer }
```

Shorthand:

```txt
M{ ... }
```

Elements inside `{}` are space-separated. Lists are written in `[]` and are space-separated.

## Default Parameter (Style)
Style controls visual appearance, not mark geometry.
It is resolved from existing SVG elements.

The default parameter of `Marks{}` is the style id. This means `style=` can be omitted:

```txt
`.M{ mk_style len=3 d=2 }`
```

is equivalent to:

```txt
`.M{ s=mk_style len=3 d=2 }`
```

### Style Sources
A style id (s=) references an SVG element by ID:

- If the ID refers to a single element (path/shape), its stroke-related style is copied to the generated mark paths.
- If the ID refers to a group (`<g>`) containing multiple child paths, it is treated as a **style stack**.
  The mark geometry is generated multiple times, each copy receives the style of one child, and the result is layered.

If no `s` is provided, a default style from preferences is used.

## Length (len=)
Length controls how far marks extend outside and inside slot edges.

`len=` defines the length of the mark segments.

Rules:

- Scalar: `len=3` means **external length = 3**, internal length = 0.
- Two-value list: `len=[out in]`.

If the layout uses offsets (staggered grids), a scalar `len=3` is treated as `[3 3]` to keep internal marks visible.

## Distance to Card (d=)
Distance is the gap between the card edge and the start of the external mark segment.

`d=` sets the distance between the card edge and the **external** mark origin.

Default: `d=2`.

`d` follows the same list grammar as border in Fit/Page:

```txt
d=[2 3 4 5] -> top, right, bottom, left
```

## Border Pattern (b=)
Border offsets let you fine-tune mark placement against bleed/margin strategies.

`b=` defines the cut-mark border offsets using the same conventions as borders in Fit/Page.

Default: `b=0`.

## Output Layer (layer=)
Separating marks into a dedicated layer helps review, export, and printer handoff.

`layer=` defines the target Inkscape layer where marks will be inserted.

Default: `layer=marks`.

If the layer does not exist, it is created.

Example:

```txt
`.M{ layer=cutmarks }`
```

## Scope
This section documents current implementation limits to avoid false expectations.

The `scope=` parameter is documented but **not implemented** in the current engine.
Marks are always generated on the front pass.

## Examples
**Basic cut marks with default style**

```txt
{A4}`.L{p=3x3 g=2}`.M{}
```

**Using a style element**

```txt
{A4}`.L{p=3x3}`.M{ mk_cut }
```

**Overlay style using a group**

```txt
{A4}`.L{p=3x3}`.M{ mk_cut_stack len=[3 2] }
```
