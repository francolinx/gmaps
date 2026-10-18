"""Callsign matching against a traffic list (Franco, Oct 9, round 3): the list of callsigns known to be on frequency, as an
ADS-B / flight-plan feed would give it. The list is an observation, never voice-derived; with no list nothing changes.

match_callsigns(ev, traffic) rewrites ev['actor_candidates'] only when exactly ONE traffic callsign fits, and records what it
did in ev['callsign_match'] (heard, matched, rule). It never invents an aircraft that is not on the list, and an unmatched
heard callsign is kept as heard. Rules, in order, per heard callsign:
  exact            the heard callsign is on the list
  same digits      '?2175' / 'XXX2175' -> the one list callsign with flight number 2175 (airline misheard or missing)
  one digit off    same airline; flight numbers differ in one digit, two adjacent digits swapped, or one digit dropped/added
If nothing was heard: a 3-4 digit number in the text that is the flight number of exactly one listed callsign.
Franco, Oct 9 (round 5): every rewrite other than 'exact' is a REPAIR: the event is tagged callsign_repaired, its confidence is
lowered to at most REPAIRED_CONFIDENCE, and alerts resting on it are scored separately. N-registrations are matched EXACTLY only
(never repaired), and a flight number shared by two listed aircraft is never used.
"""
REPAIRED_CONFIDENCE = 0.5
import re


def _split(cs):
    m = re.fullmatch(r'([A-Z?]*?)(\d+)([A-Z]{0,2})', cs or '')
    return (m.group(1), m.group(2), m.group(3)) if m else (cs, '', '')


def _one_off(a, b):
    if len(a) == len(b):
        d = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(d) == 1 or (len(d) == 2 and d[1] == d[0] + 1 and a[d[0]] == b[d[1]] and a[d[1]] == b[d[0]])   # one digit, or two adjacent swapped
    if abs(len(a) - len(b)) == 1:
        s, l = (a, b) if len(a) < len(b) else (b, a)
        return any(l[:i] + l[i + 1:] == s for i in range(len(l)))
    return False


def _unique(cands):
    cands = sorted(set(cands))
    return cands[0] if len(cands) == 1 else None


def match_one(heard, traffic):
    if heard in traffic:
        return heard, 'exact'
    if re.fullmatch(r'N\d{1,5}[A-Z]{0,2}', heard or ''):   # registrations: exact only
        return None, None
    traffic = {t for t in traffic if not re.fullmatch(r'N\d{1,5}[A-Z]{0,2}', t)}
    pre, num, suf = _split(heard)
    if num:
        m = _unique([t for t in traffic if _split(t)[1] == num])
        if m:
            return m, 'same digits'
        m = _unique([t for t in traffic if _split(t)[0] == pre and pre not in ('', '?') and _one_off(_split(t)[1], num)])
        if m:
            return m, 'one digit off'
    return None, None


def match_callsigns(ev, traffic):
    if not traffic:
        return ev
    traffic = set(traffic)
    heard = list(ev.get('actor_candidates') or [])
    out, notes = [], []
    for h in heard:
        m, rule = match_one(h, traffic)
        out.append(m or h)
        notes.append({'heard': h, 'matched': m, 'rule': rule or 'no unique match (kept as heard)'})
    if not heard:
        for num in re.findall(r'\b(\d{3,4})\b', ev.get('normalized_text') or ''):
            m = _unique([t for t in traffic if _split(t)[1] == num and not t.startswith('N')])
            if m:
                out.append(m); notes.append({'heard': None, 'matched': m, 'rule': f'flight number {num} in the text'})
                break
    ev['actor_candidates'] = list(dict.fromkeys(out))
    ev['callsign_match'] = notes
    if any(n['matched'] and n['rule'] != 'exact' for n in notes):
        ev['callsign_repaired'] = True
        u = ev.setdefault('uncertainty', {})
        u['confidence_heuristic'] = min(u.get('confidence_heuristic', 1.0), REPAIRED_CONFIDENCE)
    return ev
