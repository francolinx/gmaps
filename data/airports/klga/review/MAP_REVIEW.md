# KLGA: A4 map view review (Oct 8)

**Open:** `http://localhost:8765/airport_map.html?icao=klga` (local UI server, served from `ui_scaffold/`; also linked from `nav.html`).

## Build
- `python3 tools/airport_map/build_map.py --icao KLGA` writes `data/airports/klga/map/{chart.svg, map_meta.json}` and copies them, plus the OSM taxiway lines in their own `osm/` subfolder with `ATTRIBUTION.md`, to `ui_scaffold/maps/klga/`.
- **Georeferenced:** SVG units are metres east / south of the NASR airport reference point, north up. The chart geometry goes through the A2 transform (graticule fit), and the OSM lat/lon is projected into the same local frame.
- **What's drawn:** chart objects inside the chart frame, in original FAA styling and paint order, as **one paint-ordered group**. Margin titles and the cycle-date banners are not drawn; their content is in the footer.
- **Layer toggles:** CSS classes `L-chart` (Chart linework) and `L-labels` (Chart labels, outline lettering plus the HS 1/HS 2 text), and `layer-osm` (OSM taxiways). An earlier two-group version painted the black "D" box over its white letter; one group fixes that.
- **Map-only cosmetic:** pavement fills get a same-colour 0.25 pt stroke to hide anti-aliasing seams. `faithful.svg`, used by the overlay check, is unchanged.

## Viewer (`ui_scaffold/airport_map.html`)
- **Layers:** Chart linework, Chart labels (both on), **OSM taxiways (off by default)**. When OSM is on, the footer shows "© OpenStreetMap contributors, ODbL 1.0 · OSM data 2026-10-09T00:31:15Z · kept separate from the FAA-derived layers".
- **Footer:** the stored provenance line (`provenance.json` → `display.footer`): FAA chart AL-289, d-TPP cycle 2610, effective 2026-10-01 to 2026-10-29, the PDF sha256, the FAA URL, and "Display only. Not for navigation. No predictions."
- **Navigation:** pan (drag), zoom (wheel / + − ⤢), a scale bar, a true-north mark, and a lat/lon cursor readout labelled "from the chart: display only". URL parameters `?view=x,y,w,h` (metres) and `?osm=1` exist for screenshots.
- **What it doesn't do:** no predictions, tracks, positions or replay on this page.
- **No debug text layer and no external resources** (tested).

## Screenshots (`review/map_screens/`)
| file | view |
|---|---|
| `full.png`, `full_osm_on.png` | whole chart; OSM on shows the ODbL attribution |
| `runway_intersection.png` (+ `_osm`) | 04/22 × 13/31 |
| `terminal_b.png` (+ `_osm`) | Terminal B, TWR, Air Carrier Maintenance |
| `hot_spot_hs1.png` (+ `_osm`) | HS 1 / HS 2 circles at the intersection |
| `ils_hold_lines.png` (+ `_osm`) | the ILS HOLD north-east of the intersection, its dashed hold line and the light symbol |

## Checks
- `overlay_ink` re-run after the A4 visible-text fix: **99.771 % covered, 0.296 % extra, pass.** HS 1/HS 2 are no longer drawn twice; their letters are brown outlines, now recognised as lettering.
- Tests: `airport map view` (footer = stored provenance line; OSM off by default with ODbL attribution in its own folder; chart.svg has no OSM data, banner or debug layer; local only), plus the earlier map/provenance/OSM-separation checks. Suite **66/66**.

## Follow-up fixes (Franco's A4 review, Oct 8)
- **Scale bar, cursor readout and north mark:** opaque white backing with a thin border and shadow. At the 100 m zoom the scale bar no longer runs into "TO RUNWAY CROSSING CLEARANCES".
- **OSM labels decluttered:** ways with the same ref that share an endpoint are joined into one connected taxiway, labelled once at the midpoint of its longest way, with a white halo. Refs show only when zoomed in past 1.2 m per screen pixel; the full view is about 2.3, so it shows none.
- Re-rendered: `map_screens/full.png`, `full_osm_on.png`, `terminal_b.png`, `terminal_b_osm.png`.

