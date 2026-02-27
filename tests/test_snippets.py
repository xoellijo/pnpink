# test_snippets_unittest.py
# Unit tests for snippets.py v0.2 (strict definitions with parentheses) using stdlib unittest
# Run: python test_snippets_unittest.py
# [2026-02-19] Chore: translate comments to English.

import unittest
import snippets as S


def _make_registry_basic():
    blocks = ["""
# :bold(txt) -> "<b>${txt}</b>"
# :icon(name size=16) -> "<span class='icon' data-name='${name}' data-size='${size}'/>"
# :pair(a b="B") -> "${a} + ${b}"
# :wrap(left="[" right="]" txt="") -> "${left}${txt}${right}"
# :kv(key value) -> "${key}=${value}"
# :echo(a) -> "${a}"
# :need2(a b) -> "${a}-${b}"
# :hole(a) -> "${a} ${missing}"  # placeholder 'missing' not provided on purpose
"""]
    return S.load_definitions_from_comments(blocks)


def _expand(text, reg, **kw):
    return S.expand_snippets_in_text(text, reg, **kw)


class TestDefinitions(unittest.TestCase):
    def test_load_definitions_basic(self):
        reg = _make_registry_basic()
        self.assertTrue({"bold", "icon", "pair", "wrap", "kv", "echo", "need2", "hole"}.issubset(reg.keys()))
        s = reg["icon"]
        self.assertEqual(s.params, ["name", "size"])
        self.assertEqual(s.defaults, {"size": "16"})
        self.assertIn("${name}", s.template)
        self.assertIn("${size}", s.template)

    def test_ignores_bad_definition_with_space_before_paren(self):
        blocks = ["""
# :ok(a) -> "X ${a}"
# :bad a -> "NO"   # invalid: there is a space between name and "("
"""]
        reg = S.load_definitions_from_comments(blocks)
        self.assertIn("ok", reg)
        self.assertNotIn("bad", reg)

    def test_definition_formats_with_spaces_only(self):
        blocks = ["""
# :A(x y=2) -> "${x}-${y}"
# :B(text) -> "${text}"
"""]
        reg = S.load_definitions_from_comments(blocks)
        self.assertEqual(reg["A"].params, ["x", "y"])
        self.assertEqual(reg["A"].defaults, {"y": "2"})
        self.assertEqual(_expand(":A(1)", reg), "1-2")
        self.assertEqual(_expand(":A(1 y=9)", reg), "1-9")
        self.assertEqual(_expand(':B("ok ok")', reg), "ok ok")


class TestExpansionBasics(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_positional_and_named(self):
        out = _expand(":pair(Hello b=World)", self.reg)
        self.assertEqual(out, "Hello + World")

        out = _expand(":pair(A B)", self.reg)
        self.assertEqual(out, "A + B")

        out = _expand(":pair(X)", self.reg)
        self.assertEqual(out, "X + B")

    def test_named_then_positional_ignored_extra(self):
        out = _expand(":kv(key=foo value=bar extra data)", self.reg)
        self.assertEqual(out, "foo=bar")

    def test_name_equals_followed_by_value_with_spaces_and_nested(self):
        txt = ':kv(key= "A B C" value=:bold("Z"))'
        out = _expand(txt, self.reg)
        self.assertEqual(out, "A B C=<b>Z</b>")


class TestNestingAndQuotes(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_nested_calls(self):
        out = _expand(':pair(:bold("A") :icon(name=star size=20))', self.reg)
        self.assertEqual(out, "<b>A</b> + <span class='icon' data-name='star' data-size='20'/>")

    def test_calls_inside_quotes_are_expanded(self):
        txt = 'title ":bold(\\"XX\\")" tail'
        out = _expand(txt, self.reg)
        self.assertEqual(out, 'title "<b>XX</b>" tail')


class TestEscapingUnknownMissing(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_escape_prevents_expansion(self):
        out = _expand('\\:bold(X)', self.reg)
        self.assertEqual(out, ':bold(X)')

    def test_unknown_snippet_leaves_literal(self):
        src = ':nope(x y z)'
        out = _expand(src, self.reg)
        self.assertEqual(out, src)

    def test_missing_required_param_leaves_literal(self):
        src = ':need2(A)'
        out = _expand(src, self.reg)
        self.assertEqual(out, src)

    def test_placeholder_missing_in_template_aborts(self):
        src = ':hole(X)'
        out = _expand(src, self.reg)
        self.assertEqual(out, src)


class TestSyntaxRobustness(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_unbalanced_parenthesis_leaves_literal(self):
        src = ':bold("X"'
        out = _expand(src, self.reg)
        self.assertEqual(out, src)

    def test_bad_quote_leaves_literal(self):
        src = ':bold("unterminated)'
        out = _expand(src, self.reg)
        self.assertEqual(out, src)


class TestLimits(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_max_depth_abort_literal(self):
        depth = 20
        s = '"X"'
        for _ in range(depth):
            s = f":echo({s})"
        out = _expand(s, self.reg)  # default max_depth=16
        self.assertEqual(out, s)

        out2 = _expand(s, self.reg, max_depth=64)
        self.assertEqual(out2, "X")

    def test_max_expansions_cap_is_respected(self):
        text = " ".join([":echo(A)"] * 50)
        out = _expand(text, self.reg, max_expansions=5)
        parts = out.split(" ")
        self.assertEqual(parts[:5], ["A"] * 5)
        self.assertEqual(parts[5], ":echo(A)")


class TestMultipleCalls(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_multiple_calls_in_text(self):
        src = 'prefix :bold(One) mid :pair(Two Three) suffix'
        out = _expand(src, self.reg)
        self.assertEqual(out, 'prefix <b>One</b> mid Two + Three suffix')


# ---------------------------
# ESPACIOS / FORMATO
# ---------------------------

class TestWhitespaceAndSpacing(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry_basic()

    def test_multiple_spaces(self):
        out = _expand(":pair(   A    B   )", self.reg)
        self.assertEqual(out, "A + B")

    def test_tabs_and_newlines(self):
        out = _expand(":pair(\nA\tB)", self.reg)
        self.assertEqual(out, "A + B")

    def test_number_as_argument(self):
        self.assertEqual(_expand(":echo(123)", self.reg), "123")
        self.assertEqual(_expand(":echo(-5)", self.reg), "-5")

    def test_two_calls_back_to_back(self):
        out = _expand(":bold(X):bold(Y)", self.reg)
        self.assertEqual(out, "<b>X</b><b>Y</b>")

    def test_call_followed_by_text(self):
        out = _expand(":bold(X)Z", self.reg)
        self.assertEqual(out, "<b>X</b>Z")

    def test_preserve_spaces_inside_quotes(self):
        out = _expand(':echo("  spaced  ")', self.reg)
        self.assertEqual(out, "  spaced  ")

    def test_spaces_around_equals(self):
        out = _expand(":kv(key = foo value = bar)", self.reg)
        self.assertEqual(out, "foo=bar")

    def test_nested_with_extra_spaces(self):
        out = _expand(':pair(  :bold( "Hola" )   :icon(  name = star   size = 32  ) )', self.reg)
        self.assertEqual(out, "<b>Hola</b> + <span class='icon' data-name='star' data-size='32'/>")


class TestNoRegistry(unittest.TestCase):
    def test_no_registry_returns_original(self):
        src = ':bold(X)'
        out = S.expand_snippets_in_text(src, registry={})
        self.assertEqual(out, src)


if __name__ == "__main__":
    unittest.main()
