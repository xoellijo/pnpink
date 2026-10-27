# DSL Nomenclature
This chapter defines the shared language used across the DSL (Domain Language).
Use it as the common reference before reading module-specific pages.

## What belongs here
PnPInk has two complementary parts:

- dataset structure (rows, headers, section markers, control columns),
- DSL logic (declarative syntax that controls how data is interpreted and rendered).

This chapter is about the second part: the DSL itself.

In practice, this includes:

- module syntax (`Fit`, `Layout`, `Page`, `Source`, `Marks`, `Gradients`),
- compact notation (`~...` and operator shorthands),
- list/range/repetition grammar,
- global directives in comment lines (`# ...`) such as snippets, spritesheet aliases, and other declarations.

For dataset structure and section markers, see [Dataset Reference](../dataset.md).

## IDs and Targets
An SVG `id` is the primary selector for DSL operations.

For the Inkscape ID workflow (where to inspect/edit IDs and structure), use:
[Introduction -> How IDs connect data to graphics](../intro.md#ids-workflow).

Target forms:

- one SVG object id: `title`,
- grouped target list: `[id1 id2 id3]`,
- source target: `@{assets/icon.svg}`.

These selectors let one logical target expose multiple concrete items without creating many separate headers.

## Module Form
Modules define behavior declaratively.
Use long form for clarity and short form for compact production datasets.

Long form:

```txt
target.Module{ key=value key2=value2 }
```

Here, `key` and `key2` are module **parameters**.
Each module defines its own valid parameter set, defaults, and aliases.
In this documentation, we always call them **parameters** (not properties).

Short form:

```txt
target.F{ ... }    # Fit
target.T{ ... }    # Transform
target.L{ ... }    # Layout
target~...         # Fit-Anchor shorthand
```

Common modules:

- `Fit{}` / `F{}` / `~...`
- `Layout{}` / `L{}`
- `Page{}`
- `Source{}` / `@{}`
- `Marks{}` / `M{}`

## Global Tokens
- `[]` list or list-like values.
- `{}` module blocks (`Page`, `Layout`, `Marks`, etc.).
- `^` rotation (and page landscape in page tokens).
- `|` and `||` mirror (horizontal, vertical).
- `!` clip (see Fit-Anchor clip stage details).
- `:` substitution (inline `:icon:` and `:snippet(...)`).
- `*` has three different uses depending on context:
  - wildcard in identifiers/paths (`id*`, `file*.png`),
  - iterator prefix in dataset values (`*[ ... ]`, `**[ ... ]`, ...),
  - repetition inside list/array bodies (`3*id`, `3*(id1 id2)`).
- `()` function arguments (`:snip(a b)`) and group blocks inside lists/iterators.

## Lists, Ranges, and Repetition
Lists are space-separated:

```txt
[ID1 ID2 ID3]
```

Supported patterns in list/array bodies:

```txt
[ID1 - - - ID4]    -> same as [ID1 3- ID4], '-' means empty slot
3*ID               -> [ID ID ID]
5*:Ic(potato)      -> [:Ic(potato) :Ic(potato) :Ic(potato) :Ic(potato) :Ic(potato)]
[id1 3*(id2 5- id3)]
```

Repetition:

- `n*Id` means repeat `Id` `n` times.
- Example: `[3*Id]` is equivalent to `[Id Id Id]`.
- `Id` can be a `( )` grouped block: `[2*(idA idB)]` is equivalent to `[idA idB idA idB]`.

Ranges and selectors:

```txt
target[2]      -> item 2
target[2..4]   -> items 2, 3, 4
target[A..ZZ]  -> A B C ... Z AA AB .. ZZ
target[*]      -> all items
target[B3]     -> spreadsheet-style selector for grid/spritesheet structures
target[3!B4]   -> column B, row 4 on page 3 of a multipage spritesheet
target[2!B2:C4 3!C4] -> combined rectangle and cell selectors
target[2!B2..5!C1]   -> linear range crossing PDF pages
```

The optional `page!cell` prefix follows spreadsheet notation. It is mainly used
with PDF spritesheets: `@sp1[B4]` reads page 1, while `@sp1[3!B4]` reads the
same grid position from page 3. See [Source: Spritesheets](source.md#spritesheets)
for definitions, ranges, and lazy PDF page rendering.

Space-separated selectors are combined in order. `:` selects a rectangle on one
page; `..` follows layout order and may cross page boundaries. Prefix the source
with `*` to create one dataset instance per selected frame.

This is core PnPInk DSL syntax. The same selector grammar is used for direct
sources, iterators, raster images, and multipage PDF spritesheets; those paths do
not define separate selector variants.

Note that `target[*]` (selector) and `*[ ... ]` (iterator) are not the same operation.
- `target[*]` selects all items inside one target expression.
- `*[ ... ]` expands rows/instances during iterator processing (assign items to different cards)

## Units and Expressions
In paramters, most numeric tokens accept:

- plain numbers (current default unit),
- `mm`, `px`, `%`,
- mixed expressions such as `-25%+2`.

Percentages are evaluated against a base size.
For example border=[80% 110%+3] define a new rect size with 80% of actual width, and 110% of height + 3 mm.


## Comment Directives and Declarations
Comment rows (`# ...`) can declare DSL directives (aliases, variables, spritesheets... ) outside normal data rows.
Typical examples:

```txt
# :snippet(x) = ...
# @sp1 = @{sheet.png}.Layout{8x6}
# gradientGold = G{b88326ff^25 [-25 12 5] [55 35 20 10]}
# _DM_reset_to=3
```

These declarations are read by their corresponding modules/subsystems and then reused in dataset rows.

## Abbreviations, Defaults, and State Reuse
Module and parameter abbreviations are supported where their reference page documents them. They are not arbitrary truncated words.

```txt
Layout{} -> L{}
inside -> i
Page{pagesize=A4 border=[-2]} -> P{A4 b=[-2]}
```

For Fit-Anchor specifically, both the module and every parameter have stable initials:

```txt
Fit -> F
mode -> m
anchor -> a
border -> b
shift -> s
clip -> c
```

Most modules define default parameters, so compact forms can omit explicit keys. For example, you do not need to write `p=A4` because `pagesize` is the default parameter of `Page{}`.

When a required value is omitted, the last active state may be reused (module-dependent behavior).

For exact page-state rules, see [Page](page.md).
For Fit-Anchor shorthand order and precedence, see [Fit-Anchor](fit-anchor.md).
For a visual quick reference combining Fit-Anchor and Transform tokens, see
[Compact Notation (Fit-Anchor and Transform)](compact-notation.md).
