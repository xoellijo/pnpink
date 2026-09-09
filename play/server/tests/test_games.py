import json
import tempfile
import unittest
from pathlib import Path

from boardzilla_lite.games import GameRegistry


class GameRegistryTests(unittest.TestCase):
    def test_discovers_games_below_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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
            registry = GameRegistry([root])
            self.assertEqual(registry.get("sample").default_players, 2)


if __name__ == "__main__":
    unittest.main()

