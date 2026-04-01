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

Wildcard IDs are supported inside arrays:

```txt
[main_icon-*]
```

This expands to all matching IDs and keeps array/list semantics (single card instance).

Array repetition shorthand is also supported:

```txt
[3*:Ic(potato)]~i2
```

Equivalent to writing the same token three times in the list.

Grouped repetition is also supported with parentheses:

```txt
[id1 - 3*(id2 3- 3*id3) 4- id4]
```

Parentheses are grouping operators inside list/array bodies, so you can repeat a block as one unit.

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

### Parentheses inside iterator lists
Inside `*[ ... ]`, parentheses define one grouped iterator item (multivalue in the same generated card instance):

```txt
*[id1 id2 id3]
```

This iterates 3 scalar items (`id1`, then `id2`, then `id3`).

```txt
*[id1 (id2 id3)]
```

This iterates 2 items:

- first iteration: `id1`
- second iteration: `id2 id3` (multivalue group in the same card)

So `*[id1 id2 id3]` and `*[id1 (id2 id3)]` are intentionally different.

Group repetition also works:

```txt
*[id1 2*(id2 id3)]
```

Equivalent iterator sequence: `id1`, `(id2 id3)`, `(id2 id3)`.

Wildcard IDs are also supported in scalar/multivalue cells:

```txt
main_icon-*
```

Outside `[ ... ]`, wildcard expansion behaves as multivalue (same card instance, token sequence order).

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

## Row Sequencing from Column A
Column A can control the final sequence of generated cards for a row.
This is broader than just "number of cards": it can skip execution, set a fixed count,
filter iterator positions, reorder them, and insert empty slots.

Think of the pipeline like this:

1. Expand row iterators (`*[...]`, `**[...]`, source iterators, etc.).
2. Apply any selector written in the final column-A `[...]`.
3. Apply explicit copies if present.
4. Insert holes after the selected/generated sequence positions.

### Default behavior
If column A does not specify copies:

- without iterators: the row generates `1` card
- with iterators: the row generates the full iterator length

Examples:

```txt
Column A: <empty>
art = icon_fire
```

Interpretation: one card.

```txt
Column A: <empty>
art = *[A B C]
```

Interpretation: three cards -> `A`, `B`, `C`.

### `0` means "do not generate this row"
If column A resolves to `0` copies, the row is skipped completely.

```txt
Column A: 0
art = *[A B C]
```

Interpretation: no cards are generated from that row.

### Explicit copies
Column A can declare an explicit quantity.

With iterators:

1. If copies > iterator length, values wrap around.
2. If copies < iterator length, the sequence is truncated.

Examples:

```txt
Column A: 5
art = *[A B C]
```

Interpretation: `A, B, C, A, B`.

```txt
Column A: 2
art = *[A B C]
```

Interpretation: `A, B`.

### Selector syntax in the final `[...]`
The final bracket block in column A can select iterator positions explicitly.

Examples:

```txt
[1..5 7..100]
```

Interpretation:
- keep `1,2,3,4,5`
- skip `6`
- then keep `7..100`

```txt
[3..1 4..5]
```

Interpretation:
- reorder the beginning as `3,2,1`
- then continue with `4,5`

This selector is applied to the expanded iterator sequence before copy wrapping/truncation.

### Unknown end: `?`
Use `?` to mean "the final iterator position calculated by PnPInk".

Examples:

```txt
[13..?]
```

Interpretation: keep from `13` to the last iterator item.

```txt
[?..12]
```

Interpretation: keep from the last iterator item down to `12`, in reverse order.

### Empty slots (holes)
Hole syntax uses:

- `-` = 1 empty slot
- `N-` = `N` empty slots

Holes are inserted **after the accumulated generated run at that point**.

Examples:

```txt
[3 - 2-]
```

Interpretation:
- generate 3 cards
- then insert 1 empty slot
- then insert 2 more empty slots

```txt
[2 3- 5]
```

Interpretation:
- generate 2 cards
- insert 3 empty slots
- then generate 5 more cards

### Mixing selection, reordering and holes
Selection and holes can be mixed in the same final `[...]`.

Examples:

```txt
[1..4 3- 7..9]
```

Interpretation:
- keep iterator items `1,2,3,4`
- insert 3 empty slots
- keep iterator items `7,8,9`

```txt
[3..1 2- 4..5 7..9 ?..12]
```

Interpretation:
- reorder the first block as `3,2,1`
- insert 2 empty slots
- keep `4,5`
- keep `7,8,9`
- then append from the last item down to `12`

### Practical summary
Column A can therefore be used to:

- leave default generation untouched
- skip a row with `0`
- force a fixed number of copies
- skip parts of an iterator sequence
- reorder iterator positions
- refer to the unknown end with `?`
- insert empty slots between selected/generated runs

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
