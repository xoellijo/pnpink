from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _manifest_path(root: Path) -> Path | None:
    for name in ("play.yaml", "play.json"):
        path = root / name
        if path.is_file():
            return path
    return None


def _manifest_revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_authoring_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    path = _manifest_path(root)
    if path is None:
        raise ValueError(f"Game is not declarative: {root.name}")
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix == ".yaml" else json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid play manifest in: {root}")
    return path, manifest


def _validate_authoring_manifest(root: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != 1:
        raise ValueError("schema must be 1")
    if not str(manifest.get("name", "")).strip():
        raise ValueError("name is required")
    board = manifest.get("board")
    if not isinstance(board, dict) or float(board.get("aspectRatio", 0)) <= 0:
        raise ValueError("board.aspectRatio must be positive")
    areas = manifest.get("areas")
    if not isinstance(areas, list) or not areas:
        raise ValueError("areas must contain at least one area")
    area_ids: set[str] = set()
    toolbox_ids = {str(item.get("id")) for item in manifest.get("toolboxes", []) if isinstance(item, dict)}
    for area in areas:
        if not isinstance(area, dict) or not str(area.get("id", "")).strip():
            raise ValueError("Every area needs an id")
        area_id = str(area["id"])
        if area_id in area_ids:
            raise ValueError(f"Duplicate area: {area_id}")
        area_ids.add(area_id)
        if area.get("scope") not in {"shared", "player"}:
            raise ValueError(f"Area {area_id} has an invalid scope")
        if area.get("visibility") not in {"public", "owner", "hidden"}:
            raise ValueError(f"Area {area_id} has an invalid visibility")
        if area.get("toolbox") and area["toolbox"] not in toolbox_ids:
            raise ValueError(f"Area {area_id} uses unknown toolbox {area['toolbox']}")
        if not isinstance(area.get("layout"), dict):
            raise ValueError(f"Area {area_id} needs a layout")
    pieces = manifest.get("pieces", [])
    if isinstance(pieces, dict):
        source = pieces.get("source")
        if not source:
            raise ValueError("pieces.source is required")
        source_path = (root / str(source)).resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise ValueError(f"Piece source not found inside game: {source}")
        extras = pieces.get("extra", [])
        if not isinstance(extras, list):
            raise ValueError("pieces.extra must be a list")
        pieces_to_check = extras
    elif isinstance(pieces, list):
        pieces_to_check = pieces
    else:
        raise ValueError("pieces must be a list or source definition")
    for piece in pieces_to_check:
        if not isinstance(piece, dict) or not str(piece.get("id", "")).strip():
            raise ValueError("Every component needs an id")
        initial = piece.get("initial")
        if not isinstance(initial, dict) or initial.get("area") not in area_ids:
            raise ValueError(f"Component {piece['id']} uses an unknown initial area")


def _asset_path(root: Path, source_directory: Path, value: str) -> str:
    target = (source_directory / value).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Piece asset escapes the game directory: {value}")
    return target.relative_to(root).as_posix()


def _load_play_manifest(root: Path) -> dict[str, Any] | None:
    if _manifest_path(root) is None:
        return None
    _, manifest = _read_authoring_manifest(root)
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
    cover_path: Path | None

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "friendlyName": self.friendly_name,
            "minPlayers": self.min_players,
            "maxPlayers": self.max_players,
            "defaultPlayers": self.default_players,
            "cover": f"/games/{self.name}/cover" if self.cover_path else None,
        }


class GameRegistry:
    def __init__(self, roots: list[Path]):
        self._games: dict[str, GameDefinition] = {}
        self._manifest_revisions: dict[str, str] = {}
        for root in roots:
            self._load_root(root.expanduser().resolve())

    def _load_root(self, root: Path) -> None:
        if (root / "game.json").is_file():
            self._load(root)
            return
        manifests = sorted(root.glob("*/game.json"))
        if not manifests:
            raise ValueError(f"No PnPPlay games found in: {root}")
        for manifest in manifests:
            self._load(manifest.parent)

    def _load(self, root: Path) -> None:
        definition = self._definition(root)
        if definition.name in self._games:
            raise ValueError(f"Duplicate game name: {definition.name}")
        self._games[definition.name] = definition
        manifest_path = _manifest_path(root)
        if manifest_path:
            self._manifest_revisions[definition.name] = _manifest_revision(manifest_path)

    def _definition(self, root: Path) -> GameDefinition:
        manifest_path = root / "game.json"
        if not manifest_path.is_file():
            raise ValueError(f"PnPPlay manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        game = manifest["game"]
        ui = manifest["ui"]
        game_root = (root / game.get("root", ".")).resolve()
        ui_root = (root / ui.get("root", ".")).resolve()
        play_manifest = _load_play_manifest(root)
        players = play_manifest.get("players", {}) if play_manifest else {}
        player_count = players.get("count")
        cover_value = manifest.get("cover") or manifest.get("image")
        if not cover_value:
            cover_value = next(
                (name for name in ("cover.webp", "cover.png", "cover.jpg", "image.webp", "image.png", "image.jpg") if (root / name).is_file()),
                None,
            )
        if not cover_value and play_manifest:
            cover_value = next(
                (piece.get("front") for piece in play_manifest.get("pieces", []) if isinstance(piece, dict) and piece.get("front")),
                None,
            )
        cover_path = (root / str(cover_value)).resolve() if cover_value else None
        if cover_path and (root not in cover_path.parents or not cover_path.is_file()):
            cover_path = None
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
            cover_path=cover_path,
        )
        if not definition.server_bundle.is_file():
            raise ValueError(f"Game bundle not built: {definition.server_bundle}")
        if not definition.ui_directory.is_dir():
            raise ValueError(f"UI bundle not built: {definition.ui_directory}")
        return definition

    def authoring_manifest(self, name: str) -> tuple[dict[str, Any], str]:
        game = self.get(name)
        path, manifest = _read_authoring_manifest(game.root)
        revision = _manifest_revision(path)
        if self._manifest_revisions.get(name) != revision:
            _validate_authoring_manifest(game.root, manifest)
            self._games[name] = self._definition(game.root)
            self._manifest_revisions[name] = revision
        return manifest, revision

    def save_manifest(self, name: str, manifest: dict[str, Any], revision: str | None) -> tuple[dict[str, Any], str]:
        game = self.get(name)
        path, _ = _read_authoring_manifest(game.root)
        current_revision = _manifest_revision(path)
        if revision is not None and revision != current_revision:
            raise RuntimeError("The manifest changed on disk; reload it before saving")
        _validate_authoring_manifest(game.root, manifest)
        original = path.read_text(encoding="utf-8")
        if path.suffix == ".yaml":
            content = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120)
        else:
            content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        try:
            definition = self._definition(game.root)
        except Exception:
            path.write_text(original, encoding="utf-8")
            raise
        self._games[name] = definition
        next_revision = _manifest_revision(path)
        self._manifest_revisions[name] = next_revision
        return manifest, next_revision

    def get(self, name: str) -> GameDefinition:
        try:
            return self._games[name]
        except KeyError as exc:
            raise LookupError(f"Unknown game: {name}") from exc

    def all(self) -> list[GameDefinition]:
        return sorted(self._games.values(), key=lambda game: game.friendly_name.lower())
