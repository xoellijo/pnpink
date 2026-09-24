import json
import tempfile
import unittest
from pathlib import Path

from play.server.boardzilla_lite.games import _prepare_manifest


class ManifestTemplateTests(unittest.TestCase):
    def test_trick_template_and_ordered_property_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            assets.mkdir()
            (assets / "items.json").write_text(json.dumps({
                "items": [
                    {"id": "2C", "front": "2C.png"},
                    {"id": "AH", "front": "AH.png"},
                    {"id": "QS", "front": "QS.png"},
                ],
            }), encoding="utf-8")
            manifest = _prepare_manifest(root, {
                "schema": 1,
                "extends": "trick-game",
                "name": "Override example",
                "areas": [{"id": "deck", "stackTab": {"autoHide": False}}],
                "pieces": {
                    "source": "assets/items.json",
                    "extends": "card",
                    "initial": {"area": "deck"},
                    "overrides": {
                        "points": {"*": 0, "*H": 1, "QS": 13},
                    },
                },
            })

            self.assertEqual(manifest["turns"]["mode"], "trick-assisted")
            self.assertEqual(manifest["areas"][0]["stackTab"]["autoHide"], False)
            self.assertEqual(
                {piece["id"]: piece["properties"]["points"] for piece in manifest["pieces"]},
                {"2C": 0, "AH": 1, "QS": 13},
            )


if __name__ == "__main__":
    unittest.main()
