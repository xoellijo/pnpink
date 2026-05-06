# -*- coding: utf-8 -*-
"""DeckMaker engine execution adapter used by the resident GUI app."""

from __future__ import annotations

from types import SimpleNamespace

import deckmaker_paths as DMPATHS


class EngineEffect:
    def __init__(self, template: str, sheet_id: str, sheet_range: str, log_level: str, dataset_source_mode: str = ""):
        import inkex

        self._template = DMPATHS.normalize(template)
        with open(self._template, "rb") as fh:
            raw = fh.read()
        self.document = inkex.load_svg(raw)
        self.svg = self.document.getroot()
        self.options = SimpleNamespace(
            tab="data",
            csv_path="",
            sheet_id=str(sheet_id or "").strip(),
            sheet_range=str(sheet_range or "").strip(),
            dataset_source_mode=str(dataset_source_mode or "").strip().lower(),
            prototypes_layer="Prototypes",
            preset="{A4}",
            stop_on_error=False,
            log_level=str(log_level or "global").strip() or "global",
        )

    def document_path(self) -> str:
        return self._template

    def _document_path_or_abort(self) -> str:
        import inkex

        if not self._template:
            raise inkex.AbortExtension("Save the SVG template before running DeckMaker.")
        return self._template

    def _find_or_create_layer(self, root, label: str):
        import inkex

        for child in list(root):
            try:
                if not (hasattr(child, "tag") and isinstance(child.tag, str) and child.tag.endswith("g")):
                    continue
                if child.get(inkex.addNS("groupmode", "inkscape")) == "layer":
                    if (child.get(inkex.addNS("label", "inkscape")) or "") == label:
                        return child
            except Exception:
                continue
        layer = inkex.Group()
        layer.set(inkex.addNS("groupmode", "inkscape"), "layer")
        layer.set(inkex.addNS("label", "inkscape"), label)
        root.append(layer)
        return layer
