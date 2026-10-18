# KLGA graph — draft2 change and review report
Dataset version 2026-10-03.draft2. Schema: GMAPS airport-graph 1.0.0, unchanged. All draft1 IDs are kept; draft1 is preserved as klga.graph.draft1.json.

## Chart identity (LGA-FAA-2610)
- **Printed on the chart:** AL-289 (FAA), LAGUARDIA (LGA), NE-2, 01 OCT 2026 to 29 OCT 2026, revision mark 26134.
- **Matches the register:** effective 2026-10-01 to 2026-10-29. The retrieval date (2026-10-03) is inside the printed range.
- **Hash:** sha256 99951fad…4b93. It matches the manifest and is byte-identical to the FAA d-TPP 2610/00289AD.PDF downloaded on 2026-10-02.
- **Scope:** valid for October 2026 only. It is not evidence of the airport layout on 2026-03-22; see KLGA:ISSUE:HISTORICAL.

## Validation
- **Command:** validate_klga_only.py runs the supplied validate.py unchanged, against KLGA only.
- **Result:** 0 errors.
  - All 12 mutation tests reject the bad input.
  - All 7 resolution fixtures pass.
  - 44 KLGA alias targets exist.
- **Not run:** KBOS graph validation, the KBOS PDF hash check, and alias_targets_exist across KBOS. The files they need (kbos.graph.json and the BOS PDF) were not supplied.
- **Gate audit:**
  - all geometry, length_m, width_m and offset_m values are null;
  - every confidence value is null;
  - no routing, prediction, occupancy or is_hold_point gate is true.

## Changes, with evidence
Unless noted, every entry cites LGA-FAA-2610, page 1, and was checked on a 1200-dpi render of the chart.

| Entity | Change | Method / label |
|---|---|---|
| N:D_AA, N:D_RWY04-22, N:D_B, S:D_AA_TO_RWY, S:D_RWY_TO_B | Independently re-checked: D runs continuously from AA, across 4/22, to B. Evidence added to each. | visual_transcription / source_supported |
| N:D_BB (new) | D and BB meet west of AA. | visual / **ambiguous**. Alternatives: D continues west toward the Fire Station, or D ends on the apron. |
| S:D_BB_TO_AA (new) | D segment between BB and AA. Not added to the Delta route. | visual / ambiguous |
| HOLD:B_ILS_RWY04 | Chart text added: "Caution: aircraft taxiing on Twy B for Rwy 4 departure may be instructed to hold at the ILS hold line." | text_transcription |
| HOLD:D_APPROACH_FROM_AA / _FROM_B | Chart readback caution added. Both stay design_only and not_located, with node_id null. | text_transcription + design_convention |
| HOLD:AC_ILS (new) | ILS hold line drawn across AC, protecting 04-22. Location shown on the chart but not measured. | visual / ambiguous |
| HOLD:R_ILS (new) | ILS hold line near R. | visual / ambiguous. Alternatives: the line crosses U instead, or it protects a 13/31 ILS. |
| ZONE:HS1 | Listed labels: 04-22, 13-31, AA, P, R, S. This is label membership only: no order, no connectivity, no polygon. | visual |
| ZONE:HS2 | Listed labels: 04-22, G, Q, B. Same limits as HS1. | visual |
| ROUTE:N_A_E_HOLD04 | Source recovered from Joe's raw notes (auto-captions). At about 4:10 the instruction reads "runway 13 November alpha and echo host runway uh 4"; at 4:21 the readback reads "…November alpha echo short runway 4". **Still unresolved: no graph walk.** | secondary_reference (JOE-NOTES-RAW) |
| Sources | Added JOE-NOTES-RAW and PRIOR-LGA-GRAPH. Both are also in source_register.json. | — |
| Issues | Rewrote PRIOR_GRAPH. Added E_F_LABELS (blocking), LAHSO, FIRE_STATION_ACCESS. | — |

**Deliberately not imported** from my 2026-10-02 prior graph:
- the order of the A/B connectors;
- an E crossing of 04/22, which is contradicted by the F label (see E_F_LABELS below);
- C/D/F/G/P/R crossings beyond Delta;
- access from the Fire Station to D.

**LAHSO:** none is charted on 00289AD, so no LAHSO entries were created.

**Named areas:** terminals, alleys and the Fire Station have no connector nodes.

## Questions for Joe / a qualified reviewer
1. **E vs F (blocking).** On the connector nearest 04/22 east of the runway, the label reads F, and E appears east of B. Where does E actually meet or cross 04/22?
2. **N–A–E route.**
   - Where does the aircraft start: the ramp, or a terminal alley?
   - Which N–A junction applies?
   - Which A segments are used, and where does A meet E?
   - Is "hold short runway 4" at E's crossing of 04/22, or somewhere else?
   - Is the instruction in the captions correct? It was auto-captioned and needs the authenticated audio or transcript.
3. **D west of AA.** Does D continue to the Fire Station, and is there a verified connector?
4. **ILS holds.**
   - For R: does the line cross R or U, and does it protect 04/22 or 13/31?
   - For AC: confirm the hold line and the runway it protects.
5. **HS1/HS2.** What is the actual connectivity among P, R, S, G, Q, AA and B? Which hold lines bound each hotspot?
6. **Hold-line positions** for the two approaches on D. These need measured positions; the chart depiction is not enough.
7. **LAHSO.** Check the current Chart Supplement for LAHSO entries.
8. **March 2026 applicability.** Was there any construction, NOTAM or layout difference on 2026-03-22, compared with the October 2026 chart?
9. **Taxiway H is missing from draft2** (added Oct 8 from A3).
   - The cycle 2610 chart labels a taxiway **H** near Terminal B, at page (309.0, 186.1) in the north-up extraction frame.
   - OpenStreetMap has taxiway `ref=H` (way 664156815) 4.6 m from that label (see `klga/review/OSM_REVIEW.md`).
   - draft2's taxiway list does not include H. **Not edited here.** Franco's plan (Oct 8): the graph will be rebuilt from the reviewed OSM network, with NASR runway ends as the geometry of record and reviewed OSM second. Please confirm H and its connections then.
10. **Numbers 1-19 are gate lead-ins (decided by Franco, Oct 8).**
   - The chart prints numbers 1-19 (except 4, 10, 13) on lead-in lines at the terminals. Class `gate_leadin`, keyed by gate number; not taxiways.
   - **Clearance parsing:** destination only ("to gate 14"), never a route element.
   - **Graph rebuild from reviewed OSM:** the matching OSM ways (refs 1-19; 33 taxilane, 14 taxiway) may become gate-access edges labelled by gate number.
   - **#14** stays chart-only: no OSM taxilane 14; OSM shows gate stands (parking_position 76-79) there.
11. **Hold-short nodes for the future prediction graph (Franco, Oct 8; note only, not built now).**
   - OSM runway-entry ways end at the runway **centreline**.
   - When the graph is rebuilt, the prediction graph needs an **explicit hold-short node at the hold line for every runway entry**, the **ILS hold lines included**.
   - Sources: the chart's `hold_line_candidate` groups (4 ILS hold lines at KLGA) and/or OSM `aeroway=holding_position` (117 elements in the A3 extract).
   - Geometry of record: NASR runway ends, then reviewed OSM.
   - Route edges must stop at the hold-short node; crossing onto the runway is a separate, authorized transition.

## Usable now vs later
**Usable now:**
- Identifier lookup through aliases, including double-letter taxiways, 04 vs 22, and suffix candidates.
- The Delta fragment AA → 04/22 → B, for display and review only.
- Holds and zones as symbolic labels.
- An unresolved-state UI path for N–A–E: show the clearance words, raise no position claim.
- Parsed speech kept as separate states: instruction, readback, reported and inferred.

**Needs reviewed geometry and independent motion first:**
- any position on the airport;
- distance or time to the runway;
- hold-line crossing detection;
- occupancy of a hotspot or protected area;
- route prediction;
- any negative ("no conflict") result.

These require a geometry-capable schema version with a reviewed coordinate frame. They also need fresh surveillance tracks, with identity association, a shared clock and stated uncertainty. Radio alone cannot provide a continuous, precise track.
