# PnPInk Play

`pnpink-play` turns a declarative game package into a playable Boardzilla table.
The package describes components, zones, initial setup, visibility, layout, and
the generic operations players may perform. No game-specific TypeScript is
required for sandbox-style prototypes.

## Development

```bash
npm install
npm run typecheck
npm test
npm run build
```

The included `play.json` demonstrates a shared table, deck and discard pile,
private player hands, public player areas, hidden cards, drawing, moving,
discarding, flipping, shuffling, and ending a turn.

## Manifest

Every package contains `play.json` plus the image files referenced from it.
Coordinates are percentages of the game canvas. Player zones may define
different rectangles for their owner and their opponents, so every player sees
their own area in the preferred position.

The runtime intentionally contains only universal tabletop operations. Rules,
validation, scoring, and automation can later be layered on top without
changing the component or setup format.
