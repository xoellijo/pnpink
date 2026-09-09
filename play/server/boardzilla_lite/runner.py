from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from py_mini_racer import MiniRacer


class GameRunner:
    def __init__(self, timeout_seconds: float = 1.0, memory_limit: int = 128 * 1024 * 1024):
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self._sources: dict[Path, tuple[int, str]] = {}

    def _source(self, path: Path) -> str:
        modified = path.stat().st_mtime_ns
        cached = self._sources.get(path)
        if cached is None or cached[0] != modified:
            cached = (modified, path.read_text(encoding="utf-8"))
            self._sources[path] = cached
        return cached[1]

    def _call(self, bundle: Path, method: str, *args: Any, manifest: dict[str, Any] | None = None, optional: bool = False) -> Any:
        with MiniRacer() as context:
            if manifest is not None:
                context.eval(
                    "globalThis.__PNPINK_PLAY_MANIFEST__ = " + json.dumps(manifest, ensure_ascii=False),
                    timeout_sec=self.timeout_seconds,
                )
            context.eval(self._source(bundle), timeout_sec=self.timeout_seconds)
            context.eval("globalThis.__boardzillaGame = game.default || game")
            if optional and not context.eval(f"typeof __boardzillaGame.{method} === 'function'"):
                return None
            return context.call(
                f"__boardzillaGame.{method}",
                *args,
                timeout_sec=self.timeout_seconds,
                max_memory=self.memory_limit,
            )

    def seat_player(self, bundle: Path, players: list[dict[str, Any]], seat_count: int, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self._call(bundle, "seatPlayer", players, seat_count, manifest=manifest, optional=True)

    def initial_state(self, bundle: Path, setup: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        update = self._call(bundle, "initialState", setup, manifest=manifest)
        self._verify_update(update)
        return update

    def process_move(self, bundle: Path, state: dict[str, Any], move: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        update = self._call(bundle, "processMove", state, move, manifest=manifest)
        self._verify_update(update)
        return update

    @staticmethod
    def _verify_update(update: Any) -> None:
        if not isinstance(update, dict) or not isinstance(update.get("game"), dict):
            raise ValueError("Game returned an invalid update")
        phase = update["game"].get("phase")
        if phase == "started" and not isinstance(update["game"].get("currentPlayers"), list):
            raise ValueError("Started game update has no currentPlayers")
        if phase == "finished" and not isinstance(update["game"].get("winners"), list):
            raise ValueError("Finished game update has no winners")
        if phase not in {"started", "finished"}:
            raise ValueError(f"Invalid game phase: {json.dumps(phase)}")
        if not isinstance(update.get("players"), list):
            raise ValueError("Game update has no player states")

