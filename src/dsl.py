# [2026-02-19] Fix: correct numeric regex call for fit shift parsing.
# [2026-02-19] Fix: allow negative grid tokens to flip grid axes.
# [2026-02-19] Add: split layout gaps into gaps + offset properties.
# [2026-02-19] Change: drop legacy 6-value gaps parsing.
# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "DSLError",
    "IdRef", "AliasRef", "RangeIdx", "ListIdx", "StarIdx",
    "SourceRef", "GroupRef",
    "FitSpec", "LayoutSpec", "PageSpec", "GridSpec", "ShapeSpec",
    "ModuleCall", "Chain", "Command",
    "parse", "maybe_parse",
    "tokenize_chain", "parse_chain", "maybe_parse_chain",
    "is_source_expr", "split_source_token", "normalize_ops_suffix",
    "ops_from_fit_spec", "fit_spec_from_ops",
    "parse_copies_page_tail", "measure_to_mm",
    "parse_dataset_decl"
]

# =================== Errors ===================
class DSLError(Exception):
    ...

# =================== AST base ===================
@dataclass
class IdRef:
    name: str

@dataclass
class RangeIdx:
    a: int
    b: int

@dataclass
class ListIdx:
    items: List[int]

@dataclass
class StarIdx:
    pass

@dataclass
class AliasRef:
    name: str
    indices: List[Union[int, RangeIdx, ListIdx, StarIdx]]

@dataclass
class SourceRef:
    stype: str
    src: str
    args: Dict[str, Any] = field(default_factory=dict)

@dataclass
class GroupRef:
    items: List[Union[IdRef, SourceRef]]

# ---- Known specifications (modules) ----
@dataclass
class FitSpec:
    mode: Optional[str] = None
    anchor: Optional[int] = None
    border: Optional[List[str]] = None
    shift: Optional[List[Union[float, str]]] = None
    rotate: Optional[float] = None
    mirror: Optional[str] = None     # 'h'|'v'|'none'
    clip: Optional[bool] = None
    clip_stage: Optional[str] = None # 'pre'|'post'

@dataclass
class GridSpec:
    cols: int
    rows: int
    order: Optional[str] = None    # 'lr-tb','tb-lr'
    flip: Optional[str] = None     # 'h'|'v'
    gaps: Optional[List[Union[float,str]]] = None
    offset: Optional[List[Union[float,str]]] = None

@dataclass
class PageSpec:
    size: Optional[str] = None
    landscape: Optional[bool] = None
    border: Optional[List[str]] = None
    multiplier: Optional[int] = None
    pagebreak_only: Optional[bool] = None
    # v0.9+: global page cursor control (owned by Page{}, not Layout{}).
    # Examples: at=+3, a=-1, @5
    at: Optional[str] = None

@dataclass
class ShapeSpec:
    kind: Optional[str] = None
    preset: Optional[str] = None
    args: Optional[List[str]] = None

@dataclass
class LayoutSpec:
    grid: Optional[GridSpec] = None
    page: Optional[PageSpec] = None   # kept for spritesheet cases (not used here)
    shape: Optional[ShapeSpec] = None
    gaps: Optional[List[Union[float, str]]] = None  # compat (k as sibling)
    offset: Optional[List[Union[float, str]]] = None
    extract: Optional[bool] = None
    # Note: page cursor and page size/orientation live in Page{}, not in Layout{}.


@dataclass
class MarksSpec:
    """Marks{} / M{} specification.

    Dev note:
      - The default parameter is the style id (s=...).
      - b and d reuse the same list grammar as Page/Fit border: 1/2/3/4 tokens.
      - Rendering is slot-based (per placed instance).
    """
    style: Optional[str] = None   # s
    layer: Optional[str] = None   # target layer label
    # b: bbox inset/outset tokens (default 0). Negative values move marks inward.
    b: Optional[List[str]] = None
    # d: distance from bbox tokens (default 0mm; flush to the bbox corner)
    d: Optional[List[str]] = None
    # length tokens. If the user provides a scalar (e.g. l=5) we keep the
    # second component as None (i.e. ["5", None]) so marks.py can apply
    # heuristics/defaults (e.g. gaps-offset => internal length = external).
    length: Optional[List[Optional[str]]] = None  # len=[out in] or scalar -> [out,None]

@dataclass
class ModuleCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    spec: Optional[Union[FitSpec, LayoutSpec]] = None

@dataclass
class Chain:
    target: Optional[Union[IdRef, SourceRef, GroupRef]]
    modules: List[ModuleCall]
    legacy_ops: Optional[str] = None

@dataclass
class Command:
    name: str
    target: Optional[Union[IdRef, AliasRef, SourceRef]] = None
    fit: Optional[FitSpec] = None
    layout: Optional[LayoutSpec] = None
    args: Optional[Dict[str, Any]] = None

# =================== Common utils ===================
_num_pure_re = re.compile(r"^[-+]?\d+(?:\.\d+)?$")

def _to_number(s: str) -> Union[float, str]:
    s = str(s).strip()
    return float(s) if _num_pure_re.match(s) else s

def _try_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _try_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]

def _num_to_str_trim(v: Any) -> str:
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(v)
    except (TypeError, ValueError):
        return str(v)

def _strip_balanced(s: str, open_ch: str, close_ch: str) -> str:
    if not (s and s[0] == open_ch and s[-1] == close_ch):
        raise DSLError("Bloque desbalanceado")
    return s[1:-1].strip()

def _bal_find(s: str, i: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    for k in range(i, len(s)):
        c = s[k]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return k
    return -1

def _split_top(s: str) -> List[str]:
    out: List[str] = []
    tok: List[str] = []
    d_b = d_c = d_a = 0
    for ch in s:
        if ch == '[':
            d_b += 1; tok.append(ch); continue
        if ch == ']':
            d_b -= 1; tok.append(ch); continue
        if ch == '{':
            d_c += 1; tok.append(ch); continue
        if ch == '}':
            d_c -= 1; tok.append(ch); continue
        if ch == '<':
            d_a += 1; tok.append(ch); continue
        if ch == '>':
            d_a -= 1; tok.append(ch); continue
        if ch.isspace() and d_b == 0 and d_c == 0 and d_a == 0:
            if tok:
                out.append("".join(tok)); tok = []
        else:
            tok.append(ch)
    if tok:
        out.append("".join(tok))
    return out

def _parse_list(block: str) -> List[str]:
    inner = block.strip()
    if not inner.startswith("[") or not inner.endswith("]"):
        raise DSLError("Lista inválida")
    inner = inner[1:-1].strip()
    if "," in inner:
        raise DSLError("No se permiten comas en listas")
    return [] if not inner else _split_top(inner)

def _find_top_level_equal(t: str) -> int:
    d_b = d_c = d_a = 0
    for i, ch in enumerate(t):
        if ch == '[': d_b += 1
        elif ch == ']': d_b -= 1
        elif ch == '{': d_c += 1
        elif ch == '}': d_c -= 1
        elif ch == '<': d_a += 1
        elif ch == '>': d_a -= 1
        elif ch == '=' and d_b == 0 and d_c == 0 and d_a == 0:
            return i
    return -1

def _parse_brace_dict(body: str) -> Dict[str, Any]:
    inner = _strip_balanced(body, "{", "}")
    toks = _split_top(inner)
    out: Dict[str, Any] = {}
    for t in toks:
        idx = _find_top_level_equal(t)
        if idx >= 0:
            k = t[:idx].strip()
            v = t[idx+1:].strip()
            if v.startswith("'") and v.endswith("'"): v = v[1:-1]
            if v.startswith('"') and v.endswith('"'): v = v[1:-1]
            if v.startswith("[") and v.endswith("]"):
                lst = _parse_list(v)
                out[k] = [(_to_number(x) if _num_pure_re.match(x) else x) for x in lst]
            else:
                out[k] = _to_number(v) if _num_pure_re.match(v) else v
        else:
            out[t.strip()] = True
    return out

# =================== Dataset declaration (Column A) ===================
#
# Grammar (Column A):
#   - "{{t=[id1 id2 ...]}}"
#   - "{{t=id}}"
#   - "{{id}}"  (shorthand, t is default parameter)
#   - "id"      (bare form, only when allow_bare=True)
#
# This parser returns a minimal dict[str, list[str]] where keys are canonical
# parameter names (e.g. "template_bbox"). It performs only syntactic parsing;
# semantic interpretation (e.g. resolving SVG elements) is handled elsewhere.

_DATASET_KEY_ALIASES = {
    "t": "template_bbox",
    "template_bbox": "template_bbox",
}

def _canon_dataset_key(k: str) -> Optional[str]:
    k = (k or "").strip()
    return _DATASET_KEY_ALIASES.get(k)

def parse_dataset_decl(cellA: str, *, allow_bare: bool = False) -> Optional[Dict[str, List[str]]]:
    """Parse dataset declaration from column A.

    Returns:
      - None if the cell does not declare a dataset.
      - dict with canonical keys and list values otherwise.

    Notes:
      - Only the dataset marker syntax is recognized here; callers must enforce
        that this is only used for column A.
      - Values are always returned as lists of strings.
    """
    if cellA is None:
        return None
    s = str(cellA).strip()
    if not s:
        return None

    inner = None
    if s.startswith("{{"):
        end = s.find("}}", 2)
        if end < 0:
            return None
        inner = s[2:end].strip()
        if not inner:
            return None
    else:
        if not allow_bare:
            return None
        inner = s

    toks = _split_top(inner)
    if not toks:
        return None

    out: Dict[str, List[str]] = {}

    # Shorthand: "{{id}}" or bare "id" → template_bbox=id
    if len(toks) == 1 and _find_top_level_equal(toks[0]) < 0:
        out["template_bbox"] = [toks[0].strip()]
        return out

    for t in toks:
        idx = _find_top_level_equal(t)
        if idx < 0:
            # For now, treat additional bare tokens as syntactic error.
            raise DSLError(f"Token inválido en dataset marker: '{t}'")

        k = t[:idx].strip()
        v = t[idx + 1 :].strip()
        ck = _canon_dataset_key(k)
        if not ck:
            # Unknown keys are allowed for forward compatibility.
            ck = k

        if v.startswith("[") and v.endswith("]"):
            items = _parse_list(v)
            out[ck] = [x.strip() for x in items if str(x).strip()]
        else:
            vv = v.strip()
            if vv.startswith("'") and vv.endswith("'"):
                vv = vv[1:-1]
            if vv.startswith('"') and vv.endswith('"'):
                vv = vv[1:-1]
            out[ck] = [vv] if vv else []

    # Default parameter: if template_bbox wasn't explicitly provided but a keyless
    # shorthand was used, it was already handled above.
    return out

# =================== SOURCE + public helpers ===================
@dataclass
class SourceSuffix:
    kind: str  # 'none'|'ops'|'fit'
    ops: Optional[str] = None
    fit: Optional[FitSpec] = None
    raw_fit_text: Optional[str] = None

def is_source_expr(s: str) -> bool:
    s = (s or "").strip()
    if s.startswith("@{") and "}" in s:
        return True
    return bool(re.match(r"^(?:Source|S)\s*\{[^}]*\}", s, re.I))

def _source_from_body(body: str) -> SourceRef:
    b = body.strip()

    if b.lower() in {"img", "pdf", "url", "file", "iconify", "svg"}:
        raise DSLError("Source requiere src")

    mlead = re.match(r"^(img|pdf|url|file|iconify|svg)\s+(.*)$", b, re.I)
    if mlead:
        kind = mlead.group(1).lower()
        rest = mlead.group(2).strip()
        if not rest:
            raise DSLError("Source requiere src")
        args = _parse_brace_dict("{"+rest+"}")
        src = str(args.get("src") or args.get("href") or args.get("url") or "")
        if not src:
            raise DSLError("Source requiere src")
        return SourceRef(kind, src, args)

    has_kv = "=" in b
    if has_kv:
        args = _parse_brace_dict("{"+b+"}")
        src = str(args.get("src") or args.get("href") or args.get("url") or "")
        if not src:
            raise DSLError("Source requiere src")
        s = src
        sl = s.lower()
        if re.match(r"^https?://", s, re.I):
            kind = "url"
        elif sl.endswith(".pdf"):
            kind = "pdf"
        elif sl.endswith(".svg") or sl.endswith(".svgz"):
            kind = "svg"
        elif re.search(r"\.(png|jpg|jpeg|gif|bmp|webp)$", sl):
            kind = "img"
        elif (":" in s) and ("/" not in s):
            kind = "iconify"
        else:
            kind = "file"
        return SourceRef(kind, src, args)
    else:
        s = b
        sl = s.lower()
        if re.match(r"^https?://", s, re.I):
            kind = "url"
        elif sl.endswith(".pdf"):
            kind = "pdf"
        elif sl.endswith(".svg") or sl.endswith(".svgz"):
            kind = "svg"
        elif re.search(r"\.(png|jpg|jpeg|gif|bmp|webp)$", sl):
            kind = "img"
        elif (":" in s) and ("/" not in s):
            kind = "iconify"
        else:
            kind = "file"
        return SourceRef(kind, s, {})

def normalize_ops_suffix(ops: str) -> str:
    ops = (ops or "").strip()
    if not ops:
        return ""
    mir = ""
    if ops.endswith("||"):
        mir = "||"; ops = ops[:-2]
    elif ops.endswith("|"):
        mir = "|"; ops = ops[:-1]
    rots: List[str] = []
    rem = ops
    rx = re.compile(r"\^(-?\d+(?:\.\d+)?)")
    while True:
        m = rx.search(rem)
        if not m: break
        rots.append(m.group(0))
        rem = rem[:m.start()] + rem[m.end():]
    fit_body = rem.strip()
    parts: List[str] = []
    if fit_body: parts.append(fit_body)
    parts.extend(rots)
    if mir: parts.append(mir)
    return "".join(parts)

def split_source_token(s: str) -> Tuple[SourceRef, SourceSuffix]:
    s = (s or "").strip()
    m = re.match(
        r'^\s*(?:@\{\s*(?P<body_a>[^}]*)\s*\}|(?:Source|S)\s*\{\s*(?P<body_b>[^}]*)\s*\})\s*(?:(?P<fit>\.Fit\s*\{.*\})|~(?P<ops>.*))?\s*$',
        s,
        re.I,
    )
    if not m:
        raise DSLError("No es un token de Source válido")
    body = (m.group("body_a") or m.group("body_b") or "").strip()
    src = _source_from_body(body)
    fit_txt = (m.group("fit") or "").strip()
    ops_txt = (m.group("ops") or "").strip()
    if fit_txt:
        fs = _parse_fit_long(f"dummy.Fit{fit_txt[fit_txt.find('{'):]}")
        return src, SourceSuffix(kind="fit", fit=fs, raw_fit_text=fit_txt[fit_txt.find('{'):])
    if ops_txt:
        return src, SourceSuffix(kind="ops", ops=normalize_ops_suffix(ops_txt))
    return src, SourceSuffix(kind="none")

# =================== FIT ===================
_FIT_MODES = {
    # i inside / contain
    'i':'i','inside':'i','contain':'i',
    # o / n original / none
    'o':'o','n':'o','original':'o','none':'o',
    # w width-fit
    'w':'w','width-fit':'w',
    # h height-fit
    'h':'h','height-fit':'h',
    # m max / cover
    'm':'m','max':'m','cover':'m',
    # x x-stretch
    'x':'x','x-stretch':'x',
    # y y-stretch
    'y':'y','y-stretch':'y',
    # a all-stretch
    'a':'a','all-stretch':'a',
    # t tile
    't':'t','tile':'t',
    # b best-fit
    'b':'b','best-fit':'b',
}


def _lex_fit_trail(trail: str) -> List[str]:
    s = trail.strip()
    out: List[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace():
            i += 1; continue
        if ch == '[':
            j = _bal_find(s, i, '[', ']')
            if j < 0: raise DSLError("Lista '[' sin cerrar")
            out.append(s[i:j+1]); i = j+1; continue
        if ch in '^|!':
            if ch == '^':
                m = re.match(r"^\^(-?\d+(?:\.\d+)?)", s[i:])
                if m:
                    out.append(m.group(0)); i += len(m.group(0)); continue
                out.append('^'); i += 1; continue
            if ch == '|':
                if i+1 < len(s) and s[i+1] == '|':
                    out.append('||'); i += 2; continue
                out.append('|'); i += 1; continue
            if ch == '!':
                if i+1 < len(s) and s[i+1] == '!':
                    out.append('!!'); i += 2; continue
                out.append('!'); i += 1; continue
        m = re.match(r"^[^\s\[\]\^|!]+", s[i:])
        if not m: raise DSLError("Token inválido en fit")
        out.append(m.group(0)); i += len(m.group(0))
    return out

def _fit_from_dict(args: Dict[str, Any]) -> FitSpec:
    fs = FitSpec()
    mv = str(args.get("mode","")).lower().strip() if "mode" in args else None
    if mv and mv in _FIT_MODES:
        fs.mode = _FIT_MODES[mv]
    for k in ("i","m","w","h","x","y","a","t","o"):
        if args.get(k) is True:
            fs.mode = _FIT_MODES[k]
    if "anchor" in args:
        fs.anchor = _try_int(args.get("anchor"))
    if "a" in args and fs.anchor is None:
        fs.anchor = _try_int(args.get("a"))
    if "border" in args:
        b = _as_list(args.get("border"))
        fs.border = [_num_to_str_trim(x) for x in b]
    if "b" in args and fs.border is None:
        b = _as_list(args.get("b"))
        fs.border = [_num_to_str_trim(x) for x in b]
    # translate (formerly shift): translate/t (new) + compat shift/s (legacy).
    if "translate" in args:
        vals = _as_list(args.get("translate"))
        if len(vals) != 2: raise DSLError("translate requiere [dx dy]")
        fs.shift = [_to_number(vals[0]), _to_number(vals[1])]
    # 't' is ambiguous: if True => 'tile' mode; if list/value => translate
    if "t" in args and fs.shift is None and args.get("t") is not True:
        vals = _as_list(args.get("t"))
        if len(vals) != 2: raise DSLError("translate requiere [dx dy]")
        fs.shift = [_to_number(vals[0]), _to_number(vals[1])]
    # legacy
    if "shift" in args and fs.shift is None:
        vals = _as_list(args.get("shift"))
        if len(vals) != 2: raise DSLError("shift requiere [dx dy]")
        fs.shift = [_to_number(vals[0]), _to_number(vals[1])]
    if "s" in args and fs.shift is None:
        vals = _as_list(args.get("s"))
        if len(vals) != 2: raise DSLError("shift requiere [dx dy]")
        fs.shift = [_to_number(vals[0]), _to_number(vals[1])]
    if "rotate" in args:
        fs.rotate = _try_float(args.get("rotate"))
    if "r" in args and fs.rotate is None:
        fs.rotate = _try_float(args.get("r"))
    if "mirror" in args:
        mv = str(args["mirror"]).lower()
        if mv in ("h","v","none"): fs.mirror = mv
    if "clip" in args or args.get("c") is True:
        v = args.get("clip", True)
        fs.clip = True
        fs.clip_stage = "pre" if isinstance(v, str) else "post"
    if args.get("clip_pre") is True:
        fs.clip = True; fs.clip_stage = "pre"
    return fs

def _parse_fit_long(cmd: str) -> FitSpec:
    m = re.match(r"^\s*(?:[A-Za-z][\w\-.]*\s*)?\.Fit\s*(\{.*\})\s*$", cmd)
    if not m:
        raise DSLError("Fit largo inválido")
    args = _parse_brace_dict(m.group(1))
    return _fit_from_dict(args)

def _parse_fit_shorthand(trail: str) -> FitSpec:
    toks = _lex_fit_trail(trail)
    # Allow compact clip notation glued to other tokens, e.g. "o7!" or "!o7".
    # Keep the lexer simple: just explode leading/trailing '!'/'!!' here.
    if toks:
        exp: List[str] = []
        for tok0 in toks:
            tok = str(tok0)
            # leading bangs
            while tok.startswith('!!') and tok not in ('!!',):
                exp.append('!!'); tok = tok[2:]
            while tok.startswith('!') and tok not in ('!', '!!'):
                exp.append('!'); tok = tok[1:]

            # trailing bangs
            trail_b: List[str] = []
            while tok.endswith('!!') and tok not in ('!!',):
                trail_b.insert(0, '!!'); tok = tok[:-2]
            while tok.endswith('!') and tok not in ('!', '!!'):
                trail_b.insert(0, '!'); tok = tok[:-1]

            if tok:
                exp.append(tok)
            exp.extend(trail_b)
        toks = exp
    fs = FitSpec()
    i = 0
    saw_shift = False
    while i < len(toks):
        t = toks[i]

        # leading rotations without mode
        if fs.mode is None and t.startswith("^"):
            m_mix = re.match(r"^\^(-?\d+(?:\.\d+)?)([imwhyxaotnbo])$", t, re.I)
            if m_mix:
                deg = float(m_mix.group(1)); mchar = m_mix.group(2).lower()
                fs.mode = _FIT_MODES.get(mchar, "n")
                fs.rotate = (fs.rotate or 0.0) + deg
                i += 1; continue
            if t == "^^":
                fs.mode = fs.mode or "i"
                fs.rotate = (fs.rotate or 0.0) + 180.0
                i += 1; continue
            if t == "^":
                fs.mode = fs.mode or "i"
                fs.rotate = (fs.rotate or 0.0) + 90.0
                i += 1; continue
            m_deg = re.match(r"^\^(-?\d+(?:\.\d+)?)$", t)
            if m_deg:
                fs.mode = fs.mode or "i"
                fs.rotate = (fs.rotate or 0.0) + float(m_deg.group(1))
                i += 1; continue

        if t in ('t','s'):
            if i+1 >= len(toks) or not (toks[i+1].startswith('[') and toks[i+1].endswith(']')):
                raise DSLError("shift requiere [dx dy]")
            lst = _parse_list(toks[i+1])
            if len(lst) != 2:
                raise DSLError("shift requiere [dx dy]")
            dx, dy = lst[0], lst[1]
            dxv = float(dx) if _num_pure_re.match(dx) else dx
            dyv = float(dy) if _num_pure_re.match(dy) else dy
            fs.shift = [dxv, dyv]; saw_shift = True
            i += 2; continue

        if t.startswith('[') and t.endswith(']'):
            lst = _parse_list(t)
            if fs.border is None:
                fs.border = [_num_to_str_trim(x) for x in lst]
            else:
                if len(lst) != 2: raise DSLError("shift requiere [dx dy]")
                dx, dy = lst[0], lst[1]
                dxv = float(dx) if _num_pure_re.match(dx) else dx
                dyv = float(dy) if _num_pure_re.match(dy) else dy
                fs.shift = [dxv, dyv]; saw_shift = True
            i += 1; continue

        if t in ('|', '||'):
            fs.mirror = 'h' if t == '|' else 'v'
            i += 1; continue

        if t == 'c':
            fs.clip = True; fs.clip_stage = "post"
            i += 1; continue

        if t.startswith('^'):
            if t == '^':
                fs.rotate = (fs.rotate or 0.0) + 90.0
            else:
                fs.rotate = (fs.rotate or 0.0) + float(t[1:])
            i += 1; continue

        if t in ('!', '!!'):
            fs.clip = True
            fs.clip_stage = "pre" if not saw_shift else "post"
            i += 1; continue

        low = t.lower()
        m = re.match(r"^([imwhyxaotnbo])([1-9])$", low)
        if m:
            fs.mode = _FIT_MODES[m.group(1)]
            fs.anchor = int(m.group(2))
            i += 1; continue

        if low in _FIT_MODES:
            fs.mode = _FIT_MODES[low]
            i += 1; continue

        m_anchor_only = re.match(r"^[1-9]$", t)
        if m_anchor_only:
            if fs.mode is None: fs.mode = 'i'
            fs.anchor = int(t)
            i += 1; continue

        raise DSLError(f"Token desconocido en fit: {t}")
    return fs

def ops_from_fit_spec(fs: FitSpec) -> str:
    if fs is None:
        return "~{ i5 }"
    parts: List[str] = []
    if fs.border:
        parts.append(f"[{' '.join(fs.border)}]")
    mode = (fs.mode or 'i')
    if fs.anchor is not None:
        parts.append(f"{mode}{int(fs.anchor)}")
    else:
        parts.append(mode)
    pre = fs.clip and (str(fs.clip_stage).lower() == 'pre')
    post = fs.clip and not pre
    body: List[str] = []
    if pre: body.append('!')
    body.extend(parts)
    if fs.shift and len(fs.shift) >= 2:
        body.append(f"[{fs.shift[0]} {fs.shift[1]}]")
    if post: body.append('!')
    inblock = " ".join(body) if body else "n"
    suffix = ""
    if fs.rotate not in (None, 0, 0.0):
        suffix += f"^{fs.rotate}"
    if fs.mirror in ('h', 'v'):
        suffix += "|" if fs.mirror == 'h' else "||"
    if fs.mode in (None,):
        fs.mode = 'i'
    return f"~{{ {inblock} }}{suffix}"

def fit_spec_from_ops(ops: str) -> FitSpec:
    s = (ops or "").strip()
    if s.startswith("~"):
        s = s[1:].strip()

    if s.startswith("{"):
        j = _bal_find(s, 0, "{", "}")
        if j < 0:
            raise DSLError("Bloque '{' sin cerrar")
        body = s[:j+1]
        tail = s[j+1:].strip()

        inner = _strip_balanced(body, "{", "}")
        btoks = _split_top(inner)

        fs = FitSpec()
        saw_shift = False
        for t in btoks:
            if t == '!':
                fs.clip = True
                fs.clip_stage = "pre" if not saw_shift else "post"
                continue
            if t.startswith('[') and t.endswith(']'):
                lst = _parse_list(t)
                if fs.border is None:
                    fs.border = [_num_to_str_trim(x) for x in lst]
                else:
                    if len(lst) != 2:
                        raise DSLError("shift requiere [dx dy]")
                    dx, dy = lst[0], lst[1]
                    dxv = float(dx) if _num_pure_re.match(dx) else dx
                    dyv = float(dy) if _num_pure_re.match(dy) else dy
                    fs.shift = [dxv, dyv]
                    saw_shift = True
                continue
            low = t.lower()
            m = re.match(r"^([imwhyxaotnbo])([1-9])$", low)
            if m:
                fs.mode = _FIT_MODES[m.group(1)]
                fs.anchor = int(m.group(2))
                continue
            if low in _FIT_MODES:
                fs.mode = _FIT_MODES[low]
                continue

        if fs.mode in (None,):
            fs.mode = 'i'

        mrot = re.search(r"\^(-?\d+(?:\.\d+)?)", tail)
        if mrot:
            fs.rotate = float(mrot.group(1))
        if tail.endswith("||"):
            fs.mirror = 'v'
        elif tail.endswith("|"):
            fs.mirror = 'h'
        return fs

    return _parse_fit_shorthand(s)

# =================== LAYOUT v2 ===================

def measure_to_mm(token, base_mm=None, default_unit="mm"):
    """
    Convert a measurement token to millimeters.

    Soporta:
      - plain numbers:        5, "5", "2.5"
      - con unidad:           "5mm", "2.5cm", "1in"
      - porcentajes:          "10%" → 10% de base_mm

    Si no puede interpretar el valor, devuelve 0.0 mm y avisa por log.
    """
    if token is None:
        return 0.0

    s = str(token).strip()
    if not s:
        return 0.0

    # Porcentaje: "10%"
    if s.endswith("%"):
        num = s[:-1].strip()
        try:
            p = float(num)
        except (TypeError, ValueError):
            _l.w(f"[measure_to_mm] invalid percentage: '{s}'")
            return 0.0
        if base_mm is None:
            _l.w(f"[measure_to_mm] porcentaje '{s}' sin base_mm; devolviendo 0")
            return 0.0
        return base_mm * (p / 100.0)

    # "numero + unidad": "5mm", "2.5cm", "1in"
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)([a-zA-Z]*)$", s)
    if m:
        try:
            val = float(m.group(1))
        except (TypeError, ValueError):
            _l.w(f"[measure_to_mm] invalid numeric value: '{s}'")
            return 0.0
        unit = (m.group(2) or "").lower()

        if unit in ("", default_unit.lower(), "mm"):
            return val
        if unit == "cm":
            return val * 10.0
        if unit in ("in", "inch", "inches"):
            return val * 25.4

        _l.w(f"[measure_to_mm] unidad desconocida '{unit}' en '{s}'; asumiendo mm")
        return val

    # Último intento: float simple
    try:
        return float(s)
    except (TypeError, ValueError):
        _l.w(f"[measure_to_mm] could not parse '{s}'; using 0 mm")
        return 0.0

def _parse_gaps_v2(val: Union[str, List[Any]]) -> List[Union[float, str]]:
    """
    "Dumb" gaps parser.

    Acepta:
      k=[a], k=[a b], k=a

    Returns a list of 0..2 tokens (floats for pure numbers; strings when they include units/%).
    It does not interpret units, % or expand (1→2, etc). That is layouts.py's responsibility.
    """
    if val is None:
        return []
    if isinstance(val, list):
        seq = val
    else:
        s = str(val).strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            seq = _parse_list(s)
        else:
            seq = [s]

    out: List[Union[float, str]] = []
    for x in seq:
        if x is None:
            continue
        # Preserve numeric types whenever possible (tests expect numbers).
        if isinstance(x, (int, float)):
            out.append(float(x))
            continue
        t = str(x).strip()
        if t == "":
            continue
        if _num_pure_re.match(t):
            out.append(float(t))
        else:
            out.append(t)

    if len(out) > 2:
        raise DSLError("gaps admite como máximo 2 valores [x y]")

    return out

def _parse_offset_v2(val: Union[str, List[Any]]) -> List[Union[float, str]]:
    """
    "Dumb" offset parser.

    Acepta:
      o=[a b c d], o=a

    Returns a list of 0..4 tokens (floats for pure numbers; strings when they include units/%).
    It does not interpret units, % or expand. That is layouts.py's responsibility.
    """
    if val is None:
        return []
    if isinstance(val, list):
        seq = val
    else:
        s = str(val).strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            seq = _parse_list(s)
        else:
            seq = [s]

    out: List[Union[float, str]] = []
    for x in seq:
        if x is None:
            continue
        if isinstance(x, (int, float)):
            out.append(float(x))
            continue
        t = str(x).strip()
        if t == "":
            continue
        if _num_pure_re.match(t):
            out.append(float(t))
        else:
            out.append(t)

    if len(out) > 4:
        raise DSLError("offset admite como máximo 4 valores [w1 h1 w2 h2]")

    return out

_grid_re = re.compile(r"^(?P<c>-?\d+)(?P<mir>\|)?x(?P<r>-?\d+)(?P<mir2>\|)?(?P<v>\^)?$")

def _parse_grid_token_and_inline(value: str) -> Tuple[GridSpec, Optional[str]]:
    val = (value or "").strip()
    inline = None
    if "<" in val and val.endswith(">"):
        i = val.find("<")
        inline = val[i:]
        val = val[:i].strip()
    m = _grid_re.match(val)
    if not m:
        raise DSLError("g requiere formato colsxrows opcionalmente con '|' y/o '^'")
    cols = int(m.group("c"))
    rows = int(m.group("r"))
    flip_h = bool(m.group("mir")) or (cols < 0)
    flip_v = bool(m.group("mir2")) or (rows < 0)
    if flip_h and flip_v:
        flip = "hv"
    elif flip_h:
        flip = "h"
    elif flip_v:
        flip = "v"
    else:
        flip = None
    order = "tb-lr" if m.group("v") else "lr-tb"
    cols = abs(cols)
    rows = abs(rows)
    return GridSpec(cols=cols, rows=rows, order=order, flip=flip, gaps=None), inline

def _parse_inline_props(inline: str) -> Dict[str, Any]:
    inner = _strip_balanced(inline, "<", ">")
    toks = _split_top(inner)
    out: Dict[str, Any] = {}
    for t in toks:
        idx = _find_top_level_equal(t)
        if idx < 0:
            out[t.strip()] = True
            continue
        k = t[:idx].strip()
        v = t[idx+1:].strip()
        out[k] = v
    return out

_shape_size_re = re.compile(r"^\s*\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?(?:[a-z%]+)?\s*$", re.I)

def _parse_shape_v2(val: str) -> ShapeSpec:
    v = (val or "").strip()
    # size like "55x77" (with or without units)
    if _shape_size_re.match(v):
        return ShapeSpec(kind="rect", args=[v])
    # rect<...> / hex<...> / polygon<[...]>
    if v.startswith("rect<"):
        return ShapeSpec(kind="rect", args=[v[v.find('<'):]])
    if v.startswith("hex<"):
        return ShapeSpec(kind="hex", args=[v[v.find('<'):]])
    if v.startswith("polygon<"):
        return ShapeSpec(kind="polygon", args=[v[v.find('<'):]])
    # preset
    return ShapeSpec(kind="preset", preset=v)

def _parse_layout_v2(layout_cmd: str) -> LayoutSpec:
    m = re.match(r"^\s*(?:[A-Za-z][\w\-.]*\s*)?\.(?:Layout|L)\s*(\{.*\})\s*$", layout_cmd)
    if not m:
        raise DSLError("Layout inválido")
    # Layout braces accept:
    #   - key=value tokens (g=3x3 k=[...] s=poker ...)
    inner = _strip_balanced(m.group(1), "{", "}").strip()
    toks = _split_top(inner) if inner else []
    args: Dict[str, Any] = {}
    pos: List[str] = []
    for t in toks:
        idx = _find_top_level_equal(t)
        if idx >= 0:
            k = t[:idx].strip()
            v = t[idx+1:].strip()
            if v.startswith("'") and v.endswith("'"): v = v[1:-1]
            if v.startswith('"') and v.endswith('"'): v = v[1:-1]
            if v.startswith("[") and v.endswith("]"):
                lst = _parse_list(v)
                args[k] = [(_to_number(x) if _num_pure_re.match(x) else x) for x in lst]
            else:
                args[k] = _to_number(v) if _num_pure_re.match(v) else v
        else:
            pos.append(t.strip())

    ls = LayoutSpec()

    # --- BREAKING CHANGE: grid/g as pattern has been removed (use pattern/p) ---
    if "grid" in args:
        raise DSLError("Layout: 'grid' ha sido reemplazado por 'pattern' (p=...)")
    if "g" in args and isinstance(args.get("g"), str) and re.search(r"[xX]|\|", args.get("g")):
        raise DSLError("Layout: 'g' ya no es patrón; usa 'p='/'pattern=' para el patrón y 'g='/'gaps=' para gaps")

    # ---- positional tokens ----
    # Layout keeps a default parameter: pattern.
    # It supports positional tokens like "3x4" or "0x0" WITHOUT "p=".
    # Other positional tokens (non-pattern) are treated as boolean flags.
    pval = args.get("p", None)
    if pval is None:
        pval = args.get("pattern", None)

    if pval is None and pos:
        for t in pos:
            tt = (t or "").strip()
            if isinstance(tt, str) and re.search(r"[xX]|\|", tt) and re.search(r"\d|\?", tt):
                pval = tt
                break

    # legacy boolean flags (excluding the positional pattern token if present)
    for t in pos:
        tt = (t or "").strip()
        if not tt:
            continue
        if pval is not None and tt == pval:
            continue
        args[tt] = True

    # pattern: ONLY p/pattern (and positional). All compat with grid/g is removed.
    if pval is not None:
        if not isinstance(pval, str):
            pval = str(pval)
        grid, inline = _parse_grid_token_and_inline(pval)
        if inline:
            ip = _parse_inline_props(inline)
            # gaps can also come inline
            if "g" in ip or "gaps" in ip:
                kval = ip.get("g", ip.get("gaps"))
                grid.gaps = _parse_gaps_v2(kval)
            if "o" in ip or "offset" in ip:
                oval = ip.get("o", ip.get("offset"))
                grid.offset = _parse_offset_v2(oval)
        ls.grid = grid

    # top-level gaps: gaps/g
    gaps_val = args.get("gaps", None)
    if gaps_val is None:
        gaps_val = args.get("g", None)

    if gaps_val is not None:
        if ls.grid is None:
            ls.grid = GridSpec(cols=0, rows=0, order="lr-tb", flip=None, gaps=None)
        ls.grid.gaps = _parse_gaps_v2(gaps_val)
    # top-level offset/o
    offset_val = args.get("offset", None)
    if offset_val is None:
        offset_val = args.get("o", None)
    if offset_val is not None:
        if ls.grid is None:
            ls.grid = GridSpec(cols=0, rows=0, order="lr-tb", flip=None, gaps=None)
        ls.grid.offset = _parse_offset_v2(offset_val)
    # shape
    sval = args.get("s", None)
    if sval is None:
        sval = args.get("shape", None)
    if sval is not None:
        if not isinstance(sval, str):
            sval = str(sval)
        ls.shape = _parse_shape_v2(sval)
    # extract (spritesheet)
    if args.get("extract") is True:
        ls.extract = True
    return ls

# =================== PAGE v2 ===================

def _parse_page_v2_from_brace(body: str) -> PageSpec:
    inner = _strip_balanced(body, "{", "}")
    inner = inner.strip()
    if inner == "":
        return PageSpec(pagebreak_only=True)
    toks = _split_top(inner)
    ps = PageSpec()

    def _consume_size_token(tok: str):
        """Parse size token like 'A4', 'A4^', possibly combined with '@expr'."""
        tok = (tok or '').strip()
        if not tok:
            return
        if "@" in tok and not tok.startswith("@"):  # e.g. A4^@+3
            left, right = tok.split("@", 1)
            left = left.strip(); right = right.strip()
            if right:
                ps.at = right
            tok = left
        if not tok:
            return
        if tok.endswith("^"):
            ps.size = tok[:-1]
            ps.landscape = True
        else:
            ps.size = tok

    # multiplier like "3*A4" or just "3"
    for t in toks:
        t = (t or '').strip()
        if not t:
            continue

        # standalone cursor token: @+3 / @5
        if t.startswith("@"):
            ps.at = t[1:].strip()
            continue

        # multiplier with body: "3*A4^" or "3*A4^@+3"
        if "*" in t and not t.startswith("b=") and not t.startswith("border="):
            parts = t.split("*", 1)
            if parts[0].isdigit():
                ps.multiplier = int(parts[0])
                rest = parts[1].strip()
                if rest:
                    _consume_size_token(rest)
                continue

        idx = _find_top_level_equal(t)
        if idx >= 0:
            k = t[:idx].strip()
            v = t[idx+1:].strip()
            if v.startswith("'") and v.endswith("'"):
                v = v[1:-1]
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]

            if k in ("b", "border"):
                # border accepts either a list (b=[...]) or a scalar (b=-10% / b=-10).
                # Keep tokens as raw strings so downstream (layouts) can interpret units/%.
                if v.startswith("[") and v.endswith("]"):
                    ps.border = _parse_list(v)
                else:
                    ps.border = [v]
                continue
            if k in ("pagesize", "size"):
                _consume_size_token(v)
                continue
            if k == "landscape":
                if isinstance(v, str):
                    vv = v.strip().lower()
                    if vv in ("1", "true", "yes", "y", "on"):
                        ps.landscape = True
                    elif vv in ("0", "false", "no", "n", "off"):
                        ps.landscape = False
                    else:
                        ps.landscape = bool(vv)
                else:
                    ps.landscape = bool(v)
                continue
            if k == "portrait":
                ps.landscape = False
                continue
            if k in ("at", "a"):
                ps.at = str(v).strip()
                continue
            # Unknown key: ignore (Page{} is intentionally strict on meaning)
            continue

        if t.isdigit():
            ps.multiplier = int(t)
            continue

        if t.lower() in ("landscape", "ls"):
            ps.landscape = True
            continue
        if t.lower() in ("portrait", "pt"):
            ps.landscape = False
            continue

        # plain size token (A4, A4^, Letter, ...), optionally combined with @expr
        if re.match(r"^[A-Za-z0-9]+(\^)?(@.*)?$", t):
            _consume_size_token(t)
            continue

        raise DSLError(f"Token no reconocido en Page{{}}: {t}")

    return ps

# =================== Alias access y define ===================
def _parse_alias_access(s: str) -> AliasRef:
    s = s.strip()
    m = re.match(r"^@(?P<name>[A-Za-z][\w\-\.]*)(?P<idx>(?:\[[^\]]+\])*)$", s)
    if not m:
        raise DSLError("Alias inválido")
    name = m.group("name")
    idxs_raw = m.group("idx")
    indices: List[Union[int, RangeIdx, ListIdx, StarIdx]] = []
    if idxs_raw:
        inner = idxs_raw[1:-1] if idxs_raw else ""
        chunks = inner.split("][") if inner else []
        for ch in chunks:
            tok = ch.strip()
            if tok == "*":
                indices.append(StarIdx()); continue
            mr = re.match(r"^(?P<a>\d+)\s*\.\.\s*(?P<b>\d+)$", tok)
            if mr:
                indices.append(RangeIdx(int(mr.group("a")), int(mr.group("b")))); continue
            if re.match(r"^\d+$", tok):
                indices.append(int(tok)); continue
            if " " in tok:
                items = [int(x) for x in tok.split()]
                indices.append(ListIdx(items)); continue
            raise DSLError(f"Índice no reconocido: {tok}")
    return AliasRef(name=name, indices=indices)

# =================== Parser principal ===================
def parse(s: str) -> Command:
    s = (s or "").strip()

    # Alias define
    m_def = re.match(r"^@(?P<alias>[A-Za-z][\w\-\.]*)\s*=\s*(?P<rhs>.+)$", s)
    if m_def:
        alias = m_def.group("alias")
        rhs = m_def.group("rhs").strip()
        cmd: Optional[Command] = None
        try:
            cmd = parse(rhs)
        except DSLError:
            cmd = None
        if cmd is None:
            try:
                ch = parse_chain(rhs)
                mod = next((m for m in ch.modules if m.name.lower() in ("layout","l") and isinstance(m.spec, LayoutSpec)), None)
                if mod:
                    cmd = Command("Layout", layout=mod.spec)
            except DSLError:
                cmd = None
        if cmd is None:
            raise DSLError("RHS inválido en alias")
        return Command(name="AliasDefine", args={"alias": alias, "value": cmd})

    # Page v2: Page{...} / P{...} o {...}
    m_page = re.match(r"^\s*(?:P|Page)?\s*(\{.*\})\s*$", s)
    if m_page:
        ps = _parse_page_v2_from_brace(m_page.group(1))
        return Command("Page", args={"page": ps})

    # Fit largo
    if ".Fit" in s and "{" in s:
        fs = _parse_fit_long(s)
        m = re.match(r"^\s*(?P<id>[A-Za-z][\w\-.]*)\s*\.Fit", s)
        target = IdRef(m.group("id")) if m else IdRef("dummy")
        return Command("Fit", target=target, fit=fs)

    # Fit shorthand "~"
    if "~" in s and "{" not in s:
        m = re.match(r"^\s*(?P<id>[A-Za-z][\w\-.]*)\s*~\s*(?P<trail>.+?)\s*$", s)
        if not m:
            raise DSLError("Fit shorthand inválido")
        fs = _parse_fit_shorthand(m.group("trail"))
        return Command("Fit", target=IdRef(m.group("id")), fit=fs)

    # Layout v2
    if ".Layout" in s or ".L" in s:
        m2 = re.search(r"\.(?:Layout|L)\s*(\{.*\})\s*$", s)
        if not m2:
            raise DSLError("Layout inválido")
        ls = _parse_layout_v2(s[s.find("."):])
        return Command("Layout", layout=ls)

    # Source
    if is_source_expr(s):
        src, _ = split_source_token(s)
        return Command("Source", target=src)

    # Alias ref
    if s.startswith("@") and "{" not in s:
        return Command("AliasRef", target=_parse_alias_access(s))

    raise DSLError("No reconozco la instrucción")

def maybe_parse(s: str) -> Optional[Command]:
    try:
        return parse((s or "").strip())
    except DSLError:
        return None

# =================== Lexer/Parser de cadenas ===================
@dataclass
class Token:
    kind: str
    value: str
    start: int
    end: int

def tokenize_chain(s: str) -> List[Token]:
    s = (s or "").strip()
    out: List[Token] = []
    i = 0
    N = len(s)
    while i < N:
        ch = s[i]
        if ch.isspace():
            i += 1; continue
        if ch == '[':
            out.append(Token('group_open', '[', i, i+1)); i += 1; continue
        if ch == ']':
            out.append(Token('group_close', ']', i, i+1)); i += 1; continue
        if ch == '.':
            out.append(Token('dot', '.', i, i+1)); i += 1; continue
        if ch == '~':
            out.append(Token('tilde', '~', i, i+1)); i += 1; continue
        if ch == '@' and i+1 < N and s[i+1] == '{':
            j = _bal_find(s, i+1, '{', '}')
            if j < 0: raise DSLError("Source @{...} sin cerrar")
            out.append(Token('source', s[i:j+1], i, j+1)); i = j+1; continue
        m = re.match(r"^[A-Za-z][\w\-.]*", s[i:])
        if m:
            seg = m.group(0)
            out.append(Token('id', seg, i, i+len(seg))); i += len(seg); continue
        if ch == '{':
            j = _bal_find(s, i, '{', '}')
            if j < 0: raise DSLError("Bloque '{' sin cerrar")
            out.append(Token('brace', s[i:j+1], i, j+1)); i = j+1; continue
        out.append(Token('text', ch, i, i+1)); i += 1
    out.append(Token('eof', '', N, N))
    return out

def _parse_target(tokens: List[Token], pos: int) -> Tuple[Optional[Union[IdRef, SourceRef, GroupRef]], int]:
    if tokens[pos].kind == 'id':
        return IdRef(tokens[pos].value), pos+1
    if tokens[pos].kind == 'source':
        src, _ = split_source_token(tokens[pos].value)
        return src, pos+1
    if tokens[pos].kind == 'group_open':
        items: List[Union[IdRef, SourceRef]] = []
        p = pos+1
        while p < len(tokens) and tokens[p].kind != 'group_close':
            if tokens[p].kind == 'id':
                items.append(IdRef(tokens[p].value)); p += 1; continue
            if tokens[p].kind == 'source':
                src, _ = split_source_token(tokens[p].value)
                items.append(src); p += 1; continue
            raise DSLError("Token no válido dentro de grupo []")
        if p >= len(tokens) or tokens[p].kind != 'group_close':
            raise DSLError("Grupo '[' sin cerrar")
        return GroupRef(items), p+1
    return None, pos

def _parse_suffixes(tokens: List[Token], pos: int) -> Tuple[List[ModuleCall], Optional[str], int]:
    modules: List[ModuleCall] = []
    legacy_ops: Optional[str] = None
    p = pos
    while p < len(tokens):
        t = tokens[p]
        if t.kind == 'dot':
            if tokens[p+1].kind != 'id':
                raise DSLError("Se esperaba nombre de módulo tras '.'")
            mod_name = tokens[p+1].value
            if tokens[p+2].kind != 'brace':
                raise DSLError(f"Se esperaba '{{}}' en módulo .{mod_name}")
            body = tokens[p+2].value
            args = _parse_brace_dict(body)
            mc = ModuleCall(mod_name, args, spec=None)
            lname = mod_name.lower()
            if lname == 'fit':
                mc.spec = _fit_from_dict(args)
            elif lname in ('layout','l'):
                mc.spec = _parse_layout_v2(f"dummy.{mod_name}{body}")
            modules.append(mc)
            p += 3; continue
        if t.kind == 'tilde':
            rest = []; q = p+1
            while q < len(tokens) and tokens[q].kind != 'eof':
                rest.append(tokens[q].value); q += 1
            legacy_ops = normalize_ops_suffix("".join(rest).strip())
            p = q; break
        if t.kind in ('eof', 'group_close'):
            break
        break
    return modules, legacy_ops, p

def parse_chain(s: str) -> Chain:
    tokens = tokenize_chain(s)
    p = 0
    target, p = _parse_target(tokens, p)
    modules, legacy_ops, p = _parse_suffixes(tokens, p)
    if target is None and not modules and not legacy_ops:
        raise DSLError("Expresión vacía")
    return Chain(target, modules, legacy_ops)

def maybe_parse_chain(s: str) -> Optional[Chain]:
    try:
        return parse_chain((s or "").strip())
    except DSLError:
        return None

# =================== Leading-cell parser for DeckMaker ===================

def parse_copies_page_tail(cell0):
    """
    Return (copies, page_block, layout_block, marks_block)
    and expose holes in parse_copies_page_tail.__holes__ (1-based list).

    Supports:
      - {A4 b=[...]} / {A3^} / {} / {3} / {3*A4}
      - .L{...}  (or L{...} tail)
      - [10 3- 5 2- 5] at the very end
      - trailing number at the very end (outside [] and {})
    """
    s = str(cell0 or "")
    copies = 1
    copies_explicit = False
    page_block = None
    layout_block = None
    marks_block = None
    rest = s

    # { ... } block (page / breaks)
    m_page = re.search(r"\{.*?\}", rest)
    if m_page:
        page_block = m_page.group(0)
        rest = rest[:m_page.start()] + rest[m_page.end():]

    # sequence [N H- N H- ...] at the end:
    #   - plain number "N" adds cards
    #   - "H-" adds H empty slots after the current accumulated copy
    #   - "-" (or "---") adds 1 (or many) empty slots
    holes: List[int] = []
    # IMPORTANT: capture only the *final* flat bracket block, so we do not
    # accidentally consume bracket lists from Layout/Page tails (e.g. g=[...], o=[...]).
    m_seq = re.search(r"\[([^\[\]]*)\]\s*$", rest.strip())
    if m_seq:
        toks = [t for t in re.split(r"[\s,]+", m_seq.group(1).strip()) if t]
        run = 0
        for t in toks:
            if re.fullmatch(r"-+", t):
                if run > 0:
                    for _ in range(len(t)):
                        holes.append(run)  # empty slot(s) AFTER copy 'run'
                continue
            m_h = re.fullmatch(r"(\d+)-", t)
            if m_h:
                if run > 0:
                    n_holes = int(m_h.group(1))
                    for _ in range(max(0, n_holes)):
                        holes.append(run)  # empty slot(s) AFTER copy 'run'
                continue
            if t.isdigit():
                run += int(t)
        if run > 0:
            copies = run
            copies_explicit = True
        rest = rest[:m_seq.start()]
    # trailing number as copies (ignoring numbers inside [] and {})
    if copies == 1:
        rest_no_braces = re.sub(r"\{[^}]*\}", "", rest)
        rest_no_brackets = re.sub(r"\[[^\]]*\]", "", rest_no_braces)
        m_num = re.search(r"(?:^|\s)(\d+)\s*$", rest_no_brackets.strip())
        if m_num:
            copies = max(0, int(m_num.group(1)))
            copies_explicit = True
            # remove that trailing number so it does not block the L{...} tail
            rest = re.sub(r"(?:^|\s)\d+\s*$", "", rest)

    # tail M{ ... } — admite ".M{...}" o "M{...}" al final
    # IMPORTANT: parse M first to allow "... .L{...}.M{...}".
    m_m = re.search(r"(?:^|[\.])M\s*(\{.*\})\s*$", rest.strip())
    if m_m:
        marks_block = "M" + m_m.group(1)
        rest = rest[:m_m.start()] + rest[m_m.end():]

    # L{...} tail — accepts ".L{...}" or "L{...}" at the end (after removing copies)
    m_tail = re.search(r"(?:^|[\.])L\s*(\{.*\})\s*$", rest.strip())
    if m_tail:
        layout_block = "L" + m_tail.group(1)
        rest = rest[:m_tail.start()] + rest[m_tail.end():]

    parse_copies_page_tail.__holes__ = holes
    parse_copies_page_tail.__copies_explicit__ = bool(copies_explicit)
    return copies, page_block, layout_block, marks_block

# === PnPInk Phase 3 – public DSL helpers (no semantic changes) ===
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class DLeadingCell:
    copies: int
    holes: List[int]
    copies_explicit: bool = False
    page_block: Optional[str] = None   # texto "{A3 ...}" o None
    layout_block: Optional[str] = None # texto "L{ ... }" o "{ ... }" o None
    marks_block: Optional[str] = None  # texto "M{ ... }" o None
    page: Optional["PageSpec"] = None
    layout: Optional["LayoutSpec"] = None
    marks: Optional["MarksSpec"] = None

def parse_page_block(body: str) -> "PageSpec":
    s = (body or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise DSLError("parse_page_block espera un bloque '{...}'")
    return _parse_page_v2_from_brace(s)

def parse_layout_block(text: str) -> "LayoutSpec":
    s = (text or "").strip()
    if not s:
        raise DSLError("layout tail vacío")
    if s.startswith("L"):
        s = s[1:].lstrip()
    if not (s.startswith("{") and s.endswith("}")):
        raise DSLError("layout tail inválido; se esperaba bloque {...}")
    return _parse_layout_v2(f"dummy.L{s}")


def parse_marks_block(text: str) -> "MarksSpec":
    """Parse a Marks/M tail block.

    Expected formats:
      - "M{ ... }"
      - "{ ... }"  (internal use)

    Notes:
      - Default parameter is the style id: ".M{ mk_style d=0 }" => s=mk_style
      - b/d use the same list grammar as border in Page/Fit (no new ad-hoc parsing).
    """
    s = (text or "").strip()
    if not s:
        raise DSLError("marks tail vacío")
    if s.startswith("M"):
        s = s[1:].lstrip()
    if not (s.startswith("{") and s.endswith("}")):
        raise DSLError("marks tail inválido; se esperaba bloque {...}")

    args = _parse_brace_dict(s)

    ms = MarksSpec()

    # default parameter: style id
    # Our brace-dict parser treats bare tokens as {token: True}.
    # For Marks{}, the first bare token is the style id unless s=... is provided.
    if args.get("s") is None:
        for k, v in list(args.items()):
            if v is True and k not in ("b", "d", "layer", "len", "l", "length", "lengh"):
                ms.style = str(k)
                # remove it so it doesn't accidentally look like an unknown key later
                del args[k]
                break

    if args.get("s") is not None:
        ms.style = str(args.get("s") or "") or None
    if args.get("layer") is not None:
        ms.layer = str(args.get("layer") or "") or None

    # b: border pattern tokens (same grammar as border)
    if args.get("b") is not None:
        b = args.get("b")
        b = b if isinstance(b, list) else [b]
        ms.b = [_num_to_str_trim(x) for x in b]

    # d: distance tokens (same grammar as border)
    if args.get("d") is not None:
        d = args.get("d")
        d = d if isinstance(d, list) else [d]
        ms.d = [_num_to_str_trim(x) for x in d]

    # len: scalar or [out in]
    # Accept aliases for DSL consistency/typos: l, len, length, lengh
    ln = None
    for _k in ("len", "l", "length", "lengh"):
        if args.get(_k) is not None:
            ln = args.get(_k)
            break
    if ln is not None:
        if isinstance(ln, list):
            ms.length = [_num_to_str_trim(x) for x in ln]
        else:
            # Scalar form: for historical compatibility, we interpret l/len as
            # "[out, in]" with in=0.
            ms.length = [_num_to_str_trim(ln), "0"]

    return ms

def parse_leading_cell(cell0) -> DLeadingCell:
    copies, page_block, layout_block, marks_block = parse_copies_page_tail(cell0)
    holes = getattr(parse_copies_page_tail, "__holes__", [])
    copies_explicit = bool(getattr(parse_copies_page_tail, "__copies_explicit__", False))
    ps = None
    ls = None
    ms = None
    if page_block:
        try:
            ps = parse_page_block(page_block)
        except DSLError:
            ps = None
    if layout_block:
        try:
            ls = parse_layout_block(layout_block)
        except DSLError:
            ls = None
    if marks_block:
        try:
            ms = parse_marks_block(marks_block)
        except DSLError:
            ms = None
    return DLeadingCell(
        copies=int(copies or 1),
        holes=list(holes or []),
        copies_explicit=copies_explicit,
        page_block=page_block,
        layout_block=layout_block,
        marks_block=marks_block,
        page=ps,
        layout=ls,
        marks=ms
    )

# extend __all__ (without redefining it):
for _n in ["DLeadingCell","parse_leading_cell","parse_layout_block","parse_page_block","MarksSpec","parse_marks_block"]:
    if _n not in __all__:
        __all__.append(_n)
