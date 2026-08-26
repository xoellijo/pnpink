# Transform

`Transform` applies visual changes to the object itself after it has already been placed.

Use it when you want to alter the final appearance of an object without changing the placeholder logic of `Fit` or `Fit-Anchor`.

`Transform` is not about the placeholder.
It is about the object itself once placement is already solved.

## Transform vs Fit
Use:

- `Fit` / `Fit-Anchor` for anything relative to the placeholder
- `Transform` for anything applied to the object itself

Examples of `Fit` concerns:

- fit mode
- anchor
- border
- clipping to the placeholder
- placement inside the placeholder

Examples of `Transform` concerns:

- rotate
- mirror
- opacity
- scale
- soft edges
- later, other visual effects such as blur, color adjustments or shadows

## Syntax

```txt
object_id.T{rotate=15 mirror=h}
object_id.T{opacity=50% scale=110%}
object_id.T{o=50% s=110%}
object_id.T{f=myFilter}
object_id.T{f=image1-9-1 e=8%}
@{source}.T{o=70%}
```

`Transform` can be written as:

- `Transform{...}`
- `T{...}`

## Parameters

Current parameters are:

- `rotate` or `r`
- `mirror` or `m`
- `opacity` or `o`
- `scale` or `s`
- `edge` or `e`
- `filter` or `f`
- `text` or `t`
- `inside` or `i`

## Rotate and Mirror

```txt
.T{rotate=15}
.T{r=-42.4}
.T{mirror=h}
.T{mirror=v}
```

Compact `~` can still carry the same operations:

```txt
object_id~^15
object_id~^^
object_id~|
object_id~||
```

In compact form, `^` means rotate and `|` / `||` mean horizontal / vertical mirror.

## Opacity

```txt
.T{o=50%}
.T{opacity=80%}
```

This changes the final opacity of the placed object.

## Scale

```txt
.T{s=1}
.T{s=110%}
.T{s=110%+2}
.T{s=[1+120% -3+90%]}
```

`scale` resizes the already placed object around its current center.

Accepted forms:

- one value: same scale in width and height
- two values: `[width height]`
- absolute values are millimeters when no unit is provided
- percentages are relative to the current placed size
- absolute and percentage parts can be combined

Examples:

- `s=1` makes the object 1 mm wider and 1 mm taller
- `s=110%` makes it 10% larger
- `s=110%+2` makes it 110% of current size plus 2 mm

## Soft Edges

```txt
.T{e=12%}
.T{edge=[12% 4%]}
.T{e=[5% 10% 15% 20%]}
```

`edge` creates a soft fade towards the edges of the object.

Accepted forms:

- one value: same fade on all sides
- two values: `[horizontal vertical]`
- four values: `[top right bottom left]`

Values are percentages.

## Filter Copy

```txt
.T{f=myFilter}
.T{filter=myFilter}
.T{f=image1-9-1}
```

`filter` applies an existing SVG filter to the final placed object.

Accepted references:

- the id of a `<filter>` element, for example `f=myFilter`
- the id of another element that already has a filter applied, for example `f=image1-9-1`

When an element id is used, PnPInk copies that element's current `filter` reference.
This is useful for reusing Inkscape-made effects such as color shifts, brightness tweaks or glows without rewriting the filter in the DSL.

## Text Replacement

```txt
icon.T{text=+1}
icon.T{t=-2}
icon.T{t=[+1 -2 +5]}
```

`text` replaces plain text inside the placed object.
With a list, values are applied to the first text node, second text node, and so on.

This is intended for reusable icons or groups that contain small labels.
When `text` is used, PnPInk places a real copy instead of a shared clone, so each instance can have its own text.
The original template object is not modified.

## Dynamic Shape-Inside Frames

`inside` trims a rectangular frame to the text linked to it through Inkscape's `shape-inside` property:

```txt
frame.T{i=x}
frame.T{i=y}
frame.T{i=a}
```

- `i=x` trims horizontally. Left-aligned text keeps the left edge, right-aligned text keeps the right edge, and centered text trims both sides.
- `i=y` keeps the top edge and trims the bottom.
- `i=a` applies both rules.

PnPInk places the frame and its linked text together and lets Inkscape flow the final text. Its text ID is registered in the same shared measurement batch used by inline-icons, so all requested text geometry is resolved by one `inkscape --query-all` call. Future text-geometry features can register another consumer in that batch without adding another Inkscape process. An empty linked text removes the placed frame and text. Arrays containing dynamic frames are packed again with their final widths and heights after that same measurement. The source must be a `<rect>` and its text must reference it with `shape-inside:url(#frame_id)`.

The hidden frame can be addressed through the visible text ID with the virtual `shape-inside` property. PnPInk resolves it automatically and creates a private frame only for instances that modify it:

```txt
placeholder, description
description[shape-inside]~[90%]i.T{i=a}, Text for this instance
```

Property-only headers inherit the previous target ID, so the same operation can be split into two columns:

```txt
description, [shape-inside]
Text for this instance, .T{i=a}
```

## Typical Use

```txt
photo.T{o=85%}
photo.T{e=10%}
photo.T{f=myWarmTint}
photo.T{f=image1-9-1 e=7%}
photo.T{o=75% e=[8% 3%]}
counter_icon.T{t=+3}
```

This is useful for:

- fading photos or textures
- softening cutout edges
- blending placed art into a tile or card background

## Related Pages

- [Fit-Anchor](fit-anchor.md)
- [Source](source.md)
