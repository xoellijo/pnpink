#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import inkex
import zvg_pnp as ZP


class PnpOutput(inkex.OutputExtension):
    def save(self, stream):
        ZP.export_package(self, stream, kind="pnp")


if __name__ == "__main__":
    PnpOutput().run()

