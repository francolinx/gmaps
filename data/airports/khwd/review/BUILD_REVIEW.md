# KHWD (Hayward Executive): generalization test of `tools/build_airport.py` (Oct 8)

**Command:** `python3 tools/build_airport.py KHWD` (add `--screens` for screenshots). It runs end to end with **no KHWD-specific code**. Everything KHWD broke was fixed in the generic tools or the style file, and KLGA was re-run after each fix (see "KLGA unaffected").

**Map:** `http://localhost:8765/airport_map.html?icao=khwd`

## Inputs (found and fetched automatically)
- **FAA d-TPP cycle in effect: 2610** (0901Z 10/01/26 to 0901Z 10/29/26).
  - The APD `05015AD.PDF` ("HAYWARD EXEC", alnum 5015) is saved as `data/airports/sources/hwd_faa_2610_05015AD.pdf`, sha256 `a5006db9…`.
  - Source record: `source_chart.json`. The chart code "AL-5015 (FAA)" is read from the PDF text.
- **NASR cycle 2026-10-01:** only HWD's rows are committed (1 base, 2 runways, 4 ends). 10R/28L has displaced thresholds (826 / 676 ft); NASR's end coordinates are the physical ends.
- **OSM:** Overpass, data timestamp 2026-10-09T02:40:42Z, 172 elements. Kept in `osm/` with ODbL attribution and provenance.

## Results
| stage | result |
|---|---|
| extraction | 1,108 objects, **0 unclassified**; 2 runways; hot spots HS 1-5 (HS 5 has 2 callouts); runway ends 10L, 10R, 28L, 28R; elevations 28, 37, 52 |
| graticule | **read from the PDF text layer**: 37°40.0'N, 37°39.5'N × 122°08.0'W, 122°07.5'W, 122°07.0'W (decimal minutes; 3 meridians) |
| georef | chart scale 1:12,990; **6 control points, fit RMS 0.019 m**. This is a real consistency check that KLGA's exact 4-point fit could not give |
| runway ends vs NASR | **10L 11.83 m, 10R 10.13 m, 28L 2.65 m, 28R 1.42 m**; along-runway ≤ 1.2 m; lengths 3,109 vs 3,108 ft and 5,694 vs 5,694 ft |
| runway drawing | 10L/28R drawn −0.65° rotated, 10R/28L +0.41°: the across-runway residuals |
| overlay | **99.765 % covered, 0.309 % extra**; 100 % of both residues is in the two "SW-2 …" cycle banners (substitute typeface) |
| OSM names | matched A, A1, B, C, D, E, F, Z, Z1; conflicting none; OSM-only none; chart-only none ("GA" from "GA TRANSIENT RAMP" is an `area_label` since the overnight run, see VISUAL_REVIEW.md) |
| cycle | current, 20.3 days left; FAA lists the same APD; served PDF has the same sha256; next cycle 2611 publishes it unchanged |

## Checklist (`../review.json`)
| item | status |
|---|---|
| overlay_ink | pass |
| runway_ends | pass (max 11.83 m ≤ 30 m) |
| runway_identifiers | pass |
| taxiway_names | pass (overnight: "GA" is an area label by the generic rule; see VISUAL_REVIEW.md) |
| hot_spots | pass (6 callouts = 6 outlines) |
| ils_hold_lines | **not_applicable** (no ILS HOLD annotation on this chart) |
| unclassified | pass |
| cycle_current | pass |
| sign-off | **not signed** (Franco only); predictions off; display-only rule from the template (`geometry_of_record`) |

Not applicable here, so not reported: 10L and 10R would also fail a ~10 m prediction threshold (11.83 / 10.13 m), like KLGA 13 and 31.

## Screenshots (`map_screens/`)
`full.png`, `full_osm_on.png`, `runway_ends_10L_10R.png` (west ends, hold areas), `runway_ends_28L_28R.png` (east ends, hold areas, HS 2 and HS 5).

## Generic fixes this test forced (the real result)
1. **`dtpp.py` (new):** the d-TPP cycle in effect and the airport's APD record from FAA's metafile; downloads the PDF; writes `source_chart.json`. (KLGA's source record had been hand-assembled.)
2. **`nasr.py` (new):** the NASR cycle in effect from FAA's index page; caches the APT CSV zip outside the repo; commits only the airport's rows. Regenerates KLGA's three CSVs byte-identically, and keeps the existing PROVENANCE when source and rows are unchanged.
3. **`checklist.py` (new):** one checklist template for every airport, including the display-only geometry-of-record rule. It never overwrites an existing `review.json`.
4. **`graticule.py` (new):** reads graticule values from the PDF text layer and ties each to its line. It refuses when the chart prints them as outlines only (KLGA keeps its human-confirmed file).
5. **Style file:** new label class `graticule` (`37°40.0'N` and `40°47'N` forms).
6. **Extractor, graticule lines:** a line set also qualifies when a graticule text label sits at its end (KHWD's lines stop at their labels, short of the frame).
7. **Style file, `frame` rule:** closed shapes only. KHWD's 37°40.0'N parallel spans the chart and was being classed as a second frame.
8. **Extractor, numbers:** elevations and gate lead-ins must be pure digits ("10L" next to "ELEV 28" had been classed as an elevation). A suffixed number that is not a runway end is `runway_reference` ("28L RUN-UP").
9. **Extractor + build_map, hot spots:** outlines are brown stroked curves of any shape: circles, ellipses, or open arcs where the leader joins (≥ 15 pt, aspect ≤ 6; curved leaders are thinner). The checklist compares label **callouts** with outlines, so HS 5's two callouts need two outlines.
   - *My first version of this rule required closed paths. That flipped KLGA's signed `hot_spots` item to fail (its circles are open arcs). I caught it on the KLGA re-run, before committing, and fixed it.*
10. **Extractor, ILS hold lines:** `not_applicable` when the chart has no ILS HOLD annotation (previously a fail). The sign-off rule accepts pass or not_applicable.
11. **Extractor, closed-pavement marks:** chains of X marks (≥ 3 X's, any rotation; the text layer splits them into "XXX" + "X") are `closed_marking`, not taxiway names.
12. **OSM cross-check, callouts:** a letter on a small filled symbol badge is a chart callout, like circled letters (the solid "L" = pilot-controlled lighting).
13. **`georef.py`:** accepts any number of graticule lines (all parallel × meridian intersections are control points) and text-layer graticules with explicit line positions; records the graticule source, the control-point count and the fit RMS; refuses an unconfirmed transcription.
14. **`provenance.py`:** prefers `source_chart.json`; reads the chart code from the PDF; graticule provenance records source / confirmation / read time.
15. **`build_map.py`, review views from data:** runway-end hold areas (ends grouped by runway number: 10L+10R, 28L+28R), runway intersections, terminals, hot spots, ILS hold lines. Previously it hard-coded KLGA's Terminal B, HS 1 and first hold line.
16. **`dtpp.py` bug fixed during the run:** an `iterparse` iterator used without a reference closed its file mid-read.
17. **`tools/build_airport.py` (new):** the orchestrator; each stage is a standalone tool, and the first failure stops the build.

## KLGA unaffected (re-run through the same pipeline)
- Extraction: object and label counts identical. Overlay: 99.771 % / 0.296 % (identical).
- Georef: identical transform; 04 4.43, 13 10.40, 22 7.48, 31 13.68 m; H1 0.94 m (identical).
- OSM cross-check summary identical. Footer and chart provenance identical.
- Signed `review.json`: every status, the sign-off and the geometry-of-record decision unchanged. Two result details changed shape: hot_spots now reports `label_callouts`/`shapes` (2/2) instead of `circles`, and cycle_current refreshed `days_left`.
