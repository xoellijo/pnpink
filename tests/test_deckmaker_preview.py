import dataset
import deckmaker_preview


class _PreviewEffect:
    def __init__(self, document, path):
        self.document = document
        self._path = str(path)

    def document_path(self):
        return self._path


def test_preview_catalog_ignores_copies_and_lists_rows():
    datasets = dataset._matrix_to_datasets([
        ["card", "$name", "title"],
        ["3", "One", "First"],
        ["", "Two", "Second"],
    ])

    entries = deckmaker_preview.catalog(datasets)

    assert [entry["key"] for entry in entries] == ["1:1:1", "1:2:1"]
    assert [entry["label"] for entry in entries] == [
        "2 - First", "3 - Second",
    ]
    selected, _label, selector = deckmaker_preview._selection(datasets, entries[0])
    assert selector == ""
    assert selected[0]["rows"][0]["__dm_copies__"] == 1


def test_preview_catalog_expands_iterator_variants_once():
    datasets = dataset._matrix_to_datasets([
        ["card", "title"],
        ["", "*[One Two Three]"],
    ])

    entries = deckmaker_preview.catalog(datasets)

    assert [entry["key"] for entry in entries] == ["1:1:1", "1:1:2", "1:1:3"]
    assert [entry["label"] for entry in entries] == [
        "2.1 - One", "2.2 - Two", "2.3 - Three",
    ]
    selected, _label, selector = deckmaker_preview._selection(datasets, entries[1])
    assert selector == "[2]"
    assert selected[0]["rows"][0]["__dm_iter_select__"] == "[2]"


def test_preview_catalog_numbers_multiple_datasets_and_uses_title_priority():
    datasets = [
        {
            "headers": ["$name", "title"],
            "rows": [{"cells": ["Fallback", "First title"], "__dm_sheet_row__": 4}],
        },
        {
            "headers": ["$name", "title"],
            "rows": [{"cells": ["Second name", "Second title"], "__dm_sheet_row__": 23}],
        },
    ]

    entries = deckmaker_preview.catalog(datasets)

    assert [entry["label"] for entry in entries] == [
        "4 - First title", "23 - Second title",
    ]


def test_preview_catalog_falls_back_to_first_unique_column():
    datasets = [{
        "headers": ["shared", "code", "description"],
        "rows": [
            {"cells": ["same", "A", "Alpha"], "__dm_sheet_row__": 8},
            {"cells": ["same", "B", "Beta"], "__dm_sheet_row__": 9},
        ],
    }]

    entries = deckmaker_preview.catalog(datasets)

    assert [entry["label"] for entry in entries] == ["8 - A", "9 - B"]


def test_preview_catalog_resolves_nested_iterator_titles():
    datasets = [{
        "headers": ["$title"],
        "rows": [{"cells": ["*[A B] **[1 2]"], "__dm_sheet_row__": 6}],
    }]

    entries = deckmaker_preview.catalog(datasets)

    assert [entry["label"] for entry in entries] == [
        "6.1.1 - A 1",
        "6.1.2 - A 2",
        "6.2.1 - B 1",
        "6.2.2 - B 2",
    ]


def test_sheet_row_metadata_does_not_invalidate_render_signatures():
    first = [{"headers": ["title"], "rows": [{"cells": ["A"], "__dm_sheet_row__": 2}]}]
    moved = [{"headers": ["title"], "rows": [{"cells": ["A"], "__dm_sheet_row__": 20}]}]

    assert deckmaker_preview._render_signature_datasets(first) == (
        deckmaker_preview._render_signature_datasets(moved)
    )


def test_preview_catalog_uses_visible_text_from_snippet_title():
    datasets = [{
        "headers": ["$title"],
        "comments": [
            "# :TC(text fill stroke width) = <tspan fill='${fill}'>${text}</tspan>",
        ],
        "rows": [{"cells": [":TC(Alien yellow)"], "__dm_sheet_row__": 12}],
    }]

    entries = deckmaker_preview.catalog(datasets)

    assert entries[0]["label"] == "12 - Alien"


def test_preview_catalog_keeps_physical_rows_after_block_comments():
    datasets = dataset._matrix_to_datasets([
        ["#### hidden"],
        ["ignored"],
        ["####"],
        ["card", "title"],
        ["", "Visible"],
    ])

    entries = deckmaker_preview.catalog(datasets)

    assert entries[0]["label"] == "5 - Visible"


def test_preview_catalog_applies_google_sheet_range_offset():
    datasets = dataset._matrix_to_datasets([
        ["card", "title"],
        ["", "Visible"],
    ], source_row_start=33)

    entries = deckmaker_preview.catalog(datasets)

    assert entries[0]["label"] == "34 - Visible"


def test_symbol_base_uses_persistent_content_addressed_cache(tmp_path, monkeypatch):
    from lxml import etree

    monkeypatch.setattr(
        deckmaker_preview.APPPATHS,
        "data_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    effect = _PreviewEffect(
        etree.ElementTree(etree.fromstring(b'<svg xmlns="http://www.w3.org/2000/svg"/>')),
        tmp_path / "cards.svg",
    )
    datasets = [{
        "headers": ["card"],
        "rows": [{"cells": ["A"], "__dm_symbol_id__": "symbol-a"}],
    }]

    first = deckmaker_preview.symbol_base(effect, datasets)
    second = deckmaker_preview.symbol_base(effect, datasets)
    changed = deckmaker_preview.symbol_base(
        effect,
        [{"headers": ["card"], "rows": [{"cells": ["B"], "__dm_symbol_id__": "symbol-a"}]}],
    )

    assert first["path"] == second["path"]
    assert str(first["path"]).startswith(str(tmp_path))
    assert first["signature"] != changed["signature"]
    assert first["path"] != changed["path"]


def test_document_digest_ignores_inkscape_snapshot_noise(tmp_path):
    from lxml import etree

    saved = etree.ElementTree(etree.fromstring(b"""<svg xmlns='http://www.w3.org/2000/svg'
        xmlns:xlink='http://www.w3.org/1999/xlink'
        xmlns:sodipodi='http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd'>
        <sodipodi:namedview id='view-a'/>
        <text><tspan id='tspan-old'>Text</tspan></text>
        <image href='Assets/card.png' xlink:href='file:///project/Assets/card.png'/>
    </svg>"""))
    snapshot = etree.ElementTree(etree.fromstring(b"""<svg xmlns='http://www.w3.org/2000/svg'
        xmlns:xlink='http://www.w3.org/1999/xlink'
        xmlns:sodipodi='http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd'>
        <sodipodi:namedview id='view-b'/>
        <text><tspan id='tspan-new'>Text</tspan></text>
        <image href='../../../project/Assets/card.png' xlink:href='file:///project/Assets/card.png'/>
    </svg>"""))

    assert deckmaker_preview._document_digest(
        _PreviewEffect(saved, tmp_path / "cards.svg")
    ) == deckmaker_preview._document_digest(
        _PreviewEffect(snapshot, tmp_path / "cards.svg")
    )


def test_document_digest_detects_rendering_changes(tmp_path):
    from lxml import etree

    first = etree.ElementTree(etree.fromstring(
        b"<svg xmlns='http://www.w3.org/2000/svg'><path d='M 0,0 1,1'/></svg>"
    ))
    second = etree.ElementTree(etree.fromstring(
        b"<svg xmlns='http://www.w3.org/2000/svg'><path d='M 0,0 2,2'/></svg>"
    ))

    assert deckmaker_preview._document_digest(
        _PreviewEffect(first, tmp_path / "cards.svg")
    ) != deckmaker_preview._document_digest(
        _PreviewEffect(second, tmp_path / "cards.svg")
    )


def test_merge_symbol_base_keeps_current_template_and_imports_cached_symbols(tmp_path):
    import inkex

    current = inkex.load_svg(b"""<svg xmlns='http://www.w3.org/2000/svg'>
        <defs><linearGradient id='current-gradient'/></defs>
        <g id='template' transform='translate(20,30)'/>
    </svg>""")
    cached = b"""<svg xmlns='http://www.w3.org/2000/svg'>
        <defs>
            <linearGradient id='current-gradient'><stop offset='1'/></linearGradient>
            <linearGradient id='symbol-gradient'/>
            <symbol id='cached-card' data-pnpink-symbol-row='1'><rect width='10' height='20'/></symbol>
        </defs>
        <g id='template' transform='translate(1,2)'/>
    </svg>"""
    cache_path = tmp_path / "symbol-base.svg"
    cache_path.write_bytes(cached)
    effect = _PreviewEffect(current, tmp_path / "cards.svg")

    merged = deckmaker_preview.merge_symbol_base(effect, str(cache_path))

    root = effect.document.getroot()
    assert merged == 1
    assert root.xpath("//*[@id='template']")[0].get("transform") == "translate(20, 30)"
    assert len(root.xpath("//*[@id='cached-card']")) == 1
    assert len(root.xpath("//*[@id='symbol-gradient']")) == 1
    assert len(root.xpath("//*[@id='current-gradient']/*")) == 0
