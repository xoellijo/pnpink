from __future__ import annotations

import asyncio
import html
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


def json_value(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def player_for_game(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": player["session_user_id"],
        "position": player["position"],
        "name": player["name"],
        "avatar": f"/avatar/{player['name']}",
        "color": player["color"],
        "settings": json_value(player["settings"], {}),
        "host": bool(player["host"]),
    }


def game_for_session(request: web.Request, session: dict[str, Any]) -> GameDefinition:
    return request.app[GAMES_KEY].get(session["game_name"])


def is_administrator(session: dict[str, Any]) -> bool:
    return bool(session.get("host") and session.get("admin_mode"))


def session_and_player(request: web.Request) -> tuple[dict[str, Any], GameDefinition]:
    session = request.app[DB_KEY].session_by_token(request.match_info["token"])
    return session, game_for_session(request, session)


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


def users_event(db: Database, session: dict[str, Any]) -> dict[str, Any]:
    viewer_is_host = bool(session["host"])
    users = []
    for player in db.players(session["id"]):
        details = None
        if player["position"] is not None:
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
                "avatar": f"/avatar/{player['name']}",
                "playerDetails": details,
            }
        )
    return {"type": "users", "users": users}


def effective_position(db: Database, session: dict[str, Any], update: dict[str, Any] | None = None) -> int | None:
    if bool(session.get("spectator")) or session.get("view_position") == -1:
        current_players = (update or json_value(session.get("state"), {})).get("game", {}).get("currentPlayers", [])
        if current_players:
            return int(current_players[0])
        seated = [player["position"] for player in db.players(session["id"]) if player["position"] is not None]
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


async def web_asset(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if "/" in name or "\\" in name:
        raise web.HTTPNotFound()
    target = files("boardzilla_lite").joinpath(f"web/{name}")
    if not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


async def pwa_asset(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in {"manifest.webmanifest", "app-icon.svg", "sw.js"}:
        raise web.HTTPNotFound()
    target = files("boardzilla_lite").joinpath(f"web/{name}")
    headers = {"Cache-Control": "no-cache"} if name == "sw.js" else {}
    if name == "sw.js":
        headers["Service-Worker-Allowed"] = "/"
    return web.FileResponse(target, headers=headers)


async def list_games(request: web.Request) -> web.Response:
    return web.json_response({"games": [game.public() for game in request.app[GAMES_KEY].all()]})


async def list_rooms(request: web.Request) -> web.Response:
    rooms = [public_room(room, request.app[GAMES_KEY]) for room in request.app[DB_KEY].rooms()]
    return web.json_response({"rooms": rooms})


async def create_session(request: web.Request) -> web.Response:
    body = await request.json()
    game = request.app[GAMES_KEY].get(body["gameName"])
    seat_count = int(body.get("seatCount", game.default_players))
    if not game.min_players <= seat_count <= game.max_players:
        raise ValueError(f"seatCount must be between {game.min_players} and {game.max_players}")
    seat = await asyncio.to_thread(request.app[RUNNER_KEY].seat_player, game.server_bundle, [], seat_count, game.play_manifest)
    result = request.app[DB_KEY].create_session(
        game.name,
        str(body.get("name", "Host")).strip() or "Host",
        seat_count,
        bool(body.get("adminMode", body.get("allowUndo", True))),
        seat,
    )
    return web.json_response(result, status=201)


async def invite_details(request: web.Request) -> web.Response:
    session = request.app[DB_KEY].session_by_invite(request.match_info["code"])
    game = game_for_session(request, session)
    return web.json_response({"name": game.name, "friendlyName": game.friendly_name, "phase": session["phase"]})


async def join_session(request: web.Request) -> web.Response:
    body = await request.json()
    db = request.app[DB_KEY]
    session = db.session_by_invite(request.match_info["code"])
    game = game_for_session(request, session)
    players = [player_for_game(player) for player in db.players(session["id"]) if player["position"] is not None]
    seat = await asyncio.to_thread(request.app[RUNNER_KEY].seat_player, game.server_bundle, players, session["seat_count"], game.play_manifest)
    result = db.join_session(session["invite_code"], str(body.get("name", "Player")).strip() or "Player", seat)
    request.app[EVENTS_KEY].publish(session["id"])
    return web.json_response(result, status=201)


async def spectate_session(request: web.Request) -> web.Response:
    body = await request.json()
    db = request.app[DB_KEY]
    session = db.session_by_invite(request.match_info["code"])
    result = db.spectate_session(session["invite_code"], str(body.get("name", "Spectator")).strip() or "Spectator")
    request.app[EVENTS_KEY].publish(session["id"])
    return web.json_response(result, status=201)


async def get_session(request: web.Request) -> web.Response:
    session, game = session_and_player(request)
    position = effective_position(request.app[DB_KEY], session)
    current_players = json_value(session.get("state"), {}).get("game", {}).get("currentPlayers", [])
    players = [
        {"position": player["position"], "name": player["name"], "color": player["color"]}
        for player in request.app[DB_KEY].players(session["id"])
        if player["position"] is not None
    ]
    return web.json_response(
        {
            "host": bool(session["host"]),
            "administrator": is_administrator(session),
            "adminMode": bool(session["admin_mode"]),
            "spectator": bool(session.get("spectator")),
            "position": session["position"],
            "viewPosition": session.get("view_position"),
            "effectivePosition": position,
            "players": players,
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
    body = await request.json()
    db = request.app[DB_KEY]
    async with request.app[LOCKS_KEY][session["id"]]:
        db.apply_operations(session["id"], session, body.get("operations", []))
        refreshed = db.session_by_token(session["access_token"])
        players = db.players(session["id"])
        ready = [player for player in players if player["position"] is not None and player["ready"]]
        if refreshed["phase"] == "new" and len(ready) == refreshed["seat_count"]:
            setup = {
                "randomSeed": refreshed["random_seed"],
                "players": [player_for_game(player) for player in players if player["position"] is not None],
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
    if not is_administrator(session):
        raise PermissionError("Only the administrator can change perspective")
    body = await request.json()
    value = body.get("position")
    position = None if value is None else int(value)
    valid = {-1}
    valid.update(int(player["position"]) for player in request.app[DB_KEY].players(session["id"]) if player["position"] is not None)
    if position is not None and position not in valid:
        raise ValueError("Invalid player perspective")
    request.app[DB_KEY].set_view_position(session["access_token"], position)
    request.app[EVENTS_KEY].publish(session["id"])
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
    viewed_player = next((player for player in request.app[DB_KEY].players(session["id"]) if player["position"] == position), None)
    bootstrap = {
        "host": bool(session["host"]),
        "userID": viewed_player["session_user_id"] if viewed_player else session["session_user_id"],
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
    document = f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><style>html,body,#root{{width:100%;height:100%;margin:0;overflow:hidden}}</style><link rel=\"stylesheet\" href=\"/sessions/{token}/ui.css\"></head><body data-bootstrap-json=\"{encoded}\"><div id=\"root\"></div>{play_bootstrap}<script src=\"/sessions/{token}/ui.js\"></script><script>window.top.postMessage({{type:'sendDark'}},'*');</script></body></html>"""
    return web.Response(text=document, content_type="text/html", headers={"Cache-Control": "no-store"})


async def ui_asset(request: web.Request) -> web.FileResponse:
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
    return web.FileResponse(target, headers={"Content-Type": content_type or "application/octet-stream", "Cache-Control": "no-store"})


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


def create_app(game_roots: list[Path], database_path: Path) -> web.Application:
    app = web.Application(middlewares=[errors], client_max_size=1024 * 1024)
    app[DB_KEY] = Database(database_path)
    app[GAMES_KEY] = GameRegistry(game_roots)
    app[RUNNER_KEY] = GameRunner()
    app[EVENTS_KEY] = EventBus()
    app[LOCKS_KEY] = defaultdict(asyncio.Lock)
    app.router.add_get("/", index)
    app.router.add_get("/join/{code}", join_page)
    app.router.add_get("/s/{token}", session_page)
    app.router.add_get("/{name:manifest\\.webmanifest|app-icon\\.svg|sw\\.js}", pwa_asset)
    app.router.add_get("/static/{name}", web_asset)
    app.router.add_get("/games", list_games)
    app.router.add_get("/rooms", list_rooms)
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
    app.router.add_delete("/sessions/{token}", delete_session)
    app.router.add_get("/sessions/{token}/ui.html", ui_html)
    app.router.add_get("/sessions/{token}/{path:.*}", ui_asset)
    return app

