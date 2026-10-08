import json
from pathlib import Path

import play_asset_store


def write_export(root: Path, front: bytes, back: bytes = b"back") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "front.png").write_bytes(front)
    (root / "back.png").write_bytes(back)
    (root / "items.json").write_text(json.dumps({
        "schema": 2,
        "items": [
            {"id": "one", "front": "front.png", "back": "back.png", "properties": {"value": 1}},
            {"id": "two", "front": "front.png", "back": "back.png", "properties": {"value": 2}},
        ],
    }), encoding="utf-8")


def test_replaces_pack_manifest_and_deduplicates_objects(tmp_path: Path):
    exported = tmp_path / "exported"
    store = tmp_path / "store"
    write_export(exported, b"front-v1")

    first = play_asset_store.publish_local_pack(exported, "xoel/Medusas", store_root=store, name="Medusas")
    repeated = play_asset_store.publish_local_pack(exported, "xoel/Medusas", store_root=store, name="Medusas")

    assert first["manifest"] == repeated["manifest"]
    assert len(list((store / "objects").glob("**/*.png"))) == 2
    manifest = json.loads((store / first["manifest"]).read_text(encoding="utf-8"))
    assert manifest["schema"] == play_asset_store.PACK_SCHEMA
    assert manifest["items"][0]["front"] == manifest["items"][1]["front"]

    write_export(exported, b"front-v2")
    second = play_asset_store.publish_local_pack(exported, "xoel/Medusas", store_root=store, name="Medusas")

    assert second["manifest"] == first["manifest"]
    assert len(list((store / "objects").glob("**/*.png"))) == 3
    assert (store / first["manifest"]).is_file()
    catalog = json.loads((store / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["packs"][0]["pack"] == "xoel/medusas"
