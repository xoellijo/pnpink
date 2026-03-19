# Advanced
Advanced DSL features that are implemented in code and useful in larger projects.

## Alias Definitions
Aliases reduce repetition in large datasets and make rows easier to read.

You can define aliases and reuse them later:

```txt
@hero = @{assets/hero.png}
@icons = [icon_hp icon_atk icon_def]
```

Then reference by index:

```txt
@hero
@icons[2]
@icons[1..3]
@icons[*]
@icons[1 4 7]
```

## Source Suffixes
Suffixes are useful when source resolution and fit behavior must be expressed in one token.

Source expressions support both long Fit and shorthand ops:

```txt
@{assets/token.svg}.Fit{mode=i anchor=5}
@{assets/token.svg}~i5^15|
```

This applies the same Fit/Anchor logic used for normal SVG IDs.

## Page Cursor Control (`at`)
Use cursor control when output must start at a specific page position.

`Page{}` supports page cursor movement with `at` / `a` / `@`:

```txt
{A4 @+3}
Page{A4 at=-1}
Page{A4^@5}
```

This controls where the next generated content starts in the global page sequence.

## Page Break Blocks
These forms are useful for explicit pagination control between logical sections.

Standalone page blocks are valid and useful:

```txt
{}      # break to next page
{3}     # advance by 3 pages
{3*A4}  # multiplier + size
```

## Leading Cell Composition
Combining directives in column A allows row-level orchestration without extra columns.

Column A data rows can combine multiple directives in one cell:

```txt
{A4 b=[-5]} .L{p=3x3 g=2} .M{mk_default d=2} [10 3- 5]
```

Supported order is flexible, but the parser expects:

1. Optional `Page{}` block.
2. Optional `.M{}` marks block.
3. Optional `.L{}` layout block.
4. Optional copy/hole tail (`[10 3- 5]` or trailing number).

## Inline Icon Tokens in Text
These tokens allow icon insertion directly in flowing text while preserving typographic behavior.

Text rendering supports inline icon tokens:

```txt
:heart_icon:
:@{icon://noto/heart-suit}:
:@{icon://noto/star}~i5^10:
```

Token forms implemented:

- `:id:` uses an existing SVG id.
- `:@{...}:` uses a Source token with optional fit suffix.
- `:S{...}:` and `:Source{...}:` are equivalent source forms.
