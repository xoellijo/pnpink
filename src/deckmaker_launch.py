# -*- coding: utf-8 -*-
"""Shared Inkscape launcher helpers for DeckMaker frontends."""

from __future__ import annotations

import os

import inkex
from inkex_compat import etree

import dataset_state as DSTATE
from deckmaker_types import AppRequest
import temp_paths as TEMPPATHS


def require_document_path(extension: inkex.EffectExtension) -> str:
    path = extension.document_path()
    if not path or not os.path.isabs(path) or not os.path.isfile(path):
        raise inkex.AbortExtension("Save the SVG template before launching DeckMaker.")
    return os.path.normpath(path)


def current_document_snapshot(extension: inkex.EffectExtension, document_path: str) -> str:
    directory = TEMPPATHS.named_dir("deckmaker_snapshot", stem=TEMPPATHS.stem_for_path(document_path))
    snapshot = os.path.join(directory, "current.svg")
    temporary = snapshot + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(etree.tostring(extension.document))
    os.replace(temporary, snapshot)
    return os.path.normpath(snapshot)


def current_request(extension: inkex.EffectExtension) -> AppRequest:
    document_path = require_document_path(extension)
    snapshot = current_document_snapshot(extension, document_path)
    sheet_id = ""
    sheet_range = ""
    source_mode = ""
    selected_ids = tuple(
        str(node.get("id") or "").strip()
        for node in list(extension.svg.selection or [])
        if str(node.get("id") or "").strip()
    )
    try:
        record = DSTATE.get_gsheet_for_svg(document_path) or {}
        sheet_id = str(record.get("sheet_id") or "").strip()
        sheet_range = str(record.get("sheet_range") or "").strip()
        source_mode = str(record.get("access_mode") or "").strip().lower()
    except Exception:
        pass
    return AppRequest(
        template=document_path,
        snapshot_path=snapshot,
        sheet_id=sheet_id,
        sheet_range=sheet_range,
        dataset_source_mode=source_mode,
        selected_ids=selected_ids,
    )
