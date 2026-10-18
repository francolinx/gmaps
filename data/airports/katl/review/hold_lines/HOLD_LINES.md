# KATL hold lines: annotations and detected bars

Crops from the FAA PDF (north-up, 500 dpi): red box = detected bar, blue circle = leader tip. `tools/airport_map/hold_review.py`.

| annotation | at (pt) | leader tips | bar found | crop |
|---|---|---|---|---|
| ILS HOLD | 434, 247 | 1 | yes (bar 30) | `annotation_0_found.png` |
| ILS HOLD | 284, 111 | 1 | yes (bar 31) | `annotation_1_found.png` |
| ILS HOLD | 224, 61 | 1 | yes (bar 26, bar 32) | `annotation_2_found.png` |
| HOLD / ILS | 479, 86 | 1 | yes (bar 14) | `annotation_3_found.png` |
| HOLD / ILS | 186, 119 | 1 | yes (bar 32) | `annotation_4_found.png` |
| HOLD / ILS | 465, 68 | 1 | yes (bar 14, bar 28) | `annotation_5_found.png` |
| ILS HOLD | 394, 202 | 0 | yes (bar 27, bar 30) | `annotation_6_found.png` |
| HOLD / ILS | 135, 241 | 6 | yes (bar 0, bar 29) | `annotation_7_found.png` |

| bar | dashes | rule | crop |
|---|---|---|---|
| 0 | 2 | collinear dashes, near leader tip | `bar_0.png` |
| 14 | 3 | collinear dashes, near annotation | `bar_14.png` |
| 26 | 2 | collinear dashes, near annotation | `bar_26.png` |
| 27 | 2 | collinear dashes, near annotation | `bar_27.png` |
| 28 | 2 | collinear dashes, near leader tip | `bar_28.png` |
| 29 | 2 | collinear dashes, near annotation | `bar_29.png` |
| 30 | 1 | leader tip (single or bent dashes) | `bar_30.png` |
| 31 | 1 | leader tip (single or bent dashes) | `bar_31.png` |
| 32 | 2 | leader tip (single or bent dashes) | `bar_32.png` |
