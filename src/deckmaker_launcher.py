#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Menu launcher for the resident DeckMaker app.

This entrypoint is intentionally tiny: Inkscape calls it from a custom-GUI
extension, it sends the saved SVG path plus a snapshot of the active document
to the resident app, and exits without touching the active document.
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(__file__))

import inkex

import dataset_state as DSTATE
import deckmaker_app as DMAPP
import log as LOG
import temp_paths as TEMPPATHS

_l = LOG


def _show_warning(title: str, message: str):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showwarning(title, message, parent=root)
        root.destroy()
        return
    except Exception:
        pass
    try:
        inkex.errormsg(message)
    except Exception:
        pass


class DeckMakerLauncher(inkex.EffectExtension):
    def _document_path_or_abort(self) -> str:
        doc_path = self.document_path()
        if not doc_path or not os.path.isabs(doc_path) or not os.path.isfile(doc_path):
            msg = "Save the SVG template before launching DeckMaker App."
            _show_warning("DeckMaker App", msg)
            raise inkex.AbortExtension(msg)
        return os.path.normpath(doc_path)

    def _write_current_document_snapshot(self, doc_path: str) -> str:
        snap_dir = TEMPPATHS.named_dir("deckmaker_snapshot", stem=TEMPPATHS.stem_for_path(doc_path))
        snap_path = os.path.join(snap_dir, "current.svg")
        raw = inkex.etree.tostring(self.document)
        tmp_path = snap_path + ".tmp"
        with open(tmp_path, "wb") as fh:
            fh.write(raw)
        os.replace(tmp_path, snap_path)
        return os.path.normpath(snap_path)

    def effect(self):
        doc_path = self._document_path_or_abort()
        snapshot_path = self._write_current_document_snapshot(doc_path)
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

        if not DMAPP.notify_or_launch(doc_path, snapshot_path, sheet_id, sheet_range, "global"):
            raise inkex.AbortExtension("Could not launch DeckMaker App.")

        _l.i(f"[deckmaker_launcher] app notified template='{doc_path}' snapshot='{snapshot_path}'")
        return False


if __name__ == "__main__":
    DeckMakerLauncher().run()
