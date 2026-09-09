import json
import os
import urllib.error
import urllib.request


BASE = os.environ.get("BOARDZILLA_TEST_URL", "http://127.0.0.1:8080")


def request(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


games = request("/games")
assert games["games"][0]["name"] == "dos-frentes"

host = request("/sessions", "POST", {"gameName": "dos-frentes", "name": "Host", "allowUndo": True})
guest = request(f"/invites/{host['inviteCode']}", "POST", {"name": "Guest"})

request(
    f"/sessions/{host['playerAccessToken']}/players/operations",
    "POST",
    {
        "operations": [
            {"type": "update", "userID": host["userID"], "ready": True},
            {"type": "update", "userID": guest["userID"], "ready": True},
        ]
    },
)

host_session = request(f"/sessions/{host['playerAccessToken']}")
guest_session = request(f"/sessions/{guest['token']}")
assert host_session["phase"] == "started"
assert guest_session["phase"] == "started"

host_snapshot = request(f"/sessions/{host['playerAccessToken']}/snapshot")
guest_snapshot = request(f"/sessions/{guest['token']}/snapshot")
assert host_snapshot["events"][0]["type"] == "gameUpdate"
assert host_snapshot["events"][0]["position"] == 1
assert guest_snapshot["events"][0]["position"] == 2

state = host_snapshot["events"][0]["state"]
market_tiles = sorted(
    (node for node in walk(state) if node.get("marketSlot", -1) >= 0),
    key=lambda node: node["marketSlot"],
)
market_tile = market_tiles[0]
demote_tile = market_tiles[1]
request(
    f"/sessions/{host['playerAccessToken']}/moves",
    "POST",
    {
        "name": "buy",
        "args": {
            "marketTile": f"$eid[{market_tile['_ref']}]",
            "demoteTile": f"$eid[{demote_tile['_ref']}]",
        },
    },
)
moved = request(f"/sessions/{host['playerAccessToken']}/snapshot")["events"][0]
assert moved["seq"] == 1
assert moved["state"] != state
assert moved["canUndo"] is True
guest_after_move = request(f"/sessions/{guest['token']}/snapshot")["events"][0]
assert guest_after_move["canUndo"] is False

spectator = request(f"/invites/{host['inviteCode']}/spectate", "POST", {"name": "Viewer"})
spectator_session = request(f"/sessions/{spectator['token']}")
spectator_snapshot = request(f"/sessions/{spectator['token']}/snapshot")["events"][0]
assert spectator_session["spectator"] is True
assert spectator_snapshot["position"] == moved["currentPlayers"][0]

request(f"/sessions/{host['playerAccessToken']}/perspective", "POST", {"position": 2})
assert request(f"/sessions/{host['playerAccessToken']}/snapshot")["events"][0]["position"] == 2
request(f"/sessions/{host['playerAccessToken']}/perspective", "POST", {"position": -1})
assert request(f"/sessions/{host['playerAccessToken']}/snapshot")["events"][0]["position"] == moved["currentPlayers"][0]
request(f"/sessions/{host['playerAccessToken']}/perspective", "POST", {"position": None})

back = request(f"/sessions/{host['playerAccessToken']}/history", "POST", {"command": "back-action"})
assert back["changed"] is True and back["history"]["index"] == 0
forward = request(f"/sessions/{host['playerAccessToken']}/history", "POST", {"command": "forward-action"})
assert forward["changed"] is True and forward["history"]["index"] == 1

try:
    request(f"/sessions/{guest['token']}/undo", "POST")
except urllib.error.HTTPError as error:
    assert error.code == 403
else:
    raise AssertionError("The opponent must not undo another player's move")

request(f"/sessions/{host['playerAccessToken']}/undo", "POST")
undone = request(f"/sessions/{host['playerAccessToken']}/snapshot")["events"][0]
assert undone["seq"] == 0
assert undone["state"] == state
assert undone["canUndo"] is False

rooms = request("/rooms")
assert any(
    room["inviteCode"] == host["inviteCode"]
    and room["phase"] == "started"
    and room["spectators"] == 1
    for room in rooms["rooms"]
)
print(json.dumps({"room": host["inviteCode"], "host": host_session, "guest": guest_session}, indent=2))

