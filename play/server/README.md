# PnPInk Play Server

This directory contains the versioned source of the lightweight local Play
server. It uses SQLite for rooms and complete branched game history, and runs
compiled `bz-core` game bundles through MiniRacer.

The runtime stores the current authoritative state compressed with zlib. Its
branched history uses normalized Boardzilla entity deltas between periodic
compressed snapshots, so player views remain exact without repeating the full
JSON document for every action.

The history schema is intentionally incompatible with the earlier prototype.
Start the server with a new database when upgrading to this format.

Runtime databases, Python environments and vendored wheels are intentionally
not versioned here.

## Development

```bash
python -m unittest discover play/server/tests
python -m boardzilla_lite --games GAME_DIRECTORY --database data/play.db
```
