from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

def _norm_path(path: str) -> str:
    # MkDocs uses OS-native separators for Page.file.src_path on Windows, while
    # `nav` paths are typically written with forward slashes.
    return path.replace("\\", "/").lstrip("./")


def _strip_existing_numbering(text: str) -> str:
    # If a doc already has manual numbering in the heading text, remove it so we
    # don't end up with duplicated numbers.
    return re.sub(r"^\s*\d+(?:[.)]\d+)*(?:[.)])?\s+", "", text).strip()


def _build_prefix_map(nav: List[Any]) -> Dict[str, str]:
    """
    Build a mapping from docs-relative paths (as used in mkdocs `nav`) to their
    section prefix (e.g. '3.2').

    Supports:
    - Top-level pages: `- Title: some.md`
    - Sections: `- Section: [ ... ]`
    - One extra nesting level (rare, but supported defensively).
    """
    prefix_map: Dict[str, str] = {}
    top_index = 0

    for item in nav:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        _, value = next(iter(item.items()))
        top_index += 1

        if isinstance(value, str):
            prefix_map[_norm_path(value)] = str(top_index)
            continue

        if isinstance(value, list):
            sub_index = 0
            for child in value:
                if not isinstance(child, dict) or len(child) != 1:
                    continue
                _, child_value = next(iter(child.items()))
                sub_index += 1

                if isinstance(child_value, str):
                    prefix_map[_norm_path(child_value)] = f"{top_index}.{sub_index}"
                    continue

                if isinstance(child_value, list):
                    # For nested sections, still map any leaf pages to the same
                    # prefix (top_index.sub_index).
                    for grandchild in child_value:
                        if not isinstance(grandchild, dict) or len(grandchild) != 1:
                            continue
                        _, grandchild_value = next(iter(grandchild.items()))
                        if isinstance(grandchild_value, str):
                            prefix_map[_norm_path(grandchild_value)] = f"{top_index}.{sub_index}"

    return prefix_map


_PREFIX_MAP: Dict[str, str] = {}


def on_config(config: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    # This runs once per build; we precompute the prefix map from mkdocs.yml nav.
    global _PREFIX_MAP
    _PREFIX_MAP = _build_prefix_map(config.get("nav", []))
    return config


def on_page_markdown(
    markdown: str, *, page: Any, config: Dict[str, Any], files: Any, **kwargs: Any
) -> str:
    prefix: Optional[str] = _PREFIX_MAP.get(_norm_path(page.file.src_path))
    if not prefix:
        return markdown

    lines = markdown.splitlines()
    out: List[str] = []

    in_fence = False
    fence_marker = ""
    counters = {2: 0, 3: 0, 4: 0}
    prefix_depth = len(prefix.split("."))
    max_depth = 3  # absolute depth: e.g. "3.2.1"

    for line in lines:
        stripped = line.strip()

        # Don't touch code fences.
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            out.append(line)
            continue

        if in_fence:
            out.append(line)
            continue

        m = _HEADING_RE.match(line)
        if not m:
            out.append(line)
            continue

        hashes, title = m.groups()
        level = len(hashes)
        clean_title = _strip_existing_numbering(title)
        rel_depth = 0 if level == 1 else (level - 1)
        target_depth = prefix_depth + rel_depth

        # Keep headings beyond the configured depth unnumbered.
        if target_depth > max_depth:
            out.append(f"{hashes} {clean_title}")
            continue

        if level == 1:
            counters[2] = counters[3] = counters[4] = 0
            out.append(f"{hashes} {prefix} {clean_title}")
        elif level == 2:
            counters[2] += 1
            counters[3] = counters[4] = 0
            out.append(f"{hashes} {prefix}.{counters[2]} {clean_title}")
        elif level == 3 and target_depth <= max_depth:
            if counters[2] == 0:
                counters[2] = 1
            counters[3] += 1
            counters[4] = 0
            out.append(f"{hashes} {prefix}.{counters[2]}.{counters[3]} {clean_title}")
        else:
            out.append(f"{hashes} {clean_title}")

    return "\n".join(out)
