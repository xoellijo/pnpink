import semantic_metadata
from render_helpers import build_row_map


def test_semantic_headers_become_manifest_fields():
    metadata = semantic_metadata.from_mapping({
        "$id": "king-hearts",
        "$type": "card",
        "$name": "King of Hearts",
        "$role": "playing-card scoring-card",
        "$tags": "royal, fire royal",
        "$suit": "hearts",
        "$rank": "king",
        "$strength": "13",
        "$points": "10",
        "$trump": "false",
    })
    assert metadata == {
        "id": "king-hearts",
        "type": "card",
        "name": "King of Hearts",
        "roles": ["playing-card", "scoring-card"],
        "tags": ["royal", "fire"],
        "properties": {
            "suit": "hearts",
            "rank": "king",
            "strength": 13,
            "points": 10,
            "trump": False,
        },
    }
    assert semantic_metadata.manifest_fields(metadata) == {
        "id": "king-hearts",
        "name": "King of Hearts",
        "label": "King of Hearts",
        "type": "card",
        "roles": ["playing-card", "scoring-card"],
        "tags": ["royal", "fire"],
        "properties": metadata["properties"],
    }


def test_has_field_detects_empty_semantic_id_column():
    assert semantic_metadata.has_field({"$ID": "", "title": "Card"}, "id")
    assert not semantic_metadata.has_field({"title": "Card"}, "id")


def test_empty_and_nonsemantic_columns_are_ignored():
    assert semantic_metadata.from_mapping({"title": "Visible", "$rank": "", "$points": None}) == {}


def test_semantic_columns_are_available_to_existing_variable_syntax():
    row_map = build_row_map(["$suit", "title"], {"cells": ["hearts", "${suit}"]})
    assert row_map["$suit"] == "hearts"
    assert row_map["suit"] == "hearts"


def test_quoted_iterator_items_lose_only_syntactic_quotes():
    from render_tokens import unquote_iterator_item

    assert unquote_iterator_item('"A 1"') == ("A 1", True)
    assert unquote_iterator_item("'B 2'") == ("B 2", True)
    assert unquote_iterator_item("C") == ("C", False)
