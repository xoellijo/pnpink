# -*- coding: utf-8 -*-

import io
from copy import deepcopy

import inkex

import dataset
import gradients as GRD
import render_apply as RAP
import render_tokens as RTK
import svg as SVG
import text as TXT
import transform_fx as TFX
import text_decoration as TDEC


def _document():
    raw = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
      <defs><rect id="frame" x="0" y="0" width="100" height="50"/></defs>
      <g id="card">
        <rect id="placeholder" x="10" y="10" width="90" height="40"/>
        <text id="description" style="font-size:10px;shape-inside:url(#frame)">Hello</text>
      </g>
    </svg>'''
    root = inkex.load_svg(io.BytesIO(raw)).getroot()
    return root, root.find(".//*[@id='card']")


def test_shape_inside_relation_parses_in_object_tokens_and_headers():
    assert RTK.parse_object_token("description[shape-inside]~i5") == (
        "description[shape-inside]",
        "clone",
        "i5",
    )
    core, spec = RTK.split_transform_suffixes("description[shape-inside]~[90%]i.T{i=a}")
    assert core == "description[shape-inside]~[90%]i"
    assert TFX.has_inside(spec)
    assert dataset._expand_property_only_headers(["description", "[shape-inside]"]) == [
        "description",
        "description[shape-inside]",
    ]


def test_shape_inside_frame_is_private_and_reused_per_instance():
    root, scope = _document()
    text = root.find(".//*[@id='description']")
    frame1, owner1, text1 = TFX.ensure_private_shape_inside(root, scope, text)
    frame2, owner2, text2 = TFX.ensure_private_shape_inside(root, scope, text1)

    assert frame1 is frame2
    assert owner1 is owner2
    assert text1 is text2
    assert frame1.get("id") != "frame"
    assert f"url(#{frame1.get('id')})" in text1.get("style")
    assert root.find(".//*[@id='frame']") is not None
    assert len([node for node in scope.iter() if node.get("data-dm-shape-inside-private") == "1"]) == 1


def test_shape_inside_dependency_is_dynamic_with_its_text():
    root, _scope = _document()

    assert TFX.shape_inside_dependency_ids(root, ["description", "placeholder"]) == {"frame"}


def test_shape_inside_frame_resolves_uniquified_instance_by_origid():
    root, scope = _document()
    frame = inkex.Rectangle()
    frame.set("id", "frame-copy")
    frame.set("data-origid", "frame")
    frame.set("width", "77")
    frame.set("height", "33")
    scope.insert(0, frame)
    text = root.find(".//*[@id='description']")

    private_frame, _owner, _text = TFX.ensure_private_shape_inside(root, scope, text)

    assert private_frame.get("width") == "77"
    assert private_frame.get("height") == "33"


def test_shape_inside_frames_share_object_bbox_gradient():
    root, scope = _document()
    defs = SVG.ensure_defs(root)
    gradient = SVG.etree.SubElement(defs, inkex.addNS("linearGradient", "svg"))
    gradient.set("id", "paint")
    gradient.set("gradientUnits", "userSpaceOnUse")
    gradient.set("gradientTransform", "matrix(2,0,0,3,5,7)")
    gradient.set("x1", "0")
    gradient.set("y1", "0")
    gradient.set("x2", "10")
    gradient.set("y2", "20")
    source = root.find(".//*[@id='frame']")
    source.set("style", "fill:url(#paint);stroke:none")

    private_frame, _owner, _text = TFX.ensure_private_shape_inside(
        root,
        scope,
        root.find(".//*[@id='description']"),
    )
    converted_id = GRD._PAINT_URL_RE.fullmatch(SVG.style_map(private_frame)["fill"]).group(1)
    converted = SVG.find_id(root, converted_id, include_defs=True)

    assert converted.get("gradientUnits") == "objectBoundingBox"
    bbox_transform = inkex.Transform("matrix(100,0,0,50,0,0)")
    effective = bbox_transform @ inkex.Transform(converted.get("gradientTransform"))
    original = inkex.Transform(gradient.get("gradientTransform"))
    for point in ((0, 0), (10, 20)):
        actual = effective.apply_to_point(point)
        expected = original.apply_to_point(point)
        assert abs(actual.x - expected.x) < 1e-9
        assert abs(actual.y - expected.y) < 1e-9

    other = deepcopy(source)
    other.set("id", "other-frame")
    assert GRD.normalize_user_space_gradients(root, other, (0, 0, 100, 50)) == 1
    assert SVG.style_map(other)["fill"] == f"url(#{converted_id})"
    assert len([node for node in root.iter() if node.get("data-dm-gradient-source") == "paint"]) == 1


def test_dynamic_inside_array_repack_preserves_bottom_center_anchor():
    assert RAP._array_anchor_from_ops("2") == 2
    assert RAP._array_anchor_from_ops("~{ i8 }") == 8

    root, scope = _document()
    group = inkex.Group()
    group.set("data-bbox", "0 0 100 100")
    group.set("data-dm-array-cols", "1")
    group.set("data-dm-array-rows", "2")
    group.set("data-dm-array-gap-x", "0")
    group.set("data-dm-array-gap-y", "10")
    group.set("data-dm-array-sweep-rows", "1")
    group.set("data-dm-array-anchor", "2")
    scope.append(group)

    for index, (x, y, width, height) in enumerate(((0, 0, 20, 20), (0, 50, 30, 10))):
        item = inkex.Group()
        item.set("data-dm-array-item", str(index))
        frame = inkex.Rectangle()
        frame.set("x", str(x))
        frame.set("y", str(y))
        frame.set("width", str(width))
        frame.set("height", str(height))
        frame.set("data-dm-inside-frame", "y")
        item.append(frame)
        group.append(item)

    TFX._repack_inside_arrays({group}, {})

    assert group.get("data-bbox") == "35.0 60.0 30.0 40.0"
    transforms = [inkex.Transform(item.get("transform")) for item in group]
    first = transforms[0].apply_to_point((0, 0))
    second = transforms[1].apply_to_point((0, 50))
    assert (first.x, first.y) == (35.0, 60.0)
    assert (second.x, second.y) == (35.0, 90.0)


def test_text_probe_keeps_shape_owner_and_drops_unrelated_art():
    raw = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
      <defs>
        <linearGradient id="paint"><stop offset="0" stop-color="#000"/></linearGradient>
      </defs>
      <g id="card" transform="translate(10,20)">
        <image id="art" href="large.png" width="100" height="50"/>
        <g id="owner" data-dm-shape-inside-owner="1">
          <rect id="private-frame" width="80" height="30" fill="url(#paint)"/>
          <text id="description" style="shape-inside:url(#private-frame)">Hello<tspan id="description__hole__1">I</tspan></text>
        </g>
      </g>
    </svg>'''
    root = inkex.load_svg(io.BytesIO(raw)).getroot()

    probe, text_count, offsets = TXT._build_text_probe(root.getroottree(), {"description", "description__hole__1"})
    probe_root = probe.getroot()

    assert text_count == 1
    assert probe_root.find(".//*[@id='owner']") is not None
    assert probe_root.find(".//*[@id='private-frame']") is not None
    assert probe_root.find(".//*[@id='description']") is not None
    assert probe_root.find(".//*[@id='paint']") is not None
    assert probe_root.find(".//*[@id='art']") is None
    assert offsets == {"description": (10.0, 20.0), "description__hole__1": (10.0, 20.0)}


def test_visible_shape_inside_source_creates_visible_private_frame():
    root, scope = _document()
    source = root.find(".//*[@id='frame']")
    source.getparent().remove(source)
    scope.insert(0, source)
    text = root.find(".//*[@id='description']")

    frame, owner, _text = TFX.ensure_private_shape_inside(root, scope, text)

    assert frame.getparent() is owner
    assert not str(frame.getparent().tag).endswith("defs")
    assert any(str(node.tag).endswith("defs") for node in source.iterancestors())
    assert source.get("data-dm-shape-inside-visible-source") == "1"


def test_shape_inside_property_applies_transform_to_related_frame():
    root, scope = _document()
    result = RAP.apply_field_in_clone(
        scope,
        "description[shape-inside]",
        ".T{i=a}",
        {},
        root_doc=root,
        use_jobs=[],
        fa_jobs=[],
        path_jobs=[],
        use_seq=[0],
        transform_jobs=[],
    )

    assert result == (1, "transform")
    assert len([node for node in scope.iter() if node.get("data-dm-inside-owner") == "a"]) == 1
    assert len(TFX.pending_inside_text_ids(root)) == 1


def test_shape_inside_value_queues_private_frame_for_fit_anchor():
    root, scope = _document()
    fa_jobs = []
    result = RAP.apply_field_in_clone(
        scope,
        "placeholder",
        "description[shape-inside]~[90%]i.T{i=a}",
        {},
        root_doc=root,
        use_jobs=[],
        fa_jobs=fa_jobs,
        path_jobs=[],
        use_seq=[0],
        transform_jobs=[],
    )

    assert result == (1, "fa")
    assert len(fa_jobs) == 1
    base_id, rect_id, ops, _place, _group, _rect, transform_spec = fa_jobs[0]
    assert base_id.startswith("dm_shape_description_frame")
    assert rect_id == "placeholder"
    assert "[90%]" in ops
    assert TFX.has_inside(transform_spec)


def test_empty_inside_owner_is_removed_before_measurement():
    root, scope = _document()
    RAP.apply_field_in_clone(
        scope,
        "description[shape-inside]",
        ".T{i=a}",
        {},
        root_doc=root,
        use_jobs=[],
        fa_jobs=[],
        path_jobs=[],
        use_seq=[0],
        transform_jobs=[],
    )
    text = root.find(".//*[@id='description']")
    text.text = ""

    assert TFX.discard_empty_inside(root) == 1
    assert not TFX.pending_inside_text_ids(root)
    assert not [node for node in scope.iter() if node.get("data-dm-inside-owner")]


def test_inside_source_uses_text_coordinate_system():
    root, scope = _document()
    text = root.find(".//*[@id='description']")
    text.set("transform", "matrix(0.5,0,0,0.25,20,10)")
    frame, _owner, _text = TFX.ensure_private_shape_inside(root, scope, text)
    spec = type("InsideSpec", (), {"inside": "a"})()

    prepared = TFX.prepare_inside_source(root, scope, frame, spec)
    prepared_text = next(node for node in prepared.iter() if node.get("data-dm-inside-text") == "1")

    assert prepared.get("transform")
    assert not (prepared_text.get("transform") or "").strip()
    assert prepared.get("data-bbox") == "20.0 10.0 50.0 12.5"


def test_deferred_inside_keeps_original_flow_frame_for_scaled_text():
    root, scope = _document()
    source = root.find(".//*[@id='frame']")
    source.getparent().remove(source)
    scope.insert(0, source)
    text = root.find(".//*[@id='description']")
    text.set("transform", "scale(0.5,1)")
    text.set("style", text.get("style") + ";shape-padding:2")
    frame, owner, text = TFX.ensure_private_shape_inside(root, scope, text)
    spec = type("InsideSpec", (), {"inside": "a"})()
    assert TFX.mark_inside_owner(owner, frame, text, spec)

    assert TFX.apply_deferred_inside(
        root,
        {text.get("id"): {"x": 2.0, "y": 2.0, "width": 40.0, "height": 10.0}},
    ) == 1

    flow = next(node for node in owner.iter() if node.get("data-dm-shape-inside-flow") == frame.get("id"))
    assert flow.get("width") == "100"
    assert flow.get("height") == "50"
    assert float(frame.get("width")) == 44.01
    assert float(frame.get("height")) == 14.01
    assert f"url(#{flow.get('id')})" in text.get("style")


def test_rich_xml_parses_pnp_text_decoration_attributes():
    root, _scope = _document()
    text = root.find(".//*[@id='description']")

    SVG.replace_xml(
        text,
        "<tspan pnp:decoration='#brush' pnp:decoration-layer='front'>Marked</tspan>",
    )

    tspan = next(node for node in text if str(node.tag).endswith("tspan"))
    assert tspan.get(TDEC.DECORATION_ATTR) == "#brush"
    assert tspan.get(TDEC.LAYER_ATTR) == "front"
