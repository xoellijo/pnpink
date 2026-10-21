# Compact Notation (Fit-Anchor and Transform)

Compact notation is a short form based on `~`. It combines the most common
[Fit-Anchor](fit-anchor.md) placement operations with basic
[Transform](transform.md) rotation and mirroring in one suffix.

Use it when a dataset cell needs a clear, short placement instruction. Use the
full `.Fit{...}` and `.T{...}` forms when uncommon parameters or maximum
explicitness are more important than brevity.

## The Complete Shape

```txt
OBJECT~[border]modeanchor[shift]!^rotation|mirror
```

A compact visual version is:

```txt
OBJ ~ [b] m 7 [s] ! ^ |
 |     |  | |  |  | | +-- mirror
 |     |  | |  |  | +---- rotation
 |     |  | |  |  +------ clip
 |     |  | |  +--------- shift
 |     |  | +------------ anchor
 |     |  +-------------- mode
 |     +----------------- border
 +----------------------- object or Source
```

`[b]` and `[s]` represent value lists; the letters are not written literally.
For example:

```txt
art~[-2]i7[2 -2]!^15|
```

## Token Reference

| Part | Meaning | Examples | Default | Full reference |
| --- | --- | --- | --- | --- |
| `OBJECT` | SVG object, Source, array, or other placeable value | `art`, `@{card.png}`, `[a b c]` | Required | [Objects and Sources](source.md) |
| `~` | Starts the compact suffix | `art~i5` | — | [DSL nomenclature](nomenclature.md) |
| `[border]` | Changes the usable area before scaling | `[-2]`, `[2 3]`, `[40x60]` | No border | [Border](fit-anchor.md#border-border-b) |
| `mode` | Selects how the object is scaled | `i`, `m`, `o`, `w`, `h`, `x`, `y`, `a` | `i` (inside) | [Mode](fit-anchor.md#mode-mode-m) |
| `anchor` | Aligns the object using keypad positions `1..9` | `7` top-left, `5` center, `3` bottom-right | `5` | [Anchor](fit-anchor.md#anchor-anchor-a) |
| `[shift]` | Moves the result after fitting and anchoring | `[2 -2]`, `[10% 0]` | `[0 0]` | [Shift](fit-anchor.md#shift-shift-s) |
| `!` | Clips overflow to the placeholder | `!` | Off | [Clip](fit-anchor.md#clip-clip-c) |
| `^rotation` | Rotates the placed object | `^`, `^^`, `^^^`, `^-30`, `^15` | `0°` | [Rotate](transform.md#rotate-and-mirror) |
| `|mirror` | Mirrors the placed object | `|` horizontal, `||` vertical | Off | [Mirror](transform.md#rotate-and-mirror) |

Every operation group shown after `~` is optional. In practice, include at
least the operation you want to make explicit:

```txt
art~7       # inside mode, top-left anchor
art~m       # cover mode, centered
art~[-2]    # 2-unit inner border, inside and centered
art~^       # inside and centered, then rotate 90 degrees
```

## Order and Defaults

The canonical order is:

1. optional `[border]`,
2. optional `mode` and `anchor`,
3. optional `[shift]`,
4. optional `!` clip,
5. optional `^rotation`,
6. optional `|mirror`.

Order matters because the same list syntax is used for border and shift:

- a list **before** mode/anchor is a border;
- a two-value list **after** mode/anchor is a shift;
- when that distinction would be unclear, use the full `.Fit{border=... shift=...}` form.

Mode and anchor can be written together (`i7`, `m5`) or separately. Omitting
one keeps its default:

```txt
art~i7      # inside + top-left
art~7       # same: omitted mode defaults to inside
art~m       # cover + omitted anchor defaults to center
```

The practical defaults are:

- mode: `i` (`inside` / contain),
- anchor: `5` (center),
- border and shift: none,
- clip, rotation, and mirror: off.

## Mode and Anchor

Common mode codes are:

| Code | Result |
| --- | --- |
| `i` | Fit entirely inside while preserving proportions. |
| `m` | Cover the complete area while preserving proportions. |
| `o` | Keep original size. |
| `w` / `h` | Fit proportionally to width / height. |
| `x` / `y` | Stretch only horizontally / vertically. |
| `a` | Stretch independently in both axes. |

The anchor follows the numeric keypad:

```txt
7 8 9
4 5 6
1 2 3
```

See the complete [Mode](fit-anchor.md#mode-mode-m) and
[Anchor](fit-anchor.md#anchor-anchor-a) references for details and reserved
modes.

## Clip Position

The normal form places `!` after fit and shift, producing a final/post clip:

```txt
art~m5[10% 0]!
```

To request a pre-clip, place `!` before the first fit token:

```txt
art~!m5[10% 0]
```

Prefer the normal post-clip unless you specifically need clipping before the
remaining placement operations. See [Clip](fit-anchor.md#clip-clip-c).

## Rotation and Mirror

Compact rotation and mirror belong to Transform even though they share the same
suffix:

```txt
token~i5^       # rotate 90 degrees
token~i5^^      # rotate 180 degrees
token~i5^^^     # rotate 270 degrees
token~i5^-30    # rotate -30 degrees
token~i5|       # horizontal mirror
token~i5||      # vertical mirror
token~i5^90|    # rotate, then mirror
```

For these simple operators, PnPInk also accepts forms without `~`, such as
`token^15`, `token!`, and `token|`. The `~` form is recommended when several
operations are combined because their order remains visible.

## Worked Examples

### Fit Inside and Center

```txt
art~i5
```

Scale `art` proportionally until it fits completely inside the placeholder,
then center it. Both values are written here to make the defaults explicit.

### Cover and Crop

```txt
photo~m5!
```

Scale `photo` proportionally until it covers the placeholder, center it, and
clip the overflow. This is the usual full-bleed image pattern.

### Corner Icon with Margin and Offset

```txt
icon~[-2]i7[2 -2]
```

Shrink the usable fit area by `2`, fit the icon inside it, anchor it at the
top-left (`7`), and finally shift it right by `2` and up by `2`.

### Original Size, Rotation, and Mirror

```txt
token~o9^|
```

Keep the token at its original size, anchor it at the top-right (`9`), rotate it
90 degrees, and mirror it horizontally.

### Complete Combined Form

```txt
art~[110%x110%]m5[0 -3%]!^15||
```

Use a fit area sized to `110%` in both axes, cover and center it, move it upward
by `3%`, clip the final result, rotate it 15 degrees, and mirror it vertically.

### Source and Array Examples

Compact notation is not limited to document IDs:

```txt
@{assets/card.png}~m5!
[coin star banner].L{3x1 g=2}~i5
```

The first expression places a file Source. The second lays out three objects as
one group and then fits that complete group. See [Source](source.md) and
[Layout](layout.md) for their own syntax.

## When to Use the Full Form

Use compact notation for familiar placement patterns that remain easy to read.
Use full modules when the expression becomes ambiguous or needs parameters not
represented here:

```txt
art.Fit{border=[-2] mode=inside anchor=7 shift=[2 -2] clip}
   .T{rotate=15 mirror=h opacity=80%}
```

The compact and full forms use the same Fit-Anchor and Transform behavior; they
are two ways to express the same operations.
