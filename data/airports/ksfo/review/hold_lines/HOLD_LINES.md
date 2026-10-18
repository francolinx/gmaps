# KSFO hold lines: annotations and detected bars

Crops from the FAA PDF (north-up, 500 dpi): red box = detected bar, blue circle = leader tip. `tools/airport_map/hold_review.py`.

| annotation | at (pt) | leader tips | bar found | crop |
|---|---|---|---|---|
| HOLD / CAT 3 | 171, 153 | 2 | yes (bar 8) | `annotation_0_found.png` |
| HOLD / CAT 1 | 235, 196 | 2 | yes (bar 7) | `annotation_1_found.png` |

| bar | dashes | rule | crop |
|---|---|---|---|
| 7 | 3 | collinear dashes, near annotation | `bar_7.png` |
| 8 | 6 | collinear dashes, near leader tip | `bar_8.png` |
