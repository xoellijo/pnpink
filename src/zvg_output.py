#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import inkex
import zvg_pnp as ZP


class ZvgOutput(inkex.OutputExtension):
    def save(self, stream):
        ZP.export_package(self, stream, kind="zvg")


if __name__ == "__main__":
    ZvgOutput().run()

