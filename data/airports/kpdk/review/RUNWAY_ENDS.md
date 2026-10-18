# KPDK runway ends: along / across-track error vs NASR

Graticule: outline_glyph_decode (decoded from outlines); fit on graticule intersections only, 4 control points, RMS 0.0 m. NASR runway ends are held out.

along = error along the runway, across = perpendicular (m; diagram minus NASR). **|along| > 5 m is flagged as a fit problem, not drafting.**

| end | total m | along m | across m | flag |
|---|---|---|---|---|
| 03L | 16.64 | +16.63 | +0.40 | **flagged (along > 5 m)**: this end only: runway 03L/21R is drawn 16.0 m short at this end, the other end is within 0.9 m and the other runways fit: a chart drawing / extraction issue at this end, not the georeference |
| 03R | 0.99 | +0.82 | +0.56 |  |
| 16 | 5.87 | -5.76 | -1.13 | **flagged (along > 5 m)**: this end only: runway 16/34 is drawn 5.8 m long at this end, the other end is within 0.0 m and the other runways fit: a chart drawing / extraction issue at this end, not the georeference |
| 21L | 0.67 | +0.09 | -0.66 |  |
| 21R | 1.79 | -0.86 | -1.57 |  |
| 34 | 0.53 | +0.04 | -0.53 |  |

| runway | NASR length ft | diagram length ft | length diff m | bearing diff ° |
|---|---|---|---|---|
| 03L/21R | 3746 | 3694 | -16.0 | -0.059 |
| 03R/21L | 6001 | 5998 | -1.0 | -0.003 |
| 16/34 | 3968 | 3987 | +5.8 | -0.078 |

Flagged (|along| > 5 m, Franco Oct 9): 03L: this end only: runway 03L/21R is drawn 16.0 m short at this end, the other end is within 0.9 m and the other runways fit: a chart drawing / extraction issue at this end, not the georeference; 16: this end only: runway 16/34 is drawn 5.8 m long at this end, the other end is within 0.0 m and the other runways fit: a chart drawing / extraction issue at this end, not the georeference
