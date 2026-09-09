import json
import os
import urllib.request
import urllib.error


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


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def element_arg(event, target):
    def find(node, path):
        if node is target:
            return path
        for index, child in enumerate(node.get("children", [])):
            branch = find(child, f"{path}/{index}")
            if branch is not None:
                return branch
        return None

    branch = find(event["state"]["board"][0], "0")
    assert branch is not None
    return f"$el[{branch}]"


def snapshot(token):
    return request(f"/sessions/{token}/snapshot")["events"][0]


game = next(game for game in request("/games")["games"] if game["name"] == "pnpink-play-demo")
assert (game["minPlayers"], game["maxPlayers"], game["defaultPlayers"]) == (4, 4, 4)

host = request("/sessions", "POST", {"gameName": game["name"], "name": "P1", "seatCount": 4})
players = [host]
for position in range(2, 5):
    players.append(request(f"/invites/{host['inviteCode']}", "POST", {"name": f"P{position}"}))

request(
    f"/sessions/{host['playerAccessToken']}/players/operations",
    "POST",
    {"operations": [
        {"type": "update", "userID": player["userID"], "ready": True}
        for player in players
    ]},
)

host_event = snapshot(host["playerAccessToken"])
assert host_event["currentPlayers"] == [1]
hands = [node for node in walk(host_event["state"]) if node.get("definitionId") == "hand"]
assert sorted(len(hand.get("children", [])) for hand in hands) == [13, 13, 13, 13]

host_hand = next(node for node in hands if node.get("ownerPosition") == 1)
player_area = next(node for node in walk(host_event["state"]) if node.get("definitionId") == "player-area" and node.get("ownerPosition") == 1)
table = next(node for node in walk(host_event["state"]) if node.get("definitionId") == "table")
group_cards = host_hand["children"][:4]

try:
    request(
        f"/sessions/{host['playerAccessToken']}/moves",
        "POST",
        {"name": "move", "args": {
            "piece": element_arg(host_event, group_cards[0]),
            "pieces": [element_arg(host_event, card) for card in group_cards],
            "target": element_arg(host_event, table),
        }},
    )
    invalid_result = snapshot(host["playerAccessToken"])
    invalid_table = next(node for node in walk(invalid_result["state"]) if node.get("definitionId") == "table")
    raise AssertionError(f"table accepted more than maxDrop components: {len(invalid_table.get('children', []))} moved")
except urllib.error.HTTPError as error:
    error_body = error.read().decode()
    assert error.code in {400, 500} and "not valid" in error_body.lower()

request(
    f"/sessions/{host['playerAccessToken']}/moves",
    "POST",
    {"name": "move", "args": {
        "piece": element_arg(host_event, group_cards[0]),
        "pieces": [element_arg(host_event, card) for card in group_cards],
        "target": element_arg(host_event, player_area),
    }},
)
after_group_move = snapshot(host["playerAccessToken"])
host_hand = next(node for node in walk(after_group_move["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") == 1)
player_area = next(node for node in walk(after_group_move["state"]) if node.get("definitionId") == "player-area" and node.get("ownerPosition") == 1)
assert len(host_hand["children"]) == 9
assert len(player_area["children"]) == 5

moved_cards = [card for card in player_area["children"] if card.get("componentId") != "reference"]
request(
    f"/sessions/{host['playerAccessToken']}/moves",
    "POST",
    {"name": "move", "args": {
        "piece": element_arg(after_group_move, moved_cards[0]),
        "pieces": [element_arg(after_group_move, card) for card in moved_cards],
        "target": element_arg(after_group_move, host_hand),
    }},
)

host_event = snapshot(host["playerAccessToken"])
host_hand = next(node for node in walk(host_event["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") == 1)
first_card, second_card = host_hand["children"][:2]
opponent_before = snapshot(players[1]["token"])
opponent_hand_before = next(node for node in walk(opponent_before["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") == 1)
request(
    f"/sessions/{host['playerAccessToken']}/moves",
    "POST",
    {"name": "reorder-hand", "args": {
        "__reorder_from__": element_arg(host_event, first_card),
        "__reorder_to__": element_arg(host_event, second_card),
    }},
)
after_reorder = snapshot(host["playerAccessToken"])
host_hand = next(node for node in walk(after_reorder["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") == 1)
assert len(host_hand["children"]) == 13
assert after_reorder["currentPlayers"] == [1]
opponent_after = snapshot(players[1]["token"])
opponent_hand_after = next(node for node in walk(opponent_after["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") == 1)
assert opponent_hand_after == opponent_hand_before

for position, player in enumerate(players, 1):
    token = player.get("playerAccessToken") or player["token"]
    event = snapshot(token)
    hand = next(node for node in walk(event["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") == position)
    opponent_hand = next(node for node in walk(event["state"]) if node.get("definitionId") == "hand" and node.get("ownerPosition") != position)
    assert all(card.get("faceUp") is True and card.get("front") for card in hand["children"])
    assert all(not card.get("front") and card.get("back") for card in opponent_hand["children"])
    card = hand["children"][0]
    request(
        f"/sessions/{token}/moves",
        "POST",
        {"name": "move", "args": {
            "piece": element_arg(event, card),
            "target": element_arg(event, next(node for node in walk(event['state']) if node.get('definitionId') == 'table')),
        }},
    )

resolution = snapshot(host["playerAccessToken"])
assert resolution["currentPlayers"] == [1]
assert any(node.get("trickComplete") is True for node in walk(resolution["state"]))

request(
    f"/sessions/{host['playerAccessToken']}/moves",
    "POST",
    {"name": "takeTrick", "args": {"winner": 3}},
)
next_trick = snapshot(players[2]["token"])
center = next(node for node in walk(next_trick["state"]) if node.get("definitionId") == "table")
tricks = next(node for node in walk(next_trick["state"]) if node.get("definitionId") == "tricks" and node.get("ownerPosition") == 3)
assert next_trick["currentPlayers"] == [3]
assert len(center.get("children", [])) == 0
assert len(tricks.get("children", [])) == 4
print(json.dumps({"inviteCode": host["inviteCode"], "nextPlayer": 3, "trickCards": 4}))
