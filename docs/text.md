# Text

Text in PnPInk starts as ordinary SVG text designed in Inkscape, but it can become fully data-driven: a dataset can replace its content, mix styles, insert vector icons, flow it through a shape, resize its frame, or decorate selected words with paths. This page presents those possibilities as one workflow, from the SVG foundations to compact snippet-based notation.

## How SVG Text Works

An Inkscape text object is normally an SVG `<text>` element. Its font, size, fill, stroke, alignment and spacing may be stored directly on that element or inherited from its parent. Give the object a stable SVG ID, such as `title` or `description`, and use the same name as a dataset header to replace its content.

```xml
<text id="title" font-family="Calibri" font-size="8">Template title</text>
```

SVG does not treat the two characters `\n` as a line break. A normal text object also does not behave like an HTML paragraph. In Inkscape, separate lines are usually represented by positioned `<tspan>` children, while paragraph-like wrapping is created with `shape-inside`. If a dataset must preserve arbitrary multi-line content, a flowed `shape-inside` text is generally easier to maintain than manually positioned lines.

A `<tspan>` is a styled portion of a `<text>` object. It can change font, size, weight, color, baseline or decoration without creating a separate text object:

```xml
<text id="description">
  Normal <tspan font-weight="bold">bold</tspan>
  and <tspan fill="#e24a33">colored</tspan> text
</text>
```

Inkscape creates and edits many of these structures visually. The XML Editor is useful when you need to inspect the exact `<text>`, `<tspan>` and `shape-inside` relationships or verify their IDs.

## Plain and Rich Dataset Text

For ordinary replacement, the header is simply the text ID. The explicit form `id[text]` is equivalent:

```csv
title,description[text]
Distant World,Draw one card
```

Plain replacement removes the previous child spans and writes one text value while preserving the relevant style of the template text. Use this for titles, counters, labels and other fields with one uniform style.

Rich text uses `id[xml]`. Its value must be a valid XML fragment, normally one or more `<tspan>` elements:

```csv
description[xml]
Gain <tspan font-weight='bold'>two</tspan> cards
```

Because CSV quoting rules still apply, values containing commas or double quotes may need to be quoted by the spreadsheet application. Prefer single quotes inside XML attributes when authoring CSV by hand.

Rich XML is intentionally explicit: PnPInk does not invent an HTML-like markup layer. This keeps the output editable in Inkscape and allows any SVG text attribute that Inkscape understands.

## Rich Text with Snippets { #rich-text-with-snippets }

Writing `<tspan>` repeatedly is powerful but inconvenient. Snippets let a project define its own small text language while still generating normal SVG markup. PnPInk includes common helpers through `pnpink_ini.csv`:

```txt
:Tb(Bold text)
:Ti(Italic text)
:Td(subscript)
:Tu(superscript)
:Ts(underlined)
:Tx(struck through)
:Tf(Text "Noto Sans" 12px)
:Tp(Text 80%)
:Tc(Text #ffcc00 #000000 0.4)
```

Their expanded forms are regular `<tspan>` elements. Calls can be nested, so a short dataset value can describe several combined styles:

```txt
:Tc(:Tb(Important) #ffd84d #513a00 0.35)
```

You can define project-specific helpers in dataset comment lines. This example creates a highlighted label while making the optional size conditional:

```txt
# :Label(text color size) = <tspan fill='${color}' ${size? font-size='${size}'}>${text}</tspan>

:Label(Warning #d73535 90%)
```

Snippets expand before `${variable}` replacement and before the normal DSL is interpreted. This makes them suitable not only for text styles but also for inline icons, repeated source expressions and other reusable notation. The complete definition, argument, quoting, conditional and nesting rules remain in [Snippets](snippets.md).

## Inline Icons { #inline-icons }

Inline icons place SVG artwork inside the flow of a text object. A token delimited by colons creates a transparent typographic hole and positions the icon relative to that hole:

```txt
Gain :card_icon: and then draw :@{icon://noto/star}:.
```

The basic forms are:

```txt
:existing_svg_id:
:local-image.png:
:@{icon://noto/heart-suit}:
:S{assets/token.svg}:
:Source{assets/token.svg}:
```

The hole is derived from the surrounding text height. Fit-Anchor suffixes control how the artwork uses that hole, exactly as they do for normal placeholders:

```txt
:rebel~[30%]3:
:then~[60%]6[-80% 0]:
:@{icon://noto/star}~[50%]9|:
```

Here `[30%]` or `[60%]` changes the fit area, the keypad number selects the anchor, and the optional offset moves the result relative to the hole. Later transform suffixes such as rotation, mirror or opacity remain available. Percentages must include `%`; `4%` means a very small icon, whereas a bare `4` is interpreted through the normal measurement grammar.

An existing SVG icon or group may also receive instance-specific text:

```txt
:counter.T{t=+2}:
:then.T{t="x2"}~[75%]6[-70% 0]:
```

`Transform{text=...}` creates a real private copy instead of a shared `<use>`, then replaces the text nodes inside it. A list supplies values to successive text nodes.

Several icons can share one hole by placing their tokens inside the same colon pair:

```txt
:background_icon foreground_icon~[30%]3:
:settle~[80%]5 free~[40%]3:
```

Every item uses the same hole as its Fit-Anchor placeholder. Items are rendered from left to right, so later icons appear above earlier ones. This is useful for a base symbol plus a corner badge, value, restriction or status marker without building another permanent SVG group.

Inline icons are measured together with other geometry-dependent text features through one persistent Inkscape process. No additional configuration is required; the important template requirement is that the text has valid font metrics and remains renderable by Inkscape.

## Flowed Text and Dynamic Frames { #flowed-text-and-dynamic-frames }

Inkscape can flow a `<text>` object inside a rectangle by storing `shape-inside:url(#frame_id)` in the text style. The rectangle defines the available lines and `shape-padding` defines the inset between its edge and the text.

Create the rectangle and text in Inkscape, apply the flowed-text relationship, and give the text a memorable ID such as `description`. The shape itself may be moved into `<defs>` by Inkscape and its generated ID may be difficult to discover. PnPInk therefore exposes it through the virtual property `description[shape-inside]`.

```csv
description,description[shape-inside]
Long text for this record,.T{i=a}
```

Property-only headers inherit the previous ID, which keeps adjacent text and frame columns compact:

```csv
description,[shape-inside]
Long text for this record,.T{i=a}
```

`Transform{inside=...}`, or its short `i`, trims the frame after Inkscape has laid out the final text:

```txt
.T{i=x}   # trim horizontally
.T{i=y}   # keep the top and trim the bottom
.T{i=a}   # trim both axes
```

Horizontal trimming respects text alignment. Start-aligned text keeps its starting edge, end-aligned text keeps the opposite edge, and centered text trims symmetrically. Vertical text currently begins at the top, so vertical trimming removes unused space below it. `shape-padding` is retained around the measured content.

Fit-Anchor and transforms can be applied before the final trim. This allows a frame to be moved or resized against another placeholder, after which the text reflows inside it:

```csv
placeholder,description,description[shape-inside]
,Variable text,description[shape-inside]~[90%]i.T{i=a}
```

If the linked text is empty, its private frame is removed. Arrays and layouts containing dynamic text frames are packed again after their final dimensions are known, so descriptions with different heights can be stacked with normal `.Layout{...}` notation.

Text-level scale transforms created in Inkscape are supported. This is useful for deliberately compressed or stretched typography. Internally PnPInk preserves the original flow geometry while trimming the visible frame, avoiding the Inkscape behavior where an aggressively reduced shape can make the entire flowed line disappear. This internal detail requires no extra SVG objects in the template.

## Decorating Part of a Text { #decorating-part-of-a-text }

A `<tspan>` can generate a path behind or in front of only its own rendered text. The referenced path is a style source: its stroke paint, joins, caps, dashes, markers, gradient, filter and opacity are copied, while the generated stroke width adapts to the measured text height.

```xml
<tspan
  pnp:decoration="#yellowBrushPath"
  pnp:decoration-layer="behind"
  pnp:decoration-padding="0.15em 0.25em -0.05em">
  Highlighted text
</tspan>
```

`behind` is the default. Use `front` for translucent stamps, scratches or overlays. Padding follows CSS order: one value applies to every side, two mean vertical/horizontal, three mean top/horizontal/bottom, and four mean top/right/bottom/left. Values accept `em`, `%`, explicit SVG units or bare millimetres; surrounding brackets are optional.

The style path may remain in `<defs>` so it is not drawn by itself. Keep a decorated span on one rendered line: a wrapped `<tspan>` currently receives one path spanning its combined bbox.

The default `:Tpath` helper keeps the XML readable:

```txt
:Tpath(Highlighted yellowBrushPath [0.15em 0.25em -0.05em])
:Tpath(Stamped redStampPath [2 3] front)
```

It expands to the `pnp:decoration` attributes shown above. The older `:Tp(text size)` helper remains the shorthand for a plain sized `<tspan>`.

## Text Inside Reusable Artwork

`Transform{text=...}` is also available outside inline text. Apply it to a placed icon or group that contains one or more text nodes:

```txt
counter_icon.T{t=+3}
status_badge.T{t=[READY 2]}
```

PnPInk copies the artwork, preserves nested `<tspan>` styling and replaces its text for that instance only. This is useful for counters, costs, labels and icons whose vector artwork is shared but whose value changes per record.

## Reliable Bounds for Text Groups

Inkex versions shipped with Inkscape 1.2 through 1.4.x can return unpredictable bounds for groups containing text. If such a group must be fitted like normal artwork, add a direct child rectangle whose SVG ID or Inkscape label starts with `force_bbox`, `force-bbox` or `forcebbox`, ignoring case. PnPInk uses that guide as the group bbox even if it is transparent. An explicit `data-bbox` attribute has higher priority.

This workaround concerns fitting whole groups. Inline icons, dynamic frames and text decorations use Inkscape's shared rendered-text measurement pipeline instead.
