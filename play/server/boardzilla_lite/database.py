from __future__ import annotations

import json
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .history_codec import DELTA, SNAPSHOT, apply_history, compress_state, decompress_state, encode_history, state_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    access_token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_name TEXT NOT NULL,
    game_revision TEXT,
    phase TEXT NOT NULL DEFAULT 'new',
    next_seq INTEGER NOT NULL DEFAULT 0,
    state BLOB,
    initial_state BLOB,
    invite_code TEXT NOT NULL UNIQUE,
    settings TEXT NOT NULL DEFAULT '{}',
    random_seed TEXT NOT NULL,
    allow_undo INTEGER NOT NULL DEFAULT 1,
    admin_mode INTEGER NOT NULL DEFAULT 1,
    seat_count INTEGER NOT NULL,
    current_node_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS session_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT,
    position INTEGER,
    host INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL DEFAULT '{}',
    access_token TEXT NOT NULL UNIQUE,
    ready INTEGER NOT NULL DEFAULT 0,
    view_position INTEGER,
    spectator INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, user_id),
    UNIQUE(session_id, session_user_id),
    UNIQUE(session_id, position)
);
CREATE TABLE IF NOT EXISTS history_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES history_nodes(id) ON DELETE CASCADE,
    depth INTEGER NOT NULL,
    payload_kind INTEGER NOT NULL,
    payload BLOB NOT NULL,
    move TEXT NOT NULL,
    position INTEGER NOT NULL,
    processing_time_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS history_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    node_id INTEGER REFERENCES history_nodes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS history_heads (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    node_id INTEGER REFERENCES history_nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS sessions_updated_idx ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS users_access_token_idx ON users(access_token);
CREATE INDEX IF NOT EXISTS session_players_session_idx ON session_players(session_id);
CREATE INDEX IF NOT EXISTS session_players_user_idx ON session_players(user_id, session_id);
CREATE INDEX IF NOT EXISTS history_nodes_parent_idx ON history_nodes(session_id, parent_id, id DESC);
CREATE INDEX IF NOT EXISTS history_checkpoints_session_idx ON history_checkpoints(session_id, node_id, id);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._archive_incompatible_database()
        with self.connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone()
            if tables and version == 3:
                connection.execute("ALTER TABLE sessions ADD COLUMN game_revision TEXT")
                version = 4
            if tables and version not in (0, 4):
                raise RuntimeError("The database uses an incompatible history format; start with a new database")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version = 4")

    def _archive_incompatible_database(self) -> None:
        if not self.path.is_file():
            return
        connection = sqlite3.connect(self.path)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if not tables:
                return
            player_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(session_players)")
            }
            user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            compatible = "users" in tables and {"user_id", "session_user_id"} <= player_columns and {
                "public_id",
                "access_token",
            } <= user_columns
        finally:
            connection.close()
        if compatible:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{self.path.name}.legacy-{timestamp}")
        self.path.replace(backup)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            if sidecar.exists():
                sidecar.replace(Path(f"{backup}{suffix}"))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def token(length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def invite_code(length: int = 6) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def create_user(self, name: str) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Player name is required")
        if len(clean_name) > 40:
            raise ValueError("Player name must be 40 characters or fewer")
        with self.connect() as connection:
            public_id = self.token()
            access_token = self.token(32)
            cursor = connection.execute(
                "INSERT INTO users(public_id, name, access_token) VALUES (?, ?, ?)",
                (public_id, clean_name, access_token),
            )
            user_id = int(cursor.lastrowid)
        return {"id": user_id, "publicID": public_id, "name": clean_name, "token": access_token}

    def user_by_token(self, token: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE access_token = ?", (token,)).fetchone()
        if row is None:
            raise LookupError("User token not found")
        return dict(row)

    def player_for_user(self, session_id: int, user_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_players WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def create_session(
        self,
        game_name: str,
        user: dict[str, Any],
        seat_count: int,
        admin_mode: bool,
        seat: dict[str, Any] | None,
        game_revision: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            invite_code = self.invite_code()
            access_token = self.token(20)
            random_seed = self.token(24)
            cursor = connection.execute(
                "INSERT INTO sessions(game_name, game_revision, invite_code, random_seed, allow_undo, admin_mode, seat_count) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (game_name, game_revision, invite_code, random_seed, int(admin_mode), seat_count),
            )
            session_id = int(cursor.lastrowid)
            connection.execute(
                """INSERT INTO session_players(
                    session_id, user_id, session_user_id, name, color, position, host, settings, access_token
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    session_id,
                    user["id"],
                    user["public_id"],
                    user["name"],
                    seat.get("color") if seat else None,
                    seat.get("position") if seat else None,
                    json.dumps(seat.get("settings", {}) if seat else {}),
                    access_token,
                ),
            )
        return {"sessionID": session_id, "inviteCode": invite_code, "playerAccessToken": access_token, "userID": user["public_id"]}

    def join_session(self, invite_code: str, user: dict[str, Any], seat: dict[str, Any] | None) -> dict[str, Any]:
        with self.connect() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE invite_code = ?", (invite_code.upper(),)).fetchone()
            if session is None:
                raise LookupError("Room not found")
            existing = connection.execute(
                "SELECT access_token, session_user_id FROM session_players WHERE session_id = ? AND user_id = ?",
                (session["id"], user["id"]),
            ).fetchone()
            if existing:
                return {"sessionID": session["id"], "token": existing["access_token"], "userID": existing["session_user_id"]}
            if session["phase"] != "new":
                raise ValueError("The game has already started")
            access_token = self.token(20)
            connection.execute(
                """INSERT INTO session_players(
                    session_id, user_id, session_user_id, name, color, position, settings, access_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session["id"],
                    user["id"],
                    user["public_id"],
                    user["name"],
                    seat.get("color") if seat else None,
                    seat.get("position") if seat else None,
                    json.dumps(seat.get("settings", {}) if seat else {}),
                    access_token,
                ),
            )
        return {"sessionID": session["id"], "token": access_token, "userID": user["public_id"]}

    def spectate_session(self, invite_code: str, user: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE invite_code = ?", (invite_code.upper(),)).fetchone()
            if session is None:
                raise LookupError("Room not found")
            existing = connection.execute(
                "SELECT access_token, session_user_id FROM session_players WHERE session_id = ? AND user_id = ?",
                (session["id"], user["id"]),
            ).fetchone()
            if existing:
                return {"sessionID": session["id"], "token": existing["access_token"], "userID": existing["session_user_id"]}
            access_token = self.token(20)
            connection.execute(
                "INSERT INTO session_players(session_id, user_id, session_user_id, name, host, settings, access_token, ready, spectator) VALUES (?, ?, ?, ?, 0, '{}', ?, 1, 1)",
                (session["id"], user["id"], user["public_id"], user["name"], access_token),
            )
        return {"sessionID": session["id"], "token": access_token, "userID": user["public_id"]}

    def session_by_token(self, token: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT s.*, p.session_user_id, p.name AS player_name, p.color, p.position,
                          p.host, p.settings AS player_settings, p.ready, p.access_token,
                          p.view_position, p.spectator
                   FROM session_players p JOIN sessions s ON s.id = p.session_id
                   WHERE p.access_token = ?""",
                (token,),
            ).fetchone()
        if row is None:
            raise LookupError("Session token not found")
        session = dict(row)
        session["state"] = state_text(session["state"])
        session["initial_state"] = state_text(session["initial_state"])
        return session

    def session_by_invite(self, code: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE invite_code = ?", (code.upper(),)).fetchone()
        if row is None:
            raise LookupError("Room not found")
        session = dict(row)
        session["state"] = state_text(session["state"])
        session["initial_state"] = state_text(session["initial_state"])
        return session

    def players(self, session_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT p.*, u.public_id
                   FROM session_players p JOIN users u ON u.id = p.user_id
                   WHERE p.session_id = ? ORDER BY p.position IS NULL, p.position, p.id""",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def rooms(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT s.*,
                          COUNT(CASE WHEN p.position IS NOT NULL AND p.spectator = 0 THEN 1 END) AS player_count,
                          COUNT(CASE WHEN p.spectator = 1 THEN 1 END) AS spectator_count
                   FROM sessions s LEFT JOIN session_players p ON p.session_id = s.id
                   GROUP BY s.id ORDER BY s.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def rooms_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT s.*, mine.access_token AS player_access_token, mine.position AS player_position,
                          mine.host AS player_host, mine.spectator AS player_spectator,
                          COUNT(CASE WHEN all_players.position IS NOT NULL AND all_players.spectator = 0 THEN 1 END) AS player_count,
                          COUNT(CASE WHEN all_players.spectator = 1 THEN 1 END) AS spectator_count
                   FROM session_players mine
                   JOIN sessions s ON s.id = mine.session_id
                   LEFT JOIN session_players all_players ON all_players.session_id = s.id
                   WHERE mine.user_id = ?
                   GROUP BY s.id, mine.id
                   ORDER BY s.updated_at DESC""",
                (user_id,),
            ).fetchall()
        rooms = []
        for row in rows:
            room = dict(row)
            room["state"] = state_text(room["state"])
            rooms.append(room)
        return rooms

    def update_settings(self, session_id: int, settings: Any, seat_count: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE sessions SET settings = ?, seat_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND phase = 'new'",
                (json.dumps(settings), seat_count, session_id),
            )

    def apply_operations(self, session_id: int, actor: dict[str, Any], operations: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            for operation in operations:
                user_id = str(operation.get("userID", ""))
                if not actor["host"] and user_id != actor["session_user_id"]:
                    raise PermissionError("Only the host can update another player")
                kind = operation.get("type")
                if kind == "seat":
                    target = connection.execute(
                        "SELECT spectator FROM session_players WHERE session_id = ? AND session_user_id = ?",
                        (session_id, user_id),
                    ).fetchone()
                    if target is None or target["spectator"]:
                        raise PermissionError("Spectators cannot occupy player seats")
                    connection.execute(
                        """UPDATE session_players SET position = ?, color = ?, name = ?, settings = ?, updated_at = CURRENT_TIMESTAMP
                           WHERE session_id = ? AND session_user_id = ?""",
                        (operation["position"], operation["color"], operation["name"], json.dumps(operation.get("settings", {})), session_id, user_id),
                    )
                elif kind == "unseat":
                    connection.execute(
                        "UPDATE session_players SET position = NULL, color = NULL, ready = 0, updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND session_user_id = ?",
                        (session_id, user_id),
                    )
                elif kind == "update":
                    fields: list[str] = []
                    values: list[Any] = []
                    for source, column in (("name", "name"), ("color", "color"), ("ready", "ready")):
                        if source in operation:
                            fields.append(f"{column} = ?")
                            values.append(int(operation[source]) if source == "ready" else operation[source])
                    if "settings" in operation:
                        fields.append("settings = ?")
                        values.append(json.dumps(operation["settings"]))
                    if fields:
                        values.extend((session_id, user_id))
                        connection.execute(
                            f"UPDATE session_players SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND session_user_id = ?",
                            values,
                        )
                else:
                    raise ValueError(f"Unknown player operation: {kind}")

    def start_session(self, session_id: int, update: dict[str, Any]) -> None:
        encoded = compress_state(update)
        with self.connect() as connection:
            connection.execute("DELETE FROM history_heads WHERE session_id = ?", (session_id,))
            connection.execute(
                "UPDATE sessions SET phase = 'started', state = ?, initial_state = ?, current_node_id = NULL, next_seq = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND phase = 'new'",
                (encoded, encoded, session_id),
            )

    def save_move(self, session_id: int, position: int, move: Any, update: dict[str, Any], processing_time_ms: int) -> None:
        encoded = compress_state(update)
        phase = update["game"]["phase"]
        with self.connect() as connection:
            session = connection.execute("SELECT current_node_id, state FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError("Session not found")
            parent_id = session["current_node_id"]
            parent = connection.execute("SELECT depth FROM history_nodes WHERE id = ?", (parent_id,)).fetchone() if parent_id else None
            depth = int(parent["depth"]) + 1 if parent else 1
            payload_kind, payload = encode_history(decompress_state(session["state"]), update, depth)
            cursor = connection.execute(
                "INSERT INTO history_nodes(session_id, parent_id, depth, payload_kind, payload, move, position, processing_time_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, parent_id, depth, payload_kind, payload, json.dumps(move), position, processing_time_ms),
            )
            node_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO history_heads(session_id, node_id) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET node_id = excluded.node_id",
                (session_id, node_id),
            )
            connection.execute(
                "UPDATE sessions SET state = ?, phase = ?, next_seq = ?, current_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (encoded, phase, depth, node_id, session_id),
            )

    @staticmethod
    def _node_state(connection: sqlite3.Connection, session: sqlite3.Row, node_id: int | None) -> dict[str, Any]:
        if node_id is None:
            return decompress_state(session["initial_state"])
        chain = []
        current_id: int | None = node_id
        while current_id is not None:
            node = connection.execute("SELECT * FROM history_nodes WHERE id = ?", (current_id,)).fetchone()
            if node is None:
                raise LookupError("History node not found")
            chain.append(node)
            if int(node["payload_kind"]) == SNAPSHOT:
                break
            current_id = node["parent_id"]
        if int(chain[-1]["payload_kind"]) == SNAPSHOT:
            state = apply_history({}, SNAPSHOT, chain[-1]["payload"])
            pending = reversed(chain[:-1])
        else:
            state = decompress_state(session["initial_state"])
            pending = reversed(chain)
        for node in pending:
            if int(node["payload_kind"]) != DELTA:
                state = apply_history({}, SNAPSHOT, node["payload"])
            else:
                state = apply_history(state, DELTA, node["payload"])
        return state

    def can_undo(self, session_id: int, position: int | None, administrator: bool = False) -> bool:
        if position is None:
            return False
        with self.connect() as connection:
            row = connection.execute(
                """SELECT s.allow_undo, n.position
                   FROM sessions s LEFT JOIN history_nodes n ON n.id = s.current_node_id
                   WHERE s.id = ?""",
                (session_id,),
            ).fetchone()
        return bool(
            row
            and row["allow_undo"]
            and row["position"] is not None
            and (administrator or row["position"] == position)
        )

    def undo(self, session_id: int, position: int | None, administrator: bool = False) -> bool:
        with self.connect() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None or not session["allow_undo"] or session["current_node_id"] is None or position is None:
                return False
            latest = connection.execute("SELECT * FROM history_nodes WHERE id = ?", (session["current_node_id"],)).fetchone()
            if latest is None or (not administrator and latest["position"] != position):
                return False
            previous = connection.execute("SELECT * FROM history_nodes WHERE id = ?", (latest["parent_id"],)).fetchone() if latest["parent_id"] else None
            state = self._node_state(connection, session, int(previous["id"]) if previous else None)
            depth = int(previous["depth"]) if previous else 0
            phase = state["game"]["phase"]
            connection.execute(
                "UPDATE sessions SET state = ?, phase = ?, next_seq = ?, current_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (compress_state(state), phase, depth, latest["parent_id"], session_id),
            )
            return True

    def set_view_position(self, access_token: str, position: int | None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE session_players SET view_position = ?, updated_at = CURRENT_TIMESTAMP WHERE access_token = ? AND (host = 1 OR spectator = 1)",
                (position, access_token),
            )

    @staticmethod
    def _child(connection: sqlite3.Connection, session_id: int, parent_id: int | None) -> sqlite3.Row | None:
        if parent_id is None:
            return connection.execute(
                "SELECT * FROM history_nodes WHERE session_id = ? AND parent_id IS NULL ORDER BY id DESC LIMIT 1", (session_id,)
            ).fetchone()
        return connection.execute(
            "SELECT * FROM history_nodes WHERE session_id = ? AND parent_id = ? ORDER BY id DESC LIMIT 1", (session_id, parent_id)
        ).fetchone()

    @staticmethod
    def _children(connection: sqlite3.Connection, session_id: int, parent_id: int | None) -> list[sqlite3.Row]:
        if parent_id is None:
            return connection.execute(
                "SELECT * FROM history_nodes WHERE session_id = ? AND parent_id IS NULL ORDER BY id DESC", (session_id,)
            ).fetchall()
        return connection.execute(
            "SELECT * FROM history_nodes WHERE session_id = ? AND parent_id = ? ORDER BY id DESC", (session_id, parent_id)
        ).fetchall()

    @staticmethod
    def _move_round(node: sqlite3.Row) -> int | None:
        move = json.loads(node["move"])
        candidates = [move]
        if isinstance(move, dict) and isinstance(move.get("data"), dict):
            candidates.append(move["data"])
        for candidate in candidates:
            for key in ("round", "roundNumber", "round_number"):
                value = candidate.get(key) if isinstance(candidate, dict) else None
                if isinstance(value, int) and value >= 0:
                    return value
        return None

    @staticmethod
    def _history_structure(nodes: list[sqlite3.Row]) -> dict[str, Any]:
        nodes_by_id = {int(node["id"]): node for node in nodes}
        children_by_parent: dict[int | None, list[sqlite3.Row]] = {}
        for node in nodes:
            parent_id = int(node["parent_id"]) if node["parent_id"] is not None else None
            children_by_parent.setdefault(parent_id, []).append(node)

        branch_by_node: dict[int, str] = {}
        branch_suffixes: dict[str, int] = {}
        branch_nodes: dict[str, list[sqlite3.Row]] = {"1": []}
        branch_starts: dict[str, sqlite3.Row | None] = {"1": None}
        for node in nodes:
            node_id = int(node["id"])
            parent_id = int(node["parent_id"]) if node["parent_id"] is not None else None
            siblings = children_by_parent[parent_id]
            sibling_index = siblings.index(node)
            parent_branch = branch_by_node.get(parent_id, "1")
            if sibling_index == 0:
                path = parent_branch
            elif parent_id is None:
                path = str(sibling_index + 1)
            else:
                suffix = branch_suffixes.get(parent_branch, 1) + 1
                branch_suffixes[parent_branch] = suffix
                path = f"{parent_branch}.{suffix}"
            branch_by_node[node_id] = path
            branch_nodes.setdefault(path, []).append(node)
            if path != parent_branch and path not in branch_starts:
                branch_starts[path] = node

        branches = []
        for path, path_nodes in branch_nodes.items():
            start = branch_starts.get(path)
            tip = path_nodes[-1] if path_nodes else None
            branches.append({
                "nodeID": int(tip["id"]) if tip is not None else None,
                "startNodeID": int(start["id"]) if start is not None else None,
                "baseNodeID": int(start["parent_id"]) if start is not None and start["parent_id"] is not None else None,
                "depth": int(tip["depth"]) if tip is not None else 0,
                "path": path,
                "position": int(start["position"]) if start is not None else None,
                "move": json.loads(start["move"]) if start is not None else None,
                "createdAt": start["created_at"] if start is not None else None,
            })
        branches.sort(key=lambda branch: tuple(int(part) for part in branch["path"].split(".")))
        return {
            "nodesByID": nodes_by_id,
            "childrenByParent": children_by_parent,
            "branchByNode": branch_by_node,
            "branchNodes": branch_nodes,
            "branches": branches,
        }

    @staticmethod
    def _node_chain(nodes_by_id: dict[int, sqlite3.Row], tip_id: int | None) -> list[sqlite3.Row]:
        chain: list[sqlite3.Row] = []
        current_id = tip_id
        while current_id is not None:
            node = nodes_by_id.get(current_id)
            if node is None:
                break
            chain.append(node)
            current_id = int(node["parent_id"]) if node["parent_id"] is not None else None
        chain.reverse()
        return chain

    @staticmethod
    def _turn_groups(nodes: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
        groups: list[list[sqlite3.Row]] = []
        for node in nodes:
            if not groups or groups[-1][-1]["position"] != node["position"]:
                groups.append([])
            groups[-1].append(node)
        return groups

    @staticmethod
    def _round_groups(nodes: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
        groups: list[list[sqlite3.Row]] = []
        current_round: int | None = None
        for node in nodes:
            round_number = Database._move_round(node)
            if round_number is None:
                continue
            if not groups or round_number != current_round:
                groups.append([])
                current_round = round_number
            groups[-1].append(node)
        return groups

    @staticmethod
    def _set_history_head(connection: sqlite3.Connection, session_id: int, node_id: int | None) -> None:
        connection.execute(
            "INSERT INTO history_heads(session_id, node_id) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET node_id = excluded.node_id",
            (session_id, node_id),
        )

    def history_status(self, session_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError("Session not found")
            current_id = int(session["current_node_id"]) if session["current_node_id"] is not None else None
            forks = connection.execute(
                "SELECT COUNT(*) AS total FROM (SELECT parent_id FROM history_nodes WHERE session_id = ? GROUP BY parent_id HAVING COUNT(*) > 1)",
                (session_id,),
            ).fetchone()["total"]
            nodes = connection.execute(
                "SELECT * FROM history_nodes WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
            structure = self._history_structure(nodes)
            nodes_by_id = structure["nodesByID"]
            branch_by_node = structure["branchByNode"]
            branches = structure["branches"]
            head_row = connection.execute("SELECT node_id FROM history_heads WHERE session_id = ?", (session_id,)).fetchone()
            selected_tip_id = int(head_row["node_id"]) if head_row and head_row["node_id"] is not None else current_id
            if selected_tip_id not in nodes_by_id:
                selected_tip_id = current_id
            selected_chain = self._node_chain(nodes_by_id, selected_tip_id)
            selected_ids = [int(node["id"]) for node in selected_chain]
            if current_id is not None and current_id not in selected_ids:
                selected_tip_id = current_id
                selected_chain = self._node_chain(nodes_by_id, selected_tip_id)
                selected_ids = [int(node["id"]) for node in selected_chain]
            if head_row is None:
                self._set_history_head(connection, session_id, selected_tip_id)
            selected_branch = branch_by_node.get(selected_tip_id, "1")
            branch_index = selected_ids.index(current_id) + 1 if current_id in selected_ids else 0
            turn_groups = self._turn_groups(selected_chain)
            turn_index = next((index + 1 for index, group in enumerate(turn_groups) if current_id in {int(node["id"]) for node in group}), 0)
            round_groups = self._round_groups(selected_chain)
            round_index = next((index + 1 for index, group in enumerate(round_groups) if current_id in {int(node["id"]) for node in group}), 0)
            current_chain_index = selected_ids.index(current_id) if current_id in selected_ids else -1
            forward = selected_chain[current_chain_index + 1] if current_chain_index + 1 < len(selected_chain) else None
            children = self._children(connection, session_id, current_id)

            def branch_name(node_id: int | None) -> str:
                return branch_by_node.get(node_id, "1")

            checkpoints = connection.execute(
                "SELECT * FROM history_checkpoints WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
            return {
                "index": branch_index,
                "tip": len(selected_chain),
                "actionIndex": branch_index,
                "actionTotal": len(selected_chain),
                "turnIndex": turn_index,
                "turnTotal": len(turn_groups),
                "roundIndex": round_index,
                "roundTotal": len(round_groups),
                "currentNodeID": current_id,
                "currentBranch": selected_branch,
                "selectedTipID": selected_tip_id,
                "canBack": branch_index > 0,
                "canForward": forward is not None,
                "forks": int(forks),
                "branches": branches,
                "choices": [
                    {
                        "nodeID": int(child["id"]),
                        "depth": int(child["depth"]),
                        "path": branch_name(int(child["id"])),
                        "position": int(child["position"]),
                        "move": json.loads(child["move"]),
                        "createdAt": child["created_at"],
                    }
                    for child in children
                ],
                "checkpoints": [
                    {
                        "id": int(checkpoint["id"]),
                        "nodeID": int(checkpoint["node_id"]) if checkpoint["node_id"] is not None else None,
                        "depth": int(nodes_by_id[int(checkpoint["node_id"])]["depth"]) if checkpoint["node_id"] is not None else 0,
                        "path": branch_name(int(checkpoint["node_id"]) if checkpoint["node_id"] is not None else None),
                        "name": checkpoint["name"],
                        "createdAt": checkpoint["created_at"],
                    }
                    for checkpoint in checkpoints
                ],
            }

    def create_checkpoint(self, session_id: int, name: str) -> int:
        with self.connect() as connection:
            session = connection.execute("SELECT current_node_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError("Session not found")
            clean_name = name.strip()[:80]
            if not clean_name:
                total = connection.execute(
                    "SELECT COUNT(*) AS total FROM history_checkpoints WHERE session_id = ?", (session_id,)
                ).fetchone()["total"]
                clean_name = f"Checkpoint {int(total) + 1}"
            cursor = connection.execute(
                "INSERT INTO history_checkpoints(session_id, node_id, name) VALUES (?, ?, ?)",
                (session_id, session["current_node_id"], clean_name),
            )
            return int(cursor.lastrowid)

    def navigate_history(self, session_id: int, command: str, branch_id: int | None = None) -> bool:
        with self.connect() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError("Session not found")
            nodes = connection.execute("SELECT * FROM history_nodes WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
            structure = self._history_structure(nodes)
            nodes_by_id = structure["nodesByID"]
            current_id = int(session["current_node_id"]) if session["current_node_id"] is not None else None
            current = nodes_by_id.get(current_id) if current_id is not None else None
            original_id = int(current["id"]) if current is not None else None
            head_row = connection.execute("SELECT node_id FROM history_heads WHERE session_id = ?", (session_id,)).fetchone()
            selected_tip_id = int(head_row["node_id"]) if head_row and head_row["node_id"] is not None else current_id
            if selected_tip_id not in nodes_by_id:
                selected_tip_id = current_id
            selected_chain = self._node_chain(nodes_by_id, selected_tip_id)
            selected_ids = [int(node["id"]) for node in selected_chain]
            if current_id is not None and current_id not in selected_ids:
                selected_tip_id = current_id
                selected_chain = self._node_chain(nodes_by_id, selected_tip_id)
                selected_ids = [int(node["id"]) for node in selected_chain]
            selected_branch = structure["branchByNode"].get(selected_tip_id, "1")
            chain_index = selected_ids.index(current_id) if current_id in selected_ids else -1
            target = current
            if command == "start":
                target = None
            elif command == "back-action":
                target = selected_chain[chain_index - 1] if chain_index > 0 else None
            elif command == "forward-action":
                if branch_id is None:
                    target = selected_chain[chain_index + 1] if chain_index + 1 < len(selected_chain) else None
                else:
                    parent_id = current["id"] if current else None
                    if parent_id is None:
                        target = connection.execute(
                            "SELECT * FROM history_nodes WHERE id = ? AND session_id = ? AND parent_id IS NULL",
                            (branch_id, session_id),
                        ).fetchone()
                    else:
                        target = connection.execute(
                            "SELECT * FROM history_nodes WHERE id = ? AND session_id = ? AND parent_id = ?",
                            (branch_id, session_id, parent_id),
                        ).fetchone()
                    if target is None:
                        raise ValueError("The selected branch does not start here")
            elif command == "back-turn":
                if current is None:
                    return False
                position = current["position"]
                index = chain_index
                while index >= 0 and selected_chain[index]["position"] == position:
                    index -= 1
                target = selected_chain[index] if index >= 0 else None
            elif command == "forward-turn":
                index = chain_index + 1
                if index >= len(selected_chain):
                    return False
                position = selected_chain[index]["position"]
                while index + 1 < len(selected_chain) and selected_chain[index + 1]["position"] == position:
                    index += 1
                target = selected_chain[index]
            elif command == "end":
                target = nodes_by_id.get(selected_tip_id) if selected_tip_id is not None else None
            elif command in {"action", "turn", "round"}:
                value = max(0, int(branch_id or 0))
                if command == "action":
                    target = selected_chain[value - 1] if value and value <= len(selected_chain) else None
                else:
                    groups = self._turn_groups(selected_chain) if command == "turn" else self._round_groups(selected_chain)
                    target = groups[value - 1][-1] if value and value <= len(groups) else None
            elif command == "branch":
                if branch_id is None or branch_id not in nodes_by_id:
                    raise ValueError("The selected branch does not exist")
                selected_tip_id = branch_id
                self._set_history_head(connection, session_id, selected_tip_id)
                target = nodes_by_id[selected_tip_id]
            elif command == "node":
                if branch_id is None:
                    target = None
                else:
                    target = connection.execute(
                        "SELECT * FROM history_nodes WHERE id = ? AND session_id = ?",
                        (branch_id, session_id),
                    ).fetchone()
                    if target is None:
                        raise ValueError("The selected history point does not exist")
                    target_path = structure["branchByNode"].get(int(target["id"]), "1")
                    selected_tip_id = next(item["nodeID"] for item in structure["branches"] if item["path"] == target_path)
                    self._set_history_head(connection, session_id, selected_tip_id)
            else:
                raise ValueError(f"Unknown history command: {command}")
            target_id = int(target["id"]) if target is not None else None
            if target_id == original_id:
                return False
            state = self._node_state(connection, session, int(target["id"]) if target is not None else None)
            if not state:
                return False
            phase = state["game"]["phase"]
            connection.execute(
                "UPDATE sessions SET state = ?, phase = ?, next_seq = ?, current_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (compress_state(state), phase, int(target["depth"]) if target else 0, target["id"] if target else None, session_id),
            )
            return True

    def delete_session(self, session_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def bind_unversioned_sessions(self, revisions: dict[str, str]) -> None:
        with self.connect() as connection:
            for game_name, revision in revisions.items():
                connection.execute(
                    "UPDATE sessions SET game_revision = ? WHERE game_name = ? AND game_revision IS NULL",
                    (revision, game_name),
                )

    def apply_game_revision(
        self,
        game_name: str,
        previous: str,
        current: str,
        kind: str,
        updates: dict[str, Any],
    ) -> dict[str, list[int]]:
        promoted: list[int] = []
        deleted: list[int] = []
        mode = str(updates.get("mode") or "versioned")
        policy = str(updates.get(kind) or ("live" if kind == "assets" else "pinned"))
        with self.connect() as connection:
            if mode == "latest-only" and kind != "assets":
                rows = connection.execute(
                    "SELECT id FROM sessions WHERE game_name = ? AND (game_revision <> ? OR game_revision IS NULL)",
                    (game_name, current),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id FROM sessions WHERE game_name = ? AND game_revision = ?",
                    (game_name, previous),
                ).fetchall()
            session_ids = [int(row["id"]) for row in rows]
            if not session_ids:
                return {"promoted": promoted, "deleted": deleted}
            if mode == "latest-only" and kind != "assets":
                connection.executemany("DELETE FROM sessions WHERE id = ?", [(session_id,) for session_id in session_ids])
                deleted.extend(session_ids)
            elif policy == "live":
                connection.executemany(
                    "UPDATE sessions SET game_revision = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [(current, session_id) for session_id in session_ids],
                )
                promoted.extend(session_ids)
        return {"promoted": promoted, "deleted": deleted}

    def revision_usage(self, game_name: str) -> dict[str, dict[str, int]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT game_revision, phase, COUNT(*) AS amount
                   FROM sessions WHERE game_name = ?
                   GROUP BY game_revision, phase""",
                (game_name,),
            ).fetchall()
        usage: dict[str, dict[str, int]] = {}
        for row in rows:
            revision = str(row["game_revision"] or "unversioned")
            counts = usage.setdefault(revision, {"total": 0, "new": 0, "started": 0, "finished": 0})
            amount = int(row["amount"])
            counts["total"] += amount
            phase = str(row["phase"] or "")
            if phase in counts:
                counts[phase] += amount
        return usage
