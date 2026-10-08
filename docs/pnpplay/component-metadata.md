# Component Metadata

PnPInk can attach gameplay meaning to the components it generates. PnPPlay reads that information directly, so the same dataset that creates a card, tile or token can also describe what it is and how a game may use it.

Semantic columns begin with `$`. Unlike ordinary headers, they do not target an SVG ID and do not modify the drawing. They are expanded with the rest of the row and exported to `items.json`.

## Core columns

| Header | Purpose | Example |
|---|---|---|
| `$id` | Stable component identifier and export key | `king-hearts` |
| `$type` | Physical component type | `card` |
| `$name` | Human-readable label | `King of Hearts` |
| `$role` | One or more reusable behaviours | `playing-card scoring-card` |
| `$tags` | Free classifications used by filters | `royal fire` |

The initial standard types are `card`, `tile`, `token`, `die`, `pawn`, `meeple`, `cube`, `marker`, `board`, `mat` and `area`. PnPPlay also keeps the existing `chip` and `d6` aliases.

## Component IDs and separate backs

`$id` is the primary key used by PnPPlay. If the dataset has no `$id` column, PnPInk creates stable IDs from the template, dataset row and iterator variant. If the column exists, only generated components whose `$id` cell is not empty are exported. This makes it possible to keep helper or print-only rows in the same dataset.

A back drawn in a separate dataset row uses the component ID followed by `@back`:

<div class="csv-dataset" markdown>

| card_bbox | `$id` | title |
|---|---|---|
|  | unit-01 | Unit 01 |
|  | unit-01@back | Unit back |

</div>

One back can serve a whole family of components by using their common prefix. For example, `unit@back` is assigned to `unit-01`, `unit-02` and every other exported ID beginning with `unit`. An exact `unit-02@back` definition overrides the common back for `unit-02`; otherwise PnPInk chooses the longest matching prefix. Rows ending in `@back` provide artwork only and are not exported as playable components.

`$role` describes what a component does rather than what it physically is. Typical roles include `playing-card`, `action-card`, `resource-card`, `terrain-tile`, `deck`, `discard`, `hand`, `tableau`, `market`, `score-track` and `resource-supply`. Roles are open-ended: modules can introduce more without changing the dataset grammar.

## Properties

Every other `$...` column becomes an entry in the component's `properties` object. The first shared vocabulary is:

```text
$group  $value  $strength  $points  $cost
$suit   $rank   $category
```

You can add game-specific properties without registering them first, for example `$terrain`, `$edges`, `$resource`, `$grid`, `$rows` or `$columns`.

Numbers and the values `true`, `false`, `yes`, `no`, `null` and `none` are exported with their corresponding JSON types. Other values remain text.

## Basic dataset

<div class="csv-dataset" markdown>

| card_bbox | `$id` | `$type` | `$role` | `$tags` | `$suit` | `$rank` | `$strength` | title |
|---|---|---|---|---|---|---|---:|---|
|  | king-hearts | card | playing-card | royal scoring | hearts | king | 13 | King of Hearts |

</div>

The ordinary `title` column still changes the SVG object named `title`. The `$...` columns only describe the generated component.

Inside text and snippets, semantic values use the existing variable syntax without repeating the declaration marker. For example, the value of `$suit` is available as `${suit}`.

## Iterators

Semantic columns use the normal [iterator grammar](../dsl/iterators.md). Iterators at the same level advance together:

<div class="csv-dataset" markdown>

| card_bbox | `$rank` | `$strength` | title |
|---|---|---:|---|
|  | `*[ace 2 3 king]` | `*[14 2 3 13]` | `*[Ace 2 3 King]` |

</div>

This produces four components whose rank, strength and title remain synchronized.

Quotes used to keep spaces inside one iterator item are syntax only. For example, `*["A 1" "B 2"]` exports the values `A 1` and `B 2`, without quotation marks.

Different iterator levels retain their current cartesian behaviour. A complete deck can therefore be described as:

<div class="csv-dataset" markdown>

| card_bbox | `$type` | `$role` | `$rank` | `$suit` |
|---|---|---|---|---|
|  | card | playing-card | `*[ace 2 3 4 5 6 7 8 9 10 jack queen king]` | `**[hearts diamonds clubs spades]` |

</div>

Level `*` walks through the ranks and level `**` combines every rank with every suit. Other visual columns can use the same levels.

Parenthesized iterator items remain multivalues. This is useful for tags:

```text
*[(royal fire) (common water) (attack magic)]
```

Each parenthesized group belongs to one generated component. Tags may also be separated with commas. Use hyphens or quotes for a tag that must remain a single value, such as `victory-points` or `"victory points"`.

## Copies and exported items

Metadata is resolved after iterator expansion. Each variant receives its own final values, while physical copies of the same variant share those values. Copy count continues to use column A; there is no separate `$copies` property.

PnPInk exports additive semantic fields alongside the asset information:

```json
{
  "id": "king-hearts",
  "name": "King of Hearts",
  "label": "King of Hearts",
  "type": "card",
  "roles": ["playing-card"],
  "tags": ["royal", "fire"],
  "properties": {
    "suit": "hearts",
    "rank": "king",
    "strength": 13,
    "points": 10
  },
  "copies": 2,
  "source": {"dataset": 1, "row": 4, "variant": 13}
}
```

`source` is the only rendering trace stored in the manifest. Physical item indices are internal to the generated SVG and are not exported. Cards that share an identical back also reference the same back asset.

## PnPPlay filters

PnPPlay preserves `type`, `roles`, `tags` and `properties` on every component. An area can restrict what it accepts with a short type filter:

```yaml
accepts: card
```

Or combine semantic criteria:

```yaml
accepts:
  types: [card]
  roles: [resource-card]
  tags: [tradeable]
  properties:
    suit: hearts
```

`types` and `roles` accept a component when at least one listed value matches. Every listed tag and property must match. Omitting `accepts` preserves the current unrestricted behaviour.
