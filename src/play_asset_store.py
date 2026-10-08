"""Publish PnPPlay component packs to content-addressed asset stores."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


PACK_SCHEMA = "pnpplay.asset-pack/v2"


def local_store_root() -> Path:
    configured = str(os.environ.get("PNPPLAY_ASSET_STORE") or "").strip()
    return (Path(configured).expanduser() if configured else Path.home() / ".pnpplay" / "stores" / "local").resolve()


def normalized_pack_id(value: str) -> str:
    parts = []
    for part in str(value or "").replace("\\", "/").split("/"):
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", part.strip()).strip(".-")
        if cleaned:
            parts.append(cleaned.lower())
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("Asset pack ID needs at least one safe path segment")
    return "/".join(parts)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _object_reference(store: Path, source: Path) -> str:
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    suffix = source.suffix.lower()
    relative = Path("objects") / "sha256" / digest[:2] / f"{digest}{suffix}"
    target = store / relative
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return relative.as_posix()


def _catalog(store: Path) -> dict[str, Any]:
    packs = []
    packs_root = store / "packs"
    if packs_root.is_dir():
        for manifest_path in sorted(packs_root.glob("**/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(manifest, dict) and manifest.get("schema") == PACK_SCHEMA and manifest.get("pack"):
                packs.append({
                    "store": "local",
                    "pack": manifest["pack"],
                    "name": manifest.get("name", manifest["pack"]),
                    "manifest": manifest_path.relative_to(store).as_posix(),
                    "items": len(manifest.get("items", [])),
                })
    return {"schema": 1, "store": "local", "packs": packs}


def publish_local_pack(
    source_directory: Path,
    pack_id: str,
    *,
    store_root: Path | None = None,
    name: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_directory = Path(source_directory).resolve()
    items_path = source_directory / "items.json"
    document = json.loads(items_path.read_text(encoding="utf-8"))
    items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"PnPPlay items manifest has no items array: {items_path}")

    store = (store_root or local_store_root()).expanduser().resolve()
    normalized_id = normalized_pack_id(pack_id)
    published_items = []
    for index, original in enumerate(items, 1):
        if not isinstance(original, dict):
            raise ValueError(f"Invalid component #{index} in {items_path}")
        item = dict(original)
        for field in ("front", "back"):
            value = item.get(field)
            if not value:
                continue
            asset = (source_directory / str(value)).resolve()
            if source_directory not in asset.parents or not asset.is_file():
                raise ValueError(f"Component asset not found inside export: {value}")
            item[field] = _object_reference(store, asset)
        published_items.append(item)

    payload = {
        "schema": PACK_SCHEMA,
        "pack": normalized_id,
        "name": str(name or normalized_id.rsplit("/", 1)[-1]),
        "source": dict(source or {}),
        "items": published_items,
    }
    pack_root = store / "packs" / Path(*normalized_id.split("/"))
    manifest_path = pack_root / "manifest.json"
    _write_json_atomic(manifest_path, payload)
    published = {
        "schema": PACK_SCHEMA,
        "store": "local",
        "pack": normalized_id,
        "name": payload["name"],
        "manifest": manifest_path.relative_to(store).as_posix(),
        "items": len(published_items),
    }
    _write_json_atomic(store / "catalog.json", _catalog(store))
    return published
