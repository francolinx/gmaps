# KLGA: A1 extraction review (checkpoint 1, rev 3, Oct 8)

Source: `data/airports/sources/lga_faa_2610_00289AD.pdf` (FAA d-TPP cycle 2610, effective 01 OCT 2026 to 29 OCT 2026; sha256 `99951fad…`).
Tools:
- `.venv/bin/python tools/airport_map/extract.py <pdf> --icao KLGA` (pdfminer.six, MIT). Rules: `tools/airport_map/styles/faa_apd.json`.
- `.venv/bin/python tools/airport_map/overlay_check.py --icao KLGA` (pdftoppm + headless Chrome, Pillow + numpy).

Files:
- `extract_report.json`: counts and lists.
- `extract_preview.svg`: review styling, colour by class.
- `faithful.svg`: original FAA styling.
- `overlay_check.json` and `overlay_diff.png`: red = FAA ink missing from the reconstruction, blue = extra.
- `../review.json`: the per-airport checklist.

Page space only: **nothing is georeferenced yet.**

## Fixes since rev 1 (Franco's overlay review)
| # | issue | fix | result |
|---|---|---|---|
| 1 | rects dropped their top edge | full paths are kept, closing `h` included | chart frame and FIELD ELEV box closed |
| 2 | curves flattened to polygons | Bézier `c`/`v`/`y` segments emitted as SVG `C` | 24 curved objects (HS circles; P, H, A1, A5 circles) render smooth |
| 3 | clip paths ignored | interpreter tracks `W`/`W*` per graphics state; non-page clips become SVG clipPaths | 12 hatch strokes clipped to their polygon (class `hatch`); no spill across BB/AA |
| 4 | dash patterns lost; ILS hold lines | dash arrays kept (both runway centrelines dashed). ILS hold lines are drawn as separate collinear dash segments: groups of ≥2 within 45 pt of an ILS/HOLD annotation, off any runway axis | **4 hold lines for 3 annotations**: the Terminal A "ILS / HOLD" has two arrows to two hold lines across AC. Dash runs on a runway's axis are a new class, `runway_centerline_extension` (22 dashes) |
| 5 | glyph counters filled | paths are captured **before** pdfminer splits subpaths; fill-rule kept | 412 multi-subpath objects; all 1,425 fills even-odd; O, D, P, A, 0, 9 counters open |
| 6 | non-buildings in "building" | buildings ≥ 20 pt² only; smaller fills are `symbol`. Graticule lettering = small black fills near the frame on a graticule line's axis (`graticule_label`). Graticule lines = horizontal/vertical sets that reach the frame (excludes text underlines and the compass north line) | 12 buildings; 29 graticule-letter objects; 20 graticule segments on exactly 4 lines |
| 7 | HS boxes drawn solid | classification uses the **painted** colour only, and output keeps paint order | brown outline with white interior; "HS 1"/"HS 2" are real text |
| 8 | gate numbers as runway ends | numbers split by geometry: runway end = on a runway axis past its end; elevation = next to ELEV; gate = next to a building | runway ends exactly 4, 13, 22, 31; gates 1-19; elevations 7, 12, 21; 0 unclassified numbers |

Object count is now **1,699** (as in the brief), because multi-subpath shapes are no longer split.

## Rev 3 (Franco's follow-up review, Oct 8)
1. **Last glyph of a label classed as `symbol`** (EMAS→"S", STATION→"N", TRANSIENT→"T", GENERAL→"L", "RWY 04-22"→"2", "2D-360"→"0").
   - Cause: the visible outline lettering is wider than the hidden Times/Arial text layer, so the last outline glyph overhangs the text box by 1.2–2.3 pt.
   - Fix: glyph membership uses each label's own reading frame, extended 0.9 × letter height past the last character, 0.3 × before the first, and 0.3 × across (`post.label_glyph_*`).
   - **Single-character `symbol` items: 28 before → 0 after.** `symbol` went 61 → 10 genuine symbols, plus 23 leader arrowheads now in their own `arrowhead` class. The arrowhead class is checked before glyph, so the wider box cannot absorb a leader arrow; it is a single straight triangle, a shape no glyph on this chart has.
   - All six cases now have one glyph per character, and no label is short a glyph. Regression test in `tests/run_all.py`.
2. **Cycle provenance:** `data/airports/klga/provenance.json` (by `tools/airport_map/provenance.py`) stores cycle 2610, effective 2026-10-01 to 2026-10-29, the PDF sha256 and the FAA URL. These are checked against the PDF: the sha256 is recomputed and the dates are parsed from the chart's own banner. It also holds the exact footer the map view will show. The banners stay off the map.
3. **Debug text-layer overlay:** now opt-in (`--debug-text-layer`), drawn only into `review/extract_preview.svg`, marked `class="debug-text-layer"`. A test asserts no map/viewer output contains it. The old overlay text was tiny because the text matrix gives no font size (1.0); labels now carry their real letter height.

## Overlay check (new checklist item `overlay_ink`)
- Method: PDF rendered at 300 dpi (`pdftoppm`) and rotated into the same north-up frame, against `faithful.svg` rendered by headless Chrome at the same size. Ink = darker than 250/255; match tolerance 1 px.
- Thresholds were **fixed before the first measurement**: covered ≥ 99.0 %, extra ≤ 2.0 %.
- **Result: covered 99.123 %, extra 0.075 % → PASS** (rev 2). **Rev 3: covered 99.771 %, extra 0.334 % → PASS.** The banners are now drawn at their real letter height instead of 1.0; the remaining difference is still the substitute typeface.
- The first measurement **failed** (98.899 %). Almost all the missing ink was the two "NE-2, 01 OCT 2026 to 29 OCT 2026" banners, which are real text in an embedded font that the reconstruction draws in Helvetica. The fix fits each visible-text label to its exact baseline run (`textLength`). The thresholds were not changed.
- **Where the remaining 0.877 % (5,891 px) of FAA ink is** (analysed Oct 8 at Franco's request): **100 % of it lies inside the two "NE-2, 01 OCT 2026 to 29 OCT 2026" cycle-date banners** (17 connected regions, all on the banner text, top and bottom margins). The top 5 regions (528 / 525 / 487 / 480 / 478 px) are banner glyph runs.
  - It is not anti-aliasing: 4,231 of the pixels are solid FAA ink (gray < 128), 872 mid-gray, 788 faint edge.
  - It is the **typeface**. The banners are real text in an embedded CID font that the reconstruction draws in Helvetica stretched to the same length, so the letter shapes differ.
  - **No airport feature (pavement, runway, building, hot spot, hold line, symbol, label outline) has missing ink.**
- **Extra ink** (0.075 %, 499 px): also text only. 426 px on the banners and 73 px on "HS 1"/"HS 2", the only other visible-text labels.
- Decision: accepted as-is. The banners are chart metadata, outside the airport drawing. Exact glyphs would need the embedded font's outlines, which is not worth it for a display check.

## Checklist status (`../review.json`)
| item | status |
|---|---|
| overlay_ink | pass (99.123 % / 0.075 %) |
| hot_spots | pass (HS 1, HS 2; 2 circles) |
| ils_hold_lines | pass (3 annotations, 4 hold lines, every annotation has one) |
| unclassified | pass (0 objects, 0 numbers) |
| runway_ends, runway_identifiers | not run (A2) |
| taxiway_names | not run (A3) |
| cycle_current | not run (A6) |

## Graticule (confirmed by Franco, Oct 8)
40°47'N top, 40°46'N bottom, 73°53'W left, 73°52'W right. The lines are extracted at y ≈ 96 pt and y ≈ 353 pt, and x ≈ 169 pt and x ≈ 364 pt (north-up frame).
