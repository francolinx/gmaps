# KJFK hold lines: annotations and detected bars

Crops from the FAA PDF (north-up, 500 dpi): red box = detected bar, blue circle = leader tip. `tools/airport_map/hold_review.py`.

| annotation | at (pt) | leader tips | bar found | crop |
|---|---|---|---|---|
| CAT 2 HOLD | 357, 329 | 0 | yes (bar 14) | `annotation_0_found.png` |

| bar | dashes | rule | crop |
|---|---|---|---|
| 14 | 3 | collinear dashes, near annotation | `bar_14.png` |
