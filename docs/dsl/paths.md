# Paths

`Paths` generates helper paths directly on top of a placed shape.

Unlike `Marks`, which are defined globally in the first column and follow the page layout, `Paths` are attached to individual cell content. They are useful when each tile, hex, or placed object needs its own internal guides.

## Typical Use

Use `Paths` when you want lines such as:

- a single hex edge
- a line from the center to one side
- a line between two vertices
- a curved connection between two sides
- a bridge from one hex center to the neighboring hex center through a shared side

This is especially useful for hex maps, hextiles, movement guides, borders, and local overlays.

## Syntax

`Paths` is attached to the object token itself:

```txt
tile_id.P{path_style [a b]}
tile_id.P{path_style [5a 79]}
tile_id.P{path_style [ab cd]}
.P{path_style [a b d]}
```

You can define several styled groups in the same block:

```txt
tile_id.P{path_main [a c e] path_aux [5a 5c]}
tile_id.P{t=path_main [ab] t=path_aux [5A5]}
```

Each pair is:

- a style id
- followed by a token list in `[...]`

The style id may reference:

- one path
- or a group of paths, used as a style stack in the same z-order

## Where It Applies

`Paths` belongs to the cell content, not to the page layout header.

You can attach it explicitly to a target:

```txt
tile_id.P{path_style [ab]}
```

or use it directly on the current placed target:

```txt
.P{path_style [ab]}
```

So this is the right mental model:

- `Marks` -> page/layout-level cutting or registration guides
- `Paths` -> per-cell geometry drawn on a placed target

## Hex Nomenclature

For hex shapes, PnPInk uses:

- sides: `a b c d e f`
- vertices: `8 9 3 2 1 7`
- center: `5`

The vertices follow the same keypad-style convention already used elsewhere in the DSL.

## Sides

Each lowercase letter refers to one hex side:

```txt
[a]
[b]
[c]
```

These draw the corresponding edge itself.

## Center to Side

`5a`, `5b`, `5c` ... draw a straight line from the center of the hex to the midpoint of that side.

Examples:

```txt
[5a]
[5c 5f]
```

## Vertex to Vertex

Two keypad numbers draw a straight segment between vertices.

Examples:

```txt
[79]
[93]
[12]
```

## Side to Side

Two lowercase side letters draw a connection between side midpoints.

Examples:

```txt
[ab]
[ac]
[ad]
```

Depending on the relative position, this becomes:

- a straight line for opposite sides
- or a curved connection for non-opposite sides

## Center to Neighboring Hex

An uppercase side letter in the form `5A5`, `5B5`, ... means:

- start at the center of this hex
- go through the midpoint of that side
- continue to the center of the neighboring hex on that side

Examples:

```txt
[5A5]
[5C5 5F5]
```

This is useful for:

- adjacency guides
- movement links
- map connectivity

## Style Reuse

The path geometry comes from the tokens, but the visual appearance comes from the referenced style element.

So a common pattern is:

```txt
tile_id.P{path_thin [a b c] path_bold [5A5]}
```

This lets you draw different local guides with different strokes while keeping the syntax compact.

If the style id points to a group, PnPInk generates one path per child path in that group and preserves their stacking order. This is useful for multi-stroke styles such as railways, roads, or layered local guides.

## Related Pages

- [Hexes](/dsl/hexes/)
- [Layout](/dsl/layout/)
- [Marks](/dsl/marks/)
