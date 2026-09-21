import json
import sqlite3
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

    def user(self, name="Host"):
        created = self.db.create_user(name)
        return self.db.user_by_token(created["token"])

    def started_session(self):
        created = self.db.create_session("example", self.user(), 2, True, {"position": 1, "color": "#fff"})
        session = self.db.session_by_token(created["playerAccessToken"])
        initial = {"game": {"phase": "started", "currentPlayers": [1]}, "players": []}
        self.db.start_session(session["id"], initial)
        return created, session, initial

    def test_sessions_survive_new_database_instance(self):
        created = self.db.create_session("example", self.user(), 2, True, {"position": 1, "color": "#fff"})
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
        created = self.db.create_session("example", self.user(), 2, True, {"position": 1, "color": "#fff"})
        spectator = self.db.spectate_session(created["inviteCode"], self.user("Viewer"))
        reopened = Database(self.db.path)
        session = reopened.session_by_token(spectator["token"])
        room = reopened.rooms()[0]
        self.assertTrue(session["spectator"])
        self.assertIsNone(session["position"])
        self.assertEqual(room["player_count"], 1)
        self.assertEqual(room["spectator_count"], 1)

    def test_administrator_perspective_is_persistent(self):
        created = self.db.create_session("example", self.user(), 2, True, {"position": 1, "color": "#fff"})
        self.db.set_view_position(created["playerAccessToken"], -1)
        reopened = Database(self.db.path)
        self.assertEqual(reopened.session_by_token(created["playerAccessToken"])["view_position"], -1)

    def test_user_can_join_multiple_games_and_resume_same_seat(self):
        host = self.user("Xoel")
        guest = self.user("Ana")
        first = self.db.create_session("example", host, 2, True, {"position": 1, "color": "#fff"})
        second = self.db.create_session("example", host, 2, True, {"position": 1, "color": "#fff"})
        joined = self.db.join_session(first["inviteCode"], guest, {"position": 2, "color": "#000"})
        resumed = self.db.join_session(first["inviteCode"], guest, {"position": 2, "color": "#000"})
        self.assertEqual(joined["token"], resumed["token"])
        self.assertEqual(len(self.db.rooms_for_user(host["id"])), 2)
        self.assertEqual(len(self.db.rooms_for_user(guest["id"])), 1)
        self.assertNotEqual(first["playerAccessToken"], second["playerAccessToken"])

    def test_game_revisions_promote_visual_changes_and_delete_incompatible_latest_only(self):
        host = self.user("Versioned")
        visual = self.db.create_session("example", host, 2, True, {"position": 1, "color": "#fff"}, "v1")
        result = self.db.apply_game_revision(
            "example", "v1", "v2", "assets",
            {"mode": "versioned", "assets": "live", "components": "new-games", "rules": "pinned"},
        )
        self.assertEqual(result["promoted"], [visual["sessionID"]])
        self.assertEqual(self.db.session_by_token(visual["playerAccessToken"])["game_revision"], "v2")
        self.assertEqual(self.db.revision_usage("example")["v2"]["total"], 1)

        incompatible = self.db.create_session("example", host, 2, True, {"position": 1, "color": "#fff"}, "v2")
        result = self.db.apply_game_revision(
            "example", "v2", "v3", "components",
            {"mode": "latest-only", "assets": "live", "components": "new-games", "rules": "pinned"},
        )
        self.assertEqual(set(result["deleted"]), {visual["sessionID"], incompatible["sessionID"]})
        with self.assertRaises(LookupError):
            self.db.session_by_token(incompatible["playerAccessToken"])

    def test_incompatible_legacy_database_is_archived(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE session_players (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                name TEXT NOT NULL
            );
            PRAGMA user_version = 3;
            """
        )
        connection.close()

        database = Database(legacy_path)

        with database.connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(session_players)")}
        self.assertIn("user_id", columns)
        self.assertEqual(len(list(Path(self.temp.name).glob("legacy.db.legacy-*"))), 1)

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
        self.assertEqual({branch["path"] for branch in status["branches"]}, {"1", "1.2"})
        self.assertEqual(status["currentBranch"], "1.2")
        alternate_branch = next(branch for branch in status["branches"] if branch["path"] == "1.2")
        self.assertTrue(self.db.navigate_history(session["id"], "branch", alternate_branch["nodeID"]))
        current = self.db.session_by_token(session["access_token"])
        self.assertEqual(json.loads(current["state"])["marker"], "alternate")

    def test_branch_selection_targets_tip_and_preserves_forward_path(self):
        _, session, _ = self.started_session()

        def move(marker, position=1):
            state = {"game": {"phase": "started", "currentPlayers": [position]}, "players": [], "marker": marker}
            self.db.save_move(session["id"], position, {"marker": marker}, state, 1)

        move("shared")
        move("main-2", 2)
        move("main-3", 1)
        self.db.navigate_history(session["id"], "back-action")
        self.db.navigate_history(session["id"], "back-action")
        move("fork-2", 2)
        move("fork-3", 1)
        status = self.db.history_status(session["id"])
        main = next(branch for branch in status["branches"] if branch["path"] == "1")
        fork = next(branch for branch in status["branches"] if branch["path"] == "1.2")

        self.assertTrue(self.db.navigate_history(session["id"], "branch", main["nodeID"]))
        self.assertEqual(json.loads(self.db.session_by_token(session["access_token"])["state"])["marker"], "main-3")
        self.assertTrue(self.db.navigate_history(session["id"], "branch", fork["nodeID"]))
        self.assertEqual(json.loads(self.db.session_by_token(session["access_token"])["state"])["marker"], "fork-3")
        self.assertEqual((self.db.history_status(session["id"])["actionIndex"], self.db.history_status(session["id"])["actionTotal"]), (3, 3))
        self.assertTrue(self.db.navigate_history(session["id"], "back-action"))
        self.assertTrue(self.db.navigate_history(session["id"], "back-action"))
        self.assertEqual(self.db.history_status(session["id"])["currentBranch"], "1.2")
        self.assertTrue(self.db.navigate_history(session["id"], "forward-action"))
        self.assertEqual(json.loads(self.db.session_by_token(session["access_token"])["state"])["marker"], "fork-2")

    def test_history_counts_and_direct_navigation_are_branch_relative(self):
        _, session, _ = self.started_session()
        for position, marker in ((1, 1), (1, 2), (2, 3), (2, 4), (1, 5)):
            state = {"game": {"phase": "started", "currentPlayers": [position]}, "players": [], "marker": marker}
            self.db.save_move(session["id"], position, {"marker": marker}, state, 1)
        status = self.db.history_status(session["id"])
        self.assertEqual((status["actionIndex"], status["actionTotal"]), (5, 5))
        self.assertEqual((status["turnIndex"], status["turnTotal"]), (3, 3))
        self.assertTrue(self.db.navigate_history(session["id"], "action", 2))
        self.assertEqual(json.loads(self.db.session_by_token(session["access_token"])["state"])["marker"], 2)
        self.assertTrue(self.db.navigate_history(session["id"], "turn", 2))
        self.assertEqual(json.loads(self.db.session_by_token(session["access_token"])["state"])["marker"], 4)

    def test_round_navigation_is_exposed_only_when_moves_define_rounds(self):
        _, session, _ = self.started_session()
        for round_number, marker in ((1, 1), (1, 2), (2, 3)):
            state = {"game": {"phase": "started", "currentPlayers": [1]}, "players": [], "marker": marker}
            self.db.save_move(session["id"], 1, {"round": round_number, "marker": marker}, state, 1)
        status = self.db.history_status(session["id"])
        self.assertEqual((status["roundIndex"], status["roundTotal"]), (2, 2))
        self.assertTrue(self.db.navigate_history(session["id"], "round", 1))
        self.assertEqual(json.loads(self.db.session_by_token(session["access_token"])["state"])["marker"], 2)

    def test_checkpoints_reference_history_without_copying_state(self):
        _, session, _ = self.started_session()
        changed = {"game": {"phase": "started", "currentPlayers": [2]}, "players": [], "marker": 1}
        self.db.save_move(session["id"], 1, {"name": "move"}, changed, 1)
        checkpoint_id = self.db.create_checkpoint(session["id"], "Before scoring")
        status = self.db.history_status(session["id"])
        self.assertEqual(status["checkpoints"], [{
            "id": checkpoint_id,
            "nodeID": status["currentNodeID"],
            "depth": 1,
            "path": "1",
            "name": "Before scoring",
            "createdAt": status["checkpoints"][0]["createdAt"],
        }])

    def test_nested_forks_use_chapter_style_paths(self):
        _, session, _ = self.started_session()

        def move(name):
            state = {"game": {"phase": "started", "currentPlayers": [1]}, "players": [], "marker": name}
            self.db.save_move(session["id"], 1, {"name": name}, state, 1)

        move("trunk")
        move("branch-one")
        self.db.navigate_history(session["id"], "back-action")
        move("branch-two")
        move("subbranch-one")
        self.db.navigate_history(session["id"], "back-action")
        move("subbranch-two")
        status = self.db.history_status(session["id"])
        self.assertEqual(status["currentBranch"], "1.2.2")
        self.assertEqual({branch["path"] for branch in status["branches"]}, {"1", "1.2", "1.2.2"})

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
