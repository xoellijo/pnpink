# Transform

`Transform` applies visual changes to the object itself after it has already been placed.

Use it when you want to alter the final appearance of an object without changing the placeholder logic of `Fit` or `Fit-Anchor`.

`Transform` is not about the placeholder.
It is about the object itself once placement is already solved.

## Transform vs Fit
Use:

- `Fit` / `Fit-Anchor` for anything relative to the placeholder
- `Transform` for anything applied to the object itself

Examples of `Fit` concerns:

- fit mode
- anchor
- border
- clipping to the placeholder
- placement inside the placeholder

Examples of `Transform` concerns:

- opacity
- soft edges
- later, other visual effects such as blur, color adjustments or shadows

## Syntax

```txt
object_id.T{opacity=50% soft=12%}
object_id.T{o=50% s=12%}
@{source}.T{o=70%}
```

`Transform` can be written as:

- `Transform{...}`
- `T{...}`

## Parameters

Current parameters are:

- `opacity` or `o`
- `soft` or `s`

## Opacity

```txt
.T{o=50%}
.T{opacity=80%}
```

This changes the final opacity of the placed object.

## Soft Edges

```txt
.T{s=12%}
.T{soft=[12% 4%]}
.T{s=[5% 10% 15% 20%]}
```

`soft` creates a soft fade towards the edges of the object.

Accepted forms:

- one value: same fade on all sides
- two values: `[horizontal vertical]`
- four values: `[top right bottom left]`

Values are percentages.

## Typical Use

```txt
photo.T{o=85%}
photo.T{s=10%}
photo.T{o=75% s=[8% 3%]}
```

This is useful for:

- fading photos or textures
- softening cutout edges
- blending placed art into a tile or card background

## Related Pages

- [Fit-Anchor](/dsl/fit-anchor/)
- [Source](/dsl/source/)
