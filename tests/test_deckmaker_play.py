import json
from pathlib import Path

import deckmaker_play


def test_find_runtime_accepts_shared_dependency_launcher(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "pnpplay"
    (runtime / "server" / "boardzilla_lite").mkdir(parents=True)
    (runtime / "launch.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(deckmaker_play, "_runtime_candidates", lambda: [runtime])

    assert deckmaker_play.find_runtime() == runtime.resolve()


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


def test_publishes_assets_for_runtime_game_linked_to_template(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    target_game = runtime / "games" / "poker"
    target_game.mkdir(parents=True)
    (target_game / "game.json").write_text(json.dumps({
        "name": "poker",
        "pnpinkTemplate": "poker_deck.svg",
        "pnpinkAssetPack": "examples/poker",
    }), encoding="utf-8")
    store = tmp_path / "store"
    monkeypatch.setenv("PNPPLAY_ASSET_STORE", str(store))
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
    manifest = json.loads((store / "packs/examples/poker/manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["id"] == "ace"
    assert (store / manifest["items"][0]["front"]).read_bytes() == b"front"


def test_syncs_assets_into_configured_development_games(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    games = tmp_path / "development-games"
    target_game = games / "pelusas"
    target_game.mkdir(parents=True)
    (target_game / "game.json").write_text(json.dumps({
        "name": "pelusas",
        "pnpinkTemplate": "medusas.svg",
    }), encoding="utf-8")
    source_assets = tmp_path / "source-assets"
    source_assets.mkdir()
    (source_assets / "front.png").write_bytes(b"medusa")
    (source_assets / "items.json").write_text(json.dumps({
        "schema": 2,
        "items": [{"id": "medusa-1", "front": "front.png", "back": None}],
    }), encoding="utf-8")
    monkeypatch.setenv("PNPPLAY_GAMES", str(games))
    store = tmp_path / "store"
    monkeypatch.setenv("PNPPLAY_ASSET_STORE", str(store))

    result = deckmaker_play._sync_configured_runtime_game(
        runtime, tmp_path / "medusas.svg", source_assets
    )

    assert result == (target_game, "pelusas")
    manifest_files = list((store / "packs").glob("**/manifest.json"))
    assert len(manifest_files) == 1
    manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
    assert (store / manifest["items"][0]["front"]).read_bytes() == b"medusa"
