"""Scenario loader (sim/scenarios/*.yaml). Validates the format; never reads eval/truth or eval/labels.

Format (one YAML file per scenario):
  id, title, airport: XSIM (fictional SIM-1 only), simulated: true, end_t (scenario seconds)
  transmissions: [{id, t, speaker: ATC|PILOT, text, cite (FAA JO 7110.65 paragraph), callsign (pilots)}]   (t ascending)
  expected_alerts: [{id, rule: [R..], runway, aircraft: [callsign, callsign], window: [t0, t1], why}]   (t_fired in window)
  expected_no_alert: [{window: [t0, t1], why}]   (no unexpected alert may fire inside)
  traffic: [callsign, ...]   optional: extra callsigns on frequency, used only with --traffic (sim/options.py)
"""
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parent / 'scenarios'


class ScenarioError(ValueError):
    pass


def _need(cond, sid, msg):
    if not cond:
        raise ScenarioError(f'{sid}: {msg}')


def validate(sc, src='?'):
    sid = sc.get('id') if isinstance(sc, dict) else None
    _need(isinstance(sc, dict) and isinstance(sid, str) and sid, src, 'needs a string id')
    _need(sc.get('airport') == 'XSIM', sid, 'airport must be XSIM (fictional SIM-1 map only)')
    _need(sc.get('simulated') is True, sid, 'simulated: true is required')
    txs = sc.get('transmissions')
    _need(isinstance(txs, list) and txs, sid, 'needs transmissions')
    ids, last = set(), -1.0
    for tx in txs:
        _need(isinstance(tx, dict) and {'id', 't', 'speaker', 'text', 'cite'} <= set(tx), sid, f'transmission needs id, t, speaker, text, cite: {tx}')
        _need(tx['id'] not in ids, sid, f"duplicate transmission id {tx['id']}")
        ids.add(tx['id'])
        _need(tx['speaker'] in ('ATC', 'PILOT'), sid, f"{tx['id']}: speaker must be ATC or PILOT")
        _need(tx['speaker'] == 'ATC' or tx.get('callsign'), sid, f"{tx['id']}: a pilot transmission needs callsign (voice selection)")
        _need(isinstance(tx['t'], (int, float)) and tx['t'] >= last, sid, f"{tx['id']}: t must be a number, ascending")
        _need('7110.65' in str(tx['cite']), sid, f"{tx['id']}: cite must name a FAA JO 7110.65 paragraph")
        last = float(tx['t'])
    _need(isinstance(sc.get('end_t'), (int, float)) and sc['end_t'] >= last, sid, 'end_t must be >= the last transmission')
    for ea in sc.get('expected_alerts') or []:
        _need(isinstance(ea, dict) and {'id', 'rule', 'window'} <= set(ea), sid, f'expected alert needs id, rule, window: {ea}')
        _need(isinstance(ea['rule'], list) and ea['rule'], sid, f"{ea['id']}: rule is a non-empty list of accepted rule ids")
        _need(_window_ok(ea['window']), sid, f"{ea['id']}: window must be [t0, t1] with t0 <= t1")
        _need(len(ea.get('aircraft') or []) in (0, 2), sid, f"{ea['id']}: aircraft is a pair (or omitted)")
    for na in sc.get('expected_no_alert') or []:
        _need(isinstance(na, dict) and _window_ok(na.get('window')), sid, f'expected_no_alert needs window [t0, t1]: {na}')
    if 'traffic' in sc:   # optional extra callsigns on frequency (sim/options.py --traffic); never read unless asked
        _need(isinstance(sc['traffic'], list) and all(isinstance(c, str) and c.isupper() and c.isalnum() for c in sc['traffic']),
              sid, 'traffic must be a list of callsigns (upper-case letters and digits)')
    sc.setdefault('expected_alerts', [])
    sc.setdefault('expected_no_alert', [])
    return sc


def _window_ok(w):
    return isinstance(w, list) and len(w) == 2 and all(isinstance(x, (int, float)) for x in w) and w[0] <= w[1]


def load(path):
    path = Path(path)
    return validate(yaml.safe_load(path.read_text()), path.name)


def load_all(directory=SCENARIO_DIR, only=None):
    out = [load(p) for p in sorted(Path(directory).glob('*.yaml'))]
    seen = set()
    for sc in out:
        if sc['id'] in seen:
            raise ScenarioError(f"duplicate scenario id {sc['id']}")
        seen.add(sc['id'])
    return [sc for sc in out if not only or sc['id'] in only]
