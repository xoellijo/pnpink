# PnPInk Play Server

This directory contains the versioned source of the lightweight local Play
server. It uses SQLite for rooms and complete branched game history, and runs
compiled `bz-core` game bundles through MiniRacer.

The runtime currently stores complete compact JSON snapshots in `sessions` and
`history_nodes`. This source is the baseline before introducing a new,
incompatible compressed history format.

Runtime databases, Python environments and vendored wheels are intentionally
not versioned here.

## Development

```bash
python -m unittest discover play/server/tests
python -m boardzilla_lite --games GAME_DIRECTORY --database data/play.db
```
