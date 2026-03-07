#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import inkex
import zvg_pnp as ZP


class PnpInput(inkex.InputExtension):
    def load(self, stream):
        return ZP.import_package(self, stream, kind="pnp")


if __name__ == "__main__":
    PnpInput().run()

