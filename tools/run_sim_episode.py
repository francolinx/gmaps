"""TEST HARNESS (prepared): replay a fictional SIM-1 episode through parser -> ledger -> look-ahead predictor.

Runtime inputs only: episodes/<id>.transcript.json (radio) and episodes/<id>.obs.json (sensor packets).
It never opens eval/truth or eval/labels. Each tick, only transcript lines with t <= as_of and packets with
received_at <= as_of are used, so no future input can influence an earlier result.

usage: python3 tools/run_sim_episode.py SIM_E1_CROSS_DURING_ROLLOUT [--out ui_scaffold/timeline.json] [--no-speaker-hints]
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.parser import parse
from gmaps_core.ledger import Ledger
from gmaps_core.sim.simmap import SimAirport, SIM_MAP
from gmaps_core.sim.predict import predict, MODEL

HOLD_LINES = SIM_MAP['hold_lines']


def intents_from(led, packets_by_cs, as_of):
    out = {}
    for cs in led.actors:
        res = [r for r in led.reservations if r['callsign'] == cs and r['status'] not in ('released', 'expired')]
        it = {'authorized': [r['type'] for r in res], 'hold': None, 'surface': None, 'direction': None,
              'cancel_unacknowledged': any(r.get('cancel_status') == 'unacknowledged' for r in res), 'airborne_until': None}
        for r in res:
            if r['type'] in ('LANDING', 'TAKEOFF'):
                it['surface'], it['direction'] = 'XSIM:RWY:18-36', r['runway_end'].lstrip('0')
                seen = [p for p in packets_by_cs.get(cs, []) if p['received_at'] <= as_of]
                if not seen:
                    it['airborne_until'] = float('inf')
            if r['type'] == 'CROSSING':
                it['surface'], it['direction'] = 'XSIM:TWY:K', 'north'
        holds = [p for p in led.pending if p['callsign'] == cs and (p.get('hold') or {}).get('runway')]
        if holds:
            h = holds[-1]
            seen = [p for p in packets_by_cs.get(cs, []) if p['received_at'] <= as_of]
            side = 'south' if (not seen or seen[-1]['position']['y'] < 0) else 'north'
            hl = next(v for v in HOLD_LINES.values() if v['side'] == side)
            it['hold'] = {'point': hl['point'], 'acknowledged': h.get('acknowledged') is True, 'side': side}
            if it['surface'] is None:
                it['surface'], it['direction'] = 'XSIM:TWY:K', 'north'
        out[cs] = it
    return out


def run(eid, hints=True, t_end=100):
    tr = json.loads((ROOT / f'episodes/{eid}.transcript.json').read_text())
    ob = json.loads((ROOT / f'episodes/{eid}.obs.json').read_text())
    ap = SimAirport()
    led = Ledger(ap)
    pending_lines = sorted(tr['events'], key=lambda e: e['t'])
    tracks = {tid: dict(meta, packets=[p for p in ob['packets'] if p['track_id'] == tid]) for tid, meta in ob['tracks'].items()}
    by_cs = {meta['callsign']: tracks[tid]['packets'] for tid, meta in ob['tracks'].items()}
    frames, heard = [], []
    for as_of in range(0, t_end + 1):
        while pending_lines and pending_lines[0]['t'] <= as_of:
            e = pending_lines.pop(0)
            ev = parse(e['text_clean'], 'XSIM', speaker_hint=('ATC' if e['speaker'] == 'ATC' else 'PILOT') if hints else None,
                       resolver=ap, event_time=e['t'], source={'id': f"{eid}_{e['t']}.wav", 'start_s': e['t'], 'end_s': None})
            led.ingest(ev, e['t'])
            heard.append({'t': e['t'], 'freq': e['freq'], 'text': e['text_clean'], 'speech_act': ev.get('speech_act_resolved', ev['speech_act']),
                          'kind': ev['kind'], 'callsigns': ev['actor_candidates'], 'event_id': ev['event_id']})
        led.tick(as_of)
        intents = intents_from(led, by_cs, as_of)
        comb = predict(as_of, tracks, intents, 'combined')
        mot = predict(as_of, tracks, intents, 'motion_only')
        route_only = [w['warning_id'] for w in led.warnings if w['status'] == 'open' and w['rule_id'] in ('R1', 'R2', 'R3', 'R4', 'R10', 'R6')]
        pos = {}
        for tid, t in tracks.items():
            seen = [p for p in t['packets'] if p['received_at'] <= as_of]
            if seen:
                pos[tid] = {'x': seen[-1]['position']['x'], 'y': seen[-1]['position']['y'], 'observed_at': seen[-1]['observed_at'],
                            'age_s': round(as_of - seen[-1]['observed_at'], 1), 'callsign': t['callsign'], 'class': t['class']}
        frames.append({'t': as_of, 'positions': pos, 'combined': comb, 'motion_only': mot,
                       'route_only_raised': bool(route_only), 'open_warnings': [w['warning_id'] for w in led.warnings if w['status'] == 'open' and w['t_fired'] <= as_of],
                       'intents': {k: {kk: vv for kk, vv in v.items() if kk != 'airborne_until'} for k, v in intents.items()}})
    return {'episode': {k: v for k, v in tr.items() if k != 'events'}, 'transcript': heard, 'frames': frames, 'map': SIM_MAP,
            'model': MODEL, 'ledger': {'warnings': led.warnings, 'notes': led.notes, 'reservations': led.reservations}}


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('episode'); a.add_argument('--out'); a.add_argument('--no-speaker-hints', action='store_true')
    args = a.parse_args()
    res = run(args.episode, hints=not args.no_speaker_hints)
    for f in res['frames']:
        c = [x for x in f['combined']['candidates']]
        m = [x for x in f['motion_only']['candidates'] if x['raised']]
        if c or m or f['combined']['unavailable'] or f['route_only_raised']:
            print(f"t={f['t']:3} comb={[('RAISED' if x['raised'] else 'cond', x['overlap']) for x in c]} motion={[x['overlap'] for x in m]} route={f['route_only_raised']} unavail={[u['track'] for u in f['combined']['unavailable']]}")
    for w in res['ledger']['warnings']:
        print('LEDGER', w['rule_id'], w['severity'], w['t_fired'], w['status'], w['explain'][:110])
    if args.out:
        Path(args.out).write_text(json.dumps(res, default=str))
