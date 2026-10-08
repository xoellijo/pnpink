"""Compatibility helpers for the Inkex versions supported by PnPInk."""

try:
    from inkex import etree
except ImportError:
    from lxml import etree


__all__ = ["etree"]
