# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
r"""
snippets.py ? snippet engine (new format, no retro-compat)

Definition (only supported format):
  # :Name(arg1 arg2=def ...) = template WITHOUT quotes

Examples:
  # :Tb(text) = <tspan font-weight='bold'>${text}</tspan>
  # :Tf(text font size) = <tspan font-family='${font}'${size? font-size='${size}'}>${text}</tspan>

Calls in text:
  :Tb(Hello)
  :Tf(Title Noto 12px)

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
from typing import Dict, List, Tuple, Optional
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
    Acepta ÚNICAMENTE:
      # :Nombre(arg1 arg2=def) = plantilla
    Sin retrocompatibilidad con '->' ni comillas obligatorias.
    Debe NO haber espacio entre el nombre y '('.
    """
    if not line:
        return None
    s = line.strip()
    if not s.startswith("#"):
        return None
    body = s[1:].lstrip()
    if not body.startswith(":"):
        return None

    # :Nombre( ... ) = plantilla
    i = 1
    m = IDENT_RE.match(body, i)
    if not m:
        return None
    name = m.group(0)
    j = m.end()

    # debe venir '(' SIN espacio
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
    """Construye el registro de snippets a partir de líneas de comentario.
       Acepta tanto cadenas como filas (listas/tuplas) y usa la primera celda."""
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
    Si en 'start' hay ':Nombre(', devuelve (i0, i1, name, inner)
    donde i0..i1 abarca toda la llamada ':Nombre(...)' y 'inner' es el contenido dentro.
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
    """Divide los argumentos de la llamada por espacios (respeta comillas)."""
    inner = (inner or "").strip()
    if not inner:
        return []
    try:
        return shlex.split(inner, posix=True)
    except ValueError:
        return inner.split()


def _parse_call_kwargs(tokens: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """Separa argumentos posicionales y nombrados (y repara 'name = value')."""
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
    """Construye el mapping final de variables para la plantilla."""
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

def _apply_conditionals(tpl: str, mapping: Dict[str, str]) -> str:
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
        j = k + 2  # posición tras '${'
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

        # escanear cuerpo hasta '}' considerando ${...} anidados
        body_start = j
        depth = 0
        found_close = False
        while j < n:
            if tpl.startswith("${", j):
                depth += 1
                j += 2
                continue
            ch = tpl[j]
            if ch == "}" and depth == 0:
                found_close = True
                break
            elif ch == "}" and depth > 0:
                depth -= 1
                j += 1
                continue
            else:
                j += 1

        if not found_close:
            # no closing brace -> treat as literal '${'
            out_chunks.append("${" + var + "?")
            i = body_start
            continue

        body = tpl[body_start:j]  # sin la llave de cierre
        # include body if the variable has a value
        val = mapping.get(var, "")
        if val:
            out_chunks.append(body)
        # if empty, add nothing

        i = j + 1  # avanzar tras '}'

    return "".join(out_chunks)


def _substitute_template(tpl: str, mapping: Dict[str, str]) -> str:
    """
    1) Resuelve inclusiones condicionales ${var? ...}
    2) Sustituye ${var} por mapping[var] (vacío si falta)
    """
    # 1) Conditionals
    tpl = _apply_conditionals(tpl, mapping)

    # 2) Simple ${var} substitution
    def repl(m):
        key = m.group(1)
        return str(mapping.get(key, ""))
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, tpl)


# --------------- Text expansion ----------------

def expand_snippets_in_text(text: str,
                            registry: Dict[str, SnippetDef],
                            *,
                            max_depth: int = 32,
                            max_expansions: int = 10000) -> str:
    """
    Expande todas las llamadas :Nombre(...) en 'text'.
    """
    if not text or not registry:
        return text

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
            # 1) expand inner first (nesting)
            inner_expanded = _expand_once(inner, depth - 1)

            # 2) if snippet does not exist, keep literal (with inner already expanded)
            defn = registry.get(name)
            if defn is None:
                chunks.append(f":{name}({inner_expanded})")
                i = i1
                continue

            # 3) parse arguments
            tokens = _split_call_args(inner_expanded)
            pos, named = _parse_call_kwargs(tokens)

            # 3bis) Special case: single unnamed positional param -> take ALL inner
            if len(defn.params) == 1:
                only = defn.params[0]
                if only not in named and (inner_expanded.strip() != ""):
                    argmap = {only: inner_expanded.strip()}
                else:
                    argmap = _apply_args_to_def(defn, pos, named)
            else:
                argmap = _apply_args_to_def(defn, pos, named)

            # 4) substitute template (with conditionals)
            result = _substitute_template(defn.template, argmap)
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
    return cur
