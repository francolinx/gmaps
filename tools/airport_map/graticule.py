"""Read graticule values from an extracted FAA airport diagram and tie each to its extracted grid line. Stdlib only.

  python3 tools/airport_map/graticule.py --icao KHWD

Source: labels of class 'graticule' in the PDF's own text layer (e.g. "37°40.0'N", "122°07.5'W"). Each value is tied to
the extracted graticule_line on its axis (parallel: same y; meridian: same x). Writes data/airports/<icao>/graticule.json
with source 'pdf_text_layer'. If the chart prints its graticule values only as outlines (no text layer, e.g. KLGA), this
refuses and a person must transcribe them (as for KLGA, confirmed by Franco); an existing human-confirmed file is never
overwritten.
"""
import argparse, collections, json, math, re, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AXIS_TOL = 14.0


def value(text):
    m = re.fullmatch(r"(\d{2,3})°(\d{2}(?:\.\d+)?)'([NSEW])", text)
    deg, mins, hemi = int(m.group(1)), float(m.group(2)), m.group(3)
    v = deg + mins / 60.0
    return -v if hemi in 'SW' else v, hemi


# ---------------- outline-only graticule values: decode by exact glyph shape (no OCR, no guessing) ----------------
# The same chart draws other labels as outline glyphs OVER a hidden text layer, so it teaches its own glyph shapes:
# glyph outline (normalised in the label's reading frame) -> character. A graticule value is accepted only if every one
# of its glyphs matches a learned shape exactly (rounded), the learned shape always meant the same character, and the
# result parses as a graticule value. Otherwise refuse (a person transcribes, as for KLGA).
GRAT_RX = r"(\d{2,3})°(\d{2}(?:\.\d+)?)'([NSEW])"


def _pts(o):
    n = [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?', o['d'])]
    return list(zip(n[0::2], n[1::2]))


def shape_sig(o, u):
    """Outline in the reading frame (u = reading direction), translated to its own box and scaled by its larger side."""
    P = [(x * u[0] + y * u[1], -x * u[1] + y * u[0]) for x, y in _pts(o)]
    if not P:
        return None
    a0, c0 = min(p[0] for p in P), min(p[1] for p in P); w = max(p[0] for p in P) - a0; h = max(p[1] for p in P) - c0
    s = max(w, h) or 1
    return (re.sub(r'[^A-Za-z]', '', o['d']), round(w / s, 1), round(h / s, 1), tuple((round((a - a0) / s, 2), round((c - c0) / s, 2)) for a, c in P))


def learn_glyphs(raw, box):
    """shape signature -> set of characters, from labels whose outline glyphs pair one-to-one with their text."""
    glyphs = [o for o in raw['objects'] if o['class'] == 'glyph']
    book = collections.defaultdict(collections.Counter)
    for l in raw['labels']:
        f = l['frame']; u = f['u']; h = f['h']
        def hit(o):
            vx, vy = o['centre'][0] - l['x'], o['centre'][1] - l['y']; aa = vx * u[0] + vy * u[1]; cc = -vx * u[1] + vy * u[0]
            return f['a0'] - box['pre'] * h <= aa <= f['a1'] + box['post'] * h and f['c0'] - box['pad'] * h <= cc <= f['c1'] + box['pad'] * h
        mine = [o for o in glyphs if hit(o)]
        chars = [ch for ch in l['text'] if not ch.isspace()]
        if not mine or len(mine) != len(chars):
            continue
        mine.sort(key=lambda o: (o['centre'][0] - l['x']) * u[0] + (o['centre'][1] - l['y']) * u[1])
        for o, ch in zip(mine, chars):
            book[shape_sig(o, u)][ch] += 1
    return book


PRIME = {",": "'", "’": "'", "′": "'", "‘": "'", "'": "'"}   # comma and prime are the same outline (position differs)
SHAPE_TOL = 0.03   # max deviation of any outline point, as a fraction of the glyph's larger side


MARGIN_MIN = 0.10   # unambiguous decode only. The nearest shape of ANY OTHER character must be at least this far
                    # (same units as SHAPE_TOL) from the outline; a different outline structure counts as infinitely far


def lookup(index, sig):
    """(char, best, other): the one character every learned shape within SHAPE_TOL meant, the best deviation, and the
    deviation to the nearest learned shape of any other character. char is None if no match, ambiguous, or the margin
    to another character is below MARGIN_MIN."""
    if sig is None:
        return None, None, None
    found, best, other = set(), {}, {}
    for pts, chars in index.get((sig[0], sig[1], sig[2], len(sig[3])), []):
        d = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(pts, sig[3]))
        for ch in {PRIME.get(c, c) for c in chars}:
            best[ch] = min(best.get(ch, 9), d)
        if d <= SHAPE_TOL:
            found |= {PRIME.get(ch, ch) for ch in chars}
    if len(found) != 1:
        return None, None, None
    ch = found.pop(); others = [v for k, v in best.items() if k != ch]
    o = min(others) if others else float('inf')
    return (ch if o >= MARGIN_MIN else None), round(best[ch], 3), (round(o, 3) if others else None)


def decode_outline_labels(raw, box, book=None):
    """Graticule values drawn as outlines: returns [{'text', 'bbox', 'glyphs'}] for decoded labels, and notes."""
    book = book if book is not None else learn_glyphs(raw, box)
    index = collections.defaultdict(list)
    for k, c in book.items():
        index[(k[0], k[1], k[2], len(k[3]))].append((k[3], c))
    # candidates: outline glyphs classed graticule_label, plus small filled outlines the extractor could not place
    # (e.g. a value classed 'symbol'); only exact-shape decodes that parse as a graticule value are kept
    decoded_chart = any(l.get('source') == 'outline_decode' for l in raw['labels'])   # no text layer: lettering is classed 'glyph' inside decoded labels
    G = [o for o in raw['objects'] if o['class'] == 'graticule_label' or (o['class'] in ('symbol', 'unclassified') + (('glyph',) if decoded_chart else ()) and o['fill'] and o['long'] <= 8)]
    # group touching glyphs into words (gap below about a letter width)
    groups = []
    for o in G:
        hits = [g for g in groups if any(max(o['bbox'][0] - x['bbox'][2], x['bbox'][0] - o['bbox'][2], o['bbox'][1] - x['bbox'][3], x['bbox'][1] - o['bbox'][3]) <= 0.5 * max(o['long'], x['long']) for x in g)]
        merged = [o] + [x for g in hits for x in g]
        groups = [g for g in groups if g not in hits] + [merged]
    out, notes = [], []
    groups = [g for g in groups if len(g) >= 5]   # a graticule value has at least 5 glyphs (e.g. 9°00'N)
    for g in groups:
        best = None
        for ang in (0, 90, 180, 270):
            u = (round(math.cos(math.radians(ang)), 6), round(math.sin(math.radians(ang)), 6))
            seq = sorted(g, key=lambda o: o['centre'][0] * u[0] + o['centre'][1] * u[1])
            looks = [lookup(index, shape_sig(o, u)) for o in seq]
            chars = [x[0] for x in looks]
            if None not in chars and re.fullmatch(GRAT_RX, ''.join(chars)):
                best = ''.join(chars); best_looks = looks; best_ang = ang; break
        bb = [min(o['bbox'][0] for o in g), min(o['bbox'][1] for o in g), max(o['bbox'][2] for o in g), max(o['bbox'][3] for o in g)]
        if best:
            margins = [x[2] for x in best_looks if x[2] is not None]
            out.append({'text': best, 'bbox': [round(v, 2) for v in bb], 'glyphs': len(g), 'reading_deg': best_ang, 'max_deviation': max(x[1] for x in best_looks),
                        'min_margin_to_other_char': min(margins) if margins else None})
        else:
            notes.append({'bbox': [round(v, 2) for v in bb], 'glyphs': len(g), 'decoded': None})
    return out, notes, sum(sum(c.values()) for c in book.values()), len(book)


LIBRARY = REPO / 'tools/airport_map/glyph_library.json'   # shared FAA glyph library


def build_library():
    """Pool the glyph shapes every chart teaches (outline glyphs over its own text layer) into one library."""
    st = json.loads((REPO / 'tools/airport_map/styles/faa_apd.json').read_text())['post']
    box = {k: st[f'label_glyph_{k}'] for k in ('pre', 'post', 'pad')}
    lib, charts = collections.defaultdict(collections.Counter), {}
    for raw_p in sorted((REPO / 'data/airports').glob('*/build/layers.raw.json')):
        b = learn_glyphs(json.loads(raw_p.read_text()), box)
        if b:
            charts[raw_p.parts[-3].upper()] = sum(sum(c.values()) for c in b.values())
        for k, c in b.items():
            lib[k].update(c)
    rows = [{'cmd': k[0], 'w': k[1], 'h': k[2], 'pts': k[3], 'chars': dict(c)} for k, c in lib.items()]
    LIBRARY.write_text(json.dumps({'about': 'Shared FAA airport-diagram glyph library: outline shape (normalised in the reading frame) -> characters, '
                                            'learned from charts that draw outline glyphs over their own text layer. Built by tools/airport_map/graticule.py --build-library.',
                                   'charts': charts, 'shapes': len(rows), 'rows': rows}, separators=(',', ':')))
    print(f'{len(rows)} shapes from {charts} -> {LIBRARY.relative_to(REPO)}')


def load_library():
    if not LIBRARY.exists():
        return {}, {}
    d = json.loads(LIBRARY.read_text())
    return {(r['cmd'], r['w'], r['h'], tuple(tuple(p) for p in r['pts'])): collections.Counter(r['chars']) for r in d['rows']}, d['charts']


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao'); ap.add_argument('--build-library', action='store_true')
    a = ap.parse_args()
    if a.build_library:
        build_library(); return 0
    base = REPO / 'data/airports' / a.icao.lower()
    out = base / 'graticule.json'
    if out.exists() and json.loads(out.read_text()).get('confirmed_by'):
        print(f'{out.relative_to(REPO)} is human-confirmed; not overwritten'); return 0
    raw = json.loads((base / 'build/layers.raw.json').read_text())
    labs = [l for l in raw['labels'] if l['class'] == 'graticule' and l.get('source') != 'outline_decode']   # decoded outline text goes through the decode path below (margins recorded)
    source, decode = 'pdf_text_layer', None
    if not labs:
        st = json.loads((REPO / 'tools/airport_map/styles/faa_apd.json').read_text())['post']
        box = {k: st[f'label_glyph_{k}'] for k in ('pre', 'post', 'pad')}
        labs, notes, n_train, n_shapes = decode_outline_labels(raw, box)
        training = 'this chart'
        if not labs:   # nothing on this chart to learn from (e.g. KPDK): the shared library of FAA glyph shapes
            lib, lib_charts = load_library()
            labs, notes, n_train, n_shapes = decode_outline_labels(raw, box, lib)
            training = 'shared glyph library from ' + ', '.join(f'{k} ({v})' for k, v in sorted(lib_charts.items()))
        decode = {'method': 'exact glyph-shape match against glyphs the same chart draws over its own text layer' if training == 'this chart' else 'exact glyph-shape match against the shared FAA glyph library',
                  'training': training, 'training_glyphs': n_train,
                  'rule': f'every glyph within {SHAPE_TOL} of a learned shape of one character, and at least {MARGIN_MIN} from any other character (Franco, Oct 9: unambiguous only)',
                  'per_value': [{k: l[k] for k in ('text', 'max_deviation', 'min_margin_to_other_char')} for l in labs],
                  'distinct_shapes': n_shapes, 'decoded_labels': len(labs), 'undecoded_groups': len(notes)}
        if not labs:
            raise SystemExit('no graticule values in the PDF text layer, and the outline glyphs could not be decoded: a person must transcribe them into graticule.json')
        source = 'outline_glyph_decode'
        angs = collections.Counter(l['reading_deg'] for l in labs)
        if angs.most_common(1)[0][0] != 0:   # the decoded text does not read left to right: the extraction frame is rotated
            rot = (raw['meta']['content_rotation_deg'] - angs.most_common(1)[0][0]) % 360
            (base / 'build/rotation_hint.json').write_text(json.dumps({'content_rotation_deg': rot, 'reason': f'decoded outline text reads at {dict(angs)} deg in the current frame'}))
            raise SystemExit(f'ROTATION: decoded graticule text reads at {dict(angs)} deg; re-extract with --rotation {rot} (build/rotation_hint.json)')
    gl = [o for o in raw['objects'] if o['class'] == 'graticule_line']
    # fallback for a value whose line the extractor left as plain linework (its anchor label was outline-only): a long thin
    # straight stroke on the label's axis that ends at the label
    thin = [o for o in raw['objects'] if o['class'] == 'linework' and o['stroke'] and not o['fill'] and o['short'] <= 0.3 and o['long'] >= 15
            and min(abs(o['axis_deg']) % 90, 90 - abs(o['axis_deg']) % 90) <= 2]
    def ends_at(o, b, gap=25):
        return max(b[0] - o['bbox'][2], o['bbox'][0] - b[2], b[1] - o['bbox'][3], o['bbox'][1] - b[3]) <= gap
    vert = lambda o: abs(abs(o['axis_deg']) - 90) < 5
    par, mer, problems = [], [], []
    for l in labs:
        v, hemi = value(l['text'])
        cx, cy = (l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2
        if hemi in 'NS':
            cand = [o for o in gl if not vert(o) and abs(o['centre'][1] - cy) <= AXIS_TOL] or \
                   [o for o in thin if not vert(o) and abs(o['centre'][1] - cy) <= AXIS_TOL and ends_at(o, l['bbox'])]
            if not cand:
                problems.append(f'no parallel line for {l["text"]}'); continue
            y = sum(o['centre'][1] for o in cand) / len(cand)
            par.append({'value': l['text'], 'lat': v, 'line_pt_y': round(y, 3), 'label_bbox': l['bbox']})
        else:
            cand = [o for o in gl if vert(o) and abs(o['centre'][0] - cx) <= AXIS_TOL] or \
                   [o for o in thin if vert(o) and abs(o['centre'][0] - cx) <= AXIS_TOL and ends_at(o, l['bbox'])]
            if not cand:
                problems.append(f'no meridian line for {l["text"]}'); continue
            x = sum(o['centre'][0] for o in cand) / len(cand)
            mer.append({'value': l['text'], 'lon': v, 'line_pt_x': round(x, 3), 'label_bbox': l['bbox']})
    ps, ms = sorted(par, key=lambda p: p['line_pt_y']), sorted(mer, key=lambda m: m['line_pt_x'])
    if any(a['lat'] <= b['lat'] for a, b in zip(ps, ps[1:])) or any(a['lon'] >= b['lon'] for a, b in zip(ms, ms[1:])):
        problems.append('values not monotonic with line positions (north-up: latitude falls down the page, longitude rises to the right)')
    unmatched = [p for p in problems if p.startswith('no ')]   # a value whose line was not extracted: left out, recorded
    problems = [p for p in problems if not p.startswith('no ')]
    if problems or len(par) < 2 or len(mer) < 2:
        raise SystemExit(f'graticule incomplete: {len(par)} parallels, {len(mer)} meridians; {problems + unmatched}')
    about = ('Graticule values read from the chart PDF\'s own text layer and tied to the extracted grid lines by position (tools/airport_map/graticule.py).' if source == 'pdf_text_layer' else
             'Graticule values drawn as outlines only, decoded by exact glyph shape against glyphs the same chart draws over its own text layer, and tied to the extracted grid lines by position (tools/airport_map/graticule.py). Not human-confirmed.')
    g = {'icao': a.icao.upper(), 'source': source, 'about': about,
         'pdf_sha256': raw['meta']['pdf_sha256'], 'read_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'confirmed_by': None,
         'parallels': sorted(par, key=lambda p: p['line_pt_y']), 'meridians': sorted(mer, key=lambda m: m['line_pt_x'])}
    if decode:
        g['decode'] = decode
    if unmatched:
        g['values_without_line'] = unmatched
    out.write_text(json.dumps(g, indent=1, ensure_ascii=False))
    print(json.dumps({'parallels': [(p['value'], p['line_pt_y']) for p in g['parallels']], 'meridians': [(m['value'], m['line_pt_x']) for m in g['meridians']]}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
