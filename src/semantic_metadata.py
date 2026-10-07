# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shlex
from typing import Any, Mapping


SEMANTIC_PREFIX = "$"
TOP_LEVEL_FIELDS = {"id", "type", "name", "role", "tags"}


def is_semantic_header(header: object) -> bool:
    return str(header or "").strip().startswith(SEMANTIC_PREFIX)


def has_field(values: Mapping[str, object], field: str) -> bool:
    wanted = str(field or "").strip().lower().replace("-", "_")
    return any(
        str(header or "").strip()[1:].strip().lower().replace("-", "_") == wanted
        for header in values
        if is_semantic_header(header)
    )


def _scalar(value: object) -> Any:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        try:
            return int(text)
        except ValueError:
            pass
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", text):
        try:
            return float(text)
        except ValueError:
            pass
    return text


def _list(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        values = shlex.split(text.replace(",", " "))
    except ValueError:
        values = text.replace(",", " ").split()
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def from_mapping(values: Mapping[str, object]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    for raw_header, raw_value in values.items():
        header = str(raw_header or "").strip()
        if not is_semantic_header(header):
            continue
        key = header[1:].strip().lower().replace("-", "_")
        if not key or raw_value is None or str(raw_value).strip() == "":
            continue
        if key == "role":
            roles = _list(raw_value)
            if roles:
                metadata["roles"] = roles
        elif key == "tags":
            tags = _list(raw_value)
            if tags:
                metadata["tags"] = tags
        elif key in {"id", "type", "name"}:
            metadata[key] = str(raw_value).strip()
        else:
            properties[key] = _scalar(raw_value)
    if properties:
        metadata["properties"] = properties
    return metadata


def manifest_fields(metadata: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if metadata.get("id"):
        result["id"] = str(metadata["id"])
    if metadata.get("name"):
        result["name"] = str(metadata["name"])
        result["label"] = str(metadata["name"])
    if metadata.get("type"):
        result["type"] = str(metadata["type"])
    for key in ("roles", "tags"):
        value = metadata.get(key)
        if isinstance(value, list) and value:
            result[key] = list(value)
    properties = metadata.get("properties")
    if isinstance(properties, dict) and properties:
        result["properties"] = dict(properties)
        if "value" in properties:
            result["value"] = properties["value"]
    return result


__all__ = ["SEMANTIC_PREFIX", "TOP_LEVEL_FIELDS", "from_mapping", "has_field", "is_semantic_header", "manifest_fields"]
