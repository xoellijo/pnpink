# Hexes

PnPInk includes dedicated support for hex-based layouts.

This is useful for two common workflows:

- `hexgrid` for overlays, maps, and boards
- `hextiles` for printable hex tiles that must align edge to edge

Hex workflows often use two different line systems:

- `Marks` for global page/layout guides
- `Paths` for local lines attached to individual placed hexes

## Syntax

Hex layouts are enabled from `Layout` using the shape field:

```txt
{A4}.L{p=6x5 s=hexgrid}
{A4}.L{p=6x5 s=hextiles}
```

You can combine them with normal layout options such as `p=`, `g=` and `o=`.

## Hexgrid

`s=hexgrid` creates a staggered hex-style layout without changing the size of your source object.

Typical uses:

- map overlays
- area-control boards
- hex coordinate guides
- transparent helper layers placed over another page

PnPInk automatically applies the stagger needed for a practical hex arrangement, so you do not have to calculate the offsets by hand.

Example:

```txt
{A3^ @2}.L{s=hexgrid} 300
```

## Hextiles

`s=hextiles` is meant for physical tiles.

Use it when each placed item is a real hex tile and adjacent tiles should share the same geometric structure page-wide.

Typical uses:

- modular terrain
- map tiles
- printable counters or region tiles

Compared with `hexgrid`, `hextiles` is oriented to production: neighboring tiles line up as a tile field, and marks can follow the real hex edges.

Example:

```txt
{A4}.L{p=5x6 s=hextiles}
```

## Cut Lines In and Out of the Shape

When hex tiles are intended for printing and cutting, the most useful companion is `Marks{}`.

Marks can draw line segments on both sides of the tile edge:

- an **outer** segment, outside the tile
- an **inner** segment, inside the tile

This is controlled with `len=`.

```txt
.M{len=3}
.M{len=[3 2]}
```

Meaning:

- `len=3` means an outer segment of `3` and no inner segment
- `len=[3 2]` means outer `3`, inner `2`

For hextiles, these lines follow the real hex edges instead of behaving like simple rectangular crop marks.

This makes it easier to produce:

- shared cutting guides
- edge-aligned tile sheets
- visible internal guide lines when needed

You can also combine this with distance and border adjustments:

```txt
{A4}.L{p=5x6 s=hextiles}.M{len=[3 2] d=2 b=0}
```

In practice:

- `d=` controls how far the outer line starts from the edge
- `b=` shifts the line relative to the edge
- `len=[out in]` controls how much line appears outside and inside the shape

## Marks vs Paths

These two modules solve different problems.

### Marks

`Marks` is defined globally in the first column together with `Page{}` and `Layout{}`.

It follows the placed slots of the page and is mainly intended for cutting and registration.

Example:

```txt
{A4}.L{p=5x6 s=hextiles}.M{len=[3 2] d=2}
```

### Paths

`Paths` is attached to the content of a specific cell.

It draws local geometry on that placed hex: edges, center lines, vertex connections, or links to neighboring hexes.

Example:

```txt
tile_id.P{path_style [a 5c 79]}
```

See [Paths](/dsl/paths/) for the complete token system.

## Recommended Usage

- Use `hexgrid` when the hex pattern is mainly a placement system.
- Use `hextiles` when the hex shape itself is part of the printable result.

## Related Pages

- [Layout](/dsl/layout/)
- [Marks](/dsl/marks/)
- [Paths](/dsl/paths/)
