"""TEST HARNESS (prepared): replay a transcript fixture through parser -> ledger and print/export results.

This is not the competition agent. It exists to test the prepared libraries against fixtures and to
produce a JSON timeline the display scaffold can render. At the venue, the agent you build replaces the
fixture transcript with live ASR output and tool calls.

usage: python3 tools/replay_fixture.py SCENARIO_ID [--no-speaker-hints] [--text raw|clean] [--out file.json]
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.parser import parse
from gmaps_core.airport import Airport
from gmaps_core.ledger import Ledger

GRAPHS = {'KLGA': ROOT / 'data/airports/klga.graph.json', 'KBOS': ROOT / 'data/airports/kbos.graph.json'}


def load_scenarios():
    out = {}
    for f in [ROOT / 'data/specs/scenarios.json', *sorted((ROOT / 'episodes').glob('*.transcript.json')), *sorted((ROOT / 'data/specs').glob('*.scenario.json'))]:
        d = json.loads(f.read_text())
        for s in d.get('scenarios', [d] if 'events' in d else []):
            out[s['id']] = s
    return out


def airport_for(icao):
    """v1 graph where one exists (KLGA, KBOS); otherwise the chart identifiers of the display build (eval/synthetic/chart_ids.py):
    identifier lookup and topology only, the same interface; no coordinates reach the ledger."""
    if icao in GRAPHS:
        return Airport(GRAPHS[icao])
    sys.path.insert(0, str(ROOT / 'eval/synthetic'))
    from chart_ids import ChartIdentifiers
    return ChartIdentifiers(icao)


def run(scn, hints=True, text='clean'):
    ap = airport_for(scn['airport'])
    led = Ledger(ap)
    timeline = []
    for e in scn['events']:
        t = e['t']
        if e.get('freq') in ('adsb', 'surveillance'):
            led.observe(e.get('callsign'), e['text_clean'], t, source=e.get('freq'))
            timeline.append({'t': t, 'freq': e['freq'], 'text': e['text_clean'], 'kind': 'OBSERVATION'})
            continue
        if e.get('freq') == 'phone':
            led.notes.append({'t': t, 'event_id': None, 'note': 'phone/landline: ' + e['text_clean']})
            timeline.append({'t': t, 'freq': 'phone', 'text': e['text_clean'], 'kind': 'PHONE'})
            continue
        utter = e.get('text_raw') if (text == 'raw' and e.get('text_raw')) else e['text_clean']
        hint = None
        if hints:
            hint = 'ATC' if e.get('speaker') == 'ATC' else 'PILOT'
        ev = parse(utter, scn['airport'], speaker_hint=hint, resolver=ap, event_time=t,
                   source={'id': f"{scn['id']}#{t}", 'start_s': t, 'end_s': None},
                   confidence_override=e.get('confidence'))
        led.ingest(ev, t)
        timeline.append({'t': t, 'freq': e.get('freq'), 'text': utter, 'event': ev})
    led.tick(max(e['t'] for e in scn['events']) + 120)
    return led, timeline


if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument('scenario'); a.add_argument('--no-speaker-hints', action='store_true')
    a.add_argument('--text', default='clean'); a.add_argument('--out')
    args = a.parse_args()
    scn = load_scenarios()[args.scenario]
    led, tl = run(scn, hints=not args.no_speaker_hints, text=args.text)
    for w in led.warnings:
        print(f"[{w['severity']:8}] {w['rule_id']:4} t={w['t_fired']:>6} {w['status']:7} {w['explain'][:150]}")
        if w['status'] == 'cleared':
            print(f"           cleared t={w['cleared_at']} by: {w['cleared_by']}")
    for n in led.notes:
        print('   note', n['t'], n['note'][:140])
    if args.out:
        Path(args.out).write_text(json.dumps({'scenario': {k: v for k, v in scn.items() if k != 'events'}, 'timeline': tl, **led.export()}, indent=1, default=str))
