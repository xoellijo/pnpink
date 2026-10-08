#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tk browser selector for Deckmaker Web."""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(__file__))

import inkex

import prefs
from web_app import detected_browsers


class BrowserPreferences(inkex.EffectExtension):
    def effect(self):
        import tkinter as tk
        from tkinter import ttk

        browsers = detected_browsers()
        by_label = {browser["label"]: browser for browser in browsers}
        by_id = {browser["id"]: browser for browser in browsers}
        current = by_id.get(prefs.get_deckmaker_web_browser(), by_id["system"])

        root = tk.Tk()
        root.title("PnPInk Preferences — Browser")
        root.resizable(False, False)
        root.columnconfigure(0, weight=1)

        frame = ttk.Frame(root, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Deckmaker Web browser").grid(row=0, column=0, sticky="w")
        selection = tk.StringVar(value=current["label"])
        selector = ttk.Combobox(
            frame,
            state="readonly",
            textvariable=selection,
            values=[browser["label"] for browser in browsers],
            width=34,
        )
        selector.grid(row=1, column=0, sticky="ew", pady=(5, 3))

        path = tk.StringVar()
        path_label = ttk.Label(frame, textvariable=path, foreground="#666666", wraplength=380)
        path_label.grid(row=2, column=0, sticky="w", pady=(0, 12))

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="e")

        def update_path(*_args):
            browser = by_label.get(selection.get(), by_id["system"])
            path.set(browser["path"] or "Use the operating system default browser")

        def save():
            browser = by_label.get(selection.get(), by_id["system"])
            prefs.set_deckmaker_web_browser(browser["id"])
            root.destroy()

        ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Save", command=save).grid(row=0, column=1)
        selector.bind("<<ComboboxSelected>>", update_path)
        root.bind("<Escape>", lambda _event: root.destroy())
        root.bind("<Return>", lambda _event: save())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        update_path()
        root.update_idletasks()
        width = root.winfo_reqwidth()
        height = root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.mainloop()
        return False


if __name__ == "__main__":
    BrowserPreferences().run()
