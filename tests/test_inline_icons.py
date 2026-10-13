import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import text


def test_inline_icon_composition_accepts_multiple_sources():
    expression = (
        "@{icon://wood}~2 "
        "@{icon://white-circle}~[50%]i3 "
        "@{icon://plus}~[35%]i3[-7% -5%]"
    )
    parsed = text._parse_inline_multi_token(expression)

    assert parsed is not None
    assert [item[0] for item in parsed] == [
        "icon://wood",
        "icon://white-circle",
        "icon://plus",
    ]
    assert all(item[3] is False for item in parsed)

    sentence = f"you win 1:({expression}):for each."
    start, end, inner = text._find_inline_token(sentence, 0)
    assert sentence[start:end] == f":({expression}):"
    assert inner == expression


def test_single_source_keeps_compact_legacy_syntax():
    sentence = "Gain 1:@{icon://wood}~2: now."
    start, end, inner = text._find_inline_token(sentence, 0)

    assert sentence[start:end] == ":@{icon://wood}~2:"
    assert inner == "@{icon://wood}~2"


def test_inline_icon_composition_can_mix_document_ids_and_sources():
    parsed = text._parse_inline_multi_token("shield @{icon://plus}~[30%]3")

    assert parsed is not None
    assert parsed[0][0] == "shield"
    assert parsed[0][3] is True
    assert parsed[1][0] == "icon://plus"
    assert parsed[1][3] is False
