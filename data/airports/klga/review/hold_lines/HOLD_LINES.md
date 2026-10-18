# KLGA hold lines: annotations and detected bars

Crops from the FAA PDF (north-up, 500 dpi): red box = detected bar, blue circle = leader tip. `tools/airport_map/hold_review.py`.

| annotation | at (pt) | leader tips | bar found | crop |
|---|---|---|---|---|
| ILS HOLD | 324, 128 | 1 | yes (bar 6) | `annotation_0_found.png` |
| HOLD / ILS | 154, 263 | 2 | yes (bar 7, bar 8, bar 9) | `annotation_1_found.png` |
| ILS HOLD / ILS HOLD | 209, 317 | 2 | yes (bar 9) | `annotation_2_found.png` |

| bar | dashes | rule | crop |
|---|---|---|---|
| 6 | 3 | collinear dashes, near annotation | `bar_6.png` |
| 7 | 2 | collinear dashes, near annotation | `bar_7.png` |
| 8 | 3 | collinear dashes, near annotation | `bar_8.png` |
| 9 | 3 | collinear dashes, near annotation | `bar_9.png` |
