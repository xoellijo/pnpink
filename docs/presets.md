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
| Counter38 | counter38, inch38 | 9.525 x 9.525 |
| Counter12 | counter12, inch12 | 12.7 x 12.7 |
| Counter34 | counter34, inch34 | 19.05 x 19.05 |
| Counter1 | counter1, inch1 | 25.4 x 25.4 |
| Dixit | - | 80 x 120 |
| CreditCard | creditcard, cr80, id1, id-1 | 54 x 85.6 |

Also accepted (normalization rules):

- Matching is case-insensitive.
- Accents are normalized.
- Spaces, hyphens, underscores, and dots are ignored.

Examples:

- `STANDARD`, `standard`, `StAnDaRd` -> `Standard`
- `mini usa`, `mini-usa`, `mini_usa` -> `miniUSA`
- `id1`, `ID-1`, `id_1`, `id.1` -> `ID-1`
- `xl-poker`, `XL_POKER`, `xl poker` -> `XL_Poker`
- `counter_1/2`, `counter_1_2`, `COUNTER12` -> `Counter12`
- `inch_3/8`, `inch3_8`, `INCH38` -> `Counter38`
