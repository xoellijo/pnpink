# Asset Stores

PnPInk publishes playable components as **asset packs**. A pack contains the current component metadata, while its images are kept as immutable objects identified by their SHA-256 content hash. PnPPlay games reference the pack instead of receiving a private copy of every image.

The first available store is the local library:

```text
~/.pnpplay/stores/local/
  catalog.json
  objects/sha256/ab/<hash>.png
  packs/pnpink/my-deck/
    manifest.json
```

Publishing the same `$id` replaces that component everywhere the pack is used. Changing one card adds only that image; every unchanged front and shared back keeps the same object. Use a different `$id` when a component must remain independent.

PnPInk publishes to this library when **PnPPlay → Regenerate, sync & open Game Designer** is used. Set `PNPPLAY_ASSET_STORE` to use a different local directory.

PnPPlay references a pack from the game YAML:

```yaml
pieces:
  source:
    store: local
    pack: pnpink/my-deck
  extends: card
  initial: {area: deck, faceUp: false}
```

The manifest is intentionally mutable and simple. The image objects remain immutable and content-addressed, so they can be safely cached and deduplicated. Existing `include`, `exclude` and `overrides` behaviour can be applied by the game without modifying the original pack.

Future personal R2/S3 and shared cloud stores will use the same pack and object model. Credentials belong to the store configuration, never to a game YAML or dataset.
