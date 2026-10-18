"""Intent-aware look-ahead occupancy predictor (prepared library, stdlib only).

Inputs it is ALLOWED to see:
  * observation packets with received_at <= as_of (never truth, never future packets)
  * the fictional SIM-1 map (paths, hold lines, zone)
  * intent context from the ledger (recorded authorizations, hold constraints, ack state, cancel state)
Outputs: per-actor occupancy windows for zone Z1, candidate overlaps, and explicit 'unavailable' states.

Model (declared, engineering choices - NOT validated aviation parameters):
  * along-path kinematics; speed and acceleration estimated from the last few packets
  * bounds: speed +/- dv (grows when data is stale), acceleration within per-class limits
  * stale data: > STALE_SOFT s -> widen; > STALE_HARD s -> timed prediction unavailable
  * hold-short hypothesis: if a vehicle holds an acknowledged hold-short and could stop at the hold line
    with decel <= HOLD_DECEL_PLAUSIBLE, occupancy is only conditional ('if it does not stop');
    if the required decel exceeds that, a hold-line exceedance is projected and the window is used.
'metric_prediction_unavailable' is never equivalent to 'no conflict'.
"""
from .simmap import SIM_MAP

MODEL = {
    'version': 'gmaps-sim-predict-0.1',
    'horizon_s': 60.0, 'step_s': 0.1,
    'stale_soft_s': 3.0, 'stale_hard_s': 6.0, 'dv0_mps': 1.0, 'dv_per_stale_s': 1.5,
    'accel_limits': {'aircraft': (-3.5, 0.5), 'vehicle': (-3.0, 2.0)},
    'da_mps2': 0.5, 'v_cap': {'aircraft': 90.0, 'vehicle': 20.0},
    'hold_decel_plausible_mps2': 2.0,
    'stationary_v_mps': 1.2, 'stationary_a_mps2': 0.4,
    'hold_warn_time_s': 6.0, 'decelerating_mps2': -0.25, 'hold_warn_min_v_mps': 4.0, 'hold_warn_min_decel_mps2': 0.6,
    'departure_after_clearance': {'delay_s': (0.0, 10.0), 'accel_mps2': (1.0, 2.0), 'v_max_mps': 13.0},
    'note': 'Engineering choices for a fictional demo; not calibrated, not aviation standards.',
}
PATHS = {  # straight paths on SIM-1 known from the map + intent (which surface, which direction)
    ('XSIM:RWY:18-36', '36'): {'start': (0.0, 0.0), 'dir': (1.0, 0.0)},
    ('XSIM:TWY:K', 'north'): {'start': (1500.0, -400.0), 'dir': (0.0, 1.0)},
}
ZONE = SIM_MAP['zones']['XSIM:ZONE:Z1']


def _proj(path, x, y):
    (x0, y0), (dx, dy) = path['start'], path['dir']
    return (x - x0) * dx + (y - y0) * dy


def zone_span(path):
    """Along-path [s_in, s_out] of zone Z1 for a straight path."""
    (x0, y0), (dx, dy) = path['start'], path['dir']
    if abs(dx) > abs(dy):
        a, b = (ZONE['xmin'] - x0) / dx, (ZONE['xmax'] - x0) / dx
    else:
        a, b = (ZONE['ymin'] - y0) / dy, (ZONE['ymax'] - y0) / dy
    return min(a, b), max(a, b)


def estimate(packets, as_of, path):
    seen = sorted([p for p in packets if p['received_at'] <= as_of], key=lambda p: p['observed_at'])
    if not seen:
        return None
    last = seen[-6:]
    ts = [p['observed_at'] for p in last]
    ss = [_proj(path, p['position']['x'], p['position']['y']) for p in last]
    vs = [p['speed_or_velocity']['vx'] * path['dir'][0] + p['speed_or_velocity']['vy'] * path['dir'][1] for p in last]
    tl = ts[-1]
    n = len(last)
    # least-squares line on s(t) for position; average of reported velocities; accel from velocity trend
    if n >= 2:
        mt, ms = sum(ts) / n, sum(ss) / n
        den = sum((t - mt) ** 2 for t in ts) or 1e-9
        v_fit = sum((t - mt) * (s - ms) for t, s in zip(ts, ss)) / den
        s_last = ms + v_fit * (tl - mt)
        mv = sum(vs) / n
        a_fit = sum((t - mt) * (v - mv) for t, v in zip(ts, vs)) / den if n >= 3 else 0.0
        v_last = 0.5 * (vs[-1] + (mv + a_fit * (tl - mt)))
    else:
        s_last, v_last, a_fit = ss[-1], vs[-1], 0.0
    a_short = 0.0
    if n >= 3:
        t3, v3 = ts[-4:], vs[-4:]
        m3t, m3v = sum(t3) / len(t3), sum(v3) / len(v3)
        d3 = sum((t - m3t) ** 2 for t in t3) or 1e-9
        a_short = sum((t - m3t) * (v - m3v) for t, v in zip(t3, v3)) / d3
    return {'s': s_last, 'v': max(0.0, v_last), 'a': a_fit, 'a_short': a_short, 'last_observed_at': tl, 'age_s': round(as_of - tl, 2),
            'n_used': n, 'packet_ids': [p['packet_id'] for p in last]}


def _first_reach(s0, v0, a, d_target, vcap, horizon, step):
    s, v, tau = s0, v0, 0.0
    while tau <= horizon:
        if s >= d_target:
            return tau
        v_new = min(vcap, max(0.0, v + a * step))
        s += 0.5 * (v + v_new) * step
        v = v_new
        tau += step
        if v <= 0.0 and a <= 0:
            return None
    return None


def occupancy_window(est, path, cls, as_of, m=MODEL):
    s_in, s_out = zone_span(path)
    half = SIM_MAP['footprints_m'][cls] / 2
    lo, hi = m['accel_limits'][cls]
    stale_extra = max(0.0, est['age_s'] - m['stale_soft_s'])
    dv = m['dv0_mps'] + m['dv_per_stale_s'] * stale_extra
    v_lo, v_hi = max(0.0, est['v'] - dv), est['v'] + dv
    a_lo, a_hi = max(lo, est['a'] - m['da_mps2']), min(hi, est['a'] + m['da_mps2'])
    if a_hi < a_lo:
        a_lo = a_hi = max(lo, min(hi, est['a']))
    stationary = est['v'] < m['stationary_v_mps'] and abs(est['a']) < m['stationary_a_mps2']
    if stationary:   # a competent motion model does not predict a stopped object to start moving
        v_lo, v_hi, a_lo, a_hi = 0.0, m['stationary_v_mps'], 0.0, 0.0
    h = m['horizon_s'] + est['age_s']
    front, rear = est['s'] + half, est['s'] - half
    t0 = est['last_observed_at']
    if rear > s_out:
        return {'status': 'past_zone'}
    inside_now = front >= s_in
    e_early = 0.0 if inside_now else _first_reach(front, v_hi, a_hi, s_in, m['v_cap'][cls], h, m['step_s'])
    e_late = 0.0 if inside_now else _first_reach(front, v_lo, a_lo, s_in, m['v_cap'][cls], h, m['step_s'])
    x_early = _first_reach(rear, v_hi, a_hi, s_out, m['v_cap'][cls], h, m['step_s'])
    x_late = _first_reach(rear, v_lo, a_lo, s_out, m['v_cap'][cls], h, m['step_s'])
    if e_early is None:
        return {'status': 'no_entry_within_horizon', 'bounds': {'v': [round(v_lo, 2), round(v_hi, 2)], 'a': [round(a_lo, 2), round(a_hi, 2)]}}
    win = [round(t0 + e_early, 1), round(t0 + x_late, 1) if x_late is not None else None]
    return {'status': 'window', 'window': win, 'stationary': stationary, 'entry_latest': round(t0 + e_late, 1) if e_late is not None else None,
            'exit_earliest': round(t0 + x_early, 1) if x_early is not None else None,
            'open_ended': x_late is None,
            'bounds': {'v': [round(v_lo, 2), round(v_hi, 2)], 'a': [round(a_lo, 2), round(a_hi, 2)]},
            'zone_span_s': [round(s_in, 1), round(s_out, 1)], 'footprint_m': 2 * half}


def hold_hypothesis(est, path, hold_point, cls, m=MODEL):
    s_hold = _proj(path, *hold_point)
    front = est['s'] + SIM_MAP['footprints_m'][cls] / 2
    rem = s_hold - front
    if est['v'] < m['stationary_v_mps'] and rem > -3 * SIM_MAP.get('obs_sigma_m', 3.0):
        return {'plausible': True, 'required_decel': 0.0, 'distance_to_hold_m': round(rem, 1), 't_to_hold_s': None, 'decelerating': False,
                'reason': 'stopped at or near the hold line (within observation noise)'}
    if rem <= 0:
        return {'plausible': False, 'reason': f'front of vehicle already {abs(rem):.0f} m past the hold line (observed)', 'required_decel': None}
    need = est['v'] ** 2 / (2 * rem) if est['v'] > 0 else 0.0
    t_to_hold = rem / est['v'] if est['v'] > 0.5 else None
    decel = est.get('a_short', 0.0) <= m['decelerating_mps2']
    if need > m['hold_decel_plausible_mps2']:
        ok, why = False, (f'stopping at the hold line would need {need:.1f} m/s2 (> {m["hold_decel_plausible_mps2"]} declared plausible); '
                          'observed motion is not consistent with holding short')
    elif t_to_hold is not None and t_to_hold < m['hold_warn_time_s'] and not decel and est['v'] >= m['hold_warn_min_v_mps'] \
            and need >= m['hold_warn_min_decel_mps2']:
        ok, why = False, (f'{t_to_hold:.1f} s from the hold line at {est["v"]:.1f} m/s with no deceleration observed '
                          f'(declared warning time {m["hold_warn_time_s"]} s)')
    else:
        ok, why = True, (f'can still stop at the hold line ({need:.1f} m/s2 needed' + (', decelerating' if decel else '') + ')')
    return {'plausible': ok, 'required_decel': round(need, 2), 'distance_to_hold_m': round(rem, 1),
            't_to_hold_s': round(t_to_hold, 1) if t_to_hold else None, 'decelerating': decel, 'reason': why}


def departure_window(est, path, cls, as_of, m=MODEL):
    """Intent hypothesis: a stopped vehicle that has just been CLEARED to cross may start at any time within the
    declared delay range. Gives a window before motion is observed (motion-only cannot)."""
    d = m['departure_after_clearance']
    s_in, s_out = zone_span(path)
    half = SIM_MAP['footprints_m'][cls] / 2
    front, rear = est['s'] + half, est['s'] - half
    t0 = est['last_observed_at']
    early = _first_reach(front, 0.0, d['accel_mps2'][1], s_in, d['v_max_mps'], m['horizon_s'], m['step_s'])
    late_exit = _first_reach(rear, 0.0, d['accel_mps2'][0], s_out, d['v_max_mps'], m['horizon_s'], m['step_s'])
    if early is None:
        return {'status': 'no_entry_within_horizon'}
    return {'status': 'window', 'window': [round(max(as_of, t0 + d['delay_s'][0] + early), 1),
                                           round(t0 + d['delay_s'][1] + late_exit, 1) if late_exit is not None else None],
            'hypothesis': 'cleared to cross; start time unknown within declared delay range', 'stationary': True}


def predict(as_of, tracks, intents, method='combined', m=MODEL):
    """tracks: {track_id: {'class': 'aircraft'|'vehicle', 'callsign': str, 'packets': [...]}}
    intents: {callsign: {'surface': id, 'direction': str, 'authorized': [types], 'hold': {'point':(x,y), 'acknowledged': bool}|None,
                         'cancel_unacknowledged': bool, 'airborne_until': t|None}}
    method: 'combined' | 'motion_only'
    """
    out = {'as_of': as_of, 'method': method, 'model': m['version'], 'actors': {}, 'candidates': [], 'unavailable': [],
           'assumptions': [m['note'], 'Fictional SIM-1 map and simulated observations.',
                           'Occupancy = footprint overlaps zone Z1; vertical state not modelled (surface movement only).']}
    windows = {}
    for tid, tr in tracks.items():
        cs, cls = tr['callsign'], tr['class']
        it = intents.get(cs, {})
        rec = {'callsign': cs, 'class': cls}
        if it.get('airborne_until') is not None and as_of < it['airborne_until'] and not [p for p in tr['packets'] if p['received_at'] <= as_of]:
            rec.update(status='unavailable', reason='airborne phase not modelled; no surface observation yet')
            out['unavailable'].append({'track': tid, 'reason': rec['reason']})
            out['actors'][tid] = rec
            continue
        if method == 'combined' and it.get('surface'):
            path = PATHS[(it['surface'], it['direction'])]
        else:  # no surface from intent (or motion-only): map-match the latest observed position to the nearest SIM path
            seen = [p for p in tr['packets'] if p['received_at'] <= as_of]
            if not seen:
                rec.update(status='unavailable', reason='no observation received yet')
                out['unavailable'].append({'track': tid, 'reason': rec['reason']})
                out['actors'][tid] = rec
                continue
            x, y = seen[-1]['position']['x'], seen[-1]['position']['y']
            path = min(PATHS.values(), key=lambda P: abs((x - P['start'][0]) * P['dir'][1] - (y - P['start'][1]) * P['dir'][0]))
            rec['path_basis'] = 'map-matched from observed position'
        est = estimate(tr['packets'], as_of, path)
        if est is None:
            rec.update(status='unavailable', reason='no observation received yet')
            out['unavailable'].append({'track': tid, 'reason': rec['reason']})
            out['actors'][tid] = rec
            continue
        rec['estimate'] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in est.items()}
        if est['age_s'] > m['stale_hard_s']:
            rec.update(status='unavailable', reason=f"last observation {est['age_s']} s old (> {m['stale_hard_s']} s): timed prediction withdrawn")
            out['unavailable'].append({'track': tid, 'reason': rec['reason']})
            out['actors'][tid] = rec
            continue
        w = occupancy_window(est, path, cls, as_of, m)
        if method == 'combined' and cls == 'vehicle' and 'CROSSING' in it.get('authorized', []) and w.get('stationary', w['status'] == 'no_entry_within_horizon') \
                and not it.get('cancel_unacknowledged') is True:
            dw = departure_window(est, path, cls, as_of, m)
            if dw['status'] == 'window':
                w = dw
        rec.update(status='stale_widened' if est['age_s'] > m['stale_soft_s'] else 'ok', occupancy=w)
        hyp = None
        if method == 'combined' and cls == 'vehicle' and it.get('hold') and 'CROSSING' not in it.get('authorized', []):
            hyp = hold_hypothesis(est, path, it['hold']['point'], cls, m)
            rec['hold_hypothesis'] = hyp
        if method == 'combined' and it.get('cancel_unacknowledged') and est['v'] > 1.0:
            rec['note'] = 'stop instructed but not acknowledged; observed motion persists, so the prediction keeps the moving hypothesis'
        if hyp and not hyp['plausible']:
            users = [c for c, i in intents.items() if c != cs and set(i.get('authorized', [])) & {'LANDING', 'TAKEOFF'}]
            if users:
                out['candidates'].append({'type': 'hold_exceedance', 'pair': [tid], 'callsigns': [cs] + users, 'zone': 'XSIM:ZONE:Z1',
                                          'overlap': None, 'lead_s': hyp.get('t_to_hold_s'), 'conditional': False, 'raised': True,
                                          'basis': [hyp['reason'], f"runway authorization live for {', '.join(users)}"]})
        if w['status'] == 'window':
            conditional = bool(hyp and hyp['plausible'])
            windows[tid] = {'window': w['window'], 'conditional': conditional, 'callsign': cs, 'hyp': hyp}
        out['actors'][tid] = rec
    ids = list(windows)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            A, B = windows[ids[i]], windows[ids[j]]
            a0, a1 = A['window'][0], A['window'][1] if A['window'][1] is not None else as_of + m['horizon_s']
            b0, b1 = B['window'][0], B['window'][1] if B['window'][1] is not None else as_of + m['horizon_s']
            lo, hi = max(a0, b0), min(a1, b1)
            if lo <= hi:
                cand = {'type': 'occupancy_overlap', 'pair': [ids[i], ids[j]], 'callsigns': [A['callsign'], B['callsign']], 'zone': 'XSIM:ZONE:Z1',
                        'overlap': [round(lo, 1), round(hi, 1)], 'lead_s': round(lo - as_of, 1),
                        'conditional': A['conditional'] or B['conditional'],
                        'basis': [h['hyp']['reason'] for h in (A, B) if h.get('hyp')]}
                cand['raised'] = not cand['conditional']
                out['candidates'].append(cand)
    return out
