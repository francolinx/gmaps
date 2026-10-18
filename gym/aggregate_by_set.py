"""Per-dataset aggregate of a `gym eval run` rollouts file.

`gym eval run --split validation` collates every validation dataset of the environment (heldout5_script, heldout4_asr,
heldout4_asr_prompt) into one rollouts file; each rollout row still carries the task's `split` and `text_source`, so one run
per model is enough and this script splits the metrics back out, in the same shape as gym/run_offline_policy.py's summary so
tools/scoreboard.py can show both side by side (rows from this script are marked `via: gym eval run`).

  python3 gym/aggregate_by_set.py gym/rollouts/heldout_validation_gmaps-parser.jsonl            # prints + writes <name>_by_set.json
"""
import argparse, json, statistics
from pathlib import Path

FIELDS = ('speaker_role', 'callsign', 'runway', 'instruction', 'taxiways')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rollouts')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    p = Path(a.rollouts)
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    sets = {}
    for r in rows:
        key = f"{r.get('split')}_{r.get('text_source')}"
        sets.setdefault(key, []).append(r)
    out = {'SIMULATED': True, 'via': 'gym eval run', 'rollouts': str(p), 'model': rows[0].get('response', {}).get('model') if rows else None, 'sets': {}}
    for key, rs in sorted(sets.items()):
        comps = [r.get('reward_components') or {} for r in rs]
        summary = {'n': len(rs), 'record_exact': round(statistics.mean(r.get('reward', 0.0) for r in rs), 3),
                   'field_exact': {f: round(statistics.mean(c.get(f, 0.0) for c in comps), 3) for f in FIELDS},
                   'json_valid': round(statistics.mean(r.get('json_valid', 0.0) for r in rs), 3),
                   'groundable': round(statistics.mean(r.get('groundable', 0.0) for r in rs), 3),
                   'record_exact_and_groundable': round(statistics.mean(float(r.get('reward', 0.0) == 1.0 and r.get('groundable', 0.0) == 1.0) for r in rs), 3),
                   'failures': sum(1 for r in rs if r.get('failure_kind'))}
        out['sets'][key] = summary
        print(f"{key:28s} n={summary['n']:4d} record_exact={summary['record_exact']:.3f} groundable={summary['groundable']:.3f} json_valid={summary['json_valid']:.3f} "
              f"both={summary['record_exact_and_groundable']:.3f} fields={summary['field_exact']}")
    dest = Path(a.out) if a.out else p.with_name(p.stem + '_by_set.json')
    dest.write_text(json.dumps(out, indent=1))
    print(f'-> {dest}')


if __name__ == '__main__':
    main()
