# Fit and Anchor
The **Fit** module (`Fit{}` or shorthand `F{}` or `~`) positions one element relative to another, usually a rect acting as a **placeholder**.

Example:

```txt
ID`.Fit{ inside anchor=7 }`
```

This places the element defined by ID inside its anchor rect, anchored at position 7 (top-left).

## Syntax
```txt
ID`.Fit{ border fitmode anchor translate clip rotate mirror }`
```

List elements are space-separated.

## Shorthand Token Order
The compact Fit syntax is parsed in this order:

1. Optional border list: `[t r b l]` or `[x]` or `[x y]`
2. Mode + anchor (for example `i7`, `m5`, `a9`)
3. Optional translate list: `[dx dy]`
4. Optional clip `!` (stage depends on position)
5. Optional rotation `^deg` (or `^^` / `^^^`)
6. Optional mirror `|` or `||`

Examples:

```txt
ID~[2]i7
ID~i7[3 -2]^45|
```

## Anchor
`anchor = n`

`n` ranges from 1-9 following the numeric keypad layout:

7 8 9

4 5 6

1 2 3

Examples:

```txt
ID`.Fit{anchor=7}` -> top-left
ID`.F{a=7}` -> shorthand
ID~7 -> minimal form
```

![Anchor keypad](media/image1.png)

If not specified, the default anchor is `~5` (center).

## Fit Mode (*f=)
Defines how the target scales relative to its anchor rect.
It is the default parameter, so `fitmode=` can be omitted.

Examples:

```txt
ID`.Fit{fitmode=inside}`
ID`.F{i}`
ID~i
```

If no fit mode is given, `inside` (scaling to fit inside) is assumed.

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
| t | tile | Tiles the element as a pattern within the rect. |
| b | best-fit | Smart mode that mixes m, a, and clipping for balance. |

## Border (b=)
Defines padding or margin around the anchor rect.
Positive expands (padding), negative shrinks (margin).

Examples:

```txt
border=[-2] -> 2 mm margin on all sides
border=[2 3] -> 2 mm vertical, 3 mm horizontal
border=[2 3 4] -> top=2, bottom=3, sides=4
border=[2 3 4 5] -> top, right, bottom, left
```

Short form:

```txt
ID~[-2]i -> fits inside with 2 mm margin
```

Percentages are allowed:

```txt
border=[50%] -> scales up the rect by half before fitting
border=[5%+1 2 -4 3%-2]
```

### Absolute size mode (WxH)
If border is a single value containing `x` (no spaces), it defines an absolute target size centered on the original rect:

```txt
border=[40x60] -> rect becomes 40x60 mm (centered)
border=[50%x20] -> width = 50% of original, height = 20 mm
```

Negative values in `WxH` flip (mirror) the content:

```txt
border=[100%x-100%] -> vertical flip
border=[-100%x100%] -> horizontal flip
```

## Translate (translate= / t=)
Offsets the final position `[x y]` relative to the anchor rect.

```txt
ID`.Fit{anchor=7 translate=[-100% -100%]}`
```

This places the element outside the rect, touching its top-left corner.

Notes:

- Prefer `translate=[dx dy]` to avoid ambiguity.
- `t=` is accepted but also used as a flag for `tile` mode in long form.

## Clip (c or !)
Clips everything outside the **original** anchor rect.

Short forms:

- `c` in the long form.
- `!` in the shorthand form.

Clip stage in shorthand depends on position:

- `!` before a shift means pre-clip.
- `!` after a shift means post-clip.

## Rotate (r= or ^)
Rotates the target element (default 90 deg for bare `^`).

Examples:

```txt
ID`.Fit{rotate=-42.4}`
ID~^^^ -> rotates -90 deg before fitting
ID~^-45i7 -> rotates -45 deg, fits inside, anchored top-left
```

## Mirror (m= or |)
Mirrors the target.

Default is horizontal (across the vertical axis).

Shortcuts:

- `|` = horizontal
- `||` = vertical

Examples:

```txt
ID`.Fit{mirror=v}`
ID~||
```
