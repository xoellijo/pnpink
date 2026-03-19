# Presets
This page is a lookup reference for standard page and shape identifiers accepted by the parser.

## Page Presets (mm)

| **Preset** | **Size (mm)** |
|------------|---------------|
| A2 | 420 x 594 |
| A3 | 297 x 420 |
| A4 | 210 x 297 |
| A5 | 148 x 210 |
| A6 | 105 x 148 |
| Letter | 215.9 x 279.4 |
| Legal | 215.9 x 355.6 |
| Tabloid | 279.4 x 431.8 |

## Shape Presets
Use these identifiers in `Layout{shape=...}` / `L{s=...}` and related shape-aware workflows.

| **Identifier** | **Aliases** | **Value (mm)** |
|---|---|---|
| Standard | poker, magic, estandard, estandar | 63 x 88 |
| 2.5x3.5inch | - | 63.5 x 88.9 |
| XL_Poker | xlpoker, xlpoker_, xlstandard, xlstandar, xl_standard, xl_standar | 70.875 x 99.0 |
| USA | bridge | 56 x 87 |
| Euro | mini | 59 x 92 |
| Asia | chimera | 57.5 x 89 |
| miniEuro | euromini | 45 x 68 |
| miniAsia | asiamini, minichimera, chimeramini | 43 x 65 |
| miniUSA | usamini | 41 x 63 |
| Tarot | - | 70 x 120 |
| FrenchTarot | - | 61 x 112 |
| Volcano | - | 70 x 110 |
| Wonder | - | 65 x 100 |
| Spanish | baraja | 61 x 95 |
| Desert | - | 50 x 65 |
| squareS | - | 50 x 50 |
| square | - | 70 x 70 |
| squareL | - | 100 x 100 |
| Dixit | - | 80 x 120 |
| CreditCard | creditcard | 54 x 85.6 |
| CR80 | cr80 | 54 x 85.6 |
| ID-1 | id1, id-1 | 54 x 85.6 |

Also accepted (normalization rules):

- Matching is case-insensitive.
- Accents are normalized.
- Spaces, hyphens, underscores, and dots are ignored.

Examples:

- `STANDARD`, `standard`, `StAnDaRd` -> `Standard`
- `mini usa`, `mini-usa`, `mini_usa` -> `miniUSA`
- `id1`, `ID-1`, `id_1`, `id.1` -> `ID-1`
- `xl-poker`, `XL_POKER`, `xl poker` -> `XL_Poker`
