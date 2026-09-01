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
- Short aliases using the first letter are allowed, for example `.F{b=2}`.
- Default keys can omit the key name.
- Compact shorthand with `~` is available for the most common Fit-Anchor operations.

Long form:

```txt
object_id.Fit{ border fitmode anchor shift clip }
object_id.F{ b= f= a= s= c }          ## first-letter aliases
```

Compact form:

```txt
object_id~...
```

### Compact Example (Explained)
Example:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| id1~[10%]i7^^[-50% 0]! |

</div>

Read it left to right:

- `[10%]` -> border (resize context around placeholder),
- `i` -> fit mode `inside`,
- `7` -> anchor top-left,
- `^^` -> Transform rotate 180 deg,
- `[-50% 0]` -> shift left by half placeholder width,
- `!` -> clip to the placeholder shape.

This compact style is very expressive once you know the token order.

## Fit-Anchor Parameters
The following subsections describe each Fit-Anchor parameter and what it changes in final placement.
Order matters conceptually.

### Border (`b=`)
Border changes the usable fit area around the placeholder.

- positive values expand,
- negative values shrink.

Important behavior:

- `border` modifies placeholder size **only for Fit Mode scaling**.
- `anchor`, `shift`, and `clip` still use the **original placeholder** as geometric reference.

In other words:

1. border adjusts the fit area,
2. fit mode computes object scale in that adjusted area,
3. anchor/shift/clip are evaluated against the original placeholder.

This is why defining `border` first is conceptually important: it controls object size before final positioning.

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

### Fit Mode
Fit mode defines scaling behavior relative to the (possibly border-adjusted) fit area.
If omitted, `inside` is the practical default.

Examples:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| object_id.Fit{fitmode=inside} |
| object_id~i |

</div>

### Fit Mode Reference
| **Code** | **Name** | **Description** |
|----|----|----|
| i | inside / contain | Scales proportionally to fit entirely within the rect (default). |
| o | original / none | Keeps original size. |
| w / h | width-fit / height-fit | Scales proportionally to match rect width / height. |
| m / c | max / cover | Scales proportionally until it completely covers the placeholder, possibly overflowing. |
| x / y | x-stretch / y-stretch | Stretches width / height to match rect (non-proportional). |
| a | all-stretch | Scales independently in X/Y to fill rect exactly. |
| t | tile | Tiles the object as a pattern within the rect (not implemented). |
| ? / b | auto-fit / best-fit | Smart mode that mixes `m`, `a`, and clipping for balance. |

### Anchor
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

### Clip (`c` / `!`)
Clips outside the original placeholder shape.

```txt
object_id~i8![0 %]   # pre-clip then shift
object_id~i8[0 %]!   # shift then post-clip
```

Position of `!` matters.

## Compact Token Order (Reference)
When using shorthand `~`, tokens are parsed in this order:

1. Optional border list (`[t r b l]`, `[x]`, `[x y]`)
2. Fit mode + anchor (`i7`, `m5`, `a9`, etc.)
3. Optional shift list (`[dx dy]`)
4. Optional clip (`!`)
5. Optional Transform rotation (`^deg`, `^^`, `^^^`)
6. Optional Transform mirror (`|`, `||`)

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

## Practical Examples
This section intentionally groups compact real-life patterns.

## Compact Notation (Clarifications)
This section consolidates the compact syntax details that usually cause confusion.

### With `~` and without `~`
`~` is a compact suffix for the main Fit and Transform operations.
It is not an exact shorthand for `Fit{}` only: `^` and `|` belong to `Transform`.

For simple rotate/mirror/clip operations, `~` can be omitted:

- `id~^15` and `id^15` are equivalent.
- `id~!` and `id!` are equivalent.
- `id~|` and `id|` are equivalent.
- `id~||` and `id||` are equivalent.

Use the `~` form when you want the full compact chain in one expression (`border + fit/anchor + shift + clip + transform rotate/mirror`).

### Combined compact operations
You can combine these operators in one token, for example:

```txt
id~i5^^!
id^90|
id~m7[0 5%]!
```

### Parsing and precedence reminder
- The compact parser still applies the same Fit-Anchor merge precedence: header defaults -> iterator/global ops -> item-local ops.
- For conflicting properties, the last layer overrides previous ones.

### Example 1: Simple inside-center

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| art_id~i5 |

</div>

Places object inside placeholder, centered.

### Example 2: Cover and clip

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| art_id~m5! |

</div>

Covers placeholder area, then clips overflow to placeholder shape.

### Example 3: Corner icon with offset

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| icon_id~i7[2 -2] |

</div>

Inside fit, top-left anchor, then fine offset.

### Example 4: Multiple objects in one cell

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| id1~7 id2~9 id3~3 |

</div>

Places three objects in one placeholder flow, each with its own anchor.

### Example 5: Array group with local layout

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| [id1 id2 id3].L{3x1 g=2}~i5 |

</div>

Builds an array object, lays it out locally, then applies Fit-Anchor as one grouped target.

## Forcing a Group BBox

If Inkex measures a group incorrectly, especially when it contains text, add a direct child `<rect>` whose SVG id or Inkscape label starts with `force_bbox`, `force-bbox`, or `forcebbox`. The prefix is case-insensitive. PnPInk uses that rectangle as the group's bbox, even when the rectangle is fully transparent.

`data-bbox` still has higher priority. Only direct child rectangles are inspected, so ordinary groups keep their normal bbox behavior.

## Related Pages

- [Transform](transform.md)
- [Source](source.md)
