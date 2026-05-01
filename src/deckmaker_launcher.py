#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Menu launcher for the resident DeckMaker app.

This entrypoint is intentionally tiny: Inkscape calls it from a custom-GUI
extension, it sends the current SVG path to the resident app, and exits without
touching the active document.
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(__file__))

import inkex

import dataset_state as DSTATE
import deckmaker_app as DMAPP
import log as LOG

_l = LOG


class DeckMakerLauncher(inkex.EffectExtension):
    def _document_path_or_abort(self) -> str:
        doc_path = self.document_path()
        if not doc_path or not os.path.isabs(doc_path) or not os.path.isfile(doc_path):
            raise inkex.AbortExtension("Save the SVG template before launching DeckMaker App.")
        return os.path.normpath(doc_path)

    def effect(self):
        doc_path = self._document_path_or_abort()
        sheet_id = ""
        sheet_range = ""
        try:
            rec = DSTATE.get_gsheet_for_svg(doc_path)
            if rec:
                sheet_id = str(rec.get("sheet_id") or "").strip()
                sheet_range = str(rec.get("sheet_range") or "").strip()
        except Exception:
            import traceback
            _l.w("[deckmaker_launcher] dataset state load failed\n" + traceback.format_exc())

        if not DMAPP.notify_or_launch(doc_path, sheet_id, sheet_range, "global"):
            raise inkex.AbortExtension("Could not launch DeckMaker App.")

        _l.i(f"[deckmaker_launcher] app notified template='{doc_path}'")
        return False


if __name__ == "__main__":
    DeckMakerLauncher().run()
