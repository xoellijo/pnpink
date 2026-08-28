# -*- coding: utf-8 -*-

import render_apply as RAP


def test_header_global_layout_and_fit_are_parsed_together():
    header = RAP.parse_header_key_full("placeholder=.L{3x1 g=2}~i5")

    layout = header["global_layout"]
    assert layout is not None
    assert layout.grid.cols == 3
    assert layout.grid.rows == 1
    assert layout.grid.gaps == [2.0, 2.0]
    assert header["global_ops"] == "~i5"
    assert header["default_expr"] == ""


def test_header_global_layout_accepts_long_name_and_quoted_braces():
    header = RAP.parse_header_key_full(
        'placeholder=.Layout{3x1 o=["{left}" 0]}~a5'
    )

    assert header["global_layout"].grid.cols == 3
    assert header["global_layout"].grid.rows == 1
    assert header["global_ops"] == "~a5"


def test_array_local_layout_takes_precedence_over_header_layout():
    header = RAP.parse_header_key_full("placeholder=.L{3x1}~i5")
    inherited = RAP._parse_array_token("[a b c]")
    overridden = RAP._parse_array_token("[a b c].L{1x3}")

    inherited_layout = RAP._array_layout_with_header_default(
        inherited, header["global_layout"]
    )
    overridden_layout = RAP._array_layout_with_header_default(
        overridden, header["global_layout"]
    )

    assert (inherited_layout.grid.cols, inherited_layout.grid.rows) == (3, 1)
    assert (overridden_layout.grid.cols, overridden_layout.grid.rows) == (1, 3)
