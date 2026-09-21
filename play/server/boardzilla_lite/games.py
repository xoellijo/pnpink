from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


STANDARD_CATALOG_NAME = "standard.yaml"
REVISION_DIRECTORY = ".pnpplay/versions"
CURRENT_REVISION_FILE = ".pnpplay/current.json"


def _manifest_path(root: Path) -> Path | None:
    for name in ("play.yaml", "play.json"):
        path = root / name
        if path.is_file():
            return path
    return None


def _manifest_revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_dependency_revision(root: Path) -> str:
    path = _manifest_path(root)
    if path is None:
        return ""
    digest = hashlib.sha256(path.read_bytes())
    digest.update(_standard_catalog_path().read_bytes())
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix == ".yaml" else json.loads(path.read_text(encoding="utf-8"))
        pieces = manifest.get("pieces") if isinstance(manifest, dict) else None
        source = pieces.get("source") if isinstance(pieces, dict) else None
        if source:
            source_path = (root / str(source)).resolve()
            if root in source_path.parents and source_path.is_file():
                digest.update(source_path.read_bytes())
    except (OSError, ValueError, TypeError):
        pass
    return digest.hexdigest()


def _read_authoring_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    path = _manifest_path(root)
    if path is None:
        raise ValueError(f"Game is not declarative: {root.name}")
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix == ".yaml" else json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid play manifest in: {root}")
    return path, manifest


def _standard_catalog_path() -> Path:
    configured = os.environ.get("PNPPLAY_STANDARD_CATALOG")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"PnPPlay standard catalog not found: {path}")
        return path
    path = Path(__file__).resolve().parents[2] / STANDARD_CATALOG_NAME
    if not path.is_file():
        raise ValueError(f"PnPPlay standard catalog not found: {path}")
    return path


def _standard_catalog() -> dict[str, Any]:
    path = _standard_catalog_path()
    catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or catalog.get("schema") != 1:
        raise ValueError(f"Invalid PnPPlay standard catalog: {path}")
    return catalog


def _standard_types() -> dict[str, Any]:
    path = _standard_catalog_path()
    catalog = _standard_catalog()
    types = catalog.get("types")
    if not isinstance(types, dict):
        raise ValueError(f"PnPPlay standard catalog has no types: {path}")
    return types


def _standard_updates() -> dict[str, Any]:
    updates = _standard_catalog().get("updates", {})
    if not isinstance(updates, dict):
        raise ValueError("PnPPlay standard catalog updates must be an object")
    return deepcopy(updates)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _parent_names(value: Any, context: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return value
    raise ValueError(f"{context}.extends must be a type name or a list of type names")


def _resolved_types(definitions: Any, category: str) -> dict[str, dict[str, Any]]:
    if definitions is None:
        return {}
    if not isinstance(definitions, dict):
        raise ValueError(f"types.{category} must be an object")
    resolved: dict[str, dict[str, Any]] = {}
    resolving: set[str] = set()

    def resolve(name: str) -> dict[str, Any]:
        if name in resolved:
            return resolved[name]
        definition = definitions.get(name)
        if not isinstance(definition, dict):
            raise ValueError(f"Unknown {category[:-1]} type: {name}")
        if name in resolving:
            raise ValueError(f"Circular {category[:-1]} type inheritance: {name}")
        resolving.add(name)
        result: dict[str, Any] = {}
        for parent in _parent_names(definition.get("extends"), f"types.{category}.{name}"):
            result = _deep_merge(result, resolve(parent))
        result = _deep_merge(result, {key: value for key, value in definition.items() if key != "extends"})
        resolving.remove(name)
        resolved[name] = result
        return result

    for type_name in definitions:
        resolve(str(type_name))
    return resolved


def _apply_types(items: list[Any], definitions: dict[str, dict[str, Any]], category: str) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid {category[:-1]} #{index}")
        expanded: dict[str, Any] = {}
        for parent in _parent_names(item.get("extends"), f"{category}[{index}]"):
            if parent not in definitions:
                raise ValueError(f"Unknown {category[:-1]} type: {parent}")
            expanded = _deep_merge(expanded, definitions[parent])
        expanded = _deep_merge(expanded, {key: value for key, value in item.items() if key != "extends"})
        result.append(expanded)
    return result


def _validate_authoring_manifest(root: Path, manifest: dict[str, Any]) -> None:
    manifest = _prepare_manifest(root, manifest)
    if manifest.get("schema") != 1:
        raise ValueError("schema must be 1")
    updates = manifest.get("updates", {})
    if updates.get("mode") not in {"versioned", "latest-only"}:
        raise ValueError("updates.mode must be versioned or latest-only")
    for key in ("assets", "components", "rules"):
        if updates.get(key) not in {"live", "new-games", "pinned"}:
            raise ValueError(f"updates.{key} must be live, new-games or pinned")
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
    if not isinstance(pieces, list):
        raise ValueError("pieces must be a list or source definition")
    for piece in pieces:
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


def _prepare_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = deepcopy(manifest)
    updates = manifest.get("updates", {})
    if updates is None:
        updates = {}
    if not isinstance(updates, dict):
        raise ValueError("updates must be an object")
    manifest["updates"] = _deep_merge(_standard_updates(), updates)
    type_groups = manifest.get("types", {})
    if type_groups is None:
        type_groups = {}
    if not isinstance(type_groups, dict):
        raise ValueError("types must be an object")
    standard_groups = _standard_types()
    merged_groups = _deep_merge(standard_groups, type_groups)
    area_types = _resolved_types(merged_groups.get("areas"), "areas")
    piece_types = _resolved_types(merged_groups.get("pieces"), "pieces")

    areas = manifest.get("areas", [])
    if not isinstance(areas, list):
        raise ValueError("areas must be a list")
    manifest["areas"] = _apply_types(areas, area_types, "areas")

    pieces = manifest.get("pieces")
    if isinstance(pieces, dict):
        source_name = pieces.get("source")
        if not source_name:
            raise ValueError("pieces.source is required")
        source_path = (root / str(source_name)).resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise ValueError(f"Piece source not found inside game: {source_name}")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        items = source.get("items") if isinstance(source, dict) else None
        if not isinstance(items, list):
            raise ValueError(f"Piece source has no items array: {source_path}")
        defaults = {key: value for key, value in pieces.items() if key not in {"source", "extra"}}
        expanded = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid piece #{index} in: {source_path}")
            piece = _deep_merge(defaults, item)
            name = str(piece.get("id") or piece.get("name") or f"piece-{index}")
            piece["id"] = name
            piece["label"] = str(piece.get("label") or piece.get("name") or name)
            piece["copies"] = int(piece.get("copies", 1))
            if piece.get("front"):
                piece["front"] = _asset_path(root, source_path.parent, str(piece["front"]))
            if piece.get("back"):
                piece["back"] = _asset_path(root, source_path.parent, str(piece["back"]))
            expanded.append(piece)
        extras = pieces.get("extra", [])
        if not isinstance(extras, list):
            raise ValueError("pieces.extra must be a list")
        for index, item in enumerate(extras, 1):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid extra piece #{index}")
            piece = deepcopy(item)
            if piece.get("front"):
                piece["front"] = _asset_path(root, root, str(piece["front"]))
            if piece.get("back"):
                piece["back"] = _asset_path(root, root, str(piece["back"]))
            expanded.append(piece)
        pieces = expanded
    if not isinstance(pieces, list):
        raise ValueError("pieces must be a list or source definition")
    manifest["pieces"] = _apply_types(pieces, piece_types, "pieces")
    manifest.pop("types", None)
    return manifest


def _load_play_manifest(root: Path) -> dict[str, Any] | None:
    if _manifest_path(root) is None:
        return None
    _, manifest = _read_authoring_manifest(root)
    _validate_authoring_manifest(root, manifest)
    return _prepare_manifest(root, manifest)


def _game_revision(manifest: dict[str, Any] | None) -> str:
    payload = json.dumps(manifest or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _without_updates(manifest: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(manifest)
    result.pop("updates", None)
    return result


def _without_piece_assets(manifest: dict[str, Any]) -> dict[str, Any]:
    result = _without_updates(manifest)
    for piece in result.get("pieces", []):
        if isinstance(piece, dict):
            piece.pop("front", None)
            piece.pop("back", None)
    return result


def _without_pieces(manifest: dict[str, Any]) -> dict[str, Any]:
    result = _without_updates(manifest)
    result.pop("pieces", None)
    return result


def classify_revision(previous: dict[str, Any], current: dict[str, Any]) -> str:
    if _without_piece_assets(previous) == _without_piece_assets(current):
        return "assets"
    if _without_pieces(previous) == _without_pieces(current):
        return "components"
    return "rules"


@dataclass(frozen=True)
class RevisionChange:
    game_name: str
    previous: str
    current: str
    kind: str
    updates: dict[str, Any]


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
    revision: str

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "friendlyName": self.friendly_name,
            "minPlayers": self.min_players,
            "maxPlayers": self.max_players,
            "defaultPlayers": self.default_players,
            "cover": f"/games/{self.name}/cover" if self.cover_path else None,
            "revision": self.revision,
        }


class GameRegistry:
    def __init__(self, roots: list[Path]):
        self._games: dict[str, GameDefinition] = {}
        self._versions: dict[tuple[str, str], GameDefinition] = {}
        self._manifest_revisions: dict[str, str] = {}
        self._dependency_revisions: dict[str, str] = {}
        self._changes: list[RevisionChange] = []
        self._search_roots = [root.expanduser().resolve() for root in roots]
        self._loaded_roots: set[Path] = set()
        for root in self._search_roots:
            self._load_root(root)

    @staticmethod
    def _linked_root(root: Path) -> Path:
        manifest_path = root / "game.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        linked = manifest.get("projectRoot")
        return Path(str(linked)).expanduser().resolve() if linked else root

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
        root = self._linked_root(root)
        if root in self._loaded_roots:
            return
        definition = self._definition(root)
        if definition.name in self._games:
            raise ValueError(f"Duplicate game name: {definition.name}")
        self._loaded_roots.add(root)
        self._install_definition(definition)
        manifest_path = _manifest_path(root)
        if manifest_path:
            self._manifest_revisions[definition.name] = _manifest_revision(manifest_path)
            self._dependency_revisions[definition.name] = _manifest_dependency_revision(root)

    @staticmethod
    def _revision_path(root: Path, revision: str) -> Path:
        return root / REVISION_DIRECTORY / revision / "play.json"

    @staticmethod
    def _current_revision(root: Path) -> str | None:
        path = root / CURRENT_REVISION_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return str(value.get("revision") or "") or None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _install_definition(self, definition: GameDefinition) -> None:
        previous_revision = self._current_revision(definition.root)
        previous_manifest = None
        if previous_revision and previous_revision != definition.revision:
            previous_path = self._revision_path(definition.root, previous_revision)
            if previous_path.is_file():
                previous_manifest = json.loads(previous_path.read_text(encoding="utf-8"))
        if definition.play_manifest is not None:
            revision_path = self._revision_path(definition.root, definition.revision)
            if not revision_path.is_file():
                self._write_json_atomic(revision_path, definition.play_manifest)
            self._write_json_atomic(definition.root / CURRENT_REVISION_FILE, {"revision": definition.revision})
        self._games[definition.name] = definition
        self._versions[(definition.name, definition.revision)] = definition
        if previous_manifest is not None:
            self._changes.append(RevisionChange(
                game_name=definition.name,
                previous=previous_revision,
                current=definition.revision,
                kind=classify_revision(previous_manifest, definition.play_manifest or {}),
                updates=deepcopy((definition.play_manifest or {}).get("updates", {})),
            ))

    def _definition(
        self,
        root: Path,
        *,
        play_manifest: dict[str, Any] | None = None,
        revision: str | None = None,
    ) -> GameDefinition:
        manifest_path = root / "game.json"
        if not manifest_path.is_file():
            raise ValueError(f"PnPPlay manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        game = manifest["game"]
        ui = manifest["ui"]
        game_root = (root / game.get("root", ".")).resolve()
        ui_root = (root / ui.get("root", ".")).resolve()
        if play_manifest is None:
            play_manifest = _load_play_manifest(root)
        revision = revision or _game_revision(play_manifest)
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
            revision=revision,
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
        dependency_revision = _manifest_dependency_revision(game.root)
        if self._manifest_revisions.get(name) != revision or self._dependency_revisions.get(name) != dependency_revision:
            _validate_authoring_manifest(game.root, manifest)
            self._install_definition(self._definition(game.root))
            self._manifest_revisions[name] = revision
            self._dependency_revisions[name] = dependency_revision
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
        self._install_definition(definition)
        next_revision = _manifest_revision(path)
        self._manifest_revisions[name] = next_revision
        self._dependency_revisions[name] = _manifest_dependency_revision(game.root)
        return manifest, next_revision

    def refresh(self) -> list[RevisionChange]:
        for root in self._search_roots:
            if (root / "game.json").is_file():
                self._load(root)
            else:
                for manifest in sorted(root.glob("*/game.json")):
                    self._load(manifest.parent)
        for name, game in list(self._games.items()):
            path = _manifest_path(game.root)
            if path is None:
                continue
            dependency_revision = _manifest_dependency_revision(game.root)
            if self._dependency_revisions.get(name) == dependency_revision:
                continue
            self._install_definition(self._definition(game.root))
            self._manifest_revisions[name] = _manifest_revision(path)
            self._dependency_revisions[name] = dependency_revision
        changes, self._changes = self._changes, []
        return changes

    def revisions(self, name: str) -> list[dict[str, Any]]:
        game = self.get(name)
        root = game.root / REVISION_DIRECTORY
        entries = []
        for path in root.glob("*/play.json") if root.is_dir() else []:
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            entries.append({
                "revision": path.parent.name,
                "current": path.parent.name == game.revision,
                "createdAt": path.stat().st_mtime,
                "manifest": manifest,
            })
        entries.sort(key=lambda item: item["createdAt"])
        previous = None
        for item in entries:
            item["kind"] = "initial" if previous is None else classify_revision(previous, item["manifest"])
            item.pop("manifest", None)
            previous_path = self._revision_path(game.root, item["revision"])
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        entries.reverse()
        return entries

    def restore_revision(self, name: str, revision: str) -> None:
        game = self.get(name)
        source = self._revision_path(game.root, revision)
        if not source.is_file():
            raise LookupError(f"Unknown revision for {name}: {revision}")
        manifest = json.loads(source.read_text(encoding="utf-8"))
        _validate_authoring_manifest(game.root, manifest)
        target, _ = _read_authoring_manifest(game.root)
        if target.suffix == ".yaml":
            content = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120)
        else:
            content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)

    def delete_revision(self, name: str, revision: str) -> None:
        game = self.get(name)
        if revision == game.revision:
            raise ValueError("The current revision cannot be deleted")
        path = self._revision_path(game.root, revision)
        if not path.is_file():
            raise LookupError(f"Unknown revision for {name}: {revision}")
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        self._versions.pop((name, revision), None)

    def get(self, name: str, revision: str | None = None) -> GameDefinition:
        try:
            current = self._games[name]
        except KeyError as exc:
            raise LookupError(f"Unknown game: {name}") from exc
        if not revision or revision == current.revision:
            return current
        cached = self._versions.get((name, revision))
        if cached is not None:
            return cached
        path = self._revision_path(current.root, revision)
        if not path.is_file():
            raise LookupError(f"Unknown revision for {name}: {revision}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        definition = self._definition(current.root, play_manifest=manifest, revision=revision)
        self._versions[(name, revision)] = definition
        return definition

    def all(self) -> list[GameDefinition]:
        return sorted(self._games.values(), key=lambda game: game.friendly_name.lower())
