# Export

The export pipeline operates on the generated SVG output, not on the original template. The usual workflow is:

1. Generate the deck SVG.
2. Optionally split the generated SVG into smaller parts.
3. Export PDF, PDF/X, or another format from the generated SVG or its parts.

## Standard PDF export

Enable `PDF export` in the Export tab to create standard PDF output.

You can select one or more profiles:

| Profile | Typical use |
| --- | --- |
| `default` | General-purpose PDF output. |
| `screen` | Lower weight output for screen review. |
| `ebook` | Compact output for digital distribution. |
| `printer` | Print-oriented PDF. |
| `prepress` | Higher quality print/prepress-oriented PDF. |

The selected profiles are merged and normalized through Ghostscript after Inkscape has produced temporary page or chunk PDFs.

## PDF/X CMYK export

Enable `PDF/X export (CMYK)` when the print provider requires a PDF/X file and CMYK conversion.

Controls:

| Control | Purpose |
| --- | --- |
| `ICC` | Output CMYK ICC profile. Built-in profile names and absolute `.icc` or `.icm` paths are supported. |
| `PDF/X` | Selects `PDF/X-1a`, `PDF/X-3`, or `PDF/X-4`. |
| `Text in pure black` | Preserves text as pure black when supported by the Ghostscript conversion path. |

PDF/X export requires Ghostscript. If the selected ICC profile cannot be resolved, the exporter falls back to the Ghostscript default CMYK profile and writes a warning to the log.

## Raster filters

Filtered SVG content can be expensive or unreliable when exported directly. `Raster filters` controls how PnPInk handles this before PDF creation.

| Mode | Behavior |
| --- | --- |
| `png` | Rasterizes filtered nodes to PNG before PDF export. This is the normal safe mode. |
| `jpeg` | Rasterizes filtered nodes to JPEG. This can reduce file size for photographic content. |
| `png_alfa` | Rasterizes filtered nodes to PNG with alpha support. |
| `inkscape` | Leaves filter handling to Inkscape's PDF exporter. |
| `none` | Does not pre-rasterize filters. Fastest, but filtered output can be wrong or missing. |

The rasterization DPI is derived from the export DPI.

## Other formats

Enable `Other formats` to export page-based output in a non-PDF format.

Supported formats:

`png`, `jpeg`, `jpeg2000`, `pdf`, `svg`, `tiff`, `webp`, `ps`, `eps`, `emf`, `wmf`

Use the `Pages` field to restrict export to specific pages. Syntax examples:

| Pages value | Meaning |
| --- | --- |
| empty | Export all pages. |
| `1` | Export page 1. |
| `1,3-5,8` | Export pages 1, 3, 4, 5, and 8. |

JPEG, TIFF, JPEG2000, and WebP can use a PNG intermediate plus Pillow conversion when that is the most reliable path for the current system.

## Cutting-plotter templates

Enable `Cutting-plotter template` to export cut-only files from the final instance bbox shapes.

Formats:

| Format | Notes |
| --- | --- |
| `svg (vector, cricut)` | Preferred vector output when supported. |
| `dxf (vector, cameo)` | Vector output for Silhouette/Cameo. In Silhouette Studio, use `Simplify` before cutting if imported curves look segmented. |
| `png (raster, all)` | Raster preview/fallback for tools that do not import vectors well. |

The page border is exported in gray for positioning. Cut bbox shapes are exported in red.

For Silhouette Studio, `export_cut_dxf_cameo_scale_fix=1` compensates the observed DXF import scale mismatch. Disable it in `preferences.ini` if your Cameo workflow imports DXF at the correct physical size without it.

## SVG parts

Large generated SVG files can be split into smaller SVG parts. This is configured in the Preferences tab:

| Control | Purpose |
| --- | --- |
| `Split SVG` | Enables chunked SVG output. |
| `Part target MB` | Target size for each SVG part. |

Export detects existing chunked output and reuses it. This avoids rebuilding parts unnecessarily.

Smaller chunks can make Inkscape exports more stable and can increase parallelism, but very small chunks add overhead. For large decks, tune `Part target MB` by measuring the log instead of assuming that the smallest value is fastest.

## Parallelism

The exporter can run multiple Inkscape shell workers in parallel. The preference is:

| Key | Purpose |
| --- | --- |
| `inkscape_shell_workers` | Maximum number of parallel Inkscape shell workers used during export. |

More workers can improve throughput on multi-core machines, but the best value depends on SVG complexity, disk speed, and available memory.

## Automation profile

PnPInk launches Inkscape command-line export processes with an isolated automation environment. This keeps command-line exports from polluting the normal Inkscape `recently-used` data as much as possible.

The `Open SVG` GUI button is different from export: it opens the generated SVG for the user through the configured Inkscape launch path.

## Preferences reference

The main export preferences are stored in `src/preferences.ini`.

| Key | Values | Purpose |
| --- | --- | --- |
| `export_pdf` | `0`, `1` | Enables standard PDF export. |
| `pdf_profiles` | comma-separated `default`, `screen`, `ebook`, `printer`, `prepress` | Standard PDF output profiles. |
| `export_pdfx` | `0`, `1` | Enables PDF/X CMYK export. |
| `pdfx_version` | `1a`, `3`, `4` | PDF/X standard used for CMYK export. |
| `pdf_cmyk_icc` | profile name or path | CMYK ICC profile. |
| `pdf_cmyk_pure_black_text` | `0`, `1` | Preserve text as pure black when supported. |
| `pdf_raster_mode` | `png`, `jpeg`, `png_alpha`, `inkscape`, `none` | Filter rasterization strategy. |
| `export_png` | `0`, `1` | Enables additional non-PDF export. |
| `export_other_format` | supported format name | Format used by `Other formats`. |
| `export_other_pages` | page list | Page filter for additional exports. Empty means all pages. |
| `export_dpi` | integer >= 1 | Inkscape export DPI. |
| `export_jpeg_quality` | 70-95 | JPEG quality. |
| `split_svg_output` | `0`, `1` | Enables generated SVG parts. |
| `split_svg_chunk_mb` | integer >= 1 | Target SVG part size in MB. |
| `inkscape_shell_workers` | integer >= 1 | Parallel export workers. |

## Troubleshooting

If export cannot find the generated SVG, run `Generate` first and check that the output file exists.

If PDF/X export fails, verify that Ghostscript is installed and that the selected ICC profile exists.

If filtered images disappear or look wrong, use raster mode `png` or `png_alfa` before trying faster modes.

If export is slower after enabling SVG parts, increase `Part target MB`. Too many small chunks can cost more time than they save.
