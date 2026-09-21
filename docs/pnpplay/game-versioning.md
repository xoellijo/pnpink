# Game and Asset Versioning

PnPPlay versions every declarative game automatically. A saved room records the exact game revision it uses, while generated images use content-based filenames. Updating an illustration therefore creates a new URL without duplicating unchanged assets or altering historical states.

The default policy comes from `standard.yaml` and applies to every game:

```yaml
updates:
  mode: versioned
  assets: live
  components: new-games
  rules: pinned
```

A game may include only the values it wants to override.

## Policies

`assets: live` updates illustrations in running games when component IDs, quantities and rules remain unchanged. Clients reload the game frame automatically, and pieces resolve their new images through their stable component ID.

`components: new-games` means that added, removed or duplicated pieces are used by newly created rooms. Existing rooms remain attached to their previous revision because PnPPlay cannot safely guess where a new piece belongs or how an removed piece should be handled.

`rules: pinned` keeps areas, setup and rules exactly as they were when a room was created.

Each policy accepts `live`, `new-games` or `pinned`. `live` is intentionally dangerous for structural changes: it points existing rooms to the new definition without rewriting their state.

## Latest-only games

A rapidly changing prototype can discard incompatible rooms automatically:

```yaml
updates:
  mode: latest-only
```

When components or rules change, PnPPlay deletes every room of that game using an older revision. Asset-only changes remain compatible and follow the `assets` policy.

!!! warning "Destructive prototype mode"
    `latest-only` permanently deletes incompatible saved rooms and their history. Use the default `versioned` mode for published games or tests that must remain reproducible.

## Stable IDs

Keep `$id` unchanged when editing artwork, labels or other presentation details:

```text
$id = poker-ace-hearts
```

PnPPlay uses this logical ID to match a piece in a saved state with its current visual asset. Copies receive deterministic per-component instance IDs, so adding an unrelated component does not rename existing instances.

## Stored revisions

Prepared manifests are stored under `.pnpplay/versions/<revision>/play.json`. Generated assets remain in the game's `assets` directory under content hashes. Old hashes are retained while old game revisions may still reference them.

Changes to an external `items.json` source are detected even when `play.yaml` itself does not change.

## Deckmaker and Game Designer

Deckmaker's **PnPPlay** menu is intentionally limited to authoring actions:

- **Sync & open Game Designer** regenerates `items.json` and changed assets, then opens the web designer.
- **Open Game Designer** opens the existing project without rendering assets again.
- **Open Lobby** and **Create New Room** move directly to playtesting.

After the first synchronization, Deckmaker does not rewrite `play.yaml`. Layouts, rules and update policies edited in the Game Designer therefore survive later asset generations.

The web Game Designer owns revision administration. Its **Updates & revisions** tree configures the inherited policies, shows saved-room usage, restores previous revisions and deletes unused ones. A revision used by any saved room cannot be deleted.
