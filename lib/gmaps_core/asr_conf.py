"""Whisper word confidence for a runway designator (Franco, Oct 9): a readback-mismatch candidate needs the speech recogniser
to be confident about the runway it heard. words = [{'w': word as Whisper wrote it, 'p': min token probability}].

runway_confidence(words, '27R') -> the minimum confidence over the words that spell the designator ('two' 'seven' 'right',
'27R', '2-7 right', '27' 'right', ...), preferring an occurrence right after 'runway'; None if the designator is not found.
"""
import re

_NUM = {'ZERO': '0', 'OH': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'TREE': '3', 'FOUR': '4', 'FIVE': '5', 'FIFE': '5', 'SIX': '6',
        'SEVEN': '7', 'EIGHT': '8', 'NINE': '9', 'NINER': '9'}
_SIDE = {'LEFT': 'L', 'RIGHT': 'R', 'CENTER': 'C', 'CENTRE': 'C'}


def _parts(words):
    out = []   # (kind, value, p): kind digit / side / runway / other
    for w in words or []:
        t = re.sub(r'[^A-Za-z0-9-]', '', str(w.get('w', ''))).upper()
        for piece in [x for x in t.split('-') if x]:
            m = re.fullmatch(r'(\d+)([LRC]?)', piece)
            if m:
                out += [('digit', d, w['p']) for d in m.group(1)] + ([('side', m.group(2), w['p'])] if m.group(2) else [])
            elif piece in _NUM:
                out.append(('digit', _NUM[piece], w['p']))
            elif piece in _SIDE:
                out.append(('side', _SIDE[piece], w['p']))
            elif piece == 'RUNWAY':
                out.append(('runway', None, w['p']))
            else:
                out.append(('other', piece, w['p']))
    return out


def runway_confidence(words, runway):
    m = re.fullmatch(r'0*(\d{1,2})([LRC]?)', str(runway or ''))
    if not m or not words:
        return None
    num, side = m.group(1), m.group(2)
    P = _parts(words)
    best = None
    for i in range(len(P)):
        j, ok = i, True
        for d in num:
            if j < len(P) and P[j][0] == 'digit' and P[j][1] == d:
                j += 1
            else:
                ok = False; break
        if not ok or (i > 0 and P[i - 1][0] == 'digit') or (j < len(P) and P[j][0] == 'digit'):
            continue
        span = P[i:j]
        if side:
            if j < len(P) and P[j][0] == 'side' and P[j][1] == side:
                span = P[i:j + 1]
            else:
                continue
        conf = round(min(x[2] for x in span), 4)
        after_runway = i > 0 and P[i - 1][0] == 'runway'
        if best is None or (after_runway and not best[1]):
            best = (conf, after_runway)
    return best[0] if best else None
