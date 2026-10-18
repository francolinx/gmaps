"""Scorer: observed alerts vs a scenario's ground truth. Pure functions (no I/O), unit-tested in tests/test_sim.py.

An observed alert: {warning_id, rule, runway, aircraft: [cs, cs], t_fired (scenario s), latency_s (end of transmission -> alert, wall s)}.
Matching, per expected alert (in file order): the first unmatched observed alert whose rule is accepted, whose t_fired is in the
window, and (when given) whose aircraft pair and runway match. Unmatched observed alerts are false alarms; a false alarm inside an
expected no-alert window is flagged as such. Shadow alerts (never shown to a controller) are not scored.
"""
import statistics


def _runway_ok(want, got):
    if not want:
        return True
    return any(str(want) in str(g) for g in (got if isinstance(got, (list, tuple)) else [got]))


def _pair_ok(want, got):
    return not want or set(want) == set(got or [])


def score_scenario(sc, alerts):
    shown = [a for a in alerts if not a.get('shadow')]
    used, rows = set(), []
    for ea in sc['expected_alerts']:
        hit = None
        for i, a in enumerate(shown):
            if i in used:
                continue
            if (a.get('rule') in ea['rule'] and ea['window'][0] <= (a.get('t_fired') if a.get('t_fired') is not None else -1) <= ea['window'][1]
                    and _pair_ok(ea.get('aircraft'), a.get('aircraft')) and _runway_ok(ea.get('runway'), a.get('runway'))):
                hit = i
                break
        if hit is not None:
            used.add(hit)
        rows.append({'expected': ea['id'], 'rule': ea['rule'], 'detected': hit is not None,
                     'warning_id': shown[hit]['warning_id'] if hit is not None else None,
                     'latency_s': shown[hit].get('latency_s') if hit is not None else None})
    false_alarms = []
    for i, a in enumerate(shown):
        if i in used:
            continue
        t = a.get('t_fired')
        inside = [na['window'] for na in sc['expected_no_alert'] if t is not None and na['window'][0] <= t <= na['window'][1]]
        false_alarms.append({**{k: a.get(k) for k in ('warning_id', 'rule', 'runway', 'aircraft', 't_fired')}, 'in_no_alert_window': bool(inside)})
    lat = [r['latency_s'] for r in rows if r['detected'] and r['latency_s'] is not None]
    return {'scenario': sc['id'], 'expected': len(rows), 'detected': sum(r['detected'] for r in rows),
            'missed': sum(not r['detected'] for r in rows), 'false_alarms': len(false_alarms),
            'shadow_alerts': sum(1 for a in alerts if a.get('shadow')), 'latency_s': lat, 'rows': rows, 'false_alarm_detail': false_alarms,
            'pass': all(r['detected'] for r in rows) and not false_alarms}


def overall(per):
    lat = [x for p in per for x in p['latency_s']]
    return {'scenarios': len(per), 'passed': sum(p['pass'] for p in per), 'expected': sum(p['expected'] for p in per),
            'detected': sum(p['detected'] for p in per), 'missed': sum(p['missed'] for p in per),
            'false_alarms': sum(p['false_alarms'] for p in per),
            'latency_s': {'n': len(lat), 'median': round(statistics.median(lat), 3) if lat else None,
                          'max': round(max(lat), 3) if lat else None}}
