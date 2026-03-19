# 🧩 PnPInk — User Manual
## Introduction
**PnPInk** is a **free, open-source, and cross-platform toolkit** designed to create high-quality *print-and-play* materials for board games — cards, boards, tokens, player aids, and more — directly inside **Inkscape**.

Unlike other generators, **PnPInk is not a separate application but a native extension suite that transforms Inkscape into a complete publishing environment — capable of accompanying creators from the very first prototype designs to the final, fully professional print composition.**

## 1. What PnPInk Does
PnPInk automates the creation of:

- **Card decks** of any size or format.

- **Boards and punchboards**, including **hexagonal or square grids**.

- **Player sheets**, tiles, counters, and rule inserts.

It combines **data-driven generation** (from Google Sheets or CSV files) with **Inkscape’s full graphic power** — gradients, filters, clipping masks, paths, symbols, and layers — to produce editable, print-ready layouts with pixel-perfect precision.

## 2. Key Principles
| **Principle** | **Description** |
|----|----|
| **Open Source** | PnPInk is released under an open license. All files remain accessible, editable, and transparent. |
| **Cross-Platform** | Works wherever Inkscape runs: Windows, macOS, or Linux. |
| **Non-Destructive** | Everything you generate stays as pure SVG — fully editable afterwards. |
| **Professional Output** | Exports **PDFs with CMYK color**, ICC profiles, and print standards (FOGRA, PDF/X). |
| **Accessible & Visual** | No programming required — all actions are done through menus, dialogs, and clear visual feedback. |

## 3. Power of Inkscape Integration
Because PnPInk is built *inside* Inkscape, it inherits all of its advanced features:

- **Full vector control:** Bézier paths, shapes, and text on path.

- **Complex styling:** gradients, blurs, blend modes, and filters.

- **Image management:** linked or embedded images, with live previews.

- **Multi-page support:** each layout can produce complete, paginated projects.

- **Universal format compatibility:** SVG, PDF, PNG, EPS, DXF, and more.

This makes PnPInk suitable for both quick prototypes and professional offset printing.

## 4. Intelligent Data and Automation
PnPInk can read structured data directly from:

- **Google Sheets** (live connection with authentication), or

- **CSV files** exported from any spreadsheet program.

Each row in the data corresponds to a card, tile, or component.\
PnPInk replaces variables automatically — names, values, icons, or images — to generate all items in a single command.

You can also control elements through short, **intuitive syntax rules**:

- define margins, spacing, and grid size ({A4 3x3 \[5 4\]}),

- position elements with anchors (~anchor:top-center),

- adjust scaling and fit (~fit:\[2\]).

This nomenclature is **compact, logical, and extremely powerful**, allowing advanced layouts without complex scripting.

## 5. Extensible by Design
PnPInk includes an **internal Python engine** that lets power users create custom behaviors, filters, or generation logic.\
At the same time, the system remains compatible with **external programming** through Google Sheets formulas, scripts, and data manipulation — perfect for dynamic prototypes or large datasets.

Together, these layers make PnPInk both **simple for beginners** and **limitless for experts**.

## 6. Output Quality and Professional Printing
All final projects can be exported as:

- Editable SVG files for on-screen review.

- **Print-ready PDFs** with CMYK color conversion and **bleeds, margins, and crop marks**.

- Optional **PDF/X compliance** for professional pre-press workflows.

This ensures consistent color reproduction and compatibility with professional printers and services.

## 7. Typical Use Cases
- Design and print your own card game in minutes.

- Generate hundreds of cards from a single Google Sheet.

- Build modular or hexagonal boards with automatic alignment.

- Create color-managed PDFs for offset printing.

- Share open, editable project files with collaborators worldwide.

## 8. Why PnPInk
PnPInk bridges the gap between **creativity** and **production**.\
It combines the artistic freedom of Inkscape with the precision and automation of modern data tools — delivering a unique environment where *design, logic, and craft meet*.

# PnPInk — Basic Workflow (User Guide)
## 1) What you need
- **Inkscape** (Windows, macOS, or Linux).

- **PnPInk extension** installed.\
  After installing, **new menus** will appear in Inkscape to create cards, boards, and print-ready pages.

## 2) Your first project: the quick overview
1.  **Design the template** in Inkscape\
    Create a single “master” card (or board). This can be a full finished design, or a **generic frame** with placeholders (title, number, icon, art frame, background, etc.). This will be your **TEMPLATE**.

2.  **Name things clearly\**
    Give each element a meaningful **ID** (e.g., title, cost, icon, art, bg). IDs let PnPInk know **what to replace** later.

3.  **Prepare your data\**
    Use **Google Sheets** or a **CSV** file. Each **row** represents one card (and can include “copies” or quantity). Add columns that match your IDs—plus any other properties you want to control (texts, colors, styles, images, counts, etc.). There’s **no limit** to how many you define.

4.  **Choose a layout\**
    Tell PnPInk how to arrange cards on pages (paper size, grid, margins, gaps). Layouts are written with a **short, intuitive notation** (e.g., A4 3x3\[5 4\] = A4, 3×3 cards, margins/gaps of 5mm/4mm).

5.  **Generate\**
    PnPInk clones the template for each row, replaces variables, positions/adjusts elements with **Anchor & Fit**, arranges them into **multi-page** documents, and prepares **print-ready PDFs** (including **CMYK/ICC** for professional printing).

## 3) IDs: how elements are matched to data
- **What they are:** IDs are the “names” of objects in your template (texts, shapes, groups, images).

- **How to use them:** If your CSV/Sheet has a column **title**, and your template has a text object with ID **title**, PnPInk will insert that row’s text there.

- **Best practices:\**

  - Keep IDs **short and descriptive** (title, subtitle, icon, art, frame, bg).

  - One purpose per ID (avoid duplicates unless you intend to repeat the same value).

  - Use **groups** with an ID when multiple items belong together (e.g., a framed art block).

## 4) Layouts & pagination (short notation)
- **What layouts do:** They control paper size, card size/quantity per page, margins, and gaps.

- **Notation examples:\**

  - A4 3x3 → A4 paper, 3 columns × 3 rows.

  - A4 3x3\[5 4\] → add margins/gaps (compact, CSS-like order).

  - Orientation toggles and presets let you switch between portrait/landscape and standard card formats.

- **Why it matters:** One template can produce **hundreds of cards**, arranged automatically into **clean, paginated sheets**—ready for cutting or die-cut.

## 5) Anchor & Fit (precise placement without manual nudging)
- **Anchor** = where the element aligns inside its target area (e.g., top-center, center, bottom-right).

- **Fit** = how it scales to **fill or respect** the frame:

  - none (no scaling),

  - contain (keep ratio, fit inside),

  - cover (keep ratio, fill completely, may crop),

  - stretchXY (scale independently).

- **Margins and bleed:** You can add **margins or negative margins** (e.g., \[2\] = 2mm padding on all sides; \[-1\] = extend 1mm beyond for bleed).

- **Result:** Titles center perfectly, icons snap to corners, images fill art frames, and backgrounds expand to edges—**consistently** across every card.

## 6) Inline icons (type the name, get the icon)
- **What it is:** Write icon names directly in text (e.g., :sword:, :lightning:) and PnPInk replaces them with **vector icons**—styled to match your fonts and colors.

- **Scale & style:** Icons live **inside the text flow**, so they line up with your typography and inherit styles.

- **Big libraries:** PnPInk can connect to **large online icon sets (200k+ icons)** that you can **call by name**. No manual importing—just type the name.

- **Use cases:** Resource symbols, status icons, action markers, rarity pips—**instantly consistent** across the whole project.

## 7) Snippets & variables (write once, reuse everywhere)
- **Variables:** In text fields, you can insert **placeholders** tied to your data (names, numbers, colors, costs, etc.). When you generate, they’re replaced automatically.

- **Snippets:** Think of them as **reusable text chunks** or micro-templates. Example use cases:

  - A standardized **rules block** you reuse on multiple cards.

  - A **cost line** that composes icons + numbers based on data.

  - Language variants or conditional content (e.g., add a keyword only if a value exists).

- **Benefit:** You keep your template **clean and consistent**, even for complex sets.

## 8) Asset sources (images, art, symbols)
PnPInk can pull artwork and symbols from multiple places:

- **Local files** you link in your data (e.g., art=images/dragon_03.png).

- **Document symbols** (shared elements duplicated across cards).

- **Sprite sheets or multi-page packs** (see “wrappers” below).

- **Online icon/name catalogs** (call by name, automatically resolved).

**Tip:** Always keep a simple **asset folder** structure; name files clearly to match your rows.

## 9) Wrappers & resource packs (advanced, optional)
- **Wrappers** are optional **plugins** that let you tap into **multi-page sources**:

  - a **PDF** with many pages of art,

  - a **sprite sheet** with many tiles,

  - or a **card set** from a catalog.

- You can reference **“page 14”**, or **“tile 5×7”**, or **“card SKU”** directly in your data.

- This is ideal for **large libraries** or **third-party packs** where manual importing would be tedious.

## 10) Style control without limits
- Because everything happens **inside Inkscape**, you have the full power of:

  - **Gradients, filters, blends, masks**,

  - **Layers and symbols**,

  - **Precise vector paths** and **text styling**.

- PnPInk simply **automates** the boring part; you keep **artistic control** at all times.

## 11) Professional output (CMYK & print standards)
- Export **PDFs with CMYK color**, **ICC profiles**, and optional **PDF/X** compliance for offset printing.

- Add **bleed, crop marks, and safe margins** automatically.

- Keep the editable **SVGs** for future updates, translations, or expansions.

## 12) Typical end-to-end flow (checklist)
1.  Open Inkscape → confirm **PnPInk menus** are visible.

2.  Create your **template** (card/board) and assign **clear IDs**.

3.  Prepare your **Google Sheet or CSV**:

    - One **row per card** (or copies).

    - Columns named after your **IDs** + any extra properties (e.g., icon, art, rarity, color, bg, copies, layout, etc.).

4.  Choose a **layout** (paper size, grid, margins).

5.  Use **Anchor & Fit** for placement and scaling.

6.  Write **inline icons** right inside text by **name**.

7.  Use **snippets/variables** for reusable blocks and smart content.

8.  (Optional) Connect to **online icon libraries** and **wrappers** for multi-page asset packs.

9.  **Generate** → review pages → export **PDF (CMYK)** for print.
