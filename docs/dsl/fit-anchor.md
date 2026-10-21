# Fit-Anchor
Fit-Anchor is one of the most important modules in PnPInk.
It is the placement engine that lets you position objects relative to placeholders without manual coordinates and without hardcoding object/template sizes.

In practice, Fit-Anchor lets you place one or many objects on a placeholder and then size, align, shift and clip them relative to that placeholder.

Fit-Anchor is about the relationship between an object and its placeholder.
For visual changes applied to the object itself after placement, use [Transform](transform.md).

## Dataset Mental Model
Fit-Anchor is easier to understand if you keep this model in mind:

- the dataset header points to a placeholder object in the SVG,
- each row cell provides one or many source objects,
- Fit-Anchor defines how those source objects are positioned relative to that placeholder for each generated card/instance.

## Fit vs Transform
Use:

- `Fit` / `Fit-Anchor` for placement relative to the placeholder
- `Transform` for changes applied to the placed object itself

Typical Fit-Anchor concerns are:

- size inside the placeholder
- alignment within the placeholder
- border or fit area
- clipping to the placeholder

Typical Transform concerns are:

- rotate
- mirror
- opacity
- soft edges
- later, other visual effects applied directly to the object

## What Is an "Object" Here?
In this documentation, we call IDs "objects" (not "elements") from the DSL/user point of view.

An object can be:

- an existing SVG object by ID (rect, path, group, text, image, etc.),
- a dynamic object created by `Source` (local file, spritesheet frame, internet URL, Wikimedia/Pixabay, icon library),
- a multivalue object list in one cell: `id1~7 id2~9 id3~3`,
- an explicit object array: `[id1 id2 id3]` (optionally with local array layout).

Representative examples of dataset cells:

<div class="csv-dataset dataset-comments" markdown>

| main_art-placeholder | Meaning |
| --- | --- |
| heart_icon | # Place object `heart_icon` inside `main_art-placeholder` |
| @{assets/picture.png}~i5 | # Place a local image file, scaled proportionally to fit inside and centered |
| @sp1[A4]~m5 | # Place frame A4 from spritesheet `sp1`, scaled to cover the placeholder and centered |
| @{wkmc://File:Example.svg/svg#nodeX}~i5 | # Download this SVG from Wikimedia Commons, extract `nodeX`, and place it inside the placeholder |
| id1~7 id2~9 id3~3 | # Place 3 objects using different anchors: top-left, top-right, and bottom-left |
| [id_a id_b id_c].L{3x1 g=2}~i5 | # Arrange 3 objects in a 3x1 local grid with 2 mm gaps, then fit the whole block inside the placeholder |

</div>

## Syntax and Style
Fit-Anchor follows the same DSL conventions as other modules:

- `Module{ key1=value1 key2=value2 ... }` long form.
- The module name can be abbreviated as `F`.
- Every Fit-Anchor parameter has a first-letter alias: `mode/m`, `anchor/a`, `border/b`, `shift/s`, and `clip/c`.
- Mode values can omit the parameter name, for example `.F{inside}` or `.F{i}`.
- Compact shorthand with `~` is available for the most common Fit-Anchor operations.

Long form:

```txt
object_id.Fit{mode=inside anchor=7 border=[-2] shift=[1 0] clip}
object_id.F{m=i a=7 b=[-2] s=[1 0] c}
```

`Fit{...}` and `F{...}` are exactly equivalent. Parameter initials are also exactly equivalent to their full names.

| Parameter | Initial | Purpose |
| --- | --- | --- |
| `mode` | `m` | Scaling behavior. |
| `anchor` | `a` | Alignment point from 1 to 9. |
| `border` | `b` | Adjustment of the area used for scaling. |
| `shift` | `s` | Final positional offset. |
| `clip` | `c` | Clip overflowing content. |

Do not use `f` for mode. `F` abbreviates the **module name**; `m` abbreviates the **mode parameter**.

Compact form:

```txt
object_id~...
```

See [Compact Notation (Fit-Anchor and Transform)](compact-notation.md) for a
visual quick-reference covering token order, defaults, optional parts, and
combined examples.

### Compact Example (Explained)
Example:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| id1~[10%]i7[-50% 0]!^^ |

</div>

Read it left to right:

- `[10%]` -> border (resize context around placeholder),
- `i` -> mode `inside`,
- `7` -> anchor top-left,
- `[-50% 0]` -> shift left by half placeholder width,
- `!` -> clip to the placeholder shape,
- `^^` -> Transform rotate 180 deg.

This compact style is very expressive once you know the token order.

## Fit-Anchor Parameters
The following subsections describe each Fit-Anchor parameter and what it changes in final placement.
Order matters conceptually.

### Mode (`mode=` / `m=`)
Mode defines scaling behavior relative to the usable area. If omitted, `inside` is used.

Equivalent examples:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| object_id.Fit{mode=inside} |
| object_id.F{m=i} |
| object_id.F{inside} |
| object_id.F{i} |
| object_id~i |

</div>

`m` has two related but unambiguous uses inside the block:

- `m=value` is the initial form of the `mode` parameter: `.F{m=inside}` or `.F{m=i}`.
- a bare `m` is the mode code for `max/cover`: `.F{m}`.

The same rule applies to the mode codes `a` and `b`: a bare `.F{a}` or `.F{b}` selects a mode, while `.F{a=7}` and `.F{b=[-2]}` set the `anchor` and `border` parameters.

#### Mode Reference
| **Code** | **Name** | **Description** |
|----|----|----|
| `i` | inside / contain | Scales proportionally to fit entirely within the area (default). |
| `o` | original / none | Keeps original size. |
| `w` | width-fit | Scales proportionally to match area width. |
| `h` | height-fit | Scales proportionally to match area height. |
| `m` | max / cover | Scales proportionally until it completely covers the area, possibly overflowing. |
| `x` | x-stretch | Stretches width to match the area (non-proportional). |
| `y` | y-stretch | Stretches height to match the area (non-proportional). |
| `a` | all-stretch | Scales independently in X/Y to fill the area exactly. |

The parser also reserves `t` (`tile`) and `b` / `?` (`best-fit` / `auto-fit`), but these modes do not yet implement special scaling. Do not use them in production datasets yet.

`c` is **not** a mode code. It is the initial of the `clip` parameter.

### Border (`border=` / `b=`)
Border changes the usable area on which `mode` computes scale.

- positive values expand,
- negative values shrink.

Important behavior:

- `border` modifies placeholder size **only for mode scaling**.
- `anchor`, `shift`, and `clip` still use the **original placeholder** as geometric reference.

In other words:

1. border adjusts the fit area,
2. mode computes object scale in that adjusted area,
3. anchor/shift/clip are evaluated against the original placeholder.

Although `mode` is documented first because it is the primary choice, the placement engine computes the border-adjusted usable area before applying that mode.

Examples:

```txt
border=[-2]
border=[2 3]
border=[2 3 4 5]
object_id~[-2]i
```

Percent and absolute-size forms are supported:

```txt
border=[50%]
border=[125%]
border=[40x60]
border=[50%x20]
border=[23x?]
border=[?x12]
```

Negative `WxH` components can flip orientation:

```txt
border=[100%x-100%]
border=[-100%x100%]
```

### Anchor (`anchor=` / `a=`)
Anchor selects the reference point used to align an object to the placeholder.
Think of it as: "which point of the object goes to which point of the placeholder."

Anchor uses the **original placeholder** (not the border-adjusted fit area).
This makes anchor behavior stable and predictable while `border` only affects scale.

`anchor = 1..9` follows numeric keypad:

7 8 9

4 5 6

1 2 3

Examples:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| object_id.Fit{anchor=7} |
| object_id~7 |

</div>

![Anchor keypad](media/image1.png)

If no explicit anchor is provided, center (`5`) is used as default.

### Shift (`shift=` / `s=`)
Shift offsets final position after anchor/fit.

```txt
object_id.Fit{anchor=7 shift=[-100% -100%]}
object_id~i8[0 %]
object_id~i8[-50% 0]
```

Notes:

- `%` is relative to placeholder size,
- `%` means `100%`,
- `-%` means `-100%`,
- mixed expressions are valid (`shift=[25%+2 0]`).
- shift uses the original placeholder frame as reference.

### Clip (`clip` / `c` / `!`)
Clips outside the original placeholder shape.

In module form, `clip` and `c` are equivalent boolean flags:

```txt
object_id.Fit{mode=max clip}
object_id.F{m=m c}
```

```txt
object_id~!i8[0 %]   # pre-clip, then fit and shift
object_id~i8[0 %]!   # fit and shift, then post-clip
```

Position of `!` matters.

## Priority and Overrides
When multiple Fit-Anchor layers apply, use this precedence:

1. Header defaults/global ops
2. Iterator/global ops
3. Item-local ops

Later layers override earlier ones for conflicting properties.

Example:

<div class="csv-dataset" markdown>

| main_art-8=~[10x10] |
| --- |
| *[:fig(g918)~^^ :fig(g1720) :fig(g1264) :fig(g13590)]~[0%]5 |

</div>

Effective merge:

- start from header border,
- apply iterator-level defaults,
- apply item-level overrides last.


## Forcing a Group BBox

If Inkex measures a group incorrectly, especially when it contains text, add a direct child `<rect>` whose SVG id or Inkscape label starts with `force_bbox`, `force-bbox`, or `forcebbox`. The prefix is case-insensitive. PnPInk uses that rectangle as the group's bbox, even when the rectangle is fully transparent.

`data-bbox` still has higher priority. Only direct child rectangles are inspected, so ordinary groups keep their normal bbox behavior.

## Related Pages

- [Compact Notation (Fit-Anchor and Transform)](compact-notation.md)
- [Transform](transform.md)
- [Source](source.md)
