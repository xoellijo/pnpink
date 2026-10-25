"""Small, shared helpers for project-local asset paths."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


ASSET_DIR_NAMES = ("assets", "images", "img", "imgs")


def _named_file(directory: Path, filename: str) -> Path | None:
    candidate = directory / filename
    if candidate.is_file():
        return candidate.resolve()
    if os.name == "nt":
        wanted = filename.casefold()
        try:
            return next(
                child.resolve()
                for child in directory.iterdir()
                if child.is_file() and child.name.casefold() == wanted
            )
        except (OSError, StopIteration):
            pass
    return None


def find_named_asset(template: str, filename: str) -> Path | None:
    """Find a named asset beside a template or two directory levels below it."""
    template_path = Path(str(template or "")).expanduser().resolve()
    if not template_path.is_file():
        return None
    base = template_path.parent
    direct = _named_file(base, filename)
    if direct:
        return direct
    for directory_name in ASSET_DIR_NAMES:
        asset_root = base / directory_name
        if not asset_root.is_dir():
            continue
        hit = _named_file(asset_root, filename)
        if hit:
            return hit
        try:
            children = sorted(
                (child for child in asset_root.iterdir() if child.is_dir()),
                key=lambda child: child.name.casefold(),
            )
        except OSError:
            children = []
        for child in children:
            hit = _named_file(child, filename)
            if hit:
                return hit
    return None


def import_asset(template: str, filename: str, content: bytes) -> tuple[Path, str, bool]:
    """Resolve an existing project asset or copy it into the template's assets folder."""
    template_path = Path(str(template or "")).expanduser().resolve()
    if not template_path.is_file():
        raise RuntimeError("Save the template before importing a PDF spritesheet")
    safe_name = Path(str(filename or "spritesheet.pdf")).name or "spritesheet.pdf"
    existing = find_named_asset(str(template_path), safe_name)
    copied = existing is None
    target = existing or (template_path.parent / ASSET_DIR_NAMES[0] / safe_name)
    if copied:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.pnpink-tmp")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        target = target.resolve()
    relative = os.path.relpath(target, template_path.parent).replace(os.sep, "/")
    return target, relative, copied


def store_generated_asset(template: str, filename: str, source: str | Path) -> tuple[Path, str]:
    """Atomically store a generated file in the template's assets directory."""
    template_path = Path(str(template or "")).expanduser().resolve()
    if not template_path.is_file():
        raise RuntimeError("Save the template before generating a project asset")
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise RuntimeError(f"Generated asset not found: {source_path}")
    safe_name = Path(str(filename or source_path.name)).name or source_path.name
    target = template_path.parent / ASSET_DIR_NAMES[0] / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_path != target.resolve():
        temporary = target.with_name(f".{target.name}.pnpink-tmp")
        shutil.copyfile(source_path, temporary)
        os.replace(temporary, target)
    target = target.resolve()
    relative = os.path.relpath(target, template_path.parent).replace(os.sep, "/")
    return target, relative
