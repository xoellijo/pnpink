# PnPInk — Core Syntax and General Definitions
## 1. General Concepts
In PnPInk, any target object can be referenced from the dataset through its **SVG ID**.\
SVG IDs follow standard XML/HTML rules:

- Must start with a **letter**, cannot contain spaces or special characters, and cannot end with . or \_.

- Must be **unique** within the document.

We will refer to it simply as ID.

### 1.1. Lists and Multipliers
A list of IDs is written as:

\[ID1 ID2 ID3\]

All lists in PnPInk are **space-separated**.

You can also create lists with gaps or multipliers:

\[ID1 - - - ID4\] → same as \[ID1 3- ID4\]

3\*ID → \[ID ID ID\]

### 1.2. Target References
A *target* can be a single element or a container, such as a page, an SVG, a PDF, or a spritesheet (an image with a defined grid).

Examples:

target\[2\] → page 2

target\[2 4\] → pages 2 and 4

target\[2..4\] → pages 2, 3 and 4

target\[\*\] → all pages

target\[2\]\[3\] → frame at column 2, row 3 (in a spritesheet)

target\[4\]\[2\]\[3\] → page 4, column 2, row 3

### 1.3. Modules
A *module* applies an operation or transformation to an object or target.\
Modules are written with their first letter in **uppercase**, followed by {}. For example:

ID.Fit{ inside anchor=7 }

This means: *position ID relative to the anchor rectangle defined in the dataset header of its column, scaled to fit inside and anchored to the top-left corner (7 on the numeric keypad).*

Other modules include:

- .Layout{} — defines grid placement of repeated elements (e.g. cards on pages).

- .Source{} or shorthand @{} — defines the source of an object (e.g. file, image URL, or Iconify icon).

- .Page{}

- Marks{}

### 1.4. Syntax Rules
- {} are used for **modules\**

- \[\] for **lists\**

- \<\> for **attributes\**

- () for **function parameters**

Elements inside {}, \[\], (), or \<\> are always **space-separated**.\
\
If an element contains spaces, wrap it in quotes " " or ' ' (Python-style).

Usually, params inside blocks have assignment

.Module{ property1=value1 property2=value2 … }

### 1.5. Abbreviations and Defaults
Every keyword can be shortened to its **first letter**:

Layout{} → L{}

inside → i

Most modules define a **default property**, which can be omitted:

Page{pagesize=A4 border=\[-2\]} → P{A4 b=\[-2\]}

( “pagesize” is the default property for “Page” módule, so can be ommited)

When a required element is not specified, the **last defined value** is reused.\
Example: if no pagesize is given, the last declared one (e.g. A4) will be assumed.

### 1.6. Escape Character
The global **escape character** in PnPInk is the backslash \\

## 2. Dataset Headers
Dataset headers define which elements vary in each instance of a template.\
Typically, they correspond to existing SVG IDs whose values or properties change per card.

- If the ID refers to **text**, each dataset row defines the text shown.

  - Rich text is supported via \<tspan\> tags (bold, color, font, size, stroke, etc.).

  - Common formats can be applied using **snippets**: \[:snippet()\].

  - Inline icons are allowed using :icon:.

- If the ID is **non-textual**, it usually refers to a positioning rectangle (rect) that will be replaced by another element (image, icon, etc.) in each instance.

By default, the ID is replaced by the element specified in that dataset column.\
Suffixes modify this behavior:

| **Suffix** | **Meaning** |
|----|----|
| ID= | Set the default value for cells in columns of the dataset |
| ID+ | Places the dataset element *on top of* the original rect, without deleting it. |
| ID\<attrib\> | Changes a specific property of that ID for each instance. If the ID has already been replaced earlier in the row, the property applies to the new one. |

If only \<attribs\> appears, it refers to the **last defined ID** in the header (from previous columns).

## 3. Comments and Directives (#)
Comments are processed **before any other operation**.

Comments allow you to create directives outside the dataset and are a simple way to enable and disable parts.

### Outside the dataset (line level)
- \# at line start → comment line. Directives inside are executed.

- \## at line start → hard comment; no directives are executed.

- \## after a directive/comment → everything from \## onward is ignored.

\* The dataset starts at the first non-empty row **not starting** with \# (header row).

### Inside the dataset (cell level)
- \# anywhere → comments out the rest of that cell.

- \# at the start of a header cell → disables that entire column.

- \## at the start of column A → the entire row is ignored.

- \## at the start of a header cell → disables that column and all columns to the right.

\* Comment markers apply only to non-text cells to avoid collisions with literal content. In text cells, \# and \## are treated as plain characters.

## 4. Fit & Anchor Module
The **Fit** module (Fit{} or shorthand F{} or ~) describes how one element is positioned relative to another — usually a rect acting as a **placeholder**.

Example:

ID.Fit{ inside anchor=7 }

This places the element defined by ID inside its anchor rect, anchored at position 7 (top-left).

### 4.1. Syntax
ID.Fit{ border fitmode anchor shift clip rotate mirror }

List elements are space-separated.

### 4.2. Anchor
anchor = n

n ranges from **1–9** following the numeric keypad layout:

7 8 9

4 5 6

1 2 3

Examples:

ID.Fit{anchor=7} → top-left

ID.F{a=7} → shorthand

ID~7 → minimal form

<img src="docs\_imported\media/media/image1.png" style="width:1.67708in;height:1.10244in" />

If not specified, the default anchor is ~5 (center)

### 4.3. Fit Mode (\*f=)
Defines how the target scales relative to its anchor rect.\
It is the **default parameter**, so fitmode= can be omitted.

Examples:

ID.Fit{fitmode=inside}

ID.F{i}

ID~i

If no fitmode is given, inside (scaling to fit inside) is assumed.

#### Fit Mode Reference
| **Code** | **Name** | **Description** |
|----|----|----|
| i | inside / contain | Scales proportionally to fit entirely within the rect (default). |
| o | original | Keeps original size. |
| w | width-fit | Scales proportionally to match rect width. |
| h | height-fit | Scales proportionally to match rect height. |
| m | max / cover | Scales proportionally until it covers the rect, possibly overflowing. |
| x | x-stretch | Stretches width to match rect (non-proportional). |
| y | y-stretch | Stretches height to match rect (non-proportional). |
| a | all-stretch | Scales independently in X/Y to fill rect exactly. |
| t | tile | Tiles the element as a pattern within the rect. |
| b | best-fit ⚙️ | Smart mode — mixes m, a, and c depending on aspect ratio. Slightly deforms and clips automatically for composition balance. |
| c | clip / cut ✂️ | Explicit clipping action — synonym of ! or !! depending on context. |

### 4.4. Border (b=)
Defines padding or margin around the anchor rect.\
Positive = expand (padding), Negative = shrink (margin).

Examples:

border=\[-2\] → 2 mm margin on all sides

border=\[2 3\] → 2 mm vertical, 3 mm horizontal

border=\[2 3 4\] → top=2, bottom=3, sides=4

border=\[2 3 4 5\] → top, right, bottom, left (clockwise)

In short form:

ID~\[-2\]i → fits inside with 2 mm margin.

Percentages are allowed

border=\[50%\] → scales up (positive) the rect by half before fitting.\
\
Values may mix units and expressions:

border=\[5%+1 2 -4 3%-2\]

A missing number before % defaults to 100%:\
border=\[%\] → 100% / b=\[-%\] → -100%

#### Absolute size mode (WxH)
If border is a single value containing x (no spaces), it defines an **absolute target size** centered on the original rect:

border=\[40x60\] → rect becomes 40×60 mm (centered)

border=\[50%x20\] → width = 50% of original, height = 20 mm

Negative values in WxH flip (mirror) the content:

border=\[100%x-100%\] or b=\[%x-%\] → vertical flip (without scaling)

border=\[-100%x100%\] → horizontal flip\
\
In compact fit_anchor nomenclature, b can be omitted, being the firs \[ \] field (allways after the anchor number).\
~\[-50%\]→ inside the placeholder rect, reduced by 50% (~5 centered by default).

### 4.5. Translate (t=)
Offsets the final position \[x y\] relative to the anchor rect, in absolute or percentage units.

ID.Fit{anchor=7 trasalate=\[-100% -100%\]}

→ Positions ID outside the rect, touching its top-left corner.

In compact form, t can be omitted, been the \[ \] field after the anchor…\
\
~\[-10%\]7\[-% -%\] → first

### 4.6. Clip (c or !)
Cuts off everything outside the original (without border adjustments) anchor rect.\
Can be shortened to !.\
Double !! means clipping relative to the final rect (after border adjustments).

### 4.7. Rotate (r= or ^)
Rotates the target element (default 90°).\
Shortcut: ^. Examples:

ID.Fit{rotate=-42.4}

ID~^^^ → rotates -90° before fitting

ID~^-45i7 → rotates -45°, fits inside, anchored top-left

### 4.8. Mirror (m= or \|)
Mirrors the target.\
Default is horizontal (across vertical axis).\
Shortcuts: \| (horizontal), \|\| (vertical).

ID.Fit{mirror=v}

ID~\|\|

## 5. Layout Module (L{})
Defines how a group of elements are arranged in a grid (e.g. cards on pages).

Syntax:

Layout{ pattern=nxm shape= gaps= offset=} or L{nxm s= g= o=}

Can be applied to any object:

rect.L{}

\[rect1 rect2 …\].L{}

Page{A4}.L{p=5x8 shape=hexgrid}

### 5.1. Pattern Definition (\*p=)
pattern= (or shorthand p=) defines columns × rows, including padding.

p=3x2 → 3 columns × 2 rows

p=0x0 → auto-fit based on available area

Default alignment is centered inside the page or rect.

Placement order is **left→right, top→bottom**:

1 2 3

4 5 6

You can change order using **- (minus)** and **rotate (^)** symbols:

-3x2^ → vertical first, mirrored horizontally

Result:

5 3 1

6 4 2

### 5.2. Gaps (g=)
gaps (shorthand g) defines **spacing and relative offsets between layout slots**.

It affects **placement only** (never size) and is applied **after slot sizing, before final placement**.\
\
g = \[ w h ow1 oh1 ow2 oh2 \]\
Missing values default to 0.

| **Pos** | **Name** | **Meaning**                                 |
|---------|----------|---------------------------------------------|
| 1       | w        | Base horizontal spacing                     |
| 2       | h        | Base vertical spacing                       |
| 3       | ow1      | Horizontal offset for alternating slots     |
| 4       | oh1      | Vertical offset for alternating slots       |
| 5       | ow2      | Horizontal offset for the other alternation |
| 6       | oh2      | Vertical offset for the other alternation   |

Alternation is parity-based (row/column parity depending on grid orientation and smart shapes).\
\
g=\[2 3\] → 2 mm horizontal, 3 mm vertical

g=\[1% 3%\] → relative spacing to final rendered item

Units (mixed allowed)

g=\[1.5 3% -25%+2.5 0\]

**Technical note:** hexagonal layouts are obtained with internal manipulation of gaps parameters:\
I.e. hexgrid with flat-top hexagon shape can be emulated with g=\[-25% 0 0 50%\], or pointy-top hexgrid with g=\[0 -25% 50% 0\]

### 5.3. Shape (s=)
Defines the shape preset of each grid cell.

Common presets:

s=poker

s=minieuro

s=55.3x77.1

s=rect\<55.3x77.1\>

s=hex\<24x33\>

s=polygon\<\[5 23x32\]\>\
\[\[hay que poner la lista de todos los formatos definidos, incluidos alias\]\]

If shape size differs from template size, it will default to an **all-stretch (~a)** transformation.\
\
\
**Hex Shapes** are special cases that modify standard grid layout.

You can **create hexagons in Inkscape** with the polygon tool, set sides = 6 and hold **Ctrl** while creating to ensure perfect alignment.\
Rounded corners and decorative variants (“flowers”) are supported.

Orientation (pointy-top / flat-top) of hexagon template is **auto-detected.**

### shape=hexgrid
- Used for maps, boards, overlays.

- You can define gaps

### shape=hextiles
- **Used for Physical production of cuttable hex tiles (**adjacent tiles **share cut lines)**

- Gaps are adapted so edges always align correctly

- Marks{} generates cut marks on the **6 real edges (including border for bleed)**

If no special shape is requested, hexes behave like rectangles.

**Technical note**:\
Internally, this is implemented by dynamically adjusting horizontal and vertical gaps and alternating offsets so that neighboring hexes share edges without duplication.

## 6. Page Module (P{})
Defines page format and margins.

Page{pagesize at landscape border}

or shorthand:

P{A4}

P{letter}

{A4^} → landscape A4

When Page{} appears in the first column, the keyword “Page/P” can be omitted entirely.\
A bare {} means *continue using the previous pagesize*.

Multipliers are allowed:

{3\*A4} → three A4 pages

{3} → shorthand

### 6.1. Page{ pagesize } (\*p=)
Can be a preset (A2, A3, A4, A5, Letter, Legal, …) or user-defined dimensions (23.3x34.45).

Is the default parameter in Page{} so can be omitted.

### 6.2. Page{ landscape } (l/^)
Rotates page 90°.\
Shortcut: ^ (e.g. A4^).

### 6.3. Page{ border } (b=)
Same as in Fit & Anchor: defines padding or margin around the page.

b=\[-2\] → 2 mm margin

b=\[-2 -3\] → 2 mm vertical, 3 mm horizontal margin

b=\[2 3 4\] → padding (positive) top=2, bottom=3, sides=4

b=\[2 3 4 5\] → top, right, bottom, left

Percentage % values and absolute “wxh” are allowed (see Fit & Anchor border parameter).

**6.4. Page{ at } (a=/ @)**

at moves the **global page cursor** used to place dataset slots/cards.\
Internally it is **0-based**; at accepts **absolute (1-based)** or **relative** values.

**When applied**:

- sets page_index,

- resets slot_index = 0,

- ensures the page exists and recomputes its slots.

**When it runs\**
Only when a **Page** cell {...} is processed.\
Runs **after** the page preset and **before** multiplier page breaks.\
If the current page already has content, the engine jumps to a new page first, then applies Page{...} + at.

**Accepted syntax (equivalent)\**
at=…, a=…, @…, A4@…, A4^@…, {@…}.

**Value semantics**

- +n / -n → relative move.

- n → absolute **1-based** page (n → index n-1).

- Results \< 0 are clamped to 0.

**Notes**

- Empty {} ignores at (page break only).

- {n} / {n\*…} with body: at applies **once**; remaining n-1 are page breaks.

## Source Module (@{}/S{})
@{...} creates a source object from an external resource (file, icon, or URL), then uses it as a renderable object in the document.

Syntax:

Source{source_ref} or S{source_ref} or @{source_ref}

**Local file sources**

@{ relative/or/absolute/path.png }

@{ C:\path\to\image.png } \# Windows

@{ ~/images/token.png } \# Linux/macOS

Local sources support runtime expansion of environment/home variables at filesystem resolution time:

Windows: %USERPROFILE%

Linux/macOS: \$HOME, \${HOME}, ~

**Iconify sources**

@{ icon://icon_set/icon_name }

PnPInk resolves Iconify references against its icon catalog (200k+ free icons), downloads the icon, and registers it as an SVG symbol in the document.\
Examples:\
@{ icon://noto/heart-suit }

@{ icon://mdi/account }

There are a default snippet definition mapping to noto iconset:\
:Ic(cat) -\> @{ icon://noto/cat }

\* Placement uses clones (\<use\>) of that symbol.

\*\* Once an icon symbol exists in the document, it is reused and not downloaded again.

To force a fresh download, remove the symbol manually from the document’s symbol library (in Inkscape: Object\>Symbols or Shift+Ctrl+Y), then remove the relevant symbol entry.

**Web sources**

@{ https://... }

@{ http://... }

\* Current status: direct http/https loading is not implemented yet (placeholder behavior).

Fit/Placement behavior

A Source is treated as a final placeable object, so you can apply FitAnchor operations (scale, rotate, anchor, offsets, etc.) exactly as with other objects.

Internal model

Sources are always instantiated as clones of symbols (\<use\>), not expanded inline as full geometry copies.

This keeps the SVG compact, avoids repeated heavy geometry, and preserves a minimal canonical source definition for layout/render logic.

**. Spritesheets (@)**

The **Layout** module can also be applied to composite sources, such as **spritesheets**.

Example definition inside a comment block (before dataset)

\# @sp1 = @{file.pdf\<page=\[2-4\] border=\[-15\]\>}.Layout{pattern=3x2^ shape=poker gaps=\[4 3\]}

This creates a spritesheet from pages 2–4 of file.pdf, centered with 15 mm page margins and a 3×2 grid of poker-sized cards, spaced 4 mm horizontally and 3 mm vertically.

Then, you can reference any frame in the dataset as:

@sp1\[14\] → frame 14 (third page, second row)

@sp1\[2\]\[1\]\[3\] → page 2, column 1, row 3

(^ in the grid reverses numbering direction: “first rows, then columns” )

This spritesheet can then be used as a source in any dataset, positioned with Fit parameters (e.g. ~i6) as any other target.\
\
**7. Marks Module (M{})**

The Marks module generates cut marks aligned to the **Layout grid slots** of the **main template** (first dataset column). Marks are typically used for trimming cards or tiles after printing.

Marks are defined in the **first header cell** (same place as Page{} and Layout{}), for example:

{A4}.L{g=3x3 k=2}.M{ mk_style len=\[3 2\] d=2 }

#### 7.1. Slot-Based Behavior
Marks are generated **per slot** (per placed instance in the grid), using the transformed bounding box of the main template’s template_bbox for that slot.

This means:

- Marks follow the real grid placement (including kerf, page breaks, rotations, etc.).

- Marks are not tied to a specific template column; they follow the main layout slots.

#### 7.2. Syntax
Marks{ style len distance border layer scope }

Shorthand:

M{ ... }

Marks can be declared in the header without writing Marks explicitly if you already use the M{} shorthand.

Elements inside {} are space-separated. Lists are written in \[\] and are space-separated.

#### 7.3. Default Parameter (Style)
The default parameter of Marks{} is the **style id**. This means style= can be omitted:

.M{ mk_style len=3 d=2 }

is equivalent to:

.M{ s=mk_style len=3 d=2 }

##### Style Sources
A style id (s=) references an SVG element by ID:

- If the ID refers to a single element (path/shape), its stroke-related style is copied to the generated mark paths.

- If the ID refers to a group (\<g\>) containing multiple child paths, it is treated as a **style stack**:

  - the mark geometry is generated multiple times,

  - each copy receives the style of one child,

  - producing layered/overlay marks (e.g., black outline + colored inner stroke).

If no s is provided, a default style from preferences is used.

#### 7.4. Length (len=)
len= defines the length of the mark segments.

- Scalar form:

  - len=3 means **external length = 3**, internal length = 0.

- Two-value list:

  - len=\[out in\]

Examples:

.M{ len=2 }

.M{ len=\[3 2\] }

Interpretation:

- **external length** extends outward from the card edge.

- **internal length** extends inward (toward the card interior).\
  If internal length is 0, no internal segments are drawn.

#### 7.5. Distance to Card (d=)
d= sets the distance between the card edge and the **external** mark origin.

Default: d=2

d follows the same “border” grammar used elsewhere in PnPInk:

d=\[2 3 4 5\] → top, right, bottom, left

#### 7.6. Border Pattern (b=)
b= defines the cut-mark “border pattern” using the same conventions as borders in Fit/Page.

Default: b=0. If omitted, b=0 is assumed.

b is intended to reuse the same parsing and semantics as other border-like parameters in PnPInk. (The exact meaning of each border token is consistent with the existing border system.)

#### 7.7. Output Layer (layer=)
layer= defines the target Inkscape layer where marks will be inserted.

Default: layer=marks

If the layer does not exist, it is created.

Example:

.M{ layer=cutmarks }

#### 7.8. Scope (scope=)
scope= controls whether marks are generated on front pages, back pages, or both.

Values:

- scope=front (default)

- scope=back

- scope=both

Example:

.M{ scope=both }

This integrates with templates flagged as {... back} in the dataset header. Marks do not depend on back templates, but their rendering pass does.

#### 7.9. Examples
**Basic cut marks with default style**

{A4}.L{3x3 g=2}.M{}

**Using a style element**

{A4}.L{p=3x3}.M{ mk_cut }

**Overlay style using a group**

{A4}.L{p=3x3}.M{ mk_cut_stack len=\[3 2\] }

**Marks on both front and back**

{A4}.L{3x3}.M{ mk_cut len=3 scope=both }

## 8. Dataset Section ({{}} in cell A1)
A dataset section may begin with a **marker cell in column A**.\
This marker defines the dataset boundaries and may declare the template bounding-box reference.\
\[\[ esta seccion hay que retocarla entera, con el nuevo funcionamiento, indicando para qué sirven los template_bbox, cual es el principal y por qué se usa, importancia de la ubicacion en el z-order, etc, los fit_anchor en las celdas de {template_id} \]\]

### 8.1. Syntax
The dataset marker uses double braces:

{{template_bbox=id_main}}

{{t=id_main}}

{{id_main}}\
id_main (only for first section)

Where:

- t is the template bounding-box parameter.

- Lists are space-separated, as in the rest of PnPInk.

### 8.2. Defaults and Omissions
Following the general rules of PnPInk syntax, several elements may be omitted:

- If the dataset contains **a single section**, the outer {{ }} may be omitted.

- If the dataset uses **a single template**, the list brackets \[\] may be omitted.

- Since t is the **default parameter**, it may also be omitted.

Therefore, for a single dataset with a single template, it is sufficient to place the bounding-box ID in **row 1, column A**:

bbox_rect

This is equivalent to:

{{t=bbox_rect}}

### 8.3. Headers and Rows
- The marker row is also the **header row** of the dataset(headers start at **column B** of the same row).

- Dataset rows are all subsequent rows until another dataset marker is found, or\
  end of file.

### 8.4. Template Bounding Box
- The declared ID refers to a bounding-box element (typically a rect or path).

- The actual template is the **root-level group \<g\>** containing that element.

- The card size is computed from the declared bounding-box.

### 8.5. Multiple Datasets
When multiple dataset sections are present in the same sheet, each section must start with its own marker using {{ }} with its own headers.\
\
**9. Dataset Header Modifiers: @page, @back**

Dataset headers may declare templates inside {} blocks.\
Header modifiers define **the rendering phase** and **the reference frame** used to place a template.

Two modifiers are available:

- @page — page-anchored rendering

- @back — back-side rendering

They can be combined.

## 9.1. @back — Back-Side Templates (Back Pass)
A template column marked with @back is rendered **only on back pages**, producing output intended for **double-sided printing** (card backs, reverse sides, etc.).

{template_id @back}

Back templates are rendered in a dedicated back pass that:

- creates new pages after all front pages

- reuses the **exact same slot sequence** generated by the front Layout{}

- mirrors page order so front and back pages align correctly for duplex printing

Each back page corresponds to one front page.\
Page order is inverted:

- front page 0 → last back page

- front page 1 → second-to-last back page

- etc.

Slot positions within each page are preserved, ensuring that back elements align exactly with their corresponding front elements when printed double-sided.

The order of rows in the dataset defines the **Z-order** of back elements within each page, in the same way as on front pages.

If a dataset cell for an @back column is empty or contains "-" or "0", that back instance is skipped for that row/slot.

Typical uses include card backs, reverse-side symbols, or any slot-aligned graphics meant to match front content.

## 9.2. @page — Page-Anchored Templates (One Per Page)
A template column marked with @page is anchored to the **page frame** instead of the slot grid.

{template_id @page}

Page-anchored templates:

- do not use the main grid or slot bounding boxes

- are positioned relative to the page frame after applying Page{} borders and margins

- are rendered **once per page\**

Placement uses the standard Fit / Anchor system.

Each dataset row provides a **slot selector** in the cell value (e.g. ~8\[-5\]).\
The referenced slot determines **which page** the template belongs to, while the remaining syntax defines the Fit / Anchor parameters.

The order of rows in the dataset defines the **Z-order** of page-anchored elements on that page.

If multiple rows attempt to place the same page-anchored template on the same page, only the first one is rendered; subsequent attempts are skipped and a warning is logged.

If the dataset cell is empty or contains "-" or "0", no placement occurs.

Typical uses include:

- page titles, footers, or page numbers (text)

- decorative page overlays or frames

- background images or partial page backgrounds

- solid-color backgrounds implemented as SVG shapes

- external images or SVGs referenced via sources

## 9.3. Combining @page and @back
The modifiers may be combined to produce page-anchored elements on back pages.

{template_id @page @back}

Such templates:

- are anchored to the page frame

- are rendered **once per back page\**

- use slot selectors to determine page membership

- are placed on the mirrored back page corresponding to the referenced front page

This ensures that page-level elements on the back side align correctly with the front when printed double-sided.

## 9.4. Combined Example
main_id, front_img, {card_back @back}, back_img, {page_label @page}, label_text, {back_bg @page @back}, bg_img

{A4}.L{3x3 g=2}, f01, b01, img01, ~1\[-5\], "Page {page}", ~1, bg01

In this example:

- front_img follows the layout grid.

- card_back is rendered on back pages, aligned slot-by-slot with the front.

- page_label places a text label once per front page.

- back_bg places a background image once per back page.

- Dataset row order controls the Z-order of all rendered elements.

**10. Advanced\**
\
**10.1 Split boards**

What happens if the template is larger than the target page\
The pipeline switches to **split‑board mode**: the template is cut into tiles and each tile is placed on a page.

It reuses the same Layout/Marks/Fit‑Anchor semantics and margins.

0x0 pattern auto picks the orientation with fewer pages, tiles are centered in the inner frame, and marks use the tile bounds.
