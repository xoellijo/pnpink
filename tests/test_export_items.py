from pathlib import Path
from unittest.mock import patch

import export_items


def test_parse_item_selector_all_and_ranges():
    assert export_items.parse_item_selector("mydeck*", 52) == ("mydeck", list(range(1, 53)))
    assert export_items.parse_item_selector("card_[1-3]", 10) == ("card_", [1, 2, 3])
    assert export_items.parse_item_selector("mydeck[23-21,2,6]", 30) == ("mydeck", [23, 22, 21, 2, 6])


def test_parse_item_selector_rejects_empty_export():
    try:
        export_items.parse_item_selector("mydeck*", 0)
    except ValueError as error:
        assert str(error) == "No exportable items found"
    else:
        raise AssertionError("Empty exports must fail clearly")


def test_discover_items_uses_generated_metadata(tmp_path: Path):
    svg = tmp_path / "deck.svg"
    svg.write_text("""<svg xmlns='http://www.w3.org/2000/svg'>
      <g id='front1' data-pnpink-face='front' data-pnpink-item-index='1' data-pnpink-dataset-index='1' data-pnpink-source-row='3' data-pnpink-metadata='{"id":"king-hearts","type":"card","roles":["playing-card"],"properties":{"suit":"hearts","strength":13}}' data-pnpink-cut-bbox='10 20 30 40'/>
      <g id='back1' data-pnpink-face='back' data-pnpink-item-index='1' data-pnpink-content-key='same' data-pnpink-cut-bbox='10 70 30 40'/>
    </svg>""", encoding="utf-8")
    fronts, backs = export_items.discover_items(str(svg))
    assert fronts[0]["row"] == 3
    assert fronts[0]["bbox"] == (10.0, 20.0, 30.0, 40.0)
    assert fronts[0]["metadata"]["id"] == "king-hearts"
    assert fronts[0]["metadata"]["properties"]["strength"] == 13
    assert backs[1][0]["contentKey"] == "same"


def test_export_reuses_shared_back_and_keeps_only_source_object(tmp_path: Path):
    fronts = [
        {
            "id": f"front{index}", "itemIndex": index, "dataset": 1, "row": 2,
            "variant": index, "template": "card", "exportArea": "0:0:10:20",
            "metadata": {"name": f"Card {index}", "type": "card"},
        }
        for index in (1, 2)
    ]
    shared_back = {
        "id": "back", "itemIndex": 1, "contentKey": "shared-back",
        "exportArea": "0:0:10:20",
    }

    def fake_export(_exe, _svg, _item, target, _kind, _dpi):
        Path(target).write_bytes(b"asset")
        return 0, ""

    output = tmp_path / "items"
    with patch.object(export_items, "discover_items", return_value=(fronts, {1: [shared_back], 2: [shared_back]})), \
         patch.object(export_items.INKSCAPE, "find_executable", return_value="inkscape"), \
         patch.object(export_items, "_export_one", side_effect=fake_export) as export_one:
        ok, result = export_items.export_items_via_inkscape(
            str(tmp_path / "deck.svg"), str(tmp_path / "deck.jpg"),
            export_type="jpeg", item_spec="card*", output_dir=str(output),
        )

    assert ok
    assert export_one.call_count == 3
    assert result["results"][0]["back"] == "card_back.jpg"
    assert result["results"][1]["back"] == "card_back.jpg"
    assert result["results"][0]["name"] == "Card 1"
    assert result["results"][0]["label"] == "Card 1"
    assert result["results"][0]["source"] == {"dataset": 1, "row": 2, "variant": 1}
    assert "sourceItems" not in result["results"][0]
    assert "dataset" not in result["results"][0]
    assert result["results"][0]["id"] == "card-d1-r2-v1"


def test_explicit_ids_filter_rows_and_resolve_common_prefix_back(tmp_path: Path):
    def item(node_id, index, row, semantic_id=""):
        return {
            "id": node_id, "itemIndex": index, "dataset": 1, "row": row,
            "variant": 1, "template": "card", "contentKey": node_id,
            "exportArea": "0:0:10:20", "hasSemanticId": True,
            "metadata": {"id": semantic_id} if semantic_id else {},
        }

    fronts = [
        item("front-a", 1, 2, "pack-a"),
        item("front-empty", 2, 3),
        item("front-b", 3, 4, "pack-b"),
        item("common-back", 4, 5, "pack@back"),
    ]

    def fake_export(_exe, _svg, _item, target, _kind, _dpi):
        Path(target).write_bytes(b"asset")
        return 0, ""

    output = tmp_path / "items"
    with patch.object(export_items, "discover_items", return_value=(fronts, {})), \
         patch.object(export_items.INKSCAPE, "find_executable", return_value="inkscape"), \
         patch.object(export_items, "_export_one", side_effect=fake_export) as export_one:
        ok, result = export_items.export_items_via_inkscape(
            str(tmp_path / "deck.svg"), str(tmp_path / "deck.jpg"),
            export_type="jpeg", item_spec="card*", output_dir=str(output),
        )

    assert ok
    assert export_one.call_count == 3
    assert [record["id"] for record in result["results"]] == ["pack-a", "pack-b"]
    assert [record["back"] for record in result["results"]] == ["card_back.jpg", "card_back.jpg"]
    assert result["physical_count"] == 2


def test_exact_id_back_wins_over_prefix_back():
    front = {"metadata": {"id": "pack-b"}}
    common = {"metadata": {"id": "pack@back"}}
    exact = {"metadata": {"id": "pack-b@back"}}
    assert export_items._id_back_for(front, {"pack": common, "pack-b": exact}) is exact
