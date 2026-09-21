# PnPPlay Server

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

Open `/designer/GAME_NAME` from the server computer to edit a declarative
`play.yaml`. The Game Designer builds its lil-xgui property panels from
`web/play.schema.json`, previews area changes immediately, saves atomically,
and notices external YAML edits. Designer write routes are unavailable through
LAN connections; remote devices remain play-only clients.

## Raspberry Pi

The Node-free Raspberry Pi OS installer, `systemd` service and atomic updater
are documented in [`../deploy/raspberry/README.md`](../deploy/raspberry/README.md).
The database is stored separately from application releases, so updates never
replace users, rooms or history.
