# KLGA: A2 georeferencing review (checkpoint 2, Oct 8)

Tool: `python3 tools/airport_map/georef.py --icao KLGA` (stdlib). Output: `data/airports/klga/georef.json`.

Inputs:
- extracted layers (A1 rev 2);
- `data/airports/klga/graticule.json` (values confirmed by Franco);
- NASR cycle **2026-10-01** rows for LGA in `data/airports/klga/sources/nasr/` (APT_BASE, APT_RWY, APT_RWY_END; `PROVENANCE.json` holds the URL, zip sha256 `aba48ea8…` and retrieval time).

## Method
- **Fit (independent of NASR runway ends):** an affine transform from north-up page points to local east/north metres. The frame is centred on the NASR airport reference point (40.77724222, -73.87260555), WGS84, equirectangular. The control points are the 4 intersections of the extracted graticule lines with 40°47'N / 40°46'N and 73°53'W / 73°52'W.
- **Note:** 4 points forming a rectangle are fitted exactly, so the fit residual (0.000 m) says nothing about accuracy. **The accuracy evidence is the held-out comparison below.**
- **Transform:**
  - **Scale:** 7.2267 m per pt on both page axes, which is chart scale **1:20,485**.
  - **Shape:** page axes orthogonal (90.000°) and aligned with true north (0.000°).
- **Diagram runway end** = the end of the black runway bar, identified by the runway-end label beyond it (4, 13, 22, 31). NASR lists no displaced thresholds at LGA, so the bar end and the NASR end are the same physical point.

## Per-runway-end error (diagram vs NASR, metres)
| end | residual | along runway | across runway | Δeast | Δnorth |
|---|---|---|---|---|---|
| 04 | **4.43** | -0.42 | +4.41 | -3.96 | +1.99 |
| 13 | **10.40** | -0.92 | -10.36 | -6.29 | -8.28 |
| 22 | **7.48** | +1.30 | +7.37 | +5.55 | -5.02 |
| 31 | **13.68** | +0.33 | +13.68 | -7.55 | -11.41 |

- **Along** and **across** are relative to the exact NASR end-to-end bearing: 32.131° for 04/22 and 122.140° for 13/31. (NASR's TRUE_ALIGNMENT field is rounded to 32° and 122°; across 2 km that rounding is worth several metres sideways.) Across is positive to the left of that end's direction of travel.
- **Max 13.68 m; every end is within the 30 m display threshold → `runway_ends` PASS.**
- **Not within the ~10 m that predictions will need** (Franco D3): ends 13 (10.4 m) and 31 (13.7 m) would fail it. Predictions stay off regardless (CLAUDE.md).

### What the error is
- **Along-runway error is ≤ 1.3 m at every end.** Diagram runway lengths: 04/22 is 6,999 ft and 13/31 is 7,004 ft, against NASR's 7,002 ft for both (−0.9 m and +0.7 m). Scale and position along each runway are right.
- **Across-runway error is how each runway is drawn:**
  - **04/22** is drawn **0.317° rotated** (bearing 32.448° on the diagram against 32.131°). That is about ±5.9 m at the ends, plus a −1.5 m mean offset.
  - **13/31** is drawn **about 12 m offset to the south-west** (both ends on the same side), plus a 0.089° rotation. 12 m is 1.7 pt on the chart, about 0.6 mm: drafting placement, not a fit problem.

## Decision: geometry of record (Franco, Oct 8)
- Ends **13 (10.40 m)** and **31 (13.68 m)** pass the 30 m **display** threshold but would **fail a ~10 m prediction threshold**.
- **Chart-derived geometry is for DISPLAY only.** Any future prediction logic must use **NASR runway-end coordinates**, and the OSM taxiway network once A3 is reviewed, as the geometry of record, **not chart-traced positions**.
- **Predictions on KLGA stay OFF** regardless, until the checklist passes and Franco signs off. Recorded in `../review.json` → `geometry_of_record`.

## Independent checks
- **Helipad H1, held out:** the diagram's H symbol centre lies **0.94 m** from the NASR H1 position. That's the best check of the transform away from the runways.
- **Comparison only, an affine fitted to the 4 NASR runway ends:** fit RMS 4.83 m, but leave-one-out 35.5 / 13.3 / 15.8 / 61.4 m (04 / 13 / 22 / 31). Three runway ends, two on one runway, make a poorly conditioned affine. This supports using the graticule fit.

## Checklist (`../review.json`)
| item | status |
|---|---|
| overlay_ink | pass (99.771 % covered, 0.334 % extra; re-run Oct 8 after the label fix) |
| runway_ends | **pass** (max 13.68 m ≤ 30 m) |
| runway_identifiers | **pass** (diagram 04, 13, 22, 31 = NASR; NASR also lists helipad H1, which the diagram labels H1) |
| hot_spots | pass |
| ils_hold_lines | pass |
| unclassified | pass |
| taxiway_names | not run (A3) |
| cycle_current | not run (A6) |
| sign-off | not signed (Franco only) |
