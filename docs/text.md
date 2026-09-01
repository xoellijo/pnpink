# Text

Text in PnPInk starts as ordinary text designed in Inkscape and becomes data-driven when DeckMaker replaces it. It can remain simple, mix several styles, insert icons — images, vectors, or downloaded artwork — directly into a sentence, automatically follow the text flow, font size and baseline, resize its own frame, or decorate selected words.

This page begins with normal Inkscape text and introduces the more powerful features gradually. You do not need to understand the SVG examples to use the basic workflow.

## How Text Works in Inkscape

For PnPInk projects, it is useful to think of two kinds of Inkscape text:

- **Normal text** is created by selecting the Text tool, clicking once on the canvas and typing. It does not wrap automatically. Pressing Enter creates another line inside the same text object.
- **Text in a box** is created with the same Text tool, but by clicking and dragging a rectangle before typing. The text wraps inside that area and reflows when the box changes size.

Give either kind a memorable SVG ID such as `title` or `description`. A dataset column with the same name will replace its content:

```xml
<text id="title">Template title</text>
```

Behind the scenes, both are normal SVG `<text>` elements. Inkscape usually stores separate lines and styled fragments as `<tspan>` children. Text boxes also keep a `shape-inside` relationship with the shape that controls their width and wrapping. You can normally let Inkscape manage these details.

!!! inkscape "Inkscape tip: inspect text structure"
    Open `Object > Layers and Objects` (`Shift+Ctrl+L`) to find text IDs and stacking order. For the exact `<text>`, `<tspan>` and `shape-inside` attributes, open `Object > XML Editor` (`Shift+Ctrl+X`). The XML Editor is useful for inspection and advanced editing, but it is not required for ordinary text replacement.

The two characters `\n` are not a line break in SVG. For text that must wrap or contain an unpredictable number of lines, create text in a box instead of trying to insert `\n` into a normal text object.

## Plain and Rich Dataset Text

The normal header is simply the text object's ID. PnPInk recognizes that the target is text and replaces its content:

<div class="csv-dataset" markdown>

| title | description |
| --- | --- |
| Distant World | Draw one card |

</div>

Use this for titles, values, labels and paragraphs with one uniform style. The template keeps control of the font, size, alignment, fill and other visual properties.

PnPInk also recognizes `<tspan>` markup in the cell automatically. This lets one value contain bold, italic, colored, resized or otherwise styled fragments without changing the header:

<div class="csv-dataset" markdown>

| description |
| --- |
| Gain <tspan font-weight='bold'>two</tspan> cards |

</div>

The result remains editable SVG text in Inkscape. There is no need to choose between `id[text]` and `id[xml]`: use the plain ID and PnPInk detects rich `<tspan>` content when it is present.

!!! pnpink "PnPInk dataset tip"
    Spreadsheet applications normally quote CSV cells for you. When editing CSV by hand, quote a value that contains commas or double quotes, and prefer single quotes inside `<tspan>` attributes.

## Rich Text with Snippets { #rich-text-with-snippets }

Writing complete `<tspan>` tags in every row soon becomes inconvenient. **Snippets** turn common styles into short, readable calls such as `:TB(Important)` or `:TC(Warning #ffcc00)`.

This section only explains how to use the text helpers. See [Snippets](snippets.md) *(define reusable shortcuts)* when you want to create your own, use variables, or learn the expansion rules.

| Helper | Purpose |
| --- | --- |
| `:TB(text)` | Convert `text` to **bold**. |
| `:TI(text)` | Convert `text` to *italic*. |
| `:TL(text)` | Lower `text` as subscript. |
| `:TH(text)` | Raise `text` as superscript. |
| `:TU(text)` | Underline `text`. |
| `:TX(text)` | Cross out `text`. |
| `:TF(text font size=)` | Change the font family and, optionally, its size. |
| `:TS(text size=)` | Change the text size. |
| `:TC(text fill stroke= width=)` | Set the fill and an optional outline color and width. |
| `:TM(text styleID border= front=)` | Decorate the text with a reusable SVG style object. |

For example:

<div class="csv-dataset" markdown>

| description |
| --- |
| Gain :TB(two cards), then discard :TI(one card). |

</div>

Snippets can be nested. PnPInk expands the innermost call first, so several simple helpers can build a rich result:

<div class="csv-dataset" markdown>

| description |
| --- |
| :TM(:TC(:TI(:TB(Ancient warning)) #fff4c2 #472400 0.35) yellowBrush) |

</div>

This produces **bold italic cream text**, gives it a dark outline, and places the result over the SVG decoration named `yellowBrush`. The dataset stays short even though the generated SVG contains several nested `<tspan>` styles.

!!! pnpink "Advanced PnPInk tip"
    These basic text snippets are loaded automatically for every project. Their definitions live in `pnpink_ini.csv`, but normal projects only need to call them. Project-specific definitions and `${variable}` expressions belong in the [Snippets reference](snippets.md).

## Inline Icons { #inline-icons }

Inline icons place artwork inside a sentence as if it were another character. The icon follows the text when it moves or wraps, and its available space is calculated from the rendered text position, size and baseline.

Each inline icon can carry the same richness and power as artwork placed anywhere else by PnPInk. It may come from a local or remote Source, use Fit-Anchor to control its size and position, apply Transforms such as rotation or opacity, or combine several independent pieces into one icon assembled on the fly.

Wrap the artwork reference in colons:

<div class="csv-dataset" markdown>

| description |
| --- |
| Gain :card_icon: and then draw :star:. |

</div>

The reference can point to much more than a conventional icon:

| Example | What it uses |
| --- | --- |
| `:card_icon:` | A vector node or group already present in the SVG template. |
| `:assets/coin.png:` | A local image stored with the project. |
| `:@{icon://noto/star}:` | A vector icon downloaded on demand from Iconify. |
| `:@{wkmc://File:Haeckel_Discomedusae_8.jpg/300}:` | An image downloaded on demand from Wikimedia Commons. |

See [Sources](dsl/source.md) *(load external artwork)* for local files, web images, Iconify, Wikimedia Commons, Google Drive and other providers.

PnPInk temporarily reserves a transparent **hole** in the text and places the artwork in that hole. By default, the icon is fitted to the line height and centered. [Fit-Anchor](dsl/fit-anchor.md) *(size and place artwork)* can then make it smaller, align it to a side or shift it:

<div class="csv-dataset dataset-comments" markdown>

| description | Meaning |
| --- | --- |
| :star: | # Fit naturally in the text line |
| :star~[60%]5: | # Use 60% of the hole, centered |
| :star~[40%]3: | # Use 40%, aligned bottom-right |
| :star~[60%]6[-20% 0]: | # Align right-center, then shift left |

</div>

The percentage changes the usable area, the keypad number selects the anchor, and the final pair shifts the result horizontally and vertically. You can learn the full notation later; the important idea is that the text controls the hole while Fit-Anchor controls the artwork inside it.

### Compose an Icon on the Fly

Several pieces can share the same hole. The first item acts as the visual base and later items are drawn above it:

<div class="csv-dataset" markdown>

| description |
| --- |
| :shield crown~[30%]7: |

</div>

This creates a shield and places a crown above it, reduced to 30% of the hole and anchored in the top-left corner. Both pieces remain independent, so either can use its own Source, Fit-Anchor settings or Transform. More elaborate compositions use exactly the same idea:

<div class="csv-dataset" markdown>

| description |
| --- |
| :background_icon foreground_icon~[30%]3: |
| :settle~[80%]5 free~[40%]3: |

</div>

There is no need to build and save a new permanent SVG group for every combination. A dataset cell can assemble the required icon from reusable pieces for that record only.

### Transform the Result

[Transform](dsl/transform.md) *(modify the final object)* changes an icon after it has been placed. It can rotate, mirror, scale, fade, soften edges, apply an Inkscape filter or replace text contained inside reusable artwork:

<div class="csv-dataset dataset-comments" markdown>

| description | Meaning |
| --- | --- |
| :star.T{r=20}: | # Rotate 20 degrees |
| :portrait.T{m=h o=70%}: | # Mirror and make translucent |
| :counter.T{t=+2}: | # Replace text inside the icon |
| :then.T{t="x2"}~[75%]6: | # Combine Transform and Fit-Anchor |

</div>

When `Transform{text=...}` is used, PnPInk creates a private copy and replaces the text nodes inside it. This is useful for counters, multipliers and badges whose artwork stays the same while their value changes.

## Flowed Text and Dynamic Frames (Self-Sizing Text Boxes) { #flowed-text-and-dynamic-frames }

Inkscape can make text flow inside almost any shape: a rectangle, rounded panel, circle, speech bubble or irregular path. PnPInk can then shrink that shape around the final text, remove it when the text is empty, and stack several differently sized text panels without leaving fixed blank areas.

!!! inkscape "Inkscape tip: create flowing text"
    Create the shape and a normal text object, then select both and choose `Text > Flow into Frame` (`Alt+W`). The text immediately wraps inside the selected shape and reflows whenever it changes. Give the visible text object a useful ID such as `description`; PnPInk can find its linked shape automatically.

Behind the scenes, Inkscape stores this relationship as `shape-inside`. You do not need to know or edit the generated shape ID. Even if Inkscape moves the shape into `<defs>`, PnPInk exposes it through the visible text ID, using `description[shape-inside]`:


<div class="csv-dataset" markdown>

| description | description[shape-inside] |
| --- | --- |
| Long text for this record | .T{i=a} |

</div>

The shorter adjacent-header form is equivalent:

<div class="csv-dataset" markdown>

| description | [shape-inside] |
| --- | --- |
| Long text for this record | .T{i=a} |

</div>

`Transform{inside=...}`, or its short `i`, trims unused frame space after Inkscape has laid out the final text:

<div class="csv-dataset dataset-body-only dataset-comments" markdown>

|  | Meaning |
| --- | --- |
| .T{i=x} | # Trim horizontally |
| .T{i=y} | # Keep the top and trim the bottom |
| .T{i=a} | # Trim both axes |

</div>

Horizontal trimming follows the text alignment: left-aligned text keeps its left edge, right-aligned text keeps its right edge, and centered text trims symmetrically. Vertical trimming keeps the top and removes unused space below. An empty text removes its private frame completely.

!!! prerequisite "Before combining dynamic frames with layouts"
    The next examples use [Fit-Anchor](dsl/fit-anchor.md) *(size and place artwork)*, [Transform](dsl/transform.md) *(modify the final object)* and [Layout](dsl/layout.md) *(arrange several objects)*. Do not worry if the combined expression is not immediately clear; each linked chapter introduces one part independently.

Fit-Anchor and transforms can move or resize a frame first. Its text then reflows, the frame trims to the final content, and a Layout can stack several frames after their real heights are known:

<div class="csv-dataset" markdown>

| placeholder | description | description[shape-inside] |
| --- | --- | --- |
|  | Variable text | description[shape-inside]~[90%]i.T{i=a} |

</div>

Arrays and layouts containing dynamic frames are packed again after those final dimensions are available. This allows several descriptions with different lengths to form a clean vertical stack without reserving the maximum height for every item.

Text-level scale transforms created in Inkscape are supported, so typography may be deliberately compressed or stretched. PnPInk preserves the original flow geometry while trimming the visible frame, preventing an aggressively reduced shape from making the flowed line disappear.

## Decorating Part of a Text (Marks, Labels, and Adaptive Backgrounds) { #decorating-part-of-a-text }

!!! prerequisite "This section builds on rich text"
    Text decorators apply to a rich-text `<tspan>` and are easiest to use through `:TM(...)`. Read [Rich Text with Snippets](#rich-text-with-snippets) first. The complete [Snippets reference](snippets.md) *(define reusable shortcuts)* is only needed if you want to create a different helper.

A decorator generates artwork behind or in front of selected words. Its `styleID` refers to a reusable SVG path, group of paths, or rectangle:

<div class="csv-dataset" markdown>

| description |
| --- |
| :TM("Highlighted words" yellowBrush) |
| :TM("Stamped text" redStamp [2 3] front) |

</div>

Only `text` and `styleID` are required. `border` adds or removes space around the letters, while `front` selects an overlay; decorations are placed behind the text by default:

<div class="csv-dataset" markdown>

| description |
| --- |
| :TM(Highlighted yellowBrush) |
| :TM(Highlighted yellowBrush [0.15em 0.25em -0.05em]) |
| :TM(Stamped redStamp [2 3] front) |
| :TM(Stamped redStamp front=front) |

</div>

Without an explicit border, PnPInk adds `1 pt` on all four sides so the decoration does not sit flush against the glyphs. Pass `0` for the exact measured bounds. One border value applies to every side; two mean vertical/horizontal, three mean top/horizontal/bottom, and four mean top/right/bottom/left.

The style source controls the result:

- A **path** becomes a stroke fitted to the text width and height. Paint, caps, joins, dashes, markers, gradients, filters and opacity are preserved.
- A **group of paths** creates a layered decoration while preserving nested order, group opacity, filters and relative stroke widths.
- A **rectangle** fits around the text and preserves its fill, stroke, gradient, filter, opacity and proportional corner radii.

The equivalent low-level SVG is:

```xml
<tspan
  pnp:decoration="#yellowBrush"
  pnp:decoration-layer="behind"
  pnp:decoration-padding="0.15em 0.25em -0.05em">
  Highlighted text
</tspan>
```

The style source can remain in `<defs>` so it is not drawn on the template. Keep one decorated span on one rendered line: a wrapped `<tspan>` currently receives one decoration spanning its combined bounding box.

## Text Inside Reusable Artwork

`Transform{text=...}` also works outside inline text. Apply it to a placed icon or group containing one or more text nodes:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| counter_icon.T{t=+3} |
| status_badge.T{t=[READY 2]} |

</div>

PnPInk copies the artwork, preserves nested `<tspan>` styling and replaces its text for that instance only. This is useful for counters, costs, labels and icons whose vector artwork is shared but whose value changes per record. See [Transform](dsl/transform.md) *(modify the final object)* for the complete module.

## Reliable Bounds for Text Groups

!!! inkscape "Inkscape workaround: unpredictable text bounds"
    Inkex versions shipped with Inkscape 1.2 through 1.4.x can return unpredictable bounds for groups containing text. If PnPInk must fit such a group as normal artwork, add a direct child rectangle whose SVG ID or Inkscape label starts with `force_bbox`, `force-bbox` or `forcebbox`, ignoring case. The rectangle may be transparent and can be created entirely through the Inkscape interface.

PnPInk uses that guide as the group's bounding box. An explicit `data-bbox` attribute has higher priority. This workaround only concerns fitting whole groups; inline icons, dynamic frames and text decorators use Inkscape's shared rendered-text measurement pipeline instead.
