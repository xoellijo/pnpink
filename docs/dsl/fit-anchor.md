# Fit-Anchor
Fit-Anchor is one of the most important modules in PnPInk.
It is the placement engine that lets you position objects relative to placeholders without manual coordinates and without hardcoding object/template sizes.

In practice, Fit-Anchor lets you place one or many objects on a placeholder and then size, align, shift and clip them relative to that placeholder.

Fit-Anchor is about the relationship between an object and its placeholder.
For visual changes applied to the object itself after placement, use [Transform](/dsl/transform/).

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

Representative examples:

```txt
main_art-8
heart_icon
@{assets/picture.png}~i5
@sp1[14]~m5
@{wkmc://File:Example.svg/svg#nodeX}~i5
id1~7 id2~9 id3~3
[id_a id_b id_c].L{3x1 g=2}~i5
```

## Syntax and Style
Fit-Anchor follows the same DSL conventions as other modules:

- `Module{ key=value ... }` long form,
- short aliases (`a`, `b`, `s`, etc.),
- default parameters can be omitted,
- compact shorthand with `~` is available and heavily used in real datasets.

Long form:

```txt
object_id.Fit{ border fitmode anchor shift clip rotate mirror }
```

Compact form:

```txt
object_id~...
```

### Compact Example (Explained)
Example:

```txt
id1~[10%]i7^^[-50% 0]!
```

Read it left to right:

- `[10%]` -> border (resize context around placeholder),
- `i` -> fit mode `inside`,
- `7` -> anchor top-left,
- `^^` -> rotate 180 deg,
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

```txt
object_id.Fit{fitmode=inside}
object_id~i
```

### Fit Mode Reference
| **Code** | **Name** | **Description** |
|----|----|----|
| i | inside / contain | Scales proportionally to fit entirely within the rect (default). |
| o | original / none | Keeps original size. |
| w | width-fit | Scales proportionally to match rect width. |
| h | height-fit | Scales proportionally to match rect height. |
| m | max / cover | Scales proportionally until it covers the rect, possibly overflowing. |
| x | x-stretch | Stretches width to match rect (non-proportional). |
| y | y-stretch | Stretches height to match rect (non-proportional). |
| a | all-stretch | Scales independently in X/Y to fill rect exactly. |
| t | tile | Tiles the object as a pattern within the rect. |
| b | best-fit | Smart mode that mixes m, a, and clipping for balance. |
| ? | auto-fit | Alias of `b` (best-fit). |

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

```txt
object_id.Fit{anchor=7}
object_id~7
```

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

### Rotate (`r=` / `^`)
Rotates the target object.

```txt
object_id.Fit{rotate=-42.4}
object_id~^^^
object_id~^-45i7
```

### Mirror (`m=` / `|`)
Mirrors the target.

- `|` horizontal,
- `||` vertical.

```txt
object_id.Fit{mirror=v}
object_id~||
```

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
5. Optional rotation (`^deg`, `^^`, `^^^`)
6. Optional mirror (`|`, `||`)

## Priority and Overrides
When multiple Fit-Anchor layers apply, use this precedence:

1. Header defaults/global ops
2. Iterator/global ops
3. Item-local ops

Later layers override earlier ones for conflicting properties.

Example:

```txt
Header:
main_art-8=~[10x10]

Cell:
*[:fig(g918)~^^ :fig(g1720) :fig(g1264) :fig(g13590)]~[0%]5
```

Effective merge:

- start from header border,
- apply iterator-level defaults,
- apply item-level overrides last.

## Practical Examples
This section intentionally groups compact real-life patterns.

## Compact Notation (Clarifications)
This section consolidates the compact syntax details that usually cause confusion.

### With `~` and without `~`
For simple rotate/mirror/clip operations, `~` can be omitted:

- `id~^15` and `id^15` are equivalent.
- `id~!` and `id!` are equivalent.
- `id~|` and `id|` are equivalent.
- `id~||` and `id||` are equivalent.

Use the `~` form when you want the full compact chain in one expression (`border + fit/anchor + shift + clip + rotate + mirror`).

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

```txt
art_id~i5
```

Places object inside placeholder, centered.

### Example 2: Cover and clip

```txt
art_id~m5!
```

Covers placeholder area, then clips overflow to placeholder shape.

### Example 3: Corner icon with offset

```txt
icon_id~i7[2 -2]
```

Inside fit, top-left anchor, then fine offset.

### Example 4: Multiple objects in one cell

```txt
id1~7 id2~9 id3~3
```

Places three objects in one placeholder flow, each with its own anchor.

### Example 5: Array group with local layout

```txt
[id1 id2 id3].L{3x1 g=2}~i5
```

Builds an array object, lays it out locally, then applies Fit-Anchor as one grouped target.

## Related Pages

- [Transform](/dsl/transform/)
- [Source](/dsl/source/)
