"""SIMULATION TRUTH (prepared). Readable by the observation emitter and the evaluation runner ONLY.
The predictor must never import this module or read *.truth.json files (enforced by tests/test_separation.py).

An actor moves along a straight path (start point + unit direction) with a phase schedule:
  phases: [{"t_from": s, "accel": m/s2, "v_max": m/s, "v_min": m/s, "stop_at_s": m (optional)}]
Integration step 0.05 s. Numbers in episode files are fictional engineering choices.
"""
import math


def path_point(path, s):
    (x0, y0), (dx, dy) = path['start'], path['dir']
    return (x0 + dx * s, y0 + dy * s)


def simulate(actor, t_end, dt=0.05):
    t, s, v = actor['t_start'], actor['s0'], actor['v0']
    phases = sorted(actor['phases'], key=lambda p: p['t_from'])
    out = [(round(t, 3), s, v)]
    while t < t_end - 1e-9:
        ph = [p for p in phases if p['t_from'] <= t + 1e-9]
        p = ph[-1] if ph else {'accel': 0.0, 'v_max': v, 'v_min': 0.0}
        a = p['accel']
        if p.get('stop_at_s') is not None:
            rem = p['stop_at_s'] - s
            if rem <= 0.05 or v <= 0.01:
                a, v = 0.0, 0.0
            else:
                need = v * v / (2 * rem)
                a = -need
        v_new = min(p.get('v_max', 1e9), max(p.get('v_min', 0.0), v + a * dt))
        s += 0.5 * (v + v_new) * dt
        v = v_new
        t += dt
        out.append((round(t, 3), s, v))
    return out


def state_at(series, t):
    """Linear interpolation of (s, v) at time t; None before start."""
    if t < series[0][0]:
        return None
    for (t0, s0, v0), (t1, s1, v1) in zip(series, series[1:]):
        if t0 <= t <= t1:
            f = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            return s0 + f * (s1 - s0), v0 + f * (v1 - v0)
    return series[-1][1], series[-1][2]


def zone_interval(series, path, zone, footprint):
    """Truth occupancy interval of the zone for an actor (footprint centred on the reference point)."""
    inside = []
    for t, s, v in series:
        x, y = path_point(path, s)
        dx, dy = path['dir']
        half = footprint / 2
        xs = (x - abs(dx) * half, x + abs(dx) * half)
        ys = (y - abs(dy) * half, y + abs(dy) * half)
        if xs[1] >= zone['xmin'] and xs[0] <= zone['xmax'] and ys[1] >= zone['ymin'] and ys[0] <= zone['ymax']:
            inside.append(t)
    return (inside[0], inside[-1]) if inside else None
