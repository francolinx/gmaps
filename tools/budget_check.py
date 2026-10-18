"""Parameter-budget check for the submitted runtime. The event caps the system at 33B TOTAL parameters;
until the hosts say otherwise, count every model that runs in the judged system (ASR + parser + agent + drafter + adapters).

  python3 tools/budget_check.py                 # all configurations, exit 1 if the default (safe) one is over
  python3 tools/budget_check.py --config lightning --no-drafter
  python3 tools/budget_check.py --config lightning-grammar     # configuration B as judged: Whisper + grammar parser + Lightning, no drafter
  python3 tools/budget_check.py --config combined             # A + B together: OVER the cap, never one judged system

Parameter counts come from docs/MODEL_MANIFEST.md (model-card figures; "30B total / 3B active" counts as 30B: active
parameters are not what the rule says). LoRA adapters are counted explicitly (r=16 on all linear layers ≈ 0.8 % of the base).
"""
import argparse, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CAP_B = 33.0
LORA_FRACTION = 0.008   # r=16 adapter on q/k/v/o/gate/up/down ≈ 0.8 % of a Qwen3 base (33M on 4B); measured value goes in report.json

CONFIGS = {
    'safe': [('openai/whisper-large-v3-turbo', 'ASR'), ('Qwen/Qwen3-4B-Instruct-2507', 'parser + agent (one model, two roles)'), ('lora:Qwen/Qwen3-4B-Instruct-2507', 'parser LoRA')],
    'safe-nano': [('openai/whisper-large-v3-turbo', 'ASR'), ('nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16', 'parser + agent'), ('lora:nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16', 'parser LoRA')],
    'lightning': [('openai/whisper-large-v3-turbo', 'ASR'), ('Qwen/Qwen3-1.7B', 'parser'), ('lora:Qwen/Qwen3-1.7B', 'parser LoRA'),
                  ('nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4', 'agent / explainer'), ('nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark', 'speculative drafter')],
    # configuration B (Tool-Calling / Lightning, Sat Oct 17): the parser is the grammar (no language model), no drafter
    'lightning-grammar': [('openai/whisper-large-v3-turbo', 'ASR'), ('nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4', 'agent (grammar parser: 0 parameters, no drafter)')],
    # A + B in one system: the script prints OVER; this is not a submitted system
    'combined': [('openai/whisper-large-v3-turbo', 'ASR'), ('Qwen/Qwen3-4B-Instruct-2507', 'parser (configuration A)'), ('lora:Qwen/Qwen3-4B-Instruct-2507', 'parser LoRA (configuration A)'),
                 ('nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4', 'agent (configuration B)')],
}
NEVER_JUDGED = {'combined': 'NOT a submitted system: configurations A and B never run together as one judged system'}


def parse_params(cell):
    m = re.search(r'([\d.]+)\s*B\b', cell)
    if m:
        return float(m.group(1))
    m = re.search(r'([\d.]+)\s*M\b', cell)
    if m:
        return float(m.group(1)) / 1000
    return None


def manifest_params():
    out = {}
    for line in (REPO / 'docs/MODEL_MANIFEST.md').read_text().splitlines():
        if not line.startswith('|') or line.startswith('|---') or line.startswith('| repo id'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) >= 4:
            p = parse_params(cells[3])
            if p is not None:
                out[cells[0]] = p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', choices=sorted(CONFIGS) + ['all'], default='all')
    ap.add_argument('--no-drafter', action='store_true', help='Lightning without DSpark speculative decoding')
    ap.add_argument('--lightning-total', type=float, default=None, help='override Lightning total (e.g. 31.6 per Artificial Analysis)')
    a = ap.parse_args()
    params = manifest_params()
    bad = 0
    for name in (sorted(CONFIGS) if a.config == 'all' else [a.config]):
        rows, total = [], 0.0
        for repo, role in CONFIGS[name]:
            if a.no_drafter and 'DSpark' in repo:
                continue
            if repo.startswith('lora:'):
                base = params.get(repo[5:])
                p = round(base * LORA_FRACTION, 3) if base else None
            else:
                p = params.get(repo)
                if 'Lightning-30B-A3B-NVFP4' in repo and not repo.endswith('DSpark') and a.lightning_total:
                    p = a.lightning_total
            if p is None:
                print(f'  !! {repo}: no parameter count in docs/MODEL_MANIFEST.md'); bad += 1; continue
            total += p; rows.append((repo, role, p))
        ok = total <= CAP_B
        print(f"\n== {name}{' (no drafter)' if a.no_drafter else ''}: {total:.2f}B {'OK' if ok else 'OVER'} (cap {CAP_B:.0f}B, headroom {CAP_B - total:+.2f}B)")
        for repo, role, p in rows:
            print(f'   {p:6.3f}B  {repo:62s} {role}')
        if name in NEVER_JUDGED:
            print(f'   !! {NEVER_JUDGED[name]}')
        if not ok and (a.config == name or (a.config == 'all' and name == 'safe')):
            bad += 1
    print('\nCounts are model-card totals; "A3B" active counts do not reduce the total. Confirm with the hosts how Lightning (30B card / 31.6B per Artificial Analysis) and drafters are counted.')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
