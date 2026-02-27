#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__version__ = 'v_4.0.15-clean-prune-dslblock+dedup-matrix'
import log as LOG
_l = LOG
_l.i('deckmaker ', __version__)

import os, sys
sys.path.append(os.path.dirname(__file__))

import inkex

import engine as ENG
class DeckMaker(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab")
        pars.add_argument("--csv_path", type=str, default="")
        pars.add_argument("--sheet_id", type=str, default="")
        pars.add_argument("--sheet_range", type=str, default="")
        pars.add_argument("--prototypes_layer", type=str, default="Prototypes")
        pars.add_argument("--preset", type=str, default="{A4}")
        pars.add_argument("--stop_on_error", type=inkex.Boolean, default=False)
        pars.add_argument("--log_level", type=str, default="global")

    def _document_path_or_abort(self) -> str:
        doc_path = self.document_path()
        if not doc_path or not os.path.isabs(doc_path) or not os.path.isfile(doc_path):
            raise inkex.AbortExtension("Guarda el documento SVG antes de ejecutar la extensión.")
        return os.path.normpath(doc_path)

    def _find_or_create_layer(self, root, label: str):
        """Find or create a *top-level* Inkscape layer under <svg>.

        We intentionally do NOT reuse layers that are nested inside a template <g>,
        because moving a template group into a descendant layer would create a cycle
        (lxml: ValueError: cannot append parent to itself).
        """
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

    def effect(self):
        # Core pipeline lives in engine.py; keep entrypoint compatible with current .inx.
        ENG.run(self, __version__)


if __name__ == "__main__":
    DeckMaker().run()
