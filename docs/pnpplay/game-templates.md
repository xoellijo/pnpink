# Game Templates

PnPPlay game templates collect the usual areas, layout and assisted flow of a
family of games. A trick-taking prototype can therefore start with:

```yaml
schema: 1
extends: trick-game
name: My Trick Game

pieces:
  source: assets/items.json
  extends: card
  initial: {area: deck}

setup:
  - shuffle: deck
  - deal: {from: deck, to: hand, count: 7}
```

The standard `trick-game` template provides the deck, table, discard, hands,
trick piles and tableaux, plus seating and `trick-assisted` turns. The YAML may
replace any inherited value. Lists of areas and toolboxes merge by `id`, while
nested values such as `layout` merge recursively.

```yaml
areas:
  - id: deck
    stackTab: {autoHide: false}
  - id: table
    maxDrop: 3
```

Local templates can inherit the standard one and add reusable project defaults:

```yaml
templates:
  my-trick-game:
    extends: trick-game
    turns: {winnerDecider: all}

extends: my-trick-game
```

## Dataset Overrides

An exported deck can remain generic while each game assigns different semantic
properties. `pieces.overrides` maps a property to component-ID glob patterns:

```yaml
pieces:
  source: assets/poker/items.json
  extends: card
  initial: {area: deck}
  overrides:
    points:
      "*": 0
      "*H": 1
      QS: 13
```

Patterns are case-sensitive and are applied in written order. Here every card
starts at zero, Hearts become worth one, and the Queen of Spades becomes worth
thirteen. The resulting value is available as `piece.properties.points` to
generic rules, area filters and future assisted modules. The source JSON is not
modified, so another game can assign a completely different meaning to the same
deck.
