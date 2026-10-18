# KATL (Atlanta): sign-off readiness (Oct 9)

**Status: UNSIGNED.** Predictions are off. This file collects what you need to decide on a display-only sign-off; only you sign.

**Update Oct 9 (after the taxiway-name rules R1-R9):** all 9 checklist items pass. Nothing in the checklist is open. To know before signing:
1. **Chart cycle:** 2610 runs to 0901Z 2026-10-29 (19.7 days left). FAA's next cycle (2611) publishes the KATL diagram unchanged, but a sign-off scope still ends at the cycle, as for KLGA.
2. **Graticule decoded from outlines** (your conditions: unambiguous margin, NASR check, footer): it is decoded, not human-read. Your sign-off covers it (the footer says so).
3. **Across-track runway-end error up to 18.2 m** (along ≤ 0.43 m): fine for display (≤ 30 m); not prediction-grade.
4. **Taxiway names decided by your rules:** 4 chart labels (LC R5, A11 R4, A15 R5, A7 R4) and 8 OSM-only refs (R6, ignored). Each row is in `docs/TAXIWAY_NAMES_REVIEW.md`.

## Checklist (`../review.json`)
| item | status |
|---|---|
| overlay_ink | pass |
| runway_ends | pass |
| runway_identifiers | pass |
| taxiway_names | pass |
| hot_spots | pass |
| ils_hold_lines | pass |
| unclassified | pass |
| cycle_current | pass |
| lahso_points | pass |

- **taxiway_names = pass** (Oct 9): every difference is decided by R1-R7 (first section of `docs/TAXIWAY_NAMES_REVIEW.md`). Two-digit names (A11, B10, L12, ...) were an extraction miss that R6 exposed; they are fixed, which cut the OSM-only list from 35 to 8.
- **ils_hold_lines:** 8 of 8 annotations have their bar (was 5/8). Crops: `hold_lines/HOLD_LINES.md`.
- **lahso_points:** 4 LAHSO callouts / 4 markers (display only).
- **Graticule: decoded from outlines** (exact glyph match; margin rule). Values: 33°39'N, 33°38'N × 84°27'W, 84°26'W, 84°25'W. Verified by the NASR runway-end check below; the map footer says so.

## Runway ends: along / across-track vs NASR (m; `RUNWAY_ENDS.md`)
| end | total | along | across |
|---|---|---|---|
| 08L | 6.76 | +0.10 | -6.76 |
| 08R | 0.13 | -0.02 | +0.13 |
| 09L | 3.31 | -0.05 | +3.31 |
| 09R | 5.18 | -0.14 | +5.18 |
| 10 | 17.75 | -0.32 | +17.75 |
| 26L | 8.87 | +0.39 | +8.87 |
| 26R | 2.92 | +0.43 | +2.89 |
| 27L | 0.69 | +0.04 | +0.69 |
| 27R | 9.87 | +0.23 | -9.87 |
| 28 | 18.23 | -0.06 | -18.23 |

No end has along-track error above 5 m (max 0.43 m), so the fit is sound. The error is across-track (max 18.23 m), i.e. the runways are drawn slightly offset sideways on the chart. Under 30 m (display threshold); it would fail a ~10 m prediction threshold.

## Screenshots (`map_screens/`, each with and without OSM `_osm`)
- `full.png`, `full_osm_on.png`
- Hot spots: `hot_spot_hs1*.png`, `hot_spot_hs2*.png`
- Every hold line: `ils_hold_lines*.png`, `ils_hold_line_2*.png` … `ils_hold_line_9*.png` (9 bars). Before/after crops are in `hold_lines/`.
- End-around taxiways (found from OSM: a taxiway crossing a runway's extended centreline 250–1,500 m beyond its end): `end_around_v_08R*.png` (Taxiway V) and `end_around_w_09L*.png` (Taxiway W)
- Busiest runway-crossing area (most OSM taxiway × runway crossings within 400 m): `busiest_runway_crossings*.png` (HS 1: C and D across both runways)
- Runway-end areas: `runway_ends_*.png`
