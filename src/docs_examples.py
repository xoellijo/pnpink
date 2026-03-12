#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import inkex


class DocsExamples(inkex.EffectExtension):
    def effect(self):
        base = Path(__file__).resolve().parent
        # Avoid webbrowser's detached subprocess warning on some Linux setups.
        if sys.platform.startswith("win"):
            try:
                os.startfile(str(base))  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        elif sys.platform == "darwin":
            subprocess.run(["open", str(base)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        else:
            if shutil.which("xdg-open"):
                subprocess.run(["xdg-open", str(base)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        webbrowser.open_new_tab(base.as_uri() + "/")


if __name__ == "__main__":
    DocsExamples().run()
