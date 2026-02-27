# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
import pytest
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from dsl import (
    Command,
    DSLError,
    IdRef,
    AliasRef,
    SourceRef,
    GridSpec,
    PageSpec,
    LayoutSpec,
    FitSpec,
    maybe_parse,
    parse,
    tokenize_chain,
    parse_chain,
    maybe_parse_chain,
    is_source_expr,
    split_source_token,
    normalize_ops_suffix,
    ops_from_fit_spec,
    fit_spec_from_ops,

    # Marks
    MarksSpec,
    parse_marks_block,
    parse_leading_cell,
)

def _as_numlist(lst):
    return [str(x) if isinstance(x, str) else float(x) for x in lst]

def _is_id(x, name): 
    return isinstance(x, IdRef) and x.name == name

def _is_src(x, tail): 
    return isinstance(x, SourceRef) and x.src.endswith(tail)

# -------------------------
# maybe_parse detection
# -------------------------

@pytest.mark.parametrize("s,expected", [
    ("Texto plano sin DSL", None),
    ("ID~i7", Command),
    ("Icono.Fit{ anchor=7 i }", Command),
    (".Layout{ g=3x2 }", Command),
    (".L{ g=3x2 }", Command),
    ("@{ img src='a.png' }", Command),
    ("@sp1 = @{pdf src='file.pdf'}.L{ g=3x2 }", Command),
    ("{A4^}", Command),
    ("{3*A4}", Command),
    ("{}", Command),
])
def test_maybe_parse_detection(s, expected):
    out = maybe_parse(s)
    if expected is None:
        assert out is None
    else:
        assert isinstance(out, expected)

# -------------------------
# FIT: forma larga
# -------------------------

def test_fit_largo_minimo():
    cmd = parse("Icono.Fit{ anchor=7 mode=i }")
    assert cmd.name == "Fit"
    assert isinstance(cmd.target, IdRef) and cmd.target.name == "Icono"
    fs = cmd.fit
    assert isinstance(fs, FitSpec)
    assert fs.anchor == 7
    assert fs.mode == "i"
    assert fs.border is None
    assert fs.shift is None
    assert fs.rotate is None
    assert fs.mirror is None
    assert fs.clip is None

def test_fit_largo_border_shift_rotate_mirror_clip():
    s = "Ico.Fit{ a=1 b=[-2 3] s=[10mm -5%] r=-45 mirror=h clip }"
    cmd = parse(s)
    fs = cmd.fit
    assert fs.anchor == 1
    assert _as_numlist(fs.border) == ["-2", "3"]
    assert fs.shift == ["10mm", "-5%"]
    assert fs.rotate == -45.0
    assert fs.mirror == "h"
    assert fs.clip is True
    assert fs.clip_stage in ("pre", "post")

def test_fit_largo_clip_pre_explicito():
    s = "Ico.Fit{ r=30 clip=rect0 }"
    cmd = parse(s)
    assert cmd.fit.rotate == 30.0
    assert cmd.fit.clip is True
    assert cmd.fit.clip_stage == "pre"

# -------------------------
# FIT: shorthand (~)
# -------------------------

@pytest.mark.parametrize("s,expect", [
    ("ID~i",            dict(mode="i")),
    ("ID~m",            dict(mode="m")),
    ("ID~w",            dict(mode="w")),
    ("ID~h",            dict(mode="h")),
    ("ID~x",            dict(mode="x")),
    ("ID~y",            dict(mode="y")),
    ("ID~a",            dict(mode="a")),
    ("ID~t",            dict(mode="t")),
    ("ID~o",            dict(mode="o")),
])
def test_fit_shorthand_modes(s, expect):
    fs = parse(s).fit
    for k, v in expect.items():
        assert getattr(fs, k) == v

def test_fit_shorthand_anchor_rotate_variantes():
    fs = parse("ID~i7").fit
    assert fs.mode == "i" and fs.anchor == 7

    fs = parse("ID~^^").fit
    assert fs.rotate == 180.0

    fs = parse("ID~^-30").fit
    assert fs.rotate == -30.0

    fs = parse("ID~^45").fit
    assert fs.rotate == 45.0

def test_fit_shorthand_border_y_shift_orden_independiente():
    fs = parse("ID~[-2 3] s[10 -5] i7").fit
    assert _as_numlist(fs.border) == ["-2", "3"]
    assert fs.shift == [10.0, -5.0]
    assert fs.mode == "i" and fs.anchor == 7

    fs2 = parse("ID~s[10 -5] i7 [-2 3]").fit
    assert _as_numlist(fs2.border) == ["-2", "3"]
    assert fs2.shift == [10.0, -5.0]
    assert fs2.mode == "i" and fs2.anchor == 7

def test_fit_shorthand_mirror_y_clip_pre_post():
    fs = parse("ID~s[5 5] i7 !").fit
    assert fs.clip is True and fs.clip_stage == "post"
    fs2 = parse("ID~! s[5 5] i7").fit
    assert fs2.clip is True and fs2.clip_stage == "pre"
    fs3 = parse("ID~!! s[5 5]").fit
    assert fs3.clip is True and fs3.clip_stage == "pre"

    fs4 = parse("ID~|").fit
    assert fs4.mirror == "h"
    fs5 = parse("ID~||").fit
    assert fs5.mirror == "v"

# -------------------------
# LAYOUT v2
# -------------------------

def test_layout_minimo_grid_por_defecto():
    cmd = parse(".Layout{ g=3x2 }")
    ls = cmd.layout
    assert isinstance(ls, LayoutSpec)
    assert isinstance(ls.grid, GridSpec)
    assert ls.grid.cols == 3 and ls.grid.rows == 2
    assert ls.grid.order == "lr-tb"
    assert ls.grid.flip in (None, "h", "v")

def test_layout_grid_azucar_flip_y_order():
    cmd = parse(".Layout{ g=3|x2^ }")
    g = cmd.layout.grid
    assert g.cols == 3 and g.rows == 2
    assert g.flip == "h"
    assert g.order == "tb-lr"

def test_layout_kerf_inline_y_top_level():
    cmd = parse(".Layout{ g=3x2<k=[4 3]> }")
    assert _as_numlist(cmd.layout.grid.kerf) == [4.0, 3.0]

    cmd2 = parse(".Layout{ g=3x2 k=[1% 3% 10% 8% -10% -8%] }")
    assert len(cmd2.layout.grid.kerf) == 6
    assert all(isinstance(x, str) and x.endswith('%') or isinstance(x, float) for x in cmd2.layout.grid.kerf)

def test_layout_shape_variantes():
    cmd = parse(".Layout{ s=poker }")
    assert cmd.layout.shape.preset == "poker"
    cmd2 = parse(".Layout{ s=55.3x77.1 }")
    assert cmd2.layout.shape.kind == "rect"
    cmd3 = parse(".Layout{ s=rect<63x88mm> }")
    assert cmd3.layout.shape.kind == "rect"
    cmd4 = parse(".Layout{ s=hex<24x33> }")
    assert cmd4.layout.shape.kind == "hex"
    cmd5 = parse(".Layout{ s=polygon<[5 23x32]> }")
    assert cmd5.layout.shape.kind == "polygon"

# -------------------------
# PAGE v2
# -------------------------

def test_page_variantes_basicas():
    c1 = parse("{A4}")
    assert c1.name == "Page" and isinstance(c1.args["page"], PageSpec)
    ps = c1.args["page"]
    assert ps.size == "A4" and not ps.landscape
    c2 = parse("{A4^}")
    assert c2.args["page"].landscape is True
    c3 = parse("{3*A4}")
    assert c3.args["page"].multiplier == 3 and c3.args["page"].size == "A4"
    c4 = parse("{3}")
    assert c4.args["page"].multiplier == 3
    c5 = parse("{}")
    assert c5.args["page"].pagebreak_only is True

def test_page_con_border():
    c = parse("{A4 b=[-2 3 4 5]}")
    ps = c.args["page"]
    assert _as_numlist(ps.border) == ["-2", "3", "4", "5"]

# -------------------------
# SOURCE y ALIAS
# -------------------------

def test_source_detecta_tipo_por_extension():
    cmd_img = parse("@{ src='cover.png' }")
    assert isinstance(cmd_img.target, SourceRef)
    assert cmd_img.target.stype == "img"

    cmd_pdf = parse("@{ src='book.pdf' }")
    assert cmd_pdf.target.stype == "pdf"

    cmd_svg = parse("@{ src='sprite.svg' }")
    assert cmd_svg.target.stype == "svg"

def test_source_tipo_explicito_y_args():
    cmd = parse("@{ pdf src='book.pdf' page=3 }")
    src = cmd.target
    assert src.stype == "pdf"
    assert src.args["page"] == 3

def test_alias_define_y_layout_spritesheet():
    s = "@sp1 = @{ pdf src='file.pdf' }.Layout{ g=3x2^ s=poker extract }"
    cmd = parse(s)
    assert cmd.name == "AliasDefine"
    assert cmd.args["alias"] == "sp1"
    val = cmd.args["value"]
    assert isinstance(val, Command) and val.name == "Layout"
    assert val.layout.extract is True
    assert val.layout.grid.order == "tb-lr"
    assert val.layout.grid.cols == 3 and val.layout.grid.rows == 2

def test_alias_access_indices_lineal_y_multidim():
    c1 = parse("@sp1[14]")
    a1 = c1.target
    assert isinstance(a1, AliasRef)
    assert len(a1.indices) == 1
    assert isinstance(a1.indices[0], int) and a1.indices[0] == 14

    c2 = parse("@sp1[2][1][3]")
    a2 = c2.target
    assert len(a2.indices) == 3
    assert isinstance(a2.indices[0], int)
    assert isinstance(a2.indices[1], int)
    assert isinstance(a2.indices[2], int)

def test_alias_access_indices_rango_lista_y_asterisco():
    c1 = parse("@sp1[2..4]")
    a1 = c1.target
    from dsl import RangeIdx, ListIdx, StarIdx
    assert isinstance(a1.indices[0], RangeIdx)
    assert a1.indices[0].a == 2 and a1.indices[0].b == 4

    c2 = parse("@sp1[2 4 6]")
    a2 = c2.target
    assert isinstance(a2.indices[0], ListIdx)
    assert a2.indices[0].items == [2,4,6]

    c3 = parse("@sp1[*]")
    a3 = c3.target
    assert a3.indices and hasattr(a3.indices[0], "__class__")

# -------------------------
# ERRORES esperados
# -------------------------

@pytest.mark.parametrize("s", [
    "Icono.Fit{ shift=[10] }",
    ".Layout{ g=3 }",
    ".Layout{ g=abcx2 }",
    "@{ pdf }",
    "@sp1 = no_es_valido",
])
def test_errores(s):
    with pytest.raises(DSLError):
        parse(s)

# -------------------------
# ROTACIÓN repetida y combinada
# -------------------------

def test_rotacion_repetida_y_combinada():
    fs = parse("ID~^^^").fit
    assert fs.rotate == 270.0
    fs2 = parse("ID~^^ ^-45").fit
    assert fs2.rotate in (135.0, 180.0-45.0)

# -------------------------
# NO COMMAS as separators
# -------------------------

def test_no_comas_como_separadores_en_listas_y_args():
    cmd = parse(".Layout{ g=3x2 k=[4 3] }")
    assert cmd.layout.grid.kerf == [4.0, 3.0]
    with pytest.raises(DSLError):
        parse(".Layout{ k=[4, 3] }")

# -------------------------
# SHAPE: implicit rect size
# -------------------------

def test_shape_rect_implicito_por_tamano_suelto():
    cmd = parse(".Layout{ s=63x88mm }")
    assert cmd.layout.shape.kind == "rect"
    assert cmd.layout.shape.args == ["63x88mm"]

# -------------------------
# FIT: c (clip) recognized in shorthand and as a key
# -------------------------

def test_fit_clip_c_shorthand():
    fs = parse("ID~c").fit
    assert fs.clip is True

def test_fit_clip_clave_c_en_largo():
    fs = parse("ID.Fit{ c }").fit
    assert fs.clip is True

# -------------------------
# Helpers SOURCE/OPS/FIT + CHAIN
# -------------------------

def test_helpers_source_ops_y_fit():
    assert is_source_expr("@{a.png}")
    src, suf = split_source_token("@{img/a.png}~^12i||")
    assert _is_src(src, "img/a.png")
    assert suf.kind == "ops" and suf.ops == "i^12||"

    src2, suf2 = split_source_token("@{ img src='b.svg' }.Fit{ anchor=7 mode=i }")
    assert _is_src(src2, "b.svg")
    assert suf2.kind == "fit" and isinstance(suf2.fit, FitSpec)
    assert suf2.fit.anchor == 7 and suf2.fit.mode == "i"

def test_ops_roundtrip_fit_spec():
    fs = FitSpec(mode="i", anchor=9, border=["1mm"], shift=[10, -5], rotate=12.0, mirror="h", clip=True, clip_stage="pre")
    ops = ops_from_fit_spec(fs)
    fs2 = fit_spec_from_ops(ops)
    assert fs2.mode == "i" and fs2.anchor == 9
    assert fs2.border and fs2.border[0] == "1mm"
    assert fs2.rotate == 12.0
    assert fs2.mirror in ("h","v","none","h")

def test_parse_chain_simple_fit_desde_source():
    c = parse_chain("@{img/a.png}.Fit{ anchor=7 mode=i }")
    assert _is_src(c.target, "img/a.png")
    assert len(c.modules) == 1 and c.modules[0].name.lower() == "fit"
    assert isinstance(c.modules[0].spec, FitSpec)
    assert c.modules[0].spec.anchor == 7 and c.modules[0].spec.mode == "i"
    assert c.legacy_ops is None

def test_parse_chain_grupo_layer_fit():
    expr = "[id1 @{https://x/y.png} id2].Layer{ label='X' }.Fit{ mode=i a=9 }"
    c = parse_chain(expr)
    assert hasattr(c.target, "items")
    assert len(c.target.items) == 3
    assert _is_id(c.target.items[0], "id1")
    assert isinstance(c.target.items[1], SourceRef) and c.target.items[1].stype == "url"
    assert _is_id(c.target.items[2], "id2")
    assert [m.name for m in c.modules] == ["Layer","Fit"]
    assert isinstance(c.modules[1].spec, FitSpec)
    assert c.modules[1].spec.mode == "i" and c.modules[1].spec.anchor == 9

def test_parse_chain_con_legacy_ops():
    c = parse_chain("@{img/a.png}~^12i")
    assert _is_src(c.target, "img/a.png")
    assert c.legacy_ops == "i^12"
    assert c.modules == []

def test_tokenize_y_parse_casos_borde():
    with pytest.raises(DSLError):
        tokenize_chain("@{bad")
    c = parse_chain(".Layout{ g='3x3' }")
    assert c.target is None and len(c.modules)==1 and c.modules[0].name.lower() in ("layout","l")

def test_fit_shorthand_best_fit_b():
    fs = parse("ID~b").fit
    assert fs.mode == "b"


# -------------------------
# Marks{} (leading-cell DSL)
# -------------------------

def test_marks_defaults_and_style_default_param():
    # default param => style id
    ms = parse_marks_block("M{ mk_style }")
    assert ms.style == "mk_style"
    assert ms.layer is None
    assert ms.b is None
    assert ms.d is None
    assert ms.length is None

def test_marks_len_scalar_and_list():
    ms1 = parse_marks_block("M{ len=3 }")
    assert ms1.length == ["3", "0"]
    ms2 = parse_marks_block("M{ len=[3 2] }")
    assert ms2.length == ["3", "2"]

def test_leading_cell_parses_page_layout_marks_chain():
    lead = parse_leading_cell("{A4}.L{g=3x3 k=2}.M{ mk_style d=2 b=[0] }")
    assert lead.page_block and lead.page_block.startswith("{")
    assert lead.layout_block and lead.layout_block.startswith("L{")
    assert lead.marks_block and lead.marks_block.startswith("M{")

def test_leading_cell_holes_sequence_with_count_suffix():
    lead = parse_leading_cell("[10 3- 5 2- 5]")
    assert lead.copies == 20
    assert lead.copies_explicit is True
    # 3 empty slots after copy 10, and 2 after copy 15
    assert lead.holes.count(10) == 3
    assert lead.holes.count(15) == 2

def test_leading_cell_copies_explicit_flag():
    assert parse_leading_cell("").copies_explicit is False
    assert parse_leading_cell("*").copies == 1
    assert parse_leading_cell("*").copies_explicit is False
    assert parse_leading_cell("7").copies_explicit is True

def test_source_token_variants_are_equivalent():
    s1, _ = split_source_token("@{https://example.com/a.png}")
    s2, _ = split_source_token("S{https://example.com/a.png}")
    s3, _ = split_source_token("Source{https://example.com/a.png}")
    assert s1.src == s2.src == s3.src
