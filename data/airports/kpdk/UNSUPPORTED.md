# KPDK (DeKalb-Peachtree): no longer unsupported (Oct 9). It builds.

The overnight note said KPDK stopped at stage 4: graticule values outline-only, nothing on the chart to learn glyphs from. Since Oct 9 it builds end to end:
1. **Shared glyph library** (`tools/airport_map/glyph_library.json`): 2,247 shapes pooled from 9 charts. It decodes KPDK's 33°53.0'N, 33°52.5'N × 84°18.5'W, 84°18.0'W under the unambiguous-margin rule.
2. **Rotation from decoded text.** The chart has no text layer, so the extraction frame was 90° off. The decoded text's reading direction now sets the rotation (`build/rotation_hint.json`).
3. **Runway ends identified by the nearest NASR end** (there are no runway-end labels to read). `runway_identifiers` = review.

**Update Oct 9 (later):** the chart's outline lettering is now decoded into labels with the same library (`tools/airport_map/outline_text.py`):
- Glyphs are matched in all reading directions (1° steps for rotated text) under the unambiguous rule.
- Lines are assembled per direction; margin text and note fragments are excluded.
- **Result: all 9 checklist items pass or are n/a.** Runway identifiers 3L/3R/16/21L/21R/34; hot spots 3/3; taxiway names decided by R1 (chart-only "S" may be a decode fragment, worth a glance); helipad H1 within 2.77 m.

**03L and 16 (Franco's question):**
- The chart prints **3746 X 150** (03L/21R) and **3968 X 150** (16/34), the same as NASR's lengths (3746 / 3968 ft).
- NASR lists **no displaced threshold** at 03L or 16. 21L has one (1,005 ft), and its end fits within 0.7 m.
- The drawn runways measure 3,694 ft and 3,987 ft. So the chart draws 03L 16 m short and 16 5.8 m long against its own printed lengths: chart drafting at those ends. The other ends and the graticule fit are within 1 m.

Unsigned; predictions off.
