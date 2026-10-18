"""Read-only access to a GMAPS v1 airport graph (prepared library, stdlib only).

Only identifier lookup and topology REVIEW are offered, matching the v1 usability gates:
no routing, no shortest paths, no positions, no distances.
"""
import json
import re
from pathlib import Path


class Airport:
    def __init__(self, graph_path):
        self.path = Path(graph_path)
        self.g = json.loads(self.path.read_text(encoding='utf-8'))
        self.icao = self.g['airport']['icao']
        assert self.g['usability']['automatic_routing'] is False and self.g['usability']['metric_prediction'] is False, \
            'v1 graph must keep routing/prediction disabled'
        self.surfaces = {s['id']: s for s in self.g['surfaces']}
        self.ends = {e['id']: e for e in self.g['runway_ends']}
        self.nodes = {n['id']: n for n in self.g['nodes']}
        self.segments = {s['id']: s for s in self.g['segments']}
        self.holds = {h['id']: h for h in self.g['hold_points']}

    @property
    def version(self):
        return {'icao': self.icao, 'dataset_version': self.g['dataset_version'],
                'status': 'draft, expert review pending; topology review only'}

    # ---------- identifiers ----------
    def runway_end_candidates(self, designator):
        """'04' -> ['KLGA:END:04'] ; at KBOS '04' -> ['KBOS:END:04L','KBOS:END:04R'] (missing suffix stays ambiguous)."""
        if not designator:
            return []
        m = re.fullmatch(r'0?(\d{1,2})([LRC]?)', designator)
        if not m:
            return []
        num, suf = int(m.group(1)), m.group(2)
        out = []
        for e in self.ends.values():
            em = re.fullmatch(r'(\d{2})([LRC]?)', e['identifier'])
            if int(em.group(1)) == num and (suf == '' or em.group(2) == suf):
                out.append(e['id'])
        return sorted(out)

    def physical_runway(self, end_id):
        return self.ends[end_id]['runway_id'] if end_id in self.ends else None

    def taxiway_candidates(self, letter):
        return sorted(s['id'] for s in self.surfaces.values() if s['kind'] == 'taxiway' and s['identifier'] == letter)

    # ---------- topology review ----------
    def intersecting_runways(self):
        """Physical runway pairs that share a runway_intersection node in the draft graph."""
        pairs = set()
        for n in self.nodes.values():
            if n['kind'] == 'runway_intersection':
                rs = sorted(s for s in n['surface_ids'] if self.surfaces[s]['kind'] == 'runway')
                if len(rs) == 2:
                    pairs.add(tuple(rs))
        return pairs

    def runway_crossings_for_taxiway(self, taxiway_id):
        """[(node_id, runway_id)] where the draft graph records this taxiway meeting a runway."""
        out = []
        for n in self.nodes.values():
            if n['kind'] == 'runway_taxiway_junction' and taxiway_id in n['surface_ids']:
                for s in n['surface_ids']:
                    if self.surfaces[s]['kind'] == 'runway':
                        out.append((n['id'], s))
        return out

    def route_runway_check(self, route_letters):
        """For each route word, report recorded runway crossings or 'unresolved' (no reviewed data).
        This is NOT a route trace: order/direction of travel on a surface is unknown in v1."""
        report = []
        for w in route_letters or []:
            cands = self.taxiway_candidates(w)
            if not cands:
                report.append({'word': w, 'status': 'not_in_inventory', 'crossings': []})
                continue
            xs = self.runway_crossings_for_taxiway(cands[0])
            report.append({'word': w, 'taxiway_id': cands[0],
                           'status': 'crossing_recorded' if xs else 'no_crossing_data_in_draft_graph',
                           'crossings': [{'node_id': a, 'runway_id': b} for a, b in xs]})
        return report

    def holds_for(self, protected_runway_id, approach_taxiway_id=None):
        return [h for h in self.holds.values() if h['protected_surface_id'] == protected_runway_id and
                (approach_taxiway_id is None or h['approach_surface_id'] == approach_taxiway_id)]
