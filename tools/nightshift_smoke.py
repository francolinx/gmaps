#!/usr/bin/env python3
"""Nightshift smoke test: one residue transmission (fictional SIM-1 phrasing the grammar does not type) -> NightshiftLLM.parse_residue,
then the grounding rule. Prints model_reported + latency and writes runs/smoke/nightshift_<ts>.txt. Needs NIGHTSHIFT_BASE_URL,
NIGHTSHIFT_API_KEY (NIGHTSHIFT_MODEL optional). The key is never printed or written."""
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.providers.nightshift import NightshiftLLM      # noqa: E402
from gmaps_core.grounding import check_residue                  # noqa: E402
from gmaps_core.parser import parse                             # noqa: E402

TEXT = 'Rescue seven uh go ahead and get across three six at kilo'
lines = [f'GMAPS Nightshift smoke  {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}  (SIMULATED transmission, fictional SIM-1)']
try:
    llm = NightshiftLLM()
except ValueError as e:
    print(f'NOT RUN: {e}'); sys.exit(2)
g = parse(TEXT, 'XSIM')
lines.append(f'transmission: {TEXT!r}  grammar: speech_act={g["speech_act"]} kind={g["kind"]}')
d = llm.parse_residue(TEXT, 'XSIM')
call = d.pop('_call')
ok, verdict = check_residue(d, TEXT, {'runways': ['18', '36'], 'taxiways': ['K']})
lines += [f'base_url host: {llm.base.split("/")[2]}', f'model_requested: {call["model_requested"]}', f'model_reported: {call["model_reported"]}',
          f'latency_ms: {call["latency_ms"]}  attempts: {call.get("attempts")}  tokens in/out: {call["prompt_tokens"]}/{call["completion_tokens"]}',
          f'error: {call["error"]}', f'answer: {json.dumps(d)}', f'grounding: {"accepted" if ok else "rejected"} ({verdict})']
out = ROOT / 'runs' / 'smoke' / f'nightshift_{time.strftime("%Y%m%d-%H%M%S", time.gmtime())}.txt'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text('\n'.join(lines) + '\n')
print('\n'.join(lines)); print(f'written: {out.relative_to(ROOT)}')
sys.exit(0 if call['ok'] else 1)
