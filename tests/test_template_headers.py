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


def test_template_headers_falls_back_to_bbox_description_only_when_undeclared():
    root = _root(b"""<svg xmlns='http://www.w3.org/2000/svg'>
        <g id='tile'><rect id='frame'><desc>bbox tile</desc></rect><text id='name'/></g>
    </svg>""")

    result = template_headers.template_headers(root, [{"meta": {}}])

    assert result["headers"] == ["frame", "name"]


def test_template_headers_does_not_fallback_when_declared_bbox_is_wrong():
    root = _root(b"""<svg xmlns='http://www.w3.org/2000/svg'>
        <g><rect id='frame'><desc>bbox tile</desc></rect></g>
    </svg>""")

    try:
        template_headers.template_headers(
            root, [{"meta": {"templates_bbox_ids": ["missing"]}}],
        )
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("Expected an invalid declared bbox to fail")
