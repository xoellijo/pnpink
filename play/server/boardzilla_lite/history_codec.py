from __future__ import annotations

import json
import zlib
from typing import Any


SNAPSHOT_INTERVAL = 16
SNAPSHOT = 0
DELTA = 1

_ENTITY = "$entity"
_UNCHANGED = object()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compress_state(state: dict[str, Any]) -> bytes:
    return zlib.compress(_json_bytes(state))


def decompress_state(payload: bytes | memoryview | None) -> dict[str, Any]:
    if not payload:
        return {}
    return json.loads(zlib.decompress(bytes(payload)))


def state_text(payload: bytes | memoryview | None) -> str:
    return json.dumps(decompress_state(payload), ensure_ascii=False, separators=(",", ":"))


def _normalize(state: dict[str, Any]) -> dict[str, Any]:
    entities: dict[str, Any] = {}

    def visit(value: Any, scope: str) -> Any:
        if isinstance(value, list):
            return [visit(item, scope) for item in value]
        if not isinstance(value, dict):
            return value

        identity = None
        if "_id" in value:
            identity = f"{scope}:id:{value['_id']}"
        elif "_ref" in value:
            identity = f"{scope}:ref:{value['_ref']}"

        body = {key: visit(item, scope) for key, item in value.items()}
        if identity is None:
            return body
        previous = entities.get(identity)
        if previous is not None and previous != body:
            raise ValueError(f"Boardzilla reused entity identity {identity}")
        entities[identity] = body
        return {_ENTITY: identity}

    root: dict[str, Any] = {}
    for key, value in state.items():
        if key != "players" or not isinstance(value, list):
            root[key] = visit(value, "game")
            continue
        players = []
        for player in value:
            if not isinstance(player, dict):
                players.append(visit(player, "players"))
                continue
            position = player.get("position", "unknown")
            normalized_player = {}
            for player_key, player_value in player.items():
                if player_key == "state" and isinstance(player_value, list):
                    normalized_player[player_key] = [
                        visit(frame, f"player:{position}:frame:{index}")
                        for index, frame in enumerate(player_value)
                    ]
                else:
                    normalized_player[player_key] = visit(player_value, f"player:{position}")
            players.append(normalized_player)
        root[key] = players
    return {"root": root, "entities": entities}


def _restore(normalized: dict[str, Any]) -> dict[str, Any]:
    entities = normalized["entities"]

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {_ENTITY}:
            return visit(entities[value[_ENTITY]])
        return {key: visit(item) for key, item in value.items()}

    return visit(normalized["root"])


def _diff(previous: Any, current: Any) -> Any:
    if previous == current:
        return _UNCHANGED
    if isinstance(previous, dict) and isinstance(current, dict):
        removed = [key for key in previous if key not in current]
        changed = {}
        for key, value in current.items():
            if key not in previous:
                changed[key] = ["replace", value]
                continue
            child = _diff(previous[key], value)
            if child is not _UNCHANGED:
                changed[key] = child
        return ["dict", removed, changed]
    return ["replace", current]


def _apply(value: Any, patch: Any) -> Any:
    if patch[0] == "replace":
        return patch[1]
    result = dict(value)
    for key in patch[1]:
        result.pop(key, None)
    for key, child in patch[2].items():
        result[key] = _apply(result.get(key), child)
    return result


def encode_history(previous: dict[str, Any], current: dict[str, Any], depth: int) -> tuple[int, bytes]:
    if depth % SNAPSHOT_INTERVAL == 0:
        return SNAPSHOT, compress_state(current)
    old = _normalize(previous)
    new = _normalize(current)
    root_patch = _diff(old["root"], new["root"])
    payload = {
        "root": None if root_patch is _UNCHANGED else root_patch,
        "set": {
            key: value
            for key, value in new["entities"].items()
            if old["entities"].get(key) != value
        },
        "remove": [key for key in old["entities"] if key not in new["entities"]],
    }
    return DELTA, zlib.compress(_json_bytes(payload))


def apply_history(previous: dict[str, Any], kind: int, payload: bytes | memoryview) -> dict[str, Any]:
    if kind == SNAPSHOT:
        return decompress_state(payload)
    normalized = _normalize(previous)
    delta = json.loads(zlib.decompress(bytes(payload)))
    if delta["root"] is not None:
        normalized["root"] = _apply(normalized["root"], delta["root"])
    for key in delta["remove"]:
        normalized["entities"].pop(key, None)
    normalized["entities"].update(delta["set"])
    return _restore(normalized)
