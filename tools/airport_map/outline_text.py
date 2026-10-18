"""Synthetic text layer for charts drawn without one (e.g. KPDK): every outline glyph is decoded with the shared FAA glyph
library (tools/airport_map/glyph_library.json) under the same unambiguous rule as the graticule decode, then glyphs are
assembled into labels line by line in their own reading direction. Used by extract.py only when the PDF has (almost) no
text layer; labels made here carry source 'outline_decode'. Stdlib only.
"""
import collections, math, statistics
from graticule import load_library, lookup, shape_sig

ANGLES = (0, 90, 180, 270)


def _u(ang):
    return (round(math.cos(math.radians(ang)), 6), round(math.sin(math.radians(ang)), 6))


def decode(objs, frame_box, max_glyph_pt=12.0):
    lib, _ = load_library()
    if not lib:
        return []
    index = collections.defaultdict(list)
    for k, c in lib.items():
        index[(k[0], k[1], k[2], len(k[3]))].append((k[3], c))
    white = lambda rgb: rgb and all(v > 0.95 for v in rgb)
    gray = lambda rgb: rgb and all(abs(v - 0.812) < 0.03 for v in rgb)
    fx0, fy0, fx1, fy1 = frame_box
    cands = [o for o in objs if o['fill'] and o['long'] <= max_glyph_pt and not white(o['fill_rgb']) and not gray(o['fill_rgb'])
             and fx0 - 30 <= o['centre'][0] <= fx1 + 30 and fy0 - 30 <= o['centre'][1] <= fy1 + 30]
    # 1. every glyph, every reading direction: the characters it unambiguously is
    opts = {}
    for o in cands:
        got = {a: lookup(index, shape_sig(o, _u(a)))[0] for a in ANGLES}
        got = {a: ch for a, ch in got.items() if ch}
        if not got:   # rotated lettering (runway numbers, taxiway letters along a taxiway): sweep the reading direction in 1 degree steps
            for a in range(0, 360):
                if a % 90:
                    ch = lookup(index, shape_sig(o, _u(a)))[0]
                    if ch:
                        got[a] = ch
        if got:
            opts[o['index']] = (o, got)
    # 2. one reading direction per glyph: unique if only one matches, else the majority of its decoded neighbours
    adiff = lambda a, b: abs((a - b + 180) % 360 - 180)
    def single(g):   # all matching angles within 3 degrees of each other: one direction (their middle)
        ks = sorted(g)
        return ks[len(ks) // 2] if all(adiff(k, ks[0]) <= 3 for k in ks) else None
    fixed = {i: single(g) for i, (o, g) in opts.items() if single(g) is not None}
    for i, (o, g) in opts.items():
        if i in fixed:
            continue
        votes = [fixed[j] for j, (p, _) in opts.items() if j in fixed and math.dist(o['centre'], p['centre']) <= 15]
        best = min(g, key=lambda a: (-sum(adiff(a, v) <= 3 for v in votes), a)) if votes else None
        if best is not None and any(adiff(best, v) <= 3 for v in votes):
            fixed[i] = best
    # 3. lines: per direction, glyphs whose centres share a baseline, split into labels at large gaps, spaces at medium gaps
    labels = []
    # reading directions clustered (consecutive angles within 3 degrees form one direction; e.g. 30 and 31 for one label)
    angs = sorted(set(fixed.values())); clusters = []
    for a in angs:
        if clusters and adiff(a, clusters[-1][-1]) <= 3:
            clusters[-1].append(a)
        else:
            clusters.append([a])
    if len(clusters) > 1 and adiff(clusters[0][0], clusters[-1][-1]) <= 3:   # wrap around 0/360
        clusters[0] = clusters.pop() + clusters[0]
    rep_of = {a: sorted(c)[len(c) // 2] for c in clusters for a in c}
    buckets = collections.defaultdict(list)
    for i, a in fixed.items():
        buckets[rep_of[a]].append(i)
    for ang, members in sorted(buckets.items()):
        u = _u(ang); n = (-u[1], u[0])
        gs = []
        for i in members:
            o, g = opts[i]; a = fixed[i]
            pts = [(o['bbox'][0], o['bbox'][1]), (o['bbox'][2], o['bbox'][3]), (o['bbox'][0], o['bbox'][3]), (o['bbox'][2], o['bbox'][1])]
            al = [p[0] * u[0] + p[1] * u[1] for p in pts]; ac = [p[0] * n[0] + p[1] * n[1] for p in pts]
            w = max(al) - min(al); pad = 0.0
            gs.append({'o': o, 'ch': g[a], 'a0': min(al), 'a1': max(al), 'c0': min(ac), 'c1': max(ac), 'w': w})
        if not gs:
            continue
        hmed = statistics.median([x['c1'] - x['c0'] for x in gs if x['ch'].isalnum()] or [4.0])
        small = [x for x in gs if x['c1'] - x['c0'] < 0.45 * hmed]   # hyphens, dots, commas, primes: they do not sit on the baseline
        big = sorted((x for x in gs if x['c1'] - x['c0'] >= 0.45 * hmed), key=lambda x: (x['c0'] + x['c1']) / 2)
        lines, cur = [], []
        for x in big:   # same line: bottoms (baseline side) within 0.35 letter heights
            if cur and abs(x['c1'] - statistics.median(y['c1'] for y in cur)) > 0.35 * hmed:
                lines.append(cur); cur = []
            cur.append(x)
        if cur:
            lines.append(cur)
        for x in small:   # attach to the line whose letters span it (across) and lie next to it (along)
            cx, ax = (x['c0'] + x['c1']) / 2, (x['a0'] + x['a1']) / 2
            best = min(lines, key=lambda ln: min(abs(ax - (y['a0'] + y['a1']) / 2) for y in ln), default=None) if lines else None
            ok = best and min(y['c0'] for y in best) - 0.3 * hmed <= cx <= max(y['c1'] for y in best) + 0.3 * hmed \
                and min(abs(ax - (y['a0'] + y['a1']) / 2) for y in best) <= 1.5 * hmed
            if ok:
                best.append(x)
            else:
                lines.append([x])
        for x in gs:   # narrow glyphs (1, I, punctuation) get a nominal width so their side bearings do not read as spaces
            if x['w'] < 0.5 * hmed:
                pad = (0.5 * hmed - x['w']) / 2; x['a0'] -= pad; x['a1'] += pad
        for line in lines:
            line.sort(key=lambda x: x['a0'])
            hl = statistics.median([x['c1'] - x['c0'] for x in line if x['ch'].isalnum()] or [hmed])   # this line's letter height
            word = [line[0]]
            for prev, x in zip(line, line[1:]):
                if x['a0'] - prev['a1'] > 1.1 * hl:
                    labels.append(_label(word, u, n, ang, hl)); word = []
                word.append(x)
            labels.append(_label(word, u, n, ang, hl))
    # a short piece (<= 2 characters) on the same baseline as, and right next to, a longer decoded word is part of that
    # note (letters the library could not read split the word), not a taxiway name
    for l in labels:
        if sum(ch.isalnum() for ch in l['text']) > 2:
            continue
        for m in labels:
            if m is l or m['angle'] != l['angle'] or (sum(ch.isalpha() for ch in m['text']) < 3 and sum(ch.isdigit() for ch in m['text']) < 2):   # a word, or a number like the 20 of 'S-20'
                continue
            u = m['frame']['u']; h = max(l['size'], m['size'])
            cl = (l['x'] * -u[1] + l['y'] * u[0]); cm = (m['x'] * -u[1] + m['y'] * u[0])
            al0 = l['x'] * u[0] + l['y'] * u[1]; am0 = m['x'] * u[0] + m['y'] * u[1]
            gap = max(am0 - (al0 + l['run']), al0 - (am0 + m['run']), 0)
            if abs(cl - cm) <= 0.35 * h and gap <= 2.5 * h:
                l['force_class'] = 'note'; break
    # chart labels only: margin text outside the chart frame (titles, the cycle banner) is not part of the map
    labels = [l for l in labels if fx0 <= (l['bbox'][0] + l['bbox'][2]) / 2 <= fx1 and fy0 <= (l['bbox'][1] + l['bbox'][3]) / 2 <= fy1]
    return labels


def _label(word, u, n, ang, hmed):
    text = ''
    for i, x in enumerate(word):
        if i and x['a0'] - word[i - 1]['a1'] > 0.3 * hmed:
            text += ' '
        ch = x['ch']
        if ch == "'" and (x['c0'] + x['c1']) / 2 > statistics.median((y['c0'] + y['c1']) / 2 for y in word):
            ch = ','   # same outline as the prime; a comma sits low on the line
        text += ch
    a0, a1 = min(x['a0'] for x in word), max(x['a1'] for x in word); c0, c1 = min(x['c0'] for x in word), max(x['c1'] for x in word)
    ox, oy = a0 * u[0] + c1 * n[0], a0 * u[1] + c1 * n[1]   # origin: start of the baseline
    xs = [p for x in word for p in (x['o']['bbox'][0], x['o']['bbox'][2])]; ys = [p for x in word for p in (x['o']['bbox'][1], x['o']['bbox'][3])]
    h = c1 - c0
    return {'text': text, 'x': round(ox, 2), 'y': round(oy, 2), 'angle': ang if ang <= 180 else ang - 360, 'size': round(h, 2),
            'n_chars': len(text), 'chars_ns': sum(1 for ch in text if not ch.isspace()), 'colour': word[0]['o']['fill_rgb'], 'font': 'outline-decode',
            'run': round(a1 - a0, 2), 'bbox': [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
            'frame': {'u': [u[0], u[1]], 'a0': 0.0, 'a1': round(a1 - a0, 3), 'c0': round(-h, 3), 'c1': 0.0, 'h': round(h, 3)},
            'source': 'outline_decode', 'glyph_objects': [x['o']['index'] for x in word]}
