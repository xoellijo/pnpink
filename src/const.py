
# -*- coding: utf-8 -*-
"""
const.py — Single source of truth for presets (pages & cards) for PnPInk.
- Rich presets ported from legacy layouts (aliases, accent-insensitive).
- Adds credit card (CR80 / ID-1) and xl_poker/xl_standar/xl_standard (×1.125).
"""
from __future__ import annotations
from typing import Optional, Tuple, Dict

# ---------------- XML/SVG constants ----------------
import re

# Namespaces
NS_XML = 'http://www.w3.org/XML/1998/namespace'
NS_SVG = 'http://www.w3.org/2000/svg'
NS_XLINK = 'http://www.w3.org/1999/xlink'
NS_INKSCAPE = 'http://www.inkscape.org/namespaces/inkscape'
NS_SODIPODI = 'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd'

# Common fully-qualified attribute names
XML_SPACE = f'{{{NS_XML}}}space'
XML_BASE  = f'{{{NS_XML}}}base'
INK_GROUPMODE = f'{{{NS_INKSCAPE}}}groupmode'

# Common user-facing keys (non-FQ, used in page attribute dicts / parsing)
INK_PAGEOPACITY_KEY = 'inkscape:pageopacity'
INK_PAGECHECKERBOARD_KEY = 'inkscape:pagecheckerboard'

# Regex (shared)
RE_IMAGE_EXT = re.compile(r'\.(png|jpg|jpeg|gif|bmp|webp)$', re.IGNORECASE)
RE_ANCHOR = re.compile(r'^([imwhyxaotnbo])([1-9])$')
RE_VARS = re.compile(r'%([A-Za-z_][A-Za-z0-9_]*)%')

# ---------------- Page presets (mm) ----------------
PAGE_SIZES_MM: Dict[str, Tuple[float, float]] = {
    # ISO
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
    # North America
    "Letter":  (215.9, 279.4),
    "Legal":   (215.9, 355.6),
    "Tabloid": (279.4, 431.8),
}

# ---------------- Card presets (mm) ----------------
CARD_SIZES_MM: Dict[str, Tuple[float, float]] = {
    "Standard":     (63.0, 88.0),   # poker
    "2.5x3.5inch":  (63.5, 88.9),
    "Euro":         (59.0, 92.0),
    "USA":          (56.0, 87.0),
    "Asia":         (57.5, 89.0),   # Chimera
    "miniEuro":     (45.0, 68.0),
    "miniAsia":     (43.0, 65.0),
    "miniUSA":      (41.0, 63.0),
    "Tarot":        (70.0, 120.0),
    "FrenchTarot":  (61.0, 112.0),
    "Volcano":      (70.0, 110.0),
    "Wonder":       (65.0, 100.0),
    "Spanish":      (61.0, 95.0),
    "Desert":       (50.0, 65.0),
    "squareS":      (50.0, 50.0),
    "square":       (70.0, 70.0),
    "squareL":      (100.0, 100.0),
    "Dixit":        (80.0, 120.0),
    # New: Credit card family (ISO/IEC 7810 ID-1, CR80)
    "CreditCard":   (54.0, 85.6),
    "CR80":         (54.0, 85.6),
    "ID-1":         (54.0, 85.6),
}

# --------- Canon & alias (accent/spacing-insensitive) ----------
def _canon_key(s: str) -> str:
    s = (s or "").lower()
    # strip basic accents
    s = (s
         .replace("á","a").replace("é","e").replace("í","i")
         .replace("ó","o").replace("ú","u").replace("ü","u")
         .replace("à","a").replace("è","e").replace("ì","i")
         .replace("ò","o").replace("ù","u"))
    # remove spaces, hyphens, underscores, dots and non-alnum
    import re
    return re.sub(r"[^a-z0-9]", "", s)

_CARD_ALIAS_RAW: Dict[str, str] = {
    # Common
    "poker": "Standard",
    "magic": "Standard",
    "estandard": "Standard",
    "estandar": "Standard",
    "bridge": "USA",
    # minis
    "euromini": "miniEuro",
    "usamini": "miniUSA",
    "asiamini": "miniAsia",
    "mini": "Euro",
    # chimera
    "chimera": "Asia",
    "minichimera": "miniAsia",
    "chimeramini": "miniAsia",
    "baraja": "Spanish",
    # credit card aliases
    "creditcard": "CreditCard",
    "cr80": "CR80",
    "id1": "ID-1",
    "id-1": "ID-1",
    # xl poker family (12.5% bigger)
    "xlpoker": "XL_Poker",
    "xlpoker_": "XL_Poker",
    "xlstandar": "XL_Poker",
    "xlstandard": "XL_Poker",
    "xl_standar": "XL_Poker",
    "xl_standard": "XL_Poker",
}

# Build canonical map
CARD_NAME_CANONICAL: Dict[str, str] = {}
for _name in CARD_SIZES_MM.keys():
    CARD_NAME_CANONICAL[_canon_key(_name)] = _name
for _alias, _target in _CARD_ALIAS_RAW.items():
    CARD_NAME_CANONICAL[_canon_key(_alias)] = _target

# Page canonical map (accept lowercase tokens like 'a4')
PAGE_NAME_CANONICAL: Dict[str, str] = {}
for _name in PAGE_SIZES_MM.keys():
    PAGE_NAME_CANONICAL[_canon_key(_name)] = _name

def get_card_size_preset(name: Optional[str]) -> Optional[Tuple[float, float]]:
    """
    Resolve a card preset by name (accent/spacing-insensitive).
    Supports aliases and XL Poker (= Standard ×1.125).
    """
    if not name:
        return None
    key = _canon_key(str(name))
    # XL Poker special
    if key in ("xlpoker","xlstandar","xlstandard","xlstandar","xlstandard","xl_standar","xl_standard"):
        base_w, base_h = CARD_SIZES_MM["Standard"]
        return (round(base_w * 1.125, 3), round(base_h * 1.125, 3))
    # Canonical name
    canon = CARD_NAME_CANONICAL.get(key)
    if not canon:
        return None
    if canon == "XL_Poker":
        base_w, base_h = CARD_SIZES_MM["Standard"]
        return (round(base_w * 1.125, 3), round(base_h * 1.125, 3))
    return CARD_SIZES_MM.get(canon)

def get_page_size_preset(name: Optional[str]) -> Optional[Tuple[float, float]]:
    """Resolve page preset (accent/spacing-insensitive)."""
    if not name:
        return None
    key = _canon_key(str(name))
    canon = PAGE_NAME_CANONICAL.get(key)
    if not canon:
        return None
    return PAGE_SIZES_MM.get(canon)
