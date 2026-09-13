from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _asset_path(root: Path, source_directory: Path, value: str) -> str:
    target = (source_directory / value).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Piece asset escapes the game directory: {value}")
    return target.relative_to(root).as_posix()


def _load_play_manifest(root: Path) -> dict[str, Any] | None:
    if (root / "play.yaml").is_file():
        manifest = yaml.safe_load((root / "play.yaml").read_text(encoding="utf-8"))
    elif (root / "play.json").is_file():
        manifest = json.loads((root / "play.json").read_text(encoding="utf-8"))
    else:
        return None
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid play manifest in: {root}")
    pieces = manifest.get("pieces")
    if not isinstance(pieces, dict) or "source" not in pieces:
        return manifest
    source_path = (root / str(pieces["source"])).resolve()
    if root not in source_path.parents or not source_path.is_file():
        raise ValueError(f"Piece source not found inside game: {pieces['source']}")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    items = source.get("items") if isinstance(source, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"Piece source has no items array: {source_path}")
    defaults = pieces.get("initial", {})
    expanded = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or not item.get("front"):
            raise ValueError(f"Invalid piece #{index} in: {source_path}")
        name = str(item.get("id") or item.get("name") or f"piece-{index}")
        piece = {
            "id": name,
            "label": str(item.get("label") or item.get("name") or name),
            "copies": int(item.get("copies", 1)),
            "front": _asset_path(root, source_path.parent, str(item["front"])),
            "initial": {**defaults, **item.get("initial", {})},
        }
        if item.get("back"):
            piece["back"] = _asset_path(root, source_path.parent, str(item["back"]))
        expanded.append(piece)
    extras = pieces.get("extra", [])
    if not isinstance(extras, list):
        raise ValueError("pieces.extra must be a list")
    for index, item in enumerate(extras, 1):
        if not isinstance(item, dict) or not item.get("id") or not item.get("initial"):
            raise ValueError(f"Invalid extra piece #{index}")
        piece = dict(item)
        if piece.get("front"):
            piece["front"] = _asset_path(root, root, str(piece["front"]))
        if piece.get("back"):
            piece["back"] = _asset_path(root, root, str(piece["back"]))
        expanded.append(piece)
    manifest["pieces"] = expanded
    return manifest


@dataclass(frozen=True)
class GameDefinition:
    name: str
    friendly_name: str
    root: Path
    server_bundle: Path
    ui_directory: Path
    play_manifest: dict[str, Any] | None
    min_players: int
    max_players: int
    default_players: int

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "friendlyName": self.friendly_name,
            "minPlayers": self.min_players,
            "maxPlayers": self.max_players,
            "defaultPlayers": self.default_players,
        }


class GameRegistry:
    def __init__(self, roots: list[Path]):
        self._games: dict[str, GameDefinition] = {}
        for root in roots:
            self._load_root(root.expanduser().resolve())

    def _load_root(self, root: Path) -> None:
        if (root / "game.json").is_file():
            self._load(root)
            return
        manifests = sorted(root.glob("*/game.json"))
        if not manifests:
            raise ValueError(f"No PnPInk Play games found in: {root}")
        for manifest in manifests:
            self._load(manifest.parent)

    def _load(self, root: Path) -> None:
        manifest_path = root / "game.json"
        if not manifest_path.is_file():
            raise ValueError(f"PnPInk Play manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        game = manifest["game"]
        ui = manifest["ui"]
        game_root = (root / game.get("root", ".")).resolve()
        ui_root = (root / ui.get("root", ".")).resolve()
        play_manifest = _load_play_manifest(root)
        players = play_manifest.get("players", {}) if play_manifest else {}
        player_count = players.get("count")
        min_players = int(player_count if player_count is not None else players.get("min", manifest["minPlayers"]))
        max_players = int(player_count if player_count is not None else players.get("max", manifest["maxPlayers"]))
        definition = GameDefinition(
            name=manifest["name"],
            friendly_name=manifest.get("friendlyName", manifest["name"]),
            root=root,
            server_bundle=(game_root / game["out"]).resolve(),
            ui_directory=(ui_root / ui["outDir"]).resolve(),
            play_manifest=play_manifest,
            min_players=min_players,
            max_players=max_players,
            default_players=int(player_count if player_count is not None else manifest.get("defaultPlayers", min_players)),
        )
        if not definition.server_bundle.is_file():
            raise ValueError(f"Game bundle not built: {definition.server_bundle}")
        if not definition.ui_directory.is_dir():
            raise ValueError(f"UI bundle not built: {definition.ui_directory}")
        if definition.name in self._games:
            raise ValueError(f"Duplicate game name: {definition.name}")
        self._games[definition.name] = definition

    def get(self, name: str) -> GameDefinition:
        try:
            return self._games[name]
        except KeyError as exc:
            raise LookupError(f"Unknown game: {name}") from exc

    def all(self) -> list[GameDefinition]:
        return sorted(self._games.values(), key=lambda game: game.friendly_name.lower())
