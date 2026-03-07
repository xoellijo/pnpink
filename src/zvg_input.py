#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import inkex
import zvg_pnp as ZP


class ZvgInput(inkex.InputExtension):
    def load(self, stream):
        return ZP.import_package(self, stream, kind="zvg")


if __name__ == "__main__":
    ZvgInput().run()

