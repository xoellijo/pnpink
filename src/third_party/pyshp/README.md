Vendored `pyshp` fallback for PnPInk OSM land polygons.

Expected file:

- `src/third_party/pyshp/shapefile.py`

How it is used:

- `src/osm.py` first tries normal `import shapefile`
- if that fails, it tries to load this vendored file directly

Recommended source:

- vendored copy of the single-file `pyshp` module (`shapefile.py`)

This avoids needing to install `pyshp` into Inkscape's embedded Python.
