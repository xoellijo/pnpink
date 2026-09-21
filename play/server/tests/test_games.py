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

    def test_resolves_reusable_types_and_nested_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            manifest = yaml.safe_load((game / "play.yaml").read_text(encoding="utf-8"))
            manifest["types"] = {
                "areas": {
                    "movable": {"scope": "shared", "visibility": "public", "actions": ["move-from"]},
                    "table": {
                        "extends": "movable",
                        "kind": "table",
                        "layout": {"shared": {"left": 10, "top": 10, "width": 80, "height": 80}, "rows": 2},
                    },
                },
                "pieces": {"marker": {"type": "token", "shape": "circle", "copies": 3}},
            }
            manifest["areas"] = [{"id": "table", "extends": "table", "label": "Board", "layout": {"shared": {"width": 70}}}]
            manifest["pieces"] = [{"id": "marker", "extends": "marker", "label": "Marker", "initial": {"area": "table"}}]
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

            resolved = GameRegistry([root]).get("sample").play_manifest
            self.assertNotIn("types", resolved)
            self.assertEqual(resolved["areas"][0]["layout"]["shared"], {"left": 10, "top": 10, "width": 70, "height": 80})
            self.assertEqual(resolved["pieces"][0]["copies"], 3)

    def test_uses_standard_types_without_local_declarations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            manifest = yaml.safe_load((game / "play.yaml").read_text(encoding="utf-8"))
            manifest["areas"] = [
                {
                    "id": "deck",
                    "extends": "deck",
                    "label": "Deck",
                    "layout": {"shared": {"left": 10, "top": 10, "width": 20, "height": 30}},
                }
            ]
            manifest["pieces"] = [
                {
                    "id": "card",
                    "extends": "card",
                    "label": "Card",
                    "front": "card.png",
                    "initial": {"area": "deck"},
                }
            ]
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

            resolved = GameRegistry([root]).get("sample").play_manifest
            self.assertEqual(resolved["areas"][0]["kind"], "deck")
            self.assertEqual(resolved["areas"][0]["visibility"], "hidden")
            self.assertEqual(resolved["pieces"][0]["type"], "card")
            self.assertEqual(resolved["pieces"][0]["copies"], 1)

    def test_local_types_override_standard_types(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            manifest = yaml.safe_load((game / "play.yaml").read_text(encoding="utf-8"))
            manifest["types"] = {"areas": {"deck": {"visibility": "public", "layout": {"columns": 3}}}}
            manifest["areas"] = [
                {
                    "id": "deck",
                    "extends": "deck",
                    "label": "Open deck",
                    "layout": {"shared": {"left": 10, "top": 10, "width": 20, "height": 30}},
                }
            ]
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

            resolved = GameRegistry([root]).get("sample").play_manifest
            self.assertEqual(resolved["areas"][0]["kind"], "deck")
            self.assertEqual(resolved["areas"][0]["visibility"], "public")
            self.assertEqual(resolved["areas"][0]["layout"]["columns"], 3)

    def test_rejects_circular_type_inheritance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            manifest = yaml.safe_load((game / "play.yaml").read_text(encoding="utf-8"))
            manifest["types"] = {"areas": {"one": {"extends": "two"}, "two": {"extends": "one"}}}
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Circular area type inheritance"):
                GameRegistry([root])

    def test_piece_sources_preserve_semantic_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            (game / "assets").mkdir()
            (game / "assets/card.png").write_bytes(b"png")
            (game / "assets/items.json").write_text(json.dumps({
                "schema": 2,
                "items": [{
                    "name": "card1",
                    "id": "king-hearts",
                    "label": "King of Hearts",
                    "type": "card",
                    "roles": ["playing-card"],
                    "tags": ["royal"],
                    "properties": {"suit": "hearts", "strength": 13},
                    "front": "card.png",
                    "copies": 1,
                }],
            }), encoding="utf-8")
            manifest = yaml.safe_load((game / "play.yaml").read_text(encoding="utf-8"))
            manifest["areas"][0]["accepts"] = {"types": ["card"], "roles": ["playing-card"]}
            manifest["pieces"] = {
                "source": "assets/items.json",
                "extends": "card",
                "initial": {"area": "table"},
            }
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

            resolved = GameRegistry([root]).get("sample").play_manifest
            self.assertEqual(resolved["areas"][0]["accepts"]["roles"], ["playing-card"])
            self.assertEqual(resolved["pieces"][0]["id"], "king-hearts")
            self.assertEqual(resolved["pieces"][0]["roles"], ["playing-card"])
            self.assertEqual(resolved["pieces"][0]["properties"], {"suit": "hearts", "strength": 13})

    def test_versions_manifest_and_classifies_asset_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.create_game(root, declarative=True)
            (game / "a.png").write_bytes(b"a")
            (game / "b.png").write_bytes(b"b")
            manifest = yaml.safe_load((game / "play.yaml").read_text(encoding="utf-8"))
            manifest["pieces"] = [{
                "id": "card-a", "label": "Card", "type": "card", "front": "a.png",
                "copies": 1, "initial": {"area": "table"},
            }]
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            registry = GameRegistry([root])
            first = registry.get("sample")
            self.assertEqual(first.play_manifest["updates"], {
                "mode": "versioned", "assets": "live", "components": "new-games", "rules": "pinned",
            })

            manifest["pieces"][0]["front"] = "b.png"
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            changes = registry.refresh()
            second = registry.get("sample")
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].kind, "assets")
            self.assertNotEqual(first.revision, second.revision)
            self.assertEqual(registry.get("sample", first.revision).play_manifest["pieces"][0]["front"], "a.png")

            manifest["pieces"][0]["copies"] = 2
            (game / "play.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            self.assertEqual(registry.refresh()[0].kind, "components")
            revisions = registry.revisions("sample")
            self.assertEqual(len(revisions), 3)
            registry.restore_revision("sample", first.revision)
            registry.refresh()
            self.assertEqual(registry.get("sample").revision, first.revision)
            registry.delete_revision("sample", second.revision)
            self.assertFalse((game / ".pnpplay/versions" / second.revision / "play.json").exists())

    def test_refresh_discovers_linked_game_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog"
            catalog.mkdir()
            first_project = self.create_game(root / "first", declarative=True)
            first_link = catalog / "first"
            first_link.mkdir()
            (first_link / "game.json").write_text(
                json.dumps({"projectRoot": str(first_project)}), encoding="utf-8"
            )
            registry = GameRegistry([catalog])
            self.assertEqual(registry.get("sample").root, first_project.resolve())

            second_project = self.create_game(root / "second", declarative=True)
            game_manifest = json.loads((second_project / "game.json").read_text(encoding="utf-8"))
            game_manifest["name"] = "sample-two"
            (second_project / "game.json").write_text(json.dumps(game_manifest), encoding="utf-8")
            second_link = catalog / "second"
            second_link.mkdir()
            (second_link / "game.json").write_text(
                json.dumps({"projectRoot": str(second_project)}), encoding="utf-8"
            )

            registry.refresh()

            self.assertEqual(registry.get("sample-two").root, second_project.resolve())


if __name__ == "__main__":
    unittest.main()
