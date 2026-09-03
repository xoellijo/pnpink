from pathlib import Path

import export_items


def test_parse_item_selector_all_and_ranges():
    assert export_items.parse_item_selector("mydeck*", 52) == ("mydeck", list(range(1, 53)))
    assert export_items.parse_item_selector("card_[1-3]", 10) == ("card_", [1, 2, 3])
    assert export_items.parse_item_selector("mydeck[23-21,2,6]", 30) == ("mydeck", [23, 22, 21, 2, 6])


def test_discover_items_uses_generated_metadata(tmp_path: Path):
    svg = tmp_path / "deck.svg"
    svg.write_text("""<svg xmlns='http://www.w3.org/2000/svg'>
      <g id='front1' data-pnpink-face='front' data-pnpink-item-index='1' data-pnpink-dataset-index='1' data-pnpink-source-row='3' data-pnpink-cut-bbox='10 20 30 40'/>
      <g id='back1' data-pnpink-face='back' data-pnpink-item-index='1' data-pnpink-content-key='same' data-pnpink-cut-bbox='10 70 30 40'/>
    </svg>""", encoding="utf-8")
    fronts, backs = export_items.discover_items(str(svg))
    assert fronts[0]["row"] == 3
    assert fronts[0]["bbox"] == (10.0, 20.0, 30.0, 40.0)
    assert backs[1][0]["contentKey"] == "same"
