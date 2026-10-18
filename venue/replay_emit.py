"""Replay emitter (venue code). Runs OUTSIDE the agent runtime.

Reads the full simulated observation file from the repo and writes to runtime/feed/<episode>.json only the
packets with received_at <= as_of, plus track metadata (callsign, class). The tool server never sees
future packets because they are never written.

  python3 venue/replay_emit.py SIM_E1_CROSS_DURING_ROLLOUT --as-of 28
"""
import argparse, json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FEED = Path.home() / 'gmaps_venue' / 'runtime' / 'feed'


def emit(episode, as_of):
    ob = json.loads((REPO / 'episodes' / f'{episode}.obs.json').read_text())
    pk = [p for p in ob['packets'] if p['received_at'] <= as_of]
    FEED.mkdir(parents=True, exist_ok=True)
    out = FEED / f'{episode}.json'
    tmp = out.with_suffix('.tmp')
    tmp.write_text(json.dumps({'episode': episode, 'emitted_through': as_of, 'tracks': ob['tracks'], 'packets': pk}))
    tmp.replace(out)
    return out, len(pk), len(ob['packets'])


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('episode'); a.add_argument('--as-of', type=float, required=True)
    args = a.parse_args()
    out, n, total = emit(args.episode, args.as_of)
    print(f'{out}: {n}/{total} packets with received_at <= {args.as_of}')
