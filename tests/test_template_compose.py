import io

import inkex

import template_compose as compose


def test_composed_template_preserves_transform_wrapper():
    raw = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
      <g id="template">
        <g transform="translate(30,20)" data-dm-template-transform-wrapper="1">
          <rect id="background" width="40" height="20"/>
          <text id="title">Title</text>
        </g>
      </g>
    </svg>'''
    root = inkex.load_svg(io.BytesIO(raw)).getroot()
    template = root.getElementById("template")

    plan = compose.build_plan(
        root=root,
        proto_root=template,
        dynamic_ids={"title"},
        block_id_prefix="test",
        has_overlays=False,
        has_back_templates=False,
        has_page_templates=False,
        has_clone_fields=False,
        has_anchor_visibility=False,
        static_source_mode="defs",
    )
    instance, targets = compose.instantiate_plan(plan, "_copy", root_doc=root)

    assert inkex.Transform(plan.container_transform) == inkex.Transform("translate(30,20)")
    assert plan.static_blocks == 1
    assert plan.dynamic_roots == 1
    assert len(instance) == 1
    container = instance[0]
    assert inkex.Transform(container.get("transform")) == inkex.Transform("translate(30,20)")
    assert container.get("data-dm-template-transform-wrapper") == "1"
    assert container.find(".//*[@id='title_copy']") is not None
    assert "title" in targets


def test_composed_template_rewrites_references_across_dynamic_roots():
    raw = b'''<svg xmlns="http://www.w3.org/2000/svg">
      <g id="template">
        <rect id="background" width="100" height="100"/>
        <g id="frame-owner"><rect id="frame" width="80" height="30"/></g>
        <g id="text-owner"><text id="description" style="shape-inside:url(#frame)">Text</text></g>
      </g>
    </svg>'''
    root = inkex.load_svg(io.BytesIO(raw)).getroot()
    plan = compose.build_plan(
        root=root,
        proto_root=root.getElementById("template"),
        dynamic_ids={"frame", "description"},
        block_id_prefix="test",
        has_overlays=False,
        has_back_templates=False,
        has_page_templates=False,
        has_clone_fields=False,
        has_anchor_visibility=False,
    )

    instance, targets = compose.instantiate_plan(plan, "_copy", root_doc=root)

    assert "url(#frame_copy)" in targets["description"].get("style")
    assert instance.find(".//*[@id='frame_copy']") is targets["frame"]
