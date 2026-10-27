"""Stores the agent reads identifiers and transmissions through, and writes ledger records to (Open Agent Hackathon 2026).

Tables (same shape in both stores; tools/zetaris_load.py writes them):
  gmaps_identifiers(icao, kind, identifier, surface_id, source)   runways and taxiways per airport (XSIM fictional + KBOS graph;
                                                                   KLGA is never loaded)
  gmaps_transmissions(scenario, t, freq, speaker, text)            the scripted SIM-1 radio lines (fictional, synthetic)
  gmaps_ledger(run_id, seq, t, record_type, record_id, callsign, runway, status, body_json)   what the agent's ledger wrote

LocalStore: reads the repo files, appends ledger rows to runs/ledger/<run_id>.jsonl. Used in sample mode and as the fallback.
ZetarisStore: NOT INTEGRATED. The Zetaris documentation host was not reachable from the build session and no endpoint or
credentials were supplied, so there is no verified client here. It raises NotIntegrated instead of pretending; wire
_query() to the documented Zetaris SQL interface (REST / JDBC) with ZETARIS_URL / ZETARIS_USER / ZETARIS_PASSWORD.
"""
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXCLUDED_ICAO = {'KLGA'}   # no LaGuardia material of any kind


class NotIntegrated(RuntimeError):
    pass


def identifier_rows():
    """All identifier rows for the tables (deterministic order)."""
    import sys
    sys.path.insert(0, str(ROOT / 'lib'))
    from gmaps_core.sim.simmap import SIM_MAP
    rows = []
    for rid, r in SIM_MAP['runways'].items():
        rows += [{'icao': 'XSIM', 'kind': 'runway', 'identifier': e, 'surface_id': rid, 'source': 'fictional SIM-1 map'} for e in sorted(r['ends'])]
    rows += [{'icao': 'XSIM', 'kind': 'taxiway', 'identifier': tid.split(':')[-1], 'surface_id': tid, 'source': 'fictional SIM-1 map'} for tid in sorted(SIM_MAP['taxiways'])]
    for gp in sorted((ROOT / 'data/airports').glob('*.graph.json')):
        g = json.loads(gp.read_text())
        icao = g['airport']['icao']
        if icao in EXCLUDED_ICAO:
            continue
        src = f'{gp.relative_to(ROOT)} (display/identifier lookup only)'
        rows += [{'icao': icao, 'kind': 'runway', 'identifier': e['identifier'], 'surface_id': e['runway_id'], 'source': src} for e in g['runway_ends']]
        rows += [{'icao': icao, 'kind': 'taxiway', 'identifier': s['id'].split(':')[-1], 'surface_id': s['id'], 'source': src}
                 for s in g['surfaces'] if ':TWY:' in s['id']]
    return rows


def transmission_rows(scenarios=None):
    rows = []
    for p in sorted((ROOT / 'episodes').glob('SIM_E*.transcript.json')):
        d = json.loads(p.read_text())
        if scenarios and d['id'] not in scenarios:
            continue
        rows += [{'scenario': d['id'], 't': e['t'], 'freq': e['freq'], 'speaker': e['speaker'], 'text': e['text_clean']} for e in d['events']]
    return rows


class LocalStore:
    name = 'local'

    def __init__(self, ledger_dir=None):
        self.ledger_dir = Path(ledger_dir or ROOT / 'runs' / 'ledger')
        self._ids = None

    def identifiers(self, icao):
        if icao in EXCLUDED_ICAO:
            return {'runways': [], 'taxiways': []}
        if self._ids is None:
            self._ids = identifier_rows()
        rs = [r for r in self._ids if r['icao'] == icao]
        return {'runways': [r['identifier'] for r in rs if r['kind'] == 'runway'], 'taxiways': [r['identifier'] for r in rs if r['kind'] == 'taxiway']}

    def transmissions(self, scenario):
        return sorted(transmission_rows([scenario]), key=lambda r: r['t'])

    def append_ledger(self, run_id, rows):
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with (self.ledger_dir / f'{run_id}.jsonl').open('a') as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + '\n')

    def read_ledger(self, run_id):
        p = self.ledger_dir / f'{run_id}.jsonl'
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


class ZetarisStore(LocalStore):
    name = 'zetaris'

    def __init__(self):
        self.url, self.user = os.environ.get('ZETARIS_URL'), os.environ.get('ZETARIS_USER')
        raise NotIntegrated('Zetaris is not integrated: no verified client (docs unreachable from the build session, no credentials). '
                            'Use GMAPS_DATA=local.')
