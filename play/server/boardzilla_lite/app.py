from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import mimetypes
import time
from collections import defaultdict
from importlib.resources import files
from pathlib import Path
from typing import Any

from aiohttp import web

from .database import Database
from .events import EventBus
from .games import GameDefinition, GameRegistry
from .runner import GameRunner


DB_KEY = web.AppKey("database", Database)
GAMES_KEY = web.AppKey("games", GameRegistry)
RUNNER_KEY = web.AppKey("runner", GameRunner)
EVENTS_KEY = web.AppKey("events", EventBus)
LOCKS_KEY = web.AppKey("locks", dict)
WATCHER_KEY = web.AppKey("revision_watcher", asyncio.Task)
DEFAULT_AVATAR = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='21' r='12' fill='none' stroke='%23fff' stroke-width='5'/%3E%3Cpath d='M10 58v-5c0-11 9-20 20-20h4c11 0 20 9 20 20v5' fill='none' stroke='%23fff' stroke-width='5' stroke-linecap='round'/%3E%3C/svg%3E"


def json_value(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def player_for_game(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": player["session_user_id"],
        "position": player["position"],
        "name": player["name"],
        "avatar": DEFAULT_AVATAR,
        "color": player["color"],
        "settings": json_value(player["settings"], {}),
        "host": bool(player["host"]),
    }


def is_seated_player(player: dict[str, Any]) -> bool:
    return player["position"] is not None and not bool(player.get("spectator"))


def game_for_session(request: web.Request, session: dict[str, Any]) -> GameDefinition:
    return request.app[GAMES_KEY].get(session["game_name"], session.get("game_revision"))


def is_administrator(session: dict[str, Any]) -> bool:
    return bool(session.get("host") and session.get("admin_mode"))


def session_and_player(request: web.Request) -> tuple[dict[str, Any], GameDefinition]:
    session = request.app[DB_KEY].session_by_token(request.match_info["token"])
    return session, game_for_session(request, session)


def authenticated_user(request: web.Request) -> dict[str, Any]:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise web.HTTPUnauthorized(text="A player identity is required")
    try:
        return request.app[DB_KEY].user_by_token(token)
    except LookupError as exc:
        raise web.HTTPUnauthorized(text=str(exc)) from exc


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["public_id"], "name": user["name"]}


def public_room(room: dict[str, Any], games: GameRegistry) -> dict[str, Any]:
    try:
        game = games.get(room["game_name"])
        friendly_name = game.friendly_name
    except LookupError:
        friendly_name = room["game_name"]
    return {
        "id": room["id"],
        "gameName": room["game_name"],
        "friendlyName": friendly_name,
        "phase": room["phase"],
        "inviteCode": room["invite_code"],
        "players": room["player_count"],
        "spectators": room["spectator_count"],
        "seatCount": room["seat_count"],
        "updatedAt": room["updated_at"],
    }


def user_room(room: dict[str, Any], games: GameRegistry, players: list[dict[str, Any]]) -> dict[str, Any]:
    result = public_room(room, games)
    current_players = json_value(room.get("state"), {}).get("game", {}).get("currentPlayers", [])
    position = room.get("player_position")
    your_turn = room["phase"] == "started" and position is not None and position in current_players
    waiting_for = [
        player["name"] for player in players
        if is_seated_player(player) and player["position"] in current_players
    ]
    result.update(
        {
            "token": room["player_access_token"],
            "owner": bool(room["player_host"]),
            "spectator": bool(room["player_spectator"]),
            "yourTurn": your_turn,
            "waitingFor": waiting_for,
        }
    )
    return result


def is_local_request(request: web.Request) -> bool:
    try:
        address = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    mapped_address = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)


def require_local_designer(request: web.Request) -> None:
    if not is_local_request(request):
        raise web.HTTPForbidden(text="The Game Designer is only available from this computer")


def users_event(db: Database, session: dict[str, Any]) -> dict[str, Any]:
    viewer_is_host = bool(session["host"])
    users = []
    for player in db.players(session["id"]):
        details = None
        if is_seated_player(player):
            details = {
                "color": player["color"],
                "position": player["position"],
                "settings": json_value(player["settings"], {}),
                "ready": bool(player["ready"]),
            }
            if viewer_is_host:
                details["sessionURL"] = f"/s/{player['access_token']}"
        users.append(
            {
                "id": player["session_user_id"],
                "name": player["name"],
                "avatar": DEFAULT_AVATAR,
                "playerDetails": details,
            }
        )
    return {"type": "users", "users": users}


def effective_position(db: Database, session: dict[str, Any], update: dict[str, Any] | None = None) -> int | None:
    if bool(session.get("spectator")):
        if session.get("view_position") not in (None, -1):
            return int(session["view_position"])
        current_players = (update or json_value(session.get("state"), {})).get("game", {}).get("currentPlayers", [])
        if current_players:
            return int(current_players[0])
        seated = [player["position"] for player in db.players(session["id"]) if is_seated_player(player)]
        return int(seated[0]) if seated else None
    if session.get("view_position") == -1:
        current_players = (update or json_value(session.get("state"), {})).get("game", {}).get("currentPlayers", [])
        if current_players:
            return int(current_players[0])
        seated = [player["position"] for player in db.players(session["id"]) if is_seated_player(player)]
        return int(seated[0]) if seated else None
    if bool(session.get("host")) and session.get("view_position") is not None:
        return int(session["view_position"])
    return int(session["position"]) if session.get("position") is not None else None


def snapshot_events(db: Database, session: dict[str, Any]) -> list[dict[str, Any]]:
    if session["phase"] == "new":
        return [
            users_event(db, session),
            {
                "type": "settingsUpdate",
                "settings": json_value(session["settings"], {}),
                "seatCount": session["seat_count"],
            },
        ]
    update = json_value(session["state"], {})
    position = effective_position(db, session, update)
    if position is None:
        return []
    player_state = next(
        (state for state in update.get("players", []) if state.get("position") == position),
        None,
    )
    if player_state is None:
        return []
    game = update["game"]
    if game["phase"] == "finished":
        return [
            {
                "type": "gameFinished",
                "state": player_state["state"],
                "position": position,
                "seq": session["next_seq"],
                "winners": game["winners"],
                "canUndo": (not session.get("spectator")) and db.can_undo(session["id"], position, bool(session["host"])),
                "history": db.history_status(session["id"]),
            }
        ]
    return [
        {
            "type": "gameUpdate",
            "state": player_state["state"],
            "position": position,
            "seq": session["next_seq"],
            "currentPlayers": game["currentPlayers"],
            "canUndo": (not session.get("spectator")) and db.can_undo(session["id"], position, bool(session["host"])),
            "history": db.history_status(session["id"]),
        }
    ]


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(files("boardzilla_lite").joinpath("web/index.html"))


async def join_page(request: web.Request) -> web.FileResponse:
    request.app[DB_KEY].session_by_invite(request.match_info["code"])
    return web.FileResponse(files("boardzilla_lite").joinpath("web/join.html"))


async def session_page(request: web.Request) -> web.FileResponse:
    request.app[DB_KEY].session_by_token(request.match_info["token"])
    return web.FileResponse(files("boardzilla_lite").joinpath("web/session.html"))


async def designer_page(request: web.Request) -> web.FileResponse:
    require_local_designer(request)
    request.app[GAMES_KEY].get(request.match_info["name"])
    return web.FileResponse(files("boardzilla_lite").joinpath("web/designer.html"))


def asset_etag(target) -> str:
    stat = target.stat()
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'


def asset_response(
    request: web.Request,
    target,
    *,
    headers: dict[str, str] | None = None,
    content_type: str | None = None,
) -> web.StreamResponse:
    media_type = content_type or mimetypes.guess_type(target)[0] or "application/octet-stream"
    response_headers = dict(headers or {})
    etag = asset_etag(target)
    response_headers["ETag"] = etag
    requested_etags = request.headers.get("If-None-Match", "")
    if any(candidate.removeprefix("W/") in {etag, "*"} for candidate in map(str.strip, requested_etags.split(","))):
        return web.Response(status=304, headers=response_headers)
    if media_type.startswith("text/") or media_type in {"application/javascript", "application/json", "application/manifest+json"}:
        return web.Response(body=target.read_bytes(), headers=response_headers, content_type=media_type, charset="utf-8")
    return web.FileResponse(target, headers=response_headers)


async def web_asset(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if "/" in name or "\\" in name:
        raise web.HTTPNotFound()
    target = files("boardzilla_lite").joinpath(f"web/{name}")
    if not target.is_file():
        raise web.HTTPNotFound()
    return asset_response(request, target)


async def pwa_asset(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if name not in {"manifest.webmanifest", "app-icon.svg", "sw.js"}:
        raise web.HTTPNotFound()
    target = files("boardzilla_lite").joinpath(f"web/{name}")
    headers = {"Cache-Control": "no-cache"} if name == "sw.js" else {}
    if name == "sw.js":
        headers["Service-Worker-Allowed"] = "/"
    content_type = "application/manifest+json" if name == "manifest.webmanifest" else None
    return asset_response(request, target, headers=headers, content_type=content_type)


async def list_games(request: web.Request) -> web.Response:
    can_design = is_local_request(request)
    games = [{**game.public(), "designer": f"/designer/{game.name}" if can_design and game.play_manifest is not None else None} for game in request.app[GAMES_KEY].all()]
    return web.json_response({"games": games})


async def get_game_definition(request: web.Request) -> web.Response:
    require_local_designer(request)
    manifest, revision = request.app[GAMES_KEY].authoring_manifest(request.match_info["name"])
    schema = json.loads(files("boardzilla_lite").joinpath("web/play.schema.json").read_text(encoding="utf-8"))
    return web.json_response({"manifest": manifest, "revision": revision, "schema": schema})


async def save_game_definition(request: web.Request) -> web.Response:
    require_local_designer(request)
    body = await request.json()
    manifest = body.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    try:
        saved, revision = request.app[GAMES_KEY].save_manifest(request.match_info["name"], manifest, body.get("revision"))
    except RuntimeError as exc:
        raise web.HTTPConflict(text=str(exc)) from exc
    return web.json_response({"manifest": saved, "revision": revision})


async def game_revisions(request: web.Request) -> web.Response:
    require_local_designer(request)
    name = request.match_info["name"]
    game = request.app[GAMES_KEY].get(name)
    usage = request.app[DB_KEY].revision_usage(name)
    revisions = request.app[GAMES_KEY].revisions(name)
    for revision in revisions:
        revision["rooms"] = usage.get(revision["revision"], {"total": 0, "new": 0, "started": 0, "finished": 0})
    return web.json_response({
        "current": game.revision,
        "updates": (game.play_manifest or {}).get("updates", {}),
        "revisions": revisions,
    })


async def restore_game_revision(request: web.Request) -> web.Response:
    require_local_designer(request)
    name = request.match_info["name"]
    revision = request.match_info["revision"]
    request.app[GAMES_KEY].restore_revision(name, revision)
    _apply_revision_changes(request.app, request.app[GAMES_KEY].refresh())
    return web.json_response({"current": request.app[GAMES_KEY].get(name).revision})


async def delete_game_revision(request: web.Request) -> web.Response:
    require_local_designer(request)
    name = request.match_info["name"]
    revision = request.match_info["revision"]
    usage = request.app[DB_KEY].revision_usage(name).get(revision, {}).get("total", 0)
    if usage:
        raise ValueError(f"Revision is used by {usage} saved room(s)")
    request.app[GAMES_KEY].delete_revision(name, revision)
    return web.Response(status=204)


async def game_cover(request: web.Request) -> web.StreamResponse:
    game = request.app[GAMES_KEY].get(request.match_info["name"])
    if not game.cover_path:
        raise web.HTTPNotFound()
    return asset_response(request, game.cover_path, headers={"Cache-Control": "public, no-cache"})


async def list_rooms(request: web.Request) -> web.Response:
    rooms = [public_room(room, request.app[GAMES_KEY]) for room in request.app[DB_KEY].rooms()]
    return web.json_response({"rooms": rooms})


async def create_user(request: web.Request) -> web.Response:
    body = await request.json()
    user = request.app[DB_KEY].create_user(str(body.get("name", "")))
    return web.json_response({"user": {"id": user["publicID"], "name": user["name"], "token": user["token"]}}, status=201)


async def get_user(request: web.Request) -> web.Response:
    return web.json_response({"user": public_user(authenticated_user(request))})


async def list_user_rooms(request: web.Request) -> web.Response:
    user = authenticated_user(request)
    db = request.app[DB_KEY]
    rooms = [user_room(room, request.app[GAMES_KEY], db.players(room["id"])) for room in db.rooms_for_user(user["id"])]
    return web.json_response({"rooms": rooms})


async def create_session(request: web.Request) -> web.Response:
    user = authenticated_user(request)
    body = await request.json()
    _apply_revision_changes(request.app, request.app[GAMES_KEY].refresh())
    game = request.app[GAMES_KEY].get(body["gameName"])
    seat_count = int(body.get("seatCount", game.default_players))
    if not game.min_players <= seat_count <= game.max_players:
        raise ValueError(f"seatCount must be between {game.min_players} and {game.max_players}")
    seat = await asyncio.to_thread(request.app[RUNNER_KEY].seat_player, game.server_bundle, [], seat_count, game.play_manifest)
    result = request.app[DB_KEY].create_session(
        game.name,
        user,
        seat_count,
        bool(body.get("adminMode", body.get("allowUndo", True))),
        seat,
        game.revision,
    )
    return web.json_response(result, status=201)


async def invite_details(request: web.Request) -> web.Response:
    session = request.app[DB_KEY].session_by_invite(request.match_info["code"])
    game = game_for_session(request, session)
    players = [{
        "userID": player["public_id"],
        "name": player["name"],
        "position": player["position"],
        "ready": bool(player["ready"]),
        "spectator": bool(player["spectator"]),
    } for player in request.app[DB_KEY].players(session["id"])]
    return web.json_response({
        "name": game.name,
        "friendlyName": game.friendly_name,
        "phase": session["phase"],
        "seatCount": session["seat_count"],
        "players": players,
    })


async def join_session(request: web.Request) -> web.Response:
    user = authenticated_user(request)
    db = request.app[DB_KEY]
    session = db.session_by_invite(request.match_info["code"])
    existing = db.player_for_user(session["id"], user["id"])
    if existing:
        return web.json_response({"sessionID": session["id"], "token": existing["access_token"], "userID": existing["session_user_id"]})
    game = game_for_session(request, session)
    seated = [player for player in db.players(session["id"]) if is_seated_player(player)]
    if len(seated) >= session["seat_count"]:
        raise ValueError("All player seats are occupied; join as a spectator instead")
    players = [player_for_game(player) for player in seated]
    seat = await asyncio.to_thread(request.app[RUNNER_KEY].seat_player, game.server_bundle, players, session["seat_count"], game.play_manifest)
    if not seat or seat.get("position") is None:
        raise ValueError("No player seat is available; join as a spectator instead")
    result = db.join_session(session["invite_code"], user, seat)
    request.app[EVENTS_KEY].publish(session["id"])
    return web.json_response(result, status=201)


async def spectate_session(request: web.Request) -> web.Response:
    user = authenticated_user(request)
    db = request.app[DB_KEY]
    session = db.session_by_invite(request.match_info["code"])
    result = db.spectate_session(session["invite_code"], user)
    request.app[EVENTS_KEY].publish(session["id"])
    return web.json_response(result, status=201)


async def get_session(request: web.Request) -> web.Response:
    session, game = session_and_player(request)
    position = effective_position(request.app[DB_KEY], session)
    current_players = json_value(session.get("state"), {}).get("game", {}).get("currentPlayers", [])
    administrator = is_administrator(session)
    players = []
    room_players = request.app[DB_KEY].players(session["id"])
    for player in room_players:
        if not is_seated_player(player):
            continue
        details = {"position": player["position"], "name": player["name"], "color": player["color"], "userID": player["public_id"]}
        if administrator:
            details["sessionURL"] = f"/s/{player['access_token']}"
        players.append(details)
    return web.json_response(
        {
            "host": bool(session["host"]),
            "administrator": administrator,
            "adminMode": bool(session["admin_mode"]),
            "spectator": bool(session.get("spectator")),
            "position": session["position"],
            "viewPosition": session.get("view_position"),
            "effectivePosition": position,
            "players": players,
            "participants": [{
                "userID": player["public_id"],
                "name": player["name"],
                "ready": bool(player["ready"]),
                "spectator": bool(player["spectator"]),
            } for player in room_players],
            "currentPlayers": current_players,
            "allowUndo": bool(session["allow_undo"]),
            "canUndo": (not session.get("spectator")) and request.app[DB_KEY].can_undo(session["id"], position, bool(session["host"])),
            "history": request.app[DB_KEY].history_status(session["id"]),
            "inviteCode": session["invite_code"],
            "sessionID": session["id"],
            "game": game.public(),
            "phase": session["phase"],
            "userID": session["session_user_id"],
            "playerName": session["player_name"],
        }
    )


async def get_snapshot(request: web.Request) -> web.Response:
    session, _ = session_and_player(request)
    return web.json_response({"events": snapshot_events(request.app[DB_KEY], session)})


async def update_settings(request: web.Request) -> web.Response:
    session, game = session_and_player(request)
    if not session["host"]:
        raise PermissionError("Only the host can update game settings")
    body = await request.json()
    seat_count = int(body["seatCount"])
    if not game.min_players <= seat_count <= game.max_players:
        raise ValueError("Invalid seat count")
    async with request.app[LOCKS_KEY][session["id"]]:
        request.app[DB_KEY].update_settings(session["id"], body.get("settings", {}), seat_count)
    request.app[EVENTS_KEY].publish(session["id"])
    return web.Response(status=204)


async def update_players(request: web.Request) -> web.Response:
    session, game = session_and_player(request)
    if session.get("spectator"):
        raise PermissionError("Spectators cannot change players")
    body = await request.json()
    db = request.app[DB_KEY]
    async with request.app[LOCKS_KEY][session["id"]]:
        db.apply_operations(session["id"], session, body.get("operations", []))
        refreshed = db.session_by_token(session["access_token"])
        players = db.players(session["id"])
        ready = [player for player in players if is_seated_player(player) and player["ready"]]
        if refreshed["phase"] == "new" and len(ready) == refreshed["seat_count"]:
            setup = {
                "randomSeed": refreshed["random_seed"],
                "players": [player_for_game(player) for player in players if is_seated_player(player)],
                "settings": json_value(refreshed["settings"], {}),
            }
            update = await asyncio.to_thread(request.app[RUNNER_KEY].initial_state, game.server_bundle, setup, game.play_manifest)
            db.start_session(session["id"], update)
    request.app[EVENTS_KEY].publish(session["id"])
    return web.Response(status=204)


async def process_move(request: web.Request) -> web.Response:
    session, game = session_and_player(request)
    if session["phase"] != "started":
        raise ValueError("The session is not in the started phase")
    if session["position"] is None or session.get("spectator"):
        raise PermissionError("Spectators cannot move")
    move_data = await request.json()
    db = request.app[DB_KEY]
    async with request.app[LOCKS_KEY][session["id"]]:
        current = db.session_by_token(session["access_token"])
        update = json_value(current["state"], {})
        position = effective_position(db, current, update)
        if position is None:
            raise PermissionError("No player perspective selected")
        move = {"position": position, "data": move_data}
        previous_players = update["game"].get("currentPlayers", [])
        started = time.perf_counter()
        next_update = await asyncio.to_thread(
            request.app[RUNNER_KEY].process_move,
            game.server_bundle,
            update["game"],
            move,
            game.play_manifest,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        db.save_move(session["id"], position, move_data, next_update, elapsed)
    moves = move_data if isinstance(move_data, list) else [move_data]
    private_reorder = (
        bool(moves)
        and all(str(item.get("name", "")).startswith("reorder-") for item in moves)
        and next_update["game"].get("currentPlayers", []) == previous_players
    )
    request.app[EVENTS_KEY].publish(
        session["id"],
        access_tokens={session["access_token"]} if private_reorder else None,
    )
    return web.Response(status=204)


async def undo(request: web.Request) -> web.Response:
    session, _ = session_and_player(request)
    position = effective_position(request.app[DB_KEY], session)
    async with request.app[LOCKS_KEY][session["id"]]:
        if session.get("spectator") or not request.app[DB_KEY].undo(session["id"], position, bool(session["host"])):
            raise PermissionError("Solo puedes deshacer tu Ãºltimo movimiento antes de que juegue el rival")
    request.app[EVENTS_KEY].publish(session["id"])
    return web.Response(status=204)


async def set_perspective(request: web.Request) -> web.Response:
    session, _ = session_and_player(request)
    if not is_administrator(session) and not session.get("spectator"):
        raise PermissionError("Only administrators and spectators can change perspective")
    body = await request.json()
    value = body.get("position")
    position = None if value is None else int(value)
    valid = {-1}
    valid.update(int(player["position"]) for player in request.app[DB_KEY].players(session["id"]) if is_seated_player(player))
    if position is not None and position not in valid:
        raise ValueError("Invalid player perspective")
    request.app[DB_KEY].set_view_position(session["access_token"], position)
    request.app[EVENTS_KEY].publish(session["id"], access_tokens={session["access_token"]})
    return web.Response(status=204)


async def navigate_history(request: web.Request) -> web.Response:
    session, _ = session_and_player(request)
    if not is_administrator(session):
        raise PermissionError("Only the administrator can navigate history")
    body = await request.json()
    async with request.app[LOCKS_KEY][session["id"]]:
        branch_id = body.get("branchID")
        changed = request.app[DB_KEY].navigate_history(
            session["id"],
            str(body["command"]),
            int(branch_id) if branch_id is not None else None,
        )
    if changed:
        request.app[EVENTS_KEY].publish(session["id"])
    return web.json_response({"changed": changed, "history": request.app[DB_KEY].history_status(session["id"])})


async def create_history_checkpoint(request: web.Request) -> web.Response:
    session, _ = session_and_player(request)
    if not is_administrator(session):
        raise PermissionError("Only the administrator can create checkpoints")
    body = await request.json()
    checkpoint_id = request.app[DB_KEY].create_checkpoint(session["id"], str(body.get("name", "")))
    return web.json_response({
        "checkpointID": checkpoint_id,
        "history": request.app[DB_KEY].history_status(session["id"]),
    })


async def delete_session(request: web.Request) -> web.Response:
    session, _ = session_and_player(request)
    if not session["host"]:
        raise PermissionError("Only the host can delete a room")
    request.app[DB_KEY].delete_session(session["id"])
    request.app[EVENTS_KEY].publish(session["id"], "deleted")
    return web.Response(status=204)


async def session_events(request: web.Request) -> web.StreamResponse:
    session, _ = session_and_player(request)
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)
    bus = request.app[EVENTS_KEY]
    queue = bus.subscribe(session["id"], session["access_token"])

    async def send(event: dict[str, Any]) -> None:
        payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        await response.write(f"data: {payload}\n\n".encode())

    try:
        for event in snapshot_events(request.app[DB_KEY], session):
            await send(event)
        while True:
            try:
                signal = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                await send({"type": "ping"})
                continue
            if signal == "deleted":
                await send({"type": "roomDeleted"})
                break
            if signal == "revision":
                await send({"type": "gameRevision"})
                continue
            refreshed = request.app[DB_KEY].session_by_token(request.match_info["token"])
            for event in snapshot_events(request.app[DB_KEY], refreshed):
                await send(event)
    except (ConnectionResetError, asyncio.CancelledError, LookupError):
        pass
    finally:
        bus.unsubscribe(session["id"], queue)
    return response


async def session_socket(request: web.Request) -> web.WebSocketResponse:
    session, _ = session_and_player(request)
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    bus = request.app[EVENTS_KEY]
    queue = bus.subscribe(session["id"], session["access_token"])

    async def send(event: dict[str, Any]) -> None:
        await socket.send_str(json.dumps(event, separators=(",", ":"), ensure_ascii=False))

    receive_task = asyncio.create_task(socket.receive())
    queue_task: asyncio.Task[str] | None = None
    try:
        for event in snapshot_events(request.app[DB_KEY], session):
            await send(event)
        while not socket.closed:
            queue_task = asyncio.create_task(queue.get())
            completed, _ = await asyncio.wait(
                {queue_task, receive_task},
                timeout=15,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in completed:
                break
            if queue_task not in completed:
                queue_task.cancel()
                await send({"type": "ping"})
                continue
            signal = queue_task.result()
            if signal == "deleted":
                await send({"type": "roomDeleted"})
                break
            if signal == "revision":
                await send({"type": "gameRevision"})
                continue
            refreshed = request.app[DB_KEY].session_by_token(request.match_info["token"])
            for event in snapshot_events(request.app[DB_KEY], refreshed):
                await send(event)
    except (ConnectionResetError, asyncio.CancelledError, LookupError):
        pass
    finally:
        if queue_task is not None and not queue_task.done():
            queue_task.cancel()
        if not receive_task.done():
            receive_task.cancel()
        bus.unsubscribe(session["id"], queue)
    return socket


async def ui_html(request: web.Request) -> web.Response:
    session, game = session_and_player(request)
    position = effective_position(request.app[DB_KEY], session)
    viewed_player = next((player for player in request.app[DB_KEY].players(session["id"]) if is_seated_player(player) and player["position"] == position), None)
    bootstrap = {
        "host": bool(session["host"]),
        "userID": viewed_player["session_user_id"] if viewed_player else session["session_user_id"],
        "gameName": game.friendly_name,
        "gameCover": game.public()["cover"],
        "inviteURL": f"/join/{session['invite_code']}",
        "minPlayers": game.min_players,
        "maxPlayers": game.max_players,
        "defaultPlayers": game.default_players,
        "dev": False,
    }
    encoded = html.escape(json.dumps(bootstrap, separators=(",", ":")), quote=True)
    token = html.escape(request.match_info["token"], quote=True)
    play_bootstrap = ""
    if game.play_manifest is not None:
        manifest_json = json.dumps(game.play_manifest, ensure_ascii=False).replace("<", "\\u003c")
        play_bootstrap = f"<script>globalThis.__PNPINK_PLAY_MANIFEST__={manifest_json};</script>"
    document = f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><style>html,body,#root{{width:100%;height:100%;margin:0;overflow:hidden}}</style><link rel=\"stylesheet\" href=\"/sessions/{token}/ui.css\"><link rel=\"stylesheet\" href=\"/static/game-shell.css\"></head><body data-bootstrap-json=\"{encoded}\"><div id=\"root\"></div>{play_bootstrap}<script src=\"/sessions/{token}/ui.js\"></script><script src=\"/static/game-shell.js\"></script><script>window.top.postMessage({{type:'sendDark'}},'*');</script></body></html>"""
    return web.Response(text=document, content_type="text/html", headers={"Cache-Control": "no-store"})


async def ui_asset(request: web.Request) -> web.StreamResponse:
    session, game = session_and_player(request)
    relative = request.match_info["path"]
    if relative == "ui.js":
        relative = "index.js"
    elif relative == "ui.css":
        relative = "index.css"
    target = (game.root / relative).resolve()
    if game.root not in target.parents or not target.is_file():
        target = (game.ui_directory / relative).resolve()
        if game.ui_directory not in target.parents or not target.is_file():
            raise web.HTTPNotFound()
    content_type, _ = mimetypes.guess_type(target)
    return asset_response(request, target, headers={"Cache-Control": "private, no-cache"}, content_type=content_type)


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc)) from exc
    except PermissionError as exc:
        raise web.HTTPForbidden(text=str(exc)) from exc
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    except Exception as exc:
        request.app.logger.exception("Unhandled request error")
        raise web.HTTPInternalServerError(text=str(exc)) from exc


async def add_utf8_charset(request: web.Request, response: web.StreamResponse) -> None:
    content_type = response.headers.get("Content-Type", "")
    media_type = content_type.partition(";")[0].strip().lower()
    if "charset=" not in content_type.lower() and (
        media_type.startswith("text/")
        or media_type in {"application/javascript", "application/json", "application/manifest+json"}
    ):
        response.headers["Content-Type"] = f"{content_type}; charset=utf-8"


def _apply_revision_changes(app: web.Application, changes) -> None:
    for change in changes:
        result = app[DB_KEY].apply_game_revision(
            change.game_name,
            change.previous,
            change.current,
            change.kind,
            change.updates,
        )
        for session_id in result["promoted"]:
            app[EVENTS_KEY].publish(session_id, "revision")
        for session_id in result["deleted"]:
            app[EVENTS_KEY].publish(session_id, "deleted")


async def _start_revision_watcher(app: web.Application) -> None:
    async def watch() -> None:
        while True:
            await asyncio.sleep(1)
            try:
                _apply_revision_changes(app, app[GAMES_KEY].refresh())
            except Exception:
                app.logger.exception("Unable to refresh game revisions")

    app[WATCHER_KEY] = asyncio.create_task(watch())


async def _stop_revision_watcher(app: web.Application) -> None:
    task = app.get(WATCHER_KEY)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app(game_roots: list[Path], database_path: Path) -> web.Application:
    app = web.Application(middlewares=[errors], client_max_size=1024 * 1024)
    app.on_response_prepare.append(add_utf8_charset)
    app[DB_KEY] = Database(database_path)
    app[GAMES_KEY] = GameRegistry(game_roots)
    app[RUNNER_KEY] = GameRunner()
    app[EVENTS_KEY] = EventBus()
    app[LOCKS_KEY] = defaultdict(asyncio.Lock)
    app[DB_KEY].bind_unversioned_sessions({game.name: game.revision for game in app[GAMES_KEY].all()})
    _apply_revision_changes(app, app[GAMES_KEY].refresh())
    app.on_startup.append(_start_revision_watcher)
    app.on_cleanup.append(_stop_revision_watcher)
    app.router.add_get("/", index)
    app.router.add_get("/join/{code}", join_page)
    app.router.add_get("/s/{token}", session_page)
    app.router.add_get("/designer/{name}", designer_page)
    app.router.add_get("/{name:manifest\\.webmanifest|app-icon\\.svg|sw\\.js}", pwa_asset)
    app.router.add_get("/static/{name}", web_asset)
    app.router.add_get("/games", list_games)
    app.router.add_get("/designer-api/games/{name}", get_game_definition)
    app.router.add_put("/designer-api/games/{name}", save_game_definition)
    app.router.add_get("/designer-api/games/{name}/revisions", game_revisions)
    app.router.add_post("/designer-api/games/{name}/revisions/{revision}/restore", restore_game_revision)
    app.router.add_delete("/designer-api/games/{name}/revisions/{revision}", delete_game_revision)
    app.router.add_get("/games/{name}/cover", game_cover)
    app.router.add_get("/rooms", list_rooms)
    app.router.add_post("/users", create_user)
    app.router.add_get("/users/me", get_user)
    app.router.add_get("/users/me/rooms", list_user_rooms)
    app.router.add_post("/sessions", create_session)
    app.router.add_get("/invites/{code}", invite_details)
    app.router.add_post("/invites/{code}", join_session)
    app.router.add_post("/invites/{code}/spectate", spectate_session)
    app.router.add_get("/sessions/{token}", get_session)
    app.router.add_get("/sessions/{token}/snapshot", get_snapshot)
    app.router.add_get("/sessions/{token}/socket", session_socket)
    app.router.add_get("/sessions/{token}/events", session_events)
    app.router.add_post("/sessions/{token}/settings", update_settings)
    app.router.add_post("/sessions/{token}/players/operations", update_players)
    app.router.add_post("/sessions/{token}/moves", process_move)
    app.router.add_post("/sessions/{token}/undo", undo)
    app.router.add_post("/sessions/{token}/perspective", set_perspective)
    app.router.add_post("/sessions/{token}/history", navigate_history)
    app.router.add_post("/sessions/{token}/history/checkpoints", create_history_checkpoint)
    app.router.add_delete("/sessions/{token}", delete_session)
    app.router.add_get("/sessions/{token}/ui.html", ui_html)
    app.router.add_get("/sessions/{token}/{path:.*}", ui_asset)
    return app
