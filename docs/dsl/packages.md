# ZVG and PNP Packages
PnPInk supports two ZIP-based package formats for portable projects:

- `.zvg`: package the current SVG project and local assets for reproducible sharing.
- `.pnp`: package a lightweight project intended to regenerate content from dataset/web sources on open.

## Where They Appear in Inkscape

- `File > Open` can open `.zvg` and `.pnp`.
- `File > Save As` / `Save a Copy` can export `.zvg` and `.pnp`.

## Import Extraction Folder

When opening a `.zvg` or `.pnp`, PnPInk extracts package contents into a dedicated folder:

- folder path: same directory as the package file,
- folder name: package filename without extension.

Example:

- package: `MyDeck.pnp`
- extracted to: `MyDeck/` (contains SVG, manifest, CSV, assets, etc.)

This keeps assets from different packages isolated and avoids cross-package mixing.

## Package Contents

Typical package structure:

- One main SVG in ZIP root.
- Optional CSV dataset.
- Optional `manifest.json`.
- Local assets (typically under `assets/`).

Defaults work without manifest:

- If there is one SVG in root, it is used as main document.
- CSV is expected with the same base name as the SVG.
- Assets are expected under `assets/`.

If multiple SVG files are included, set `manifest.json` with the `svg` field to disambiguate.

## ZVG vs PNP Behavior

- `.zvg` favors reproducibility:
  - includes referenced local assets,
  - includes local cached downloadable assets when referenced.

- `.pnp` favors minimal payload:
  - includes required local project assets,
  - skips cache-like downloaded files when possible,
  - may run DeckMaker on import when requested by manifest (`run_deckmaker_on_import`).

## Compression Policy

- Images and PDFs are stored without deflate recompression:
  - `.png`, `.jpg`, `.jpeg`, `.webp`, `.pdf`
- Other files are compressed with deflate (level 9).

## Recommended Use

- Use `.zvg` to archive/share an exact visual state.
- Use `.pnp` to share a compact, regeneration-oriented project.
