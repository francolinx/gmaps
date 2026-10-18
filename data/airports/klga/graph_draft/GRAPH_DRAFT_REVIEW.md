# KLGA graph DRAFT vs hand-built v1 graph (`klga.graph.json`)

**DRAFT, NOT WIRED.** No runtime code loads `graph_draft/` (a test enforces this). Predictions stay off. Nothing here is reviewed.
The v1 graph keeps geometry null by rule. This draft has coordinates (OSM + NASR) and is **only** a review aid for a future, separately approved graph.

## Draft contents
- 340 nodes, 465 edges (OSM taxiway / taxilane / runway ways split at junctions); edge lengths from the OSM shape.
- 70 hold-short nodes (OSM holding_position nodes and ways, and chart ILS hold lines). 4/4 chart ILS hold lines snapped within 40 m. 10 OSM holds not attached.
- 101 gate lead-in edges (destination only, never a route element).
- 4 NASR runway ends (geometry of record), each tied to the nearest OSM runway-way node: 04 0.6 m, 22 0.8 m, 13 0.8 m, 31 0.9 m.

## Taxiway identifiers
- In both: A, AA, AC, B, BB, C, CC, CY, D, DD, E, F, G, GG, J, K, L, M, N, P, Q, R, S, T, U, V, W, Y, YY, Z
- **Draft only (OSM):** H. **Includes Taxiway H** (KLGA_CHANGES_draft2 item 9: missing from v1).
- **Hand only (v1, not in OSM refs):** none

## Hand-built junction missing from OSM
- **`KLGA:N:D_AA` (D × AA): OSM has no node shared by them; the closest D and AA draft nodes are 33 m apart (`osm:10092290302`, `osm:12572340773`)**

## Hand-built junction nodes: does the draft have the junction?
- `KLGA:N:D_AA` (taxiway_junction, v1 source_supported): **no shared draft node**: KLGA:TWY:D 12 nodes, KLGA:TWY:AA 19 nodes (OSM may join them through an unnamed connector or apron)
- `KLGA:N:D_RWY04-22` (runway_taxiway_junction, v1 source_supported): **yes**, 1 draft node(s) shared by KLGA:TWY:D + KLGA:RWY:04-22 (e.g. `osm:6236284107`)
- `KLGA:N:D_B` (taxiway_junction, v1 source_supported): **yes**, 1 draft node(s) shared by KLGA:TWY:D + KLGA:TWY:B (e.g. `osm:46537589`)
- `KLGA:N:RWY04-22_RWY13-31` (runway_intersection, v1 source_supported): **yes**, 1 draft node(s) shared by KLGA:RWY:04-22 + KLGA:RWY:13-31 (e.g. `osm:1168727438`)
- `KLGA:N:D_BB` (taxiway_junction, v1 ambiguous): **yes**, 1 draft node(s) shared by KLGA:TWY:D + KLGA:TWY:BB (e.g. `osm:45260793`)

## Hand-built segments (all along Delta)
- `KLGA:S:D_AA_TO_RWY`: KLGA:N:D_AA → KLGA:N:D_RWY04-22 on KLGA:TWY:D; v1 source_supported, routing_enabled false. The draft has 11 edges with ref D.
- `KLGA:S:D_RWY_TO_B`: KLGA:N:D_RWY04-22 → KLGA:N:D_B on KLGA:TWY:D; v1 source_supported, routing_enabled false. The draft has 11 edges with ref D.
- `KLGA:S:D_BB_TO_AA`: KLGA:N:D_BB → KLGA:N:D_AA on KLGA:TWY:D; v1 ambiguous, routing_enabled false. The draft has 11 edges with ref D.

## Hand-built hold points vs draft hold-short nodes
- `KLGA:HOLD:D_APPROACH_FROM_AA` (runway_hold_constraint, marking not_located): 4 draft hold-short node(s) on D (osm_holding_position_node ×4, osm_holding_position_way ×3). Which one, if any, is this hold point is a reviewer decision.
- `KLGA:HOLD:D_APPROACH_FROM_B` (runway_hold_constraint, marking not_located): 4 draft hold-short node(s) on D (osm_holding_position_node ×4, osm_holding_position_way ×3). Which one, if any, is this hold point is a reviewer decision.
- `KLGA:HOLD:B_ILS_RWY04` (ils_hold, marking chart_depicted_location_unmeasured): 3 draft hold-short node(s) on B (osm_holding_position_node ×3, faa_chart_ils_hold_line ×1, osm_holding_position_way ×1). Which one, if any, is this hold point is a reviewer decision.
- `KLGA:HOLD:AC_ILS` (ils_hold, marking chart_depicted_location_unmeasured): 1 draft hold-short node(s) on AC (osm_holding_position_node ×1, faa_chart_ils_hold_line ×1). Which one, if any, is this hold point is a reviewer decision.
- `KLGA:HOLD:R_ILS` (ils_hold, marking chart_depicted_location_unmeasured): 4 draft hold-short node(s) on R (osm_holding_position_node ×4, osm_holding_position_way ×4, faa_chart_ils_hold_line ×1). Which one, if any, is this hold point is a reviewer decision.

## Chart ILS hold lines (display-grade georef) → draft
- chart group 6 (near "ILS HOLD"): snapped to `osm:9311571489` at 15.3 m
- chart group 7 (near "HOLD"): snapped to `osm:9311515569` at 2.8 m
- chart group 8 (near "HOLD"): snapped to `osm:9311515570` at 13.8 m
- chart group 9 (near "ILS HOLD"): snapped to `osm:3067763441` at 6.7 m

## Not done (by design)
- Nothing is wired: no routing, no look-ahead, no position inference.
- No turn permissions or direction. Runway protected areas are not modelled. Zones (HS 1, HS 2, Delta crossing) are not drawn.
- The draft is not merged into `klga.graph.json`. Every difference above is a question for a qualified reviewer (`KLGA_CHANGES_draft2.md`).
