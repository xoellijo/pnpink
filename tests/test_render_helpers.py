import io

import inkex

import render_helpers as helpers
import svg


def _document():
    raw = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
      <g id="template" transform="translate(30,20)">
        <rect id="frame" x="5" y="7" width="40" height="20"/>
        <text id="text" transform="scale(2)">Example</text>
      </g>
    </svg>'''
    return inkex.load_svg(io.BytesIO(raw)).getroot()


def test_flatten_group_transform_can_preserve_shared_text_hierarchy():
    root = _document()
    template = root.getElementById("template")
    before = {
        element_id: str(svg.composed_transform(root.getElementById(element_id)))
        for element_id in ("frame", "text")
    }

    helpers.flatten_group_transform(template, preserve_hierarchy=True)

    assert template.get("transform") is None
    assert len(template) == 1
    wrapper = template[0]
    assert str(wrapper.tag).endswith("}g")
    assert wrapper.get("transform") is not None
    assert root.getElementById("frame").getparent() is wrapper
    assert root.getElementById("text").getparent() is wrapper
    assert {
        element_id: str(svg.composed_transform(root.getElementById(element_id)))
        for element_id in ("frame", "text")
    } == before


def test_flatten_group_transform_keeps_legacy_distribution_by_default():
    root = _document()
    template = root.getElementById("template")

    helpers.flatten_group_transform(template)

    assert template.get("transform") is None
    assert len(template) == 2
    assert root.getElementById("frame").getparent() is template
    assert root.getElementById("text").getparent() is template
