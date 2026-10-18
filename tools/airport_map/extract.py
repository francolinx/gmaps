"""A1: extract and classify the vector layers of an FAA airport diagram (APD) PDF. Display geometry only.

  .venv/bin/python tools/airport_map/extract.py data/airports/sources/lga_faa_2610_00289AD.pdf --icao KLGA

Reads every painted path with pdfminer.six (MIT) **before** pdfminer splits multi-subpath shapes, so each object keeps
all its subpaths, its Bézier segments, its fill rule (even-odd / nonzero), dash pattern, colours, paint order and the
clipping path active when it was painted. Characters come from pdfminer's text layer.

Writes (page space, north-up, PDF points; nothing georeferenced yet):
  data/airports/<icao>/build/layers.raw.json        every object + label with its class (gitignored, rebuildable)
  data/airports/<icao>/review/extract_report.json   counts per class, lists, unclassified objects
  data/airports/<icao>/review/extract_preview.svg   objects coloured by class (review styling)
  data/airports/<icao>/review/faithful.svg          objects in their original FAA styling (used by overlay_check.py)
Nothing is dropped: objects no rule matches are 'unclassified' and listed.
"""
import argparse, collections, hashlib, json, math, re, sys, time
from pathlib import Path

from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LTChar
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.utils import apply_matrix_pt
import pdfminer

REPO = Path(__file__).resolve().parents[2]
STYLES = Path(__file__).with_name('styles') / 'faa_apd.json'


# ---------------- PDF reading: full paths, paint order, clip ----------------
def _flatten(path, ctm):
    """pdfminer path segments -> subpaths of absolute ops in device space: ('M',p) ('L',p) ('C',c1,c2,p) ('Z',)."""
    subs, cur, cp, start = [], None, None, None
    T = lambda x, y: apply_matrix_pt(ctm, (x, y))
    for seg in path:
        op = seg[0]
        if op == 'm':
            cur = [('M', T(*seg[1:3]))]; subs.append(cur); cp = start = (seg[1], seg[2])
        elif cur is None:
            continue
        elif op == 'l':
            cur.append(('L', T(*seg[1:3]))); cp = (seg[1], seg[2])
        elif op == 'c':
            cur.append(('C', T(*seg[1:3]), T(*seg[3:5]), T(*seg[5:7]))); cp = (seg[5], seg[6])
        elif op == 'v':   # first control point = current point
            cur.append(('C', T(*cp), T(*seg[1:3]), T(*seg[3:5]))); cp = (seg[3], seg[4])
        elif op == 'y':   # second control point = end point
            cur.append(('C', T(*seg[1:3]), T(*seg[3:5]), T(*seg[3:5]))); cp = (seg[3], seg[4])
        elif op == 'h':
            cur.append(('Z',)); cp = start
    return [s for s in subs if len(s) > 1]


def _colour(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return (float(v),) * 3
    try:
        v = tuple(float(x) for x in v)
    except TypeError:
        return None
    if len(v) == 1:
        return v * 3
    if len(v) == 4:
        c, m, y, k = v
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return v[:3]


class Device(PDFPageAggregator):
    """Records each painted path whole (all subpaths), with styling and the active clip, in paint order."""

    def __init__(self, rsrcmgr, laparams=None):
        super().__init__(rsrcmgr, laparams=laparams)
        self.paths, self.interp = [], None

    def paint_path(self, gstate, stroke, fill, evenodd, path):
        subs = _flatten(path, self.ctm)
        if not subs or not (stroke or fill):
            return
        a, b, c, d = self.ctm[:4]
        scale = math.sqrt(abs(a * d - b * c)) or 1.0
        dash = None
        if gstate.dash and gstate.dash[0]:
            dash = ([float(x) * scale for x in gstate.dash[0]], float(gstate.dash[1] or 0) * scale)
        self.paths.append({'subpaths': subs, 'stroke': bool(stroke), 'fill': bool(fill), 'evenodd': bool(evenodd),
                           'linewidth': float(gstate.linewidth or 0) * scale, 'dash': dash,
                           'stroke_rgb': _colour(gstate.scolor) if stroke else None, 'fill_rgb': _colour(gstate.ncolor) if fill else None,
                           'clip': self.interp.clip if self.interp else None})


class Interp(PDFPageInterpreter):
    """Tracks the clipping path per graphics state (pdfminer ignores W/W*)."""
    clip = None

    def init_state(self, ctm):
        super().init_state(ctm)
        self.clipstack = []

    def dup(self):
        n = super().dup(); n.clip = self.clip
        return n

    def render_contents(self, resources, streams, ctm=(1, 0, 0, 1, 0, 0)):
        prev = self.device.interp; self.device.interp = self
        try:
            super().render_contents(resources, streams, ctm=ctm)
        finally:
            self.device.interp = prev

    def do_q(self):
        super().do_q(); self.clipstack.append(self.clip)

    def do_Q(self):
        super().do_Q()
        if self.clipstack:
            self.clip = self.clipstack.pop()

    def _set_clip(self, evenodd):
        subs = _flatten(self.curpath, self.ctm)
        if subs:
            self.clip = {'subpaths': subs, 'evenodd': evenodd}

    def do_W(self):
        self._set_clip(False)

    def do_W_a(self):   # pdfminer dispatches 'W*' to do_W_a
        self._set_clip(True)


def _walk(o):
    for c in getattr(o, '_objs', []):
        yield c
        yield from _walk(c)


def read_pdf(pdf_path):
    with open(pdf_path, 'rb') as f:
        doc = PDFDocument(PDFParser(f))
        page = next(PDFPage.create_pages(doc))
        rm = PDFResourceManager()
        dev = Device(rm)
        it = Interp(rm, dev)
        it.process_page(page)
        lt = dev.get_result()
        x0, y0, x1, y1 = page.mediabox
        chars = [o for o in _walk(lt) if isinstance(o, LTChar)]
        return dev.paths, chars, (float(x1 - x0), float(y1 - y0))


# ---------------- geometry helpers (north-up frame, y down) ----------------
def make_rotator(rot, W, H):
    """y-up PDF coords -> north-up frame (y down) in which the dominant text reads left to right."""
    if rot == 90:
        return (lambda x, y: (y, x)), (H, W)
    if rot == 270:
        return (lambda x, y: (H - y, W - x)), (H, W)
    if rot == 180:
        return (lambda x, y: (W - x, y)), (W, H)
    return (lambda x, y: (x, H - y)), (W, H)


def dominant_rotation(chars):
    c = collections.Counter(round(math.degrees(math.atan2(ch.matrix[1], ch.matrix[0])) / 90) * 90 % 360 for ch in chars)
    return c.most_common(1)[0][0]


def pca(pts):
    n = len(pts)
    mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts); syy = sum((p[1] - my) ** 2 for p in pts); sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    u, v = (math.cos(ang), math.sin(ang)), (-math.sin(ang), math.cos(ang))
    a = [p[0] * u[0] + p[1] * u[1] for p in pts]; b = [p[0] * v[0] + p[1] * v[1] for p in pts]
    return max(a) - min(a), max(b) - min(b), math.degrees(ang), (mx, my), u


def poly_area(pts):
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def anchors(sub):
    return [s[-1] for s in sub if s[0] != 'Z']


def svg_d(subpaths):
    out = []
    for sub in subpaths:
        for s in sub:
            if s[0] == 'Z':
                out.append('Z')
            elif s[0] == 'C':
                out.append('C' + ' '.join(f'{x:.3f},{y:.3f}' for x, y in s[1:]))
            else:
                out.append(f'{s[0]}{s[1][0]:.3f},{s[1][1]:.3f}')
    return ' '.join(out)


def near(c, ref, tol):
    return c is not None and all(abs(a - b) <= tol for a, b in zip(c, ref))


# ---------------- labels from the text layer ----------------
def group_labels(chars, R):
    """Group characters into labels in content-stream order (APDs emit each label's glyphs, including explicit
    spaces, consecutively). New label when the reading direction changes or the next glyph leaves the baseline."""
    labels, cur = [], None

    def flush():
        if cur:
            text = re.sub(r'\s+', ' ', ''.join(cur['chars'])).strip()
            if text and cur['pts']:
                xs, ys = [p[0] for p in cur['pts']], [p[1] for p in cur['pts']]
                u = cur['u']
                al = [(p[0] - cur['x']) * u[0] + (p[1] - cur['y']) * u[1] for p in cur['pts']]
                ac = [-(p[0] - cur['x']) * u[1] + (p[1] - cur['y']) * u[0] for p in cur['pts']]
                h = max(ac) - min(ac)   # letter height across the baseline (the text matrix does not carry the font size)
                labels.append({'text': text, 'x': round(cur['x'], 2), 'y': round(cur['y'], 2), 'angle': cur['ang'], 'size': round(h, 2),
                               'n_chars': len(cur['chars']), 'chars_ns': sum(1 for ch in text if not ch.isspace()), 'colour': cur['colour'], 'font': cur['font'],
                               'run': round(cur['run'], 2), 'bbox': [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
                               'frame': {'u': [round(u[0], 6), round(u[1], 6)], 'a0': round(min(al), 3), 'a1': round(max(al), 3),
                                         'c0': round(min(ac), 3), 'c1': round(max(ac), 3), 'h': round(h, 3)}})
    for ch in chars:
        a, b, c, d, e, f = ch.matrix
        ox, oy = R(e, f)
        ex, ey = R(e + a, f + b)
        dx, dy = ex - ox, ey - oy
        L = math.hypot(dx, dy) or 1
        u = (dx / L, dy / L)
        ang = round(math.degrees(math.atan2(u[1], u[0])))
        fs = math.hypot(c, d) or ch.size
        adv = abs(getattr(ch, 'adv', 0) or 0) * (math.hypot(a, b) or 1)
        if cur is not None:
            vx, vy = ox - cur['lx'], oy - cur['ly']
            along = vx * cur['u'][0] + vy * cur['u'][1]; across = -vx * cur['u'][1] + vy * cur['u'][0]
            step = max(cur['adv'], adv, 0.5)
            if not (abs(ang - cur['ang']) <= 2 and abs(across) <= 0.6 * max(step, cur['fs'] * 0.5) and -0.5 <= along <= 2.0 * max(step, cur['fs'] * 0.4)):
                flush(); cur = None
        if cur is None:
            cur = {'chars': [], 'x': ox, 'y': oy, 'ang': ang, 'u': u, 'fs': fs, 'adv': adv, 'pts': [],
                   'colour': _colour(getattr(ch.graphicstate, 'ncolor', None)), 'font': ch.fontname}
        cur['chars'].append(ch.get_text()); cur['lx'], cur['ly'] = ox, oy
        ex2, ey2 = R(e + a * (getattr(ch, 'adv', 0) or 0), f + b * (getattr(ch, 'adv', 0) or 0))   # end of this glyph's advance
        cur['run'] = max(cur.get('run', 0), (ex2 - cur['x']) * cur['u'][0] + (ey2 - cur['y']) * cur['u'][1])
        cur['adv'] = max(cur['adv'], adv); cur['fs'] = max(cur['fs'], fs)
        if ch.get_text().strip():
            cur['pts'] += [R(ch.x0, ch.y0), R(ch.x1, ch.y1), R(ch.x0, ch.y1), R(ch.x1, ch.y0)]
    flush()
    return labels


# ---------------- classification ----------------
def in_box(pt, b, pad=0.6):
    return b[0] - pad <= pt[0] <= b[2] + pad and b[1] - pad <= pt[1] <= b[3] + pad


LABEL_BOX = {'pre': 0.3, 'post': 0.9, 'pad': 0.3}   # overwritten from styles['post'] in main()


def label_hit(pt, l):
    """Is pt inside the label's reading box? Oriented along its baseline, extended past the last character because
    the visible outline lettering is wider than the hidden text layer (the last glyph overhangs the text box)."""
    f = l['frame']; u = f['u']; h = f['h']
    vx, vy = pt[0] - l['x'], pt[1] - l['y']
    a = vx * u[0] + vy * u[1]; c = -vx * u[1] + vy * u[0]
    return f['a0'] - LABEL_BOX['pre'] * h <= a <= f['a1'] + LABEL_BOX['post'] * h and f['c0'] - LABEL_BOX['pad'] * h <= c <= f['c1'] + LABEL_BOX['pad'] * h


def classify(objs, labels, st, frame_long):
    cols, tol = st['colours'], st['colour_tolerance']
    for o in objs:
        o['class'] = 'unclassified'
        for r in st['rules']:
            if 'painted_colour' in r and not (near(o['fill_rgb'], cols[r['painted_colour']], tol) or near(o['stroke_rgb'], cols[r['painted_colour']], tol)):
                continue
            if 'fill' in r and not (o['fill'] and near(o['fill_rgb'], cols[r['fill']], tol)):
                continue
            if 'stroke' in r and (o['fill'] or not (o['stroke'] and near(o['stroke_rgb'], cols[r['stroke']], tol))):
                continue
            if r.get('clipped') is not None and bool(o['clip']) != r['clipped']:
                continue
            if r.get('closed') and not o['d'].rstrip().endswith('Z'):
                continue
            if r.get('dashed') is not None and bool(o['dash']) != r['dashed']:
                continue
            if 'max_vertices' in r and (o['vertices'] > r['max_vertices'] or o['beziers'] or o['subpaths_n'] != 1):
                continue
            if r.get('inside_label') and not any(label_hit(o['centre'], l) for l in labels):
                continue
            checks = {'min_aspect': o['aspect'] >= r.get('min_aspect', -1), 'max_aspect': o['aspect'] < r.get('max_aspect', 1e9),
                      'min_long_axis_frac': o['long'] >= r.get('min_long_axis_frac', 0) * frame_long, 'max_long_axis': o['long'] <= r.get('max_long_axis', 1e9),
                      'min_area': o['area'] >= r.get('min_area', -1), 'max_area': o['area'] <= r.get('max_area', 1e9),
                      'min_linewidth': o['linewidth'] >= r.get('min_linewidth', -1)}
            if all(checks.values()):
                o['class'] = r['class']
                break


def post_pass(objs, labels, st, frame_box):
    """Geometry-dependent refinements (parameters in styles['post'])."""
    P = st['post']
    fx0, fy0, fx1, fy1 = frame_box
    edge = lambda c: min(c[0] - fx0, fx1 - c[0], c[1] - fy0, fy1 - c[1])
    black = lambda o: o['fill'] and near(o['fill_rgb'], (0, 0, 0), 0.02)
    # the same runway drawn twice (a plain rectangle, then a detailed outline over it): keep the first as the runway
    rws = [o for o in objs if o['class'] == 'runway']
    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
        return ix * iy / u if u > 0 else 0
    for i, a in enumerate(rws):
        for b in rws[i + 1:]:
            if b['class'] == 'runway' and abs(a['axis_deg'] - b['axis_deg']) <= 1 and iou(a['bbox'], b['bbox']) >= P['runway_overdraw_iou']:
                b['class'] = 'runway_overdraw'
    # graticule lines: long thin black horizontal/vertical strokes
    glines = [o for o in objs if o['class'] == 'linework' and o['stroke'] and not o['fill'] and o['short'] <= 0.3
              and min(abs(o['axis_deg']) % 90, 90 - abs(o['axis_deg']) % 90) <= P['graticule_axis_tol_deg'] and o['long'] >= P['graticule_line_min_len']]
    # keep only collinear sets (same x for meridians, same y for parallels) that reach the chart frame:
    # excludes text underlines and the compass north line, which never touch the frame
    def key(o):
        vert = abs(abs(o['axis_deg']) - 90) < 5
        return ('v', round(o['centre'][0] / P['graticule_collinear_tol'])) if vert else ('h', round(o['centre'][1] / P['graticule_collinear_tol']))
    sets = collections.defaultdict(list)
    for o in glines:
        sets[key(o)].append(o)
    touches = lambda o: min(abs(o['bbox'][0] - fx0), abs(o['bbox'][2] - fx1), abs(o['bbox'][1] - fy0), abs(o['bbox'][3] - fy1)) <= P['graticule_frame_touch']
    glabs = [l for l in labels if l['class'] == 'graticule']

    def anchored(k, grp):   # a graticule text label (e.g. "37°40.0'N") sits at an end of this line, on its axis
        x0 = min(o['bbox'][0] for o in grp); x1 = max(o['bbox'][2] for o in grp); y0 = min(o['bbox'][1] for o in grp); y1 = max(o['bbox'][3] for o in grp)
        for l in glabs:
            cx, cy = (l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2
            if k[0] == 'h' and l['text'][-1] in 'NS' and abs(cy - (y0 + y1) / 2) <= P['graticule_label_axis_dist'] and min(abs(l['bbox'][0] - x1), abs(x0 - l['bbox'][2])) <= P['graticule_label_end_gap']:
                return True
            if k[0] == 'v' and l['text'][-1] in 'EW' and abs(cx - (x0 + x1) / 2) <= P['graticule_label_axis_dist'] and min(abs(l['bbox'][1] - y1), abs(y0 - l['bbox'][3])) <= P['graticule_label_end_gap']:
                return True
        return False
    glines = [o for k, grp in sets.items() if any(touches(o) for o in grp) or anchored(k, grp) for o in grp]
    for o in glines:
        o['class'] = 'graticule_line'

    def on_gline(c):
        for g in glines:
            vert = abs(abs(g['axis_deg']) - 90) < 5
            if (vert and abs(c[0] - g['centre'][0]) <= P['graticule_label_axis_dist']) or (not vert and abs(c[1] - g['centre'][1]) <= P['graticule_label_axis_dist']):
                return True
        return False
    # graticule lettering (outline-only, no text layer): small black fills near the frame edge, next to a graticule line
    for o in objs:
        if o['class'] in ('building', 'symbol') and black(o) and edge(o['centre']) <= P['graticule_label_edge_dist'] \
                and o['area'] <= P['graticule_label_max_area'] and on_gline(o['centre']):
            o['class'] = 'graticule_label'
    # small black fills that are not buildings: approach-light dots, runway-end triangles, compass arrows
    for o in objs:
        if o['class'] == 'building' and o['area'] < P['building_min_area']:
            o['class'] = 'symbol'
    rw_ends, rw_axes = [], []
    for r in (o for o in objs if o['class'] == 'runway'):
        pts = [p for sub in r['subpaths'] for p in anchors(sub)]
        al = [(p[0] - r['centre'][0]) * r['u'][0] + (p[1] - r['centre'][1]) * r['u'][1] for p in pts]
        rw_axes.append((r['centre'], r['u'], min(al), max(al)))
        for t in (min(al), max(al)):
            rw_ends.append((r['centre'][0] + t * r['u'][0], r['centre'][1] + t * r['u'][1]))

    def beyond_runway_end(c):   # on a runway's axis line, past one of its ends
        for (cx, cy), u, t0, t1 in rw_axes:
            t = (c[0] - cx) * u[0] + (c[1] - cy) * u[1]; perp = abs(-(c[0] - cx) * u[1] + (c[1] - cy) * u[0])
            past = (t0 - t) if t < t0 else (t - t1) if t > t1 else -1
            if perp <= P['runway_end_label_perp'] and -P['runway_end_label_overlap'] <= past <= P['runway_end_label_dist']:   # beside the very end counts too
                return True
        return False
    # LAHSO points (display only): a straight dashed line crossing a runway, next to a "LAHSO" callout
    lahso_labels = [((l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2) for l in labels if l['text'] == 'LAHSO']
    def seg_ends(o):
        e = [tuple(map(float, q.split(','))) for q in re.findall(r'-?\d+\.\d+,-?\d+\.\d+', o['d'])]
        return (e[0], e[-1]) if len(e) >= 2 else None
    def seg_dist(pt, a, b):
        vx, vy = b[0] - a[0], b[1] - a[1]; L2 = vx * vx + vy * vy
        t = 0 if L2 == 0 else max(0, min(1, ((pt[0] - a[0]) * vx + (pt[1] - a[1]) * vy) / L2))
        return math.dist(pt, (a[0] + t * vx, a[1] + t * vy))
    lahso_tips = {}   # label centre -> leader tips (solid straight lines starting at the label)
    for l in (l for l in labels if l['text'] == 'LAHSO'):
        c = ((l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2); tips = []
        for o in objs:
            if o['class'] in ('linework', 'arrowhead') and o['stroke'] and not o['fill'] and not o.get('dash') and o['vertices'] <= 3 and o['long'] >= P['lahso_leader_min_len']:
                se = seg_ends(o)
                if se:
                    for a_, b_ in (se, se[::-1]):
                        if in_box(a_, l['bbox'], 3.0) and not in_box(b_, l['bbox'], 1.0):
                            tips.append(b_)
                    pts = [tuple(map(float, q.split(','))) for q in re.findall(r'-?\d+\.\d+,-?\d+\.\d+', o['d'])]
                    if len(pts) == 3 and in_box(pts[1], l['bbox'], 3.0):   # a forked "<" leader: apex at the label, both ends are tips (KORD)
                        tips += [e for e in se if not in_box(e, l['bbox'], 1.0)]
        lahso_tips[c] = tips
    def crosses_runway(o):
        se = seg_ends(o)
        if not se:
            return False
        (x1, y1), (x2, y2) = se
        d = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        for (cx, cy), u, t0, t1 in rw_axes:
            a1, a2 = (cx + t0 * u[0], cy + t0 * u[1]), (cx + t1 * u[0], cy + t1 * u[1])
            if d(a1, a2, (x1, y1)) * d(a1, a2, (x2, y2)) < 0 and d((x1, y1), (x2, y2), a1) * d((x1, y1), (x2, y2), a2) < 0:
                return True
        return False
    # a marker counts only if a LAHSO label's leader points to it: each leader tip takes the ONE nearest
    # dashed runway-crossing line (within lahso_tip_dist); a line that merely passes near a tip does not count
    cands = [o for o in objs if lahso_labels and o['class'] == 'linework' and o['stroke'] and not o['fill'] and o.get('dash') and o['vertices'] == 2 and crosses_runway(o)]
    for l in (l for l in labels if l['text'] == 'LAHSO'):
        c = ((l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2); l['lahso_markers'] = []
        for o in cands:   # the dashed line itself starts at the label (the label points along it; KBOS, KATL, KORD)
            se = seg_ends(o)
            if any(in_box(e, l['bbox'], P['lahso_line_start_gap']) for e in se):
                o['class'] = 'lahso_point'
                if o['index'] not in l['lahso_markers']:
                    l['lahso_markers'].append(o['index'])
        for t in lahso_tips.get(c, []):
            pick = min(cands, key=lambda o: seg_dist(t, *seg_ends(o)), default=None)
            if pick is not None and seg_dist(t, *seg_ends(pick)) <= P['lahso_tip_dist']:
                pick['class'] = 'lahso_point'
                if pick['index'] not in l['lahso_markers']:
                    l['lahso_markers'].append(pick['index'])
    # dashed lines drawn as separate collinear segments: extended runway centrelines vs ILS hold lines
    dashes = [o for o in objs if o['class'] == 'linework' and o['stroke'] and not o['fill'] and o['subpaths_n'] == 1
              and P['hold_dash_min_len'] <= o['long'] <= P['hold_dash_max_len']]
    groups, used = [], set()
    for i in range(len(dashes)):
        if i in used:
            continue
        g = [i]; used.add(i); changed = True
        while changed:
            changed = False
            for j, b in enumerate(dashes):
                if j in used:
                    continue
                for k in g:
                    c = dashes[k]
                    da = abs(((b['axis_deg'] - c['axis_deg']) + 90) % 180 - 90)
                    vx, vy = b['centre'][0] - c['centre'][0], b['centre'][1] - c['centre'][1]
                    along = abs(vx * c['u'][0] + vy * c['u'][1]); across = abs(-vx * c['u'][1] + vy * c['u'][0])
                    if da <= P['hold_dash_angle_tol_deg'] and across <= P['hold_dash_offset'] and along <= P['hold_dash_max_step']:
                        g.append(j); used.add(j); changed = True
                        break
        if len({tuple(round(v, 1) for v in dashes[k]['bbox']) for k in g}) >= P['hold_dash_min_count']:   # distinct dashes (a stroke drawn twice counts once)
            groups.append(g)
    centre_of = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    # hold annotations: 'ILS HOLD', or 'ILS' / 'CAT n' + 'HOLD' pieces. A HOLD inside a place name ("SCENIC HOLD PAD") is not one.
    def touching(a, b):   # same block of text: same angle, next word on the line or the next line (tight gaps)
        if abs((a.get('angle') or 0) - (b.get('angle') or 0)) > 2:
            return False
        A, B = a['bbox'], b['bbox']; h = max(A[3] - A[1], B[3] - B[1])
        xg, yg = max(B[0] - A[2], A[0] - B[2], 0), max(B[1] - A[3], A[1] - B[3], 0)
        return (yg == 0 and xg <= 0.6 * h) or (xg == 0 and yg <= 0.6 * h)
    HOLD_WORDS = {'ILS', 'HOLD', 'ILS HOLD', 'CAT', 'AREA', 'CRITICAL', 'CRITICAL AREA', 'AREA HOLD', 'I', 'II', 'III', '1', '2', '3', 'II/III'}
    for l in labels:
        if re.fullmatch(HOLD_ANN_RX, l['text']):
            block = [l] + [m for m in labels if m is not l and touching(l, m)]
            l['hold_annotation'] = not any(re.fullmatch(r'[A-Z]{3,}', m['text']) and m['text'] not in HOLD_WORDS and not re.fullmatch(HOLD_ANN_RX, m['text']) for m in block)   # e.g. SCENIC / HOLD / PAD
            if l['text'].startswith('CAT') and 'HOLD' not in l['text'] and not any('HOLD' in m['text'] for m in block):
                l['hold_annotation'] = False   # a bare "CAT 2" names the runway's CAT II/III markings, not a hold (KEWR)
            if l['hold_annotation']:
                # leaders: a straight line starting at this text block; its far end (tip) is where the bar is drawn
                tips = []
                for o in objs:
                    if o['class'] in ('linework', 'arrowhead') and o['stroke'] and not o['fill'] and not o.get('dash') and o['vertices'] <= 3 and o['long'] >= P['hold_leader_min_len']:
                        ends = [tuple(map(float, q.split(','))) for q in re.findall(r'-?\d+\.\d+,-?\d+\.\d+', o['d'])]
                        if len(ends) < 2:
                            continue
                        e0, e1 = ends[0], ends[-1]
                        for near_end, far_end in ((e0, e1), (e1, e0)):
                            if any(in_box(near_end, m['bbox'], P['hold_leader_start_gap']) for m in block) and not any(in_box(far_end, m['bbox'], 1.0) for m in block):
                                tips.append(far_end)
                l['hold_leader_tips'] = [[round(v, 2) for v in t] for t in tips]
    ils_labels = [(centre_of(l['bbox']), l['text']) for l in labels if l.get('hold_annotation')]
    ils_tips = [(tuple(t), l['text']) for l in labels if l.get('hold_annotation') for t in l.get('hold_leader_tips', [])]
    hold_groups, n = [], 0
    for g in groups:
        cs = [dashes[k]['centre'] for k in g]; c = (sum(x for x, _ in cs) / len(cs), sum(y for _, y in cs) / len(cs))
        perp = min((abs(-(c[0] - cx) * u[1] + (c[1] - cy) * u[0]) for (cx, cy), u, _, _ in rw_axes), default=1e9)
        closest = min(((math.hypot(c[0] - q[0], c[1] - q[1]), t) for q, t in ils_labels), default=(1e9, None))
        tip = min(((math.hypot(c[0] - q[0], c[1] - q[1]), t) for q, t in ils_tips), default=(1e9, None))
        if tip[0] <= P['hold_leader_tip_dist'] and closest[0] > P['hold_label_dist']:   # the leader only matters when the label is out of range
            closest = (tip[0], tip[1] + ' (via leader)')
        if perp <= P['centerline_ext_perp'] and len(g) >= 3:
            cls = 'runway_centerline_extension'
        elif closest[0] <= P['hold_label_dist']:
            cls = 'hold_line_candidate'
        else:
            continue
        for k in g:
            dashes[k]['class'] = cls; dashes[k]['group'] = n
        if cls == 'hold_line_candidate':
            xs = [v for k in g for v in (dashes[k]['bbox'][0], dashes[k]['bbox'][2])]; ys = [v for k in g for v in (dashes[k]['bbox'][1], dashes[k]['bbox'][3])]
            hold_groups.append({'group': n, 'dashes': len(g), 'bbox': [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
                                'nearest_annotation': closest[1], 'annotation_dist_pt': round(closest[0], 1), 'indices': [dashes[k]['index'] for k in g]})
        n += 1
    # a bar at a hold annotation's leader tip that the collinear-dash rule misses (one dash, or dashes bending with a curved
    # taxiway): the short dashes right at the tip are that bar
    for t, txt in ils_tips:
        if any(math.hypot(t[0] - (g['bbox'][0] + g['bbox'][2]) / 2, t[1] - (g['bbox'][1] + g['bbox'][3]) / 2) <= P['hold_leader_tip_dist'] for g in hold_groups):
            continue
        at = [d for d in dashes if d['class'] == 'linework' and math.hypot(d['centre'][0] - t[0], d['centre'][1] - t[1]) <= P['hold_tip_dash_dist']]
        if at and max(d['long'] for d in at) >= P['hold_tip_dash_min_len']:
            for d in at:
                d['class'] = 'hold_line_candidate'; d['group'] = n
            xs = [v for d in at for v in (d['bbox'][0], d['bbox'][2])]; ys = [v for d in at for v in (d['bbox'][1], d['bbox'][3])]
            hold_groups.append({'group': n, 'dashes': len(at), 'bbox': [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
                                'nearest_annotation': txt + ' (at leader tip)', 'annotation_dist_pt': 0.0, 'indices': [d['index'] for d in at]})
            n += 1
    # label refinements: runway ends, elevations, gate numbers
    buildings = [o for o in objs if o['class'] == 'building']
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    elev = [(centre(l['bbox']), l['angle']) for l in labels if l['text'] == 'ELEV']

    def bdist(pt, b):
        return math.hypot(max(b[0] - pt[0], 0, pt[0] - b[2]), max(b[1] - pt[1], 0, pt[1] - b[3]))
    # closed-pavement marks (FAA legend): chains of X marks, any rotation, >= 3 X's within a few points of each other
    xs = [l for l in labels if re.fullmatch(r'X+', l['text'])]
    seen, gapx = set(), lambda a, b: math.hypot(max(b['bbox'][0] - a['bbox'][2], 0, a['bbox'][0] - b['bbox'][2]), max(b['bbox'][1] - a['bbox'][3], 0, a['bbox'][1] - b['bbox'][3]))
    for i, l in enumerate(xs):
        if i in seen:
            continue
        chain, todo = {i}, [i]
        while todo:
            k = todo.pop()
            for j, m in enumerate(xs):
                if j not in chain and gapx(xs[k], m) <= P['closed_marking_gap']:
                    chain.add(j); todo.append(j)
        seen |= chain
        if sum(len(xs[j]['text']) for j in chain) >= P['closed_marking_min_x']:
            for j in chain:
                xs[j]['class'] = 'closed_marker'
    for l in xs:   # Franco R3: a standalone "X" glyph is a closed-pavement / closed-runway marker, never a taxiway name
        if l['class'] == 'taxiway_candidate':
            l['class'] = 'closed_marker'
    # a single capital letter right next to a building word ('TERMINAL', 'PIER', ...) names the building, not a taxiway
    terms = [l['bbox'] for l in labels if l['text'] in P['building_name_words']]   # TERMINAL, PIER, CONCOURSE, HANGAR (+ letter)
    gap = lambda a, b: math.hypot(max(b[0] - a[2], 0, a[0] - b[2]), max(b[1] - a[3], 0, a[1] - b[3]))
    for l in labels:
        if l['class'] == 'taxiway_candidate' and re.fullmatch(r'[A-Z]{1,2}', l['text']) and terms and min(gap(l['bbox'], t) for t in terms) <= P['terminal_label_gap']:
            l['class'] = 'terminal_label'
    # a word in a multi-word area name ("GA TRANSIENT RAMP", "APRON 2") names the area, not a taxiway. Only would-be taxiway
    # names are reclassified; other words keep their class (signed KLGA counts must not move)
    area = set(P['area_keywords'])
    def stacked(a, b):   # same letter height and angle; next word on the line, or next line of the same block
        ha, hb = a['frame']['h'], b['frame']['h']
        if abs(ha - hb) > 0.1 * ha or abs((a.get('angle') or 0) - (b.get('angle') or 0)) > 1:
            return False
        A, B = a['bbox'], b['bbox']
        xg, yg = max(B[0] - A[2], A[0] - B[2], 0), max(B[1] - A[3], A[1] - B[3], 0)
        cx = abs((A[0] + A[2]) / 2 - (B[0] + B[2]) / 2)   # stacked lines of one name are centred on each other (GA / TRANSIENT / RAMP);
        cy = abs((A[1] + A[3]) / 2 - (B[1] + B[3]) / 2)   # words on one line share a baseline (not just overlapping boxes)
        return (yg == 0 and xg <= P['area_word_gap'] * ha and cy <= 0.5 * ha) or (xg == 0 and yg <= P['area_line_gap'] * ha and cx <= P['area_line_centre_tol'] * ha)   # a taxiway label above a name is not (KORD Z1)
    words = [l for l in labels if re.fullmatch(r'[A-Z0-9-]+', l['text'])]
    seen = set()
    for i in range(len(words)):
        if i in seen:
            continue
        block, todo = {i}, [i]
        while todo:
            k = todo.pop()
            for j in range(len(words)):
                if j not in block and stacked(words[k], words[j]):
                    block.add(j); todo.append(j)
        seen |= block
        if len(block) >= 2 and any(words[j]['text'] in area for j in block):
            for j in block:
                if words[j]['class'] == 'taxiway_candidate':
                    words[j]['class'] = 'area_label'
    block_words = [w for w in labels if re.fullmatch(r'[A-Z]{2,}', w['text']) and w['text'] != 'ELEV' and w['class'] in ('note', 'annotation')]
    for l in labels:
        if l['class'] != 'number':
            continue
        c = centre(l['bbox'])
        digits = l['text'].isdigit()   # elevations and gate numbers are pure numbers; L/R/C suffixes only name runways
        # an elevation reads at the same angle as its ELEV label; a runway number is turned along its runway
        if digits and elev and min(math.hypot(c[0] - e[0], c[1] - e[1]) + (0 if abs(l['angle'] - ea) <= 2 else 1e9) for e, ea in elev) <= P['elev_label_dist']:
            l['class'] = 'elevation'
        elif any(w is not l and stacked(l, w) for w in block_words):   # a number in a text block with a word ("CAT / 2") belongs to that note
            l['class'] = 'note'
        elif re.match(r'^(0?[1-9]|[12][0-9]|3[0-6])[LRC]?$', l['text']) and beyond_runway_end(c) and not near(l['colour'] or (0, 0, 0), (1, 1, 1), 0.05):   # runway numbers are dark text (white digits belong to badges)
            l['class'] = 'runway_end'
        elif not digits:
            l['class'] = 'runway_reference'   # a runway named elsewhere on the chart, e.g. "28L RUN-UP"
        elif buildings and min(bdist(c, b['bbox']) for b in buildings) <= P['gate_label_building_dist']:
            l['class'] = 'gate_leadin'; l['gate'] = int(l['text'])   # gate lead-in, keyed by gate number
    # a tiny leftover stroke inside a symbol badge or outline glyph (e.g. the white arc of a black-disc callout) is part of it
    pad = P['symbol_detail_pad']
    hosts = [o['bbox'] for o in objs if o['class'] in ('symbol', 'glyph')]
    for o in objs:
        if o['class'] == 'unclassified' and o['long'] <= P['symbol_detail_max_len'] and any(
                h[0] - pad <= o['bbox'][0] and o['bbox'][2] <= h[2] + pad and h[1] - pad <= o['bbox'][1] and o['bbox'][3] <= h[3] + pad for h in hosts):
            o['class'] = 'symbol'
    # insets: an enlarged detail drawn at ANOTHER scale inside a large circle or box (e.g. KBOS terminals). Only when the
    # chart says "SEE INSET": the large outline holding that note marks the true-scale area (kept); every other large
    # outline is an inset, and everything inside it is inset content (not georeferenced, not drawn on the map)
    if any(l['text'] == 'INSET' for l in labels):
        see = [centre(l['bbox']) for l in labels if l['text'] == 'INSET']
        def inside(c, o):
            (cx, cy), r = o['centre'], o['long'] / 2
            return math.hypot(c[0] - cx, c[1] - cy) <= r if o['beziers'] else o['bbox'][0] <= c[0] <= o['bbox'][2] and o['bbox'][1] <= c[1] <= o['bbox'][3]
        outlines = [o for o in objs if o['class'] == 'linework' and o['stroke'] and not o['fill'] and o['aspect'] <= 1.3 and o['long'] >= P['inset_min_long']]
        for ins in [o for o in outlines if not any(inside(c, o) for c in see)]:
            ins['class'] = 'inset_frame'
            for o in objs:
                if o is not ins and o['class'] not in ('frame', 'graticule_line', 'inset_frame') and inside(o['centre'], ins):
                    o['class'] = 'inset_content'
            for l in labels:
                if inside(centre(l['bbox']), ins):
                    l['class'] = 'inset_label'
    return hold_groups, rw_ends


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf'); ap.add_argument('--icao', required=True); ap.add_argument('--rotation', type=int, choices=(0, 90, 180, 270))
    ap.add_argument('--debug-text-layer', action='store_true', help='review preview only: draw the hidden text layer over the outlines (class debug-text-layer); never in map outputs')
    a = ap.parse_args()
    pdf_path = Path(a.pdf).resolve()
    st = json.loads(STYLES.read_text())
    LABEL_BOX.update({k: st['post'][f'label_glyph_{k}'] for k in ('pre', 'post', 'pad')})
    paths, chars, (W, H) = read_pdf(pdf_path)
    rot = a.rotation if a.rotation is not None else dominant_rotation(chars)   # override: charts with no text layer (rotation from decoded outline text)
    R, (FW, FH) = make_rotator(rot, W, H)

    objs = []
    for i, p in enumerate(paths):
        subs = [[(s[0],) + tuple(R(*q) for q in s[1:]) for s in sub] for sub in p['subpaths']]
        allp = [q for sub in subs for s in sub for q in s[1:]]
        xs, ys = [q[0] for q in allp], [q[1] for q in allp]
        lg, sh, ang, cen, u = pca(allp) if len(allp) > 1 else (0, 0, 0, (xs[0], ys[0]), (1, 0))
        clip = None
        if p['clip']:
            csubs = [[(s[0],) + tuple(R(*q) for q in s[1:]) for s in sub] for sub in p['clip']['subpaths']]
            cpts = [q for sub in csubs for s in sub for q in s[1:]]
            cb = [min(q[0] for q in cpts), min(q[1] for q in cpts), max(q[0] for q in cpts), max(q[1] for q in cpts)]
            if (cb[2] - cb[0]) * (cb[3] - cb[1]) < 0.95 * W * H:       # page-sized clips change nothing
                clip = {'d': svg_d(csubs), 'evenodd': p['clip']['evenodd'], 'bbox': [round(v, 3) for v in cb]}
        objs.append({'index': i, 'd': svg_d(subs), 'subpaths': subs, 'subpaths_n': len(subs), 'fill': p['fill'], 'stroke': p['stroke'],
                     'evenodd': p['evenodd'], 'fill_rgb': p['fill_rgb'], 'stroke_rgb': p['stroke_rgb'], 'linewidth': round(p['linewidth'], 3),
                     'dash': p['dash'], 'clip': clip, 'bbox': [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)],
                     'long': round(lg, 3), 'short': round(sh, 3), 'aspect': round(lg / max(sh, 0.05), 2), 'axis_deg': round(ang, 1),
                     'centre': (round(cen[0], 3), round(cen[1], 3)), 'u': u, 'beziers': sum(1 for sub in subs for s in sub if s[0] == 'C'),
                     'vertices': sum(1 for sub in subs for s in sub if s[0] in ('M', 'L', 'C')),
                     'area': round(max((poly_area(anchors(sub)) for sub in subs if len(anchors(sub)) > 2), default=0.0), 3)})
    cand = [o for o in objs if o['stroke'] and not o['fill'] and o['long'] > 0.6 * max(FW, FH)]
    frame = max(cand, key=lambda o: (o['bbox'][2] - o['bbox'][0]) * (o['bbox'][3] - o['bbox'][1]), default=None)
    frame_box = frame['bbox'] if frame else [0, 0, FW, FH]
    frame_long = max(frame_box[2] - frame_box[0], frame_box[3] - frame_box[1])

    labels = group_labels(chars, R)
    if len(labels) < 20:   # (almost) no text layer, e.g. KPDK: decode the outline lettering with the shared FAA glyph library
        import outline_text
        labels += outline_text.decode(objs, frame_box)
    for lb in labels:
        lb['class'] = lb.get('force_class') or next(r['class'] for r in st['labels'] if re.match(r['regex'], lb['text']))
    classify(objs, labels, st, frame_long)
    if frame:
        frame['class'] = 'frame'
    hold_groups, rw_ends = post_pass(objs, labels, st, frame_box)
    gl = [o['centre'] for o in objs if o['class'] == 'glyph']
    # any small filled outline counts as lettering for the visible-text test (HS 1/HS 2 letters are brown outlines, class hot_spot)
    outl = [o['centre'] for o in objs if o['fill'] and o['long'] <= 15.0 and o['class'] not in ('pavement', 'label_background') and not near(o['fill_rgb'], (1, 1, 1), 0.02)]
    for lb in labels:   # glyphs = black outline glyphs assigned to this label; no outlines at all = drawn as real text (cycle-date banners)
        lb['glyphs'] = sum(1 for c in gl if label_hit(c, lb))
        lb['outlines'] = sum(1 for c in outl if label_hit(c, lb))
        lb['visible_text'] = lb['outlines'] == 0

    icao = a.icao.upper(); out = REPO / 'data/airports' / icao.lower()
    (out / 'build').mkdir(parents=True, exist_ok=True); (out / 'review').mkdir(parents=True, exist_ok=True)
    meta = {'icao': icao, 'pdf': str(pdf_path.relative_to(REPO)) if pdf_path.is_relative_to(REPO) else str(pdf_path),
            'pdf_sha256': hashlib.sha256(pdf_path.read_bytes()).hexdigest(), 'page_size_pt': [W, H], 'content_rotation_deg': rot,
            'frame_size_pt': [FW, FH], 'chart_frame_bbox': frame_box, 'extractor': 'pdfminer.six ' + pdfminer.__version__, 'styles': str(STYLES.relative_to(REPO)),
            'debug_text_layer_in_preview': bool(a.debug_text_layer)}
    for o in objs:
        o.pop('subpaths', None)
    (out / 'build/layers.raw.json').write_text(json.dumps({'meta': meta, 'objects': objs, 'labels': labels}))
    cc = collections.Counter(o['class'] for o in objs); lc = collections.Counter(l['class'] for l in labels)
    rep = {'meta': meta, 'objects_total': len(objs), 'objects_by_class': dict(cc.most_common()), 'labels_total': len(labels),
           'labels_by_class': dict(lc.most_common()),
           'fill_rule': dict(collections.Counter('evenodd' if o['evenodd'] else 'nonzero' for o in objs if o['fill'])),
           'multi_subpath_objects': sum(1 for o in objs if o['subpaths_n'] > 1), 'bezier_objects': sum(1 for o in objs if o['beziers']),
           'dashed_objects': [{'index': o['index'], 'class': o['class'], 'dash': o['dash']} for o in objs if o['dash']],
           'clipped_objects': [{'index': o['index'], 'class': o['class']} for o in objs if o['clip']],
           'runways': [{'index': o['index'], 'long_pt': o['long'], 'short_pt': o['short'], 'axis_deg': o['axis_deg']} for o in objs if o['class'] == 'runway'],
           'runway_end_points_pt': [[round(x, 2), round(y, 2)] for x, y in rw_ends], 'hold_line_groups': hold_groups,
           'hot_spot_labels': sorted({l['text'] for l in labels if l['class'] == 'hot_spot_label'}),
           'runway_end_labels': sorted({l['text'] for l in labels if l['class'] == 'runway_end'}, key=lambda s: (len(s), s)),
           'gate_leadins': sorted({l['gate'] for l in labels if l['class'] == 'gate_leadin'}),
           'elevations': sorted({l['text'] for l in labels if l['class'] == 'elevation'}),
           'taxiway_candidates': sorted({l['text'] for l in labels if l['class'] == 'taxiway_candidate'}),
           'terminal_labels': sorted({l['text'] for l in labels if l['class'] == 'terminal_label'}),
           'helipad_labels': sorted({l['text'] for l in labels if l['class'] == 'helipad_label'}),
           'unclassified_numbers': sorted({l['text'] for l in labels if l['class'] == 'number'}),
           'visible_text_labels': [l['text'] for l in labels if l['visible_text']],
           'label_glyph_shortfall': [{'text': l['text'], 'chars': l['chars_ns'], 'glyphs': l['glyphs']} for l in labels if 0 < l['glyphs'] < l['chars_ns']],
           'label_glyphs': [{'text': l['text'], 'chars': l['chars_ns'], 'glyphs': l['glyphs']} for l in labels],
           'unclassified_objects': [{k: o[k] for k in ('index', 'bbox', 'fill_rgb', 'stroke_rgb', 'fill', 'stroke', 'linewidth', 'area', 'aspect')} for o in objs if o['class'] == 'unclassified']}
    area_names = sorted({l['text'] for l in labels if l['class'] == 'area_label'})
    if area_names:   # only when present, so reports of airports without area labels keep their shape
        rep['area_labels'] = area_names
    (out / 'review/extract_report.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    update_checklist(out / 'review.json', objs, labels, rep)
    write_svgs(out / 'review', objs, labels, FW, FH, meta, debug_text_layer=a.debug_text_layer)
    show = ('objects_total', 'objects_by_class', 'labels_by_class', 'fill_rule', 'multi_subpath_objects', 'bezier_objects', 'clipped_objects', 'dashed_objects',
            'runways', 'hold_line_groups', 'hot_spot_labels', 'runway_end_labels', 'gate_leadins', 'elevations', 'unclassified_numbers', 'visible_text_labels')
    print(json.dumps({k: rep[k] for k in show}, ensure_ascii=False))
    print('unclassified objects', len(rep['unclassified_objects']))


HOLD_ANN_RX = r'(ILS( HOLD| CRITICAL AREA)?|HOLD|AREA HOLD|CRITICAL AREA|CAT( (I{1,3}|[123])(/(I{1,3}|[123]))?)?( HOLD)?)'   # every hold-line label style
P_LEADER_TIP = 20.0   # a hold bar within this of a hold annotation's leader tip covers it (pt)
P_OPEN_ARC = 0.8   # end gap / size above which a hot-spot curve is an open arc (a piece of an outline)


def update_checklist(path, objs, labels, rep):
    """Score the extraction-level checklist items (hot_spots, ils_hold_lines, unclassified) in review.json."""
    if not path.exists():
        return
    rv = json.loads(path.read_text())
    items = {i['id']: i for i in rv['items']}
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    hs_inst = [l['text'] for l in labels if l['class'] == 'hot_spot_label']
    hs_labels = sorted(set(hs_inst))
    # hot-spot outlines: brown stroked curves (circles or ellipses; often open arcs where the leader joins), one per callout
    def end_gap(o):
        n = [float(v) for v in re.findall(r'-?\d+\.\d+', o['d'])]
        return math.hypot(n[0] - n[-2], n[1] - n[-1]) / (o['long'] or 1) if len(n) >= 4 else 1
    # outlines: curves >= 10 pt that are not thin (curved leaders are thin, aspect > 6), or small round closed circles (KORD HS 2, 5.4 pt)
    hs_shapes = [o for o in objs if o['class'] == 'hot_spot' and o['stroke'] and not o['fill'] and o['beziers']
                 and ((o['long'] >= 10 and o['aspect'] <= 6) or (o['long'] >= 4 and o['aspect'] <= 1.5 and end_gap(o) <= 0.2))]
    # one outline may be drawn as several clearly open arcs (KJFK: an ellipse in two halves): overlapping open arcs count
    # once. Nearly closed outlines (end gap <= 0.8 of their size, e.g. KLGA's open circles) always count on their own
    def open_arc(o):
        n = [float(v) for v in re.findall(r'-?\d+\.\d+', o['d'])]
        return len(n) >= 4 and math.hypot(n[0] - n[-2], n[1] - n[-1]) > P_OPEN_ARC * o['long']
    clusters = []
    for o in hs_shapes:
        if not open_arc(o):
            clusters.append([o]); continue
        hit = [c for c in clusters if open_arc(c[0]) and any(not (o['bbox'][2] < x['bbox'][0] or x['bbox'][2] < o['bbox'][0] or o['bbox'][3] < x['bbox'][1] or x['bbox'][3] < o['bbox'][1]) for x in c)]
        clusters = [c for c in clusters if c not in hit] + [[o] + [x for c in hit for x in c]]
    hs_shapes = clusters
    if 'hot_spots' in items:
        ok = len(hs_inst) == len(hs_shapes)
        items['hot_spots'].update(status=('pass' if ok and hs_inst else 'not_applicable' if not hs_inst and not hs_shapes else 'fail'),
                                  result={'labels': hs_labels, 'label_callouts': len(hs_inst), 'shapes': len(hs_shapes), 'checked_at': stamp})
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    ann = []   # hold annotations (see post_pass), deduplicated by position; each with its leader tips
    for l in labels:
        if l.get('hold_annotation'):
            c = centre(l['bbox'])
            same = next((a for a in ann if math.hypot(c[0] - a[0][0], c[1] - a[0][1]) <= 14), None)
            if same:
                same[1].extend(l.get('hold_leader_tips', []))
            else:
                ann.append((c, list(l.get('hold_leader_tips', []))))
    holds = rep['hold_line_groups']
    near = lambda p, g, d: math.hypot(p[0] - centre(g['bbox'])[0], p[1] - centre(g['bbox'])[1]) <= d
    covered = [any(near(a, g, 50) or any(near(t, g, P_LEADER_TIP) for t in tips) for g in holds) for a, tips in ann]
    if 'ils_hold_lines' in items:
        items['ils_hold_lines'].update(status=('not_applicable' if not ann else 'pass' if all(covered) else 'fail'),
                                       result={'annotations': len(ann), 'hold_lines': len(holds), 'annotations_with_hold_line': sum(covered), 'checked_at': stamp})
    if 'lahso_points' in items:   # display only: LAHSO callouts vs dashed hold-short markers across a runway
        lab = [centre(l['bbox']) for l in labels if l['text'] == 'LAHSO']
        mk = [o['centre'] for o in objs if o['class'] == 'lahso_point']
        covered = [bool(l.get('lahso_markers')) for l in labels if l['text'] == 'LAHSO']
        items['lahso_points'].update(status='not_applicable' if not lab and not mk else 'pass' if all(covered) and mk else 'fail',
                                     result={'labels': len(lab), 'markers': len(mk), 'labels_with_marker': sum(covered), 'display_only': True, 'checked_at': stamp})
    if 'unclassified' in items:
        items['unclassified'].update(status='pass' if not rep['unclassified_objects'] else 'fail',
                                     result={'unclassified_objects': len(rep['unclassified_objects']), 'unclassified_numbers': rep['unclassified_numbers'], 'checked_at': stamp})
    path.write_text(json.dumps(rv, indent=1, ensure_ascii=False))


PREVIEW = {'arrowhead': '#2f6f9f', 'runway_centerline_extension': '#7a7a7a', 'label_background': None, 'pavement': '#c9c9c9', 'glyph': '#b0b0ff', 'graticule_label': '#ff7a7a', 'building': '#5b4a8a', 'runway': '#111111',
           'runway_marking': None, 'hot_spot': '#9a5200', 'symbol': '#2f6f9f', 'hatch': '#2f9f8f', 'linework': '#3a8f3a', 'graticule_line': '#d62f2f',
           'graticule_tick': '#d62f2f', 'hold_line_candidate': '#e0007a', 'frame': '#999999', 'unclassified': '#ff00ff'}
LABEL_COL = {'closed_marker': '#6a6a6a', 'graticule': '#d62f2f', 'runway_reference': '#0040c0', 'terminal_label': '#5b4a8a', 'area_label': '#5b4a8a', 'helipad_label': '#2f6f9f', 'hot_spot_label': '#9a5200', 'runway_end': '#0040c0', 'taxiway_candidate': '#007a33', 'gate_leadin': '#a05000', 'lahso_point': '#7a7a7a', 'elevation': '#6a6a6a',
             'heading': '#6a6a6a', 'dimension': '#6a6a6a', 'number': '#ff00ff'}


def _rgb(c):
    return 'none' if c is None else '#%02x%02x%02x' % tuple(max(0, min(255, round(v * 255))) for v in c)


def write_svgs(folder, objs, labels, FW, FH, meta, debug_text_layer=False):
    clips, defs = {}, []
    for o in objs:
        if o['clip'] and o['clip']['d'] not in clips:
            cid = f'clip{len(clips)}'; clips[o['clip']['d']] = cid
            defs.append(f'<clipPath id="{cid}"><path d="{o["clip"]["d"]}" clip-rule="{"evenodd" if o["clip"]["evenodd"] else "nonzero"}"/></clipPath>')

    def path(o, fill, stroke):
        a = [f'd="{o["d"]}"', f'fill="{fill}"', f'fill-rule="{"evenodd" if o["evenodd"] else "nonzero"}"']
        if stroke != 'none':
            a += [f'stroke="{stroke}"', f'stroke-width="{max(o["linewidth"], 0.1):.3f}"', 'stroke-linejoin="round"']
            if o['dash']:
                a.append(f'stroke-dasharray="{" ".join(f"{v:.3f}" for v in o["dash"][0])}" stroke-dashoffset="{o["dash"][1]:.3f}"')
        else:
            a.append('stroke="none"')
        if o['clip']:
            a.append(f'clip-path="url(#{clips[o["clip"]["d"]]})"')
        return '<path ' + ' '.join(a) + '/>'

    esc = lambda t: t.replace('&', '&amp;').replace('<', '&lt;')
    # faithful: original colours, paint order, fill rule, dashes, clips; visible-text labels as text
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FW} {FH}" width="{FW}" height="{FH}" preserveAspectRatio="none">',
         f'<defs>{"".join(defs)}</defs>', f'<rect width="{FW}" height="{FH}" fill="#ffffff"/>']
    for o in objs:
        s.append(path(o, _rgb(o['fill_rgb']) if o['fill'] else 'none', _rgb(o['stroke_rgb']) if o['stroke'] else 'none'))
    for lb in labels:
        if lb['visible_text']:
            s.append(f'<text x="{lb["x"]}" y="{lb["y"]}" font-size="{lb["size"]}" font-family="Helvetica,Arial,sans-serif" fill="{_rgb(lb["colour"]) if lb["colour"] else "#000"}" '
                     f'textLength="{lb["run"]}" lengthAdjust="spacingAndGlyphs" transform="rotate({lb["angle"]} {lb["x"]} {lb["y"]})">{esc(lb["text"])}</text>')
    s.append('</svg>')
    (folder / 'faithful.svg').write_text('\n'.join(s))
    # review preview: class colours (preview-only styling), same geometry and paint order; white fills keep their colour
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -40 {FW + 20} {FH + 60}" font-family="Helvetica,Arial,sans-serif">', f'<defs>{"".join(defs)}</defs>',
         f'<rect x="-10" y="-40" width="{FW + 20}" height="{FH + 60}" fill="#ffffff"/>',
         f'<text x="0" y="-26" font-size="8">EXTRACTION PREVIEW (review only, not for navigation). {meta["icao"]} · {meta["pdf"]} · sha256 {meta["pdf_sha256"][:12]}… · rotation {meta["content_rotation_deg"]}° undone</text>',
         '<text x="0" y="-14" font-size="6">' + ' '.join(f'<tspan fill="{c or "#bbbbbb"}">■ {k}</tspan>' for k, c in PREVIEW.items()) + '</text>']
    for o in objs:
        col = PREVIEW.get(o['class'], '#ff00ff')
        if col is None or near(o['fill_rgb'] if o['fill'] else o['stroke_rgb'], (1, 1, 1), 0.02):
            col = _rgb(o['fill_rgb'] if o['fill'] else o['stroke_rgb'])
        s.append(path(o, col if o['fill'] else 'none', col if o['stroke'] else 'none'))
    if debug_text_layer:   # DEBUG ONLY (review/ outputs, --debug-text-layer): hidden text layer drawn over the outlines
        s.append('<g class="debug-text-layer" opacity="0.85">')
        for lb in labels:
            if lb['class'] in LABEL_COL:
                s.append(f'<text class="debug-text-layer" x="{lb["x"]}" y="{lb["y"]}" font-size="{lb["size"]}" fill="{LABEL_COL[lb["class"]]}" '
                         f'transform="rotate({lb["angle"]} {lb["x"]} {lb["y"]})">{esc(lb["text"])}</text>')
        s.append('</g>')
    s.append('</svg>')
    (folder / 'extract_preview.svg').write_text('\n'.join(s))


if __name__ == '__main__':
    sys.exit(main())
