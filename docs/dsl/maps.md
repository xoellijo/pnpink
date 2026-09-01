# Maps

PnPInk can generate maps directly from simple source URLs such as `osm://...` and `ofm://...`.

PnPInk resolves the selected area automatically, downloads the needed vector tiles, builds a light layered SVG, and inserts it directly into the document. The result remains compatible with Fit/Anchor and is easy to edit afterwards because it is regular SVG content inside the page.

## Syntax

All map sources use one of these forms:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| @{ osm://[lat1 lon1 lat2 lon2] } |
| @{ ofm://[lat1 lon1 lat2 lon2] } |
| @{ osm://madrid } |
| @{ ofm://spain } |
| @{ osm://spain/z4 } |
| @{ ofm://madrid/z8 } |
| @{ ofm://shikoku/t1 } |
| @{ ofm://shikoku/z10/t2 } |
| @{ ofm://shikoku/z8/t1 view=all-labels+water_name[bay lake] } |
| @{ ofm://asturias view=mountains smooth=3 } |

</div>

- `osm://[...]` and `ofm://[...]`
  - use a bounding box defined by two opposite corners
- `osm://place` and `ofm://place`
  - use a place name
- `/zN`
  - forces a specific zoom level
- `/tN`
  - sets the maximum automatic tile grid per axis
- `view=...`
  - selects which map layers/features are rendered
- `smooth=...` or `s=...`
  - controls cheap curve smoothing/simplification for configured layers

If no zoom is forced, PnPInk chooses it automatically. `/t1` forces the automatic zoom to fit the requested area in one tile, useful when you want to style polygons without tile-boundary strokes.

`/zN` and `/tN` are part of the map URL. `view=` and `smooth=` are Source parameters:

<div class="csv-dataset dataset-body-only dataset-comments" markdown>

|  | Meaning |
| --- | --- |
| @{ ofm://berlin/z12/t4 view=all-labels } | # Forced zoom/tile grid with labels |
| @{ ofm://berlin view=nude+transport+places } | # Combine view presets |
| @{ ofm://berlin view=all-landuses+landuse[residential industrial] } | # Include selected landuse kinds |
| @{ ofm://shikoku view=all-label+water_name[bay lake] } | # Add selected water labels |
| @{ ofm://asturias view=mountains smooth=0 } | # No smoothing |
| @{ ofm://asturias view=mountains smooth=1 } | # Curve every point |
| @{ ofm://asturias view=mountains smooth=4 } | # Roughly 4 points -> 1 curve |
| @{ ofm://asturias view=mountains s=3/2 } | # Roughly 3 points -> 2 curves |

</div>

`view` expressions start from a preset (`all`, `nude`, `water`, `transport`, `places`, etc.) and then apply `+` or `-` modifiers. Feature lists use brackets with spaces, not commas.

`smooth` defaults to `0` because dense curved maps are expensive for Inkscape to render. It only affects layers enabled in `map_style.jsonc` (`line_smoothing.layers` and `line_smoothing.polygon_layers`). Higher values reduce SVG size but may remove detail.

Useful map view presets:

```txt
default     normal map without boundaries
all         all styled layers
nude        clean land/water base
water       water layers and water labels
transport   roads/rail and transport labels
places      places and place labels
landuse     land, landcover, parks, landuse
mountains   peaks and mountain-like landcover
buildings   buildings
boundaries  administrative boundaries
labels      main label layers
```

`mountains` uses provider data such as `mountain_peak ele/rank` and rocky/highland landcover (`bare_rock`, `scree`, `heath`, `grassland`, etc.). It is not DEM/contour data.

Default maximum zoom and tile grid values are configured in `preferences.ini` (`map_osm_max_zoom`, `map_ofm_max_zoom`, `map_osm_max_tile_grid`, `map_ofm_max_tile_grid`).

As a simple rule:

- use `osm://` for a lighter base map
- use `ofm://` for a richer map with more terrain and landcover detail

## Coordinates

Bounding boxes use this order:

<div class="csv-dataset dataset-body-only" markdown>

|  |
| --- |
| [lat1 lon1 lat2 lon2] |

</div>

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
- clips each tile exactly to its tile rectangle
- clips the result to the requested area
- inserts the generated SVG into the document

Downloaded tiles and place lookups are cached automatically in `assets/maptiles/`, so repeated renders of the same areas are much faster.

## Map Content

The generated SVG is layered and grouped, so it can contain examples such as water, rivers, roads, landcover, parks, labels, places, and mountain peaks depending on provider and zoom.

This makes it practical to style or edit parts of the map later inside Inkscape.

## Default Style

The built-in vector map style lives in:

`src/map_style.jsonc`

It controls layer order, label language priority, zoom bands, feature filters, SVG styles, label styles, and label offsets. JSONC comments (`// ...`) are allowed.

Template objects can override generated map styles by Inkscape label:

<div class="csv-dataset dataset-body-only" markdown>

| Inkscape label |
| --- |
| paste-style: water_group* |
| paste-filter: water_*_u4 |

</div>

`paste-style:` copies visual style attributes, including filters, to generated IDs matching the glob pattern. When the target is a group, paint attributes are also applied to descendant geometry. `paste-filter:` copies only the filter. Hyphens and underscores are treated as equivalent in paste patterns.
