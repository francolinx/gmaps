"""Generate the FICTIONAL SIM-1 episodes (prepared fixtures).

Writes, per episode:
  episodes/<id>.transcript.json  - speech lines to RE-VOICE (runtime-readable: this is the 'radio')
  episodes/<id>.obs.json         - simulated observation packets (runtime-readable: this is the 'sensor')
  eval/truth/<id>.truth.json     - kinematic truth (EVALUATION ONLY - never given to the predictor)
  eval/labels/<id>.labels.json   - expected answers derived from truth (EVALUATION ONLY)

usage: python3 tools/make_episodes.py            # the six named episodes
       python3 tools/make_episodes.py --hidden N --seed S   # N unfamiliar episodes for a blind test at the venue
"""
import argparse, json, random, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.sim.truth import simulate, zone_interval, state_at, path_point
from gmaps_core.sim.observe import emit
from gmaps_core.sim.simmap import SIM_MAP

RWY36 = {'start': (0.0, 0.0), 'dir': (1.0, 0.0)}
K_N = {'start': (1500.0, -400.0), 'dir': (0.0, 1.0)}
ZONE = SIM_MAP['zones']['XSIM:ZONE:Z1']
HOLD_S = 325.0  # along K: hold line y=-75


def aircraft(touchdown=20.0, v0=70.0, decel=-2.0, s0=300.0, v_taxi=12.0):
    return {'track_id': 'T-AC1', 'callsign': 'SIM212', 'class': 'aircraft', 'path': RWY36, 't_start': touchdown, 's0': s0, 'v0': v0,
            'obs_from': touchdown, 'phases': [{'t_from': touchdown, 'accel': decel, 'v_max': v0, 'v_min': v_taxi}]}


def truck(t_go=31.0, s0=100.0, v0=0.0, accel=2.0, vmax=12.0, stop_at=None, t_start=0.0):
    ph = [{'t_from': t_start, 'accel': 0.0 if v0 == 0 else 0.0, 'v_max': max(v0, 0.0), 'v_min': v0}]
    ph.append({'t_from': t_go, 'accel': accel, 'v_max': vmax, 'v_min': 0.0})
    if stop_at is not None:
        ph.append({'t_from': stop_at[0], 'accel': 0.0, 'v_max': vmax, 'v_min': 0.0, 'stop_at_s': stop_at[1]})
    return {'track_id': 'T-VH7', 'callsign': 'RESCUE7', 'class': 'vehicle', 'path': K_N, 't_start': t_start, 's0': s0, 'v0': v0,
            'obs_from': t_start, 'phases': ph}


def L(t, freq, speaker, text, **kw):
    d = {'t': t, 'freq': freq, 'speaker': speaker, 'text_clean': text}
    d.update(kw)
    return d


LAND = [L(0, 'tower', 'ATC', 'SimAir two one two, runway three six, cleared to land.'),
        L(3, 'tower', 'SIM212', 'Cleared to land runway three six, SimAir two one two.')]
REQ = [L(24, 'tower', 'RESCUE7', 'Tower, Rescue seven, request to cross runway three six at Kilo.')]
CROSS = lambda t: [L(t, 'tower', 'ATC', 'Rescue seven, cross runway three six at Kilo.'),
                   L(t + 3, 'tower', 'RESCUE7', 'Crossing runway three six at Kilo, Rescue seven.')]

EPISODES = {
    'SIM_E1_CROSS_DURING_ROLLOUT': dict(
        title='Crossing clearance issued while a landing aircraft is rolling out (fictional)',
        speech=LAND + REQ + CROSS(28), actors=[aircraft(), truck(t_go=31.0)], drops={},
        purpose='Positive case: look-ahead shows overlapping occupancy windows at Z1 before the simulated encounter.'),
    'SIM_E2_SAME_PATHS_LATER': dict(
        title='Same paths, crossing issued after the aircraft has passed (fictional)',
        speech=LAND + [L(70, 'tower', 'RESCUE7', 'Tower, Rescue seven, request to cross runway three six at Kilo.')] + CROSS(74),
        actors=[aircraft(), truck(t_go=77.0)], drops={},
        purpose='Timing case: authorizations still overlap in the ledger (route-only would alarm), but observed motion shows no occupancy overlap.'),
    'SIM_E3_STOP_NOT_OBSERVED': dict(
        title='Stop issued, but observed motion continues (fictional)',
        speech=LAND + REQ + CROSS(28) + [L(38, 'tower', 'ATC', 'Rescue seven, stop, stop, stop.')],
        actors=[aircraft(), truck(t_go=31.0)], drops={},
        purpose='A cancellation does not instantly stop observed motion; the candidate must not be dismissed prematurely.'),
    'SIM_E4_FEED_LOSS': dict(
        title='Vehicle track lost mid-crossing (fictional)',
        speech=LAND + REQ + CROSS(28), actors=[aircraft(), truck(t_go=31.0)], drops={'T-VH7': [(36.0, 70.0)]},
        purpose='Lost observations: precise prediction is withdrawn instead of extrapolated forever.'),
    'SIM_E5_HOLD_NOT_SLOWING': dict(
        title='Hold-short read back, but the vehicle is not slowing (fictional)',
        speech=LAND + [L(18, 'tower', 'ATC', 'Rescue seven, hold short runway three six at Kilo, traffic landing.'),
                       L(21, 'tower', 'RESCUE7', 'Hold short runway three six at Kilo, Rescue seven.')],
        actors=[aircraft(), truck(t_go=0.0, s0=0.0, v0=5.0, accel=0.15, vmax=9.0)], drops={},
        purpose='Intent-aware case: instruction says hold short; observed motion is inconsistent with stopping at the hold line.'),
    'SIM_E6_HOLD_OK': dict(
        title='Hold-short read back and the vehicle slows normally (fictional)',
        speech=LAND + [L(18, 'tower', 'ATC', 'Rescue seven, hold short runway three six at Kilo, traffic landing.'),
                       L(21, 'tower', 'RESCUE7', 'Hold short runway three six at Kilo, Rescue seven.')],
        actors=[aircraft(), truck(t_go=0.0, s0=0.0, v0=5.0, accel=0.15, vmax=9.0, stop_at=(32.0, HOLD_S - 6.0))], drops={},
        purpose='Negative case for intent: motion-only extrapolation would project a crossing; the hold-short intent plus observed deceleration does not.'),
}


def labels_for(eid, ep, t_end):
    series = {a['track_id']: simulate(a, t_end) for a in ep['actors']}
    ivs = {a['track_id']: zone_interval(series[a['track_id']], a['path'], ZONE, SIM_MAP['footprints_m'][a['class']]) for a in ep['actors']}
    ac, vh = ivs.get('T-AC1'), ivs.get('T-VH7')
    enc = None
    if ac and vh and max(ac[0], vh[0]) <= min(ac[1], vh[1]):
        enc = [max(ac[0], vh[0]), min(ac[1], vh[1])]
    # hold-line exceedance: vehicle front passes y=-75 at any time
    exceed = None
    for a in ep['actors']:
        if a['class'] == 'vehicle':
            for t, s, v in series[a['track_id']]:
                if s + SIM_MAP['footprints_m']['vehicle'] / 2 >= HOLD_S:
                    exceed = t
                    break
    has_hold = any('HOLD SHORT' in l['text_clean'].upper() for l in ep['speech'])
    should_raise = bool(enc) or (has_hold and exceed is not None)
    return {'episode_id': eid, 'generated_from': 'simulation truth (evaluation only)',
            'truth_zone_intervals': ivs, 'truth_encounter_interval': enc, 'vehicle_passes_hold_line_at': exceed,
            'should_raise_candidate': should_raise,
            'expected_ledger_rules': sorted(({'R1'} if any('cross runway' in l['text_clean'].lower() and l['speaker'] == 'ATC' for l in ep['speech']) else set())
                                            | ({'R13'} if any('stop' in l['text_clean'].lower() and l['speaker'] == 'ATC' for l in ep['speech']) else set())),
            'note': 'Labels are computed from truth that the runtime never sees. Prediction quality is scored against these, not tuned to them.'}


def write(eid, ep, seed, t_end=110.0):
    (ROOT / 'episodes').mkdir(exist_ok=True)
    (ROOT / 'eval/labels').mkdir(parents=True, exist_ok=True)
    tr = {'id': eid, 'airport': 'XSIM', 'is_fictional': True, 'title': ep['title'], 'purpose': ep['purpose'],
          'map': 'SIM-1', 'revoice_note': 'Record each line as its own clip; file name = <id>_<t>.wav. Fictional callsigns.',
          'events': ep['speech']}
    obs = []
    for a in ep['actors']:
        obs += emit(a, t_end, rate_hz=1.0, drops=ep['drops'].get(a['track_id'], ()), seed=seed + sum(map(ord, a['track_id'])))
    tracks = {a['track_id']: {'class': a['class'], 'callsign': a['callsign']} for a in ep['actors']}
    (ROOT / f'episodes/{eid}.transcript.json').write_text(json.dumps(tr, indent=1))
    (ROOT / f'episodes/{eid}.obs.json').write_text(json.dumps({'id': eid, 'is_simulated': True, 'tracks': tracks,
                                                               'packets': sorted(obs, key=lambda p: p['received_at'])}, indent=1))
    (ROOT / f'eval/truth/{eid}.truth.json').write_text(json.dumps({'id': eid, 'EVALUATION_ONLY': True, 'actors': ep['actors'], 'drops': ep['drops']}, indent=1))
    lab = labels_for(eid, ep, t_end)
    (ROOT / f'eval/labels/{eid}.labels.json').write_text(json.dumps(lab, indent=1))
    return lab


def hidden(n, seed):
    rng = random.Random(seed)
    out = {}
    for k in range(n):
        td = rng.uniform(15, 25)
        v0 = rng.uniform(60, 75)
        dec = rng.uniform(-2.6, -1.6)
        kind = rng.choice(['cross', 'cross_late', 'hold_ok', 'hold_bad'])
        speech = [L(0, 'tower', 'ATC', 'SimAir two one two, runway three six, cleared to land.'),
                  L(3, 'tower', 'SIM212', 'Cleared to land runway three six, SimAir two one two.')]
        if kind.startswith('cross'):
            tc = rng.uniform(22, 34) if kind == 'cross' else rng.uniform(70, 85)
            speech += CROSS(round(tc))
            veh = truck(t_go=round(tc) + 3.0, accel=rng.uniform(1.5, 2.5), vmax=rng.uniform(9, 14))
        else:
            speech += [L(18, 'tower', 'ATC', 'Rescue seven, hold short runway three six at Kilo, traffic landing.'),
                       L(21, 'tower', 'RESCUE7', 'Hold short runway three six at Kilo, Rescue seven.')]
            veh = truck(t_go=0.0, s0=rng.uniform(0, 60), v0=rng.uniform(5, 8), accel=0.3, vmax=11.0,
                        stop_at=(rng.uniform(18, 26), HOLD_S - 6.0) if kind == 'hold_ok' else None)
        out[f'SIM_H{seed}_{k}'] = dict(title='Hidden episode (blind test)', speech=speech, purpose='blind test',
                                       actors=[aircraft(touchdown=td, v0=v0, decel=dec), veh], drops={})
    return out


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('--hidden', type=int, default=0); a.add_argument('--seed', type=int, default=7)
    args = a.parse_args()
    eps = hidden(args.hidden, args.seed) if args.hidden else EPISODES
    for eid, ep in eps.items():
        lab = write(eid, ep, args.seed)
        if not args.hidden:
            print(eid, 'encounter', lab['truth_encounter_interval'], 'zone', lab['truth_zone_intervals'], 'exceed', lab['vehicle_passes_hold_line_at'], 'raise', lab['should_raise_candidate'])
        else:
            print('wrote', eid, '(labels hidden in eval/labels)')
