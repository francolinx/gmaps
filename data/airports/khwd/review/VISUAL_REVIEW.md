# KHWD visual review: map render vs FAA PDF, region by region (overnight, Oct 8/9)

**Method:** `tools/airport_map/side_by_side.py KHWD <out> 220 3`.
- It renders the FAA PDF (pdftoppm, rotated north-up like the extraction) and the map view's `chart.svg` at the same scale (220 dpi).
- It cuts both into 3 × 3 region tiles and shows them side by side. Every tile was inspected.
- Tiles stay out of the repo.

| region | finding |
|---|---|
| r00 NW (frequency box, ATIS/TWR/GND/CLNC) | identical |
| r01 N (West Hangars, FBO ramp, HS 3, GA TRANSIENT RAMP) | identical. Cycle banner and margin title not drawn (by design: margins are outside the frame; provenance is in the footer) |
| r02 NE (ADS-B note, north arrow, 37°40.0'N) | identical. Margin title "AIRPORT DIAGRAM" and the frame border not drawn (by design: `build_map.py` `NEVER = {'frame'}`, inside-frame objects only) |
| r10 W (10R end, ELEV 28, 0.4% UP, Z) | identical |
| r11 centre (both runways, HS 1, HS 4, A/D/E, 3108 X 75, 5694 X 150, TWR) | identical |
| r12 E (variation note, FBO ramp, East Hangars, 37°39.5'N) | identical |
| r20 SW (runway data box, CAUTION note, 122°08.0'W) | identical. Margin "HAYWARD EXEC (HWD)" not drawn (by design) |
| r21 S (closed-pavement X chains, FBO ramp, fire station, 122°07.5'W) | identical |
| r22 SE (28L end, FIELD ELEV 52, HS 2, HS 5 ×2, Z1, A1) | identical |

**Generic fixes from this pass**
1. **Area labels.** A word in a multi-word area name is an `area_label`, not a taxiway name. Area names are blocks of same-height words on one line or stacked lines, containing RAMP / APRON / PARKING / AREA (style file: `area_keywords`, `area_word_gap`, `area_line_gap`).
   - Only would-be taxiway names are reclassified.
   - KHWD: "GA" (GA TRANSIENT RAMP) → `area_label`, so `taxiway_names` now **passes** (chart-only list empty).
   - KLGA: no change (its PARKING/TRANSIENT words were already notes; counts identical).
2. **KLGA facts removed from `osm.py`.** The gate lead-in note ("Numbers 1-19 … #14 chart-only", "parking_position 76-79") was hard-coded and had leaked into KHWD's review.
   - The sentence is now generated from the data.
   - The reviewed per-gate note lives in `data/airports/klga/review/human_notes.json`.
   - KLGA output is byte-identical except timestamps. KHWD now says "No numbered gate lead-ins on this chart" (status `none_on_chart`).

**Not changed:** KHWD stays **unsigned**, with predictions off. All 8 checklist items are now pass or not_applicable (ILS hold lines n/a); that is for Franco to review and sign, or not.
