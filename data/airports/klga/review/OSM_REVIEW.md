# KLGA: A3 OpenStreetMap layer + taxiway-name cross-check (Oct 8)

Tool: `python3 tools/airport_map/osm.py {fetch|import|build} --icao KLGA` (stdlib).

Data: `data/airports/klga/osm/` (its own folder; **© OpenStreetMap contributors, ODbL 1.0**, see `osm/ATTRIBUTION.md`):
- `overpass_raw.json`: 420 elements; sha256 `20a75e1e…`.
- `query.overpassql`: the exact query sent, a bounding box 40.7600,-73.8950 → 40.7920,-73.8480 for aeroway taxiway / taxilane / runway / holding_position / apron / parking_position / stopway ways, plus holding_position / runway_end nodes.
- `PROVENANCE.json`: endpoint overpass-api.de, requested 2026-10-09T00:32:22Z, **OSM data timestamp 2026-10-09T00:31:15Z**.
- `osm_aeroways.geojson`: every element with its tags.

Not merged with the chart-derived layers (tested). `provenance.json` carries the OSM block for the map view's attribution.

Method: each chart taxiway label (A1 extraction) is placed on the ground with the A2 transform (`georef.json`). It **matches** if an OSM taxiway or taxilane with the same `ref` runs within 60 m of the label centre. Labels inside a circle symbol are chart callouts, not taxiway names, and are excluded: A1 ×1, A5 ×2 (approach lights), H ×2 (helipad), P ×4, D ×1 (circled/boxed symbols).

Full detail: `review/osm_names_report.json` (84 chart taxiway-label instances after the decisions below; nearest OSM ways with distances).

## Franco's decisions (Oct 8) and what changed
1. **Stray A, B, C → `terminal_label`.** Crops: `crops/terminal_label_A_1.png`, `terminal_label_B_0.png`, `terminal_label_C_2.png`. Each letter sits 2.8-3.4 pt from a "TERMINAL" label whose leader points at the terminal building, so they name Terminals A, B, C, not taxiways.
   - Rule (extraction post pass, `post.terminal_label_gap` = 6 pt): a single capital letter within 6 pt of a "TERMINAL" label. It matches exactly these three.
2. **H1 → `helipad_label`** (label rule `^H[0-9]+$`), excluded from the taxiway-name check. Its 0.94 m georef check (diagram H symbol vs NASR H1) is unchanged.
3. **Numbers 1-19 → `gate_leadin` (decided by Franco, Oct 8).** They are gate lead-ins, keyed by gate number, **not taxiway names**.
   - **Clearance parsing** may use them **only as a destination** ("to gate 14"), **never as a route element**.
   - **Future graph** (rebuilt from reviewed OSM): the matching OSM ways (47: 33 taxilane, 14 taxiway, refs 1-19) may become **gate-access edges labelled by gate number**.
   - **#14 stays chart-only**, with this note: no OSM taxiway/taxilane 14; OSM shows gate stands (parking_position 76-79) there.
   - Crops: `crops/numbered_5.png`, `numbered_9.png`, `numbered_16.png`, `numbered_14.png`. On 5, 9 and 16 the OSM way with the same ref follows the chart's lead-in line.
4. **Taxiway H** added to the qualified-reviewer list in `data/airports/KLGA_CHANGES_draft2.md` (item 9); the graph is not edited. The graph will be rebuilt later from the reviewed OSM network, with NASR runway ends as the geometry of record and reviewed OSM second.

## Summary after the decisions (nothing auto-resolved)
| category | names |
|---|---|
| **matched** (every instance within 60 m of the same OSM ref) | A, AA, AC, B, BB, C, CC, CY, D, DD, E, F, G, GG, **H**, J, K, L, M, N, P, Q, R, S, T, U, V, W, Y, YY, Z (31) |
| conflicting | none |
| chart-only | none |
| OSM-only (letter refs) | none |
| **gate lead-ins** (not taxiway names) | chart gates 1, 2, 3, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19 vs OSM refs 1-19 (no 4, 10, 13, 14); #14 chart-only |
| excluded from the check | terminal labels A, B, C; helipad H1; circled chart callouts A1, A5, H, P, D |

38 OSM taxiway/taxilane ways have no `ref` (26 taxiway, 12 taxilane).

## Extra: OSM runway geometry vs NASR (information for the geometry of record)
| end | OSM runway endpoint → NASR end |
|---|---|
| 04 | 0.60 m |
| 13 | 0.82 m |
| 22 | 0.81 m |
| 31 | 0.93 m |

OSM's runway centrelines (ways 5782325 for 04/22, 5782302 for 13/31) end within **1 m** of NASR, far closer than the chart-derived ends (4.4-13.7 m). That is consistent with the geometry-of-record decision: NASR ends first, then reviewed OSM.

## Checklist
`taxiway_names` = **pass**: all 31 letter-named taxiways match, nothing is conflicting, chart-only or OSM-only, and the numbers are classified as gate lead-ins (Franco). **The checklist stays unsigned until Franco signs.**
