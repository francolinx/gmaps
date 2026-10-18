"""Simulated observation emitter (prepared). Converts truth into observation packets the runtime may read.

Packet fields follow runtime_mapping_contract.json required_motion_fields. Every packet says is_simulated=True.
Noise, latency and dropouts are declared per episode so they are visible to reviewers, not hidden.
"""
import random
from .truth import simulate, path_point, state_at


def emit(actor, t_end, rate_hz=1.0, sigma_pos=3.0, sigma_vel=0.5, latency_s=0.6, drops=(), seed=0):
    rng = random.Random(seed)
    series = simulate(actor, t_end)
    pk = []
    k = 0
    t = actor.get('obs_from', actor['t_start'])
    while t <= t_end + 1e-9:
        st = state_at(series, t)
        if st is not None and not any(a <= t < b for a, b in drops):
            s, v = st
            x, y = path_point(actor['path'], s)
            dx, dy = actor['path']['dir']
            k += 1
            pk.append({
                'packet_id': f"{actor['track_id']}-{k:04d}", 'track_id': actor['track_id'],
                'actor_association_status': actor.get('association', 'associated_by_scenario_fixture'),
                'actor_hint': actor.get('callsign'),
                'observed_at': round(t, 2), 'received_at': round(t + latency_s, 2),
                'position': {'x': round(x + rng.gauss(0, sigma_pos), 2), 'y': round(y + rng.gauss(0, sigma_pos), 2)},
                'coordinate_frame': 'SIM-LOCAL-1 (fictional, metres)',
                'speed_or_velocity': {'vx': round(dx * v + rng.gauss(0, sigma_vel), 2), 'vy': round(dy * v + rng.gauss(0, sigma_vel), 2)},
                'uncertainty': {'sigma_pos_m': sigma_pos, 'sigma_vel_mps': sigma_vel, 'basis': 'declared simulation noise'},
                'provenance': 'simulated observation emitter (gmaps_core.sim.observe)',
                'is_simulated': True,
            })
        t = round(t + 1.0 / rate_hz, 6)
    return pk
