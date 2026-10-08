# -*- coding: utf-8 -*-
"""Prepare and launch an immediate PnPPlay table."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import export_items as EXPORTITEMS
import play_asset_store as ASSETSTORE


PLAY_DPI = 150


def _runtime_candidates() -> list[Path]:
    source_dir = Path(__file__).resolve().parent
    project_dir = source_dir.parent
    configured = str(os.environ.get("PNPPLAY_RUNTIME") or os.environ.get("PNPINK_PLAY_RUNTIME") or "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        (
            source_dir / "play_runtime",
            Path.home() / ".pnpplay" / "runtime",
            project_dir.parent / "pnpplay",
            project_dir.parent / "pnpplay" / ".artifacts" / "play-runtime",
            project_dir / ".artifacts" / "play-runtime",
        )
    )
    return candidates


def find_runtime() -> Path:
    for candidate in _runtime_candidates():
        root = candidate.expanduser().resolve()
        if (root / "launch.py").is_file() and (root / "server" / "boardzilla_lite").is_dir():
            return root
    raise RuntimeError("PnPPlay runtime is not installed")


def _game_id(template: str) -> str:
    stem = ''.join(character.lower() if character.isalnum() else '-' for character in Path(template).stem).strip('-') or 'pnpink-game'
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(template)).encode('utf-8')).hexdigest()[:8]
    return f"{stem}-{digest}"


def game_project(template: str) -> tuple[Path, str]:
    template_path = Path(template).resolve()
    return template_path.with_suffix(".play"), _game_id(str(template_path))


def _asset_pack_id(template: str) -> str:
    return f"pnpink/{_game_id(template)}"


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_lines(value, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                first_key = next(iter(item))
                first_value = item[first_key]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{prefix}- {first_key}:")
                    lines.extend(_yaml_lines(first_value, indent + 4))
                else:
                    lines.append(f"{prefix}- {first_key}: {_yaml_scalar(first_value)}")
                lines.extend(_yaml_lines({key: item[key] for key in list(item)[1:]}, indent + 2))
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _write_yaml(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(_yaml_lines(value)) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _content_address_play_assets(assets_dir: Path, result: dict) -> None:
    renamed: dict[str, str] = {}
    for item in result.get("results", []):
        for field in ("front", "back"):
            source_name = item.get(field)
            if not source_name:
                continue
            if source_name in renamed:
                item[field] = renamed[source_name]
                continue
            source = assets_dir / source_name
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
            target_name = f"{digest}{source.suffix.lower()}"
            target = assets_dir / target_name
            if target != source:
                if target.exists():
                    source.unlink()
                else:
                    os.replace(source, target)
            renamed[source_name] = target_name
            item[field] = target_name
    manifest_path = assets_dir / "items.json"
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps({"schema": 2, "items": result.get("results", [])}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_manifest, manifest_path)


def _convert_play_assets_to_webp(assets_dir: Path, result: dict) -> bool:
    try:
        from PIL import Image, features
    except ImportError:
        return False
    if not features.check("webp"):
        return False
    converted: dict[str, str] = {}
    for item in result.get("results", []):
        for field in ("front", "back"):
            source_name = item.get(field)
            if not source_name:
                continue
            if source_name in converted:
                item[field] = converted[source_name]
                continue
            source = assets_dir / source_name
            target = source.with_suffix(".webp")
            try:
                with Image.open(source) as image:
                    image.save(target, format="WEBP", quality=88, method=4)
                if not target.is_file() or target.stat().st_size <= 0:
                    return False
                source.unlink()
            except (OSError, ValueError):
                return False
            converted[source_name] = target.name
            item[field] = target.name
    return bool(converted)


def _configured_game_roots(runtime: Path) -> list[Path]:
    roots = [runtime / "games"]
    configured = str(os.environ.get("PNPPLAY_GAMES") or "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    development_root = Path(__file__).resolve().parents[2] / "pnpplay" / "games"
    roots.append(development_root)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(str(root.resolve()))
        if key not in seen and root.is_dir():
            seen.add(key)
            unique.append(root)
    return unique


def _sync_configured_runtime_game(runtime: Path, template_path: Path, source_assets: Path) -> tuple[Path, str] | None:
    for games_root in _configured_game_roots(runtime):
        for manifest_path in games_root.glob("*/game.json"):
            result = _sync_configured_game(manifest_path, template_path, source_assets)
            if result is not None:
                return result
    return None


def _sync_configured_game(manifest_path: Path, template_path: Path, source_assets: Path) -> tuple[Path, str] | None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        configured = str(manifest.get("pnpinkTemplate") or "").strip()
        if not configured:
            return None
        configured_path = Path(configured).expanduser()
        matches = configured_path.name.casefold() == template_path.name.casefold()
        if configured_path.is_absolute():
            matches = os.path.normcase(str(configured_path.resolve())) == os.path.normcase(str(template_path))
        if not matches:
            return None
        target_game = manifest_path.parent
        pack_id = str(manifest.get("pnpinkAssetPack") or _asset_pack_id(str(template_path)))
        ASSETSTORE.publish_local_pack(
            source_assets,
            pack_id,
            name=str(manifest.get("friendlyName") or template_path.stem),
            source={"template": template_path.name},
        )
        return target_game, str(manifest["name"])


def prepare_game(
    template: str,
    svg_path: str,
    runtime: Path,
    *,
    cached_pngs: dict[str, str] | None = None,
) -> tuple[Path, str]:
    template_path = Path(template).resolve()
    game_id = _game_id(str(template_path))
    game_dir = template_path.with_suffix(".play")
    assets_dir = game_dir / "assets"
    game_dir.mkdir(parents=True, exist_ok=True)
    prefix = ''.join(character if character.isalnum() or character in "_-" else '_' for character in template_path.stem) or "card"
    ok, result = EXPORTITEMS.export_items_via_inkscape(
        svg_path,
        str(game_dir / "items.png"),
        export_type="png",
        item_spec=f"{prefix}*",
        export_dpi=PLAY_DPI,
        output_dir=str(assets_dir),
        cached_pngs=cached_pngs,
    )
    if not ok:
        raise RuntimeError(str(result.get("error") or "Unable to export Play assets"))
    _convert_play_assets_to_webp(assets_dir, result)
    _content_address_play_assets(assets_dir, result)
    pack_id = _asset_pack_id(str(template_path))
    ASSETSTORE.publish_local_pack(
        assets_dir,
        pack_id,
        name=template_path.stem,
        source={"template": template_path.name},
    )

    if not result.get("results"):
        raise RuntimeError("The generated SVG contains no playable items")

    play_manifest = {
        "schema": 1,
        "extends": "trick-games-template",
        "name": template_path.stem,
        "description": "Immediate tabletop generated by PnPInk",
        "players": {"count": 4},
        "turns": {"mode": "trick-assisted", "order": "clockwise"},
        "board": {"aspectRatio": 1.6, "background": "#173f35"},
        "areas": [
            {"id": "deck", "extends": "deck-area", "label": "Deck", "layout": {"shared": {"left": 7, "top": 36, "width": 11, "height": 28}}},
            {"id": "table", "layout": {"shared": {"left": 22, "top": 23, "width": 56, "height": 52}, "rows": 3, "columns": 6}},
            {"id": "discard", "extends": "discard-area", "label": "Discard", "layout": {"shared": {"left": 82, "top": 36, "width": 11, "height": 28}}},
            {"id": "hand", "layout": {"owner": {"left": 22, "top": 78, "width": 56, "height": 19}, "opponent": {"left": 31, "top": 2, "width": 38, "height": 16}, "rows": 1, "columns": 8}},
            {"id": "tricks", "extends": "trick-area", "label": "Tricks", "layout": {"owner": {"left": 90, "top": 76, "width": 8, "height": 20}, "opponent": {"left": 72, "top": 3, "width": 8, "height": 15}}},
        ],
        "pieces": {
            "source": {"store": "local", "pack": pack_id},
            "initial": {"area": "deck", "faceUp": False},
        },
        "setup": [
            {"shuffle": "deck"},
            {"deal": {"from": "deck", "to": "hand", "count": 13}},
        ],
    }
    game_manifest = {
        "minPlayers": 4,
        "maxPlayers": 4,
        "defaultPlayers": 4,
        "ui": {"root": str(runtime), "outDir": "build/ui"},
        "game": {"root": str(runtime), "out": "build/game/game-interface.js"},
        "name": game_id,
        "pnpinkAssetPack": pack_id,
        "friendlyName": f"{template_path.stem} — PnPPlay",
    }
    authoring_manifest = game_dir / "play.yaml"
    existing_text = authoring_manifest.read_text(encoding="utf-8", errors="replace") if authoring_manifest.is_file() else ""
    legacy_generated_manifest = bool(existing_text and ("\nzones:" in existing_text or "\ncomponents:" in existing_text))
    if not existing_text or legacy_generated_manifest:
        _write_json(game_dir / "play.json", play_manifest)
        _write_yaml(authoring_manifest, play_manifest)
    _write_json(game_dir / "game.json", game_manifest)
    configured_game = _sync_configured_runtime_game(runtime, template_path, assets_dir)
    if configured_game is not None:
        return configured_game
    deployed_game = runtime / "games" / game_id
    deployed_game.mkdir(parents=True, exist_ok=True)
    _write_json(deployed_game / "game.json", {"projectRoot": str(game_dir)})
    return game_dir, game_id


def _central_database(runtime: Path) -> Path:
    configured = str(os.environ.get("PNPPLAY_DATABASE") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    project_root = Path(__file__).resolve().parent.parent
    windows_database = project_root / ".artifacts" / "play-runtime" / "data" / "pnpink-play-windows.db"
    if windows_database.is_file():
        return windows_database
    return runtime / "data" / "pnpplay.db"


def _request_json(url: str, body: dict | None = None, *, token: str = "", timeout: float = 5.0) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=max(0.1, float(timeout))) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"PnPPlay server returned HTTP {error.code}") from error


class PlayServer:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.game_dir: Path | None = None
        self.port = 0
        self.log_path: Path | None = None

    @staticmethod
    def _server_has_game(port: int, game_id: str) -> bool:
        try:
            response = _request_json(f"http://127.0.0.1:{port}/games", timeout=0.25)
        except Exception:
            return False
        games = response.get("games", response if isinstance(response, list) else [])
        return any(isinstance(game, dict) and game.get("name") == game_id for game in games)

    def _startup_error(self, message: str) -> RuntimeError:
        detail = ""
        if self.log_path is not None:
            try:
                lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                detail = "\n".join(lines[-12:]).strip()
            except Exception:
                pass
        return RuntimeError(f"{message}\n{detail}".rstrip())

    def close(self) -> None:
        process = self.process
        self.process = None
        self.game_dir = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self.port = 0

    @property
    def managed(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def running(self) -> bool:
        try:
            _request_json("http://127.0.0.1:8080/games", timeout=0.25)
            return True
        except Exception:
            return False

    def open_game(self, game_dir: Path, game_id: str, runtime: Path) -> str:
        self.ensure(game_dir, runtime)
        try:
            user = _request_json(f"http://127.0.0.1:{self.port}/users", {"name": "Host"})["user"]
            session = _request_json(
                f"http://127.0.0.1:{self.port}/sessions",
                {"gameName": game_id, "seatCount": 2, "adminMode": True},
                token=user["token"],
            )
        except Exception:
            self.close()
            raise
        return f"http://127.0.0.1:{self.port}/s/{session['playerAccessToken']}"

    def ensure(self, game_dir: Path, runtime: Path) -> None:
        manifest_path = game_dir / "game.json"
        game_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(game_manifest.get("pnpinkAssetPack") or "").startswith("pnpink/"):
            expected = {
                "ui": {"root": str(runtime), "outDir": "build/ui"},
                "game": {"root": str(runtime), "out": "build/game/game-interface.js"},
            }
            if game_manifest.get("ui") != expected["ui"] or game_manifest.get("game") != expected["game"]:
                game_manifest.update(expected)
                _write_json(manifest_path, game_manifest)
        game_id = str(game_manifest["name"])
        self.port = 8080
        if self.running:
            for _attempt in range(20):
                if self._server_has_game(self.port, game_id):
                    self.game_dir = game_dir
                    return
                time.sleep(0.1)
            raise RuntimeError(f"PnPPlay is running on port 8080 but game '{game_id}' is not registered")
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.port = 8080
            database = _central_database(runtime)
            database.parent.mkdir(parents=True, exist_ok=True)
            self.log_path = runtime / "data" / "server.log"
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable,
                "-I",
                "-S",
                str(runtime / "launch.py"),
                "--games",
                str(runtime / "games"),
                "--database",
                str(database),
                "--host",
                "0.0.0.0",
                "--port",
                str(self.port),
            ]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with self.log_path.open("a", encoding="utf-8") as server_log:
                server_log.write(f"\n--- Starting PnPPlay on port {self.port} ---\n")
                server_log.flush()
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            self.game_dir = game_dir
            endpoint = f"http://127.0.0.1:{self.port}/games"
            for _attempt in range(40):
                if self.process.poll() is not None:
                    raise self._startup_error("PnPPlay server stopped during startup")
                try:
                    _request_json(endpoint, timeout=0.3)
                    break
                except Exception:
                    time.sleep(0.15)
            else:
                raise self._startup_error("PnPPlay server did not start")
            for _attempt in range(20):
                if self._server_has_game(self.port, game_id):
                    return
                time.sleep(0.1)
            raise self._startup_error(f"PnPPlay started but game '{game_id}' is not registered")

    def lobby_url(self, game_dir: Path, runtime: Path) -> str:
        self.ensure(game_dir, runtime)
        return f"http://127.0.0.1:{self.port}/"

    def designer_url(self, game_dir: Path, game_id: str, runtime: Path) -> str:
        self.ensure(game_dir, runtime)
        return f"http://127.0.0.1:{self.port}/designer/{urllib.parse.quote(game_id)}"
