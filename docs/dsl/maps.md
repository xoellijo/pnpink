# Maps

PnPInk can generate maps directly from simple source URLs such as `osm://...` and `ofm://...`.

PnPInk resolves the selected area automatically, downloads the needed vector tiles, builds a light layered SVG, and inserts it directly into the document. The result remains compatible with Fit/Anchor and is easy to edit afterwards because it is regular SVG content inside the page.

## Syntax

All map sources use one of these forms:

```txt
@{ osm://[lat1 lon1 lat2 lon2] }
@{ ofm://[lat1 lon1 lat2 lon2] }

@{ osm://madrid }
@{ ofm://spain }

@{ osm://spain/z4 }
@{ ofm://madrid/z8 }
```

- `osm://[...]` and `ofm://[...]`
  - use a bounding box defined by two opposite corners
- `osm://place` and `ofm://place`
  - use a place name
- `/zN`
  - forces a specific zoom level

If no zoom is forced, PnPInk chooses it automatically.

As a simple rule:

- use `osm://` for a lighter base map
- use `ofm://` for a richer map with more terrain and landcover detail

## Coordinates

Bounding boxes use this order:

```txt
[lat1 lon1 lat2 lon2]
```

The two points are opposite corners of the rectangle.

You can obtain coordinates easily from:

- OpenStreetMap: Open the map, go to `Export`, and inspect the selected area
- Google Maps: right click on a point and copy the coordinates

## What PnPInk Automates

You only provide the area or the place name.

PnPInk automatically:

- resolves the place when needed
- chooses an appropriate zoom if you do not force one
- downloads the necessary tiles
- joins adjacent tiles
- clips the result to the requested area
- inserts the generated SVG into the document

## Map Content

The generated SVG is layered and grouped, so it can contain examples such as water, rivers, roads, landcover, parks, labels, places, and mountain peaks depending on provider and zoom.

This makes it practical to style or edit parts of the map later inside Inkscape.
