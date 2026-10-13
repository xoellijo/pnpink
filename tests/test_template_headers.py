from lxml import etree

import template_headers


def _root(source: bytes):
    return etree.fromstring(source)


def test_template_headers_uses_declared_bbox_and_unique_labels():
    root = _root(b"""<svg xmlns='http://www.w3.org/2000/svg'
        xmlns:inkscape='http://www.inkscape.org/namespaces/inkscape'>
        <g inkscape:groupmode='layer'><g id='card'>
            <rect id='card-bbox'/>
            <text id='text1' inkscape:label='title'/>
            <rect id='image1' inkscape:label='art'/>
        </g></g>
    </svg>""")
    datasets = [{"meta": {"templates_bbox_ids": ["card-bbox"]}}]

    result = template_headers.template_headers(root, datasets)

    assert result["template"] == "card"
    assert result["headers"] == ["card-bbox", "title", "art"]
    assert result["text"] == "card-bbox\ttitle\tart"


def test_template_headers_falls_back_to_inkscape_bbox_label():
    root = _root(b"""<svg xmlns='http://www.w3.org/2000/svg'
        xmlns:inkscape='http://www.inkscape.org/namespaces/inkscape'>
        <g id='card'><rect id='frame' inkscape:label='bbox'/><text id='name'/></g>
    </svg>""")

    result = template_headers.template_headers(root, [])

    assert result["headers"] == ["frame", "name"]


def test_template_headers_does_not_use_svg_description_as_bbox():
    root = _root(b"""<svg xmlns='http://www.w3.org/2000/svg'>
        <g id='card'><rect id='frame'><desc>bbox card</desc></rect></g>
    </svg>""")

    try:
        template_headers.template_headers(root, [])
    except ValueError as error:
        assert "Inkscape label" in str(error)
    else:
        raise AssertionError("SVG descriptions must not define PnPInk semantics")


def test_template_headers_does_not_fallback_when_declared_bbox_is_wrong():
    root = _root(b"""<svg xmlns='http://www.w3.org/2000/svg'>
        <g><rect id='frame'/></g>
    </svg>""")

    try:
        template_headers.template_headers(
            root, [{"meta": {"templates_bbox_ids": ["missing"]}}],
        )
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("Expected an invalid declared bbox to fail")
