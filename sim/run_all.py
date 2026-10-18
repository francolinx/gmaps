"""Run the whole simulator library unattended and write the scorecard (SIMULATED; fictional XSIM; synthetic audio only).

  python3 -m sim.run_all [--speed 1.0] [--only ID ...] [--set PREFIX] [--out-json eval/results/sim_<date>.json] [--no-docs]
                         [--traffic off|perfect] [--whisper-prompt] [--llm | --no-llm] [--text | --replay RESULTS.json]

Options (claude/sim-upgrades, all off by default, so the default run is the Q5 run):
  --traffic perfect   give the fast path a traffic list (ADS-B stand-in): scenario callsigns + fixed distractors (sim/options.py)
  --whisper-prompt    Whisper uses the round-3 initial prompt built for XSIM (sim/options.whisper_prompt; same for every scenario)
  --no-llm            turn the local-LLM parse fallback off (it is on by default in audio runs, as in the server's main())
  --text              TEXT MODE: script text straight to the fast path on a simulated clock. No audio, no Whisper, no vLLM
                      (LLM off unless --llm). Runs on any CPU box in seconds. Checks scenarios against the deterministic pipeline.
  --replay FILE       REPLAY MODE: like --text, but each transmission's text is what Whisper heard in that earlier audio run
                     so the same misheard words can be re-run with other options.
Output files carry the options in their name (e.g. sim_<date>_traffic_prompt.json), so runs never overwrite each other.

Steps: load sim/scenarios/*.yaml -> render missing clips (Kokoro, local cache outside the repo) -> start one warm Whisper worker
-> run every scenario through the live path in real time (sim/runner.py) -> score (sim/score.py) -> eval/results/sim_<date>.json
and docs/SIM_SCORECARD_<date>.md. Needs the Spark (Kokoro venv, Whisper weights, the local container image, vLLM for the LLM
fallback). Never reads eval/truth or eval/labels; never changes the parser or ledger.
"""
import argparse, json, sys, time
from pathlib import Path

from sim import options, runner, scenario, score

ROOT = Path(__file__).resolve().parents[1]


def scorecard_md(res):
    o = res['overall']
    c = res.get('config') or {}
    audio = not str(c.get('mode', 'audio')).startswith(('text', 'replay'))
    what = ('Fictional XSIM airport (SIM-1 map), scripted traffic, **synthetic Kokoro voices** through a simulated radio channel, '
            'streamed in real time through end of speech → Whisper → the deployed fast path (parser + ledger rules + clock). ') if audio else (
            f"Fictional XSIM airport (SIM-1 map), scripted traffic, **{c.get('mode')}**: text goes straight to the deployed fast path "
            '(parser + ledger rules) on a simulated 1 s clock. **No audio and no speech recognition, so no latency is measured**. '
            + ('It re-runs the exact words Whisper heard in that audio run, with this run\'s options. ' if str(c.get('mode')).startswith('replay')
               else 'With perfect transcripts this checks the scenarios against the deterministic pipeline. '))
    L = [f"# Simulator scorecard, {res['date']} (SIMULATED)", '', what +
         'No real ATC audio. A warning means two recorded authorizations or an open obligation at time t.', '',
         f"Config: traffic list **{c.get('traffic', 'off')}**, Whisper prompt **{'on' if c.get('whisper_prompt') else 'off'}**, "
         f"LLM fallback **{'on' if c.get('llm_fallback', True) else 'off'}**.", '',
         f"Run: speed ×{res['speed']}, {res['started']} → {res['finished']}, {res['transmissions']} transmissions. "
         f"Source: `{res['out_json']}`.", '',
         '| scenario | expected | detected | missed | false alarms | latency (s, end of transmission → alert) | result |',
         '|---|---|---|---|---|---|---|']
    for p in res['per_scenario']:
        L.append(f"| {p['scenario']} | {p['expected']} | {p['detected']} | {p['missed']} | {p['false_alarms']} | "
                 f"{', '.join(f'{x:.2f}' for x in p['latency_s']) or '—'} | {'PASS' if p['pass'] else 'FAIL'} |")
    lat = o['latency_s']
    L += [f"| **overall** | {o['expected']} | {o['detected']} | {o['missed']} | {o['false_alarms']} | "
          f"median {lat['median']}, max {lat['max']} (n={lat['n']}) | {o['passed']}/{o['scenarios']} pass |", '',
          '## What Whisper heard (per transmission)', '', '| scenario | tx | script | Whisper | path | typed as | ASR s | end-of-speech wait s |', '|---|---|---|---|---|---|---|---|']
    for sid, run in res['runs'].items():
        for h in run['heard']:
            L.append(f"| {sid} | {h['tx']} | {h['script']} | {h['whisper']} | {h.get('path')} | {h['kind'] or '—'} | {h['asr_s']} | {h['endpoint_wait_s']} |")
    L += ['', 'Misses and false alarms are findings (docs/SIM_FINDINGS.md); the parser and ledger are frozen and were not changed. '
          'What this does and does not show: docs/SIM_README.md.', '']
    return '\n'.join(L)


def _tag(args):
    mode = '_text' if args.text else ('_replay' if args.replay else '')
    llm_on = args.llm if args.llm is not None else not (args.text or args.replay)
    return (mode + ('_traffic' if args.traffic == 'perfect' else '') + ('_prompt' if args.whisper_prompt else '')
            + ('' if llm_on == (not (args.text or args.replay)) else ('_llm' if llm_on else '_nollm'))), llm_on


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('--speed', type=float, default=1.0, help='scenario clock speed-up (1.0 = real time)')
    a.add_argument('--only', nargs='*'); a.add_argument('--out-json'); a.add_argument('--no-docs', action='store_true')
    a.add_argument('--set', help='only scenarios whose id starts with this prefix (e.g. Q6)')
    a.add_argument('--traffic', choices=('off', 'perfect'), default='off')
    a.add_argument('--whisper-prompt', action='store_true')
    a.add_argument('--llm-url', help='LLM fallback base URL for this run (sim only; default: runtime_config, :8000)')
    a.add_argument('--llm-model', help='LLM fallback model name for this run (with --llm-url)')
    g = a.add_mutually_exclusive_group(); g.add_argument('--llm', dest='llm', action='store_true', default=None); g.add_argument('--no-llm', dest='llm', action='store_false')
    m = a.add_mutually_exclusive_group(); m.add_argument('--text', action='store_true'); m.add_argument('--replay')
    args = a.parse_args(argv)
    if args.whisper_prompt and (args.text or args.replay):
        a.error('--whisper-prompt needs audio (no --text / --replay)')
    tag, llm_on = _tag(args)
    if bool(args.llm_url) != bool(args.llm_model):
        a.error('--llm-url and --llm-model go together')
    ep = (args.llm_url, args.llm_model) if args.llm_url else None
    date = time.strftime('%Y-%m-%d')
    out_json = Path(args.out_json) if args.out_json else ROOT / f'eval/results/sim_{date}{tag}.json'
    started = time.strftime('%H:%M:%S')
    mode = 'text (script, no audio)' if args.text else (f'replay of {args.replay} (no audio)' if args.replay else 'audio (synthetic voices, real time)')
    print(f'SIMULATED self-run simulator: {started}, mode {mode}, speed x{args.speed}, traffic {args.traffic}, '
          f'whisper prompt {"on" if args.whisper_prompt else "off"}, LLM fallback {("on, " + (ep[1] + " @ " + ep[0] if ep else "runtime_config")) if llm_on else "off"}', flush=True)
    scs = scenario.load_all(only=set(args.only) if args.only else None)
    if args.set:
        scs = [s for s in scs if s['id'].startswith(args.set)]
    print(f'scenarios: {len(scs)} ({", ".join(s["id"] for s in scs)})', flush=True)
    replayed = {}
    if args.replay:
        prev = json.loads(Path(args.replay).read_text())
        replayed = {sid: {h['tx']: h['whisper'] for h in run['heard']} for sid, run in prev['runs'].items()}
        missing = [s['id'] for s in scs if s['id'] not in replayed]
        if missing:
            print(f'replay: no earlier transcripts for {missing}; those run on script text', flush=True)
    prompt = options.whisper_prompt() if args.whisper_prompt else None
    wh = None
    if not (args.text or args.replay):
        runner.render(scs)
        wh = runner.Whisper(prompt=prompt)
    runs, per = {}, []
    log = lambda msg: print(msg, flush=True)
    try:
        for sc in scs:
            print(f"--- {sc['id']}: {sc['title']}", flush=True)
            traffic = options.traffic_list(sc, args.traffic)
            if wh is None:
                runs[sc['id']] = runner.run_scenario_text(sc, traffic=traffic, llm=llm_on, log=log, texts=replayed.get(sc['id']), llm_endpoint=ep)
            else:
                runs[sc['id']] = runner.run_scenario(sc, wh, args.speed, log=log, traffic=traffic, llm=llm_on, llm_endpoint=ep)
            p = score.score_scenario(sc, runs[sc['id']]['alerts'])
            per.append(p)
            print(f"  => {'PASS' if p['pass'] else 'FAIL'}: detected {p['detected']}/{p['expected']}, missed {p['missed']}, "
                  f"false alarms {p['false_alarms']}, latency {p['latency_s']}", flush=True)
    finally:
        if wh is not None:
            wh.close()
    res = {'SIMULATED': True, 'date': date, 'started': started, 'finished': time.strftime('%H:%M:%S'), 'speed': args.speed,
           'config': {'mode': mode, 'traffic': args.traffic, 'whisper_prompt': prompt, 'llm_fallback': llm_on,
                      'llm_endpoint': (dict(zip(('base_url', 'model'), ep)) if ep else 'runtime_config') if llm_on else None,
                      'text_mode_arrival': f'{runner.WORDS_PER_S} words/s + {runner.END_OF_SPEECH_S} s end of speech + {runner.ASR_S} s ASR' if wh is None else None},
           'transmissions': sum(len(s['transmissions']) for s in scs), 'out_json': str(out_json.relative_to(ROOT)) if out_json.is_relative_to(ROOT) else str(out_json),
           'overall': score.overall(per), 'per_scenario': per, 'runs': runs,
           'live_path': ('script/replayed text -> GmapsTools.fast_path (grammar' + (' + LLM fallback' if llm_on else '') + ' + ledger) on a simulated 1 s clock; no audio, no ASR'
                         if wh is None else
                         'Kokoro (local) + radio channel -> 20 ms real-time stream -> squelch end of speech (300 ms) -> Whisper large-v3-turbo '
                         f'(warm, offline container, {"round-3 prompt" if prompt else "no prompt"}) -> GmapsTools.fast_path (grammar'
                         + (' + LLM fallback' if llm_on else '') + ' + ledger) + 1 s scenario clock')}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(res, indent=1) + '\n')
    o = res['overall']
    print(f"\nOVERALL: {o['passed']}/{o['scenarios']} scenarios pass; detected {o['detected']}/{o['expected']}, missed {o['missed']}, "
          f"false alarms {o['false_alarms']}; latency median {o['latency_s']['median']} s, max {o['latency_s']['max']} s", flush=True)
    print(f'results: {out_json}', flush=True)
    if not args.no_docs:
        md = ROOT / f'docs/SIM_SCORECARD_{date}{tag}.md'
        md.write_text(scorecard_md(res))
        print(f'scorecard: {md}', flush=True)
    return res


if __name__ == '__main__':
    main(sys.argv[1:])
