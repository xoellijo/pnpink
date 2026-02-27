# Iterators and Copies
This chapter explains the difference between array placement and row iteration, and how both interact with card quantity in column A.

## Why this matters
At first sight, these two expressions look similar:

```txt
[id1 id2 ... idn]
*[id1 id2 ... idn]
```

They are not equivalent.
The first builds one composite placement inside one generated card.
The second expands the dataset row into multiple generated cards, one per iterator value.

## Array Without Iterator (`[ ... ]`)
When a non-text field uses:

```txt
[id1 id2 ... idn]
```

PnPInk treats it as an array/group target for that field in the current card instance.
All listed items are processed in the same row instance.

This is useful when you want several IDs resolved together in one card, and optionally apply local array layout/fit behavior.

## Iterator (`*...`)
When a cell starts with `*`, it becomes an iterator expression.

```txt
*[id1 id2 ... idn]
```

Result:

- the row is expanded into multiple internal instances,
- each generated card gets one iterator value at that cell,
- by default (without explicit copies), generated cards count equals iterator length.

Conceptually: one different card per value.

## Multi-Level Iteration (`*`, `**`, `***`)
PnPInk supports multiple iterator levels using leading stars.

```txt
*[A B C]
**[1 2]
```

This produces a nested expansion (cartesian-style): every level-1 value is combined with every level-2 value.
You can add more levels (`***`, etc.) when needed.

## Supported Iterator Expressions
Current parser supports iterator payloads such as:

- bracket list/range: `*[1 2 3]`, `*[1..10]`, `*[A..F]`,
- virtual sources with selectors,
- filesystem glob through `*@{...}`,
- spritesheet wildcard alias: `*@alias[*]`.

If an iterator resolves to zero values, that row yields zero generated instances.

## Copies in Column A (Card Quantity)
Column A can declare explicit quantity (copies) for a row.
With iterators, behavior is:

1. No explicit copies in column A:
   Iterator count defines card count.
2. Explicit copies in column A and copies > iterator count:
   Iterator values wrap around.
3. Explicit copies in column A and copies < iterator count:
   Iterator sequence is truncated.

This gives predictable control when you need either:

- one card per iterator value,
- or a fixed number of cards regardless of iterator length.

## Examples
### One card with grouped IDs
```txt
art = [icon_fire icon_air icon_earth]
```

Interpretation: one row instance, one generated card, grouped placement in that card.

### One card per ID
```txt
art = *[icon_fire icon_air icon_earth]
```

Interpretation: three generated cards from that row (unless column A copies overrides).

### Iterator + explicit copies
```txt
Column A: 5
art = *[A B C]
```

Interpretation: 5 cards -> `A, B, C, A, B` (wrap).

```txt
Column A: 2
art = *[A B C]
```

Interpretation: 2 cards -> `A, B` (truncate).
