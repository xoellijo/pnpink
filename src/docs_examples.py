#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import webbrowser
from pathlib import Path

import inkex


class DocsExamples(inkex.EffectExtension):
    def effect(self):
        base = Path(__file__).resolve().parent
        webbrowser.open_new_tab(base.as_uri() + "/")


if __name__ == "__main__":
    DocsExamples().run()
