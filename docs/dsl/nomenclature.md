# DSL Nomenclature
This section defines rules that apply to the whole DSL.

## Global Tokens
- `^` always means rotation (degrees).
- `|` and `||` always mean mirror (horizontal, vertical).
- `!` always means clip (see Fit for stage rules).
- `[]` always means list or list-like values.
- `{}` always means a module block or a page/layout/marks block.

## IDs and Targets
An SVG `id` is the primary selector.
To inspect or edit IDs in Inkscape:

- `Object > Objects...` (Shift+Ctrl+O): first step for hierarchy, groups/layers, and Z-order.
- `Object > XML Editor` (Shift+Ctrl+X): low-level XML/SVG attribute editing.

IDs must be unique and follow XML rules (letters first, no spaces).

## Lists, Ranges, and Multipliers
Lists are space-separated:

```txt
[ID1 ID2 ID3]
```

Gaps and multipliers are supported:

```txt
[ID1 - - - ID4]  -> same as [ID1 3- ID4]
3*ID             -> [ID ID ID]
```

Ranges and selectors:

```txt
target[2]      -> item 2
target[2..4]   -> items 2, 3, 4
target[*]      -> all items
```

## Units
Most numeric tokens accept:

- plain numbers (interpreted in the current default unit).
- mm, px, %, and expressions like `-25%+2`.

Percentages are evaluated against a base size. If a base size is required
but not available, the engine logs an error and falls back.

## Module Blocks
Modules are written as:

```txt
ID`.Module{ key=value key2=value2 }`
```

Most modules have:

- a default parameter (can be omitted),
- a one-letter shorthand for common parameters,
- a short form (e.g. `L{}` instead of `Layout{}`).

## Abbreviations and Defaults
Every keyword can be shortened to its first letter when supported:

```txt
Layout{} -> L{}
inside -> i
Page{pagesize=A4 border=[-2]} -> P{A4 b=[-2]}
```

Most modules define a default property, so it can be omitted in compact form.

When a required element is not specified, the last defined value is reused.
Example: if page size is omitted in a following row, the previous page size remains active.

For exact page-state behavior, see [Page](page.md).

For Fit shorthand token order, see [Fit and Anchor](fit-anchor.md).
