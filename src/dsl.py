# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "DSLError",
    "IdRef", "AliasRef", "RangeIdx", "ListIdx", "StarIdx",
    "SourceRef", "GroupRef",
    "FitSpec", "TransformSpec", "LayoutSpec", "PageSpec", "GridSpec", "ShapeSpec",
    "ModuleCall", "Chain", "Command",
    "parse", "maybe_parse",
    "tokenize_chain", "parse_chain", "maybe_parse_chain",
    "is_source_expr", "split_source_token", "normalize_ops_suffix",
    "ops_from_fit_spec", "fit_spec_from_ops", "split_ops_fit_transform",
    "parse_copies_page_tail", "parse_index_selector_1based", "measure_to_mm",
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
class TransformSpec:
    rotate: Optional[float] = None
    mirror: Optional[str] = None     # 'h'|'v'|'none'
    opacity: Optional[str] = None
    scale: Optional[List[str]] = None
    soft: Optional[List[str]] = None
    filter_ref: Optional[str] = None
    text: Optional[List[str]] = None

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
    # Global page cursor control belongs to Page{}, not Layout{}.
    # Examples: at=+3, a=-1, @5
    at: Optional[str] = None

@dataclass
class ShapeSpec:
    kind: Optional[str] = None
    preset: Optional[str] = None
    args: Optional[List[str]] = None
    rotation_steps: Optional[int] = None

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

    Developer note:
      - The style template is selected with t=...
      - b and d reuse the same list grammar as Page/Fit border: 1/2/3/4 tokens.
      - Rendering is slot-based (per placed instance).
    """
    style: Optional[str] = None   # t
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
    spec: Optional[Union[FitSpec, TransformSpec, LayoutSpec]] = None

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


def _normalize_short_list(vals: List[Any], *, name: str, allowed: tuple[int, ...], duplicate_1_to_2: bool = False) -> List[Any]:
    vals = list(vals or [])
    if duplicate_1_to_2 and len(vals) == 1 and 2 in allowed:
        return [vals[0], vals[0]]
    if len(vals) not in allowed:
        if duplicate_1_to_2 and 2 in allowed:
            raise DSLError(f"{name} requires 1 or 2 values")
        allowed_txt = " or ".join(str(n) for n in allowed)
        raise DSLError(f"{name} requires {allowed_txt} values")
    return vals


def _normalize_shift_pair(vals: List[Any]) -> List[Any]:
    return _normalize_short_list(vals, name="shift", allowed=(2,), duplicate_1_to_2=True)

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
    quote = ""
    for ch in s:
        if quote:
            tok.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            tok.append(ch)
            continue
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

def _unquote_value(v: str) -> str:
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1]
    if v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    return v


def _parse_brace_dict(body: str, *, raw_keys: Optional[set[str]] = None) -> Dict[str, Any]:
    inner = _strip_balanced(body, "{", "}")
    toks = _split_top(inner)
    out: Dict[str, Any] = {}
    raw_keys = {str(k).lower() for k in (raw_keys or set())}
    for t in toks:
        idx = _find_top_level_equal(t)
        if idx >= 0:
            k = t[:idx].strip()
            v = t[idx+1:].strip()
            raw_value = k.lower() in raw_keys
            v = _unquote_value(v)
            if v.startswith("[") and v.endswith("]"):
                lst = _parse_list(v)
                out[k] = [_unquote_value(x) for x in lst] if raw_value else [(_to_number(x) if _num_pure_re.match(x) else x) for x in lst]
            else:
                out[k] = v if raw_value else (_to_number(v) if _num_pure_re.match(v) else v)
        else:
            out[t.strip()] = True
    return out

# =================== Dataset declaration (Column A) ===================
#
# Grammar (Column A):
#   - "{{t=[id1 id2 ...]}}"
#   - "{{t=id}}"
#   - "{{id}}"              (shorthand, t is default parameter)
#   - "{{id @split}}"       (explicit split mode)
#   - "{{id!}}"             (split shorthand alias)
#   - "id"                  (bare form, only when allow_bare=True)
#
# This parser returns a minimal dict[str, list[str]] where keys are canonical
# parameter names (e.g. "template_bbox"). It performs only syntactic parsing;
# semantic interpretation (e.g. resolving SVG elements) is handled elsewhere.

_DATASET_KEY_ALIASES = {
    "t": "template_bbox",
    "template_bbox": "template_bbox",
    "split": "split",
    "@split": "split",
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

    split_flags = {"@split", "split", "!"}

    # Shorthand: "{{id}}" / "{{id!}}" / bare "id"
    if len(toks) == 1 and _find_top_level_equal(toks[0]) < 0:
        tok0 = toks[0].strip()
        if tok0 in split_flags:
            out["split"] = ["1"]
            return out
        if tok0.endswith("!") and tok0 != "!":
            out["template_bbox"] = [tok0[:-1].strip()]
            out["split"] = ["1"]
            return out
        out["template_bbox"] = [tok0]
        return out

    for t in toks:
        idx = _find_top_level_equal(t)
        if idx < 0:
            tt = t.strip()
            # Explicit split mode as standalone token (e.g. "{{t=id @split}}").
            if tt in split_flags:
                out["split"] = ["1"]
                continue
            # Alias: "{{id!}}"
            if tt.endswith("!") and tt != "!" and "template_bbox" not in out:
                out["template_bbox"] = [tt[:-1].strip()]
                out["split"] = ["1"]
                continue
            # Additional shorthand token for template id (e.g. "{{id @split}}").
            if "template_bbox" not in out:
                out["template_bbox"] = [tt]
                continue
            raise DSLError(f"Invalid token in dataset marker: '{t}'")

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
            if ck == "template_bbox" and vv.endswith("!") and vv != "!":
                vv = vv[:-1].strip()
                out["split"] = ["1"]
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
    if (len(b) >= 2) and ((b[0] == '"' and b[-1] == '"') or (b[0] == "'" and b[-1] == "'")):
        b = b[1:-1].strip()

    if b.lower() in {"img", "pdf", "url", "file", "iconify", "svg"}:
        raise DSLError("Source requires src")

    mlead = re.match(r"^(img|pdf|url|file|iconify|svg)\s+(.*)$", b, re.I)
    if mlead:
        kind = mlead.group(1).lower()
        rest = mlead.group(2).strip()
        if not rest:
            raise DSLError("Source requires src")
        args = _parse_brace_dict("{"+rest+"}")
        src = str(args.get("src") or args.get("href") or args.get("url") or "")
        if not src:
            raise DSLError("Source requires src")
        return SourceRef(kind, src, args)

    has_kv = "=" in b
    if has_kv:
        args = _parse_brace_dict("{"+b+"}")
        src = str(args.get("src") or args.get("href") or args.get("url") or "")
        if not src:
            for k, v in list(args.items()):
                if v is True and re.match(r"^(?:https?://|wkmc://|pxby://|oclp://|pnp://|osm://|ofm://|icon://|file:|data:)", str(k), re.I):
                    src = str(k)
                    args["src"] = src
                    del args[k]
                    break
        if not src:
            raise DSLError("Source requires src")
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
    rx = re.compile(r"\^(-?\d+(?:\.\d+)?)|\^{1,3}(?!\^)")
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
    # ? auto-fit (alias for best-fit)
    '?':'b','auto-fit':'b','autofit':'b','auto':'b',
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
        if not m: raise DSLError("Invalid fit token")
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
    # shift (primary): shift/s.
    if "shift" in args:
        vals = _normalize_shift_pair(_as_list(args.get("shift")))
        fs.shift = [_to_number(vals[0]), _to_number(vals[1])]
    if "s" in args and fs.shift is None:
        vals = _normalize_shift_pair(_as_list(args.get("s")))
        fs.shift = [_to_number(vals[0]), _to_number(vals[1])]
    # No retro-compat aliases for simplicity.
    if "translate" in args:
        raise DSLError("translate is not supported; use shift=[dx dy]")
    # 't' is reserved for tile mode (boolean flag), never for shift.
    if "t" in args and args.get("t") is not True:
        raise DSLError("t is reserved for tile mode; use shift=[dx dy]")
    if "rotate" in args or "r" in args:
        raise DSLError("rotate belongs to Transform; use .T{rotate=...} or shorthand ~^")
    if "mirror" in args:
        raise DSLError("mirror belongs to Transform; use .T{mirror=...} or shorthand ~|")
    if "clip" in args or args.get("c") is True:
        v = args.get("clip", True)
        fs.clip = True
        fs.clip_stage = "pre" if isinstance(v, str) else "post"
    if args.get("clip_pre") is True:
        fs.clip = True; fs.clip_stage = "pre"
    return fs

def _transform_from_dict(args: Dict[str, Any]) -> TransformSpec:
    ts = TransformSpec()
    if "rotate" in args:
        ts.rotate = _try_float(args.get("rotate"))
    if "r" in args and ts.rotate is None:
        ts.rotate = _try_float(args.get("r"))
    if "mirror" in args:
        mv = str(args["mirror"]).lower()
        if mv in ("h", "v", "none"):
            ts.mirror = mv
    if "m" in args and ts.mirror is None:
        mv = str(args["m"]).lower()
        if mv in ("h", "v", "none"):
            ts.mirror = mv
    if "opacity" in args:
        ts.opacity = str(args.get("opacity") or "").strip()
    if "o" in args and not ts.opacity:
        ts.opacity = str(args.get("o") or "").strip()
    if "filter" in args:
        ts.filter_ref = str(args.get("filter") or "").strip()
    if "f" in args and not ts.filter_ref:
        ts.filter_ref = str(args.get("f") or "").strip()
    raw_text = None
    if "text" in args:
        raw_text = args.get("text")
    elif "t" in args:
        raw_text = args.get("t")
    if raw_text is not None:
        vals = [str(v).strip() for v in _as_list(raw_text)]
        ts.text = vals

    raw_scale = None
    if "scale" in args:
        raw_scale = args.get("scale")
    elif "s" in args:
        raw_scale = args.get("s")
    if raw_scale is not None:
        vals = [str(v).strip() for v in _as_list(raw_scale) if str(v).strip()]
        if len(vals) not in (1, 2):
            raise DSLError("scale requires 1 or 2 values")
        ts.scale = vals

    raw_soft = None
    if "edge" in args:
        raw_soft = args.get("edge")
    elif "e" in args:
        raw_soft = args.get("e")
    if raw_soft is not None:
        vals = _as_list(raw_soft)
        vals = [str(v).strip() for v in vals if str(v).strip()]
        if len(vals) not in (1, 2, 4):
            raise DSLError("edge requires 1, 2 or 4 values")
        ts.soft = vals
    return ts

def _parse_fit_long(cmd: str) -> FitSpec:
    m = re.match(r"^\s*(?:[A-Za-z][\w\-.]*\s*)?\.Fit\s*(\{.*\})\s*$", cmd)
    if not m:
        raise DSLError("Invalid long Fit form")
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
    saw_mode_or_anchor = False
    saw_nonclip = False
    while i < len(toks):
        t = toks[i]

        # leading rotations without mode
        if fs.mode is None and t.startswith("^"):
            m_mix = re.match(r"^\^(-?\d+(?:\.\d+)?)([imwhyxaotnbo\?])$", t, re.I)
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
                raise DSLError("shift requires [dx dy] or [d]")
            lst = _normalize_shift_pair(_parse_list(toks[i+1]))
            dx, dy = lst[0], lst[1]
            dxv = float(dx) if _num_pure_re.match(dx) else dx
            dyv = float(dy) if _num_pure_re.match(dy) else dy
            fs.shift = [dxv, dyv]; saw_shift = True
            saw_nonclip = True
            i += 2; continue

        if t.startswith('[') and t.endswith(']'):
            lst = _parse_list(t)
            # Positional shorthand rule:
            # - If [..] appears after mode/anchor and has 2 values, treat it as shift.
            # - Otherwise, first [..] is border; second [..] is shift.
            if fs.border is None and saw_mode_or_anchor and len(lst) == 2:
                dx, dy = lst[0], lst[1]
                dxv = float(dx) if _num_pure_re.match(dx) else dx
                dyv = float(dy) if _num_pure_re.match(dy) else dy
                fs.shift = [dxv, dyv]
                saw_shift = True
            elif fs.border is None:
                fs.border = [_num_to_str_trim(x) for x in lst]
            else:
                lst = _normalize_shift_pair(lst)
                dx, dy = lst[0], lst[1]
                dxv = float(dx) if _num_pure_re.match(dx) else dx
                dyv = float(dy) if _num_pure_re.match(dy) else dy
                fs.shift = [dxv, dyv]; saw_shift = True
            saw_nonclip = True
            i += 1; continue

        if t in ('|', '||'):
            fs.mirror = 'h' if t == '|' else 'v'
            saw_nonclip = True
            i += 1; continue

        if t == 'c':
            fs.clip = True; fs.clip_stage = "post"
            i += 1; continue

        if t.startswith('^'):
            if t == '^':
                fs.rotate = (fs.rotate or 0.0) + 90.0
            else:
                fs.rotate = (fs.rotate or 0.0) + float(t[1:])
            saw_nonclip = True
            i += 1; continue

        if t in ('!', '!!'):
            fs.clip = True
            fs.clip_stage = "pre" if not saw_nonclip else "post"
            i += 1; continue

        low = t.lower()
        m = re.match(r"^([imwhyxaotnbo\?])([1-9])$", low)
        if m:
            fs.mode = _FIT_MODES[m.group(1)]
            fs.anchor = int(m.group(2))
            saw_mode_or_anchor = True
            saw_nonclip = True
            i += 1; continue

        if low in _FIT_MODES:
            fs.mode = _FIT_MODES[low]
            saw_mode_or_anchor = True
            saw_nonclip = True
            i += 1; continue

        m_anchor_only = re.match(r"^[1-9]$", t)
        if m_anchor_only:
            if fs.mode is None: fs.mode = 'i'
            fs.anchor = int(t)
            saw_mode_or_anchor = True
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

def split_ops_fit_transform(ops: str) -> Tuple[str, Optional[TransformSpec]]:
    fs = fit_spec_from_ops(ops)
    ts = TransformSpec()
    has_transform = False
    if fs.rotate not in (None, 0, 0.0):
        ts.rotate = fs.rotate
        has_transform = True
    if fs.mirror in ('h', 'v'):
        ts.mirror = fs.mirror
        has_transform = True
    fs.rotate = None
    fs.mirror = None
    return ops_from_fit_spec(fs), (ts if has_transform else None)

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
        saw_mode_or_anchor = False
        saw_nonclip = False
        for t in btoks:
            if t == '!':
                fs.clip = True
                fs.clip_stage = "pre" if not saw_nonclip else "post"
                continue
            if t.startswith('[') and t.endswith(']'):
                lst = _parse_list(t)
                # Keep shorthand positional behavior inside '~{ ... }' too.
                if fs.border is None and saw_mode_or_anchor and len(lst) == 2:
                    dx, dy = lst[0], lst[1]
                    dxv = float(dx) if _num_pure_re.match(dx) else dx
                    dyv = float(dy) if _num_pure_re.match(dy) else dy
                    fs.shift = [dxv, dyv]
                    saw_shift = True
                elif fs.border is None:
                    fs.border = [_num_to_str_trim(x) for x in lst]
                else:
                    lst = _normalize_shift_pair(lst)
                    dx, dy = lst[0], lst[1]
                    dxv = float(dx) if _num_pure_re.match(dx) else dx
                    dyv = float(dy) if _num_pure_re.match(dy) else dy
                    fs.shift = [dxv, dyv]
                    saw_shift = True
                saw_nonclip = True
                continue
            low = t.lower()
            m = re.match(r"^([imwhyxaotnbo\?])([1-9])$", low)
            if m:
                fs.mode = _FIT_MODES[m.group(1)]
                fs.anchor = int(m.group(2))
                saw_mode_or_anchor = True
                saw_nonclip = True
                continue
            if low in _FIT_MODES:
                fs.mode = _FIT_MODES[low]
                saw_mode_or_anchor = True
                saw_nonclip = True
                continue

        if fs.mode in (None,):
            fs.mode = 'i'

        mrot = re.search(r"\^(-?\d+(?:\.\d+)?)", tail)
        if mrot:
            fs.rotate = float(mrot.group(1))
        else:
            mcarets = re.search(r"(\^{1,3})(?!\^)", tail)
            if mcarets:
                fs.rotate = float(len(mcarets.group(1)) * 90)
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

    # Porcentaje: "10%"; "%" is shorthand for "100%" and "-%" for "-100%".
    if s.endswith("%"):
        num = s[:-1].strip()
        if num in ("", "+"):
            num = "100"
        elif num == "-":
            num = "-100"
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

    # Last fallback: plain float.
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

    if out:
        out = _normalize_short_list(out, name="gaps", allowed=(1, 2), duplicate_1_to_2=True)

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

    if out:
        out = _normalize_short_list(out, name="offset", allowed=(1, 2, 3, 4))

    return out

def _parse_grid_token_and_inline(value: str) -> Tuple[GridSpec, Optional[str]]:
    val = (value or "").strip()
    inline = None
    if "<" in val and val.endswith(">"):
        i = val.find("<")
        inline = val[i:]
        val = val[:i].strip()
    m = re.match(
        r"^\s*(?P<c>-?(?:\d+|\?))(?P<mir>\|)?x(?P<r>-?(?:\d+|\?))(?P<mir2>\|)?(?P<v>\^)?\s*$",
        val,
        re.I,
    )
    if not m:
        raise DSLError("pattern requires colsxrows format (supports ?, | and ^)")

    c_tok = (m.group("c") or "").strip()
    r_tok = (m.group("r") or "").strip()

    def _tok_to_int(tok: str) -> int:
        t = (tok or "").strip()
        sign = -1 if t.startswith("-") else 1
        core = t[1:] if t.startswith("-") else t
        if core == "?":
            return 0 * sign
        return sign * int(core)

    cols = _tok_to_int(c_tok)
    rows = _tok_to_int(r_tok)
    flip_h = bool(m.group("mir")) or (str(c_tok).startswith("-"))
    flip_v = bool(m.group("mir2")) or (str(r_tok).startswith("-"))
    if flip_h and flip_v:
        flip = "hv"
    elif flip_h:
        flip = "h"
    elif flip_v:
        flip = "v"
    else:
        flip = None
    order = "tb-lr" if m.group("v") else "lr-tb"
    cols = abs(int(cols))
    rows = abs(int(rows))
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
    rotation_steps = None
    mrot = re.search(r"(\^+)$", v)
    if mrot:
        n = len(mrot.group(1))
        if n > 3:
            raise DSLError("shape rotation supports only ^, ^^ or ^^^")
        rotation_steps = n % 4
        v = v[:mrot.start()].strip()
    # size like "55x77" (with or without units)
    if _shape_size_re.match(v):
        return ShapeSpec(kind="rect", args=[v], rotation_steps=rotation_steps)
    # rect<...> / hex<...> / polygon<[...]>
    if v.startswith("rect<"):
        return ShapeSpec(kind="rect", args=[v[v.find('<'):]], rotation_steps=rotation_steps)
    if v.startswith("hex<"):
        return ShapeSpec(kind="hex", args=[v[v.find('<'):]], rotation_steps=rotation_steps)
    if v.startswith("polygon<"):
        return ShapeSpec(kind="polygon", args=[v[v.find('<'):]], rotation_steps=rotation_steps)
    # preset
    return ShapeSpec(kind="preset", preset=v, rotation_steps=rotation_steps)

def _parse_layout_v2(layout_cmd: str) -> LayoutSpec:
    m = re.match(r"^\s*(?:[A-Za-z][\w\-.]*\s*)?\.(?:Layout|L)\s*(\{.*\})\s*$", layout_cmd)
    if not m:
        raise DSLError("Invalid layout")
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
        raise DSLError("Invalid alias")
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
            raise DSLError("Invalid alias RHS")
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
            raise DSLError("Invalid Fit shorthand")
        fs = _parse_fit_shorthand(m.group("trail"))
        return Command("Fit", target=IdRef(m.group("id")), fit=fs)

    # Layout v2
    if ".Layout" in s or ".L" in s:
        m2 = re.search(r"\.(?:Layout|L)\s*(\{.*\})\s*$", s)
        if not m2:
            raise DSLError("Invalid layout")
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
        if re.match(r"^[A-Za-z]", s[i:]):
            j = i + 1
            while j < N:
                ch2 = s[j]
                if re.match(r"[\w\-\.]", ch2):
                    if ch2 == '.' and re.match(r"^\.[A-Za-z][\w\-]*\{", s[j:]):
                        break
                    j += 1
                    continue
                break
            seg = s[i:j]
            out.append(Token('id', seg, i, j)); i = j; continue
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
            mc = ModuleCall(mod_name, {}, spec=None)
            lname = mod_name.lower()
            if lname == 'fit':
                args = _parse_brace_dict(body)
                mc.args = args
                mc.spec = _fit_from_dict(args)
            elif lname in ('transform', 't'):
                args = _parse_brace_dict(body, raw_keys={"text", "t"})
                mc.args = args
                mc.spec = _transform_from_dict(args)
            elif lname in ('layout','l'):
                args = _parse_brace_dict(body)
                mc.args = args
                mc.spec = _parse_layout_v2(f"dummy.{mod_name}{body}")
            else:
                mc.args = _parse_brace_dict(body)
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
    and expose holes in parse_copies_page_tail.__holes__ (1-based list)
    and iterator selectors in parse_copies_page_tail.__iter_select__.
    Also exposes slot selectors in:
      - parse_copies_page_tail.__slot_select_raw__
      - parse_copies_page_tail.__slot_select_mode__ ('declarative' | 'procedural' | None)

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
    iter_select: List[int] = []
    iter_select_raw = None
    slot_select_raw = None
    slot_select_mode = None
    rest = s

    # { ... } block (page / breaks)
    # IMPORTANT: do not capture dataset markers "{{...}}" as a Page block.
    m_page = re.search(r"(?<![\w\{])\{[^{}]*\}(?!\})", rest)
    if m_page:
        page_block = m_page.group(0)
        rest = rest[:m_page.start()] + rest[m_page.end():]

    def _looks_like_slot_selector_body(body: str) -> bool:
        bb = str(body or "").strip()
        if not bb:
            return False
        if ":" in bb:
            return True
        if re.search(r"[A-Za-z]+\d+", bb):
            return True
        return False

    def _looks_like_slot_selector_proc(body: str) -> bool:
        bb = str(body or "").strip()
        if not bb:
            return False
        toks = [t for t in re.split(r"[\s,]+", bb) if t]
        if len(toks) < 2:
            return False
        if not re.fullmatch(r"[A-Za-z]+[1-9]\d*", toks[0]):
            return False
        later_cells = [t for t in toks[1:] if re.fullmatch(r"[A-Za-z]+[1-9]\d*", t)]
        if later_cells:
            return False
        return any(re.fullmatch(r"\d+|\d+-|-+", t) for t in toks[1:])

    # sequence [N H- N H- ...] at the end:
    #   - plain number "N" adds cards
    #   - "H-" adds H empty slots after the current accumulated copy
    #     (at the beginning, before the first copy)
    #   - "-" (or "---") adds 1 (or many) empty slots
    #   - when ranges are present ("A..B"), numbers/ranges select iterator
    #     positions and hole markers still apply after the accumulated selected run
    holes: List[int] = []
    # IMPORTANT: capture only the *final* flat bracket block, so we do not
    # accidentally consume bracket lists from Layout/Page tails (e.g. g=[...], o=[...]).
    m_seq = re.search(r"\[([^\[\]]*)\]\s*$", rest.strip())
    if m_seq:
        seq_body = m_seq.group(1).strip()
        toks = [t for t in re.split(r"[\s,]+", seq_body) if t]
        if _looks_like_slot_selector_proc(seq_body):
            slot_select_raw = seq_body
            slot_select_mode = "procedural"
        elif _looks_like_slot_selector_body(seq_body):
            slot_select_raw = seq_body
            slot_select_mode = "declarative"
        elif any(".." in t for t in toks):
            iter_select_raw = seq_body
            run = 0
            for t in toks:
                if re.fullmatch(r"-+", t):
                    for _ in range(len(t)):
                        holes.append(run)
                    continue
                m_h = re.fullmatch(r"(\d+)-", t)
                if m_h:
                    n_holes = int(m_h.group(1))
                    for _ in range(max(0, n_holes)):
                        holes.append(run)
                    continue
                m_r = re.fullmatch(r"(\d+)\s*\.\.\s*(\d+)", t)
                if m_r:
                    a = int(m_r.group(1))
                    b = int(m_r.group(2))
                    step = 1 if b >= a else -1
                    seq = list(range(a, b + step, step))
                    iter_select.extend(seq)
                    run += len(seq)
                    continue
                if t.isdigit():
                    iter_select.append(int(t))
                    run += 1
        else:
            run = 0
            for t in toks:
                if re.fullmatch(r"-+", t):
                    for _ in range(len(t)):
                        holes.append(run)  # run=0 means before first copy
                    continue
                m_h = re.fullmatch(r"(\d+)-", t)
                if m_h:
                    n_holes = int(m_h.group(1))
                    for _ in range(max(0, n_holes)):
                        holes.append(run)  # run=0 means before first copy
                    continue
                if t.isdigit():
                    run += int(t)
            if run > 0:
                copies = run
                copies_explicit = True
        rest = rest[:m_seq.start()]
    elif _looks_like_slot_selector_proc(rest):
        slot_select_raw = str(rest or "").strip()
        slot_select_mode = "procedural"
        rest = ""
    # trailing number / '?' as copies (ignoring tokens inside [] and {})
    if copies == 1 and slot_select_mode is None:
        rest_no_braces = re.sub(r"\{[^}]*\}", "", rest)
        rest_no_brackets = re.sub(r"\[[^\]]*\]", "", rest_no_braces)
        m_num = re.search(r"(?:^|\s)(\d+|\?)\s*$", rest_no_brackets.strip())
        if m_num:
            copies = "?" if m_num.group(1) == "?" else max(0, int(m_num.group(1)))
            copies_explicit = True
            # remove that trailing number so it does not block the L{...} tail
            rest = re.sub(r"(?:^|\s)(?:\d+|\?)\s*$", "", rest)

    # tail M{ ... } — admite ".M{...}" o "M{...}" al final
    # IMPORTANT: parse M first to allow "... .L{...}.M{...}".
    m_m = re.search(r"(?:^|[\.])M\s*(\{.*\})\s*$", rest.strip())
    if m_m:
        marks_block = "M" + m_m.group(1)
        rest = rest[:m_m.start()] + rest[m_m.end():]

    # Layout tail — accepts ".L{...}" / "L{...}" / ".Layout{...}" / "Layout{...}" at the end.
    m_tail = re.search(r"(?:^|[\.])(?:Layout|L)\s*(\{.*\})\s*$", rest.strip(), re.I)
    if m_tail:
        layout_block = "L" + m_tail.group(1)
        rest = rest[:m_tail.start()] + rest[m_tail.end():]

    parse_copies_page_tail.__holes__ = holes
    parse_copies_page_tail.__iter_select__ = iter_select
    parse_copies_page_tail.__iter_select_raw__ = iter_select_raw
    parse_copies_page_tail.__slot_select_raw__ = slot_select_raw
    parse_copies_page_tail.__slot_select_mode__ = slot_select_mode
    parse_copies_page_tail.__copies_explicit__ = bool(copies_explicit)
    return copies, page_block, layout_block, marks_block

# === PnPInk Phase 3 – public DSL helpers (no semantic changes) ===
from dataclasses import dataclass
from typing import Optional, List

def parse_index_selector_1based(sel: str, size: Optional[int] = None) -> List[int]:
    """Parse selector body like '2 4..12 15..?' into 1-based integer indices."""
    body = str(sel or '').strip()
    if body.startswith('[') and body.endswith(']'):
        body = body[1:-1].strip()
    if not body:
        return []
    out: List[int] = []

    def _parse_endpoint(tok: str):
        t = str(tok or "").strip().lower()
        if t == "?":
            return int(size) if size is not None else None
        if re.match(r'^\d+$', t):
            return int(t)
        return None

    toks = [t for t in re.split(r'[\s,]+', body) if t]
    for t in toks:
        m = re.match(r'^([A-Za-z0-9?]+)\s*\.\.\s*([A-Za-z0-9?]+)$', t)
        if m:
            a = _parse_endpoint(m.group(1))
            b = _parse_endpoint(m.group(2))
            if a is None or b is None:
                continue
            step = 1 if b >= a else -1
            out.extend(list(range(a, b + step, step)))
            continue
        v = _parse_endpoint(t)
        if v is not None:
            out.append(v)
            continue
    return out

@dataclass
class DLeadingCell:
    copies: object
    holes: List[int]
    iter_select: List[int] = field(default_factory=list)
    iter_select_raw: Optional[str] = None
    slot_select_raw: Optional[str] = None
    slot_select_mode: Optional[str] = None
    copies_explicit: bool = False
    page_block: Optional[str] = None   # "{A3 ...}" text or None.
    layout_block: Optional[str] = None # "L{ ... }" / "{ ... }" text or None.
    marks_block: Optional[str] = None  # "M{ ... }" text or None.
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
    if re.match(r"^(?:Layout|L)\b", s, re.I):
        s = re.sub(r"^(?:Layout|L)\b", "", s, count=1, flags=re.I).lstrip()
    if not (s.startswith("{") and s.endswith("}")):
        raise DSLError("Invalid layout tail; expected {...} block")
    return _parse_layout_v2(f"dummy.L{s}")


def parse_marks_block(text: str) -> "MarksSpec":
    """Parse a Marks/M tail block.

    Expected formats:
      - "M{ ... }"
      - "{ ... }"  (internal use)

    Notes:
      - Style template is declared with t=style_id.
      - b/d use the same list grammar as border in Page/Fit (no new ad-hoc parsing).
    """
    s = (text or "").strip()
    if not s:
        raise DSLError("marks tail vacío")
    if s.startswith("M"):
        s = s[1:].lstrip()
    if not (s.startswith("{") and s.endswith("}")):
        raise DSLError("Invalid marks tail; expected {...} block")

    args = _parse_brace_dict(s)

    ms = MarksSpec()

    if args.get("t") is not None:
        ms.style = str(args.get("t") or "") or None
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
    iter_select = getattr(parse_copies_page_tail, "__iter_select__", [])
    iter_select_raw = getattr(parse_copies_page_tail, "__iter_select_raw__", None)
    slot_select_raw = getattr(parse_copies_page_tail, "__slot_select_raw__", None)
    slot_select_mode = getattr(parse_copies_page_tail, "__slot_select_mode__", None)
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
        copies=(copies if copies == "?" else int(copies or 1)),
        holes=list(holes or []),
        iter_select=list(iter_select or []),
        iter_select_raw=(str(iter_select_raw).strip() if iter_select_raw else None),
        slot_select_raw=(str(slot_select_raw).strip() if slot_select_raw else None),
        slot_select_mode=(str(slot_select_mode).strip() if slot_select_mode else None),
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
