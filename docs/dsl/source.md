# Source
```txt
@{...} creates a source object from an external resource (file, icon, or URL), then uses it as a renderable object in the document.
```
Use Source when content comes from outside the template and must be resolved at generation time.

## Syntax
All three forms below are equivalent entry points into the same source resolver pipeline.

```txt
Source{source_ref} or S{source_ref} or @{source_ref}
```

The `source_ref` can be:

- a path to a local file,
- an iconify reference,
- a web URL,
- or a virtual source (Wikimedia Commons, Pixabay, Openclipart).

## Local File Sources
Use local files for stable, reproducible builds where assets are versioned with the project.

```txt
@{ relative/or/absolute/path.png }
@{ C:\path\to\image.png }   # Windows
@{ ~/images/token.png }     # Linux/macOS
```

Local sources support environment/home expansion:

- Windows: `%USERPROFILE%`
- Linux/macOS: `$HOME`, `${HOME}`, `~`

## Iconify Sources
Use icon sources for semantic symbols (costs, resources, status icons) without managing local files manually.

```txt
@{ icon://icon_set/icon_name }
```

Examples:

```txt
@{ icon://noto/heart-suit }
@{ icon://mdi/account }
@{ icon://cat }   # uses default set (noto)
```

There is a default snippet definition mapping to the `noto` icon set:

```txt
:Ic(icon) -> @{ icon://noto/icon }
```

Notes:

- Icons are cached as SVG symbols in `<defs>`.
- If `iconify.py` is not available, a placeholder is created.
- To force a re-download, remove the symbol from `Object > Symbols` (Shift+Ctrl+Y).

## Web Sources (HTTP/HTTPS)
Use web sources when assets are remote and can be cached at generation time.

```txt
@{ https://... }
@{ http://... }
```

Web sources are **downloaded and cached** into the `assets` folder next to the SVG (or project root).
If the download fails, a placeholder symbol is created.

## Virtual Sources
Virtual sources map high-level search expressions to real downloadable URLs.
They are useful for exploratory workflows and rapid prototyping.

PnPInk supports virtual sources that resolve to real URLs:

```txt
@{ wkmc://query/size }
@{ pxby://query/size }
@{ oclp://query/size }
```

- `wkmc://` (Wikimedia Commons) supports:
 - search text: `wkmc://query/size`
 - specific file: `wkmc://File:Name.ext/size`
 - category files: `wkmc://Category:Name/size`
- `pxby://` (Pixabay) supports:
 - normal search list: `pxby://query/size`
 - direct lookup for no-space query when possible (single hit / id).
- `oclp://` (Openclipart) supports:
 - normal search list: `oclp://query/size`
 - specific image by id/detail: `oclp://id:12345/size` (resolved internally via `https://openclipart.org/detail/12345/...`)

Size accepts:

- presets: `tiny`, `small`, `medium`, `large`, `xlarge`, `largest`
- minimum side: `N` (example: `1000`)
- minimum width+height: `WxH` (example: `1000x1000`)

Examples:

```txt
@{ wkmc://"The Ancestral Homes of Britain"/large }
@{ wkmc://"File:Complete_Saxonian_deck.jpg"/1000 }
@{ wkmc://"Category:Complete_decks_of_playing_cards_laid_out"/1000x1000 }
@{ pxby://castle/1200 }
@{ oclp://wolf/large }
@{ oclp://id:24829/largest }
```

Multiple-result virtual sources can be selected outside the source with a 1-based selector:

```txt
@{ wkmc://"The Ancestral Homes of Britain"/medium }[2 4..12 15..26]
*@{ pxby://castle/large }[1..20]
```

Selector notes:

- indices are 1-based,
- out-of-range indices are ignored with warning,
- without selector, if multiple results exist, the first one is used (warning in log).

## Fit and Placement Behavior
After resolution, a source behaves like any other placeable target in Fit/Anchor terms.

A Source is treated as a final placeable object, so you can apply Fit/Anchor operations exactly as with other objects.

## Internal Model
This internal model is important for performance and file size on large projects.

Sources are instantiated as clones of symbols (`<use>`), not inline geometry copies.
This keeps the SVG compact and avoids repeating heavy geometry.

## Spritesheets (@)
Spritesheets allow one asset to provide many addressable frames.
This is useful for token sheets, icon atlases, and catalog-like image packs.

The **Layout** module can also be applied to composite sources, such as spritesheets.

Example definition inside a comment block (before the dataset):

```txt
# @sp1 = @{sheet.png}.Layout{p=3x2^ s=poker g=[4 3]}
```

This creates a spritesheet from `sheet.png` with a 3x2 grid of poker-sized cards, spaced 4 mm horizontally and 3 mm vertically.

Then, you can reference any frame in the dataset as:

```txt
@sp1[14] -> frame 14 (third page, second row)
@sp1[2][1][3] -> page 2, column 1, row 3
@sp1[1..6] -> range selector
@sp1[1 4 7] -> explicit list selector
```

(`^` in the grid reverses numbering direction: first rows, then columns.)

This spritesheet can be used as a source in any dataset, positioned with Fit parameters (e.g. `~i6`) as any other target.

Notes:

- Alias definitions are read from comment lines before dataset rows.
- Frame selectors are 1-based.
- If a frame is out of range, a warning is logged and placement is skipped.
