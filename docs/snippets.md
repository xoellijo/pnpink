# Snippets
Snippets are reusable text templates expanded before normal `${var}` replacement.
This feature is designed to keep datasets readable when text patterns repeat.

## Syntax
This section defines the only supported definition format in the current engine.

Define snippets in comment lines:

```txt
# :Name(arg1 arg2=default) = template text
```

Rules from the current engine:

- No space is allowed between snippet name and `(`.
- Parameters are space-separated.
- Defaults use `name=value`.
- The replacement template is plain text after `=`.
- Old `->` definitions are not supported.

Call syntax:

```txt
:Name(value1 value2)
```

## Arguments and Expansion
Argument mapping rules are what make snippets usable in real datasets.

Snippets support positional args, named args, defaults, and nesting.

```txt
# :Join(a b) = ${a}/${b}
# :Tf(text font size) = <tspan font-family='${font}'${size? font-size='${size}'}>${text}</tspan>

:Join(cards heroes)                -> cards/heroes
:Tf(Title Noto 16px)               -> <tspan font-family='Noto' font-size='16px'>Title</tspan>
:Tf(Title Noto)                    -> <tspan font-family='Noto'>Title</tspan>
:Tf(text=Title font=Noto size=12)  -> named arguments are valid
```

Conditional blocks use `${var? ...}` and render only when `var` is non-empty.

If a snippet has exactly one parameter, that parameter captures the full inner text:

```txt
# :Bold(text) = <b>${text}</b>
:Bold(This is bold text) -> <b>This is bold text</b>
```

## Escaping and Errors
These rules explain why malformed or unknown calls do not break generation.

- Use `\:` to keep a call literal.
- If a snippet does not exist, the call stays literal.
- If a call is malformed, it stays literal.

```txt
\:Join(a b) -> :Join(a b)
```

## Practical SVG Text Helpers
These helpers are optional, but they are the fastest way to build rich text consistently.

Useful helpers for `<tspan>` rich text:

```txt
# :Tb(text) = <tspan font-weight='bold'>${text}</tspan>
# :Ti(text) = <tspan font-style='italic'>${text}</tspan>
# :Ts(text) = <tspan text-decoration='underline'>${text}</tspan>
# :Tx(text) = <tspan text-decoration='line-through'>${text}</tspan>
# :Tc(text fill border bordercolor) = <tspan fill='${fill}'${border? stroke='${bordercolor}' stroke-width='${border}' paint-order='stroke'}>${text}</tspan>
# :Te(text) = <tspan font-family='Noto Color Emoji'>${text}</tspan>
```

Nested expansion is supported and resolved from inner to outer calls.

## Processing Order
Processing order matters when snippets and `${var}` are combined in the same cell.

For each dataset cell:

1. Snippets are expanded.
2. `${var}` replacements are applied.
3. DSL placement and rendering run.
