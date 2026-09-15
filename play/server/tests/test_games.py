import json
import tempfile
import unittest
from pathlib import Path

import yaml

from boardzilla_lite.games import GameRegistry


class GameRegistryTests(unittest.TestCase):
    def create_game(self, root: Path, declarative: bool = False) -> Path:
        game = root / "sample"
        (game / "build/game").mkdir(parents=True)
        (game / "build/ui").mkdir(parents=True)
        (game / "build/game/index.js").write_text("var game = {};", encoding="utf-8")
        (game / "game.json").write_text(
            json.dumps(
                {
                    "name": "sample",
                    "friendlyName": "Sample",
                    "minPlayers": 2,
                    "maxPlayers": 4,
                    "game": {"out": "build/game/index.js"},
                    "ui": {"outDir": "build/ui"},
                }
            ),
            encoding="utf-8",
        )
        if declarative:
            (game / "play.yaml").write_text(
                yaml.safe_dump(
                    {
                        "schema": 1,
                        "name": "Sample",
                        "board": {"aspectRatio": 1.5},
                        "areas": [
                            {
                                "id": "table",
                                "label": "Table",
                                "kind": "table",
                                "scope": "shared",
                                "visibility": "public",
                                "layout": {"shared": {"left": 10, "top": 10, "width": 80, "height": 80}},
                            }
                        ],
                        "pieces": [],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        return game

    def test_discovers_games_below_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.create_game(root)
            registry = GameRegistry([root])
            self.assertEqual(registry.get("sample").default_players, 2)

    def test_saves_and_reloads_declarative_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.create_game(root, declarative=True)
            registry = GameRegistry([root])
            manifest, revision = registry.authoring_manifest("sample")
            manifest["board"]["aspectRatio"] = 1.8
            saved, next_revision = registry.save_manifest("sample", manifest, revision)
            self.assertEqual(saved["board"]["aspectRatio"], 1.8)
            self.assertNotEqual(next_revision, revision)
            self.assertEqual(registry.get("sample").play_manifest["board"]["aspectRatio"], 1.8)

    def test_rejects_stale_designer_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            registry = GameRegistry([root])
            manifest, revision = registry.authoring_manifest("sample")
            path = game / "play.yaml"
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                registry.save_manifest("sample", manifest, revision)


if __name__ == "__main__":
    unittest.main()
