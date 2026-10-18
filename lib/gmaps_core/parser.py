"""Grammar-first ATC utterance parser (prepared library, stdlib only).

Implements the pattern families in data/specs/phraseology_grammar.json and emits events with the
fields required by data/airports/runtime_mapping_contract.json. A language model is NOT called here;
anything the grammar cannot type is returned with speech_act='unknown' and parse_path='residue'
so a local model can be asked later (the venue build decides how).

Rules kept from the contract:
  * a request is never an authorization;
  * a slot that was not heard stays null (never filled from context);
  * runway suffixes and double letters are preserved;
  * identifiers are resolved against the selected airport only (airport.py).
"""
import itertools
import re
from .normalize import repair, tokenize, collapse_numbers, collapse_phonetics, canonical_runway, NUMBER_WORDS, PHONETICS, context_number_repairs

AIRLINES = {'UNITED': 'UAL', 'DELTA': 'DAL', 'AMERICAN': 'AAL', 'JAZZ': 'JZA', 'FRONTIER': 'FFT', 'JETBLUE': 'JBU',
            'SOUTHWEST': 'SWA', 'SPIRIT': 'NKS', 'REPUBLIC': 'RPA', 'ENDEAVOR': 'EDV', 'TRADEWIND': 'GPD',
            'CACTUS': 'AWE', 'BRICKYARD': 'RPA', 'SKYWEST': 'SKW', 'ENVOY': 'ENY', 'PIEDMONT': 'PDT', 'COBALT': 'CBT',
            'SPEEDBIRD': 'BAW', 'SIMAIR': 'SIM', 'AIRCANADA': 'ACA'}
VEHICLES = {'TRUCK': 'TRUCK', 'OPS': 'OPS', 'OPERATIONS': 'OPS', 'RESCUE': 'RESCUE', 'CAR': 'CAR', 'FIRE': 'FIRE'}
# JO 7110.65BB 2-4-20a1: U.S. civil registrations, "November One Two Three Four Golf", or the type in place of November
# ("Jet Commander One Two Three Four Papa", "Bonanza One Two Three Four Tango", "Twin Cessna Four Four Golf")
CIVIL_TYPES = {'BONANZA', 'BARON', 'CESSNA', 'CITATION', 'SIKORSKY', 'PIPER', 'CIRRUS', 'MOONEY', 'LEARJET', 'GULFSTREAM',
               'CHALLENGER', 'BEECH', 'BEECHCRAFT', 'COMMANDER', 'KINGAIR', 'SKYHAWK', 'CHEROKEE', 'NAVAJO'}
_ids = itertools.count(1)
RWY = r'(?P<{n}>\d{{1,2}}[LRC]?)'
TWY = r'[A-Z]{1,2}\d?'
# JO 7110.65BB: what may stand between 'RUNWAY (number)' and the clearance: an intersection (3-9-4m, 3-9-10b),
# 'full length' / 'shortened' / 'intersection departure' (3-9-10c,g; 3-10-5f) and the surface wind (3-9-10i, 3-10-5e; 2-4-17g)
_SEP = r'(?: [,.])?'
_RWY_CLAUSE = (r'(?: AT [A-Z0-9]+' + _SEP + r')?(?: (?:FULL LENGTH|INTERSECTION DEPARTURE|SHORTENED)' + _SEP + r')*'
               r'(?: WIND \d{1,3} AT \d{1,2}(?: [A-Z]+ \d{1,2})?' + _SEP + r')?')   # 'gusts' or one misheard word before the gust speed


def _num_from_words(words):
    digits = []
    for w in words:
        if w in NUMBER_WORDS:
            digits.append(NUMBER_WORDS[w])
        elif re.fullmatch(r'\d+', w):
            digits.append(w)
        else:
            break
    return ''.join(digits), len(digits)


def extract_callsigns(tokens):
    """Find airline/vehicle callsigns on RAW tokens (before phonetics collapse).
    Returns list of (callsign, start_index, end_index) and the tokens with callsign spans removed."""
    found = []
    i = 0
    keep = []
    while i < len(tokens):
        w = tokens[i]
        route_ctx = bool(keep) and keep[-1] in ('VIA', 'AT', 'ON', 'OF', 'AND', ',', 'THEN', 'CROSS', 'SHORT', 'TAXIWAY')
        long_num = i + 1 < len(tokens) and (bool(re.fullmatch(r'\d{3,4}', tokens[i + 1])) or len(_num_from_words(tokens[i + 1:i + 6])[0] or '') >= 3)   # 'at Delta 1432' / '..., Delta eight four five': a flight number (taxiway names have at most 2 digits)
        if w in AIRLINES and i + 1 < len(tokens) and not (w in ('DELTA',) and route_ctx and not long_num):
            num, n = _num_from_words(tokens[i + 1:i + 6])
            if num and 2 <= len(num) <= 4:
                found.append((AIRLINES[w] + num, i, i + n))
                i += 1 + n
                continue
        nov_ok = w == 'NOVEMBER' and not (keep and keep[-1] in ('VIA', 'AT', 'ON', 'OF', 'SHORT', 'CROSS', 'TAXIWAY'))   # 'via November 2' is a taxiway
        if nov_ok or w in CIVIL_TYPES:   # civil registration -> N-number (the length rule below keeps taxiway names N2 / N12 out)
            num, n = _num_from_words(tokens[i + 1:i + 6])
            letters = []
            k = i + 1 + n
            while k < len(tokens) and tokens[k] in PHONETICS and len(letters) < 2:
                letters.append(PHONETICS[tokens[k]]); k += 1
            body = num + ''.join(letters)
            if num and num[0] != '0' and len(body) <= 5 and (w != 'NOVEMBER' or len(num) >= 3 or letters and len(num) >= 2):
                found.append(('N' + body, i, k - 1))
                i = k
                continue
        if w in VEHICLES and i + 1 < len(tokens):
            num, n = _num_from_words(tokens[i + 1:i + 3])
            if num:
                end = i + 1 + n
                if tokens[end:end + 2] == ['AND', 'COMPANY']:
                    end += 2
                found.append((VEHICLES[w] + num, i, end - 1))
                i = end
                continue
        # Abbreviated callsign: bare 2-4 digit flight number at the start (optionally after a facility word)
        # or at the end ('2384, ...', 'Ground, 2384 is on Bravo', '..., 263'). Altitudes/headings excluded.
        words_before = [x for x in keep if x not in (',', '.', '?', '!')]
        prev = words_before[-1] if words_before else None
        at_start = not words_before or (len(words_before) == 1 and words_before[0] in ('GROUND', 'TOWER', 'ROGER'))
        at_end = all(x in (',', '.', '?', '!') for x in tokens[i + 1:])
        if re.fullmatch(r'\d{3,4}', w) and (at_start or at_end) \
                and prev not in ('RUNWAY', 'ON', 'TO', 'OF', 'AT', 'CONTACT', 'HEADING', 'MAINTAIN', 'CLIMB', 'DESCEND', 'ALTITUDE', 'GATE', 'LANE') \
                and not (len(w) == 4 and w.endswith('00')):
            found.append(('?' + w, i, i))
            i += 1
            continue
        keep.append(w)
        i += 1
    return found, keep


_VALID = None  # set per call to a predicate for 2-distinct-letter tokens (airport inventory)


def _is_twy(t):
    if re.fullmatch(r'[A-Z]\d?|([A-Z])\1', t):
        return True
    if re.fullmatch(r'[A-Z]{2}', t):
        return _VALID(t) if _VALID else True
    return False


def _route(text):
    """'N , A , E' or 'N A AND E' -> ['N','A','E'] ; stops at the first non-taxiway word."""
    toks = re.split(r'[ ,]+', text.strip())
    out = []
    for t in toks:
        if t in ('AND', 'THEN', 'TAXI', 'VIA', ''):
            continue
        if not _is_twy(t):
            break
        out.append(t)
    return out


def _hold(text):
    m = re.search(r'HOLD SHORT (?:OF )?(?:RUNWAY (?P<rwy>\d{1,2}[LRC]?)|(?P<twy>' + TWY + r')\b)(?: AT (?P<at>' + TWY + r'))?(?: ON RUNWAY (?P<on>\d{1,2}[LRC]?))?', text) \
        or re.search(r'\bSHORT (?:OF )?(?:RUNWAY (?P<rwy>\d{1,2}[LRC]?)|(?P<twy>' + TWY + r')\b)(?: AT (?P<at>' + TWY + r'))?(?: ON RUNWAY (?P<on>\d{1,2}[LRC]?))?', text)
    if not m:
        return None
    h = {}
    if m.group('rwy'):
        h['runway'] = canonical_runway(m.group('rwy'))
    if m.group('twy'):
        h['taxiway'] = m.group('twy')
    if m.group('at'):
        h['at'] = m.group('at')
    if m.group('on'):
        h['on_runway'] = canonical_runway(m.group('on'))
    return h


def _advisory(text):
    m = re.search(r'TRAFFIC [^,.]*', text)
    return m.group(0).strip() if m else None


def classify(norm, callsigns, lead_callsign, speaker_hint=None):
    """Return (speech_act, kind, slots, confidence). Patterns are tried in priority order."""
    s = {}
    adv = _advisory(norm)
    # --- instructions with runway authority ---
    m = re.search(r'(?:RUNWAY ' + RWY.format(n='r1') + _SEP + _RWY_CLAUSE + r' )?CLEARED TO LAND(?: (?:, )?(?:RUNWAY )?' + RWY.format(n='r2') + r')?(?: (?:, )?NUMBER (?P<seq>\d+))?', norm)
    if m:
        rw = m.group('r1') or m.group('r2')
        s.update(runway=canonical_runway(rw) if rw else None, sequence=m.group('seq'), traffic_advisory=adv)
        return 'instruction', 'LANDING_CLEARANCE', s, 0.95 if rw else 0.6
    m = re.search(r'(?:RUNWAY ' + RWY.format(n='r1') + _SEP + _RWY_CLAUSE + r' )?CLEARED FOR TAKEOFF(?: (?:, )?(?:RUNWAY )?' + RWY.format(n='r2') + r')?', norm)
    if m:
        rw = m.group('r1') or m.group('r2')
        s.update(runway=canonical_runway(rw) if rw else None, traffic_advisory=adv)
        return 'instruction', 'TAKEOFF_CLEARANCE', s, 0.95 if rw else 0.6
    m = re.search(r'(?:RUNWAY ' + RWY.format(n='r1') + _SEP + _RWY_CLAUSE + r' )?LINE UP AND WAIT(?: (?:, )?(?:RUNWAY )?' + RWY.format(n='r2') + r')?', norm)
    if m:
        rw = m.group('r1') or m.group('r2')
        s.update(runway=canonical_runway(rw) if rw else None, traffic_advisory=adv)
        return 'instruction', 'LUAW', s, 0.95 if rw else 0.6
    m = re.search(r'REQUEST(?:ING)? (?:TO )?CROSS (?:RUNWAY )?' + RWY.format(n='r') + r'(?: AT (?P<at>' + TWY + r'))?', norm)
    if m:
        s.update(runway=canonical_runway(m.group('r')), crossing_at=m.group('at'))
        return 'request', 'CROSS_REQUEST', s, 0.95
    m = re.search(r'\bCROSSING COMPLETE\b', norm)
    if m:
        r = re.search(r'RUNWAY ' + RWY.format(n='r'), norm)
        s.update(runway=canonical_runway(r.group('r')) if r else None)
        return 'report', 'CROSSING_COMPLETE', s, 0.85
    m = re.search(r'\bCROSS(?P<ing>ING)? (?:RUNWAY )?' + RWY.format(n='r') + r' AT (?P<at>' + TWY + r')', norm)
    if m:
        s.update(runway=canonical_runway(m.group('r')), crossing_at=m.group('at'), traffic_advisory=adv)
        return ('readback' if m.group('ing') else 'instruction'), 'CROSS', s, 0.95
    m = re.search(r'\bCROSS(?P<ing>ING)? RUNWAY ' + RWY.format(n='r') + r'\b', norm)
    if m:
        at = re.search(r'\bAT (?P<at>' + TWY + r') (?:, )?CROSS', norm)
        s.update(runway=canonical_runway(m.group('r')), crossing_at=at.group('at') if at else None, traffic_advisory=adv)
        return ('readback' if m.group('ing') else 'instruction'), 'CROSS', s, 0.9
    m = re.search(r'(?:CLEAR OF|CLEAR(?= RUNWAY)|ACROSS|CROSSING COMPLETE)(?: RUNWAY)? ' + RWY.format(n='r'), norm)   # 'clear runway 31 at T' (round 3)
    if not m and re.search(r'\bCLEAR OF (?:THE )?RUNWAY\b(?! \d)', norm):
        # P/CG 'Clear of the runway' (JO 7110.65BB 3-7-2g2(a) reference; 3-10-9): the pilot's report with no runway number.
        # The ledger releases only THIS aircraft's landing. NOT YET HELD-OUT VALIDATED (Franco, Oct 9: judged on real clips)
        s.update(runway=None)
        return 'report', 'CROSSING_COMPLETE', s, 0.8
    if m:
        s.update(runway=canonical_runway(m.group('r')))
        return 'report', 'CROSSING_COMPLETE', s, 0.9
    if re.search(r'\bGOING AROUND\b', norm):
        return 'report', 'GO_AROUND', s, 0.9
    if re.search(r'\bGO AROUND\b', norm):
        return 'instruction', 'GO_AROUND', s, 0.9
    if re.search(r'\bSTOP\b|CANCEL TAKEOFF CLEARANCE', norm):
        return 'instruction', 'CANCEL', s, 0.9
    # --- taxi / hold ---
    m = re.search(r'(?:RUNWAY ' + RWY.format(n='r') + r'(?: [,.])? )?(?:TAXI )?VIA (?P<route>[A-Z0-9 ,]+?)(?= (?:, )?HOLD| (?:, )?SHORT|$| \.)', norm) \
        or re.search(r'RUNWAY ' + RWY.format(n='r') + r' (?:, )?(?P<route>(?:' + TWY + r' (?:, )?(?:AND )?)+)', norm)
    if not m:
        m = re.match(r'^(?P<route>(?:[A-Z]{1,2}\d? , )+[A-Z]{1,2}\d?) (?:, )?(?=HOLD|SHORT)', norm)
    if m and _route(m.group('route')):
        s.update(runway=canonical_runway(m.group('r')) if m.groupdict().get('r') else None,
                 route=_route(m.group('route')), hold_short=_hold(norm))
        return 'instruction', 'TAXI', s, 0.9
    if re.search(r'\b(?:TAXI|CONTINUE TAXIING|PROCEED) (?:TO|INTO) (?:THE )?(?:RAMP|GATE|APRON|PARKING|HANGAR|TERMINAL|CARGO)\b', norm):   # 3-7-2a 'TO (location)'
        s.update(route=[], hold_short=_hold(norm))
        return 'instruction', 'TAXI', s, 0.8
    hs = _hold(norm)
    m = re.search(r'(?:MAKE (?:THE |IT )?)?(?:(?:RIGHT|LEFT) (?:TURN )?)?(?:AT|ON) (?P<a>' + TWY + r')\b(?:\s*,?\s*(?:AND )?(?:THEN )?(?:(?:RIGHT|LEFT) (?:TURN )?)?(?:ON|AT) (?P<b>' + TWY + r')\b)?', norm)
    m2 = re.search(r'\bMAKE IT (?P<a>' + TWY + r')\b', norm)
    mm = m or m2
    if mm and re.search(r'\b(MAKE|TURN|RIGHT|LEFT)\b', norm) and not re.search(r'\b(IS|WE ?RE) ON\b', norm):
        route = [x for x in [mm.groupdict().get('a'), mm.groupdict().get('b')] if x]
        s.update(route=route, hold_short=hs)
        return 'instruction', 'TAXI', s, 0.8
    m = re.search(r'\bTURN (?:LEFT|RIGHT) (?:(?P<next>NEXT)\b|(?:AT |ON )?(?P<a>' + TWY + r')\b)', norm)
    if m:
        s.update(route=[m.group('a')] if m.group('a') else [], hold_short=hs)
        return 'instruction', 'TAXI', s, 0.8
    if re.search(r'\bHOLDING SHORT\b', norm):
        s.update(hold_short=_hold(norm.replace('HOLDING SHORT', 'HOLD SHORT')))
        if callsigns and not lead_callsign and norm.startswith('HOLDING SHORT'):   # read-back shape: 'Holding short runway 4, Delta 2189' (2-4-3b)
            return 'readback', 'HOLD_SHORT', s, 0.85
        return 'report', 'REPORT_HOLDING_SHORT', s, 0.9
    if not hs and re.search(r'\bWE NEED TO\b', norm):
        return 'request', 'REQUEST_OTHER', s, 0.6
    if hs or re.search(r'HOLD (?:YOUR )?POSITION', norm):
        s.update(hold_short=hs, hold_position=bool(re.search(r'HOLD (?:YOUR )?POSITION', norm)))
        return 'instruction', 'HOLD_SHORT', s, 0.85
    # --- reports ---
    m = re.search(r'REJECT(?:ED|ING)|ABORT(?:ED|ING)', norm)
    if m:
        r = re.search(r'(?:ON )?RUNWAY ' + RWY.format(n='r') + r'|\bON ' + RWY.format(n='r2') + r'\b', norm)
        rw = (r.group('r') or r.group('r2')) if r else None
        s.update(runway=canonical_runway(rw) if rw else None)
        return 'report', 'REPORT_REJECTED_TAKEOFF', s, 0.85
    if re.search(r'STAYING HERE|HOLDING POSITION|WE ?RE HOLDING\b', norm):
        return 'report', 'REPORT_HOLDING_POSITION', s, 0.8
    if re.search(r'\bWE NEED TO\b|\bREQUEST(?:ING)?\b', norm):
        return 'request', 'REQUEST_OTHER', s, 0.6
    if re.search(r'DECLARING (?:AN )?EMERGENCY', norm):
        return 'report', 'REPORT_EMERGENCY', s, 0.9
    if re.search(r'READY TO TAXI', norm):
        return 'report', 'REPORT_READY', s, 0.9
    m = re.search(r'CONTACT (?P<pos>GROUND|TOWER)', norm)
    if m:
        s.update(to=m.group('pos'))
        return 'instruction', 'FREQ_CHANGE', s, 0.9
    m = re.search(r'\b(?:IS |WE ?RE )ON (?P<t>' + TWY + r')\b', norm)
    if m:
        s.update(taxiway=m.group('t'))
        return 'report', 'REPORT_POSITION', s, 0.8
    if re.fullmatch(r'(?:[ ,.]*(?:LAGUARDIA|BOSTON|LOGAN|TOWER|GROUND|YEP|YES|CHECKING IN|CHECK IN ON TOWER|\(|\)))*[ ,.]*', norm):
        return 'report', 'CHECK_IN', s, 0.6
    return 'unknown', 'OTHER', s, 0.3


def parse(text, airport_icao, speaker_hint=None, source=None, event_time=None, received_time=None,
          resolver=None, confidence_override=None):
    """Parse one utterance.

    speaker_hint: 'ATC' | 'PILOT' | 'VEHICLE' | None. If None, role is inferred:
      leading callsign (or none) + instruction verbs -> ATC; trailing callsign -> pilot/vehicle.
    resolver: optional airport.Airport for identifier resolution (runway ends / taxiways).
    """
    global _VALID
    _VALID = (lambda t: bool(resolver.taxiway_candidates(t))) if resolver is not None else None
    repaired, repairs = repair(text)
    raw_tokens = [t for t in tokenize(repaired) if t not in ('TAXIWAY', 'CLIPPED', 'A', 'AN', 'THE')]  # articles are not taxiway A
    raw_tokens, ctx_repairs = context_number_repairs(raw_tokens, AIRLINES)   # Whisper number forms, callsign/runway contexts only
    repairs = list(repairs) + ctx_repairs
    joined = ' '.join(raw_tokens).replace('SIM AIR', 'SIMAIR').replace('AIR CANADA', 'AIRCANADA')
    raw_tokens = joined.split()
    calls, rest = extract_callsigns(raw_tokens)
    norm = ' '.join(collapse_phonetics(collapse_numbers(rest)))
    norm = re.sub(r'\s+', ' ', norm).strip().lstrip(' ,.;:').strip()
    if resolver is not None:  # merge adjacent distinct letters into a 2-letter taxiway only if it exists (A C -> AC)
        toks = norm.split()
        merged = []
        for t in toks:
            if merged and re.fullmatch(r'[A-Z]', t) and re.fullmatch(r'[A-Z]', merged[-1]) and resolver.taxiway_candidates(merged[-1] + t):
                merged[-1] = merged[-1] + t
            else:
                merged.append(t)
        norm = ' '.join(merged)
    n = len(raw_tokens)
    lead = bool(calls) and calls[0][1] <= 1
    trail = bool(calls) and calls[-1][2] >= n - 2
    addressed_facility = bool(re.match(r'^(?:,? )?(TOWER|GROUND|LAGUARDIA TOWER|BOSTON TOWER)\b', norm)) or ' TOWER ' in f' {norm} ' and not lead
    act, kind, slots, conf = classify(norm, calls, lead, speaker_hint)

    role = speaker_hint
    if role is None:
        if act in ('request', 'report', 'readback') or kind == 'CHECK_IN' or addressed_facility:
            role = 'PILOT_OR_VEHICLE'
        elif trail and not lead and act == 'instruction':
            role = 'AMBIGUOUS'      # 'Runway 4, cleared to land, Jazz 646' can be ATC or a readback
        elif trail and not lead:
            role = 'PILOT_OR_VEHICLE'
        elif not calls and act == 'instruction':
            role = 'AMBIGUOUS'      # no callsign heard: the ledger decides from pending instructions
        else:
            role = 'ATC'
    # A pilot/vehicle repeating an instruction is a readback, not an instruction.
    if role in ('PILOT', 'VEHICLE', 'PILOT_OR_VEHICLE') and act == 'instruction':
        act = 'readback'
    if role == 'AMBIGUOUS' and act == 'instruction':
        act = 'instruction_or_readback'
    if role in ('PILOT', 'VEHICLE', 'PILOT_OR_VEHICLE') and kind == 'GO_AROUND':
        act = 'report' if 'GOING AROUND' in norm else 'readback'
    if role == 'ATC' and act in ('report', 'request') and kind != 'CROSSING_COMPLETE':
        act, kind = 'unknown', 'OTHER'   # controller asking about / relaying a report, not a report
    if role == 'ATC' and act == 'readback':
        act = 'instruction'
    if confidence_override is not None:
        conf = confidence_override
    clipped = bool(re.search(r'\[CLIPPED\]', text.upper())) or text.rstrip().endswith(('—', '-'))  # audio-level clip detection is a venue task
    if clipped:
        conf = min(conf, 0.45)

    unresolved = []
    runway_end_candidates, route_candidates = [], []
    if resolver is not None:
        if slots.get('runway'):
            c = resolver.runway_end_candidates(slots['runway'])
            runway_end_candidates = c
            if not c:
                unresolved.append(f"runway {slots['runway']} not in {airport_icao} inventory")
        for t in slots.get('route') or []:
            c = resolver.taxiway_candidates(t)
            route_candidates.append({'word': t, 'candidates': c})
            if not c:
                unresolved.append(f'taxiway {t} not in {airport_icao} inventory')
        for k in ('crossing_at',):
            if slots.get(k):
                c = resolver.taxiway_candidates(slots[k])
                if not c:
                    unresolved.append(f'taxiway {slots[k]} not in {airport_icao} inventory')
    hold = slots.get('hold_short')
    ev = {
        'event_id': f"ev{next(_ids):05d}",
        'airport_icao': airport_icao,
        'actor_candidates': [c[0] for c in calls],
        'speaker_role': role,
        'speech_act': act,
        'kind': kind,
        'raw_text': text,
        'normalized_text': norm,
        'asr_repairs_applied': repairs,
        'source_id': (source or {}).get('id'),
        'source_start_s': (source or {}).get('start_s'),
        'source_end_s': (source or {}).get('end_s'),
        'event_time': event_time,
        'received_time': received_time if received_time is not None else event_time,
        'runway': slots.get('runway'),
        'runway_end_candidates': runway_end_candidates,
        'route': slots.get('route'),
        'route_surface_candidates': route_candidates,
        'hold_constraints': [hold] if hold else [],
        'hold_position': slots.get('hold_position', False),
        'crossing_at': slots.get('crossing_at'),
        'traffic_advisory': slots.get('traffic_advisory'),
        'sequence': slots.get('sequence'),
        'clipped': clipped,
        'parse_path': 'grammar' if act != 'unknown' else 'residue',
        'uncertainty': {'confidence_heuristic': round(conf, 2),
                        'note': 'heuristic parser score, not a calibrated probability',
                        'unresolved': unresolved},
        'supersedes_event_id': None,
    }
    return ev


# ---------- speaker role, deterministic (Franco, Oct 9, round 3): used by the host fast path, which has no speaker labels ----------
_PILOT_PHRASES = re.compile(r'\b(?:ROGER|WILCO|HOLDING SHORT|GOING AROUND|CROSSING|CONTINUING|CLEAR OF|CLEAR RUNWAY|WITH YOU|REQUEST(?:ING)?|'
                            r'READY|UNABLE|SAY AGAIN|LOOKING FOR|WE ?RE)\b')
# 7110.65 3-9-10a / 3-10-5a / 3-9-4b: the controller states the runway FIRST, then the clearance; a pilot read back states the
# clearance first ('roger, cleared to land runway four left', 2-4-3 EXAMPLE)
_CLEARANCE_VERB = re.compile(r'\b(?:CLEARED TO LAND|CLEARED FOR TAKEOFF|LINE UP AND WAIT)\b')
_VERB_THEN_RUNWAY = re.compile(r'(?:CLEARED TO LAND|CLEARED FOR TAKEOFF|LINE UP AND WAIT) (?:, )?(?:RUNWAY )?\d{1,2}[LRC]?\b')


def classify_speaker(text):
    """'ATC' | 'PILOT' | 'AMBIGUOUS' (ledger decides from pending instructions) | None (no callsign: parser infers), with the reason.
    Pilot phrases (roger, wilco, holding short, going around, crossing, ...) -> PILOT; callsign last -> PILOT;
    callsign first -> ATC, unless the clearance comes before the runway -> AMBIGUOUS;
    addressed to a facility ('Tower, ...') -> PILOT. A pilot's read back is never treated as a clearance."""
    repaired, _ = repair(text)
    toks = [t for t in tokenize(repaired) if t not in ('TAXIWAY', 'CLIPPED', 'A', 'AN', 'THE')]
    toks, _ = context_number_repairs(toks, AIRLINES)
    toks = ' '.join(toks).replace('SIM AIR', 'SIMAIR').replace('AIR CANADA', 'AIRCANADA').split()
    calls, rest = extract_callsigns(toks)
    norm = re.sub(r'\s+', ' ', ' '.join(collapse_phonetics(collapse_numbers(rest)))).strip(' ,.')
    n = len(toks)
    lead = bool(calls) and calls[0][1] <= 1
    trail = bool(calls) and calls[-1][2] >= n - 2 and not (len(calls) == 1 and lead)
    if _PILOT_PHRASES.search(norm):
        return 'PILOT', 'pilot phrase'
    if re.match(r'^(?:[A-Z]+ )?(?:TOWER|GROUND)\b', norm) and not lead:
        return 'PILOT', 'addressed to a facility'
    if trail and not lead:
        return 'PILOT', 'callsign last'
    if lead:
        # callsign first + clearance before runway: a pilot read back in 2-4-3 order, or a controller off the 7110.65 order.
        # AMBIGUOUS: the ledger decides from what is pending (read back of a recorded instruction, else an instruction)
        if _VERB_THEN_RUNWAY.search(norm):
            return 'AMBIGUOUS', 'callsign first, clearance before runway'
        v = _CLEARANCE_VERB.search(norm)
        if v and not re.search(r'\bRUNWAY \d{1,2}[LRC]?\b', norm[:v.start()]):   # 3-10-5a / 3-9-10a / 3-9-4b: a controller states the runway first
            return 'AMBIGUOUS', 'callsign first, clearance with no runway before it'
        return 'ATC', 'callsign first'
    return None, 'no callsign heard'
