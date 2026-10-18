"""FICTIONAL simulation map SIM-1 (prepared asset). Not a real airport. Not derived from any chart.

Frame: SIM-LOCAL-1, metres, x east, y north, origin at the west end of RW1 (fictional).
All numbers here are ENGINEERING CHOICES for a demo, not aviation standards or real-airport values.

Layout:
  RW1  runway '18/36' along +x from x=0 to x=2400 (36 direction = +x), centreline y=0, width 45
  K    taxiway along +y at x=1500, crossing RW1, from y=-400 to y=+400
  hold lines on K at y=-75 (south side) and y=+75 (north side)
  Z1   shared zone = RW1 protected area between the K hold lines: x in [1460,1540], y in [-75,75]

SimAirport mimics the identifier/topology interface of airport.Airport so the same ledger runs on it.
"""

SIM_MAP = {
    'map_id': 'SIM-1', 'is_fictional': True, 'icao': 'XSIM',
    'coordinate_frame': 'SIM-LOCAL-1 (fictional, metres, x east / y north)',
    'disclaimer': 'Fictional demo geometry. Not LaGuardia, not Logan, not any real airport.',
    'runways': {'XSIM:RWY:18-36': {'ends': {'18': {'heading_vec': (-1, 0)}, '36': {'heading_vec': (1, 0)}},
                                    'centerline': [(0.0, 0.0), (2400.0, 0.0)], 'width_m': 45}},
    'taxiways': {'XSIM:TWY:K': {'centerline': [(1500.0, -400.0), (1500.0, 400.0)], 'width_m': 23}},
    'hold_lines': {'XSIM:HOLD:K_SOUTH_RWY36': {'taxiway': 'XSIM:TWY:K', 'protected': 'XSIM:RWY:18-36', 'side': 'south', 'point': (1500.0, -75.0)},
                   'XSIM:HOLD:K_NORTH_RWY36': {'taxiway': 'XSIM:TWY:K', 'protected': 'XSIM:RWY:18-36', 'side': 'north', 'point': (1500.0, 75.0)}},
    'zones': {'XSIM:ZONE:Z1': {'xmin': 1460.0, 'xmax': 1540.0, 'ymin': -75.0, 'ymax': 75.0,
                               'meaning': 'RW1 protected area between the K hold lines (fictional)'}},
    'footprints_m': {'aircraft': 40.0, 'vehicle': 10.0},
}


class SimAirport:
    """Minimal stand-in for airport.Airport on the fictional map (identifier + topology review only)."""
    def __init__(self, m=SIM_MAP):
        self.m = m
        self.icao = m['icao']
        self.nodes = {'XSIM:N:K_RWY18-36': {'id': 'XSIM:N:K_RWY18-36', 'kind': 'runway_taxiway_junction',
                                            'surface_ids': ['XSIM:TWY:K', 'XSIM:RWY:18-36']}}

    @property
    def version(self):
        return {'icao': self.icao, 'dataset_version': 'SIM-1', 'status': 'FICTIONAL demo map'}

    def runway_end_candidates(self, d):
        d = (d or '').lstrip('0')
        return [f'XSIM:END:{d}'] if d in ('18', '36') else []

    def physical_runway(self, end_id):
        return 'XSIM:RWY:18-36' if end_id and end_id.startswith('XSIM:END:') else None

    def taxiway_candidates(self, letter):
        return ['XSIM:TWY:K'] if letter == 'K' else []

    def intersecting_runways(self):
        return set()

    def route_runway_check(self, route):
        out = []
        for w in route or []:
            if w == 'K':
                out.append({'word': 'K', 'taxiway_id': 'XSIM:TWY:K', 'status': 'crossing_recorded',
                            'crossings': [{'node_id': 'XSIM:N:K_RWY18-36', 'runway_id': 'XSIM:RWY:18-36'}]})
            else:
                out.append({'word': w, 'status': 'not_in_inventory', 'crossings': []})
        return out
