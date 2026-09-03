import json
import os
import urllib.request


BASE = os.environ.get("BOARDZILLA_LITE_URL", "http://127.0.0.1:8082")


def request(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    request_value = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request_value) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


games = request("/games")
assert any(game["name"] == "pnpink-play-demo" for game in games["games"])

host = request("/sessions", "POST", {"gameName": "pnpink-play-demo", "name": "Host"})
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

snapshot = request(f"/sessions/{host['playerAccessToken']}/snapshot")["events"][0]
assert snapshot["type"] == "gameUpdate"
assert snapshot["currentPlayers"] == [1]


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


nodes = list(walk(snapshot["state"]))
deck = next(node for node in nodes if node.get("definitionId") == "deck")
host_hand = next(node for node in nodes if node.get("definitionId") == "hand" and node.get("ownerPosition") == 1)
request(
    f"/sessions/{host['playerAccessToken']}/moves",
    "POST",
    {"name": "draw", "args": {"source": f"$eid[{deck['_ref']}]", "target": f"$eid[{host_hand['_ref']}]"}},
)

host_state = request(f"/sessions/{host['playerAccessToken']}/snapshot")["events"][0]["state"]
guest_state = request(f"/sessions/{guest['token']}/snapshot")["events"][0]["state"]
host_card = next(
    node for node in walk(host_state)
    if node.get("className") == "PlayPiece" and node.get("componentId") in {"sun", "moon", "forest"}
)
guest_hidden_cards = [
    node for node in walk(guest_state)
    if node.get("className") == "PlayPiece" and "componentId" not in node
]
assert host_card["front"].startswith("assets/card-")
assert len(guest_hidden_cards) == 9
print(json.dumps({"inviteCode": host["inviteCode"], "phase": "started"}))
