"""Grounding rule for model answers (Open Agent Hackathon 2026), applied by the Evaluator after every model call.

Residue parse (RESIDUE_SCHEMA answer): the same rule as venue/llm_fallback.py, on the residue fields: every value must be
heard in the transmission text and exist at the selected airport (identifiers come from the data store):
  kind's key words heard; each callsign's digits heard and its airline / vehicle word named; runway number heard and on the
  airport's runway list; crossing / hold-short / route taxiways heard and on the airport's taxiway list.
Explanation (explain_warning answer): at most two sentences, names every callsign in the facts, no 'would have prevented',
no 'channel'; otherwise rejected and the ledger's own explanation is shown, labelled as such.
"""
import re
from .normalize import NUMBER_WORDS, PHONETICS
from .parser import AIRLINES, VEHICLES

KEYWORDS = {'LANDING_CLEARANCE': r'\bLAND', 'TAKEOFF_CLEARANCE': r'TAKE ?OFF', 'LUAW': r'\bLINE UP|\bWAIT\b', 'CROSS': r'\bCROSS|\bACROSS\b',
            'CROSS_REQUEST': r'\bCROSS|\bACROSS\b', 'HOLD_SHORT': r'\bHOLD(?:ING)? SHORT\b|\bSHORT OF\b', 'TAXI': r'\bTAXI|\bTURN\b|\bVIA\b|\bEXIT',
            'GO_AROUND': r'\bGO(?:ING)?[ -]AROUND', 'CROSSING_COMPLETE': r'\bCLEAR\b', 'CANCEL': r'\bSTOP\b|\bCANCEL'}
TELEPHONY = {v: k for k, v in list(AIRLINES.items()) + list(VEHICLES.items())}


def _norm(text):
    return ' ' + re.sub(r'[^A-Z0-9 ]', ' ', text.upper().replace('-', ' ')) + ' '


def _digits(up):
    return re.sub(r'\D', '', ' '.join(NUMBER_WORDS.get(w, w) for w in up.split()))


def _twy_heard(tw, up):
    words = {k for k, v in PHONETICS.items() if v == tw[:1]}
    return any(f' {w} ' in up for w in words) or f' {tw} ' in up


def check_residue(d, text, identifiers):
    """-> (accepted: bool, verdict: str). identifiers: {'runways': [...], 'taxiways': [...]} for the selected airport."""
    if not isinstance(d, dict) or d.get('error'):
        return False, f"no answer ({(d or {}).get('error', 'none')})"
    for k in ('speech_act', 'kind', 'callsigns'):
        if k not in d:
            return False, f'schema: missing {k}'
    if d['kind'] in ('OTHER', 'REPORT') or d['speech_act'] == 'unknown':
        return False, 'no actionable content'
    up = _norm(text)
    dig = _digits(up)
    kw = KEYWORDS.get(d['kind'])
    if kw and not re.search(kw, up):
        return False, f"grounding: no '{d['kind']}' words in the text"
    if not d['callsigns']:
        return False, 'grounding: no callsign'
    for cs in d['callsigns']:
        m = re.fullmatch(r'([A-Z]+?)(\d{1,4})', str(cs))
        if not m:
            return False, f'schema: callsign {cs}'
        if m.group(2) not in dig:
            return False, f'grounding: number of {cs} not heard'
        word = TELEPHONY.get(m.group(1))
        if not word or word not in up.replace(' ', '') and f' {word} ' not in up:
            return False, f'grounding: {m.group(1)} not named in the text'
    rw = d.get('runway')
    if rw:
        rn = re.fullmatch(r'0?(\d{1,2})([LRC]?)', str(rw))
        if not rn or rn.group(1) not in dig:
            return False, 'grounding: runway number not heard'
        if str(rw).lstrip('0') not in {str(r).lstrip('0') for r in identifiers.get('runways', [])}:
            return False, f'grounding: runway {rw} not at this airport'
    twys = [d.get('crossing_at')] + list(d.get('route') or []) + [(d.get('hold_short') or {}).get('at')]
    for tw in [t for t in twys if t]:
        if tw not in identifiers.get('taxiways', []):
            return False, f'grounding: taxiway {tw} not at this airport'
        if not _twy_heard(tw, up):
            return False, f'grounding: taxiway {tw} not heard'
    return True, 'valid'


BANNED = (r'would have prevented', r'\bchannel\b')


def check_explanation(text, facts):
    """-> (accepted, verdict)."""
    if not isinstance(text, str) or not text.strip():
        return False, 'empty'
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    if len(sentences) > 2:
        return False, f'{len(sentences)} sentences (max 2)'
    for b in BANNED:
        if re.search(b, text, re.I):
            return False, f'banned phrase: {b}'
    for cs in facts.get('callsigns', []):
        m = re.fullmatch(r'([A-Z]+?)(\d+)', cs)
        spoken = TELEPHONY.get(m.group(1), '') if m else ''
        if cs not in text.replace(' ', '') and not (m and m.group(2) in text and (spoken.lower() in text.lower() or m.group(1) in text)):
            return False, f'callsign {cs} missing'
    return True, 'valid'
