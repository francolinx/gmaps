# KBOS graph: draft2 change and review report
Dataset version 2026-10-03.draft2. It uses GMAPS airport-graph schema 1.0.0, unchanged. Every draft1 KBOS ID is kept, and draft1 is preserved as kbos.graph.draft1.json.

## Source (BOS-FAA-UPLOAD)
The chart prints the following, each kept in its own metadata field:
- Airport and chart: AL-58 (FAA), GENERAL EDWARD LAWRENCE LOGAN INTL (BOS)
- Printed cycle: NE-1, 01 OCT 2026 to 29 OCT 2026
- Revision mark: 26134

The PDF's sha256 is 36121d22…de15. It matches the register, and validate.py checked it with the file present. The retrieval date, 2026-10-03, falls inside the printed range.

This chart is the October 2026 depiction only. It is not evidence of the June 2026 event geometry, and not of live status.

## Validation (completed artifact checks)
- **`validate.py --self-test`**, run unchanged against both airports with all source files present:
  - 0 errors for KBOS and 0 for KLGA. KLGA is the draft2 file.
  - 32 of 32 self-tests pass: 12 mutation tests, alias targets, and 18 resolver fixtures (7 original and 11 new).
- **`bos_extra_tests.py`** adds 15 more checks. All of them pass:
  - a missing geometry key or a missing length key is rejected;
  - an invented hold offset is rejected;
  - a LAHSO hold placed at the runway intersection node is rejected;
  - a hold approached from inside the protected runway is rejected;
  - a cross-airport ID or endpoint is rejected;
  - merging 33L into 15L/33R is rejected, and so is dropping the 33L suffix;
  - setting HS 4 as usable for occupancy is rejected;
  - a route that grants authority is rejected, and so is turning automatic routing on;
  - a metric distance query is refused, because v1 has no geometry.
- **Known gap:** the v1 schema accepts a numeric `confidence` value between 0 and 1, but SCHEMA_GUIDE says it must be null. I did not change the schema. Instead, an audit confirms every confidence value is null in both graphs.
- **Gate audit:**
  - all geometry, length, width, offset and hold node_id values are null;
  - no routing, prediction, occupancy, authority or navigation gate is true;
  - no review is marked accepted.

## Resolver tests
| Input | Result |
|---|---|
| `runway 27`, `runway two seven` | END:27 |
| `runway 33 left`, `runway three three left` | END:33L |
| `runway 33`, `runway three three` | Candidates 33L **and** 33R (ambiguous, missing suffix) |
| `runway 15` | 15L and 15R |
| `delta` | TWY:D only, never D1 |
| `delta 1`, `delta one` | TWY:D1 |
| `delta two` | TWY:D2 |
| KLGA + `delta two` / `runway 33 left` | No result (rejected across airports) |

**Fixed:** before this pass, `runway 33 left`, `33 left`, `runway 33` and `delta 1` returned nothing. Those digit-plus-spoken-word forms are what Whisper typically outputs. I added parser aliases for every KBOS L/R runway end and every numbered KBOS taxiway, in both the graph and identifier_aliases.json. These are parser conveniences, not chart facts.

## Topology: independent re-check of every junction and segment
I checked each item on a 1200-dpi render of the chart.

| Entity | Result |
|---|---|
| N:RWY09-27_RWY15R-33L | Confirmed. Physical 09/27 (7001×150) crosses physical 15R/33L (10083×150). 27 and 33L are direction names for those two runways. 15L/33R (2557×100) is a separate runway further north and is not merged. |
| N:D_RWY15R-33L | Confirmed. D crosses 15R/33L inside HS 4, northwest of the runway crossing. |
| N:D_D1, N:D_D2 | Confirmed. D1 is west of D2. |
| N:D1_RWY09-27, N:D2_RWY09-27 | Confirmed. Both connectors reach the north edge of 9/27. |
| S:D_RWY15_TO_D1, S:D_D1_TO_D2 | Confirmed. No other branch is visible along either stretch. |
| S:D1_TO_RWY09, S:D2_TO_RWY09 | Confirmed. The chart does not draw hold markings on these connectors. |
| S:RWY09_X_TO_D1, S:RWY09_D1_TO_D2 | Confirmed. No connector is visible on either side of the runway along these stretches. |
| S:RWY15_D_TO_X | Confirmed. The LAHSO line lies on this stretch, so it must be split at reviewed boundaries before any routing. |

## Changes
- **HOLD:LAHSO_RWY15R_BEFORE09**
  - **What the chart shows:** the dashed LAHSO line runs perpendicular to 15R/33L. It crosses 15R/33L between the D crossing and the 9/27 crossing, on the northwest side.
  - **Approach side now set:** segment RWY15_D_TO_X, approached from node D_RWY15R-33L.
  - **Label:** ambiguous. The primary reading is "runway 15R landings hold short of 09/27", because an aircraft landing on 33L reaches 9/27 before it reaches this line. The chart does not show whether LAHSO is currently available, what its conditions are, or the available landing distance.
- **HOLD:D_EAST_APPROACH_RWY15R33L (new)**
  - D approaching 15R/33L from the D1 side.
  - design_only, not_located, node_id null.
  - Needed so the route fragment run in reverse has a hold before 15R/33L.
- **ZONE:HS4**
  - Now lists its labels only: 09/27, 15R/33L, C, D, plus the D crossing node.
  - The ellipse sits **west** of the runway crossing; the crossing's centre appears just outside its east edge. Not usable as an occupancy area.
- **Evidence:** 1200-dpi re-check evidence was added to all 6 nodes, all 7 segments, both zones and the route.
- **Issues:**
  - New: D_EAST_END (unlabeled pavement east of D2 that reaches 9/27 near the 27 threshold); D_WEST_SIDE (C branches off D west of 15R/33L and crosses 9/27 inside HS 4); HS4_EXTENT (blocking); ALIASES.
  - Updated: LAHSO, HOLDS, HISTORICAL. HISTORICAL now says that incident distances and timings must not be used as geometry.

**Deliberately not added:**
- the C crossing of 9/27;
- the D/C junction;
- the unlabeled leg east of D2;
- a west-side D hold;
- any LAHSO distance.

## Expert-review queue
1. **LAHSO line:** confirm it is for 15R arrivals holding short of 09/27, and check its current availability and conditions in the Chart Supplement.
2. **Hold-line positions:** where are the hold lines on D1 and D2 (for 9/27), and on D at both sides of 15R/33L?
3. **HS 4:** what hazard does it describe, and what protected area should conflict logic use?
4. **Unlabeled pavement east of D2:** is it D, D2 or unnamed?
5. **West side of 15R/33L:** what is the D/C junction order there?
6. **Applicability:** does the October chart match June 2026, and are there current NOTAMs or closures?

## Before any predictive demo
- **Map:** either a reviewed metric map, or a separate map clearly labelled as a fictional simulation.
- **Tracks:** independent, timestamped tracks with identity association, a shared clock and stated uncertainty.
- **What can't substitute:** neither radio words nor the PDF can supply position, speed or ETA.

**This draft is not for navigation.**
