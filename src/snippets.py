# -*- coding: utf-8 -*-
r"""
snippets.py ? snippet engine (new format, no retro-compat)

Definition (only supported format):
  # :Name(arg1 arg2=def ...) = template WITHOUT quotes

Examples:
  # :TB(text) = <tspan font-weight='bold'>${text}</tspan>
  # :TF(text font size) = <tspan font-family='${font}'${size? font-size='${size}'}>${text}</tspan>

Calls in text:
  :TB(Hello)
  :TF(Title Noto 12px)

Features:
  - Nesting (inner first), with safety limits.
  - Expands calls even inside quotes.
  - Supports positional args, named args, and defaults.
  - Space-separated; 'name=value' also allows spaces around '='.
  - Escaping with '\\:Name(...)' to keep literal.
  - If snippet does not exist or call is malformed, it stays literal.
  - Conditional inclusion: ${var? ...} -> includes '...' only if var has a non-empty value;
    supports nested ${...} inside the conditional body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
import re
import shlex

# ---------------- Constants / Regex ----------------

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# detect ':Name(' without space between name and '('
CALL_LEAD_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)\(")


# ---------------- Model ----------------

@dataclass
class SnippetDef:
    name: str
    params: List[str]
    defaults: Dict[str, str]
    template: str


# --------------- Definition parsing ----------------

def _split_args_spec(spec: str) -> Tuple[List[str], Dict[str, str]]:
    """Parsea la lista de argumentos de la DEFINICIÓN: 'a b=def c=\"x y\"'."""
    spec = (spec or "").strip()
    if not spec:
        return [], {}
    try:
        tokens = shlex.split(spec, posix=True)
    except ValueError:
        tokens = spec.split()

    params: List[str] = []
    defaults: Dict[str, str] = {}
    for t in tokens:
        if "=" in t:
            k, v = t.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if not IDENT_RE.fullmatch(k):
                continue
            if k not in params:
                params.append(k)
            defaults[k] = v
        else:
            k = t.strip()
            if IDENT_RE.fullmatch(k) and k not in params:
                params.append(k)
    return params, defaults


def parse_definition_line(line: str) -> Optional[SnippetDef]:
    """
    Accept only:
      # :Name(arg1 arg2=default) = template
    There is no compatibility with '->' or mandatory quotes.
    There must be no space between the name and '('.
    """
    if not line:
        return None
    s = line.strip()
    if not s.startswith("#"):
        return None
    body = s[1:].lstrip()
    if not body.startswith(":"):
        return None

    # :Name( ... ) = template
    i = 1
    m = IDENT_RE.match(body, i)
    if not m:
        return None
    name = m.group(0)
    j = m.end()

    # '(' must follow the name without a space.
    if j >= len(body) or body[j] != "(":
        return None

    # capture block (...), balancing parentheses
    j += 1
    par = 1
    start_args = j
    while j < len(body) and par > 0:
        c = body[j]
        if c == "(":
            par += 1
        elif c == ")":
            par -= 1
        j += 1
    if par != 0:
        return None
    args_spec = body[start_args:j-1].strip()

    # ahora debe venir '='
    rest = body[j:].lstrip()
    if not rest.startswith("="):
        return None

    template = rest[1:].lstrip()  # todo lo que queda tras '='

    params, defaults = _split_args_spec(args_spec)
    return SnippetDef(name=name, params=params, defaults=defaults, template=template)


def load_definitions_from_comments(comment_lines: List[str]) -> Dict[str, SnippetDef]:
    """Build the snippet registry from comment lines.

    Accepts either strings or CSV/Sheet rows and uses the first cell.
    """
    reg: Dict[str, SnippetDef] = {}
    for raw in (comment_lines or []):
        # Normalize: may be a full CSV/Sheet row.
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else ""
        else:
            raw = str(raw)
        d = parse_definition_line(raw)
        if d is not None:
            reg[d.name] = d
    return reg

# --------------- Call and argument parsing ----------------

def _find_call_at(text: str, start: int) -> Optional[Tuple[int, int, str, str]]:
    """
    If ':Name(' starts at `start`, return (i0, i1, name, inner).
    i0..i1 spans the full ':Name(...)' call; inner is the content inside.
    """
    m = CALL_LEAD_RE.match(text, start)
    if not m:
        return None
    name = m.group(1)
    i0 = m.start()
    j = m.end()
    par = 1
    start_inner = j
    while j < len(text) and par > 0:
        c = text[j]
        if c == "(":
            par += 1
        elif c == ")":
            par -= 1
        j += 1
    if par != 0:
        return None
    inner = text[start_inner:j-1]
    i1 = j
    return (i0, i1, name, inner)


def _split_call_args(inner: str) -> List[str]:
    """Split call arguments on spaces while respecting quotes and nested calls."""
    inner = (inner or "").strip()
    if not inner:
        return []

    out: List[str] = []
    buf: List[str] = []
    quote = ""
    keep_quote = False
    token_started = False
    par = 0
    bracket = 0
    i = 0
    while i < len(inner):
        ch = inner[i]
        if quote:
            if ch == "\\" and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == quote:
                if keep_quote:
                    buf.append(ch)
                quote = ""
                keep_quote = False
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            token_started = True
            quote = ch
            keep_quote = par > 0
            if keep_quote:
                buf.append(ch)
        elif ch == "(":
            par += 1
            buf.append(ch)
        elif ch == ")" and par > 0:
            par -= 1
            buf.append(ch)
        elif ch == "[":
            bracket += 1
            buf.append(ch)
        elif ch == "]" and bracket > 0:
            bracket -= 1
            buf.append(ch)
        elif ch.isspace() and par == 0 and bracket == 0:
            if token_started:
                out.append("".join(buf))
                buf = []
                token_started = False
        else:
            token_started = True
            buf.append(ch)
        i += 1
    if token_started:
        out.append("".join(buf))
    return out


def _parse_call_kwargs(tokens: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """Split positional and named arguments, also repairing 'name = value'."""
    fixed: List[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if i + 2 < len(tokens) and tokens[i + 1] == "=":
            fixed.append(f"{t}={tokens[i + 2]}")
            i += 3
        else:
            fixed.append(t)
            i += 1

    pos: List[str] = []
    named: Dict[str, str] = {}
    for t in fixed:
        if "=" in t:
            k, v = t.split("=", 1)
            named[k] = v
        else:
            pos.append(t)
    return pos, named


def _apply_args_to_def(defn: SnippetDef, pos: List[str], named: Dict[str, str]) -> Dict[str, str]:
    """Build the final variable mapping for the template."""
    out: Dict[str, str] = {}
    for i, p in enumerate(defn.params):
        if i < len(pos):
            out[p] = pos[i]
    for k, v in named.items():
        if k in defn.params:
            out[k] = v
    for p in defn.params:
        if p not in out and p in defn.defaults:
            out[p] = defn.defaults[p]
    return out


# --------------- Substitution (includes conditionals) ----------------

def _resolve_mapping_expr(expr: str, mapping: Dict[str, Any]) -> Any:
    """Resolve a small safe expression: name, name[0], name[0].attr."""
    s = str(expr or "").strip()
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", s)
    if not m:
        return ""
    cur = mapping.get(m.group(1), "")
    i = m.end()
    n = len(s)
    while i < n:
        if s[i] == ".":
            i += 1
            m_attr = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s[i:])
            if not m_attr:
                return ""
            attr = m_attr.group(0)
            if isinstance(cur, dict):
                cur = cur.get(attr, "")
            else:
                cur = getattr(cur, attr, "")
            i += len(attr)
            continue
        if s[i] == "[":
            j = s.find("]", i + 1)
            if j < 0:
                return ""
            idx_s = s[i + 1:j].strip()
            try:
                idx = int(idx_s)
                cur = cur[idx]
            except Exception:
                return ""
            i = j + 1
            continue
        return ""
    return cur


def _split_conditional_body(body: str) -> Tuple[str, Optional[str]]:
    """Split 'true : false' at a top-level ':'; keep legacy 'true' unchanged."""
    quote = ""
    brace_depth = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\" and i + 1 < len(body):
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}" and brace_depth > 0:
            brace_depth -= 1
            i += 1
            continue
        if (
            ch == ":"
            and brace_depth == 0
            and i > 0
            and i + 1 < len(body)
            and body[i - 1].isspace()
            and body[i + 1].isspace()
        ):
            return body[:i].strip(), body[i + 1:].strip()
        i += 1
    return body, None


def _apply_conditionals(tpl: str, mapping: Dict[str, Any]) -> str:
    """
    Aplica inclusiones condicionales del tipo:
      ${var? ...}
    Incluye el cuerpo '...' solo si mapping[var] es no vacío.
    Soporta ${...} anidados dentro del cuerpo.
    """
    if "${" not in tpl:
        return tpl

    out_chunks: List[str] = []
    i = 0
    n = len(tpl)

    while i < n:
        # find the next ${ sequence
        k = tpl.find("${", i)
        if k == -1:
            out_chunks.append(tpl[i:])
            break

        # copy the previous segment
        out_chunks.append(tpl[i:k])

        # try parsing ${var? body}
        j = k + 2  # Position after '${'.
        # var name
        m = IDENT_RE.match(tpl, j)
        if not m:
            # not an identifier -> not a conditional: keep literal '${'
            out_chunks.append("${")
            i = j
            continue

        var = m.group(0)
        j = m.end()

        # is there a '?' next?
        if j >= n or tpl[j] != "?":
            # not conditional -> keep '${' and continue (will resolve later in ${var})
            out_chunks.append("${" + var)
            i = j
            continue

        j += 1  # saltar '?'
        # skip optional spaces
        while j < n and tpl[j].isspace():
            j += 1

        # Scan to the outer closing brace, balancing modifier and nested braces.
        body_start = j
        brace_depth = 0
        quote = ""
        found_close = False
        while j < n:
            ch = tpl[j]
            if quote:
                if ch == "\\" and j + 1 < n:
                    j += 2
                    continue
                if ch == "$" and j + 1 < n and tpl[j + 1] == "{":
                    brace_depth += 1
                    j += 2
                    continue
                if ch == "}" and brace_depth > 0:
                    brace_depth -= 1
                    j += 1
                    continue
                if ch == quote:
                    quote = ""
                j += 1
                continue
            if ch in ("'", '"'):
                quote = ch
                j += 1
                continue
            if ch == "{":
                brace_depth += 1
                j += 1
                continue
            if ch == "}" and brace_depth == 0:
                found_close = True
                break
            if ch == "}" and brace_depth > 0:
                brace_depth -= 1
                j += 1
                continue
            j += 1

        if not found_close:
            # no closing brace -> treat as literal '${'
            out_chunks.append("${" + var + "?")
            i = body_start
            continue

        true_body, false_body = _split_conditional_body(tpl[body_start:j])
        val = _resolve_mapping_expr(var, mapping)
        if val:
            out_chunks.append(true_body)
        elif false_body is not None:
            out_chunks.append(false_body)

        i = j + 1  # avanzar tras '}'

    return "".join(out_chunks)


def _substitute_template(tpl: str, mapping: Dict[str, Any]) -> str:
    """
    1) Resuelve inclusiones condicionales ${var? ...}
    2) Sustituye ${var} por mapping[var] (vacío si falta)
    """
    # 1) Conditionals
    tpl = _apply_conditionals(tpl, mapping)

    # 2) ${var} / ${var[0].attr} substitution
    def repl(m):
        key = m.group(1)
        return str(_resolve_mapping_expr(key, mapping))
    return re.sub(r"\$\{([^{}]*)\}", repl, tpl)


def expand_variables_in_text(text: str, variables: Optional[Dict[str, Any]] = None) -> str:
    if not text or not variables:
        return text
    return _substitute_template(text, variables)


# --------------- Text expansion ----------------

def expand_snippets_in_text(text: str,
                            registry: Dict[str, SnippetDef],
                            *,
                            variables: Optional[Dict[str, Any]] = None,
                            max_depth: int = 32,
                            max_expansions: int = 10000) -> str:
    """
    Expande todas las llamadas :Nombre(...) en 'text'.
    """
    if not text:
        return text
    if not registry:
        return expand_variables_in_text(text, variables)

    ESC_MARK = "\uE000"
    text = text.replace(r"\:", ESC_MARK)

    expansions = 0

    def _expand_once(s: str, depth: int) -> str:
        nonlocal expansions
        if depth <= 0 or expansions >= max_expansions:
            return s
        i = 0
        chunks: List[str] = []
        while i < len(s):
            m = CALL_LEAD_RE.search(s, i)
            if not m:
                chunks.append(s[i:])
                break
            chunks.append(s[i:m.start()])

            found = _find_call_at(s, m.start())
            if not found:
                chunks.append(s[m.start()])
                i = m.start() + 1
                continue

            i0, i1, name, inner = found
            defn = registry.get(name)
            if defn is None:
                inner_expanded = _expand_once(inner, depth - 1)
                chunks.append(f":{name}({inner_expanded})")
                i = i1
                continue

            tokens = _split_call_args(inner)
            raw_pos, raw_named = _parse_call_kwargs(tokens)
            pos = [_expand_once(v, depth - 1) for v in raw_pos]
            named = {k: _expand_once(v, depth - 1) for k, v in raw_named.items()}

            # Single-param snippets take the whole inner content as text.
            if len(defn.params) == 1:
                only = defn.params[0]
                inner_expanded = _expand_once(inner, depth - 1)
                if only not in named and (inner_expanded.strip() != ""):
                    argmap = {only: inner_expanded.strip()}
                else:
                    argmap = _apply_args_to_def(defn, pos, named)
            else:
                argmap = _apply_args_to_def(defn, pos, named)

            # 4) substitute template (with conditionals)
            tpl_mapping: Dict[str, Any] = {}
            if variables:
                tpl_mapping.update(variables)
            tpl_mapping.update(argmap)
            result = _substitute_template(defn.template, tpl_mapping)
            chunks.append(result)
            i = i1
            expansions += 1
            if expansions >= max_expansions:
                chunks.append(s[i:])
                break

        return "".join(chunks)

    cur = text
    for _ in range(max_depth):
        before = cur
        cur = _expand_once(cur, max_depth)
        if cur == before:
            break

    cur = cur.replace(ESC_MARK, ":")
    return expand_variables_in_text(cur, variables)
