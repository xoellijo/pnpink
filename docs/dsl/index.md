# Core Syntax
This page defines the shared grammar used across all DSL modules.
For dataset grammar (markers, headers, comments), see [Dataset Reference](../dataset.md).

## Targets
Before using modules, you need a target.
A target is the element or element set that the module will operate on.

A target can be:

- one SVG ID: `title`,
- a list/group: `[id1 id2 id3]`,
- a source expression: `@{assets/icon.svg}`.

Indexed targets are supported:

```txt
target[2]
target[2..4]
target[*]
target[2][3]
```

## Module Form
Modules express transformation intent in a compact way.
Use long form when clarity matters, and short form when writing dense production rows.

Long form:

```txt
target.Module{ key=value key2=value2 }
```

Short form:

```txt
target.M{ ... }    # module short name
target~...         # Fit shorthand
```

Common modules:

- `Fit{}` or `~...`
- `Layout{}` or `L{}`
- `Page{}`
- `Source{}` or `@{}`
- `Marks{}` or `M{}`

## Lists and values
Lists are heavily used in PnPInk for target groups, selectors, borders, gaps, and offsets.

All lists are space-separated:

```txt
[a b c]
```

Supported patterns:

```txt
[id1 3- id2]   # holes/gaps in list
3*id           # multiplier
```

No commas are allowed inside DSL lists.

## Defaults and abbreviations
This is essential for readable compact DSL.
Most practical sheets rely on these abbreviations.

- Most modules have a default parameter.
- Most properties have short aliases (`a`, `b`, `g`, `o`, etc.).
- The parser keeps strict semantics but tolerates equivalent aliases when implemented.

## Quoting
Use quoting whenever a token contains spaces, otherwise parsing splits it into multiple tokens.

If a value contains spaces, quote it with `'...'` or `"..."`.

## State behavior
PnPInk is stateful across rows for page/layout modules.
Understanding this behavior avoids accidental resets and repeated boilerplate.

Page and layout state are reused when not redefined.
This is why a dataset can declare `Page{}` once, then only adjust rows where needed.
