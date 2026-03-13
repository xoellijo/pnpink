# Gradients (Experimental)

!!! warning
    This feature is currently **in development**.
    Syntax and behavior can still change in future versions.

## Overview
PnPInk can create SVG `linearGradient` definitions directly from dataset comment directives.
These gradients are generated in `<defs>` and can then be used from any SVG style, for example:

```css
fill: url(#gradientX);
stroke: url(#gradientX);
```

## Where to define gradients
Gradient definitions are read from dataset comment lines (same global/local comment mechanism used by snippets and spritesheets).

Typical location:

- top comment block of your dataset file,
- before data rows,
- usually with `#` at the start of the first cell.

Example:

```txt
# gradientX=G{b88326ff^25 [-25 12 5] [#BA8726FF 35 20 10]}
```

## How to apply gradients
Once a gradient ID exists, apply it in your SVG element style:

```xml
style="fill:url(#gradientX);"
```

Or as stroke:

```xml
style="stroke:url(#gradientX);"
```

PnPInk does not require a special dataset field for this part.
You use normal SVG styling with the generated gradient ID.

## Syntax
Two forms are supported.

### Short form (`G{...}`)

```txt
# gradientX=G{basecolor^angle [stop1] [stop2] ...}
```

Example:

```txt
# gradientX=G{b88326ff^25 [-25 12 5] [#BA8726FF 35 20 10]}
```

Meaning:

- `basecolor`:
  base color of the gradient (`#RRGGBB` or `#RRGGBBAA`, `#` optional).
- `^angle`:
  linear gradient angle in degrees (optional, default `0`).
- each stop block:
  `[...]` definition for local highlight/shadow behavior.

### Long form (`Gradient{...}`)

```txt
# gradientX=Gradient{basecolor=b88326ff rotate=25 stops=[[-25 12 5] [55 35 20 10]]}
```

Parameters:

- `basecolor` (or `base`/`color`): required.
- `rotate` (or `angle`/`r`): optional, default `0`.
- `stops=[ ... ]`: required in long form.

## Stop definition
Each stop can be numeric-light or explicit-color.

### Numeric light stop

```txt
[light pos a b]
```

- `light`: `-100..100`
  - positive -> lighter (towards white),
  - negative -> darker (towards black).
- `pos`: position in `%`.
- `a`: left width in `%`.
- `b`: right width in `%` (optional, if missing then `b=a`).

Example:

```txt
[-25 12 5]
```

This creates a darker zone centered at `12%`, with left/right width `5%`.

### Explicit color stop

```txt
[color pos a b]
```

- `color`: `#RRGGBB` or `#RRGGBBAA`.
- `pos`, `a`, `b`: same meaning as above.

Example:

```txt
[#BA8726FF 35 20 10]
```

This creates a custom-color peak at `35%`, with asymmetric widths (`20%` left, `10%` right).

## Behavioral details
Current implementation rules:

1. Base color is present at `0%` and `100%`.
2. Each stop adds three internal points:
   - `(pos-a) -> base color`
   - `pos -> peak color`
   - `(pos+b) -> base color`
3. Offsets are clamped to `0..100%`.
4. If multiple points collapse to the same offset, last generated value wins.
5. `light` values are converted by mixing base color with white/black.

## End-to-end example

Dataset comments:

```txt
# gradientGold=G{b88326ff^25 [-25 12 5] [55 35 20 10]}
```

SVG object:

```xml
<rect id="card_bg" style="fill:url(#gradientGold);stroke:#000;stroke-width:0.3" />
```

Result:

- `gradientGold` is created in `<defs>`,
- object fill resolves to the generated linear gradient.

## Current limitations
- Only `linearGradient` is generated.
- `radialGradient` and mesh gradients are not part of this feature yet.
- Because this is experimental, parser details and interpolation behavior can evolve.
