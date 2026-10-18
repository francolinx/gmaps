"""Text normalisation for ATC phraseology (prepared library, stdlib only).

Order matters:
  1. known ASR repairs (from phraseology_grammar.json asr_repairs_observed)
  2. callsign extraction happens in parser.py on the repaired text BEFORE phonetics are collapsed,
     so 'Delta 520' (airline) is not turned into taxiway D.
  3. number words -> digits, runway suffix words -> L/R/C
  4. ICAO phonetics -> letters; identical adjacent phonetics -> double letter (AA);
     phonetic + single digit -> letter+digit (D1) for numbered taxiways.
Nothing here guesses: unknown words pass through unchanged.
"""
import re

NUMBER_WORDS = {'ZERO': '0', 'OH': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'TREE': '3', 'FOUR': '4',
                'FIVE': '5', 'FIFE': '5', 'SIX': '6', 'SEVEN': '7', 'EIGHT': '8', 'NINE': '9', 'NINER': '9'}
PHONETICS = {'ALPHA': 'A', 'ALFA': 'A', 'BRAVO': 'B', 'CHARLIE': 'C', 'DELTA': 'D', 'ECHO': 'E', 'FOXTROT': 'F',
             'GOLF': 'G', 'HOTEL': 'H', 'INDIA': 'I', 'JULIET': 'J', 'JULIETT': 'J', 'KILO': 'K', 'LIMA': 'L',
             'MIKE': 'M', 'NOVEMBER': 'N', 'OSCAR': 'O', 'PAPA': 'P', 'QUEBEC': 'Q', 'ROMEO': 'R', 'SIERRA': 'S',
             'TANGO': 'T', 'UNIFORM': 'U', 'VICTOR': 'V', 'WHISKEY': 'W', 'XRAY': 'X', 'X-RAY': 'X', 'YANKEE': 'Y',
             'ZULU': 'Z'}
SUFFIX = {'LEFT': 'L', 'RIGHT': 'R', 'CENTER': 'C', 'CENTRE': 'C'}
FILLERS = {'UH', 'UM', 'ER', 'AH', 'OKAY', 'OK', 'PLEASE', 'JUST'}
# Words after which a following bare number is a runway designator.
RUNWAY_CONTEXT = {'RUNWAY', 'CROSS', 'CROSSING', 'ACROSS', 'OF', 'ON', 'TO', 'LAND', 'TAKEOFF', 'SHORT'}

ASR_REPAIRS = [
    ('host short of', 'hold short of'), ('host runway', 'hold short runway'), ('i should mic', 'hold short of mike'),
    ('hold your text to wake kila', 'hold short of kilo'), ('kila', 'kilo'), ('boarding the takeoff', 'aborting the takeoff'),
    ('retracted on runway', 'rejecting on runway'), ('crossing for adelva', 'crossing 4 at delta'),
    ('adelva', 'at delta'), ('clear for takeoff', 'cleared for takeoff'), ('clear to land', 'cleared to land'),
]


ASR_REGEX_REPAIRS = [
    (r'\bline up,? and (?:wait|weight)\b', 'line up and wait', 'line up and wait/weight'),
    (r'\b[a-z]*ear(?:ed)?,? (for takeoff|to land)\b', r'cleared \1', '<x>ear(ed) for takeoff / to land'),   # appeared / speared / fear / clear
]


def repair(text: str):
    """Apply observed ASR repairs. Returns (text, list_of_repairs_applied) so the change is auditable."""
    low = ' ' + text.lower() + ' '
    applied = []
    for bad, good in ASR_REPAIRS:
        if bad in low:
            low = low.replace(bad, good)
            applied.append((bad, good))
    for rx, good, tag in ASR_REGEX_REPAIRS:   # round 2 (Franco, Oct 9; dev3 Whisper text): only inside these fixed phrases
        new = re.sub(rx, good, low)
        if new != low:
            applied.append((tag, good)); low = new
    return low.strip(), applied


def tokenize(text: str):
    t = text.upper()
    t = t.replace('-', ' ').replace('/', ' ')
    t = re.sub(r'([,.;:?!])', r' \1 ', t)
    t = re.sub(r"[^A-Z0-9,.;:?!' ]", ' ', t)
    return [w for w in t.split() if w not in FILLERS]


def collapse_numbers(tokens):
    """ONE THREE -> 13 ; 3 3 LEFT -> 33L (only when preceded by runway context or followed by a suffix)."""
    out = []
    i = 0
    while i < len(tokens):
        w = tokens[i]
        if w in NUMBER_WORDS or re.fullmatch(r'\d', w):
            digits = []
            j = i
            while j < len(tokens) and (tokens[j] in NUMBER_WORDS or re.fullmatch(r'\d', tokens[j])):
                digits.append(NUMBER_WORDS.get(tokens[j], tokens[j]))
                j += 1
            num = ''.join(digits)
            if j < len(tokens) and tokens[j] in SUFFIX and len(num) <= 2:
                num += SUFFIX[tokens[j]]
                j += 1
            out.append(num)
            i = j
            continue
        if re.fullmatch(r'\d{1,2}', w) and i + 1 < len(tokens) and tokens[i + 1] in SUFFIX:
            out.append(w + SUFFIX[tokens[i + 1]])
            i += 2
            continue
        m = re.fullmatch(r'(\d{1,2})([LRC])', w)
        out.append(w if not m else m.group(1) + m.group(2))
        i += 1
    return out


def collapse_phonetics(tokens):
    """DELTA -> D ; ALPHA ALPHA -> AA ; DOUBLE ALPHA -> AA ; DELTA 1 -> D1 (single digit only)."""
    out = []
    i = 0
    while i < len(tokens):
        w = tokens[i]
        if w == 'DOUBLE' and i + 1 < len(tokens) and tokens[i + 1] in PHONETICS:
            out.append(PHONETICS[tokens[i + 1]] * 2)
            i += 2
            continue
        if w in PHONETICS:
            letter = PHONETICS[w]
            if i + 1 < len(tokens) and tokens[i + 1] == w:
                out.append(letter * 2)
                i += 2
                continue
            if i + 1 < len(tokens) and re.fullmatch(r'\d', tokens[i + 1]):
                out.append(letter + tokens[i + 1])
                i += 2
                continue
            out.append(letter)
            i += 1
            continue
        out.append(w)
        i += 1
    return out


def canonical_runway(token: str):
    """'4' -> '04', '33L' -> '33L'. Returns None if not a runway-shaped token (1..36 + optional suffix)."""
    m = re.fullmatch(r'(\d{1,2})([LRC]?)', token or '')
    if not m:
        return None
    n = int(m.group(1))
    if not 1 <= n <= 36:
        return None
    return f'{n:02d}{m.group(2)}'


def normalize(text: str):
    """Full normalisation (used on the part of the utterance left after callsigns were removed)."""
    repaired, applied = repair(text)
    toks = collapse_phonetics(collapse_numbers(tokenize(repaired)))
    return ' '.join(toks), applied

# ---------- Whisper number forms, repaired ONLY in callsign and runway contexts (Franco, Oct 9; synthetic dev split) ----------
_DIGIT_WORD = {'NINER': '9', 'NINERS': '9', 'NINE': '9', 'TREE': '3', 'FIFE': '5'}


def _numish(t):
    return bool(re.fullmatch(r'\d+(ER)?', t)) or t in NUMBER_WORDS or t in _DIGIT_WORD


def _digits(t):
    m = re.fullmatch(r'(\d+)(ER)?', t)
    return m.group(1) if m else NUMBER_WORDS.get(t) or _DIGIT_WORD.get(t)


# JO 7110.65BB 2-4-20a2: an airline call sign is followed by "the flight number in group form" ("American Fifty-Two",
# "Delta One Hundred", "General Motors Thirty Fifteen", "TWA Ten Zero Four", "American Four Ninety Two"). Read ONLY
# after an airline word (round 2, Franco Oct 9).
_TEENS = {'TEN': 10, 'ELEVEN': 11, 'TWELVE': 12, 'THIRTEEN': 13, 'FOURTEEN': 14, 'FIFTEEN': 15, 'SIXTEEN': 16,
          'SEVENTEEN': 17, 'EIGHTEEN': 18, 'NINETEEN': 19}
_TENS = {'TWENTY': 2, 'THIRTY': 3, 'FORTY': 4, 'FOURTY': 4, 'FIFTY': 5, 'SIXTY': 6, 'SEVENTY': 7, 'EIGHTY': 8, 'NINETY': 9}


def _unit(t):
    d = NUMBER_WORDS.get(t) or _DIGIT_WORD.get(t) or (t if re.fullmatch(r'\d', t) else None)
    return d


def _below_100(toks, k):
    """'fifty' / 'fifty two' / 'twelve' / 'five' at toks[k] -> (value, next index) or None."""
    if k < len(toks) and toks[k] in _TEENS:
        return _TEENS[toks[k]], k + 1
    if k < len(toks) and toks[k] in _TENS:
        u = _unit(toks[k + 1]) if k + 1 < len(toks) else None
        return (_TENS[toks[k]] * 10 + int(u), k + 2) if u not in (None, '0') else (_TENS[toks[k]] * 10, k + 1)
    if k < len(toks) and _unit(toks[k]) is not None:
        return int(_unit(toks[k])), k + 1
    return None


def group_form_number(toks, j):
    """Flight number in group form starting at toks[j] -> (digits, next index), or None if no group word is used.
    'fourteen oh nine' 1409, 'twelve thirty one' 1231, 'four ninety two' 492, 'ten forty two' 1042, 'thirty fifteen' 3015,
    'one hundred' 100, 'two hundred five' 205, 'ten zero four' 1004."""
    out, k, grouped = '', j, False
    while k < len(toks) and len(out) < 4:
        t = toks[k]
        if t in _TEENS or t in _TENS:
            v, k = _below_100(toks, k); out += f'{v:02d}'; grouped = True
        elif t == 'HUNDRED' and len(out) in (1, 2):
            # 'one hundred' (100), 'two hundred five' (205); and, 2-4-20a2 group form for four digits, 'twenty four hundred' (2400),
            # 'twelve hundred five' (1205). The four-digit form is NOT YET HELD-OUT VALIDATED (Franco, Oct 9: judged on real clips)
            rest = _below_100(toks, k + 1)
            out += f'{rest[0]:02d}' if rest else '00'; k = rest[1] if rest else k + 1; grouped = True
        elif _unit(t) is not None:
            out += _unit(t); k += 1
        else:
            break
    return (out, k) if grouped and 2 <= len(out) <= 4 else None


def context_number_repairs(tokens, airlines):
    """Repair Whisper's renderings of ATC numbers where a callsign or runway number is expected:
      callsign (after an airline word): 'SOUTHWEST TO 284' -> 2284 ; '739ER' -> 739 ; '9 OR 6 2' / 'NINERS 6 2' -> 962
      runway (after RUNWAY): 'TO SIX LEFT' -> 2 6 LEFT ; '26 , RIGHT' / '9 ARE RIGHT' / '9 OR LEFT' -> 26 RIGHT / 9 RIGHT / 9 LEFT
      airline words: 'JET BLUE' -> JETBLUE ; 'ENDEAVOUR' -> ENDEAVOR
    Anything else is left untouched. Returns (tokens, repairs_applied)."""
    out, applied, i = [], [], 0
    toks = []
    k = 0
    while k < len(tokens):   # airline spellings first
        if tokens[k] == 'JET' and k + 1 < len(tokens) and tokens[k + 1] == 'BLUE':
            toks.append('JETBLUE'); applied.append(('jet blue', 'jetblue')); k += 2; continue
        if tokens[k] == 'ENDEAVOUR':
            toks.append('ENDEAVOR'); applied.append(('endeavour', 'endeavor')); k += 1; continue
        toks.append(tokens[k]); k += 1
    while i < len(toks):
        w = toks[i]; out.append(w); i += 1
        if w in airlines and group_form_number(toks, i):   # 2-4-20a2 group form ('fourteen oh nine' -> 1409)
            num, i = group_form_number(toks, i)
            out.append(num); applied.append(('group-form flight number', num))
            continue
        if w in airlines:   # callsign number run
            run, j, fixed = [], i, False
            while j < len(toks):
                t = toks[j]
                if _numish(t):
                    fixed |= t in _DIGIT_WORD and t not in ('NINER', 'NINE') or t.endswith('ER') and t[:-2].isdigit()
                    run.append(_digits(t)); j += 1
                elif t == 'TO' and not run and j + 1 < len(toks) and _numish(toks[j + 1]):
                    run.append('2'); fixed = True; j += 1
                elif t in ('OR', 'ARE') and run and j + 1 < len(toks) and _numish(toks[j + 1]):
                    fixed = True; j += 1
                else:
                    break
            num = ''.join(run)
            if fixed and 2 <= len(num) <= 4:
                out.append(num); applied.append(('callsign number', num)); i = j
        elif w == 'FOR' and len(out) >= 3 and out[-3:-1] in (['TO', 'LAND'], ['AND', 'WAIT'], ['FOR', 'TAKEOFF']) and (i >= len(toks) or toks[i] in airlines or toks[i] in (',', '.')):
            out[-1] = '4'; applied.append(("'for' after a clearance", '4'))   # 'cleared to land for, Endeavor 1988' -> runway 4
        elif w == 'RUNWAY' or (w in ('CROSS', 'CROSSING') and i < len(toks) and _numish(toks[i])):   # runway number run, then a side that Whisper split off
            run, j, fixed = [], i, False
            while j < len(toks) and len(''.join(run)) < 2:
                t = toks[j]
                if _numish(t) and len(_digits(t) or '') <= 2:
                    run.append(_digits(t)); fixed |= t in _DIGIT_WORD and t not in ('NINER', 'NINE'); j += 1
                elif t == 'TO' and not run and j + 1 < len(toks) and _numish(toks[j + 1]):
                    run.append('2'); fixed = True; j += 1
                elif t == ',' and len(run) == 1 and len(run[0]) == 1 and j + 1 < len(toks) and _numish(toks[j + 1]) and len(_digits(toks[j + 1]) or '') == 1:
                    fixed = True; j += 1   # 'runway 2, 7 left': one designator split by a comma
                else:
                    break
            if run and j + 1 < len(toks) and toks[j] in (',', 'ARE', 'OR') and toks[j + 1] in SUFFIX and (j + 2 >= len(toks) or toks[j + 2] != 'TURN'):
                fixed = True; side = toks[j + 1]; j += 2
            else:
                side = None
            if fixed:
                out += [''.join(run)] + ([side] if side else []); applied.append(('runway number', ''.join(run) + (SUFFIX[side] if side else ''))); i = j
    return out, applied

