import json
import tempfile
import unittest
from pathlib import Path

from boardzilla_lite.database import Database
from boardzilla_lite.history_codec import DELTA, SNAPSHOT, SNAPSHOT_INTERVAL, apply_history, encode_history


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    def started_session(self):
        created = self.db.create_session("example", "Host", 2, True, {"position": 1, "color": "#fff"})
        session = self.db.session_by_token(created["playerAccessToken"])
        initial = {"game": {"phase": "started", "currentPlayers": [1]}, "players": []}
        self.db.start_session(session["id"], initial)
        return created, session, initial

    def test_sessions_survive_new_database_instance(self):
        created = self.db.create_session("example", "Host", 2, True, {"position": 1, "color": "#fff"})
        reopened = Database(self.db.path)
        session = reopened.session_by_token(created["playerAccessToken"])
        self.assertEqual(session["game_name"], "example")
        self.assertEqual(session["invite_code"], created["inviteCode"])

    def test_undo_restores_initial_state(self):
        created, session, initial = self.started_session()
        changed = {"game": {"phase": "started", "currentPlayers": [2]}, "players": []}
        self.db.save_move(session["id"], 1, {"name": "move"}, changed, 1)
        self.assertTrue(self.db.can_undo(session["id"], 1))
        self.assertFalse(self.db.can_undo(session["id"], 2))
        self.assertTrue(self.db.can_undo(session["id"], 2, administrator=True))
        self.assertFalse(self.db.undo(session["id"], 2))
        self.assertTrue(self.db.undo(session["id"], 2, administrator=True))
        restored = self.db.session_by_token(created["playerAccessToken"])
        self.assertIn('"currentPlayers":[1]', restored["state"])
        self.assertTrue(self.db.history_status(session["id"])["canForward"])

    def test_spectator_is_persistent_and_not_counted_as_player(self):
        created = self.db.create_session("example", "Host", 2, True, {"position": 1, "color": "#fff"})
        spectator = self.db.spectate_session(created["inviteCode"], "Viewer")
        reopened = Database(self.db.path)
        session = reopened.session_by_token(spectator["token"])
        room = reopened.rooms()[0]
        self.assertTrue(session["spectator"])
        self.assertIsNone(session["position"])
        self.assertEqual(room["player_count"], 1)
        self.assertEqual(room["spectator_count"], 1)

    def test_administrator_perspective_is_persistent(self):
        created = self.db.create_session("example", "Host", 2, True, {"position": 1, "color": "#fff"})
        self.db.set_view_position(created["playerAccessToken"], -1)
        reopened = Database(self.db.path)
        self.assertEqual(reopened.session_by_token(created["playerAccessToken"])["view_position"], -1)

    def test_turn_navigation_groups_consecutive_actions(self):
        _, session, _ = self.started_session()
        for position, marker in ((1, 1), (1, 2), (2, 3), (2, 4), (1, 5)):
            update = {"game": {"phase": "started", "currentPlayers": [position]}, "players": [], "marker": marker}
            self.db.save_move(session["id"], position, {"marker": marker}, update, 1)
        self.assertTrue(self.db.navigate_history(session["id"], "back-turn"))
        current = self.db.session_by_token(session["access_token"])
        self.assertEqual(current["next_seq"], 4)
        self.assertEqual(json.loads(current["state"])["marker"], 4)
        self.assertTrue(self.db.navigate_history(session["id"], "back-turn"))
        current = self.db.session_by_token(session["access_token"])
        self.assertEqual(current["next_seq"], 2)
        self.assertTrue(self.db.navigate_history(session["id"], "forward-turn"))
        current = self.db.session_by_token(session["access_token"])
        self.assertEqual(current["next_seq"], 4)

    def test_new_move_from_the_past_creates_selectable_branch(self):
        _, session, _ = self.started_session()
        first = {"game": {"phase": "started", "currentPlayers": [2]}, "players": [], "marker": "first"}
        original = {"game": {"phase": "started", "currentPlayers": [1]}, "players": [], "marker": "original"}
        alternate = {"game": {"phase": "started", "currentPlayers": [1]}, "players": [], "marker": "alternate"}
        self.db.save_move(session["id"], 1, {"name": "first"}, first, 1)
        self.db.save_move(session["id"], 2, {"name": "original"}, original, 1)
        self.assertTrue(self.db.navigate_history(session["id"], "back-action"))
        self.db.save_move(session["id"], 2, {"name": "alternate"}, alternate, 1)
        self.assertTrue(self.db.navigate_history(session["id"], "back-action"))
        status = self.db.history_status(session["id"])
        self.assertEqual(status["forks"], 1)
        self.assertEqual(len(status["branches"]), 2)
        original_branch = next(branch for branch in status["branches"] if branch["move"]["name"] == "original")
        self.assertTrue(self.db.navigate_history(session["id"], "forward-action", original_branch["nodeID"]))
        current = self.db.session_by_token(session["access_token"])
        self.assertEqual(json.loads(current["state"])["marker"], "original")

    def test_history_codec_preserves_reordered_boardzilla_entities(self):
        previous = {
            "game": {"state": {"board": [{"_id": 0, "children": [{"_id": 1}, {"_id": 2}]}]}},
            "players": [{"position": 1, "state": {"board": [{"_ref": 0, "children": [{"_ref": 1}, {"_ref": 2}]}]}}],
        }
        current = {
            "game": {"state": {"board": [{"_id": 0, "children": [{"_id": 2}, {"_id": 1, "faceUp": True}]}]}},
            "players": [{"position": 1, "state": {"board": [{"_ref": 0, "children": [{"_ref": 2}, {"_ref": 1, "faceUp": True}]}]}}],
        }
        kind, payload = encode_history(previous, current, 1)
        self.assertEqual(kind, DELTA)
        self.assertEqual(apply_history(previous, kind, payload), current)

    def test_periodic_history_node_is_snapshot(self):
        _, session, _ = self.started_session()
        for marker in range(1, SNAPSHOT_INTERVAL + 1):
            update = {"game": {"phase": "started", "currentPlayers": [1]}, "players": [], "marker": marker}
            self.db.save_move(session["id"], 1, {"marker": marker}, update, 1)
        with self.db.connect() as connection:
            kinds = [row["payload_kind"] for row in connection.execute(
                "SELECT payload_kind FROM history_nodes WHERE session_id = ? ORDER BY depth", (session["id"],)
            )]
        self.assertEqual(kinds[:-1], [DELTA] * (SNAPSHOT_INTERVAL - 1))
        self.assertEqual(kinds[-1], SNAPSHOT)
        for _ in range(SNAPSHOT_INTERVAL):
            self.assertTrue(self.db.navigate_history(session["id"], "back-action"))
        restored = self.db.session_by_token(session["access_token"])
        self.assertEqual(json.loads(restored["state"]), {"game": {"phase": "started", "currentPlayers": [1]}, "players": []})


if __name__ == "__main__":
    unittest.main()
