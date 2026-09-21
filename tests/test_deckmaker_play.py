import json
from pathlib import Path

import deckmaker_play


def test_play_assets_are_content_addressed_and_deduplicated(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "card1.png").write_bytes(b"same-front")
    (assets / "card2.png").write_bytes(b"same-front")
    (assets / "back.png").write_bytes(b"shared-back")
    result = {
        "results": [
            {"name": "one", "front": "card1.png", "back": "back.png"},
            {"name": "two", "front": "card2.png", "back": "back.png"},
        ]
    }

    deckmaker_play._content_address_play_assets(assets, result)

    first, second = result["results"]
    assert first["front"] == second["front"]
    assert first["back"] == second["back"]
    assert len(list(assets.glob("*.png"))) == 2
    stored = json.loads((assets / "items.json").read_text(encoding="utf-8"))
    assert stored["items"] == result["results"]


def test_syncs_assets_into_runtime_game_linked_to_template(tmp_path: Path):
    runtime = tmp_path / "runtime"
    target_game = runtime / "games" / "poker"
    target_game.mkdir(parents=True)
    (target_game / "game.json").write_text(json.dumps({
        "name": "poker",
        "pnpinkTemplate": "poker_deck.svg",
    }), encoding="utf-8")
    source_assets = tmp_path / "source-assets"
    source_assets.mkdir()
    (source_assets / "front.png").write_bytes(b"front")
    (source_assets / "back.png").write_bytes(b"back")
    (source_assets / "items.json").write_text(json.dumps({
        "schema": 2,
        "items": [{"id": "ace", "front": "front.png", "back": "back.png"}],
    }), encoding="utf-8")

    result = deckmaker_play._sync_configured_runtime_game(
        runtime, tmp_path / "poker_deck.svg", source_assets
    )

    assert result == (target_game, "poker")
    assert (target_game / "assets" / "front.png").read_bytes() == b"front"
    assert json.loads((target_game / "assets" / "items.json").read_text(encoding="utf-8"))["items"][0]["id"] == "ace"
