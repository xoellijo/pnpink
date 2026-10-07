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

import deckmaker_app as DMAPP
import deckmaker_launch as LAUNCH
import log as LOG

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
    def effect(self):
        try:
            request = LAUNCH.current_request(self)
        except inkex.AbortExtension as error:
            _show_warning("DeckMaker App", str(error))
            raise
        if not DMAPP.notify_or_launch(
            request.template, request.snapshot_path, request.sheet_id, request.sheet_range,
            "global", request.dataset_source_mode,
        ):
            raise inkex.AbortExtension("Could not launch DeckMaker App.")
        _l.i(f"[deckmaker_launcher] app notified template='{request.template}' snapshot='{request.snapshot_path}'")
        return False


if __name__ == "__main__":
    DeckMakerLauncher().run()
