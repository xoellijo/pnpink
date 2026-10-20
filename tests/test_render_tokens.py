import dsl
from render_tokens import fit_suffix_to_ops, parse_object_token, parse_source_like_token


def test_fit_suffix_conversion_preserves_max_anchor_and_clip():
    ops = fit_suffix_to_ops(".F{ m a=1 c }")
    converted = dsl.fit_spec_from_ops(ops)

    assert converted.mode == "m"
    assert converted.anchor == 1
    assert converted.clip is True


def test_short_fit_module_survives_object_and_source_tokenization():
    object_id, placement, object_ops = parse_object_token("icon.F{m}")
    assert object_id == "icon"
    assert placement == "clone"
    assert dsl.fit_spec_from_ops(object_ops).mode == "m"

    source, ops, kind = parse_source_like_token("@{icon://cat}.F{m}")
    assert source == "icon://cat"
    assert dsl.fit_spec_from_ops(ops).mode == "m"
    assert kind == "@{...}"


def test_long_and_short_fit_forms_match_across_render_token_paths():
    _, _, long_object_ops = parse_object_token(
        "icon.Fit{mode=inside anchor=7 border=[-2] shift=[3 4] clip}"
    )
    _, _, short_object_ops = parse_object_token(
        "icon.F{m=i a=7 b=[-2] s=[3 4] c}"
    )
    assert dsl.fit_spec_from_ops(short_object_ops) == dsl.fit_spec_from_ops(
        long_object_ops
    )

    _, long_source_ops, _ = parse_source_like_token(
        "@{icon://cat}.Fit{mode=inside anchor=7}"
    )
    _, short_source_ops, _ = parse_source_like_token(
        "@{icon://cat}.F{m=i a=7}"
    )
    assert dsl.fit_spec_from_ops(short_source_ops) == dsl.fit_spec_from_ops(
        long_source_ops
    )
