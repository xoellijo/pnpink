#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inkscape menu launcher for the browser DeckMaker UI."""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(__file__))

import inkex

import deckmaker_launch as LAUNCH
import deckmaker_web as WEB


class DeckMakerWebLauncher(inkex.EffectExtension):
    def effect(self):
        request = LAUNCH.current_request(self)
        if not WEB.notify_or_launch(
            request.template, request.snapshot_path, request.sheet_id, request.sheet_range,
            "global", request.dataset_source_mode, selected_ids=request.selected_ids,
        ):
            raise inkex.AbortExtension("Could not launch DeckMaker Web.")
        return False


if __name__ == "__main__":
    DeckMakerWebLauncher().run()
